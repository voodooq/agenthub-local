"""
Windows UI Automation (UIA) 原生无界面控件交互引擎（工业级高鲁棒版）。
针对无 API 封闭桌面应用（如微信 4.0+、钉钉、Qt/Electron 自绘应用、特定行业系统、设置弹窗等）提供结构化控件识别与操作。

技术特性：
1. 桌面沙箱穿透：自动切换至 WinSta0\\default 用户交互桌面，不受后台子进程桌面隔离影响。
2. 零安装依赖：原生通过 Windows user32.dll 与 .NET System.Windows.Automation 进行交互。
3. 无障碍树唤醒：通过 WM_GETOBJECT 与 SPI_SETSCREENREADER 强制唤醒 Qt/Chromium 懒加载 DOM。
4. 双模控件树提取：标准 FindAll 优先，遇自绘断层 (<=3) 自动平滑降级至 RawViewWalker BFS 遍历，并附带物理坐标 [x,y,w,h]。
5. 双保险操作执行：支持原生 Patterns、剪贴板中文防乱码粘贴 (^v) 与物理坐标模拟点击兜底。
"""

import base64
import ctypes
import json
import logging
import re
import subprocess
import sys
from typing import Any

logger = logging.getLogger("agenthub.ui_operator")

if sys.platform == "win32":
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
else:
    user32 = None
    WNDENUMPROC = None


class UIOperator:
    """Windows 原生 UI 自动化控制器（非 Windows 平台安全降级返回未支持提示）。"""

    @classmethod
    def _get_default_desktop_windows(cls) -> list[dict[str, Any]]:
        """
        穿透至 default 用户桌面，安全枚举所有可见顶级窗口。
        """
        if sys.platform != "win32" or user32 is None or WNDENUMPROC is None:
            return []
        windows: list[dict[str, Any]] = []
        hdesk = user32.OpenDesktopW("default", 0, False, 0x01FF)
        if not hdesk:
            logger.warning("无法打开 WinSta0\\default 桌面，回退至当前默认上下文。")
            hdesk = None

        def _enum_cb(hwnd: int, lparam: int) -> bool:
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    title_buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, title_buff, length + 1)
                    title = title_buff.value.strip()

                    class_buff = ctypes.create_unicode_buffer(256)
                    user32.GetClassNameW(hwnd, class_buff, 256)
                    class_name = class_buff.value.strip()

                    if title:
                        windows.append({
                            "hwnd": int(hwnd),
                            "title": title,
                            "class_name": class_name,
                        })
            return True

        if hdesk:
            user32.EnumDesktopWindows(hdesk, WNDENUMPROC(_enum_cb), 0)
            user32.CloseDesktop(hdesk)
        else:
            user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)

        return windows

    @classmethod
    def list_windows(cls, pattern: str = "") -> list[dict[str, Any]]:
        """
        列出当前可见的所有顶级窗口。
        若指定 pattern，则进行不区分大小写模糊匹配过滤。
        """
        windows = cls._get_default_desktop_windows()
        if not pattern:
            return windows

        pat = pattern.strip().lower()
        filtered = [
            w for w in windows
            if pat in w["title"].lower() or pat in w["class_name"].lower()
        ]
        return filtered

    @classmethod
    def _resolve_target_hwnd(cls, target: str | int) -> tuple[int | None, str | None]:
        """
        解析目标窗口为 HWND 和标题。
        """
        if isinstance(target, int) or (isinstance(target, str) and target.isdigit()):
            target_hwnd = int(target)
            windows = cls._get_default_desktop_windows()
            for w in windows:
                if w["hwnd"] == target_hwnd:
                    return target_hwnd, w["title"]
            return target_hwnd, f"HWND({target_hwnd})"

        # 按标题匹配
        target_str = str(target).strip()
        candidates = cls.list_windows(target_str)
        if not candidates:
            # 尝试宽松匹配
            all_wins = cls.list_windows()
            for w in all_wins:
                if target_str.lower() in w["title"].lower():
                    candidates.append(w)

        if not candidates:
            return None, None

        best = candidates[0]
        return best["hwnd"], best["title"]

    @classmethod
    def inspect_window(cls, target: str | int, max_items: int = 60) -> str:
        """
        分析指定窗口的内部 UIA 控件树结构。
        内置：
        1. WM_GETOBJECT + SPI_SETSCREENREADER 唤醒 Qt/Chromium 懒加载 DOM
        2. FindAll 优先，若元素 <= 3 则自动切换 RawViewWalker 兜底
        3. 输出屏幕物理坐标 [x,y,w,h]，方便无 Pattern 自绘控件回退物理点击
        """
        if sys.platform != "win32" or user32 is None:
            return "当前宿主系统非 Windows，原生 Win32 UIA 窗口控件扫描暂不可用。"

        hwnd, title = cls._resolve_target_hwnd(target)
        if not hwnd:
            all_wins = cls.list_windows()
            sample_titles = "\n".join(f" - [{w['hwnd']}] {w['title']}" for w in all_wins[:10])
            return f"❌ 未找到匹配的目标窗口: '{target}'。\n当前活跃窗口参考：\n{sample_titles}"

        ps_script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$OutputEncoding = [System.Text.Encoding]::UTF8;
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;

