---
name: email-assistant
command: /mail-assistant
type: skill
description: 通用多邮箱管理助手（支持 Gmail、163、Outlook、QQ），提供 IMAP 连接测试、智能分类过滤、垃圾邮件自动清理与桌面通知。
---

## 🚀 快速开始



```bash

# 1. 交互式配置

python skills/email-assistant/email163.py config



# 2. 测试连接

python skills/email-assistant/email163.py test



# 3. 手动执行一次完整流程

python skills/email-assistant/email163.py run



# 4. 安装每小时定时任务（需管理员终端）

python skills/email-assistant/email163.py schedule install

```



## 📋 支持的邮箱服务商



| 提供商 | IMAP 主机 | 端口 | 需要应用专用密码/授权码 | 垃圾箱文件夹 |

|--------|-----------|------|------------------------|--------------|

| **Gmail** | imap.gmail.com | 993 | ✅ 是 | `[Gmail]/Spam` |

| **163** | imap.163.com | 993 | ✅ 是 | `Junk` |

| **Outlook** | outlook.office365.com | 993 | ✅ 是 | `Junk Email` |

| **QQ** | imap.qq.com | 993 | ✅ 是 | `Spam` |



## 📦 命令速查



| 命令 | 说明 |

|------|------|

| `config` | 交互式配置邮箱提供商、账号及应用专用密码 |

| `test` | 测试 IMAP 连通性与凭据正确性 |

| `run` | 执行一次完整流程（智能分类 + 桌面通知 + 垃圾邮件自动清理） |

| `preview` | 仅预览邮件分类，**不移动**任何邮件 |

| `status` | 查看当前配置与绑定状态 |

| `schedule install` | 安装每小时后台定时任务 |

| `schedule uninstall` | 卸载定时任务 |

| `schedule status` | 查看任务计划程序运行状态 |



## 🔧 配置文件说明



路径：`skills/email-assistant/config.json`



```json

{

  "provider": "gmail",

  "email": "your@email.com",

  "keywords_important": ["紧急", "重要", "发票", "合同", "面试", "offer"],

  "senders_important": [],

  "keywords_spam": ["推广", "营销", "广告", "贷款", "保险"],

  "senders_spam": [],

  "max_fetch": 50,

  "notification_enabled": true

}

```
