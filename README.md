# GeminiHub Local (本地多账号智能算力中心与私有化研发工作台)

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?logo=python" alt="Python Version" />
  <img src="https://img.shields.io/badge/FastAPI-0.111%2B-009688?logo=fastapi" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Next.js-14.2-black?logo=next.js" alt="Next.js" />
  <img src="https://img.shields.io/badge/SQLite-WAL%20%2B%20FTS5-003B57?logo=sqlite" alt="SQLite" />
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License" />
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20Docker-brightgreen" alt="Platform" />
</p>

> **数据与算力彻底解耦**：所有会话历史、上下文快照与个人偏好 100% 存留于本地原生 SQLite 数据库（WAL 模式 + FTS5 trigram 全文检索）。云端多账号与 CLI 仅充当无状态算力执行节点，零数据外泄，实现真正的个人私有化 AI 智能中枢。

---

## 🌟 核心特性

- 🧠 **微内核 Agent 运行时与三层记忆架构（Microkernel Agent & Three-Tier Memory）**  
  内置**短期滑动窗口记忆**（Turn 轮次锚定 + 历史长命令输出自动折叠降噪）、**工作记忆草稿纸**（多步复杂任务状态实时追踪）与**零外部依赖长期记忆画像**（SQLite FTS5 BM25 毫秒级召回 + 30天自然指数衰减 + 置顶规则豁免）。支持「极速单次 / 标准对话 / Agent 任务」三档场景切换与「无痕模式」。
- 🖥️ **受控本地终端沙箱与扩展能力总线（LocalShell + Skill + MCP）**  
  内置三级安全管控本地终端执行器（只读命令直通、灰名单命令内嵌弹窗审批、高危破坏性命令拦截），具备跨命令 CWD 工作目录保持、防交互挂死、进程树级强杀与 UTF-8/GBK 容错解码；同时支持 `skills/*.md` 按需加载与常驻 `MCP` 扩展连接池。
- 📥 **跨平台历史会话与长期记忆「四步导入向导」**  
  支持一键拖拽导入 **ChatGPT 官方导出包 (`.zip` / `conversations.json` / `memories.json`)**、**Claude 导出包**、**IDE 规则文件 (`GEMINI.md` / `CLAUDE.md` / `.cursorrules`)**。自动展平树状会话并建立本地 FTS5 索引，提供可视化记忆冲突比对表（新增 / 冲突覆盖 / 重复跳过）与按批次一键无痕撤回（Rollback）。
- ⚡ **多协议混合算力池与 429 零感知接力（Unified EnginePool）**  
  既支持多 Google OAuth / CLI 账号池 **< 50ms** 429 自动冷却接力，又支持无缝接入 **OpenAI / DeepSeek / 硅基流动 / OpenRouter / 本地 Ollama (`http://127.0.0.1:11434/v1`) / Anthropic Claude**，并支持将「主力推理」与「后台轻量记忆提炼」绑定至不同模型以实现 0 额外账单成本。
- 🧪 **模型权限自适应识别与一键可用性实测**  
  根据当前活跃账号属性动态判断兼容模型。内置「一键并发测试」引擎，自动验证并测速各模型联通状态，输入框下拉列表仅展示实测可用的模型，杜绝“选了模型却报错”的尴尬。
- 🎨 **极简干净现代交互与三态主题外观**  
  去除一切杂乱的冗余图标与视觉噪点，专注纯粹舒适的对话交互。原生支持 **浅色模式（Light）**、**深色模式（Dark）** 与 **跟随系统（System）** 三态切换。
- 📋 **微信与剪贴板富媒体混排粘贴**  
  深度适配日常办公流，直接通过 `Ctrl + V` 将微信截图、剪贴板图片、文本文档等粘贴至输入框作为附件，快速结合多模态模型进行视觉解析与代码审查。
- 🔍 **毫秒级 FTS5 全文搜索与会话自愈**  
  按 `Ctrl + K` 呼出全局搜索，秒级检索数万条本地及导入的历史对话与代码片段，点击任意历史对话即可使用本地算力池无缝接续往下聊；内置 `SessionHealer` 断电/中断会话自愈机制。

---

## 🏗️ 系统架构

