---
name: local-env-doctor
command: /doctor
description: 排查本机端口占用、僵尸进程、Python/Node 环境依赖冲突与磁盘空间告警。
---

# 本机研发环境诊断专家 (Local Env Doctor)

1. 排查端口占用时，先通过 `netstat -ano` 查找对应 PID，再通过 `tasklist` 核实进程名；
2. 发现目标 PID 与进程名称后，立即调用 `update_working_memory` 记录到任务草稿纸；
3. 若需终止进程，调用 `execute_local_command` 发起 `taskkill /PID <pid> /F`（由系统灰名单弹窗请用户审批）。
