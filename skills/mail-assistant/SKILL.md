---
name: mail-assistant
command: /mail
version: 2.0.0
category: 办公与文档
type: skill
environment:
  os:
    - all
  requires_gui: false
  requires_runtime:
    - python
description: 通用多邮箱管理助手（支持 163、QQ、Outlook、Gmail），提供基于标准 IMAP/SMTP 协议的邮箱连接测试、未读邮件检索摘要、分类归档与垃圾邮件清理。
---

# 通用多邮箱管理助手 (Mail Assistant)

## 一、支持的邮箱服务商与协议
内置对以下主流邮箱的 IMAP/SMTP 标准配置支持：
- **网易 163 / 126 邮箱**：`imap.163.com:993`
- **QQ / Foxmail 邮箱**：`imap.qq.com:993`（使用授权码登录）
- **微软 Outlook / Office365**：`outlook.office365.com:993`
- **谷歌 Gmail**：`imap.gmail.com:993`（使用 App 专用密码）

---

## 二、随包内置脚本工具
测试邮箱连接与收件箱状态：
```powershell
python skills/mail-assistant/scripts/email_client.py --test --service 163 --user your_email@163.com --password your_auth_code
```
- 所有密码与授权码必须从环境变量中读取（如 `$env:MAIL_AUTH_CODE`），严禁明文硬编码。

