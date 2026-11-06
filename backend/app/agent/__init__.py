"""
GeminiHub Local 微内核 Agent 运行时模块
包含：
- LocalShellExecutor: 受控本地终端执行器（四大工程防御 + 三级安全校验）
- ToolProtocolAdapter: 原生 Tools 与 XML ReAct 混合协议适配器
- ContextBuilder & SessionHealer: 三层记忆组装、历史命令折叠与未闭合工具自愈
- AgentRuntime: 双层循环编排引擎
"""