$win32Def = @"
using System;
using System.Runtime.InteropServices;
public class UIAEngine {{
    [DllImport("user32.dll", SetLastError = true)]
    public static extern IntPtr OpenDesktop(string lpszDesktop, uint dwFlags, bool fInherit, uint dwDesiredAccess);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool SetThreadDesktop(IntPtr hDesktop);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam, uint fuFlags, uint uTimeout, out IntPtr lpdwResult);

    [DllImport("user32.dll")]
    public static extern bool SystemParametersInfo(uint uiAction, uint uiParam, IntPtr pvParam, uint fWinIni);

    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);

    public static void WakeUp(IntPtr hwnd) {{
        IntPtr res;
        // 0x0047 = SPI_SETSCREENREADER (通知应用处于无障碍辅助模式，强制 Qt/Chromium 暴露 DOM)
        SystemParametersInfo(0x0047, 1, IntPtr.Zero, 0);
        // 0x003D = WM_GETOBJECT, -4 = OBJID_CLIENT
        SendMessageTimeout(hwnd, 0x003D, IntPtr.Zero, (IntPtr)(-4), 0x0002, 300, out res);
    }}
}}
"@
Add-Type -TypeDefinition $win32Def -ErrorAction SilentlyContinue

$hdesk = [UIAEngine]::OpenDesktop("default", 0, $false, 0x01FF)
if ($hdesk -ne [IntPtr]::Zero) {{
    [UIAEngine]::SetThreadDesktop($hdesk) | Out-Null
}}

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