```
┌────────────────────────────────────────────────────────┐
│              GeminiHub Local 客户端交互层              │
│   (Next.js 14 + React + Tailwind CSS + WebView2 原生窗体)│
└───────────────────────────▲────────────────────────────┘
                            │ 本地回环通信 (HTTP / SSE 流式传输)
┌───────────────────────────▼────────────────────────────┐
│                  FastAPI 核心服务中枢                   │
│  ├─ 动态端口自适应与生命周期托管                        │
│  ├─ EnginePool 算力调度池 (429 智能防抖与状态机)       │
│  ├─ Model Tester 模型真伪并发实测引擎                  │
│  └─ Security & Audit 全链路安全审计与令牌防护          │
└─────────────▲────────────────────────────▲─────────────┘
              │ 异步持久化                  │ 调度执行
┌─────────────▼─────────────┐ ┌────────────▼─────────────┐
│    本地数据源 (SQLite)    │ │   外部无状态算力执行节点  │
│  - WAL 高性能日志并发模式 │ │  - Google One OAuth 节点 │
│  - FTS5 全文检索引擎      │ │  - Antigravity / CLI     │
│  - 纯本地存储、绝不上云   │ │  - 429 自动冷却与接力池  │
└───────────────────────────┘ └──────────────────────────┘
```

---

## 🚀 运行方案与快速启动

本项目提供三种运行方案，根据您的使用场景任意选择：

### 方案 A：桌面原生客户端运行（推荐，开箱即用）

适用于 Windows 桌面日常使用，启动即享全屏工作台。

#### 1. 克隆代码库
```bash
git clone https://github.com/your-username/geminihub-local.git
cd geminihub-local
```

#### 2. 准备 Python 虚拟环境并安装依赖
```powershell
# 创建虚拟环境 (建议 Python 3.10 或更高版本)
python -m venv .venv

# 激活虚拟环境 (Windows PowerShell)
.\.venv\Scripts\Activate.ps1

# 安装核心依赖
pip install -r backend/requirements.txt
```

#### 3. 一键启动
您可以直接在项目根目录下**双击运行**：
- `启动GeminiHub桌面版.bat` （静默原生桌面模式）
- 或 `启动GeminiHub_调试模式(带控制台日志).bat` （带控制台实时滚屏日志）

也可以在终端中执行：
```powershell
.\.venv\Scripts\python.exe desktop_app.py
```
> **提示**：前端静态生产级产物已内置于 `frontend/out/`，启动时由桌面应用自动托管，无需提前安装 Node.js 即可直接使用！

---

### 方案 B：本地前后端分离调试运行（适合开发者二次开发）

如果您希望对前端或后端代码进行二次开发、热重载调试：

#### 1. 启动后端服务 (FastAPI)
```bash
cd backend
python -m uvicorn main:app --host 127.0.0.1 --port 8686 --reload
```
后端就绪后可访问交互式 API 契约文档：`http://127.0.0.1:8686/docs`

#### 2. 启动前端服务 (Next.js)
另起一个终端：
```bash
cd frontend
npm install
npm run dev
```
前端开发服务启动后，浏览器打开：`http://localhost:3000`

---

### 方案 C：Docker Compose 容器化部署（服务器 / 远程环境）

适合在 Linux 服务器或 NAS 上私有化部署。

```bash
# 复制环境变量配置
cp .env.example .env

# 一键启动容器集群
docker compose up -d --build
```

- 前端工作台访问：`http://<您的IP>:3000`
- 后端服务接口访问：`http://<您的IP>:8686`

---

## ⚙️ 环境变量配置 (`.env`)

复制根目录下的 `.env.example` 为 `.env` 可自定义各项参数：

| 配置项 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `TZ` | `Asia/Shanghai` | 系统时区 |
| `LOG_LEVEL` | `INFO` | 日志级别 (`DEBUG` / `INFO` / `WARNING` / `ERROR`) |
| `DB_PATH` | `/app/data/gemini_hub_local.db` | SQLite 数据库文件绝对或相对路径 |
| `AUTO_FAILOVER_RETRY` | `3` | 429 限流重试容错阈值 |
| `DEFAULT_COOLDOWN_SECONDS` | `60` | 账号触发限流后的冷却锁定秒数 |
| `GOOGLE_OAUTH_CLIENT_ID` | *(空)* | 可选，自定义 Google Cloud OAuth Client ID |
| `GOOGLE_OAUTH_CLIENT_SECRET` | *(空)* | 可选，自定义 Google Cloud OAuth Client Secret |

---

## 🔒 隐私与安全性规范

1. **零数据上传与网络隔离**：系统默认仅监听 `127.0.0.1` 回环地址，所有数据与个人对话全部保存在本地 `data/` 目录的 SQLite 文件中。
2. **凭据安全处理**：
   - 绝不硬编码任何个人密钥或账号信息；
   - 敏感配置文件与本地日志已通过严格的 `.gitignore` 排除规则保护；
   - 支持动态在界面中配置私有 Google OAuth 凭据。
3. **安全审计追踪**：内置审计日志功能，记录所有核心操作与模型调用，方便回溯与排查。

---

## 🤝 贡献与反馈

欢迎提交 Issue 和 Pull Request！
如果您觉得本项目对您的工作流有所帮助，欢迎给一个 ⭐️ **Star** 鼓励支持！

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 开源协议分发与使用。
