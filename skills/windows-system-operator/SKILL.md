---
name: windows-system-operator
command: /win-ops
version: 2.0.0
category: 系统与自动化
type: skill
environment:
  os:
    - windows
  requires_gui: true
  requires_runtime:
    - powershell
    - python
description: Windows 桌面与运维控制中枢（Windows 专享）：涵盖端口占用排查、僵尸进程诊断、注册表寻路、URI深链唤起、Docker服务管理、以及封闭GUI软件原生窗口自动化操作。
---

# Windows 桌面与运维控制中枢 (Windows System Operator)

> [!IMPORTANT]
> **本技能仅限 Windows 宿主机环境运行**。在 Linux 容器或 macOS 环境下系统将自动跳过本技能。

## 一、本地开发环境与进程诊断
当遇到端口冲突、服务未响应或僵尸进程占用时，严格使用 Windows PowerShell 原生指令排查：
1. **排查端口占用 (替代 netstat/lsof)**：
   ```powershell
   Get-NetTCPConnection -LocalPort <端口号> -ErrorAction SilentlyContinue | Select-Object LocalAddress, LocalPort, OwningProcess, State
   ```
2. **核实目标进程详情 (PID ➔ 进程名与命令行)**：
   ```powershell
   Get-CimInstance Win32_Process -Filter "ProcessId = <PID>" | Select-Object ProcessId, Name, CommandLine
   ```
3. **精准终止异常进程**：
   ```powershell
   Stop-Process -Id <PID> -Force
   ```

---

## 二、注册表自动寻路与软件控制
1. **注册表自动定位已安装软件**：
   查询 `HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths` 或用户路径定位执行文件，无需猜测默认盘符。
2. **Windows URI 深链原生唤起**：
   - 微信：`weixin://`
   - 邮件：`mailto:`
   - 设置面板：`ms-settings:`
3. **Windows 容器与服务管理**：
   使用 PowerShell 原生调用 Docker CLI 或 `Get-Service` / `Restart-Service` 管理后台常驻服务。

---

## 三、封闭 GUI 原生窗口自动化与截图分析
针对无公开 API 的桌面应用（如微信、钉钉、特定行业客户端）：
1. **窗口激活与置顶**：
   通过 PowerShell Windows API 绑定（`user32.dll`）查找目标句柄并置顶：
   ```powershell
   [Add-Type] -TypeDefinition @"
   using System;
   using System.Runtime.InteropServices;
   public class WinHelper {
       [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
   }
   "@
   ```
2. **屏幕取证与视觉核验**：
   调用 Python `PIL.ImageGrab` 或 PowerShell 截取当前屏幕，落盘至 `output/screenshots/` 供模型视觉核验执行状态。
