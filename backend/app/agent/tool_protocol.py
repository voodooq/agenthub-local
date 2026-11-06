"""
统一混合工具协议适配器 (ToolProtocolAdapter)
解决痛点：
算力池中包含 Antigravity CLI 节点、OAuth 节点与 API Key 节点，部分通道不支持原生 OpenAI/Gemini tools 参数。
本模块提供：
1. 标准内置工具集定义 (execute_local_command, update_working_memory, load_skill_detail + MCP Tools)
2. 紧凑 XML ReAct 工具协议生成与解析器 (<tool_call name="...">{"arg": "val"}</tool_call>)
3. 原生 tool_calls 与 XML 协议的无感双向转换，保证 429 跨账号接力时工具调用不断链
"""
import json
import re
import uuid
from typing import Any


class ToolProtocolAdapter:
    """混合工具协议转换与解析器"""

    _XML_TOOL_PATTERN = re.compile(
        r"<tool_call\s+name=[\"']([^\"']+)[\"']\s*>\s*(.*?)\s*</tool_call>",
        re.DOTALL | re.IGNORECASE,
    )

    @staticmethod
    def get_builtin_tools() -> list[dict[str, Any]]:
        """获取微内核内置的 3 个核心原子工具声明"""
        return [
            {
                "name": "execute_local_command",
                "description": "在用户本机终端（Windows PowerShell / Linux Bash）受控执行命令。只读命令自动直通，修改命令将触发前端弹窗审批。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "要执行的单行终端命令（禁止交互式命令，需加 -y 等非交互标志）",
                        },
                        "reason": {
                            "type": "string",
                            "description": "简短说明执行该命令的目的",
                        },
                    },
                    "required": ["command"],
                },
            },
            {
                "name": "update_working_memory",
                "description": "更新当前多步任务的工作记忆草稿纸（Scratchpad），记录已查明的 PID、文件路径、子步骤状态，防止后续步骤遗忘。任务彻底完成时传 task_completed=true 清空草稿。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "goal": {
                            "type": "string",
                            "description": "当前多步任务的总目标简述",
                        },
                        "notes": {
                            "type": "object",
                            "description": "要合并写入草稿纸的键值对状态（如 {'target_pid': 14520, 'port': 8080, 'step': '已定位进程'}）",
                        },
                        "task_completed": {
                            "type": "boolean",
                            "description": "当前多步任务是否已彻底交付完成（若为 true 则归档并清空草稿纸）",
                        },
                    },
                    "required": [],
                },
            },
            {
                "name": "load_skill_detail",
                "description": "按需读取已挂载专业技能包（Skill）的完整 SKILL.md 操作规程。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "技能包名称（如 git-commit-helper 或 local-env-doctor）",
                        }
                    },
                    "required": ["skill_name"],
                },
            },
            {
                "name": "write_local_file",
                "description": "在用户本地磁盘安全创建或写入文件（自动递归创建父目录）。所有写操作均真实落盘物理文件，拒绝幻觉。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "目标文件相对路径或绝对路径（如 skills/my-skill/SKILL.md 或 src/main.py）",
                        },
                        "content": {
                            "type": "string",
                            "description": "要写入的文件完整文本内容",
                        },
                        "overwrite": {
                            "type": "boolean",
                            "description": "若目标文件已存在是否覆盖（默认 true）",
                        },
                    },
                    "required": ["file_path", "content"],
                },
            },
            {
                "name": "read_local_file",
                "description": "安全读取本地磁盘指定文本文件的内容。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "要读取的文件相对路径或绝对路径",
                        },
                    },
                    "required": ["file_path"],
                },
            },
        ]

    @classmethod
    def build_xml_protocol_prompt(cls, tools: list[dict[str, Any]]) -> str:
        """
        将工具列表编译为紧凑的 XML ReAct 协议说明，注入 System Prompt。
        使任何纯文本 CLI 或 API 模型都能稳定发起工具调用。
        """
        if not tools:
            return ""

        tool_Wait_lines = [
            "### 🛠️ 可用工具与调用协议 (Tool Calling Protocol)：",
            "当你需要执行本机命令、更新任务草稿纸或加载专业技能以完成用户请求时，请在回复末尾输出如下标准 XML 标签（一次可输出一个或多个）：",
            "```xml",
            '<tool_call name="工具名称">',
            '{"参数名": "参数值"}',
            "</tool_call>",
            "```",
            "注意：",
            "1. 标签内部必须是合法的 JSON 对象；",
            "2. 输出 `<tool_call>` 后请立即停止编造执行结果，等待系统真实执行并回填结果后再继续下一轮推理；",
            "3. 若无需调用工具即可直接回答用户问题，请直接输出正常回复，不要输出 `<tool_call>` 标签。",
            "",
            "当前已激活的工具列表：",
        ]

        for t in tools:
            params_str = json.dumps(t.get("parameters", {}).get("properties", {}), ensure_ascii=False)
            tool_Wait_lines.append(f"- **`{t['name']}`**: {t.get('description', '')} | 参数: `{params_str}`")

        return "\n".join(tool_Wait_lines)

    @classmethod
    def parse_xml_tool_calls(cls, raw_text: str) -> tuple[str, list[dict[str, Any]]]:
        """
        从模型输出文本中解析所有 <tool_call name="...">...</tool_call> 标签。

        Returns:
            (clean_text, tool_calls_list)
            - clean_text: 剥离了 <tool_call> 标签后的干净正文
            - tool_calls_list: 标准化的工具调用列表 [{"id": "call_xxx", "function": {"name": ..., "arguments": ...}}]
        """
        if not raw_text or "<tool_call" not in raw_text:
            return raw_text, []

        tool_calls: list[dict[str, Any]] = []

        def _replacer(match: re.Match) -> str:
            func_name = match.group(1).strip()
            raw_json = match.group(2).strip()
            # 清理可能包裹的 markdown 代码块符号
            if raw_json.startswith("```"):
                raw_json = re.sub(r"^```(?:json)?\s*", "", raw_json)
                raw_json = re.sub(r"\s*```$", "", raw_json)

            try:
                parsed_args = json.loads(raw_json) if raw_json else {}
            except Exception:
                # 容错：若单行命令未写成严格 JSON，尝试兜底包装
                parsed_args = {"command": raw_json} if func_name == "execute_local_command" else {}

            call_id = f"call_{uuid.uuid4().hex[:10]}"
            tool_calls.append({
                "id": call_id,
                "function": {
                    "name": func_name,
                    "arguments": json.dumps(parsed_args, ensure_ascii=False),
                },
            })
            return ""

        clean_text = cls._XML_TOOL_PATTERN.sub(_replacer, raw_text)
        # 清理外围可能残留的空 xml 代码块围栏
        clean_text = re.sub(r"```xml\s*```", "", clean_text).strip()
        return clean_text, tool_calls

