"""
统一混合工具协议适配器 (ToolProtocolAdapter)
解决痛点：
算力池中包含 Antigravity CLI 节点、OAuth 节点与 API Key 节点，部分通道不支持原生 OpenAI/Gemini tools 参数。
本模块提供：
1. 标准内置工具集定义 (execute_local_command, update_working_memory, load_skill_detail + MCP Tools)
2. 紧凑 XML ReAct 工具协议生成与解析器 (<tool_call name="...">{"arg": "val"}</tool_call>)
3. 原生 tool_calls 与 XML 协议的无感双向转换，保证 429 跨账号接力时工具调用不断链
"""
import html
import json
import re
import uuid
from typing import Any


class ToolProtocolAdapter:
    """混合工具协议转换与解析器"""

    # 军工级容错正则：无缝支持尖括号 <tool_call name="..."> 与方括号 [tool_call name="..."] 两种常见模型变体
    _XML_TOOL_PATTERN = re.compile(
        r"[<\[]tool_call\s+name=[\"']?([^\"'\]>]+)[\"']?\s*[>\]](.*?)[<\[]/tool_call[>\]]",
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
                "name": "save_long_term_memory",
                "description": "当用户明确要求“记住某偏好/植入长期记忆/保存个人画像或交互规则”时，直接将记忆条目持久化写入 SQLite 长期记忆库 (long_term_memories)，跨会话永久生效。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": ["env_info", "preference", "rule"],
                            "description": "记忆分类：env_info(本机软硬件环境/技术栈/身份背景)、preference(个人习惯/兴趣偏好)、rule(交互与回复规则)",
                        },
                        "content": {
                            "type": "string",
                            "description": "精炼、高信息密度的单条长期记忆文本",
                        },
                        "is_pinned": {
                            "type": "boolean",
                            "description": "是否设为全局核心置顶记忆（默认 true，置顶后每次对话固定优先注入且永不衰减）",
                        },
                    },
                    "required": ["category", "content"],
                },
            },
            {
                "name": "load_skill_detail",
                "description": "按需读取已挂载专业技能包（Skill）的完整 SKILL.md 操作规程及四元组挂载信息。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "技能包名称或快捷指令（如 git-commit-helper 或 /kb）",
                        }
                    },
                    "required": ["skill_name"],
                },
            },
            {
                "name": "search_skill_knowledge",
                "description": "基于本地 SQLite FTS5 BM25 引擎毫秒级按需检索指定 Skill `knowledge/` 目录下的行业规程、标准模板或参考资料。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "目标技能包 ID 或名称",
                        },
                        "query": {
                            "type": "string",
                            "description": "检索关键词（多个关键词以空格分隔）",
                        },
                    },
                    "required": ["skill_name", "query"],
                },
            },
            {
                "name": "run_skill_tool",
                "description": "执行指定 Skill `tools/` 目录下随包挂载的确定性算子脚本（如财务对账、报名规则核验、数据清洗脚本），杜绝大模型计算幻觉。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "目标技能包 ID 或名称",
                        },
                        "script_name": {
                            "type": "string",
                            "description": "算子脚本文件名（如 audit.py）",
                        },
                        "arguments": {
                            "type": "object",
                            "description": "传递给算子脚本的 JSON 参数字典",
                        },
                    },
                    "required": ["skill_name", "script_name"],
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
            {
                "name": "web_fetch",
                "description": "实时网络访问与网页正文提纯工具：抓取指定 HTTP/HTTPS 网页并自动剥离广告导航转为 Markdown 正文；秒级探测本地 localhost 端口与微服务健康状态。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "要抓取或探测的 URL 地址（如 https://github.com/... 或 http://localhost:8080/health）",
                        },
                        "extract_main_text": {
                            "type": "boolean",
                            "description": "是否对 HTML 网页进行降噪提纯提取正文 Markdown（默认 true）",
                        },
                    },
                    "required": ["url"],
                },
            },
            {
                "name": "ui_action",
                "description": "Windows 原生 UI 自动化（UIA）工具：针对无 API 封闭桌面软件（如微信、钉钉、特定行业系统、设置弹窗），穿透桌面沙箱列出窗口、提取控件树并触发原生点击、输入与聚焦，免截图且零视觉误差。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["list", "inspect", "click", "input", "focus"],
                            "description": "操作类型：list(列出可见窗口), inspect(分析指定窗口控件树), click(点击控件), input(输入文本), focus(置顶并聚焦)",
                        },
                        "target_window": {
                            "type": "string",
                            "description": "目标窗口标题关键词或 HWND 句柄（如 '微信'、'Chrome' 或 '131820'）。执行 inspect/click/input/focus 时必填。",
                        },
                        "control_selector": {
                            "type": "string",
                            "description": "目标控件的 AutomationId 或 Name（如 '发送'、'搜索'、'Close'）。执行 click/input/focus 时必填。",
                        },
                        "value": {
                            "type": "string",
                            "description": "当 action='input' 时要填充或发送的文本内容（自动通过剪贴板防乱码安全注入）。",
                        },
                        "press_enter": {
                            "type": "boolean",
                            "description": "当 action='input' 时，输入完成后是否自动按下 Enter 发送/确认（默认 false）。",
                        },
                    },
                    "required": ["action"],
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
    def _parse_tool_arguments(cls, func_name: str, raw_json: str) -> dict[str, Any]:
        """
        鲁棒性极高的工具参数解析器：自动容忍转义不全、Windows反斜杠路径、双引号连写及格式残缺。
        支持从 Markdown 代码块（```powershell ... ```）中直接提取免转义执行内容。
        """
        raw = (raw_json or "").strip()
        # 1. 直接提取内嵌的 Markdown 代码块
        code_block_m = re.search(r"```(?:powershell|bash|sh|cmd|python|json)?\s*\n([\s\S]*?)\n```", raw, re.IGNORECASE)
        if code_block_m and func_name == "execute_local_command" and '"command"' not in raw:
            return {"command": code_block_m.group(1).strip(), "reason": "执行代码块中的终端操作"}

        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw).strip()

        # 2. 直接尝试标准 JSON 解析
        try:
            return json.loads(raw)
        except Exception:
            pass

        # 3. 自动容错修复未转义的 Windows 反斜杠（如 "E:\work\..." -> "E:\\work\\..."）与非法转义符
        repaired = re.sub(r'(?<!\\)\\(?!["\\/bfnrtu])', r'\\\\', raw)
        try:
            return json.loads(repaired)
        except Exception:
            pass

        # 4. 修复模型常见双引号连写（例如 \"\", 修复为 \",）
        fixed = re.sub(r'\\""\s*,', '",', repaired)
        fixed = re.sub(r'\\""\s*}', '"}', fixed)
        try:
            return json.loads(fixed)
        except Exception:
            pass

        # 5. 针对 execute_local_command 进行模式提取
        if func_name == "execute_local_command":
            cmd_match = re.search(r'"command"\s*:\s*"(.*?)(?:"\s*,\s*"reason"|"\s*}$)', raw, re.DOTALL)
            reason_match = re.search(r'"reason"\s*:\s*"(.*?)"\s*\}', raw, re.DOTALL)
            if cmd_match:
                cmd = cmd_match.group(1).replace(r'\"', '"').replace(r'\\', '\\')
                reason = reason_match.group(1) if reason_match else "执行本地操作"
                return {"command": cmd, "reason": reason}

        # 6. 针对 write_local_file 进行模式提取
        if func_name == "write_local_file":
            path_m = re.search(r'"file_path"\s*:\s*"([^"]+)"', raw)
            content_m = re.search(r'"content"\s*:\s*"(.*?)"(?:\s*,\s*"overwrite"|"\s*}$)', raw, re.DOTALL)
            if path_m and content_m:
                return {
                    "file_path": path_m.group(1),
                    "content": content_m.group(1).replace(r'\"', '"').replace(r'\n', '\n').replace(r'\\', '\\'),
                    "overwrite": True,
                }

        # 7. 兜底包装
        return {"command": raw} if func_name == "execute_local_command" else {}

    _CORE_TOOL_NAMES = (
        "execute_local_command",
        "write_local_file",
        "read_local_file",
        "web_fetch",
        "update_working_memory",
        "load_skill_detail",
    )

    _ATTR_PATTERN = re.compile(
        r'(\w+)=(?:"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'|\{([^}]*)\}|([^\s/>]+))',
        re.DOTALL,
    )

    @classmethod
    def _parse_tag_attributes(cls, attr_str: str) -> dict[str, Any]:
        """从直接标签属性串中精准提取键值对（支持转义双引号、单引号与 JSX 花括号）"""
        args: dict[str, Any] = {}
        for m in cls._ATTR_PATTERN.finditer(attr_str):
            k = m.group(1)
            val = m.group(2) if m.group(2) is not None else (
                m.group(3) if m.group(3) is not None else (
                    m.group(4) if m.group(4) is not None else m.group(5)
                )
            )
            val = html.unescape((val or "").strip())
            # 处理转义引号
            if val.startswith('"') and val.endswith('"') and len(val) >= 2:
                val = val[1:-1]
            val = val.replace(r'\"', '"')

            if val.lower() == "true":
                args[k] = True
            elif val.lower() == "false":
                args[k] = False
            else:
                # 尝试解析可能内嵌的 JSON 字典（例如 notes="{'step': ...}"）
                if (val.startswith("{") and val.endswith("}")) or (val.startswith("[") and val.endswith("]")):
                    try:
                        args[k] = json.loads(val.replace("'", '"'))
                    except Exception:
                        args[k] = val
                else:
                    args[k] = val
        return args

    @classmethod
    def _extract_balanced_json_objects(cls, text: str) -> list[str]:
        """
        从任意混杂文本中精准提取平衡花括号包裹的 JSON 字符串。
        增强工程防线：
        1. 字符状态机全面支持单引号 ('...')、双引号 ("...") 与模板反引号 (`...`) 内的孤立花括号隔离；
        2. 若模型生成被截断或存在奇异字符导致扫描至末尾 depth > 0，启用渐进式 Auto-Close 切片降级修复。
        """
        results: list[str] = []
        i = 0
        n = len(text)
        while i < n:
            if text[i] == '{':
                start = i
                depth = 0
                in_quote: str | None = None
                escape = False
                matched_idx = -1

                j = i
                while j < n:
                    ch = text[j]
                    if in_quote:
                        if escape:
                            escape = False
                        elif ch == '\\':
                            escape = True
                        elif ch == in_quote:
                            in_quote = None
                    else:
                        if ch in ('"', "'", '`'):
                            in_quote = ch
                        elif ch == '{':
                            depth += 1
                        elif ch == '}':
                            depth -= 1
                            if depth == 0:
                                matched_idx = j
                                break
                    j += 1

                if matched_idx != -1:
                    results.append(text[start : matched_idx + 1])
                    i = matched_idx + 1
                    continue
                else:
                    # ⚠️ 安全红线：若字符串引号未闭合（in_quote is not None），说明命令/路径参数在网络中途被截断腰斩！
                    # 绝对严禁自动补齐引号与括号执行，否则极可能因残缺路径产生破坏性误操作！
                    if in_quote is not None:
                        break

                    # 仅当所有字符串参数均已合法闭合、仅缺失最外层 JSON 大括号时，才允许渐进式 Auto-Close 补齐
                    last_close = text.rfind('}', start)
                    if last_close > start:
                        candidate = text[start : last_close + 1]
                        try:
                            json.loads(candidate)
                            results.append(candidate)
                            i = last_close + 1
                            continue
                        except Exception:
                            # 尝试补齐缺失的闭合括号
                            for pad in range(1, 4):
                                try:
                                    repaired = candidate + ("}" * pad)
                                    json.loads(repaired)
                                    results.append(repaired)
                                    i = last_close + 1
                                    break
                                except Exception:
                                    pass
            i += 1
        return results

    @classmethod
    def parse_xml_tool_calls(cls, raw_text: str) -> tuple[str, list[dict[str, Any]]]:
        """
        从模型输出文本中解析所有工具调用。
        支持：
        1. 标准/方括号工具标签：<tool_call name="...">...</tool_call> 或 [tool_call name="..."]...[/tool_call]
        2. 直接工具名标签（如 <execute_local_command command="..."/> 或 <write_local_file ...>...</write_local_file>）
        """
        if not raw_text:
            return raw_text, []

        tool_calls: list[dict[str, Any]] = []
        clean_text = raw_text

        # ── 模式 1: 通用工具包装标签 (<tool_call> / [tool_call]) ──
        if "<tool_call" in clean_text or "[tool_call" in clean_text:
            def _replacer(match: re.Match) -> str:
                func_name = match.group(1).strip()
                raw_json = match.group(2).strip()
                parsed_args = cls._parse_tool_arguments(func_name, raw_json)

                call_id = f"call_{uuid.uuid4().hex[:10]}"
                tool_calls.append({
                    "id": call_id,
                    "function": {
                        "name": func_name,
                        "arguments": json.dumps(parsed_args, ensure_ascii=False),
                    },
                })
                return ""

            clean_text = cls._XML_TOOL_PATTERN.sub(_replacer, clean_text)

            # 模式 1B: 兼容无属性 <tool_call>{"name": "...", "arguments": {...}}</tool_call> 变体
            pattern_bare_tc = re.compile(
                r"[<\[]tool_call\s*[>\]]([\s\S]*?)[<\[]/tool_call[>\]]",
                re.DOTALL | re.IGNORECASE,
            )
            for m in pattern_bare_tc.finditer(clean_text):
                inner = m.group(1).strip().rstrip(">")
                raw_blocks = cls._extract_balanced_json_objects(inner)
                for blk in raw_blocks:
                    try:
                        obj = json.loads(blk)
                        if isinstance(obj, dict) and "name" in obj:
                            fn_name = str(obj["name"]).strip()
                            fn_args = obj.get("arguments") or obj.get("parameters") or {}
                            if isinstance(fn_args, str):
                                fn_args = cls._parse_tool_arguments(fn_name, fn_args)
                            elif not isinstance(fn_args, dict):
                                fn_args = {}
                            call_id = f"call_{uuid.uuid4().hex[:10]}"
                            tool_calls.append({
                                "id": call_id,
                                "function": {
                                    "name": fn_name,
                                    "arguments": json.dumps(fn_args, ensure_ascii=False),
                                },
                            })
                    except Exception:
                        pass
            clean_text = pattern_bare_tc.sub("", clean_text)

            # 模式 1C: 容错解析末尾未闭合的 <tool_call name="..."> 标签 (模型提前截断或省略了 </tool_call>)
            pattern_unclosed = re.compile(
                r"[<\[]tool_call\s+name=[\"']?([^\"'\]>]+)[\"']?\s*[>\]]([\s\S]*?)(?=(?:[<\[]tool_call|$))",
                re.IGNORECASE,
            )
            for m in pattern_unclosed.finditer(clean_text):
                fn_name = m.group(1).strip()
                after_content = m.group(2).strip()
                raw_blocks = cls._extract_balanced_json_objects(after_content)
                if raw_blocks:
                    first_json = raw_blocks[0]
                    parsed_args = cls._parse_tool_arguments(fn_name, first_json)
                    call_id = f"call_{uuid.uuid4().hex[:10]}"
                    tool_calls.append({
                        "id": call_id,
                        "function": {
                            "name": fn_name,
                            "arguments": json.dumps(parsed_args, ensure_ascii=False),
                        },
                    })
            clean_text = pattern_unclosed.sub("", clean_text)

        # ── 模式 2: 模型自发输出的直接工具标签（如 <execute_local_command command="..."/>） ──
        for t_name in cls._CORE_TOOL_NAMES:
            if f"<{t_name}" not in clean_text:
                continue

            # A. 自闭合标签: <t_name attr="val" ... />
            pattern_self = re.compile(rf"<{t_name}\s+([^>]*?)\s*/>", re.DOTALL | re.IGNORECASE)
            for m in pattern_self.finditer(clean_text):
                attr_str = m.group(1)
                args = cls._parse_tag_attributes(attr_str)
                call_id = f"call_{uuid.uuid4().hex[:10]}"
                tool_calls.append({
                    "id": call_id,
                    "function": {
                        "name": t_name,
                        "arguments": json.dumps(args, ensure_ascii=False),
                    },
                })
            clean_text = pattern_self.sub("", clean_text)

            # B. 成对标签: <t_name ...>...</t_name>
            pattern_pair = re.compile(rf"<{t_name}(?:\s+([^>]*?))?\s*>(.*?)</{t_name}>", re.DOTALL | re.IGNORECASE)
            for m in pattern_pair.finditer(clean_text):
                attr_str = m.group(1) or ""
                body_str = (m.group(2) or "").strip()
                args = cls._parse_tag_attributes(attr_str) if attr_str else {}
                if body_str:
                    try:
                        body_json = json.loads(body_str)
                        if isinstance(body_json, dict):
                            args.update(body_json)
                    except Exception:
                        if t_name == "execute_local_command" and "command" not in args:
                            args["command"] = body_str
                        elif t_name == "write_local_file" and "content" not in args:
                            args["content"] = body_str

                call_id = f"call_{uuid.uuid4().hex[:10]}"
                tool_calls.append({
                    "id": call_id,
                    "function": {
                        "name": t_name,
                        "arguments": json.dumps(args, ensure_ascii=False),
                    },
                })
            clean_text = pattern_pair.sub("", clean_text)

        # ── 模式 3: 隐式工具调用容错自愈（捕获裸 JSON、带有 </slot> 或嵌套花括号脚本块的工具调用） ──
        if not tool_calls and ('"command"' in clean_text or '"file_path"' in clean_text or '"skill_name"' in clean_text or '"url"' in clean_text):
            raw_json_blocks = cls._extract_balanced_json_objects(clean_text)
            for block in raw_json_blocks:
                try:
                    obj = json.loads(block)
                    if isinstance(obj, dict):
                        call_id = f"call_{uuid.uuid4().hex[:10]}"
                        if "command" in obj and isinstance(obj["command"], str):
                            tool_calls.append({
                                "id": call_id,
                                "function": {
                                    "name": "execute_local_command",
                                    "arguments": json.dumps({
                                        "command": obj["command"],
                                        "reason": obj.get("reason", "执行本地终端操作与异常自愈"),
                                    }, ensure_ascii=False),
                                },
                            })
                            clean_text = clean_text.replace(block, "")
                        elif "file_path" in obj and "content" in obj:
                            tool_calls.append({
                                "id": call_id,
                                "function": {
                                    "name": "write_local_file",
                                    "arguments": json.dumps(obj, ensure_ascii=False),
                                },
                            })
                            clean_text = clean_text.replace(block, "")
                        elif "file_path" in obj and isinstance(obj["file_path"], str):
                            tool_calls.append({
                                "id": call_id,
                                "function": {
                                    "name": "read_local_file",
                                    "arguments": json.dumps({
                                        "file_path": str(obj["file_path"]).strip(),
                                    }, ensure_ascii=False),
                                },
                            })
                            clean_text = clean_text.replace(block, "")
                        elif "skill_name" in obj and "query" in obj:
                            tool_calls.append({
                                "id": call_id,
                                "function": {
                                    "name": "search_skill_knowledge",
                                    "arguments": json.dumps(obj, ensure_ascii=False),
                                },
                            })
                            clean_text = clean_text.replace(block, "")
                        elif "skill_name" in obj and "script_name" in obj:
                            tool_calls.append({
                                "id": call_id,
                                "function": {
                                    "name": "run_skill_tool",
                                    "arguments": json.dumps(obj, ensure_ascii=False),
                                },
                            })
                            clean_text = clean_text.replace(block, "")
                        elif "skill_name" in obj:
                            tool_calls.append({
                                "id": call_id,
                                "function": {
                                    "name": "load_skill_detail",
                                    "arguments": json.dumps(obj, ensure_ascii=False),
                                },
                            })
                            clean_text = clean_text.replace(block, "")
                        elif "url" in obj and isinstance(obj["url"], str):
                            tool_calls.append({
                                "id": call_id,
                                "function": {
                                    "name": "web_fetch",
                                    "arguments": json.dumps({
                                        "url": str(obj["url"]).strip(),
                                        "extract_main_text": bool(obj.get("extract_main_text", True)),
                                    }, ensure_ascii=False),
                                },
                            })
                            clean_text = clean_text.replace(block, "")
                except Exception:
                    pass

        # 彻底清洗残余的各种模型畸形标记（如 </slot>, <slot>, </tool_code>, ```xml 空围栏及孤立的 ```xml / xml 行等）
        clean_text = re.sub(r"</?slot\b[^>]*>", "", clean_text, flags=re.IGNORECASE)
        clean_text = re.sub(r"</?tool_code\b[^>]*>", "", clean_text, flags=re.IGNORECASE)
        clean_text = re.sub(r"</?tool_call\b[^>]*>", "", clean_text, flags=re.IGNORECASE)
        clean_text = re.sub(r"\[/?tool_call\b[^\]]*\]", "", clean_text, flags=re.IGNORECASE)
        clean_text = re.sub(r"```(?:xml|json|tool_call)?\s*```", "", clean_text, flags=re.IGNORECASE)
        clean_text = re.sub(r"^\s*```xml\s*$", "", clean_text, flags=re.IGNORECASE | re.MULTILINE)
        clean_text = re.sub(r"^\s*xml\s*$", "", clean_text, flags=re.IGNORECASE | re.MULTILINE)
        clean_text = re.sub(r"\[调用工具:\s*[^\]]+\]", "", clean_text).strip()
        return clean_text, tool_calls

