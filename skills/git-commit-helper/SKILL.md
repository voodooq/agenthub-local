---
name: git-commit-helper
command: /git
description: 遵循 Conventional Commits 规范审查 Git 改动并生成高质量中英文提交信息。
---

# Git 提交规范助手 (Git Commit Helper)

1. 先使用 `execute_local_command` 执行 `git status` 和 `git diff --stat` 查看变更范围；
2. 严格遵循 `<type>(<scope>): <subject>` 格式（如 `feat`, `fix`, `refactor`, `docs`）；
3. 严禁将 `.env`、`*.db`、`*.log` 或含个人密钥的文件纳入暂存区。
