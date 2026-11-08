---
name: windows-gui-operator
command: /gui
description: Windows 桌面封闭 GUI 软件与窗口无 API 原生自动化操作指南（微信、钉钉、特定行业系统、设置弹窗等）。
---

# Windows GUI Operator 专业操作技能指南

当用户需要你与本地正在运行的 Windows 桌面客户端交互，且该软件**没有提供开放的 HTTP API、CLI 命令行或 MCP 服务**（如微信桌面版、钉钉、播放器、办公自研系统、系统控制面板与弹窗）时，必须严格执行本规范。

---

## 🚫 严禁事项（Anti-Patterns）

1. **严禁截图 + 视觉坐标点击**：截图 Token 消耗巨大、延迟高（2~5秒），且在 Windows 125%/150% DPI 缩放下极易点偏。
2. **严禁盲猜控件**：在未执行 `inspect` 之前，不要盲目猜测控件名称。
3. **严禁反复原地循环**：若某个控件连续点击无响应，应立刻换用 `focus` + 键盘操作或检查是否为自绘无障碍降级界面。

---

## 🧭 标准三步操作流程（The 3-Step Workflow）

### 第一步：定位窗口（List Windows）
在操作前，先通过 `ui_action` 列出当前桌面上所有正在运行的可见窗口，确认目标软件的窗口标题与 HWND。

```xml
<tool_call name="ui_action">
{"action": "list", "target_window": "微信"}
</tool_call>
```

Observation 将返回匹配的窗口列表及对应 HWND。

---

### 第二步：分析控件树（Inspect Window）
拿到窗口标题或 HWND 后，分析其公开的 UIA 控件树结构。

```xml
<tool_call name="ui_action">
{"action": "inspect", "target_window": "微信"}
</tool_call>
```

Observation 将以扁平缩进树的形式返回控件列表：
```text
WINDOW_TITLE: 微信
TOTAL_ELEMENTS: 12
  - <Pane name='Weixin' />
  - <Button id='search_btn' name='搜索' />
  - <Edit id='input_box' name='输入' />
  - <Button id='send_btn' name='发送' />
```

---

### 第三步：精准触发操作（Perform Action）
根据控件树中识别到的 `name` 或 `id`，发起针对性操作：

#### 1. 点击按钮或切换选项
```xml
<tool_call name="ui_action">
{"action": "click", "target_window": "微信", "control_selector": "发送"}
</tool_call>
```

#### 2. 向输入框填入文本
```xml
<tool_call name="ui_action">
{"action": "input", "target_window": "微信", "control_selector": "输入", "value": "自动化测试消息"}
</tool_call>
```

#### 3. 将窗口或控件置顶并赋予焦点
```xml
<tool_call name="ui_action">
{"action": "focus", "target_window": "微信", "control_selector": "搜索"}
</tool_call>
```

---

## 🛡️ 异常与降级应对策略

1. **若控件树显示为自绘无障碍黑盒（如极少数老旧 DirectX 游戏窗口）**：
   - 优先使用 `action="focus"` 激活窗口；
   - 然后通过 `execute_local_command` 配合 PowerShell 原生 `[System.Windows.Forms.SendKeys]::SendWait(...)` 模拟全局快捷键（如 Enter、Tab、Esc、Ctrl+S）。
2. **遇到多重同名窗口（如弹出多个相同设置窗口）**：
   - 使用第一步获取到的唯一 `HWND` 整数作为 `target_window` 参数（例如 `target_window="131820"`），以杜绝歧义。

