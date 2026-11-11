---
name: windows-app-master
command: /app
description: Windows 桌面软件与后台服务精准控制（注册表自动寻路、URI深链唤起、Office COM操控、服务与Docker容器管理）。
---

# Windows 桌面软件与系统服务精准控制规程

本规程定义了在 Windows 10/11 环境下，如何通过最稳定、无感、低幻觉的原生技术通道控制本地桌面软件与系统后台服务。
**核心原则：优先使用系统 API、注册表与 CLI 深度协议，严禁直接盲目依赖屏幕坐标点击。**

---

## 一、 软件安装路径自动寻路（免问用户装在哪个盘）

绝大多数 Windows 桌面软件在安装时都会在系统注册表的 `App Paths` 登记其可执行文件完整路径。
当你需要启动某个软件但不知道其安装在 C 盘、D 盘还是其他目录时，直接在终端执行以下 PowerShell 命令自动定位：

```powershell
# 示例：自动查找特定软件的安装路径（如 code.exe, chrome.exe, wechat.exe, notion.exe）
$target = "code.exe"  # 替换为目标 exe 名称
$regPaths = @(
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\$target",
    "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\$target"
)
foreach ($rp in $regPaths) {
    if (Test-Path $rp) {
        $exe = (Get-ItemProperty -Path $rp).'(default)'
        if ($exe -and (Test-Path $exe)) { Write-Output "FOUND: $exe"; break }
    }
}
```