try {{
    # 强制唤醒无障碍树
    [UIAEngine]::WakeUp([IntPtr]{hwnd})
    Start-Sleep -Milliseconds 80

    $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]{hwnd})
    if (-not $root) {{
        [Console]::WriteLine("ERROR: 无法从 HWND {hwnd} 获取 AutomationElement")
        exit 0
    }}

    [Console]::WriteLine("WINDOW_TITLE: " + $root.Current.Name)

    # 策略 A: 先尝试标准 FindAll 快速扫描
    $cond = [System.Windows.Automation.Condition]::TrueCondition
    $children = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
    
    $items = @()
    if ($children.Count -gt 3) {{
        [Console]::WriteLine("SCAN_MODE: Standard FindAll (Elements: " + $children.Count + ")")
        foreach ($c in $children) {{
            $name = $c.Current.Name
            $autoId = $c.Current.AutomationId
            $type = $c.Current.ControlType.ProgrammaticName.Replace("ControlType.", "")
            $rect = $c.Current.BoundingRectangle
            $rectStr = if ($rect.Width -gt 0) {{ " rect='$([int]$rect.X),$([int]$rect.Y),$([int]$rect.Width),$([int]$rect.Height)'" }} else {{ "" }}

            if ($name -or $autoId -or ($type -in @("Button", "Edit", "CheckBox", "RadioButton", "ComboBox", "Hyperlink", "ListItem", "TabItem", "MenuItem"))) {{
                $idStr = if ($autoId) {{ " id='$autoId'" }} else {{ "" }}
                $nameStr = if ($name) {{ " name='$name'" }} else {{ "" }}
                $items += "  - <$type$idStr$nameStr$rectStr />"
            }}
            if ($items.Count -ge {max_items}) {{ break }}
        }}
    }} else {{
        # 策略 B: 元素 <= 3，说明命中 Qt / MMUIRenderSubWindowHW 自绘黑盒容器，自动切 RawViewWalker BFS 深度展开
        [Console]::WriteLine("SCAN_MODE: RawViewWalker Fallback (Deep Penetration)")
        $walker = [System.Windows.Automation.TreeWalker]::RawViewWalker
        $queue = New-Object System.Collections.Queue
        $queue.Enqueue($root)
        $visited = 0

        while ($queue.Count -gt 0 -and $visited -lt 120 -and $items.Count -lt {max_items}) {{
            $curr = $queue.Dequeue()
            $visited++
            $child = $walker.GetFirstChild($curr)
            while ($child -ne $null -and $items.Count -lt {max_items}) {{
                $name = $child.Current.Name
                $autoId = $child.Current.AutomationId
                $type = $child.Current.ControlType.ProgrammaticName.Replace("ControlType.", "")
                $rect = $child.Current.BoundingRectangle
                $rectStr = if ($rect.Width -gt 0) {{ " rect='$([int]$rect.X),$([int]$rect.Y),$([int]$rect.Width),$([int]$rect.Height)'" }} else {{ "" }}

                if ($name -or $autoId -or ($type -in @("Button", "Edit", "CheckBox", "RadioButton", "ComboBox", "Hyperlink", "ListItem", "TabItem", "MenuItem"))) {{
                    $idStr = if ($autoId) {{ " id='$autoId'" }} else {{ "" }}
                    $nameStr = if ($name) {{ " name='$name'" }} else {{ "" }}
                    $items += "  - <$type$idStr$nameStr$rectStr />"
                }}
                $queue.Enqueue($child)
                $child = $walker.GetNextSibling($child)
            }}
        }}
    }}

    [Console]::WriteLine("TOTAL_INTERACTIVE_ELEMENTS: " + $items.Count)
    foreach ($line in $items) {{
        [Console]::WriteLine($line)
    }}
}} catch {{
    [Console]::WriteLine("ERROR: " + $_.Exception.Message)
}}
"""
        b64 = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
        try:
            res = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", b64],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=12,
            )

            lines = [line.strip() for line in res.stdout.splitlines() if line.strip() and not line.strip().startswith("#<")]
            if not lines:
                return f"⚠️ 成功挂载窗口 [{hwnd}] '{title}'，但未探测到可识别的 UIA 控件结构（可能是完全自绘且关闭无障碍的独占窗口）。"

            return f"### 🖥️ 窗口控件树 [{hwnd}] {title}：\n" + "\n".join(lines)
        except Exception as e:
            return f"❌ 探测窗口异常: {str(e)}"

    @classmethod
    def perform_action(
        cls,
        target: str | int,
        control_selector: str,
        action: str = "click",
        value: str = "",
        press_enter: bool = False,
    ) -> dict[str, Any]:
        """
        对指定窗口的控件执行工业级双保险原生操作。
        :param target: 目标窗口 HWND 或标题匹配关键词
        :param control_selector: 目标控件的 AutomationId 或 Name，或物理坐标 "x,y"
        :param action: 操作类型，可选 'click', 'input', 'focus'
        :param value: 当 action='input' 时要填充的文本（自动通过剪贴板安全粘贴，杜绝中文乱码）
        :param press_enter: 输入完成后是否自动发送 Enter 回车键
        """
        if sys.platform != "win32" or user32 is None:
            return {
                "success": False,
                "error": "当前宿主系统非 Windows，不支持 Win32 UIA 控件操作。",
            }

        hwnd, title = cls._resolve_target_hwnd(target)
        if not hwnd:
            return {
                "success": False,
                "error": f"未找到目标窗口: '{target}'",
            }

        act = action.strip().lower()
        selector = control_selector.strip()
        val_escaped = value.replace('"', '`"').replace("$", "`$")
        enter_flag = "$true" if press_enter else "$false"

        ps_script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$OutputEncoding = [System.Text.Encoding]::UTF8;
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;

$win32Def = @"
using System;
using System.Runtime.InteropServices;
public class UIAActionEngine {{
    [DllImport("user32.dll", SetLastError = true)]
    public static extern IntPtr OpenDesktop(string lpszDesktop, uint dwFlags, bool fInherit, uint dwDesiredAccess);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool SetThreadDesktop(IntPtr hDesktop);

    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool SetProcessDPIAware();

    [DllImport("user32.dll")]
    public static extern bool SetCursorPos(int X, int Y);

    [DllImport("user32.dll")]
    public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);

    public const uint MOUSEEVENTF_LEFTDOWN = 0x0002;
    public const uint MOUSEEVENTF_LEFTUP   = 0x0004;

    public static void PhysicalClick(int x, int y) {{
        SetCursorPos(x, y);
        System.Threading.Thread.Sleep(50);
        mouse_event(MOUSEEVENTF_LEFTDOWN, (uint)x, (uint)y, 0, UIntPtr.Zero);
        System.Threading.Thread.Sleep(50);
        mouse_event(MOUSEEVENTF_LEFTUP, (uint)x, (uint)y, 0, UIntPtr.Zero);
    }}
}}
"@
Add-Type -TypeDefinition $win32Def -ErrorAction SilentlyContinue
$hdesk = [UIAActionEngine]::OpenDesktop("default", 0, $false, 0x01FF)
if ($hdesk -ne [IntPtr]::Zero) {{
    [UIAActionEngine]::SetThreadDesktop($hdesk) | Out-Null
}}
[UIAActionEngine]::SetProcessDPIAware() | Out-Null

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms

try {{
    # 步骤 1: 窗口唤醒与激活置顶（SW_RESTORE = 9）
    [UIAActionEngine]::ShowWindow([IntPtr]{hwnd}, 9) | Out-Null
    [UIAActionEngine]::SetForegroundWindow([IntPtr]{hwnd}) | Out-Null
    Start-Sleep -Milliseconds 60

    $sel = "{selector}"

    # 支持坐标直接点击模式: "1240,680"
    if ($sel -match '^(\\d+)\\s*,\\s*(\\d+)$') {{
        $cx = [int]$matches[1]
        $cy = [int]$matches[2]
        [UIAActionEngine]::PhysicalClick($cx, $cy)
        [Console]::WriteLine("RESULT:SUCCESS:已执行屏幕物理坐标点击: ($cx, $cy)")
        exit 0
    }}

    $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]{hwnd})
    if (-not $root) {{
        [Console]::WriteLine("RESULT:FAIL:无法获取窗口元素 HWND {hwnd}")
        exit 0
    }}

    # 策略 1: AutomationId 优先
    $condId = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, $sel)
    $targetElem = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condId)

    # 策略 2: Name 优先
    if (-not $targetElem) {{
        $condName = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, $sel)
        $targetElem = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condName)
    }}

    # 策略 3: 模糊遍历
    if (-not $targetElem) {{
        $all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
        foreach ($item in $all) {{
            if ($item.Current.Name -like "*$sel*" -or $item.Current.AutomationId -like "*$sel*") {{
                $targetElem = $item
                break
            }}
        }}
    }}

    if (-not $targetElem) {{
        [Console]::WriteLine("RESULT:NOT_FOUND:未能匹配到 selector 为 '$sel' 的控件")
        exit 0
    }}

    $elemType = $targetElem.Current.ControlType.ProgrammaticName.Replace("ControlType.", "")
    $elemName = $targetElem.Current.Name
    $elemId = $targetElem.Current.AutomationId
    $rect = $targetElem.Current.BoundingRectangle

    if ("{act}" -eq "focus") {{
        $targetElem.SetFocus()
        [Console]::WriteLine("RESULT:SUCCESS:已置顶并聚焦控件 [$elemType] '$elemName' (id: $elemId)")
        exit 0
    }}

    if ("{act}" -eq "click") {{
        # 首选 InvokePattern
        $invPattern = $null
        try {{ $invPattern = $targetElem.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern) }} catch {{}}
        if ($invPattern) {{
            $invPattern.Invoke()
            [Console]::WriteLine("RESULT:SUCCESS:通过 InvokePattern 成功点击控件 [$elemType] '$elemName'")
            exit 0
        }}

        # 次选 TogglePattern
        $togPattern = $null
        try {{ $togPattern = $targetElem.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern) }} catch {{}}
        if ($togPattern) {{
            $togPattern.Toggle()
            [Console]::WriteLine("RESULT:SUCCESS:通过 TogglePattern 成功切换控件 [$elemType] '$elemName'")
            exit 0
        }}

        # 终极回退: 物理坐标点击
        if ($rect.Width -gt 0 -and $rect.Height -gt 0) {{
            $clickX = [int]($rect.X + ($rect.Width / 2))
            $clickY = [int]($rect.Y + ($rect.Height / 2))
            [UIAActionEngine]::PhysicalClick($clickX, $clickY)
            [Console]::WriteLine("RESULT:SUCCESS:控件不支持标准 Pattern，已通过物理坐标中心点 ($clickX, $clickY) 成功触发点击")
            exit 0
        }}

        # 再次回退焦点
        $targetElem.SetFocus()
        [Console]::WriteLine("RESULT:SUCCESS:控件无 Pattern 且无有效物理坐标，已为其赋予焦点 SetFocus")
        exit 0
    }}

    if ("{act}" -eq "input") {{
        $targetElem.SetFocus()
        Start-Sleep -Milliseconds 50

        # 双保险文本输入：先尝试标准 ValuePattern，若自绘控件不支持则使用剪贴板粘贴
        $inputSuccess = $false
        $valPattern = $null
        try {{ $valPattern = $targetElem.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern) }} catch {{}}

        if ($valPattern) {{
            try {{
                $valPattern.SetValue("{val_escaped}")
                $inputSuccess = $true
            }} catch {{}}
        }}

        if (-not $inputSuccess) {{
            # 剪贴板安全注入（雁过无痕：暂存用户原剪贴板内容并在注入后恢复）
            $oldClip = $null
            try {{ $oldClip = Get-Clipboard -Raw -ErrorAction SilentlyContinue }} catch {{}}

            Set-Clipboard -Value "{val_escaped}"
            Start-Sleep -Milliseconds 50
            [System.Windows.Forms.SendKeys]::SendWait("^v")
            Start-Sleep -Milliseconds 150

            if ($oldClip -ne $null) {{
                try {{ Set-Clipboard -Value $oldClip -ErrorAction SilentlyContinue }} catch {{}}
            }}
            $inputSuccess = $true
        }}

        if ({enter_flag}) {{
            Start-Sleep -Milliseconds 50
            [System.Windows.Forms.SendKeys]::SendWait("{{ENTER}}")
        }}

        [Console]::WriteLine("RESULT:SUCCESS:成功填入文本至 [$elemType] '$elemName' (是否回车: {enter_flag})")
        exit 0
    }}

    [Console]::WriteLine("RESULT:FAIL:不支持的操作类型: {act}")
}} catch {{
    [Console]::WriteLine("RESULT:ERROR:" + $_.Exception.Message)
}}
"""
        b64 = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
        try:
            res = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", b64],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=12,
            )
            raw_out = res.stdout.strip()
            match = re.search(r"RESULT:([A-Z_]+):(.*)", raw_out)
            if match:
                status, msg = match.group(1), match.group(2).strip()
                return {
                    "success": (status == "SUCCESS"),
                    "status": status,
                    "message": msg,
                    "window": f"[{hwnd}] {title}",
                }
            return {
                "success": False,
                "error": raw_out or res.stderr,
                "window": f"[{hwnd}] {title}",
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"执行 UI 操作异常: {str(e)}",
                "window": f"[{hwnd}] {title}",
            }


ui_operator = UIOperator()