若注册表中未直接登记，可快速检索常用开始菜单与用户本地安装目录：
- `$env:LOCALAPPDATA\Programs\`
- `$env:ProgramFiles` 与 `${env:ProgramFiles(x86)}`

---

## 二、 CLI 与 URI Scheme 深度唤起协议

通过软件原生支持的命令行标志或系统协议深链唤起，比单纯启动软件更能直接达成用户目标。

### 1. 代码编辑器（VSCode / Cursor）
- **打开文件并直达指定行号和列号**：
  ```powershell
  code --goto "E:\work\geminihub-local\backend\main.py:45:10"
  ```
- **在当前窗口新建或打开工作区**：
  ```powershell
  code -r "E:\work\geminihub-local"
  ```
- **文件差异对比 (Diff)**：
  ```powershell
  code --diff "fileA.py" "fileB.py"
  ```

### 2. 浏览器打开特定网页或进入调试模式（CDP 9222 规范）
- **环境变量强制语法红线（防产生 `%SystemDrive%` 畸形目录）**：
  - 在 PowerShell 命令或路径参数中**严禁使用** `%SystemDrive%`、`%LOCALAPPDATA%`、`%USERPROFILE%` 等 CMD 批处理 `%...%` 变量；必须统一使用 `$env:LOCALAPPDATA`、`$env:USERPROFILE`、`Join-Path` 或工作区真实绝对路径。
- **默认浏览器快速打开 URL**（无需持续接管页面时）：
  ```powershell
  Start-Process "https://github.com"
  ```
- **以独立用户目录异步拉起带 CDP 调试端口（`9222`）的 Edge / Chrome（供 Playwright MCP 持续接管）**：
  1. 桌面端若已有常规 Edge 在运行，使用默认配置目录启动 `--remote-debugging-port=9222` 会立刻附着并退出而不监听调试端口。因此**必须指定独立 `--user-data-dir`**；
  2. 必须先检测 `9222` 端口是否已被调试实例复用，未就绪时通过 `Start-Process ... -WindowStyle Normal` 异步非阻塞启动（严禁直接前台调用阻塞式 `msedge.exe` 命令），随后在 10 秒内轮询 CDP HTTP 接口验证就绪：
  ```powershell
  $cdpUrl = "http://127.0.0.1:9222/json/version"
  $targetUrl = "https://www.12306.cn/index/"
  $ready = $false
  try {
      $resp = Invoke-RestMethod -Uri $cdpUrl -TimeoutSec 2 -ErrorAction Stop
      if ($resp) { $ready = $true; Write-Output "CDP_ALREADY_READY: $($resp.Browser)" }
  } catch {}

  if (-not $ready) {
      $edgePath = @(
          "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe",
          "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"
      ) | ForEach-Object {
          if (Test-Path $_) { (Get-ItemProperty -Path $_).'(default)' }
      } | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
      if (-not $edgePath) { $edgePath = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe" }

      $profileDir = Join-Path $env:LOCALAPPDATA "AgentHub\edge_cdp_profile"
      New-Item -ItemType Directory -Force -Path $profileDir | Out-Null

      Start-Process -FilePath $edgePath -ArgumentList @(
          "--remote-debugging-port=9222",
          "--user-data-dir=$profileDir",
          "--no-first-run",
          "--no-default-browser-check",
          $targetUrl
      ) -WindowStyle Normal

      for ($i = 0; $i -lt 10; $i++) {
          Start-Sleep -Seconds 1
          try {
              $resp = Invoke-RestMethod -Uri $cdpUrl -TimeoutSec 2 -ErrorAction Stop
              if ($resp) { Write-Output "CDP_READY: $($resp.Browser)"; $ready = $true; break }
          } catch {}
      }
      if (-not $ready) { Write-Output "CDP_TIMEOUT_WAITING_9222" }
  }
  ```
  3. 确认 `CDP_READY` 后，若工具清单中已有 `mcp__playwright-cdp__*` 工具，立即衔接调用（如 `mcp__playwright-cdp__browser_snapshot`、`browser_navigate`、`browser_click`）操控真实页面。

### 3. 本地客户端协议唤起（URI Scheme）
许多主流软件注册了专属协议头，可直接拉起特定界面：
- **微信客户端唤起**：`Start-Process "weixin://"`
- **系统设置特定页**：
  - 网络设置：`Start-Process "ms-settings:network"`
  - 声音设置：`Start-Process "ms-settings:sound"`
  - Windows 更新：`Start-Process "ms-settings:windowsupdate"`

---

## 三、 Office 办公软件 COM 自动化交互（静默实时操控）

当用户要求：“把查出来的数据写进我桌面上正在打开的 Excel 表格里”时，直接使用 Windows COM 组件进行对象级操控，100% 准确且零界面依赖。

### 1. 操控当前正打开的 Excel 实例
```powershell
# 获取当前正运行的 Excel 进程
$excel = [Runtime.InteropServices.Marshal]::GetActiveObject("Excel.Application")
# 若未打开则新建：if (-not $excel) { $excel = New-Object -ComObject Excel.Application; $excel.Visible = $true }
$workbook = $excel.ActiveWorkbook
if (-not $workbook) { $workbook = $excel.Workbooks.Add() }
$sheet = $workbook.ActiveSheet

# 在 A1 和 B1 单元格填入标题
$sheet.Cells.Item(1, 1).Value2 = "服务名称"
$sheet.Cells.Item(1, 2).Value2 = "状态"
$sheet.Range("A1:B1").Font.Bold = $true

# 填入数据
$sheet.Cells.Item(2, 1).Value2 = "Backend API"
$sheet.Cells.Item(2, 2).Value2 = "Running"
```

---

## 四、 本地服务、端口冲突与 Docker 容器调度

### 1. 端口占用与冲突精准定位
若本地某个端口（如 8080、3000、5432）启动失败或被占用，快速找出拥有该端口的进程名与 PID：
```powershell
# 查找占用 8080 端口的进程详情
Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue | 
    ForEach-Object { 
        $proc = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
        [PSCustomObject]@{
            Port = $_.LocalPort
            PID = $_.OwningProcess
            ProcessName = $proc.ProcessName
            Path = $proc.Path
        }
    } | Format-Table -AutoSize
```

### 2. 本地系统服务控制（Windows Services）
```powershell
# 检查服务状态（如 MySQL, Docker, Redis）
Get-Service -Name "*mysql*", "*docker*", "*redis*" -ErrorAction SilentlyContinue

# 重启或启动指定服务（注意需相应权限）
Restart-Service -Name "com.docker.service" -ErrorAction SilentlyContinue
```

### 3. 本地 Docker 容器快速调度
```powershell
# 查看所有运行中的容器
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# 重启指定服务容器并获取最新 20 行日志
docker restart <container_name>
docker logs --tail 20 <container_name>
```

---

## 五、 操作后闭环验证准则

每次执行桌面软件唤起或系统服务重启操作后：
1. **进程存在性确认**：使用 `Get-Process <ProcessName>` 验证软件是否真实被拉起运行；
2. **网络与 CDP 探针校验**：若重启了 Web/API 服务或启动了浏览器调试端口（9222），校验 `http://127.0.0.1:9222/json/version` 或服务健康接口返回正常状态；
3. **工具链路衔接**：若已挂载 `mcp__playwright-cdp__*` 工具且需要继续页面交互（查票/填表/点击），直接在下一工具步发起 MCP 页面操作，**切勿在执行完毕后挂起等待浏览器退出**；
4. **向用户汇报清晰事实**：简要说明软件状态、工作目录或已接管的页面地址，支付与敏感提交前保留人工确认红线。

