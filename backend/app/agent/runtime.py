"""
智能体双层循环编排运行时 (AgentRuntime)
核心架构：
- 外层循环 (Inter-Turn): 会话级多轮对话演进，维护 turn_id 递增、场景模式切换与回合后异步记忆提炼
- 内层循环 (Intra-Turn ReAct): 单次提问内的多步“推理 -> 工具拦截 -> 安全审批/执行 -> Observation入库 -> 再次推理”闭环
"""
import asyncio
import hashlib
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Awaitable, Callable

from ..core.logging_config import logger
from ..repository.conversation_repository import ConversationRepository
from ..repository.db import db_manager
from ..repository.message_repository import ConversationDeletedError, MessageRepository
from ..repository.search_repository import SearchRepository
from ..service.agent_runner import cancel_cli_processes
from ..service.engine_pool import engine_pool
from ..service.audit_service import audit_service
from ..service.attachment_service import attachment_service
from ..memory.experience_store import episodic_experience_store
from ..memory.fact_manager import fact_manager
from ..memory.ledger import event_ledger
from ..memory.long_term import long_term_memory
from ..memory.policy_guard import MemoryPolicyGuard
from ..memory.session_summary import session_summary_manager
from ..memory.workspace_fts import WorkspaceFTSEngine
from ..extensions.skill_loader import skill_loader
from ..extensions.mcp_manager import mcp_manager
from ..extensions.mcp_file_bridge import (
    download_mcp_artifact_to_workspace,
    upload_workspace_file_to_mcp,
)
from ..skills.experience_distiller import ExecutionTrace, experience_distiller
from .checkpoint_manager import ShadowCheckpointManager
from .policy_interceptor import PolicyInterceptor, approval_event_manager
from .shell_executor import shell_executor
from .tool_protocol import ToolProtocolAdapter
from .context_builder import ContextBuilder
from .context_compressor import (
    LocalContextCompressor,
    get_effective_rollover_ratio,
    read_ephemeral_tool_ref,
    resolve_scrub_char_threshold,
)
from .ui_operator import ui_operator
from ..repository.loop_run_repository import TaskLoopRepository
from ..repository.tool_ref_repository import ToolRefRepository
from ..repository.token_usage_repository import TokenUsageRepository, _build_turn_summary_from_rows
from ..tools.code_surgery import (
    read_local_file_sliced,
    apply_search_replace_blocks,
)
from ..tools.ast_skeleton import (
    get_workspace_skeleton,
    validate_syntax_before_write,
)
from ..tools.git_checkpoint import (
    create_shadow_snapshot,
    restore_shadow_snapshot,
)


class AgentRuntime:
    """微内核智能体双层循环编排器"""

    DEFAULT_REACT_STEPS = 16
    MAX_REACT_STEPS = 500  # 仅作极端防爆保护，常规推进按需动态扩展，仅在重复同一失败方案超过 3 次时熔断
    AUTO_ROLLOVER_WATERMARK_RATIO = 0.90  # 上下文水位达到 90% 时自动蒸馏交接并在同工作区创建接力新对话

    @staticmethod
    def _compute_next_rollover_title(base_title: str) -> str:
        """为自动接力新会话生成递增序号标题（如 `原标题 · 接力 #2`）。"""
        clean = (base_title or "新会话").strip() or "新会话"
        match = re.search(r"^(.*?)\s*·\s*接力\s*#(\d+)$", clean)
        if match:
            prefix = match.group(1).strip() or "新会话"
            next_idx = int(match.group(2)) + 1
            return f"{prefix} · 接力 #{next_idx}"
        return f"{clean[:60]} · 接力 #2"

    # 活跃流式执行注册表：conversation_id -> {"token": str, "cancel_event": asyncio.Event}
    _active_streams: dict[str, dict[str, Any]] = {}
    # S3 会话级单调递增 MCP 已激活服务集合（仅增不减以守护 Zone 1 缓存，直到分块坍缩推进锚点）
    _activated_mcp_servers_by_conv: dict[str, set[str]] = {}
    _mcp_anchor_by_conv: dict[str, int] = {}
    # S7 会话级读文件去重缓存: conv_id -> {(abs_path, start_line, end_line): (mtime_ns, size, turn_id, step_ref, line_count)}
    _session_file_read_cache_by_conv: dict[
        str, dict[tuple[str, int | None, int | None], tuple[int, int, int, str, int]]
    ] = {}

    def __init__(self) -> None:
        self.tool_adapter = ToolProtocolAdapter

    @classmethod
    def _invalidate_file_read_cache(cls, conv_id: str, file_path: str | Path) -> None:
        """文件发生写入或变更时，失效该会话下对应路径的读文件去重缓存。"""
        cache = cls._session_file_read_cache_by_conv.get(conv_id)
        if not cache:
            return
        try:
            abs_target = str(Path(file_path).resolve())
        except Exception:
            abs_target = str(file_path)
        keys_to_remove = [k for k in cache if k[0] == abs_target]
        for k in keys_to_remove:
            cache.pop(k, None)

    @staticmethod
    def _compute_tool_args_hash(args: Any) -> str:
        """S10: 计算工具参数的规范化哈希，用于检测无写操作穿插的重复调用。"""
        try:
            canonical = json.dumps(args or {}, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            canonical = str(args)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def _resolve_step_thinking_level(
        cls,
        *,
        base_thinking_level: str,
        lock_high_thinking: bool,
        active_scene: str,
        step_idx: int,
        is_planning_phase: bool,
        is_forced_final_step: bool,
        recovered_error_count: int,
        consecutive_stalls: int,
        last_step_error_info: dict[str, Any] | None,
        is_chitchat_turn: bool,
        user_message: str,
        has_attachments: bool = False,
    ) -> str:
        """S6: 根据当前 ReAct 步语义动态调节思考档位（仅在 base == 'high' 且未锁回时向下自适应降载）。"""
        normalized_base = (base_thinking_level or "high").strip().lower()
        if normalized_base != "high":
            return normalized_base
        if lock_high_thinking:
            return "high"

        if is_forced_final_step or active_scene == "oneshot" or is_chitchat_turn or active_scene != "agent":
            return "low"

        if (
            is_planning_phase
            or recovered_error_count > 0
            or last_step_error_info is not None
            or consecutive_stalls > 0
        ):
            return "high"

        if step_idx == 0:
            msg = (user_message or "").strip()
            has_complex_feature = (
                has_attachments
                or bool(cls._TASK_INTENT_KEYWORDS_RE.search(msg))
                or len(msg) > 100
                or "```" in msg
            )
            return "high" if has_complex_feature else "medium"

        return "medium"

    def _append_schema_hint_if_arg_error(
        self,
        func_name: str,
        tool_output: str,
        *,
        args_parse_failed: bool = False,
    ) -> str:
        """S4 联动回补：当工具因参数格式错误或缺失失败时，自动附带该单工具的完整 JSON Schema 说明。"""
        out_str = str(tool_output or "")
        out_lower = out_str.lower()
        is_arg_error = args_parse_failed or (
            out_str.startswith("Error:")
            and any(
                kw in out_lower
                for kw in ("缺少必要参数", "参数", "missing", "required", "invalid", "schema", "json", "格式")
            )
        )
        if not is_arg_error:
            return out_str
        schema_hint_fn = getattr(self.tool_adapter, "build_single_tool_schema_hint", None)
        if not callable(schema_hint_fn):
            return out_str
        try:
            schema_hint = schema_hint_fn(func_name)
            if schema_hint and str(schema_hint).strip():
                return f"{out_str}\n\n{str(schema_hint).strip()}"
        except Exception as hint_err:
            logger.debug(f"[AgentRuntime] 附带单工具 Schema 提示跳过: {hint_err}")
        return out_str

    @staticmethod
    def _is_path_in_allowed_dirs(
        target_file: Path,
        allowed_write_dirs: list[str] | None,
        base_dir: Path,
    ) -> bool:
        """校验目标写入路径是否落在授权目录白名单内（allowed_write_dirs 为空时放行）。"""
        if not allowed_write_dirs:
            return True
        resolved_target = target_file.resolve()
        for raw_dir in allowed_write_dirs:
            d_str = str(raw_dir or "").strip()
            if not d_str:
                continue
            cand = Path(d_str)
            if not cand.is_absolute():
                cand = (base_dir / cand).resolve()
            else:
                cand = cand.resolve()
            if resolved_target == cand or resolved_target.is_relative_to(cand):
                return True
        return False

    @classmethod
    async def _assemble_agent_tools(
        cls,
        *,
        conv_id: str,
        user_message: str,
        collapse_anchor_turn_id: int = 0,
        extra_activate_servers: list[str] | set[str] | None = None,
        allowed_tools: list[str] | None = None,
        include_swarm: bool = False,
    ) -> tuple[list[dict[str, Any]], str]:
        """装配 Agent 模式可用工具集（支持 MCP 两段式目录、Swarm 原语按需挂载与工具白名单过滤）。"""
        all_builtin = ToolProtocolAdapter.get_builtin_tools()
        active_tools: list[dict[str, Any]] = list(all_builtin)
        if include_swarm:
            active_tools.extend(ToolProtocolAdapter.get_swarm_tools())
        mcp_catalog_prompt = ""

        prev_anchor = cls._mcp_anchor_by_conv.get(conv_id, 0)
        if collapse_anchor_turn_id > prev_anchor:
            cls._activated_mcp_servers_by_conv[conv_id] = set()
            cls._mcp_anchor_by_conv[conv_id] = collapse_anchor_turn_id
        elif conv_id not in cls._mcp_anchor_by_conv:
            cls._mcp_anchor_by_conv[conv_id] = collapse_anchor_turn_id

        activated_set = cls._activated_mcp_servers_by_conv.setdefault(conv_id, set())

        def _filter_allowed(tools_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
            if not allowed_tools:
                return tools_list
            allow_set = {str(t).strip() for t in allowed_tools if str(t).strip()}
            if not allow_set:
                return tools_list
            return [t for t in tools_list if str(t.get("name", "")).strip() in allow_set]

        try:
            catalog = await mcp_manager.get_mcp_catalog_summary()
        except Exception as e:
            logger.warning(f"加载 MCP 目录摘要异常: {e}")
            return _filter_allowed(active_tools), mcp_catalog_prompt

        total_tools = int(catalog.get("total_tools") or 0)
        total_schema_chars = int(catalog.get("total_schema_chars") or 0)
        servers_meta: dict[str, list[dict[str, str]]] = catalog.get("servers") or {}

        if total_tools == 0:
            return _filter_allowed(active_tools), ""

        # 工具总数 <= 5 且 Schema 体积 <= 2000 字符时直接全量挂载，免去额外 discover 往返
        if total_tools <= 5 and total_schema_chars <= 2000:
            active_tools.extend(catalog.get("all_tools") or [])
            return _filter_allowed(active_tools), ""

        # 两段式延迟挂载：按显式请求、关键词与热区历史单调递增激活 Server
        if extra_activate_servers:
            for srv in extra_activate_servers:
                s_clean = str(srv).strip()
                if s_clean in servers_meta:
                    activated_set.add(s_clean)

        query_lower = (user_message or "").lower()
        for srv_name in servers_meta.keys():
            if srv_name.lower() in query_lower:
                activated_set.add(srv_name)

        try:
            min_turn = max(1, int(collapse_anchor_turn_id or 0) + 1)
            hot_msgs = await MessageRepository.get_by_turn_window(conv_id, min_turn)
            for hm in hot_msgs:
                combined = f"{hm.content or ''}\n{hm.tool_calls_json or ''}"
                if "mcp__" in combined:
                    for srv_name in servers_meta.keys():
                        if f"mcp__{srv_name}__" in combined:
                            activated_set.add(srv_name)
        except Exception as hist_err:
            logger.debug(f"[AgentRuntime] 扫描热区历史 MCP 调用跳过: {hist_err}")

        if activated_set:
            try:
                activated_tools = await mcp_manager.get_tools_by_servers(sorted(activated_set))
                active_tools.extend(activated_tools)
            except Exception as act_err:
                logger.warning(f"按需加载已激活 MCP 工具异常: {act_err}")

        mcp_catalog_prompt = str(catalog.get("catalog_text") or "").strip()
        return _filter_allowed(active_tools), ("" if allowed_tools else mcp_catalog_prompt)

    _CHITCHAT_GREETING_RE = re.compile(
        r"^\s*(?:你好|您好|hi+|hello+|hey+|早安|早上好|下午好|晚上好|在吗|在不在|谢谢|多谢|感谢|再见|拜拜|晚安|你是谁|你叫什么|你能做什么|你会什么|介绍一下你自己|讲个笑话|陪我聊聊天)[！!。.?？\s]*$",
        re.IGNORECASE,
    )
    _TASK_INTENT_KEYWORDS_RE = re.compile(
        r"(?:/[a-zA-Z0-9_-]+|\.(?:py|ts|tsx|js|jsx|json|md|csv|xlsx|sql|yaml|yml|sh|ps1|txt|pdf|png|jpg)\b|"
        r"分析|重构|修复|调试|排查|检查|创建|新建|生成|编写|写一个|修改|编辑|删除|清理|运行|执行|测试|验证|部署|构建|打包|"
        r"读取|写入|搜索|检索|导出|汇总|统计|对比|整理|画图|渲染|优化|安装|配置|回滚|提炼|蒸馏|任务|代码|文件|目录|脚本|数据库|接口)",
        re.IGNORECASE,
    )

    @classmethod
    def _is_likely_pure_chitchat(
        cls,
        user_message: str,
        *,
        has_pending_loop: bool = False,
        has_attachments: bool = False,
        explicit_execute_unlock: bool = False,
    ) -> bool:
        """
        三层纯闲聊判断（第一层事前语义识别 + 第三层上下文防误伤）：
        1. 若存在未完结/待审批的历史任务、附带上传文件或显式执行确认标记，绝不视为闲聊；
        2. 命中明确寒暄词或超短无工程动作关键词的纯问答返回 True，避免无谓激活 LoopHUD。
        """
        if has_pending_loop or has_attachments or explicit_execute_unlock:
            return False
        msg = (user_message or "").strip()
        if not msg:
            return True
        if msg.startswith("/") or len(msg) > 160:
            return False
        if cls._CHITCHAT_GREETING_RE.match(msg):
            return True
        if cls._TASK_INTENT_KEYWORDS_RE.search(msg):
            return False
        return len(msg) <= 36

    @classmethod
    def cancel_session_stream(cls, conversation_id: str) -> bool:
        """主动中止指定会话正在执行的 ReAct 推理流与关联 CLI 子进程"""
        if not conversation_id:
            return False
        entry = cls._active_streams.get(conversation_id)
        cancelled = False
        if entry:
            ev = entry.get("cancel_event")
            if ev and not ev.is_set():
                ev.set()
                cancelled = True
        killed_cli = cancel_cli_processes(conversation_id)
        return cancelled or killed_cli > 0

    PLANNING_SAFE_TOOLS = frozenset({
        "read_local_file",
        "read_tool_ref",
        "search_memory_history",
        "search_skill_knowledge",
        "load_skill_detail",
        "web_fetch",
        "update_working_memory",
    })

    ARCHIVE_ONLY_TOOLS = frozenset({
        "update_working_memory",
        "save_long_term_memory",
    })

    @staticmethod
    def _is_substantive_deliverable_text(text: str) -> bool:
        """判断文本是否为面向用户的完整交付报告（而非简短的过程过渡旁白）。"""
        clean = (text or "").strip()
        if not clean:
            return False
        if len(clean) >= 120:
            return True
        if len(clean) >= 40 and (
            bool(re.search(r"^#{1,4}\s+\S", clean, re.MULTILINE))
            or bool(re.search(r"\|[\s:-]+\|", clean))
            or len(clean.splitlines()) >= 3
        ):
            return True
        return False

    # 识别无工具纯文本伪落盘/伪成功宣称模式
    _HALLUCINATED_WRITE_CLAIM_PATTERNS = (
        re.compile(r"写入文件成功[:：]\s*[`'\"]?([^\s`'\"()（）,，\r\n]+?\.\w{1,8})", re.IGNORECASE),
        re.compile(
            r"(?:已成功(?:生成|创建|写入|更新|同步更新)|已完成(?:生成|创建|写入)|已物理落盘)[^。\r\n]{0,24}?[`'\"]?([A-Za-z0-9_.\u4e00-\u9fa5/\\-]+\.\w{1,8})",
            re.IGNORECASE,
        ),
        re.compile(
            r"[`'\"]?([A-Za-z0-9_.\u4e00-\u9fa5/\\-]+\.\w{1,8})[`'\"]?\s*(?:写入完毕|已成功物理落盘|created successfully|written successfully)",
            re.IGNORECASE,
        ),
    )
    _HALLUCINATED_WRITE_GENERIC_RE = re.compile(
        r"(?:文件已成功物理落盘|Physical files created successfully|代码生成脚本\s*[`'\"A-Za-z0-9_.\-]+\s*写入完毕)",
        re.IGNORECASE,
    )
    _DECLARED_SIZE_RE = re.compile(
        r"[`'\"]?([A-Za-z0-9_.\u4e00-\u9fa5/\\-]+\.\w{1,8})[`'\"]?\s*(?:\(\s*(?:共\s*)?(?:字节数|文件大小|size)[:：]?\s*(\d{3,})\s*(?:字节|字符|bytes?)?\s*\)|\s+(\d{3,})\s*(?:字节|bytes?)?)",
        re.IGNORECASE,
    )
    _PREMATURE_POST_TOOL_HALLUCINATION_RE = re.compile(
        r"(?:Physical files created successfully|写入文件成功|写入完毕|已成功(?:生成并写入|同步更新|物理落盘)|文件已\s*100%\s*真实落盘|Ran\s+\d+\s+tests?\s+in\s+\d|静态健全性检查\s*100%|^\s*#{2,3}\s+)",
        re.IGNORECASE | re.MULTILINE,
    )
    _FIRST_TOOL_TAG_RE = re.compile(
        r"(?:<\s*tool_call\b|\[\s*tool_call\b|<\s*/?\s*[\uFF5C|][\uFF5C|\s]*DSML\b|<\s*(?:execute_local_command|write_local_file|replace_in_file|read_local_file|update_working_memory)\b)",
        re.IGNORECASE,
    )
    _WRITE_SHELL_HINT_RE = re.compile(
        r"(?:write_text|write_bytes|writelines|shutil\.|os\.rename|os\.replace|"
        r"open\([^)]*['\"][wa+]|Set-Content|Add-Content|Out-File|New-Item|"
        r"(?<![=!<>])>{1,2}(?![=])|python\s+[^\s-][^\r\n]*\.py\b|\.(?:ps1|sh|bat)\b|"
        r"npm\s+run\s+build|vite\s+build|pip\s+install)",
        re.IGNORECASE,
    )

    @classmethod
    def _detect_unverified_write_hallucination(
        cls,
        text: str,
        *,
        cwd: str | None,
        produced_artifacts: list[str],
        executed_mutating_shell: bool,
    ) -> list[str]:
        """检测纯文本中宣称文件已落盘，但本轮未真写盘或磁盘与声明不符的幻觉清单。"""
        clean = (text or "").strip()
        if not clean:
            return []

        ws_root = Path(cwd).resolve() if cwd else None
        produced_norm = {p.replace("\\", "/").lstrip("./").lower() for p in produced_artifacts if p}
        produced_names = {Path(p).name.lower() for p in produced_artifacts if p}

        # 收集文本中附带的“声称字节/字符数”用于核对同名旧文件（如声明 36766 字节但磁盘仍是旧文件 25461 字节）
        declared_sizes: dict[str, int] = {}
        for m_sz in cls._DECLARED_SIZE_RE.finditer(clean):
            fname_raw = (m_sz.group(1) or "").strip()
            sz_str = m_sz.group(2) or m_sz.group(3)
            if fname_raw and sz_str:
                try:
                    declared_sizes[Path(fname_raw).name.lower()] = int(sz_str)
                except ValueError:
                    pass

        unverified: list[str] = []
        claimed_files: list[str] = []
        for pat in cls._HALLUCINATED_WRITE_CLAIM_PATTERNS:
            for m in pat.finditer(clean):
                # 跳过明确否定语境（如“未写入文件成功” / “尚未物理落盘”）
                prefix_ctx = clean[max(0, m.start() - 12):m.start()]
                if re.search(r"(?:未|不|尚未|没有|failed|not)\s*$", prefix_ctx, re.IGNORECASE):
                    continue
                cand = (m.group(1) or "").strip().strip("`'\".,;:!?")
                if cand and cand not in claimed_files:
                    claimed_files.append(cand)

        for cand in claimed_files:
            norm_cand = cand.replace("\\", "/").lstrip("./").lower()
            base_cand = Path(cand).name.lower()
            if norm_cand in produced_norm or base_cand in produced_names:
                continue
            if ws_root and ws_root.is_dir():
                cand_path = (ws_root / cand.replace("\\", "/").lstrip("/"))
                try:
                    resolved = cand_path.resolve()
                    is_in_ws = resolved.is_relative_to(ws_root)
                except (ValueError, OSError):
                    is_in_ws = False
                if not is_in_ws or not cand_path.exists() or not cand_path.is_file():
                    unverified.append(cand)
                    continue
                expected_sz = declared_sizes.get(base_cand)
                # 本轮既未通过文件工具写盘、也未执行疑似写盘的 Shell，或磁盘字节数明显与声明不符 -> 视为复用旧文件张冠李戴
                if not executed_mutating_shell or (
                    expected_sz is not None and abs(cand_path.stat().st_size - expected_sz) > 64
                ):
                    unverified.append(cand)
            else:
                unverified.append(cand)

        if not unverified and cls._HALLUCINATED_WRITE_GENERIC_RE.search(clean):
            if not produced_artifacts and not executed_mutating_shell:
                unverified.append("声明生成的文件")

        return unverified

    @classmethod
    def _strip_premature_post_tool_hallucination(
        cls,
        raw_text: str,
        clean_text: str,
        *,
        has_substantive_tools: bool,
    ) -> tuple[str, bool]:
        """若实质性工具调用步骤在首个 <tool_call> 后夹带了提前脑补的执行结果/交付报告，剥离后半段伪剧透。"""
        if not has_substantive_tools or not clean_text or not raw_text:
            return clean_text, False

        first_tag = cls._FIRST_TOOL_TAG_RE.search(raw_text)
        if not first_tag:
            return clean_text, False

        pre_raw = raw_text[:first_tag.start()].strip()
        post_raw = raw_text[first_tag.start():]
        def _clean_segment(seg_raw: str) -> str:
            parsed_seg, _ = ToolProtocolAdapter.parse_xml_tool_calls(seg_raw)
            return ToolProtocolAdapter.strip_pseudo_transcript_prefixes(parsed_seg or "").strip()

        # 仅查看首个工具标签之后除工具调用外的文本片段，判断是否夹带伪结果/提前终稿
        post_clean = _clean_segment(post_raw)
        if not post_clean:
            return clean_text, False

        looks_like_fake_deliverable = bool(
            cls._PREMATURE_POST_TOOL_HALLUCINATION_RE.search(post_clean)
            or (len(post_clean) >= 320 and ("\n### " in post_clean or "\n## " in post_clean or "```" in post_clean))
        )
        if not looks_like_fake_deliverable:
            return clean_text, False

        pre_clean = _clean_segment(pre_raw)
        if not pre_clean or cls._PREMATURE_POST_TOOL_HALLUCINATION_RE.search(pre_clean):
            return "正在调用本地工具执行并核验结果...", True
        return pre_clean, True

    @classmethod
    def _detect_unverified_action_claims(
        cls,
        text: str,
        executed_tools: list[dict[str, Any]],
    ) -> bool:
        """当开启 verify_tool_call 时，检测模型是否在没有任何成功工具调用的情况下宣称已完成查询或处理"""
        if not text or len(text.strip()) < 10:
            return False
        # 只要存在成功的真实工具调用，就不算纯文本虚假声明
        if any(t.get("status") == "success" for t in executed_tools):
            return False

        # 匹配虚假查询/完成声明模式
        claim_patterns = (
            r"(?:已经?为(?:您|你)|已成功|已完成)(?:查询|收集|读取|汇总|检索|整理|提取|抓取|扫描|分析|统计|生成|记录)",
            r"(?:查询|收集|整理|汇总|扫描|检索)已?完成",
            r"今日(?:所有的?|全部)?交互(?:内容|记录)?已(?:收集|整理|汇总|记录|归档)",
            r"以上是?从(?:数据库|本地日志|工作区|会话记录)中?(?:查询|收集|提取|检索)到的",
        )
        for pat in claim_patterns:
            if re.search(pat, text):
                return True
        return False

    EXECUTION_APPROVAL_MARKERS = (
        "实施方案已通过用户审批",
        "批准执行以下步骤清单",
        "全部确认完毕",
        "解锁执行权限",
    )

    @classmethod
    def detect_prior_execution_unlock(cls, history_messages: list[Any]) -> bool:
        """检查当前会话既往消息中是否已完成方案审批或真实执行过非规划写盘/终端工具。

        用于解除同一会话已获执行许可后，因策略仍保留在 plan_first 而产生的二次强制锁死。
        """
        if not history_messages:
            return False

        tool_outputs_by_id: dict[str, str] = {}
        for msg in history_messages:
            role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", "")
            tc_id = msg.get("tool_call_id") if isinstance(msg, dict) else getattr(msg, "tool_call_id", None)
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
            if role == "tool" and tc_id:
                tool_outputs_by_id[str(tc_id)] = str(content or "")

        for msg in history_messages:
            role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", "")
            content = str((msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")) or "")
            if role == "user" and any(marker in content for marker in cls.EXECUTION_APPROVAL_MARKERS):
                return True

            if role in ("assistant", "model"):
                raw_tc_json = msg.get("tool_calls_json") if isinstance(msg, dict) else getattr(msg, "tool_calls_json", None)
                if not raw_tc_json:
                    continue
                try:
                    tc_list = json.loads(raw_tc_json) if isinstance(raw_tc_json, str) else raw_tc_json
                except Exception:
                    continue
                if not isinstance(tc_list, list):
                    continue
                for tc in tc_list:
                    func_name = str((tc.get("function") or {}).get("name") or "").strip()
                    call_id = str(tc.get("id") or "").strip()
                    if func_name and func_name not in cls.PLANNING_SAFE_TOOLS:
                        out_text = tool_outputs_by_id.get(call_id, "")
                        if out_text and not out_text.lstrip().startswith("Denied:"):
                            return True
        return False

    @staticmethod
    def compute_planning_phase(
        *,
        active_scene: str,
        execution_strategy: str,
        approved_plan_steps: list[dict[str, Any]] | None,
        inquiry_confirmed: bool,
        inquiry_action: str | None,
        has_prior_execution_unlock: bool,
    ) -> bool:
        """统一计算本轮是否处于物理只读硬门控规划态。"""
        if active_scene != "agent":
            return False

        normalized_action = (inquiry_action or "").strip().lower() or None
        if approved_plan_steps or (inquiry_confirmed and normalized_action == "execute_now"):
            return False

        if normalized_action == "clarify_only":
            return execution_strategy == "plan_first"

        if execution_strategy == "plan_first":
            return not has_prior_execution_unlock

        return False

    @staticmethod
    def _filter_visual_attachments(attachments: list[Any] | None) -> list[Any]:
        """提取本轮上传的图片/PDF 视觉附件，用于同 Turn 多步调用中保留多模态视野。"""
        if not attachments:
            return []
        visuals: list[Any] = []
        for att in attachments:
            att_dict = att if isinstance(att, dict) else att.model_dump()
            name = str(att_dict.get("name") or "")
            mime = str(att_dict.get("mime_type") or "").lower()
            cat = str(att_dict.get("category") or "").lower()
            if not cat or not mime:
                _, inferred_cat, canonical_mime = attachment_service.classify_extension(name)
                cat = cat or (inferred_cat or "")
                mime = mime or (canonical_mime or "")
            if cat in ("image", "pdf") or mime.startswith("image/") or mime == "application/pdf":
                visuals.append(att)
        return visuals

    @staticmethod
    async def _ingest_confirmed_facts(
        *,
        confirmed_facts: list[Any] | None,
        workspace_id: str,
        conversation_id: str,
        turn_id: int,
        incognito: bool,
        scene_mode: str,
    ) -> list[dict[str, Any]]:
        """
        将前端交互对齐卡提交的结构化确认事实直接写入 event_ledger 与 memory_facts（0 额外 LLM Token）。
        """
        if incognito or scene_mode == "oneshot" or not confirmed_facts:
            return []

        normalized_items: list[dict[str, str]] = []
        for item in confirmed_facts:
            d = item if isinstance(item, dict) else item.model_dump()
            f_key = str(d.get("fact_key") or "").strip()
            f_val = str(d.get("fact_value") or "").strip()
            if not f_key or not f_val:
                continue
            normalized_items.append({
                "fact_key": f_key,
                "fact_value": f_val,
                "category": str(d.get("category") or "biz_rule").strip(),
                "workspace_scope": str(d.get("workspace_scope") or "workspace").strip().lower(),
            })

        if not normalized_items:
            return []

        evt = await event_ledger.append_event(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            event_type="inquiry_confirmed",
            actor="user",
            payload={"facts": normalized_items},
            incognito=False,
        )
        source_evt_id = evt["id"] if evt else None

        saved_facts: list[dict[str, Any]] = []
        for item in normalized_items:
            target_ws = "GLOBAL" if item["workspace_scope"] == "global" else workspace_id
            try:
                res = await fact_manager.upsert_fact(
                    fact_key=item["fact_key"],
                    fact_value=item["fact_value"],
                    workspace_id=target_ws,
                    category=item["category"],
                    source_type="inquiry",
                    source_event_id=source_evt_id,
                    incognito=False,
                    scene_mode=scene_mode,
                    verified=True,
                )
                if res.get("fact"):
                    saved_facts.append(res["fact"])
            except Exception as e:
                logger.warning(f"[AgentRuntime] 对齐卡确认事实落盘失败 ({item['fact_key']}): {e}")

        return saved_facts

    @staticmethod
    async def _execute_search_memory_history(
        *,
        query: str,
        scope: str = "all",
        limit: int = 5,
        workspace_id: str = "default",
        target_cwd: str | None = None,
        incognito: bool = False,
    ) -> str:
        """
        执行按需 FTS5 历史对话、事实变更历史与工作区文档/源码联合召回（全量结果均经隐私脱敏）。
        """
        clean_q = (query or "").strip()
        if not clean_q:
            return "Error: search_memory_history 参数 query 不能为空。"

        clean_scope = (scope or "all").strip().lower()
        if clean_scope not in ("messages", "facts_history", "workspace", "all"):
            clean_scope = "all"
        safe_limit = max(1, min(int(limit or 5), 50))

        sections: list[str] = []

        # 1. 历史消息召回：支持按日期（today/今天/今日/yesterday/昨天/YYYY-MM-DD）全量收集当日交互，或 FTS5 BM25 检索
        if not incognito and clean_scope in ("messages", "all"):
            date_target: str | None = None
            q_lower = clean_q.lower()
            if q_lower in ("today", "今天", "今日", "daily"):
                date_target = datetime.now().strftime("%Y-%m-%d")
            elif q_lower in ("yesterday", "昨天", "昨日"):
                from datetime import timedelta
                date_target = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", clean_q):
                date_target = clean_q

            if date_target:
                day_rows: list[dict[str, Any]] = []
                try:
                    async with db_manager.get_connection() as conn:
                        async with conn.execute(
                            """
                            SELECT m.role, m.content, m.created_at, m.conversation_id,
                                   COALESCE(c.title, m.conversation_id) AS conversation_title
                            FROM messages m
                            LEFT JOIN conversations c ON c.id = m.conversation_id
                            WHERE COALESCE(c.incognito, 0) = 0
                              AND m.role IN ('user', 'model', 'assistant')
                              AND substr(datetime(m.created_at, 'localtime'), 1, 10) = ?
                            ORDER BY m.created_at ASC
                            LIMIT ?;
                            """,
                            (date_target, safe_limit),
                        ) as cursor:
                            day_rows = [dict(r) for r in await cursor.fetchall()]
                except Exception as day_err:
                    logger.warning(f"按日期召回交互记录失败 ({date_target}): {day_err}")

                if day_rows:
                    lines = [f"#### 📅 {date_target} 当日全量交互记录汇总（共 {len(day_rows)} 条）"]
                    for hit in day_rows:
                        safe_snip, _ = MemoryPolicyGuard.sanitize_text(str(hit.get("content") or "")[:300])
                        title = str(hit.get("conversation_title") or hit.get("conversation_id") or "")
                        lines.append(
                            f"- **[{hit.get('role', 'user')}]** (会话 `{title}`, {hit.get('created_at', '')}): {safe_snip}"
                        )
                    sections.append("\n".join(lines))
                else:
                    sections.append(f"#### 📅 {date_target} 当日交互记录\n- 当日暂无已落库的对话消息。")
            else:
                msg_hits = await SearchRepository.search_messages(clean_q, limit=safe_limit)
                if msg_hits:
                    lines = ["#### 💬 历史会话消息召回 (messages_fts)"]
                    for hit in msg_hits[:safe_limit]:
                        raw_snip = re.sub(r"<[^>]+>", "", str(hit.get("highlighted_snippet") or hit.get("content") or ""))
                        safe_snip, _ = MemoryPolicyGuard.sanitize_text(raw_snip[:220])
                        title = str(hit.get("conversation_title") or hit.get("conversation_id") or "")
                        lines.append(
                            f"- **[{hit.get('role', 'user')}]** (会话 `{title}`, {hit.get('created_at', '')}): {safe_snip}"
                        )
                    sections.append("\n".join(lines))

        # 2. 双时间轴历史事实 + 非结构化长期记忆（无痕模式跳过）
        if not incognito and clean_scope in ("facts_history", "all"):
            fact_hits = await fact_manager.search_history(clean_q, workspace_id=workspace_id, limit=safe_limit)
            lt_hits_all = await long_term_memory.list_all()
            lt_matched = [
                m for m in lt_hits_all
                if clean_q.lower() in str(m.get("content") or "").lower()
                or clean_q.lower() in str(m.get("category") or "").lower()
            ][:safe_limit]

            if fact_hits or lt_matched:
                lines = ["#### 📜 确定性事实历史与长期画像 (memory_facts & long_term_memories)"]
                for f in fact_hits:
                    safe_val, _ = MemoryPolicyGuard.sanitize_text(str(f.get("fact_value") or ""))
                    status_tag = "当前生效" if f.get("is_active") else f"已归档 @{f.get('valid_to')}"
                    lines.append(
                        f"- `[{f.get('workspace_id')}:{f.get('fact_key')}]` = `{safe_val}` ({status_tag}, 来源: {f.get('source_type')})"
                    )
                for m in lt_matched:
                    safe_c, _ = MemoryPolicyGuard.sanitize_text(str(m.get("content") or ""))
                    lines.append(f"- `[LTM:{m.get('category')}]` {safe_c}")
                sections.append("\n".join(lines))

        # 3. 当前工作区代码与文档 FTS5（需物理目录存在）
        if target_cwd and clean_scope in ("workspace", "all"):
            cwd_path = Path(target_cwd)
            if cwd_path.exists() and cwd_path.is_dir():
                def _run_ws_search() -> list[dict[str, Any]]:
                    index_db = cwd_path / ".agenthub" / "index.db"
                    if not index_db.exists():
                        return []
                    engine = WorkspaceFTSEngine(str(cwd_path))
                    return engine.search(clean_q, limit=safe_limit)

                try:
                    ws_hits = await asyncio.to_thread(_run_ws_search)
                except Exception as e:
                    logger.debug(f"[AgentRuntime] WorkspaceFTSEngine 检索跳过: {e}")
                    ws_hits = []

                if ws_hits:
                    lines = ["#### 📁 工作区文档/源码片段召回 (workspace_fts)"]
                    for wh in ws_hits[:safe_limit]:
                        raw_s = re.sub(r"</?b>", "", str(wh.get("snippet") or ""))
                        safe_s, _ = MemoryPolicyGuard.sanitize_text(raw_s[:240])
                        lines.append(f"- `{wh.get('rel_path')}`: {safe_s}")
                    sections.append("\n".join(lines))

        if not sections:
            return f"未在范围 `{clean_scope}` 内检索到与 '{clean_q}' 相关的历史记忆或工作区片段。"

        return f"### 🔍 按需记忆与历史溯源检索结果 (`query=\"{clean_q}\"`)\n\n" + "\n\n".join(sections)

    async def _persist_approved_plan(
        self,
        workspace_root: str | None,
        approved_steps: list[dict[str, Any]],
        goal_text: str,
    ) -> None:
        """将用户审批通过的步骤清单落盘写入工作区 .agenthub/scratchpad.md 并同步工作记忆"""
        if not workspace_root:
            return
        try:
            hub_dir = Path(workspace_root) / ".agenthub"
            hub_dir.mkdir(parents=True, exist_ok=True)
            scratch_file = hub_dir / "scratchpad.md"

            lines = [
                "# 任务自主推演工作草稿纸 (Task Scratchpad)",
                f"\n**目标**: {goal_text}\n",
                "## 经用户审批确认的实施步骤清单 (Step-by-Step Task List)\n",
            ]
            for idx, item in enumerate(approved_steps, 1):
                step_no = item.get("step", idx)
                title = item.get("title") or item.get("label") or f"步骤 {step_no}"
                tool_lvl = item.get("tool_level", "L1")
                lines.append(f"- [ ] 步骤 {step_no}: {title} `[{tool_lvl}]`")

            lines.append("\n---\n*提示: Agent 将严格按照上述经审批的步骤推进并更新勾选状态。*\n")
            scratch_file.write_text("\n".join(lines), encoding="utf-8")
            logger.info(f"[AgentRuntime] 已成功将审批步骤持久化至 {scratch_file}")
        except Exception as e:
            logger.warning(f"[AgentRuntime] 持久化审批步骤至 scratchpad.md 失败: {e}")

    async def chat_stream(
        self,
        conversation_id: str | None,
        user_message: str,
        model_name: str = "4 Argon",
        thinking_level: str = "high",
        system_override: str | None = None,
        temperature: float = 0.7,
        context_rounds: int = 10,
        attachments: list[Any] | None = None,
        scene_mode: str | None = None,
        incognito: bool | None = None,
        workspace_id: str | None = None,
        execution_strategy: str = "direct",
        clarify_mode: bool = False,
        approved_plan_steps: list[dict[str, Any]] | None = None,
        confirmed_facts: list[Any] | None = None,
        inquiry_confirmed: bool = False,
        inquiry_action: str | None = None,
        is_disconnected: Callable[[], Awaitable[bool]] | None = None,
        allowed_tools: list[str] | None = None,
        allowed_write_dirs: list[str] | None = None,
        swarm_mode: bool = False,
        swarm_id: str | None = None,
        swarm_member_name: str | None = None,
        swarm_role: str | None = None,
        swarm_brief: str | None = None,
        agent_id: str | None = None,
        grounding_config: dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        执行双层循环流式推理与工具编排，向外输出标准化 SSE 字典事件流。
        """
        conv_id = conversation_id
        is_new_conv = False
        effective_user_message = (user_message or "").strip()
        first_att_name = ""
        if attachments:
            first_att = attachments[0]
            first_att_name = str(first_att.get("name") if isinstance(first_att, dict) else getattr(first_att, "name", "") or "").strip()
        if not effective_user_message and attachments:
            effective_user_message = "请结合附带的文件或图片内容进行分析解答。"
        user_message = effective_user_message or user_message
        if first_att_name and (not (user_message or "").strip() or user_message == "请结合附带的文件或图片内容进行分析解答。"):
            short_title = f"[附件] {first_att_name}"[:24]
        else:
            short_title = (user_message or "").strip().split("\n")[0][:24] or "新会话"
        ws_id = workspace_id or "default"
        target_cwd = None
        try:
            from ..repository.workspace_repository import WorkspaceRepository
            ws = await WorkspaceRepository.get_by_id(ws_id)
            if ws and ws.get("root_path"):
                target_cwd = ws["root_path"]
        except Exception:
            pass

        # 1. 确保会话存在并同步最新场景/无痕状态与工作区
        if not conv_id:
            conv_id = str(uuid.uuid4())
            is_new_conv = True
            await ConversationRepository.create({
                "id": conv_id,
                "title": short_title,
                "model_name": model_name,
                "scene_mode": scene_mode or "agent",
                "incognito": 1 if incognito else 0,
                "workspace_id": ws_id,
                "current_cwd": target_cwd,
            })
        else:
            conv_existing = await ConversationRepository.get_by_id(conv_id)
            if not conv_existing:
                is_new_conv = True
                await ConversationRepository.create({
                    "id": conv_id,
                    "title": short_title,
                    "model_name": model_name,
                    "scene_mode": scene_mode or "agent",
                    "incognito": 1 if incognito else 0,
                    "workspace_id": ws_id,
                    "current_cwd": target_cwd,
                })
            elif scene_mode is not None or incognito is not None:
                await ConversationRepository.update_agent_state(
                    conv_id,
                    scene_mode=scene_mode,
                    incognito=incognito,
                )

        conv_obj = await ConversationRepository.get_by_id(conv_id)
        active_scene = (conv_obj.scene_mode if conv_obj else (scene_mode or "agent")) or "agent"
        active_incognito = bool(conv_obj.incognito) if conv_obj else bool(incognito)
        ws_id = (getattr(conv_obj, "workspace_id", None) if conv_obj else None) or ws_id

        # ── 解析专家绑定配置与防幻觉参数 ──
        effective_grounding = grounding_config
        if not effective_grounding and agent_id:
            try:
                from ..repository.agent_repository import AgentProfileRepository
                ag_profile = await AgentProfileRepository.get_agent(agent_id)
                if ag_profile:
                    effective_grounding = ag_profile.get("grounding_config")
                    if not allowed_tools and ag_profile.get("allowed_tools"):
                        allowed_tools = ag_profile.get("allowed_tools")
                    if not allowed_write_dirs and ag_profile.get("allowed_write_dirs"):
                        allowed_write_dirs = ag_profile.get("allowed_write_dirs")
            except Exception as ag_err:
                logger.debug(f"[AgentRuntime] 加载专家 {agent_id} 防幻觉配置跳过: {ag_err}")

        # ── 模型温度能力嗅探与自适应压制 ──
        effective_temperature = temperature
        if effective_grounding and effective_grounding.get("strict_mode"):
            if abs(temperature - 0.7) < 1e-4:
                effective_temperature = float(effective_grounding.get("temperature", 0.1))

        lower_model = (model_name or "").lower()
        is_fixed_temp_model = any(
            k in lower_model for k in ("o1-", "o1-mini", "o1-preview", "o3-", "o3-mini", "deepseek-r1", "-r1", "r1-")
        )
        if is_fixed_temp_model:
            logger.debug(f"[AgentRuntime] 识别到固定温度推理模型 {model_name}，自动忽略自定义 temperature 参数")

        # ── Pre-Turn 0-Token 斜杠管理指令即时拦截 (/ckpt /rollback /remember /distill) ──
        trimmed_cmd_text = (user_message or "").strip()
        cmd_match = re.match(r"^/(ckpt|rollback|remember|distill)(?:\s+([\s\S]*))?$", trimmed_cmd_text, re.IGNORECASE)
        if cmd_match:
            cmd_verb = cmd_match.group(1).lower()
            cmd_arg = (cmd_match.group(2) or "").strip()
            effective_ws_root = target_cwd or os.getcwd()
            current_max_turn = await MessageRepository.get_max_turn_id(conv_id)
            yield {
                "type": "conv_init",
                "conversation_id": conv_id,
                "is_new": is_new_conv,
                "title": short_title,
                "turn_id": current_max_turn,
                "scene_mode": active_scene,
                "incognito": active_incognito,
            }

            reply_text = ""
            if cmd_verb == "ckpt":
                if cmd_arg.lower() in ("list", "--list", "-l"):
                    ckpts = await ShadowCheckpointManager.list_checkpoints(conv_id, limit=10)
                    if not ckpts:
                        reply_text = "🕰️ 当前会话尚无历史检查点，可使用 `/ckpt 备注` 创建快照。"
                    else:
                        lines = ["🕰️ **当前会话检查点列表**（越靠前越新）：\n"]
                        for c in ckpts:
                            lines.append(
                                f"- `{c['ckpt_id']}` | Turn {c['turn_id']} | 提交 `{str(c['git_hash'])[:7]}` | {c['note']} ({str(c['created_at'])[:19]})"
                            )
                        lines.append("\n提示：输入 `/rollback <ckpt_id>` 可回滚到指定节点。")
                        reply_text = "\n".join(lines)
                else:
                    try:
                        ckpt_info = await ShadowCheckpointManager.create_checkpoint(
                            conversation_id=conv_id,
                            workspace_id=ws_id,
                            workspace_root=effective_ws_root,
                            turn_id=current_max_turn,
                            note=cmd_arg or f"manual_turn_{current_max_turn}",
                        )
                        reply_text = (
                            f"✅ **已创建双态时光机检查点**\n"
                            f"- **检查点 ID**：`{ckpt_info['ckpt_id']}`\n"
                            f"- **对话水位**：Turn `{ckpt_info['turn_id']}`\n"
                            f"- **影子 Git 提交**：`{str(ckpt_info['git_hash'])[:7]}`（已隔离于 `.agenthub/shadow_git`）\n"
                            f"- **备注**：{ckpt_info['note']}\n\n"
                            f"后续若修改偏航，输入 `/rollback` 即可一键还原文件与记忆状态。"
                        )
                    except Exception as ckpt_err:
                        reply_text = f"⚠️ 创建检查点失败：{ckpt_err}"

            elif cmd_verb == "rollback":
                target_ckpt = None if cmd_arg.lower() in ("", "latest") else cmd_arg.split()[0]
                try:
                    rb_res = await ShadowCheckpointManager.rollback(
                        conversation_id=conv_id,
                        workspace_root=effective_ws_root,
                        ckpt_id=target_ckpt,
                    )
                    if rb_res.get("ok"):
                        yield {
                            "type": "control",
                            "action": "rollback_completed",
                            "target_turn_id": rb_res["target_turn_id"],
                            "conversation_id": conv_id,
                            "ckpt_id": rb_res["ckpt_id"],
                        }
                        reply_text = (
                            f"⏪ **已执行全量双回滚时光机还原**\n"
                            f"- **还原节点**：`{rb_res['ckpt_id']}` (Turn `{rb_res['target_turn_id']}`, 备注: {rb_res['note']})\n"
                            f"- **磁盘代码**：已通过 `.agenthub/shadow_git` 硬重置至 `{str(rb_res['git_hash'])[:7]}`，并同步还原工作草稿纸\n"
                            f"- **数据库截断**：已清理后续 `{rb_res['deleted_messages']}` 条消息与 `{rb_res['deleted_ledger']}` 条行为账本，提取水位已同步回退"
                        )
                    else:
                        reply_text = f"⚠️ 回滚失败：{rb_res.get('error', '未找到匹配检查点')}"
                except Exception as rb_err:
                    reply_text = f"⚠️ 执行回滚异常：{rb_err}"

            elif cmd_verb == "remember":
                if not cmd_arg:
                    reply_text = "ℹ️ 用法：`/remember <规则标签> <规范内容>` 或 `/remember <需要长期遵守的规范>`"
                elif active_incognito:
                    reply_text = "🕵️ 当前处于无痕模式，为保护隐私，`/remember` 未向持久化记忆库写入规则。"
                else:
                    parts = cmd_arg.split(None, 1)
                    if len(parts) == 2 and len(parts[0]) <= 36 and not re.search(r"[，。；！？]", parts[0]):
                        raw_key, raw_val = parts[0], parts[1]
                    else:
                        raw_key, raw_val = f"manual.{uuid.uuid4().hex[:6]}", cmd_arg
                    try:
                        res_fact = await fact_manager.upsert_fact(
                            fact_key=raw_key,
                            fact_value=raw_val,
                            workspace_id=ws_id,
                            category="biz_rule",
                            source_type="explicit_cmd",
                            verified=True,
                        )
                        if res_fact.get("status") == "blocked":
                            reply_text = f"🛡️ 安全策略已拦截写入：{res_fact.get('reason', '包含高敏凭据信息')}"
                        else:
                            fact_obj = res_fact.get("fact") if isinstance(res_fact.get("fact"), dict) else {}
                            saved_k = fact_obj.get("fact_key") or raw_key
                            saved_v = fact_obj.get("fact_value") or raw_val
                            reply_text = (
                                f"📌 **已直接固化 L4 确定性契约规则**（后续每轮自动前置生效）\n"
                                f"- **工作区作用域**：`{ws_id}`\n"
                                f"- **规则键**：`{saved_k}`\n"
                                f"- **规则内容**：{saved_v}"
                            )
                    except Exception as rem_err:
                        reply_text = f"🛡️ 写入规则未通过校验或异常：{rem_err}"

            elif cmd_verb == "distill":
                if not cmd_arg:
                    reply_text = "ℹ️ 用法：`/distill <技能名称> [附加提纯说明]`（例如：`/distill excel-audit 严格校验汇总行`）"
                elif active_incognito:
                    reply_text = "🕵️ 无痕模式下禁用 `/distill` 资产持久化沉淀。"
                else:
                    dist_parts = cmd_arg.split(None, 1)
                    raw_sk_name = dist_parts[0]
                    extra_desc = dist_parts[1] if len(dist_parts) > 1 else ""
                    yield {"type": "chunk", "content": f"⚗️ 正在针对 `{raw_sk_name}` 执行正交双提纯与安全审计...\n\n"}
                    try:
                        d_res = await experience_distiller.dual_distill_skill_and_memory(
                            skill_name=raw_sk_name,
                            conversation_id=conv_id,
                            workspace_id=ws_id,
                            workspace_root=effective_ws_root,
                            user_instruction=extra_desc,
                        )
                        if d_res.get("ok"):
                            reply_text = (
                                f"✅ **正交双提纯完成并已 0ms 热加载**\n"
                                f"- **新技能标识**：`/{d_res['skill_id']}`\n"
                                f"- **落盘目录**：`{d_res['skill_dir']}`（已生成 `SKILL.md` + `scripts/pipeline.py`）\n"
                                f"- **安全审计**：{d_res['audit_summary']}\n"
                                f"- **记忆正交分流**：已同步沉淀 `{d_res['facts_saved']}` 条 L4 业务契约与 `{d_res['cases_saved']}` 条 L5 实战避坑案例"
                            )
                        else:
                            reply_text = f"⚠️ 正交双提纯被拦截：{d_res.get('error', '未知错误')}"
                    except Exception as dist_err:
                        reply_text = f"⚠️ 正交双提纯异常：{dist_err}"

            yield {"type": "chunk", "content": reply_text}
            yield {
                "type": "done",
                "full_content": reply_text,
                "final_step_content": reply_text,
                "account": "local-command",
                "model": "AgentHub-Control",
            }
            return

        # 用户发起新一轮消息，立即取消该会话挂起的后台记忆提炼定时器，避免争抢算力
        long_term_memory.cancel_extraction(conv_id)

        # 2. 对齐当前工作区沙盒附件并率先落盘用户提问（确保无论切换工作区或重发旧消息均落盘当前沙盒）
        if attachments:
            try:
                attachments = await attachment_service.ensure_attachments_in_workspace(
                    ws_id,
                    attachments,
                    conversation_id=conv_id,
                )
            except Exception as sync_att_err:
                logger.warning(f"[AgentRuntime] 同步工作区沙盒附件异常: {sync_att_err}")

            # ⚡ 关键基准重置：用户上传新附件时，自动净化与新附件冲突的历史旧工作记忆
            user_msg_lower = (user_message or "").lower()
            user_explicit_prior = any(
                k in user_msg_lower
                for k in [
                    "之前的数据", "历史数据", "上一次的数据", "上次的数据",
                    "上次传的", "之前传的", "原有数据", "沿用之前", "基于上一次"
                ]
            )
            if not user_explicit_prior and conv_obj:
                try:
                    att_names = [
                        str(a.get("name") if isinstance(a, dict) else getattr(a, "name", "") or "")
                        for a in attachments
                    ]
                    att_names_clean = [n for n in att_names if n]
                    clean_att_desc = ", ".join(att_names_clean) if att_names_clean else "新附件"

                    fresh_wm = {
                        "active_baseline_file": clean_att_desc,
                        "status": "fresh_attachment_uploaded_awaiting_analysis",
                        "updated_reason": "用户上传了最新附件，旧文件数据缓存已自动归档重置",
                    }
                    await ConversationRepository.update_agent_state(
                        conv_id,
                        working_memory_json=json.dumps(fresh_wm, ensure_ascii=False),
                    )
                    logger.info(f"[AgentRuntime] 检测到用户上传新附件 {clean_att_desc}，已自动刷新重置历史 working_memory")
                except Exception as wm_reset_err:
                    logger.debug(f"[AgentRuntime] 刷新 working_memory 异常: {wm_reset_err}")

        # 2.5 先检查既往历史轮次是否已完成方案审批或进入执行态，再落盘本轮 user 消息
        has_prior_execution_unlock = False
        if not is_new_conv and active_scene == "agent":
            try:
                prior_msgs = await MessageRepository.get_by_conversation(conv_id, limit=300)
                has_prior_execution_unlock = self.detect_prior_execution_unlock(prior_msgs)
            except Exception as prior_err:
                logger.debug(f"[AgentRuntime] 检查历史执行解锁状态跳过: {prior_err}")

        max_turn = await MessageRepository.get_max_turn_id(conv_id)
        current_turn_id = max_turn + 1
        persisted_user_content = attachment_service.append_attachments_anchor(user_message, attachments)

        await MessageRepository.save_message(
            conversation_id=conv_id,
            role="user",
            content=persisted_user_content,
            turn_id=current_turn_id,
        )
        await ConversationRepository.touch(conv_id)

        normalized_inquiry_action = (inquiry_action or "").strip().lower() or None
        explicit_execute_unlock = bool(
            approved_plan_steps
            or (inquiry_confirmed and normalized_inquiry_action == "execute_now")
        )

        # 对话模式下需求澄清解锁落盘：若用户已确认方案并授权立即执行或批准步骤，平滑升舱为 agent 模式并解除只读限制
        if active_scene == "chat" and explicit_execute_unlock:
            active_scene = "agent"
            try:
                await ConversationRepository.update_agent_state(conv_id, scene_mode="agent")
                logger.info(f"[AgentRuntime] 用户确认方案并授权执行，会话 {conv_id} 从对话模式自动升舱为 Agent 任务模式 (解锁工具与落盘权限)")
            except Exception as up_err:
                logger.warning(f"[AgentRuntime] 升舱更新会话模式异常: {up_err}")

        is_planning_phase = self.compute_planning_phase(
            active_scene=active_scene,
            execution_strategy=execution_strategy,
            approved_plan_steps=approved_plan_steps,
            inquiry_confirmed=inquiry_confirmed,
            inquiry_action=normalized_inquiry_action,
            has_prior_execution_unlock=has_prior_execution_unlock,
        )

        pre_matched_ids: list[str] = []
        try:
            pre_matched = await asyncio.to_thread(
                skill_loader.resolve_slash_commands,
                user_message,
                target_cwd,
            )
            for m_sk in pre_matched:
                sid = str(m_sk.get("id") or "").strip()
                if sid and sid not in pre_matched_ids:
                    pre_matched_ids.append(sid)
        except Exception as pre_err:
            logger.debug(f"[AgentRuntime] 预解析用户输入 /skill 跳过: {pre_err}")

        # 2.5 非无痕且非单次模式下：追加 user_input 账本事件并执行对齐卡 confirmed_facts 0-Token 直通入库
        if not active_incognito and active_scene != "oneshot":
            try:
                await event_ledger.append_event(
                    workspace_id=ws_id,
                    conversation_id=conv_id,
                    turn_id=current_turn_id,
                    event_type="user_input",
                    actor="user",
                    skill_id=pre_matched_ids[0] if pre_matched_ids else None,
                    payload={
                        "message": (user_message or "")[:1200],
                        "scene_mode": active_scene,
                        "execution_strategy": execution_strategy,
                        "active_skill_ids": pre_matched_ids,
                    },
                    incognito=False,
                )
            except Exception as evt_err:
                logger.debug(f"[AgentRuntime] 记录 user_input 账本跳过: {evt_err}")

            if confirmed_facts:
                await self._ingest_confirmed_facts(
                    confirmed_facts=confirmed_facts,
                    workspace_id=ws_id,
                    conversation_id=conv_id,
                    turn_id=current_turn_id,
                    incognito=active_incognito,
                    scene_mode="plan_first" if is_planning_phase else active_scene,
                )

        # 3. 推送第一条会话初始化握手事件
        yield {
            "type": "conv_init",
            "conversation_id": conv_id,
            "is_new": is_new_conv,
            "title": short_title,
            "turn_id": current_turn_id,
            "scene_mode": active_scene,
            "incognito": active_incognito,
        }

        PLANNING_SAFE_TOOLS = self.PLANNING_SAFE_TOOLS

        # 4. 准备当前场景可用的工具集合
        active_tools: list[dict[str, Any]] = []
        mcp_catalog_prompt: str = ""
        conv_collapse_anchor = 0
        if not active_incognito:
            try:
                conv_meta = await ConversationRepository.get_by_id(conv_id)
                if conv_meta:
                    conv_collapse_anchor = int(getattr(conv_meta, "collapse_anchor_turn_id", 0) or 0)
            except Exception:
                conv_collapse_anchor = 0

        include_swarm_tools = bool(swarm_mode or swarm_role)
        allowed_tools_set: set[str] | None = (
            {str(t).strip() for t in allowed_tools if str(t).strip()}
            if allowed_tools
            else None
        )

        if active_scene == "agent":
            if is_planning_phase:
                # 🛡️ 物理只读硬门控：规划态仅向模型暴露 L0 只读与记忆探测工具，写盘与终端完全隔离
                all_builtin = ToolProtocolAdapter.get_builtin_tools()
                active_tools.extend([
                    t for t in all_builtin
                    if t.get("name") in PLANNING_SAFE_TOOLS
                    and (allowed_tools_set is None or t.get("name") in allowed_tools_set)
                ])
            else:
                active_tools, mcp_catalog_prompt = await self._assemble_agent_tools(
                    conv_id=conv_id,
                    user_message=user_message,
                    collapse_anchor_turn_id=conv_collapse_anchor,
                    allowed_tools=allowed_tools,
                    include_swarm=include_swarm_tools,
                )

        # 若用户已批准方案，将执行步骤持久化写入工作区 .agenthub/scratchpad.md
        if approved_plan_steps and target_cwd:
            await self._persist_approved_plan(
                workspace_root=target_cwd,
                approved_steps=approved_plan_steps,
                goal_text=user_message.strip().split("\n")[0][:120],
            )

        # 规划态步数收敛限制为最多 4 步，用于探测目录和输出方案卡片
        current_max_steps = (4 if is_planning_phase else self.DEFAULT_REACT_STEPS) if active_scene == "agent" else 1
        final_account: str | None = None
        accumulated_visible_text = ""
        effective_model_name = model_name
        last_step_error_info: dict[str, Any] | None = None
        cli_native_tool_retry_count = 0
        replace_prev_empty_step_pending = False
        consecutive_stalls = 0
        allow_step_extension = not is_planning_phase
        command_history: list[dict[str, Any]] = []
        active_skill_ids: list[str] = list(pre_matched_ids)
        recovered_error_count = 0
        total_tool_steps = 0
        circuit_breaker_triggered = False
        produced_artifacts: list[str] = []
        has_workspace_mutations = False
        executed_mutating_shell = False
        injected_case_ids: list[str] = []
        turn_checkpoint_taken = False
        lock_high_thinking = False
        session_file_read_cache = self._session_file_read_cache_by_conv.setdefault(conv_id, {})
        turn_tool_sig_cache: dict[tuple[str, str], int] = {}
        failed_attempt_counts: dict[tuple[str, str], int] = {}
        consecutive_err_fp: dict[str, Any] = {"fp": "", "count": 0}
        last_executed_shell_cmd: str = ""
        rollover_triggered = False
        rollover_task_completed = False
        recent_error_summaries: list[str] = []

        # 4.5 关联工作区长程自主领航舱 (TaskLoopRunner 闭环驱动：严格按 conversation_id 隔离 + 渐进式按需激活)
        active_loop_obj = None
        tracked_loop_id: str | None = None
        existing_loop = None
        if active_scene == "agent" and ws_id:
            try:
                existing_loop = await TaskLoopRepository.get_active_loop_by_workspace(
                    ws_id,
                    include_completed=False,
                    conversation_id=conv_id,
                )
                if not is_planning_phase and existing_loop:
                    active_loop_obj = existing_loop
                    if (
                        active_loop_obj.loop_status == "waiting_approval"
                        and active_loop_obj.pending_approval_call is None
                    ):
                        # 若上一轮因交互卡停在 waiting_approval，本轮进入执行态时自动恢复为 executing
                        await TaskLoopRepository.update_loop_status(
                            loop_id=active_loop_obj.id,
                            status="executing",
                        )
                        refreshed_loop = await TaskLoopRepository.get_loop(active_loop_obj.id)
                        if refreshed_loop:
                            active_loop_obj = refreshed_loop
                        yield {
                            "type": "loop_step",
                            "conversation_id": conv_id,
                            "loop_id": active_loop_obj.id,
                            "iteration": active_loop_obj.current_iteration,
                            "max_iterations": active_loop_obj.max_iterations,
                            "status": active_loop_obj.loop_status,
                            "pending_approval_call": None,
                            "scratchpad_md": active_loop_obj.scratchpad_md,
                        }
                elif existing_loop and existing_loop.conversation_id == conv_id:
                    active_loop_obj = existing_loop
                if active_loop_obj:
                    tracked_loop_id = active_loop_obj.id
            except Exception as e:
                logger.warning(f"初始化任务自主领航舱实例异常: {e}")

        is_chitchat_turn = self._is_likely_pure_chitchat(
            user_message,
            has_pending_loop=bool(existing_loop),
            has_attachments=bool(attachments),
            explicit_execute_unlock=bool(explicit_execute_unlock),
        )

        policy_gate = PolicyInterceptor(
            workspace_root=str(target_cwd or os.getcwd()),
            security_level="standard",
        )

        async def _ensure_active_loop(initial_status: str = "executing"):
            """渐进式按需创建 Loop：仅在实质工具调用、待确认交互或故障中断时才创建，避免纯闲聊触发 HUD 闪现"""
            nonlocal active_loop_obj, tracked_loop_id
            if active_loop_obj is not None:
                return active_loop_obj
            if active_scene != "agent" or not ws_id:
                return None
            raw_goal_line = user_message.strip().split("\n")[0][:120]
            if raw_goal_line.startswith(("已确认", "✅ 实施方案", "✅")):
                raw_goal_line = "按确认决策执行任务"
            created = await TaskLoopRepository.create_loop(
                workspace_id=ws_id,
                conversation_id=conv_id,
                goal_prompt=raw_goal_line or "执行自主推演任务",
                max_iterations=10,
            )
            if initial_status != "executing":
                await TaskLoopRepository.update_loop_status(
                    loop_id=created.id,
                    status=initial_status,
                )
                created = await TaskLoopRepository.get_loop(created.id) or created
            active_loop_obj = created
            tracked_loop_id = created.id
            return active_loop_obj

        async def _eval_and_wait_l2_approval(
            t_name: str,
            t_args: dict[str, Any],
            r_reason: str,
            snap_path: str | None = None,
            result_holder: dict[str, Any] | None = None,
        ) -> AsyncGenerator[dict[str, Any], None]:
            """
            🔴 L2 高危敏感操作拦截网关：挂起循环并呼出四键审批卡片（15s 超时自动封锁）。
            支持四键操作：批准本次 (approve_once) / 本任务白名单 (approve_all) / 改参另存为 (modify_args) / 拒绝并改道 (reject)。
            """
            nonlocal active_loop_obj, tracked_loop_id, step_idx

            # 0. 检查是否在白名单中
            if t_name in policy_gate.session_whitelists:
                if result_holder is not None:
                    result_holder["allowed"] = True
                    result_holder["args"] = t_args
                    result_holder["guidance"] = "在任务白名单中直接放行"
                    result_holder["is_timeout"] = False
                return

            # 1. 确保 Loop 存在且置为 waiting_approval
            eff_loop = await _ensure_active_loop(initial_status="waiting_approval")
            loop_id = eff_loop.id if eff_loop else f"loop_{conv_id}"
            call_id = f"call_{uuid.uuid4().hex[:8]}"
            from ..schema.loop_schema import PendingApprovalCall
            pending_call = PendingApprovalCall(
                call_id=call_id,
                tool_name=t_name,
                arguments=t_args,
                policy_level="L2",
                risk_reason=r_reason,
                snapshot_path=snap_path,
            )
            approval_payload = pending_call.model_dump()

            await TaskLoopRepository.update_loop_status(
                loop_id=loop_id,
                status="waiting_approval",
                pending_approval=approval_payload,
            )
            if active_loop_obj:
                active_loop_obj.loop_status = "waiting_approval"
                active_loop_obj.pending_approval_call = pending_call

            # 2. 推送 SSE 事件让前端呼出 ApprovalCard（启动 15s 倒计时）
            yield {
                "type": "loop_step",
                "loop_id": loop_id,
                "status": "waiting_approval",
                "iteration": step_idx,
                "pending_approval_call": approval_payload,
                "scratchpad_md": active_loop_obj.scratchpad_md if active_loop_obj else "",
            }

            # 3. 注册并异步挂起协程，等待人工操作或 15 秒超时自动封锁
            approval_event_manager.register(call_id, loop_id=loop_id)
            decision_res = await approval_event_manager.wait_for_decision(call_id, timeout_seconds=15.0)

            # 4. 恢复 Loop 状态为 executing 并清除待审批项
            await TaskLoopRepository.update_loop_status(
                loop_id=loop_id,
                status="executing",
                pending_approval=None,
            )
            if active_loop_obj:
                active_loop_obj.loop_status = "executing"
                active_loop_obj.pending_approval_call = None

            yield {
                "type": "loop_step",
                "loop_id": loop_id,
                "status": "executing",
                "iteration": step_idx,
                "pending_approval_call": None,
                "scratchpad_md": active_loop_obj.scratchpad_md if active_loop_obj else "",
            }

            # 5. 根据决策结果分支填充返回
            if decision_res.action == "approve_once":
                if result_holder is not None:
                    result_holder["allowed"] = True
                    result_holder["args"] = t_args
                    result_holder["guidance"] = "用户已批准本次执行"
                    result_holder["is_timeout"] = False
            elif decision_res.action == "approve_all":
                policy_gate.add_to_whitelist(t_name)
                if result_holder is not None:
                    result_holder["allowed"] = True
                    result_holder["args"] = t_args
                    result_holder["guidance"] = "已加入本任务白名单并放行"
                    result_holder["is_timeout"] = False
            elif decision_res.action == "modify_args":
                eff_args = decision_res.modified_arguments or t_args
                if result_holder is not None:
                    result_holder["allowed"] = True
                    result_holder["args"] = eff_args
                    result_holder["guidance"] = "已采用用户修改后的参数放行"
                    result_holder["is_timeout"] = False
            else:  # reject 或 15s 超时自动封锁
                guidance = decision_res.user_guidance or "用户拒绝了该项高危操作，请更换安全的替代方案。"
                if decision_res.is_timeout:
                    guidance = "【15秒超时自动封锁】审批卡未在 15 秒内收到操作，安全网关已自动封锁该高危操作并阻止执行。"
                if result_holder is not None:
                    result_holder["allowed"] = False
                    result_holder["args"] = t_args
                    result_holder["guidance"] = guidance
                    result_holder["is_timeout"] = decision_res.is_timeout

        async def _finalize_turn_loop(
            summary_text: str,
            *,
            is_waiting_user_decision: bool = False,
        ) -> AsyncGenerator[dict[str, Any], None]:
            """
            循环收尾唯一真理状态机：
            1) 存在未恢复报错或触发死循环熔断 -> 标记 failed 并推 loop_failed；
            2) 处于待用户确认交互卡或规划阶段 -> 创建/更新为 waiting_approval；
            3) 零工具零产出零变更且未推进任何轮次（纯闲聊/纯提问）-> 归档为 idle 或无痕退出，绝不虚标 completed；
            4) 完整无错工具执行闭环 -> complete_loop 并附带精确 distillable 资格信号。
            """
            nonlocal active_loop_obj
            if active_scene != "agent" or not ws_id:
                return

            if last_step_error_info is not None or circuit_breaker_triggered:
                eff_loop = await _ensure_active_loop()
                if eff_loop:
                    err_detail = str(
                        (last_step_error_info or {}).get("summary")
                        or "执行链路存在未解决异常或触发死循环熔断"
                    )
                    err_pad = eff_loop.scratchpad_md or ""
                    err_note = f"\n\n> ⚠️ 终止原因: {err_detail[:200]}"
                    await TaskLoopRepository.update_loop_status(
                        loop_id=eff_loop.id,
                        status="failed",
                        scratchpad_md=f"{err_pad}{err_note}",
                    )
                    yield {
                        "type": "loop_failed",
                        "conversation_id": conv_id,
                        "loop_id": eff_loop.id,
                        "iteration": eff_loop.current_iteration,
                        "max_iterations": eff_loop.max_iterations,
                        "status": "failed",
                        "error": err_detail,
                    }
                    active_loop_obj = None
                return

            if is_waiting_user_decision:
                eff_loop = await _ensure_active_loop(initial_status="waiting_approval")
                if eff_loop:
                    if eff_loop.loop_status != "waiting_approval":
                        await TaskLoopRepository.update_loop_status(
                            loop_id=eff_loop.id,
                            status="waiting_approval",
                        )
                        refreshed = await TaskLoopRepository.get_loop(eff_loop.id)
                        eff_loop = refreshed or eff_loop
                    if target_cwd:
                        try:
                            ws_sp = Path(target_cwd) / ".agenthub" / "scratchpad.md"
                            ws_sp.parent.mkdir(parents=True, exist_ok=True)
                            ws_sp.write_text(eff_loop.scratchpad_md or "", encoding="utf-8")
                        except Exception:
                            pass
                    yield {
                        "type": "loop_step",
                        "conversation_id": conv_id,
                        "loop_id": eff_loop.id,
                        "iteration": eff_loop.current_iteration,
                        "max_iterations": eff_loop.max_iterations,
                        "status": "waiting_approval",
                        "pending_approval_call": None,
                        "scratchpad_md": eff_loop.scratchpad_md,
                    }
                    active_loop_obj = eff_loop
                return

            is_zero_action_turn = (
                total_tool_steps == 0
                and not produced_artifacts
                and not has_workspace_mutations
                and (not active_loop_obj or active_loop_obj.current_iteration == 0)
            )
            if is_zero_action_turn:
                if active_loop_obj:
                    await TaskLoopRepository.archive_loop(active_loop_obj.id)
                    yield {
                        "type": "loop_step",
                        "conversation_id": conv_id,
                        "loop_id": active_loop_obj.id,
                        "iteration": active_loop_obj.current_iteration,
                        "max_iterations": active_loop_obj.max_iterations,
                        "status": "idle",
                        "pending_approval_call": None,
                        "scratchpad_md": active_loop_obj.scratchpad_md,
                    }
                    active_loop_obj = None
                return

            if active_loop_obj:
                completed_loop = await TaskLoopRepository.complete_loop(
                    loop_id=active_loop_obj.id,
                    summary=summary_text or "模型已完成自主闭环并输出交付结论",
                )
                if completed_loop:
                    await TaskLoopRepository._enrich_distill_eligibility(completed_loop)
                    if target_cwd:
                        try:
                            ws_sp = Path(target_cwd) / ".agenthub" / "scratchpad.md"
                            ws_sp.parent.mkdir(parents=True, exist_ok=True)
                            ws_sp.write_text(completed_loop.scratchpad_md or "", encoding="utf-8")
                        except Exception:
                            pass
                    yield {
                        "type": "loop_completed",
                        "conversation_id": conv_id,
                        "loop_id": completed_loop.id,
                        "iteration": completed_loop.current_iteration,
                        "max_iterations": completed_loop.max_iterations,
                        "status": "completed",
                        "distillable": bool(completed_loop.distillable),
                        "distill_reason": completed_loop.distill_reason,
                        "scratchpad_md": completed_loop.scratchpad_md,
                    }
                    active_loop_obj = None

        async def _perform_context_rollover(
            watermark_pct: float,
            *,
            task_completed: bool,
        ) -> dict[str, Any] | None:
            """上下文水位达到阈值时，蒸馏关键上下文并在同工作区创建接力新对话。"""
            nonlocal active_loop_obj
            if active_incognito or not conv_id:
                return None

            source_conv = await ConversationRepository.get_by_id(conv_id)
            target_ws_id = (
                (getattr(source_conv, "workspace_id", None) if source_conv else None)
                or ws_id
                or "default"
            )
            rollover_cwd = (
                (getattr(source_conv, "current_cwd", None) if source_conv else None)
                or ctx.get("current_cwd")
                or target_cwd
            )
            target_model = (
                effective_model_name
                or (getattr(source_conv, "model_name", None) if source_conv else None)
                or model_name
                or "4 Argon"
            )
            target_scene = (
                active_scene
                or (getattr(source_conv, "scene_mode", None) if source_conv else None)
                or "agent"
            )
            base_title = (
                (getattr(source_conv, "title", None) if source_conv else None)
                or user_message.strip().split("\n")[0][:60]
                or "新会话"
            )
            new_title = self._compute_next_rollover_title(base_title)
            existing_summary = ""
            if source_conv:
                existing_summary = (
                    getattr(source_conv, "anchored_summary", None)
                    or getattr(source_conv, "rolling_summary", None)
                    or ""
                )

            # 汇总最近错误卡点（含 last_step_error_info 兜底）
            merged_errors = list(recent_error_summaries)
            if isinstance(last_step_error_info, dict):
                err_txt = str(last_step_error_info.get("error") or "").strip()
                if err_txt and err_txt not in merged_errors:
                    merged_errors.append(err_txt[:240])

            # 提取当前会话已确认的工作区事实（如有）
            confirmed_facts_list: list[str] = []
            try:
                facts = await fact_manager.list_facts(workspace_id=target_ws_id)
                for f_obj in (facts or [])[:8]:
                    f_val = (
                        f_obj.get("fact_text")
                        if isinstance(f_obj, dict)
                        else getattr(f_obj, "fact_text", "")
                    )
                    if f_val:
                        confirmed_facts_list.append(str(f_val))
            except Exception:
                pass

            distilled = await LocalContextCompressor.distill_handoff_summary(
                conversation_id=conv_id,
                user_goal=user_message.strip(),
                current_cwd=rollover_cwd,
                produced_artifacts=produced_artifacts,
                active_loop_scratchpad=getattr(active_loop_obj, "scratchpad_md", "") or "",
                confirmed_facts=confirmed_facts_list,
                recent_errors=merged_errors,
                existing_summary=existing_summary,
                task_completed=task_completed,
                watermark_pct=watermark_pct,
            )
            handoff_summary = str(distilled.get("handoff_summary") or "").strip()
            continue_prompt = str(distilled.get("continue_prompt") or "").strip()

            new_conv_id = str(uuid.uuid4())
            await ConversationRepository.create(
                {
                    "id": new_conv_id,
                    "title": new_title,
                    "source_type": "local",
                    "source_account": getattr(source_conv, "source_account", None) if source_conv else None,
                    "model_name": target_model,
                    "scene_mode": target_scene,
                    "incognito": False,
                    "current_cwd": rollover_cwd,
                    "workspace_id": target_ws_id,
                    "parent_conversation_id": conv_id,
                }
            )
            await ConversationRepository.update_collapse_anchor(
                conv_id=new_conv_id,
                anchor_turn_id=0,
                anchored_summary=handoff_summary,
                added_saved_tokens=0,
            )
            await ConversationRepository.update_agent_state(
                new_conv_id,
                rolling_summary=handoff_summary,
                current_cwd=rollover_cwd,
            )
            # 在新会话首条落库交接卡片，让用户和后续上下文都能直观看到完整蒸馏结果
            await MessageRepository.save_message(
                conversation_id=new_conv_id,
                role="model",
                content=handoff_summary,
                executed_by_account=final_account,
                token_count=max(1, len(handoff_summary) // 3),
                turn_id=1,
            )
            await ConversationRepository.touch(new_conv_id)

            # 若旧会话存在未结束的 Loop，则自动暂停避免残留运行态
            if active_loop_obj and getattr(active_loop_obj, "id", None):
                try:
                    await TaskLoopRepository.update_loop_status(
                        loop_id=active_loop_obj.id,
                        status="paused",
                    )
                    active_loop_obj = None
                except Exception as loop_err:
                    logger.debug(f"[ContextRollover] 暂停旧会话活跃 Loop 跳过: {loop_err}")

            logger.info(
                f"[ContextRollover] 会话 {conv_id} 水位达 {watermark_pct:.1f}% (>=90%)，"
                f"已自动创建同工作区接力新会话 {new_conv_id} ({new_title})，"
                f"auto_continue={not task_completed}"
            )
            return {
                "type": "conversation_rollover",
                "old_conversation_id": conv_id,
                "new_conversation_id": new_conv_id,
                "new_title": new_title,
                "workspace_id": target_ws_id,
                "current_cwd": rollover_cwd,
                "watermark_pct": round(watermark_pct, 1),
                "auto_continue": not task_completed,
                "continue_prompt": continue_prompt,
                "handoff_summary": handoff_summary,
            }

        async def _emit_turn_tail_events(
            summary_text: str,
            *,
            is_waiting_user_decision: bool = False,
        ):
            """统一推送本轮结束时的 Loop 收敛、物理资产同步与 Token 微水表事件。"""
            try:
                async for loop_ev in _finalize_turn_loop(
                    summary_text,
                    is_waiting_user_decision=is_waiting_user_decision,
                ):
                    yield loop_ev
            except Exception as e:
                logger.warning(f"自主循环状态收敛更新异常: {e}")

            if has_workspace_mutations and ws_id:
                try:
                    from ..repository.artifact_repository import ArtifactRepository
                    await ArtifactRepository.sync_physical_workspace_artifacts(ws_id)
                except Exception as sync_err:
                    logger.debug(f"[AgentRuntime] 任务收尾物理资产同步跳过: {sync_err}")

            turn_stats_payload: dict[str, Any] | None = None
            try:
                turn_stats_payload = (
                    None
                    if active_incognito
                    else await TokenUsageRepository.get_turn_summary(conv_id, current_turn_id)
                )
                if turn_stats_payload is None and in_memory_step_metrics:
                    turn_stats_payload = _build_turn_summary_from_rows(
                        current_turn_id, in_memory_step_metrics, 0
                    )
                if turn_stats_payload:
                    yield {
                        "type": "token_stats",
                        "conversation_id": conv_id,
                        "turn_id": current_turn_id,
                        "stats": turn_stats_payload,
                    }
            except Exception as stats_err:
                logger.debug(f"[AgentRuntime] 推送 token_stats 跳过: {stats_err}")

            # 90% 上下文水位自动蒸馏交接：确保无痕模式下不触发，且同一轮内防重
            nonlocal rollover_triggered
            if not active_incognito and not rollover_triggered:
                try:
                    ctx_tokens = int((turn_stats_payload or {}).get("context_tokens") or 0)
                    ctx_limit = int((turn_stats_payload or {}).get("context_limit") or 0)
                    if (ctx_limit <= 0 or ctx_tokens <= 0) and in_memory_step_metrics:
                        last_m = in_memory_step_metrics[-1]
                        ctx_tokens = int(last_m.get("prompt_tokens") or 0)
                        ctx_limit = int(last_m.get("context_limit") or 0)
                    if (
                        ctx_limit > 0
                        and ctx_tokens > 0
                        and (ctx_tokens / ctx_limit) >= get_effective_rollover_ratio()
                    ):
                        rollover_triggered = True
                        watermark_pct = min(100.0, (ctx_tokens / ctx_limit) * 100.0)
                        rollover_ev = await _perform_context_rollover(
                            watermark_pct,
                            task_completed=rollover_task_completed,
                        )
                        if rollover_ev:
                            yield rollover_ev
                except Exception as rollover_err:
                    logger.warning(f"[ContextRollover] 自动蒸馏接力新对话失败（已跳过）: {rollover_err}")

        # 抽取本轮视觉附件（图片/PDF），同 Turn 多步调用中保持多模态视觉输入不丢失
        visual_attachments = self._filter_visual_attachments(attachments)

        # 预解析当前模型匹配的主算力通道元信息，供 ContextBuilder 识别本地/云端坍缩策略及调用 /tokenize
        resolved_primary_provider = await engine_pool.resolve_primary_provider_info(model_name)
        # 同轮多步 ReAct 期间锁死 step_idx == 0 生成的 Zone 3 尾注，既守护同轮前缀命中又保持跨轮零残留
        frozen_turn_tail_prompt: str | None = None
        # 进程内存级指标流水收集（兼容无痕模式下零落库依然实时展示底部微水表）
        in_memory_step_metrics: list[dict[str, Any]] = []

        # 5. 进入单轮内 ReAct 思考与工具执行循环 (Intra-Turn Adaptive Loop)
        cancel_event = asyncio.Event()
        run_token = uuid.uuid4().hex
        prev_stream = self._active_streams.get(conv_id)
        if prev_stream and prev_stream.get("cancel_event"):
            prev_stream["cancel_event"].set()
        self._active_streams[conv_id] = {
            "token": run_token,
            "cancel_event": cancel_event,
        }

        async def _check_cancelled() -> bool:
            if cancel_event.is_set():
                return True
            if is_disconnected is not None:
                try:
                    if await is_disconnected():
                        cancel_event.set()
                        return True
                except Exception:
                    pass
            return False

        async def _cleanup_and_pause_loop_on_abort(abort_reason: str) -> None:
            """流断开或协程被取消时，统一终止子进程并确保活跃 Loop 可靠收敛为 paused。"""
            nonlocal active_loop_obj
            cancel_event.set()
            cancel_cli_processes(conv_id)
            shell_executor.cancel_session_processes(conv_id)

            async def _persist_paused_state() -> None:
                nonlocal active_loop_obj
                try:
                    if (
                        active_scene == "agent"
                        and ws_id
                        and not active_loop_obj
                        and not tracked_loop_id
                        and not is_chitchat_turn
                    ):
                        await _ensure_active_loop(initial_status="paused")
                        return

                    target_loop = active_loop_obj
                    if target_loop is None and tracked_loop_id:
                        target_loop = await TaskLoopRepository.get_loop(tracked_loop_id)
                    if target_loop is not None:
                        latest_loop = await TaskLoopRepository.get_loop(target_loop.id) or target_loop
                        if latest_loop.loop_status in (
                            "planning",
                            "executing",
                            "verifying",
                            "reflecting",
                        ):
                            await TaskLoopRepository.update_loop_status(
                                loop_id=latest_loop.id,
                                status="paused",
                            )
                except Exception as pause_err:
                    logger.warning(f"[AgentRuntime] {abort_reason} 收敛 Loop 为 paused 失败: {pause_err}")

            persist_task = asyncio.create_task(_persist_paused_state())
            try:
                await asyncio.shield(persist_task)
            except BaseException as shield_err:
                logger.debug(f"[AgentRuntime] {abort_reason} 外层等待被二次中断，状态更新移交后台完成: {shield_err}")

        step_raw_text = ""
        encountered_stream_error = False
        stream_error_message = ""
        was_cancelled = False
        stream_completed_normally = False

        try:
            step_idx = 0
            force_final_text_only_step = False
            executed_tools_record: list[dict[str, Any]] = []
            retrieved_evidence_chunks: list[dict[str, Any]] = []
            critique_retry_count: int = 0
            while step_idx < current_max_steps:
                if await _check_cancelled():
                    was_cancelled = True
                    break

                is_forced_final_step = force_final_text_only_step
                force_final_text_only_step = False

                # 步数自适应动态扩展检查：只要未触发死循环熔断且任务仍在推进，即自动按 +16 步扩展（不再受限于固定 40 步）
                if active_scene == "agent" and allow_step_extension and step_idx >= current_max_steps - 2:
                    if current_max_steps < self.MAX_REACT_STEPS:
                        current_max_steps = min(current_max_steps + 16, self.MAX_REACT_STEPS)
                        yield {
                            "type": "step_extend",
                            "step": step_idx + 1,
                            "max_steps": current_max_steps,
                            "message": f"任务仍在正常推进中，已自动扩展执行预算至第 {current_max_steps} 步...",
                        }

                ctx = await ContextBuilder.build(
                    conversation_id=conv_id,
                    current_turn_id=current_turn_id,
                    user_query=user_message,
                    tools=active_tools if active_scene == "agent" else None,
                    context_rounds_override=0 if active_scene == "oneshot" else context_rounds,
                    base_system_prompt=system_override,
                    execution_strategy=execution_strategy,
                    clarify_mode=clarify_mode,
                    approved_plan_steps=approved_plan_steps,
                    inquiry_confirmed=inquiry_confirmed,
                    inquiry_action=normalized_inquiry_action,
                    has_prior_execution_unlock=has_prior_execution_unlock,
                    provider_info=resolved_primary_provider,
                    model_name=model_name,
                    frozen_tail_prompt=frozen_turn_tail_prompt,
                    mcp_catalog_prompt=mcp_catalog_prompt if (active_scene == "agent" and not is_planning_phase) else None,
                    swarm_mode=swarm_mode,
                    swarm_role=swarm_role,
                    swarm_member_name=swarm_member_name,
                    swarm_brief=swarm_brief,
                    grounding_config=effective_grounding,
                )

                if await _check_cancelled():
                    was_cancelled = True
                    break

                # 首步向前端推送命中的长期记忆溯源、活跃确定性事实视图与当前任务草稿纸
                if step_idx == 0:
                    frozen_turn_tail_prompt = str(ctx.get("tail_injection_prompt") or "")
                    for exp_cid in ctx.get("injected_case_ids") or []:
                        if exp_cid and exp_cid not in injected_case_ids:
                            injected_case_ids.append(str(exp_cid))
                    matched_ids_from_ctx = ctx.get("matched_skill_ids") or []
                    if not matched_ids_from_ctx and ctx.get("matched_skill_id"):
                        matched_ids_from_ctx = [ctx["matched_skill_id"]]
                    for m_id in matched_ids_from_ctx:
                        clean_sk_id = str(m_id or "").strip()
                        if clean_sk_id and clean_sk_id not in active_skill_ids:
                            active_skill_ids.append(clean_sk_id)
                    if ctx.get("active_facts"):
                        yield {
                            "type": "fact_refs",
                            "facts": ctx["active_facts"],
                        }
                    if ctx.get("referenced_memories"):
                        yield {
                            "type": "memory_refs",
                            "memories": [
                                {
                                    "id": m["id"],
                                    "category": m["category"],
                                    "content": m["content"],
                                    "confidence": m["confidence"],
                                }
                                for m in ctx["referenced_memories"]
                            ],
                        }
                    if ctx.get("working_memory"):
                        yield {
                            "type": "scratchpad_update",
                            "working_memory": ctx["working_memory"],
                        }

                # 构造本步传给算力池的 pre_built_history 与 step_prompt
                history_for_step = list(ctx.get("history_messages", []))
                if step_idx == 0:
                    # 剥离刚刚入库的当前用户消息，交由 execute_stream_with_failover 作为最新 user_prompt 附带附件发送
                    if history_for_step and history_for_step[-1].get("role") == "user":
                        history_for_step = history_for_step[:-1]
                    step_prompt = user_message
                    step_attachments = attachments
                    step_attachments_inline_only = False
                else:
                    if last_step_error_info:
                        if last_step_error_info.get("tool") == "inquiry_guard":
                            step_prompt = (
                                "[🚨 禁止二次弹卡强纠偏]:\n"
                                "用户已经在前端交互卡片中完成全部确认并点击「统一开始执行」，全部执行工具已物理开放！\n"
                                "1. 本轮严禁再次输出任何 `interactive_inquiry` 代码块或请求用户确认的废话；\n"
                                "2. 立即输出标准 `<tool_call>`（如 `write_local_file` / `execute_local_command` / `read_local_file`）完成实际修改、脚本执行与验收验证！"
                            )
                        elif last_step_error_info.get("tool") == "cli_native_tool_denied":
                            denied_names = last_step_error_info.get("denied_tools") or "内置工具"
                            step_prompt = (
                                "[🚨 CLI 宿主内置工具调用越权强纠偏]:\n"
                                f"你刚才试图调用 CLI 宿主原生工具（{denied_names}），已被沙箱策略直接拦截！\n"
                                "本运行环境中宿主原生工具均不可用，右侧前端也**没有任何授权确认弹窗**。\n"
                                "立即改用系统唯一合法工具协议：若需执行操作，请在正文中直接输出标准 XML 块 "
                                '`<tool_call name="execute_local_command">{"command": "...", "reason": "..."}</tool_call>`'
                                "（或 `read_local_file` / `write_local_file` 等），禁止再次触发任何原生工具调用；若无需工具则直接回答用户。"
                            )
                        elif last_step_error_info.get("tool") == "hallucinated_write":
                            claimed_list = ", ".join(last_step_error_info.get("unverified_files") or ["目标文件"])
                            step_prompt = (
                                "[🚨 严禁无工具纯文本伪落盘 —— 立即真实调用工具]:\n"
                                f"系统核验发现你刚才在正文中声称已生成或写入 `{claimed_list}`，但本轮尚未执行任何真实写盘调用，且磁盘中并无对应最新产出！\n"
                                "1. 严禁再用纯文字编造「写入文件成功 / 已物理落盘 / 字节数: xxx」等虚假日志或交付报告；\n"
                                "2. 你必须立即在正文中输出标准 `<tool_call name=\"write_local_file\">` 标签将完整源码直接写入目标文件（切勿将上万字符内联塞入 `execute_local_command`）；\n"
                                "3. 仅当工具返回真实成功回执后，方可向用户汇报完成。"
                            )
                        elif last_step_error_info.get("tool") == "hallucinated_action":
                            step_prompt = (
                                "[🚨 系统防幻觉硬拦截警告 —— 严禁虚假行动与查询声明]:\n"
                                "系统核验发现你在尚未成功调用任何数据查询/读取工具的情况下，在纯文本中声称「已为您查询/已收集完成/已为您记录」。\n"
                                "1. 严禁在无真实证据时凭空捏造数据或谎称已完成操作！\n"
                                "2. 你必须立即输出标准 `<tool_call>`（如 `search_memory_history`、`read_local_file` 等）获取真实数据；\n"
                                "3. 若经工具检索确认当前无任何真实记录（或文件缺失），请直接如实告知用户「未检索到相关记录」，绝不允许为了交差编造假数据！"
                            )
                        elif last_step_error_info.get("circuit_breaker"):
                            failed_c = last_step_error_info.get("failed_cmd", "")
                            step_prompt = (
                                f"[🚨 语义级死循环物理熔断已触发 - 强制切换执行路径]:\n"
                                f"系统检测到你连续多次尝试执行完全相同的失败命令: `{failed_c}`！\n"
                                f"【死循环熔断强制铁律】:\n"
                                f"1. 严禁再次尝试该相同命令！自动步数扩展权限已被系统安全锁定；\n"
                                f"2. 必须立即切换为完全不同的工具或实现路径（例如: 换用 Python 脚本操作文件、检查工作目录或权限）；\n"
                                f"3. 若确认该问题属于外部不可解物理障碍（如系统内核锁死文件、缺少系统管理员权限、网络断开），请立即停止发起任何工具调用，直接向用户详细陈述排查事实与物理卡点，请求人工支持！"
                            )
                        else:
                            err_msg = last_step_error_info.get("summary", "执行返回异常或报错")
                            step_prompt = (
                                f"[🚨 第一性原理自愈与深度推演调度]:\n"
                                f"上一步执行返回异常: {err_msg}。\n"
                                f"【自主攻坚铁律 - 绝不允许卡住或直接退出】:\n"
                                f"1. 仔细阅读上方工具返回的真实报错（特别是诊断日志与 STDERR 堆栈）；\n"
                                f"2. 深度分析报错根因（例如: 若出现 `.Name : The term '.Name' is not recognized`，说明在 PowerShell 双引号内 `$_` 被外层提前展开丢失，应在单引号内书写脚本块，或改用标准的 Python 脚本 `python -c ...` 执行扫描与清理；若缺少依赖或文件请检查相对路径与 `--prefix`）；\n"
                                f"3. 紧扣用户最终目标，自己构思可靠的替代解决方案；\n"
                                f"4. 严禁向用户复述错误或只输出声明！所有工具调用均会自动确认执行，必须立即输出标准 `<tool_call>` 标签执行具体命令纠偏自愈，直到彻底达成目标：\n"
                                f"```xml\n"
                                f'<tool_call name="execute_local_command">\n'
                                f'{{"command": "修正后的终端命令或 Python 替代执行脚本", "reason": "分析日志与语法根因后的自愈攻坚操作"}}\n'
                                f"</tool_call>\n"
                                f"```"
                            )
                    elif is_forced_final_step:
                        step_prompt = (
                            "[最终交付总结阶段 - 禁止调用任何工具]:\n"
                            "全部执行操作、校验与工作记忆归档均已完成，请立即基于上方工具返回的真实结果与物理产出文件，"
                            "向用户输出完整、清晰、结构化的最终交付报告。\n"
                            "1. 本步严禁输出任何 `<tool_call>` 工具调用标签；\n"
                            "2. 严禁自行编造 `[终端/工具输出]:` 或 `[Gemini]:` 等前缀标签。"
                        )
                    else:
                        step_prompt = (
                            "[系统工作流推进]: 上述工具已成功执行并返回真实结果（见上方记录）。\n"
                            "请基于上述真实结果深度思考并推进实现用户的最终目标：\n"
                            "- 若任务尚未彻底达成（例如刚完成扫描，仍需进一步分析、删除无效项、修改文件或验证），请立即输出下一个 `<tool_call>` 继续执行；\n"
                            "- 只有当用户的目标已经物理完成并验证通过时，才向用户输出最终完整的成果解答。"
                        )

                    # 轮内工作草稿纸协同提醒：多步执行时可将关键结论沉淀至草稿纸，防止长任务遗忘
                    if active_scene == "agent" and step_idx >= 4 and not is_forced_final_step:
                        step_prompt += (
                            "\n\n💡 [多步任务协同提醒]: 若你已查明关键文件路径、数据结构或中间状态，"
                            "请适时通过 `update_working_memory` 记录关键结论，以便后续步骤高效直达。"
                        )

                    step_attachments = visual_attachments or None
                    step_attachments_inline_only = bool(visual_attachments)

                step_raw_text = ""
                encountered_stream_error = False
                stream_error_message = ""
                stream_error_code = ""
                stream_denied_tools = ""

                step_thinking_level = self._resolve_step_thinking_level(
                    base_thinking_level=thinking_level,
                    lock_high_thinking=lock_high_thinking,
                    active_scene=active_scene,
                    step_idx=step_idx,
                    is_planning_phase=is_planning_phase,
                    is_forced_final_step=is_forced_final_step,
                    recovered_error_count=recovered_error_count,
                    consecutive_stalls=consecutive_stalls,
                    last_step_error_info=last_step_error_info,
                    is_chitchat_turn=is_chitchat_turn,
                    user_message=user_message,
                    has_attachments=bool(attachments),
                )

                async for event in engine_pool.execute_stream_with_failover(
                    conversation_id=conv_id,
                    user_prompt=step_prompt,
                    model_name=model_name,
                    thinking_level=step_thinking_level,
                    system_override=ctx.get("system_instruction"),
                    temperature=effective_temperature,
                    work_dir=ctx.get("current_cwd"),
                    context_rounds=context_rounds,
                    attachments=step_attachments,
                    pre_built_history=history_for_step,
                    attachments_inline_only=step_attachments_inline_only,
                    turn_id=current_turn_id,
                    step_index=step_idx,
                    agent_role="primary",
                    tail_injection=frozen_turn_tail_prompt or "",
                    incognito=active_incognito,
                    cancel_event=cancel_event,
                ):
                    if await _check_cancelled():
                        was_cancelled = True
                        break
                    etype = event.get("type")
                    if etype == "cancelled":
                        was_cancelled = True
                        break
                    elif etype == "start":
                        start_payload = {**event, "step_index": step_idx}
                        if replace_prev_empty_step_pending:
                            start_payload["replace_prev_empty_step"] = True
                            replace_prev_empty_step_pending = False
                        yield start_payload
                    elif etype == "thinking_chunk":
                        yield event
                    elif etype == "model_downgrade":
                        effective_model_name = event.get("current_model", effective_model_name)
                        yield event
                    elif etype == "step_metrics":
                        m_dict = event.get("metrics")
                        if isinstance(m_dict, dict):
                            in_memory_step_metrics.append(m_dict)
                    elif etype == "stream_reset":
                        # 网络中途断流重连接力：立即清空当前步骤已累加的半截文本，与前端气泡绝对对齐
                        step_raw_text = ""
                        yield event
                    elif etype == "chunk":
                        chunk_str = event.get("content", "")
                        step_raw_text += chunk_str
                        final_account = event.get("account", final_account)
                        yield event
                    elif etype == "done":
                        step_raw_text = event.get("full_content", step_raw_text)
                        final_account = event.get("account", final_account)
                    elif etype == "error":
                        encountered_stream_error = True
                        stream_error_message = str(event.get("content") or "算力调度发生错误")
                        stream_error_code = str(event.get("error_code") or "")
                        stream_denied_tools = str(event.get("denied_tools") or "")
                        break
                    else:
                        # 透传非原生工具类的辅助事件
                        yield event

                if was_cancelled or await _check_cancelled():
                    was_cancelled = True
                    break

                if encountered_stream_error:
                    if (
                        stream_error_code == "cli_native_tool_denied"
                        and cli_native_tool_retry_count < 2
                        and step_idx < current_max_steps - 1
                        and not is_forced_final_step
                    ):
                        cli_native_tool_retry_count += 1
                        if step_thinking_level in ("low", "medium"):
                            lock_high_thinking = True
                        logger.warning(
                            "[AgentRuntime] 捕获 CLI 越权调用宿主内置工具 (%s)，触发盘中无感自愈纠偏 (%s/2)",
                            stream_denied_tools or "native_tool",
                            cli_native_tool_retry_count,
                        )
                        last_step_error_info = {
                            "tool": "cli_native_tool_denied",
                            "denied_tools": stream_denied_tools or "内置工具",
                            "summary": stream_error_message,
                        }
                        replace_prev_empty_step_pending = True
                        yield {"type": "stream_reset"}
                        step_idx += 1
                        continue
                    if stream_error_code == "cli_native_tool_denied":
                        denied_label = stream_denied_tools or "内置工具"
                        stream_error_message = (
                            f"CLI 引擎多次尝试调用宿主内置工具（{denied_label}）已被沙箱策略自动拦截（无需在右侧确认弹窗授权）。"
                            "请重试或提示模型改用标准 <tool_call> 执行。"
                        )
                    yield {"type": "error", "content": stream_error_message}
                    # 异常中断也要保留用户可见报错并终结活跃 Loop，防止重开或切换后只剩孤零零用户消息
                    err_banner = f"⚠️ {stream_error_message or '模型响应异常，本次执行已终止。'}"
                    partial_clean = (
                        ToolProtocolAdapter.parse_xml_tool_calls(step_raw_text)[0].strip()
                        if step_raw_text
                        else ""
                    )
                    unverified_partial = self._detect_unverified_write_hallucination(
                        partial_clean,
                        cwd=ctx.get("current_cwd"),
                        produced_artifacts=produced_artifacts,
                        executed_mutating_shell=executed_mutating_shell,
                    )
                    if unverified_partial:
                        err_banner = (
                            f"{err_banner}\n\n"
                            f"> ⚠️ **物理落盘未完成警示**：本次执行已在完成前中断，上文中提及的 `"
                            + "`, `".join(unverified_partial[:5])
                            + "` 尚未通过真实写盘工具生效，请以工作区目录实际文件为准。"
                        )
                    persisted_err = f"{partial_clean}\n\n{err_banner}" if partial_clean else err_banner
                    if not active_incognito and conv_id:
                        try:
                            await MessageRepository.save_message(
                                conversation_id=conv_id,
                                role="model",
                                content=persisted_err,
                                executed_by_account=final_account,
                                token_count=len(persisted_err) // 3,
                                turn_id=current_turn_id,
                            )
                            await ConversationRepository.touch(conv_id)
                        except Exception as save_err:
                            logger.warning(f"[AgentRuntime] 保存流式报错消息失败: {save_err}")
                    if active_scene == "agent" and ws_id:
                        try:
                            eff_err_loop = await _ensure_active_loop()
                            if eff_err_loop:
                                err_pad = eff_err_loop.scratchpad_md or ""
                                err_note = f"\n\n> ⚠️ 终止原因: {stream_error_message[:200]}" if stream_error_message else ""
                                await TaskLoopRepository.update_loop_status(
                                    loop_id=eff_err_loop.id,
                                    status="failed",
                                    scratchpad_md=(f"{err_pad}{err_note}" if err_pad and err_note else None),
                                )
                                yield {
                                    "type": "loop_failed",
                                    "conversation_id": conv_id,
                                    "loop_id": eff_err_loop.id,
                                    "iteration": eff_err_loop.current_iteration,
                                    "max_iterations": eff_err_loop.max_iterations,
                                    "status": "failed",
                                    "error": stream_error_message,
                                }
                                active_loop_obj = None
                        except Exception as loop_err:
                            logger.warning(f"[AgentRuntime] 报错收敛 Loop 状态失败: {loop_err}")
                    break

                if (last_step_error_info or {}).get("tool") == "cli_native_tool_denied":
                    # 上一步 CLI 越权已在本步成功自愈恢复，清理残留错误标记以免误判 stall 或 loop_failed
                    last_step_error_info = None
                    recovered_error_count += 1

                # 6. 解析本步输出是否包含工具调用 (<tool_call>)
                clean_text, tool_calls = ToolProtocolAdapter.parse_xml_tool_calls(step_raw_text)
                if is_forced_final_step and tool_calls:
                    logger.warning(
                        "[AgentRuntime] is_forced_final_step 出站拦截：已强制剥离 %d 个意外 <tool_call> 调用",
                        len(tool_calls),
                    )
                    tool_calls = []

                # ── 关键反停滞与伪调用拦截（Anti-Stall & Steer）带精准白名单 ──
                raw_stripped = (step_raw_text or "").strip()
                # 先剥离空壳/无效的 <tool_call> 残余，防止误判伪工具调用
                stripped_without_empty = ToolProtocolAdapter.strip_empty_tool_calls(raw_stripped).strip()

                # 白名单判据：模型礼貌向用户发起确认提问（如询问是否允许删除），或完成了正常完整总结
                is_user_confirmation_request = bool(re.search(
                    r"(?:是否允许|请问是否|请确认|需要您确认|是否继续|是否需要|是否删除|请选择|如需继续|？|\?)",
                    stripped_without_empty
                ))
                has_explicit_pseudo_tags = bool(re.search(
                    r"(?:\[调用工具|\[tool_call|<tool_call|<function=|</tool_code>|```xml\s*<tool_call|<\s*/?\s*[\uFF5C|][\uFF5C|\s]*DSML\b)",
                    stripped_without_empty,
                    re.IGNORECASE
                ))
                is_bare_json_command = bool(re.search(r'\{\s*"command"\s*:', stripped_without_empty))

                # 仅当命中真实机器伪标签、低/中思考档位空回复，或上步报错且模型输出极简敷衍且非提问时，才触发强制推偏
                is_empty_in_reduced_thinking = (
                    step_thinking_level in ("low", "medium")
                    and not stripped_without_empty
                    and not tool_calls
                )
                is_abnormal_stall = (
                    has_explicit_pseudo_tags
                    or is_bare_json_command
                    or is_empty_in_reduced_thinking
                    or (last_step_error_info is not None and len(stripped_without_empty) < 60 and not is_user_confirmation_request)
                )
                # 若剔除空标签后文本有效且无伪标签，直接放行，严禁触发重发
                if not tool_calls and not has_explicit_pseudo_tags and len(stripped_without_empty) >= 15:
                    is_abnormal_stall = False

                # S6 自动升档护栏：low/medium 档位下若发生空回复或工具解析失败，立即锁定当前轮剩余步骤升回 high
                if step_thinking_level in ("low", "medium") and (
                    is_empty_in_reduced_thinking
                    or (not tool_calls and (has_explicit_pseudo_tags or is_bare_json_command))
                ):
                    lock_high_thinking = True
                    logger.info(
                        f"[AgentRuntime] S6 自动升档护栏触发（{step_thinking_level} -> high）: "
                        f"empty={is_empty_in_reduced_thinking}, pseudo_tag={has_explicit_pseudo_tags}"
                    )

                # 情况 A：模型未发起真实工具调用
                if not tool_calls or active_scene != "agent":
                    unverified_write_claims = (
                        self._detect_unverified_write_hallucination(
                            stripped_without_empty,
                            cwd=ctx.get("current_cwd"),
                            produced_artifacts=produced_artifacts,
                            executed_mutating_shell=executed_mutating_shell,
                        )
                        if (active_scene == "agent" and not is_planning_phase)
                        else []
                    )
                    if (
                        active_scene == "agent"
                        and not is_planning_phase
                        and not is_forced_final_step
                        and unverified_write_claims
                        and not is_user_confirmation_request
                        and consecutive_stalls < 3
                        and step_idx < current_max_steps - 1
                    ):
                        consecutive_stalls += 1
                        logger.warning(
                            f"[AgentRuntime] 拦截无工具纯叙事伪落盘幻觉 ({unverified_write_claims})，激活纠偏 (第{consecutive_stalls}次)"
                        )
                        replace_prev_empty_step_pending = True
                        yield {"type": "stream_reset"}
                        last_step_error_info = {
                            "tool": "hallucinated_write",
                            "unverified_files": unverified_write_claims,
                            "summary": "模型未调用写盘工具却在纯文本中宣称文件已落盘，强制要求通过 write_local_file 真实写入",
                        }
                        step_idx += 1
                        continue

                    # 🛡️ 拦截开启 verify_tool_call 时无工具调用的虚假行动/查询声明幻觉
                    if (
                        effective_grounding
                        and effective_grounding.get("verify_tool_call")
                        and not is_planning_phase
                        and not is_forced_final_step
                        and self._detect_unverified_action_claims(stripped_without_empty, executed_tools_record)
                        and consecutive_stalls < 2
                        and step_idx < current_max_steps - 1
                    ):
                        consecutive_stalls += 1
                        logger.warning(f"[AgentRuntime] 拦截无工具调用虚假行动声明幻觉，激活纠偏 (第{consecutive_stalls}次)")
                        replace_prev_empty_step_pending = True
                        yield {"type": "stream_reset"}
                        last_step_error_info = {
                            "tool": "hallucinated_action",
                            "summary": "尚未成功调用任何数据查询或操作工具，严禁在纯文本中声称已完成查询或汇总。请先调用对应工具获取真实数据！",
                        }
                        step_idx += 1
                        continue

                    # 防止用户已点确认执行后，模型仍违背指令二次吐出 interactive_inquiry 卡空转
                    if (
                        active_scene == "agent"
                        and explicit_execute_unlock
                        and not is_planning_phase
                        and ToolProtocolAdapter.contains_interactive_inquiry(raw_stripped)
                        and consecutive_stalls < 2
                        and step_idx < current_max_steps - 1
                    ):
                        consecutive_stalls += 1
                        logger.warning(f"[AgentRuntime] 拦截确认后非法二次 interactive_inquiry 弹窗，触发执行纠正 (第{consecutive_stalls}次)")
                        yield {"type": "stream_reset"}
                        last_step_error_info = {
                            "tool": "inquiry_guard",
                            "summary": "用户已完成全部确认并点击开始执行，严禁重复输出 interactive_inquiry 确认卡",
                        }
                        step_idx += 1
                        continue

                    if (
                        active_scene == "agent"
                        and (not is_forced_final_step or is_empty_in_reduced_thinking)
                        and is_abnormal_stall
                        and not is_user_confirmation_request
                        and consecutive_stalls < 3
                        and step_idx < current_max_steps - 1
                    ):
                        if is_forced_final_step and is_empty_in_reduced_thinking:
                            force_final_text_only_step = True
                        consecutive_stalls += 1
                        logger.warning(f"[AgentRuntime] 捕获模型伪工具调用声明/卡顿: '{raw_stripped}'，激活强制纠偏推进器 (第{consecutive_stalls}次)")
                        last_step_error_info = {
                            "tool": "steering",
                            "summary": "模型输出了未执行的伪工具声明或空回复，未包含有效内容或具体 <tool_call> 标签与参数",
                        }
                        step_idx += 1
                        continue

                    if clean_text:
                        raw_final_text = clean_text
                    elif has_explicit_pseudo_tags:
                        raw_final_text = "（模型输出的工具调用协议未正确闭合或已达推演步数上限，已自动过滤内部协议文本；如需继续执行请重试。）"
                    else:
                        raw_final_text = step_raw_text
                    if raw_final_text and (
                        "interactive_inquiry" in raw_final_text.lower()
                        or '"inquiry_id"' in raw_final_text
                    ):
                        final_saved_text = ToolProtocolAdapter.normalize_interactive_inquiry_markdown(raw_final_text)
                    else:
                        final_saved_text = raw_final_text

                    has_inquiry_card = ToolProtocolAdapter.contains_interactive_inquiry(final_saved_text)
                    if unverified_write_claims and final_saved_text and not has_inquiry_card:
                        final_saved_text += (
                            "\n\n> ⚠️ **系统磁盘核验提示**：未能在工作区中核实 `"
                            + "`, `".join(unverified_write_claims[:5])
                            + "` 的有效落盘记录，实际产出请以工作区物理文件夹为准。"
                        )
                    is_waiting_user_decision = bool(
                        active_scene == "agent" and (has_inquiry_card or is_planning_phase)
                    )

                    # 阶段四：自然收尾自审与多维置信度治理 (v1.5.5)
                    try:
                        from ..governance.self_critique import SelfCritiqueCritic
                        from ..governance.confidence_evaluator import ConfidenceEvaluator

                        critique_res = SelfCritiqueCritic.evaluate_draft(
                            user_query=user_message,
                            retrieved_evidence=retrieved_evidence_chunks,
                            draft_response=final_saved_text or accumulated_visible_text,
                            has_version_conflict=False,
                        )
                        if not critique_res.is_passed and critique_retry_count < 1:
                            critique_retry_count += 1
                            yield {
                                "type": "critique_redraft",
                                "reasons": critique_res.critique_points,
                                "message": f"自审检出潜在缺陷: {'; '.join(critique_res.critique_points)}，正在触发一次自动纠偏重写...",
                            }
                            critique_prompt = (
                                f"[系统自审质量拦截]: 前置交付草稿经自审查验发现以下缺陷:\n"
                                + "\n".join(f"- {p}" for p in critique_res.critique_points)
                                + "\n请严格依据真实检索到的事实依据修正上述问题，重新输出严谨准确的最终回答。"
                            )
                            await MessageRepository.save_message(
                                conversation_id=conv_id,
                                role="user",
                                content=critique_prompt,
                            )
                            step_idx += 1
                            continue

                        conf_rep = ConfidenceEvaluator.evaluate(
                            retrieved_chunks=retrieved_evidence_chunks,
                            critique_res=critique_res,
                            tools_executed=executed_tools_record,
                            strict_mode=False,
                        )
                        yield {
                            "type": "confidence_report",
                            "score": conf_rep.score,
                            "tier_level": conf_rep.tier_level,
                            "reasons": conf_rep.reasons,
                            "requires_confirmation": conf_rep.requires_confirmation,
                            "evidence_sources": conf_rep.evidence_sources,
                        }
                        if conf_rep.requires_confirmation:
                            is_waiting_user_decision = True
                    except Exception as conf_err:
                        logger.debug(f"[AgentRuntime] 自然收尾置信度治理跳过: {conf_err}")

                    if final_saved_text:
                        await MessageRepository.save_message(
                            conversation_id=conv_id,
                            role="model",
                            content=final_saved_text,
                            executed_by_account=final_account,
                            token_count=len(final_saved_text) // 3,
                            turn_id=current_turn_id,
                        )
                        await ConversationRepository.touch(conv_id)
                        if conv_id and not active_incognito:
                            long_term_memory.schedule_extraction(conv_id, delay_seconds=180)
                    accumulated_visible_text += ("\n\n" + final_saved_text if accumulated_visible_text else final_saved_text)

                    # ── 任务自主闭环门控与收尾统一事件推送 ──
                    rollover_task_completed = True
                    summary_snippet = (final_saved_text or accumulated_visible_text).strip().split("\n")[0][:80]
                    async for tail_ev in _emit_turn_tail_events(
                        summary_snippet or "模型已完成自主闭环并输出交付结论",
                        is_waiting_user_decision=is_waiting_user_decision,
                    ):
                        yield tail_ev

                    stream_completed_normally = True
                    yield {
                        "type": "done",
                        "full_content": accumulated_visible_text.strip(),
                        "final_step_content": final_saved_text,
                        "account": final_account,
                        "model": effective_model_name,
                    }
                    break

                consecutive_stalls = 0

                # 情况 B：模型发起了工具调用 -> 先清洗工具调用前的预剧透伪结果，再规范化工具 XML 并落盘
                step_tool_names = [
                    str((tc.get("function") or {}).get("name") or "").strip()
                    for tc in tool_calls
                ]
                has_substantive_tools_in_step = any(
                    tname and tname not in self.ARCHIVE_ONLY_TOOLS for tname in step_tool_names
                )
                clean_text, was_premature_stripped = self._strip_premature_post_tool_hallucination(
                    step_raw_text,
                    clean_text,
                    has_substantive_tools=has_substantive_tools_in_step,
                )
                if was_premature_stripped:
                    logger.warning(
                        "[AgentRuntime] 拦截并剥离实质性工具调用后的未执行预剧透伪落盘报告 "
                        f"(conversation={conv_id}, turn={current_turn_id}, tools={step_tool_names})"
                    )
                    yield {"type": "stream_reset"}
                    if clean_text:
                        yield {"type": "chunk", "content": clean_text, "account": final_account}
                if clean_text and "interactive_inquiry" in clean_text.lower():
                    clean_text = ToolProtocolAdapter.normalize_interactive_inquiry_markdown(clean_text)
                accumulated_visible_text += ("\n\n" + clean_text if (accumulated_visible_text and clean_text) else clean_text)
                formatted_tc_xml = "\n".join(
                    f'<tool_call name="{tc.get("function", {}).get("name", "")}">{tc.get("function", {}).get("arguments", "{}")}</tool_call>'
                    for tc in tool_calls
                )
                saved_content = (clean_text + "\n" + formatted_tc_xml).strip() if clean_text else formatted_tc_xml

                step_process_msg_id = await MessageRepository.save_message(
                    conversation_id=conv_id,
                    role="model",
                    content=saved_content,
                    executed_by_account=final_account,
                    token_count=len(step_raw_text) // 3,
                    turn_id=current_turn_id,
                    tool_calls_json=json.dumps(tool_calls, ensure_ascii=False),
                )

                # 逐个执行本步拦截到的工具调用
                task_marked_completed = False
                for tc in tool_calls:
                    if await _check_cancelled():
                        was_cancelled = True
                        break
                    call_id = tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                    func_info = tc.get("function", {})
                    func_name = func_info.get("name", "")
                    raw_args = func_info.get("arguments", "{}")
                    args_parse_failed = False
                    try:
                        parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                        if not isinstance(parsed_args, dict):
                            args = {}
                            args_parse_failed = True
                        else:
                            args = parsed_args
                    except Exception:
                        args = {}
                        args_parse_failed = bool(isinstance(raw_args, str) and raw_args.strip())

                    tool_output: str = ""
                    compressed: str = ""
                    rescue_addon = ""
                    tool_exit_code: int | None = None
                    s10_intercepted = False
                    is_bg_detached = False
                    bg_poll_running = False
                    args_hash = self._compute_tool_args_hash(args)
                    sig_key = (func_name, args_hash)

                    # 🛡️ 工具白名单强隔离门控：allowed_tools 非空时严格拦截白名单外工具
                    if allowed_tools_set is not None and func_name not in allowed_tools_set:
                        deny_tool_msg = (
                            f"Denied: 【工具权限隔离拦截】工具 `{func_name}` 未在当前 Agent 的授权工具白名单 "
                            f"({sorted(allowed_tools_set)}) 中，已拒绝执行。"
                        )
                        compressed = f"[工具白名单拦截 `{func_name}`]"
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "status": "denied",
                            "output": deny_tool_msg,
                            "compressed": compressed,
                        }
                        await MessageRepository.save_message(
                            conversation_id=conv_id,
                            turn_id=current_turn_id,
                            role="tool",
                            content=deny_tool_msg,
                            compressed_content=compressed,
                            tool_call_id=call_id,
                        )
                        last_step_error_info = {
                            "tool": func_name,
                            "summary": deny_tool_msg,
                        }
                        continue

                    # 🛡️ 物理只读硬门控拦截：若处于规划阶段，绝对禁止执行任何破坏性写入或终端命令
                    if is_planning_phase and func_name not in PLANNING_SAFE_TOOLS:
                        deny_reason = (
                            f"Denied: 【安全只读硬门控】当前处于「谋定而后动 (Plan-First)」方案规划阶段，写盘与终端执行权限已被物理锁定。"
                            f"工具 [{func_name}] 禁止调用。请先向用户展示完整的【实施方案】与【分步执行步骤清单】，"
                            f"并在末尾输出 ```interactive_inquiry 协议块等待用户确认批准。"
                        )
                        compressed = f"[只读门控硬拦截 `{func_name}`]"
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "status": "denied",
                            "output": deny_reason,
                            "compressed": compressed,
                        }
                        await MessageRepository.save_message(
                            conversation_id=conv_id,
                            turn_id=current_turn_id,
                            role="tool",
                            content=deny_reason,
                            compressed_content=compressed,
                            tool_call_id=call_id,
                        )
                        last_step_error_info = {
                            "tool": func_name,
                            "summary": deny_reason,
                        }
                        continue

                    # 渐进式按需激活 Loop：确认即将实际执行合法工具时才初始化领航舱实例
                    if active_scene == "agent" and ws_id and not is_planning_phase and not active_loop_obj:
                        try:
                            await _ensure_active_loop()
                        except Exception as init_loop_err:
                            logger.warning(f"按需激活自主循环实例异常: {init_loop_err}")

                    # 🕰️ Pre-Tool 自动静默快照：本轮首次准备修改代码/执行终端命令时自动建立双态还原点
                    if (
                        not turn_checkpoint_taken
                        and not is_planning_phase
                        and active_scene == "agent"
                        and func_name in ("write_local_file", "replace_in_file", "execute_local_command", "run_skill_tool")
                    ):
                        turn_checkpoint_taken = True
                        try:
                            ckpt_ws_root = target_cwd or os.getcwd()
                            ckpt_info = await ShadowCheckpointManager.create_checkpoint(
                                conversation_id=conv_id,
                                workspace_id=ws_id,
                                workspace_root=ckpt_ws_root,
                                turn_id=max(0, current_turn_id - 1),
                                note=f"pre_{func_name}",
                            )
                            yield {
                                "type": "checkpoint_created",
                                "ckpt_id": ckpt_info["ckpt_id"],
                                "turn_id": ckpt_info["turn_id"],
                                "note": ckpt_info["note"],
                            }
                        except Exception as ckpt_err:
                            logger.warning(f"[AgentRuntime] Pre-Tool 自动静默快照跳过: {ckpt_err}")

                    # S10: 轮内无写操作穿插的重复工具签名拦截（连续第 2 次完全相同只读/失败调用直接拦截；豁免后台轮询与 Swarm 阻塞等待/增量拉取）
                    if (
                        not args_parse_failed
                        and func_name not in ("check_background_command", "swarm_wait", "swarm_read")
                        and sig_key in turn_tool_sig_cache
                    ):
                        s10_intercepted = True
                        seen_step = turn_tool_sig_cache[sig_key]
                        tool_output = (
                            f"Denied: [重复工具调用拦截] 工具 `{func_name}` 使用完全相同的参数已于 Step {seen_step} 执行过，"
                            f"且期间未发生任何文件写入或状态变更，结果不会发生变化。"
                            f"请立即基于已有信息更换排查路径、修改代码或向用户如实报告阻塞原因。"
                        )
                        compressed = f"[拦截重复调用 `{func_name}` (与 Step {seen_step} 参数一致)]"
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": func_name,
                            "reason": "拦截无写操作穿插的重复工具调用",
                            "status": "denied",
                            "output": tool_output,
                            "compressed": compressed,
                        }

                    # ── 工具 1: 受控本地终端命令 (execute_local_command) ──
                    if s10_intercepted:
                        pass
                    elif func_name == "execute_local_command":
                        cmd_str = str(args.get("command", "")).strip()
                        reason_str = str(args.get("reason", "执行本地终端诊断或操作")).strip()
                        norm_cmd = re.sub(r"\s+", " ", cmd_str)

                        # 语义级死循环物理硬阻断：若模型在收到熔断警告后仍顽固输出同一条失败命令，直接封禁执行
                        is_hard_loop_blocked = bool(
                            last_step_error_info
                            and last_step_error_info.get("circuit_breaker")
                            and last_step_error_info.get("failed_cmd") == norm_cmd
                        )

                        sec_level = "DENY" if is_hard_loop_blocked else shell_executor.check_security(cmd_str)

                        if sec_level == "DENY":
                            if is_hard_loop_blocked:
                                deny_reason = (
                                    f"Denied: 【死循环物理硬阻断】命令 `{cmd_str}` 已经触发死循环熔断，系统已物理封禁重复执行此失败操作。"
                                    f"请立即停止调用工具，直接向用户详细陈述排查事实与物理卡点，说明无法自动完成的原因并请求人工介入。"
                                )
                                await audit_service.log(
                                    event_type="shell_command_denied",
                                    actor="agent",
                                    detail={"command": cmd_str, "reason": reason_str, "loop_blocked": True},
                                    approved=False,
                                )
                                tool_output = deny_reason
                                compressed = f"[死循环物理硬阻断 `{norm_cmd[:45]}`]"
                                yield {
                                    "type": "tool_end",
                                    "call_id": call_id,
                                    "name": func_name,
                                    "command": cmd_str,
                                    "reason": reason_str,
                                    "status": "denied",
                                    "output": tool_output,
                                    "compressed": compressed,
                                }
                            else:
                                # 🔴 L2 高危 Shell 拦截：呼出四键审批卡（15s 超时自动封锁）
                                l2_gate_res: dict[str, Any] = {}
                                async for evt in _eval_and_wait_l2_approval(
                                    t_name=func_name,
                                    t_args={"command": cmd_str, "reason": reason_str},
                                    r_reason=f"命令包含高危敏感特征或系统破坏性指令: {cmd_str[:60]}",
                                    result_holder=l2_gate_res,
                                ):
                                    yield evt

                                if not l2_gate_res.get("allowed", False):
                                    deny_reason = f"Denied: 【高危拦截已阻断】{l2_gate_res.get('guidance', '用户拒绝了该项高危操作')}"
                                    await audit_service.log(
                                        event_type="shell_command_denied",
                                        actor="agent",
                                        detail={"command": cmd_str, "reason": reason_str, "guidance": l2_gate_res.get("guidance")},
                                        approved=False,
                                    )
                                    tool_output = deny_reason
                                    compressed = f"[高危操作阻断 `{cmd_str[:45]}`]"
                                    yield {
                                        "type": "tool_end",
                                        "call_id": call_id,
                                        "name": func_name,
                                        "command": cmd_str,
                                        "reason": reason_str,
                                        "status": "denied",
                                        "output": tool_output,
                                        "compressed": compressed,
                                    }
                                else:
                                    # 用户批准本次或改参放行
                                    eff_cmd = str(l2_gate_res.get("args", {}).get("command") or cmd_str).strip()
                                    cmd_str = eff_cmd
                                    norm_cmd = re.sub(r"\s+", " ", cmd_str)
                                    sec_level = "CONFIRM"

                        if sec_level != "DENY":
                            # 全自动确认直通执行（包含 CONFIRM 与 SAFE 级命令），无需用户手动点击确认
                            yield {
                                "type": "tool_start",
                                "call_id": call_id,
                                "name": func_name,
                                "command": cmd_str,
                                "reason": reason_str,
                                "security": "AUTO_CONFIRMED" if sec_level == "CONFIRM" else "SAFE",
                            }
                            try:
                                exec_res = await shell_executor.run(
                                    command=cmd_str,
                                    cwd=ctx.get("current_cwd"),
                                    session_id=conv_id,
                                    run_in_background=bool(args.get("run_in_background", False)),
                                )
                            except OSError as shell_os_err:
                                logger.warning(f"[AgentRuntime] 本地命令子进程启动异常: {shell_os_err}")
                                exec_res = {
                                    "output": (
                                        f"Error: 命令进程启动失败 ({shell_os_err})。"
                                        "若因单条命令行参数过长或路径超限引发（如 WinError 206），请改用 `write_local_file` 直接写入目标文件后再执行。"
                                    ),
                                    "exit_code": -2,
                                    "new_cwd": ctx.get("current_cwd"),
                                }
                            tool_output = str(exec_res.get("output") or "")
                            is_bg_detached = bool(exec_res.get("background"))
                            tool_exit_code = 0 if is_bg_detached else int(exec_res.get("exit_code", 0) or 0)
                            raw_new_cwd = exec_res.get("new_cwd")
                            new_cwd: str | None = raw_new_cwd if isinstance(raw_new_cwd, str) and raw_new_cwd else None
                            if new_cwd and new_cwd != ctx.get("current_cwd"):
                                ctx["current_cwd"] = new_cwd
                                await ConversationRepository.update_agent_state(conv_id, current_cwd=new_cwd)

                            # 若执行了环境安装/桌面应用或 CDP 调试启动命令，尝试唤醒此前冷却中的 MCP 服务并刷新工具清单
                            if (
                                not is_bg_detached
                                and tool_exit_code == 0
                                and not is_planning_phase
                                and mcp_manager.has_cooling_servers()
                                and re.search(
                                    r"(9222|remote-debugging-port|playwright|mcp|npx|npm\s+install|pip\s+install|Start-Process)",
                                    cmd_str,
                                    re.I,
                                )
                            ):
                                try:
                                    mcp_manager.clear_cooldowns()
                                    active_tools, mcp_catalog_prompt = await self._assemble_agent_tools(
                                        conv_id=conv_id,
                                        user_message=user_message,
                                        collapse_anchor_turn_id=int(ctx.get("collapse_anchor_turn_id") or conv_collapse_anchor),
                                    )
                                except Exception as mcp_refresh_err:
                                    logger.debug(f"命令后刷新 MCP 工具跳过: {mcp_refresh_err}")

                            if is_bg_detached:
                                bg_job_id = str(exec_res.get("job_id") or "")
                                compressed = f"[命令已转入后台运行 job_id={bg_job_id}: `{cmd_str[:45]}`]"
                            else:
                                compressed = shell_executor.compress_output(
                                    cmd_str, tool_output, tool_exit_code
                                )
                            yield {
                                "type": "tool_end",
                                "call_id": call_id,
                                "name": func_name,
                                "command": cmd_str,
                                "reason": reason_str,
                                "status": "running" if is_bg_detached else ("completed" if tool_exit_code == 0 else "error"),
                                "exit_code": tool_exit_code,
                                "output": tool_output,
                                "compressed": compressed,
                                "cwd": new_cwd,
                                **({"job_id": str(exec_res.get("job_id") or "")} if is_bg_detached else {}),
                            }

                    # ── 工具 1.1: 后台长耗时命令状态查询与安全等待 (check_background_command) ──
                    elif func_name == "check_background_command":
                        bg_job_id = str(args.get("job_id", "")).strip()
                        wait_secs = float(args.get("wait_seconds", 20) if args.get("wait_seconds") is not None else 20)
                        tail_lines = int(args.get("tail_lines", 40) or 40)
                        yield {
                            "type": "tool_start",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"check_background_command('{bg_job_id}', wait={int(wait_secs)}s)",
                            "reason": f"跟踪后台任务 {bg_job_id} 最新进度与增量日志",
                            "security": "SAFE",
                        }
                        bg_res = await shell_executor.background_manager.check_job(
                            bg_job_id,
                            wait_seconds=wait_secs,
                            tail_lines=tail_lines,
                        )
                        bg_status = str(bg_res.get("status") or "unknown")
                        tool_output = str(bg_res.get("output") or "")
                        bg_poll_running = bg_status == "running"
                        if bg_status == "completed":
                            tool_exit_code = 0
                            bg_new_cwd = bg_res.get("new_cwd")
                            if isinstance(bg_new_cwd, str) and bg_new_cwd and bg_new_cwd != ctx.get("current_cwd"):
                                ctx["current_cwd"] = bg_new_cwd
                                await ConversationRepository.update_agent_state(conv_id, current_cwd=bg_new_cwd)
                        elif bg_status in ("failed", "not_found"):
                            raw_rc = bg_res.get("exit_code")
                            tool_exit_code = int(raw_rc) if raw_rc is not None else -1
                        else:
                            tool_exit_code = 0

                        compressed = (
                            f"[后台任务 `{bg_job_id}` 状态: {bg_status}, "
                            f"日志={bg_res.get('log_bytes', 0)}B, 缓存={bg_res.get('active_dir_bytes', 0)}B]"
                        )
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"check_background_command('{bg_job_id}')",
                            "reason": f"查询后台任务 {bg_job_id} 状态",
                            "status": (
                                "running"
                                if bg_status == "running"
                                else ("completed" if bg_status in ("completed", "cancelled") else "error")
                            ),
                            "exit_code": tool_exit_code,
                            "output": tool_output,
                            "compressed": compressed,
                            "job_id": bg_job_id,
                        }

                    # ── 工具 1.2: 终止后台运行命令 (cancel_background_command) ──
                    elif func_name == "cancel_background_command":
                        bg_job_id = str(args.get("job_id", "")).strip()
                        yield {
                            "type": "tool_start",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"cancel_background_command('{bg_job_id}')",
                            "reason": f"终止后台任务 {bg_job_id}",
                            "security": "AUTO_CONFIRMED",
                        }
                        cancel_res = await shell_executor.background_manager.cancel_job(bg_job_id)
                        cancel_status = str(cancel_res.get("status") or "cancelled")
                        tool_output = str(cancel_res.get("output") or "")
                        tool_exit_code = 0 if cancel_status == "cancelled" else -1
                        compressed = f"[已终止后台任务 `{bg_job_id}`: {cancel_status}]"
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"cancel_background_command('{bg_job_id}')",
                            "reason": f"终止后台任务 {bg_job_id}",
                            "status": "completed" if cancel_status == "cancelled" else "error",
                            "exit_code": tool_exit_code,
                            "output": tool_output,
                            "compressed": compressed,
                            "job_id": bg_job_id,
                        }

                    # ── 工具 1.3: 内置断点续传下载器 (resumable_download) ──
                    elif func_name == "resumable_download":
                        dl_url = str(args.get("url", "")).strip()
                        raw_save_path = str(args.get("save_path", "")).strip()
                        raw_fallbacks = args.get("fallback_urls")
                        fallback_list: list[str] = []
                        if isinstance(raw_fallbacks, list):
                            fallback_list = [str(u).strip() for u in raw_fallbacks if str(u).strip()]
                        elif isinstance(raw_fallbacks, str) and raw_fallbacks.strip():
                            fallback_list = [raw_fallbacks.strip()]
                        expected_sha = str(args.get("sha256", "")).strip() or None

                        cwd_base = str(ctx.get("current_cwd") or os.getcwd())
                        resolved_save = (
                            str((Path(cwd_base) / raw_save_path).resolve())
                            if raw_save_path and not Path(raw_save_path).is_absolute()
                            else raw_save_path
                        )

                        yield {
                            "type": "tool_start",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"resumable_download('{dl_url}')",
                            "reason": f"断点续传下载至 {resolved_save}",
                            "security": "AUTO_CONFIRMED",
                        }

                        if not dl_url or not resolved_save:
                            tool_output = "Error: resumable_download 必须提供有效的 `url` 与 `save_path` 参数。"
                            tool_exit_code = -1
                            compressed = "[Error: resumable_download 缺少参数]"
                            dl_status = "error"
                            dl_job_id = ""
                        else:
                            dl_job = shell_executor.background_manager.start_download_job(
                                url=dl_url,
                                save_path=resolved_save,
                                cwd=cwd_base,
                                session_id=conv_id,
                                fallback_urls=fallback_list or None,
                                expected_sha256=expected_sha,
                            )
                            dl_res = await shell_executor.background_manager.wait_initial_or_detach(
                                dl_job,
                                observe_seconds=10.0,
                            )
                            tool_output = str(dl_res.get("output") or "")
                            is_dl_bg = bool(dl_res.get("background"))
                            dl_job_id = str(dl_res.get("job_id") or "")
                            tool_exit_code = 0 if is_dl_bg else int(dl_res.get("exit_code", 0) or 0)
                            if is_dl_bg:
                                dl_status = "running"
                                compressed = f"[断点下载已转入后台 job_id={dl_job_id}: `{Path(resolved_save).name}`]"
                            elif tool_exit_code == 0:
                                dl_status = "completed"
                                compressed = f"[断点下载已完成: `{Path(resolved_save).name}`]"
                            else:
                                dl_status = "error"
                                compressed = f"[断点下载失败: `{Path(resolved_save).name}`]"

                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"resumable_download('{dl_url}')",
                            "reason": f"断点续传下载至 {resolved_save}",
                            "status": dl_status,
                            "exit_code": tool_exit_code,
                            "output": tool_output,
                            "compressed": compressed,
                            "job_id": dl_job_id or None,
                        }

                    # ── 工具 1.4: 后台长驻进程托管服务 (start_background_process) ──
                    elif func_name == "start_background_process":
                        bg_cmd = str(args.get("command", "")).strip()
                        bg_wait = int(args.get("wait_seconds", 3) or 3)
                        yield {
                            "type": "tool_start",
                            "call_id": call_id,
                            "name": func_name,
                            "command": bg_cmd,
                            "reason": "启动后台长驻服务/守护进程",
                            "security": "AUTO_CONFIRMED",
                        }

                        # 敏感指令安全审计
                        sec_level = shell_executor.check_security(bg_cmd)
                        if sec_level == "DENY":
                            l2_gate_res: dict[str, Any] = {}
                            async for evt in _eval_and_wait_l2_approval(
                                t_name=func_name,
                                t_args={"command": bg_cmd, "wait_seconds": bg_wait},
                                r_reason=f"后台启动命令包含高危敏感特征: {bg_cmd[:60]}",
                                result_holder=l2_gate_res,
                            ):
                                yield evt

                            if not l2_gate_res.get("allowed", False):
                                deny_reason = f"Denied: 【高危拦截已阻断】{l2_gate_res.get('guidance', '用户拒绝了该项后台长驻操作')}"
                                tool_output = deny_reason
                                yield {
                                    "type": "tool_end",
                                    "call_id": call_id,
                                    "name": func_name,
                                    "command": bg_cmd,
                                    "status": "denied",
                                    "output": tool_output,
                                    "compressed": f"[高危操作阻断 `{bg_cmd[:45]}`]",
                                }
                                bg_cmd = ""

                        if bg_cmd:
                            from app.tools.background_process import start_background_process as bg_start_fn
                            ws_cwd = str(ctx.get("current_cwd") or os.getcwd())
                            bg_res = bg_start_fn(
                                command=bg_cmd,
                                wait_seconds=bg_wait,
                                workspace_id=ws_id,
                                conversation_id=conv_id,
                                cwd=ws_cwd,
                            )
                            is_ok = bool(bg_res.get("success"))
                            tool_status = bg_res.get("status", "running" if is_ok else "error")
                            tool_exit = bg_res.get("exit_code", 0 if is_ok else -1)
                            ports = bg_res.get("port_hints", [])
                            port_str = f", 监听端口: {ports}" if ports else ""
                            tool_output = (
                                f"后台进程启动成功 (ID: {bg_res.get('proc_id')}, PID: {bg_res.get('pid')}{port_str})\n"
                                f"初始输出摘要:\n" + "\n".join(bg_res.get("initial_logs", []))
                                if is_ok else
                                f"后台进程启动失败 ({tool_status}): {bg_res.get('error')}\n" + "\n".join(bg_res.get("diagnostics", []))
                            )
                            compressed = f"[后台进程 `{bg_res.get('proc_id')}` {tool_status}{port_str}]"
                            yield {
                                "type": "tool_end",
                                "call_id": call_id,
                                "name": func_name,
                                "command": bg_cmd,
                                "status": tool_status,
                                "exit_code": tool_exit,
                                "output": tool_output,
                                "compressed": compressed,
                                "proc_id": bg_res.get("proc_id"),
                                "port_hints": ports,
                            }

                    # ── 工具 1.5: 读取后台长驻进程日志 (read_process_logs) ──
                    elif func_name == "read_process_logs":
                        p_id = str(args.get("proc_id", "")).strip()
                        tail_n = int(args.get("tail_lines", 80) or 80)
                        only_err = bool(args.get("only_errors", False))
                        yield {
                            "type": "tool_start",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"read_process_logs('{p_id}')",
                            "reason": f"读取后台进程 {p_id} 日志",
                            "security": "AUTO_CONFIRMED",
                        }
                        from app.tools.background_process import read_process_logs as bg_read_fn
                        log_res = bg_read_fn(proc_id=p_id, tail_lines=tail_n, only_errors=only_err)
                        if log_res.get("success"):
                            log_lines = log_res.get("logs", [])
                            tool_output = "\n".join(log_lines) if log_lines else "(暂无新日志输出)"
                            tool_status = log_res.get("status", "running")
                            tool_exit = log_res.get("exit_code") or 0
                            compressed = f"[{p_id} 日志读取: {len(log_lines)}行, 状态={tool_status}]"
                        else:
                            tool_output = f"Error: {log_res.get('error')}"
                            tool_status = "error"
                            tool_exit = -1
                            compressed = f"[{p_id} 日志读取失败]"

                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"read_process_logs('{p_id}')",
                            "status": tool_status,
                            "exit_code": tool_exit,
                            "output": tool_output,
                            "compressed": compressed,
                        }

                    # ── 工具 1.6: 列出后台长驻进程 (list_background_processes) ──
                    elif func_name == "list_background_processes":
                        yield {
                            "type": "tool_start",
                            "call_id": call_id,
                            "name": func_name,
                            "command": "list_background_processes()",
                            "reason": "列出当前工作区后台进程",
                            "security": "AUTO_CONFIRMED",
                        }
                        from app.tools.background_process import list_background_processes as bg_list_fn
                        list_res = bg_list_fn(workspace_id=ws_id)
                        procs = list_res.get("processes", [])
                        tool_output = json.dumps(procs, ensure_ascii=False, indent=2) if procs else "当前工作区暂无任何运行中的后台托管进程。"
                        compressed = f"[当前活跃托管进程: {len(procs)}个]"
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": "list_background_processes()",
                            "status": "completed",
                            "exit_code": 0,
                            "output": tool_output,
                            "compressed": compressed,
                        }

                    # ── 工具 1.7: 终止后台长驻进程 (stop_background_process) ──
                    elif func_name == "stop_background_process":
                        p_id = str(args.get("proc_id", "")).strip()
                        yield {
                            "type": "tool_start",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"stop_background_process('{p_id}')",
                            "reason": f"终止后台进程 {p_id}",
                            "security": "AUTO_CONFIRMED",
                        }
                        from app.tools.background_process import stop_background_process as bg_stop_fn
                        stop_res = bg_stop_fn(proc_id=p_id)
                        if stop_res.get("success"):
                            tool_output = stop_res.get("message", "进程已终止")
                            tool_status = "killed"
                            tool_exit = 0
                            compressed = f"[已终止后台进程 `{p_id}`]"
                        else:
                            tool_output = f"Error: {stop_res.get('error')}"
                            tool_status = "error"
                            tool_exit = -1
                            compressed = f"[终止后台进程 `{p_id}` 失败]"

                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"stop_background_process('{p_id}')",
                            "status": tool_status,
                            "exit_code": tool_exit,
                            "output": tool_output,
                            "compressed": compressed,
                        }

                    # ── 工具 1.8: 只读并发子智能体派发器 (dispatch_research_subagents) ──
                    elif func_name == "dispatch_research_subagents":
                        raw_tasks = args.get("tasks") or []
                        yield {
                            "type": "tool_start",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"dispatch_research_subagents(tasks_count={len(raw_tasks)})",
                            "reason": f"并发派发 {len(raw_tasks)} 个只读侦察兵子智能体进行代码与架构调研",
                            "security": "SAFE",
                        }
                        from app.tools.sub_agent import dispatch_research_subagents as sub_spawner_fn
                        ws_cwd = str(ctx.get("current_cwd") or os.getcwd())
                        # 嗅探当前模型是否为本地引擎
                        is_local_eng = any(k in str(model_name).lower() for k in ("ollama", "local", "llama", "qwen-local"))
                        sub_res = await sub_spawner_fn(
                            tasks=raw_tasks,
                            workspace_root=ws_cwd,
                            is_local_engine=is_local_eng,
                        )
                        tool_status = "completed" if sub_res.get("success") else "error"
                        tool_output = sub_res.get("summary_markdown", "") or f"Sub-Agent 派发失败: {sub_res.get('error')}"
                        compressed = f"[并发调研完成: {sub_res.get('tasks_count', 0)}任务, 耗时{sub_res.get('total_duration', 0)}s, {sub_res.get('total_tokens', 0)} tokens]"
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"dispatch_research_subagents({len(raw_tasks)} tasks)",
                            "status": tool_status,
                            "exit_code": 0 if sub_res.get("success") else -1,
                            "output": tool_output,
                            "compressed": compressed,
                            "subagent_metrics": {
                                "tasks_count": sub_res.get("tasks_count", 0),
                                "total_tokens": sub_res.get("total_tokens", 0),
                                "duration": sub_res.get("total_duration", 0),
                            },
                        }

                    # ── 工具 1.5: 按需 FTS5 记忆与工作区历史检索 (search_memory_history) ──
                    elif func_name == "search_memory_history":
                        mem_query = str(args.get("query", "")).strip()
                        mem_scope = str(args.get("scope", "all")).strip()
                        mem_limit = int(args.get("limit", 5) or 5)
                        yield {
                            "type": "tool_start",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"search_memory_history('{mem_query}', scope='{mem_scope}')",
                            "reason": f"按需 FTS5 检索历史记忆与工作区轨迹: {mem_query}",
                            "security": "SAFE",
                        }
                        tool_output = await self._execute_search_memory_history(
                            query=mem_query,
                            scope=mem_scope,
                            limit=mem_limit,
                            workspace_id=ws_id,
                            target_cwd=ctx.get("current_cwd") or target_cwd,
                            incognito=active_incognito,
                        )
                        compressed = f"[已按需检索历史记忆 ({mem_scope}): '{mem_query[:40]}']"
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"search_memory_history('{mem_query[:40]}')",
                            "reason": f"按需检索历史记忆: {mem_query}",
                            "status": "completed",
                            "output": tool_output,
                            "compressed": compressed,
                        }

                    # ── 工具 2: 更新任务工作草稿纸 (update_working_memory) ──
                    elif func_name == "update_working_memory":
                        current_wm = dict(ctx.get("working_memory") or {})
                        if args.get("task_completed") is True:
                            current_wm = {}
                            tool_output = "任务标记为已完成，工作记忆草稿纸已归档并清空。"
                            compressed = "[已清空归档任务工作草稿纸]"
                            task_marked_completed = True
                        else:
                            if args.get("goal"):
                                current_wm["goal"] = str(args["goal"])
                            if isinstance(args.get("notes"), dict):
                                existing_notes = current_wm.get("notes")
                                if not isinstance(existing_notes, dict):
                                    existing_notes = {}
                                existing_notes.update(args["notes"])
                                current_wm["notes"] = existing_notes
                            tool_output = f"工作记忆草稿纸已更新: {json.dumps(current_wm, ensure_ascii=False)}"
                            compressed = f"[已更新工作草稿纸: {json.dumps(current_wm, ensure_ascii=False)[:80]}]"

                        ctx["working_memory"] = current_wm
                        await ConversationRepository.update_agent_state(
                            conv_id,
                            working_memory_json=json.dumps(current_wm, ensure_ascii=False),
                        )
                        yield {
                            "type": "scratchpad_update",
                            "working_memory": current_wm,
                        }
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": "update_working_memory",
                            "reason": "更新当前任务关键状态草稿",
                            "status": "completed",
                            "output": tool_output,
                            "compressed": compressed,
                        }

                    # ── 工具 2.5: 直接写入长期记忆库 (save_long_term_memory) ──
                    elif func_name == "save_long_term_memory":
                        cat = str(args.get("category", "preference")).strip()
                        mem_content = str(args.get("content", "")).strip()
                        pinned = bool(args.get("is_pinned", True))
                        if active_incognito:
                            tool_output = "Skipped: 当前处于无痕模式 (incognito=True)，禁止写入长期记忆库。"
                            compressed = "[无痕模式跳过写入长期记忆]"
                        elif not mem_content:
                            tool_output = "Error: 长期记忆内容 content 不能为空。"
                            compressed = "[长期记忆写入失败: 内容为空]"
                        else:
                            safe_mem_content, _ = MemoryPolicyGuard.sanitize_text(mem_content)
                            saved_mem = await long_term_memory.add_memory(
                                category=cat,
                                content=safe_mem_content,
                                confidence=0.98,
                                source="user_explicit",
                                is_pinned=1 if pinned else 0,
                            )
                            tool_output = f"✅ 已成功写入本地 SQLite 长期记忆库 (ID: `{saved_mem['id']}`, 分类: `{cat}`, 置顶: `{pinned}`): {safe_mem_content}"
                            compressed = f"[已植入长期记忆 ({cat}): {safe_mem_content[:45]}...]"
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"save_long_term_memory({cat})",
                            "reason": "持久化写入本地长期记忆库",
                            "status": "completed",
                            "output": tool_output,
                            "compressed": compressed,
                        }

                    # ── 工具 3: 按需加载 Skill 详情 (load_skill_detail) ──
                    elif func_name == "load_skill_detail":
                        skill_name = str(args.get("skill_name", "")).strip()
                        eff_ws_root = ctx.get("current_cwd") or target_cwd
                        tool_output = skill_loader.get_full_skill_markdown(
                            skill_name,
                            workspace_root=eff_ws_root,
                        )
                        clean_sid = skill_name.lstrip("/")
                        if clean_sid and clean_sid not in active_skill_ids and not str(tool_output).startswith("Error:"):
                            active_skill_ids.append(clean_sid)
                        compressed = f"[已加载专业技能 `{skill_name}` 操作规程与四元组清单]"
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"load_skill_detail({skill_name})",
                            "reason": f"读取技能包 {skill_name} 详细指南",
                            "status": "completed",
                            "output": tool_output,
                            "compressed": compressed,
                        }

                    # ── 工具 3.1: FTS5 检索 Skill 领域知识库 (search_skill_knowledge) ──
                    elif func_name == "search_skill_knowledge":
                        sk_name = str(args.get("skill_name", "")).strip()
                        kb_query = str(args.get("query", "")).strip()
                        eff_ws_root = ctx.get("current_cwd") or target_cwd
                        yield {
                            "type": "tool_start",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"search_skill_knowledge({sk_name}, '{kb_query}')",
                            "reason": f"FTS5 毫秒级检索技能 {sk_name} 的领域知识库",
                            "security": "SAFE",
                        }
                        tool_output = await skill_loader.search_knowledge(
                            sk_name,
                            kb_query,
                            workspace_root=eff_ws_root,
                        )
                        clean_sid = sk_name.lstrip("/")
                        if clean_sid and clean_sid not in active_skill_ids:
                            active_skill_ids.append(clean_sid)
                        compressed = f"[已检索 `{sk_name}` 领域知识库: 关键词 '{kb_query}']"
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"search_skill_knowledge({sk_name})",
                            "reason": f"检索技能知识库: {kb_query}",
                            "status": "completed",
                            "output": tool_output,
                            "compressed": compressed,
                        }

                    # ── 工具 3.2: 执行 Skill 随包确定性算子 (run_skill_tool) ──
                    elif func_name == "run_skill_tool":
                        sk_name = str(args.get("skill_name", "")).strip()
                        scr_name = str(args.get("script_name", "")).strip()
                        scr_args = args.get("arguments") if isinstance(args.get("arguments"), dict) else {}
                        eff_ws_root = ctx.get("current_cwd") or target_cwd
                        yield {
                            "type": "tool_start",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"run_skill_tool({sk_name}/tools/{scr_name})",
                            "reason": f"运行技能 {sk_name} 随包专属算子 {scr_name}",
                            "security": "AUTO_CONFIRMED",
                        }
                        tool_output = await skill_loader.execute_skill_tool(
                            sk_name,
                            scr_name,
                            scr_args,
                            workspace_root=eff_ws_root,
                        )
                        clean_sid = sk_name.lstrip("/")
                        if clean_sid and clean_sid not in active_skill_ids:
                            active_skill_ids.append(clean_sid)
                        compressed = f"[已运行随包算子 `{sk_name}/tools/{scr_name}`]"
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"run_skill_tool({sk_name}/{scr_name})",
                            "reason": f"执行技能专属算子 {scr_name}",
                            "status": "completed",
                            "output": tool_output,
                            "compressed": compressed,
                        }

                    # ── 工具: 物理写入本地文件 (write_local_file - 绝不幻觉) ──
                    elif func_name == "write_local_file":
                        raw_path = str(args.get("file_path") or args.get("path") or "").strip()
                        file_content = str(args.get("content", ""))
                        overwrite = bool(args.get("overwrite", True))
                        current_work_dir = Path(ctx.get("current_cwd") or os.getcwd()).resolve()
                        ws_guard_root = Path(target_cwd or current_work_dir).resolve()

                        if not raw_path:
                            tool_output = "Error: 缺少必要参数 `file_path`。"
                            compressed = "[写入失败: 未指定目标路径]"
                            yield {
                                "type": "tool_end",
                                "call_id": call_id,
                                "name": func_name,
                                "command": "write_file()",
                                "reason": "参数缺失拒绝写入",
                                "status": "completed",
                                "output": tool_output,
                                "compressed": compressed,
                                "cwd": str(current_work_dir),
                            }
                        else:
                            target_file = Path(raw_path)
                            if not target_file.is_absolute():
                                target_file = (current_work_dir / target_file).resolve()
                            else:
                                target_file = target_file.resolve()

                            rel_display = str(target_file)
                            try:
                                rel_display = str(target_file.relative_to(current_work_dir))
                            except Exception:
                                pass

                            # 🛡️ 物理只读硬拦截：严禁向工作区用户附件目录写入或伪造 Mock 输入
                            if (
                                attachment_service.is_path_in_attachments_dir(target_file, workspace_root=ws_guard_root)
                                or attachment_service.is_path_in_attachments_dir(target_file, workspace_root=current_work_dir)
                            ):
                                deny_att_msg = (
                                    f"Denied: 安全红线拦截！工作区输入附件目录 (`attachments/` 与 `.agenthub/attachments/`) "
                                    f"为用户原始输入只读区，严禁 Agent 写入、修改或伪造测试文件 (`{rel_display}`)。\n"
                                    "请将输出报告或中间产物写入 `./output/` 目录；若发现预期输入文件缺失，严禁生成 Mock 测试数据替代分析，必须立即向用户如实报告！"
                                )
                                await audit_service.log(
                                    event_type="write_attachments_denied",
                                    actor="agent",
                                    detail={"target_file": str(target_file), "reason": "read_only_attachments_guard"},
                                    approved=False,
                                )
                                tool_output = deny_att_msg
                                compressed = f"[红线拦截写附件目录 `{rel_display}`]"
                                yield {
                                    "type": "tool_end",
                                    "call_id": call_id,
                                    "name": func_name,
                                    "command": f"write_file({rel_display})",
                                    "reason": f"只读附件目录保护拦截 {rel_display}",
                                    "status": "denied",
                                    "output": tool_output,
                                    "compressed": compressed,
                                    "cwd": str(current_work_dir),
                                }
                            elif allowed_write_dirs and not self._is_path_in_allowed_dirs(
                                target_file, allowed_write_dirs, current_work_dir
                            ):
                                tool_output = (
                                    f"Denied: 【目录权限隔离拦截】目标路径 `{rel_display}` 不在当前 Agent 的授权写入目录 "
                                    f"({allowed_write_dirs}) 范围内，已拒绝写入。"
                                )
                                compressed = f"[目录隔离拦截写入 `{rel_display}`]"
                                yield {
                                    "type": "tool_end",
                                    "call_id": call_id,
                                    "name": func_name,
                                    "command": f"write_file({rel_display})",
                                    "reason": "授权写入目录越界拦截",
                                    "status": "denied",
                                    "output": tool_output,
                                    "compressed": compressed,
                                    "cwd": str(current_work_dir),
                                }
                            else:
                                # 🔴 L2 跨工作区越界写检查
                                is_out_of_bounds = False
                                try:
                                    target_file.relative_to(ws_guard_root)
                                except ValueError:
                                    is_out_of_bounds = True

                                write_allowed = True
                                if is_out_of_bounds:
                                    l2_write_res: dict[str, Any] = {}
                                    async for evt in _eval_and_wait_l2_approval(
                                        t_name=func_name,
                                        t_args=args,
                                        r_reason=f"写操作尝试跨越工作区边界: 目标路径 `{target_file}` 不在当前工作区 `{ws_guard_root}` 范围内",
                                        result_holder=l2_write_res,
                                    ):
                                        yield evt

                                    if not l2_write_res.get("allowed", False):
                                        write_allowed = False
                                        deny_msg = f"Denied: 【跨目录越界写已阻断】{l2_write_res.get('guidance', '用户拒绝了跨目录写入')}"
                                        tool_output = deny_msg
                                        compressed = f"[越界写已阻断 `{rel_display}`]"
                                        yield {
                                            "type": "tool_end",
                                            "call_id": call_id,
                                            "name": func_name,
                                            "command": f"write_file({rel_display})",
                                            "reason": "跨目录越界写审批未通过",
                                            "status": "denied",
                                            "output": tool_output,
                                            "compressed": compressed,
                                            "cwd": str(current_work_dir),
                                        }
                                    else:
                                        eff_args = l2_write_res.get("args") or args
                                        args = eff_args
                                        new_raw_path = str(args.get("file_path") or args.get("path") or "").strip()
                                        if new_raw_path:
                                            target_file = Path(new_raw_path)
                                            if not target_file.is_absolute():
                                                target_file = (current_work_dir / target_file).resolve()
                                            else:
                                                target_file = target_file.resolve()
                                            file_content = str(args.get("content", file_content))
                                            overwrite = bool(args.get("overwrite", overwrite))

                                if write_allowed:
                                    # 全自动确认直通写入物理文件，无需用户手动点击确认
                                    yield {
                                        "type": "tool_start",
                                        "call_id": call_id,
                                        "name": func_name,
                                        "command": f"write_file -> {rel_display} ({len(file_content)} 字符)",
                                        "reason": f"真实落盘本地文件 {rel_display}",
                                        "security": "AUTO_CONFIRMED",
                                    }
                                write_snapshot_hash = None
                                existed_before = False
                                edit_file_size = 0
                                edit_time_str = datetime.now().strftime("%H:%M:%S")
                                write_succeeded = False
                                skill_updated_name = ""
                                try:
                                    existed_before = target_file.exists()
                                    if existed_before and not overwrite:
                                        tool_output = f"Error: 文件 `{rel_display}` 已存在且 overwrite=False，取消写入。"
                                        compressed = f"[写入取消: 文件已存在 `{rel_display}`]"
                                    else:
                                        if existed_before:
                                            write_snapshot_hash = create_shadow_snapshot(current_work_dir)
                                            if target_file.name.upper() == "SKILL.MD":
                                                from ..skills.skill_version_service import snapshot_skill_before_write
                                                snapshot_skill_before_write(target_file.parent, source="write_local_file")
                                        target_file.parent.mkdir(parents=True, exist_ok=True)
                                        target_file.write_text(file_content, encoding="utf-8")
                                        write_succeeded = True
                                        self._invalidate_file_read_cache(conv_id, target_file)
                                        try:
                                            st = target_file.stat()
                                            edit_file_size = int(st.st_size)
                                            edit_time_str = datetime.fromtimestamp(st.st_mtime).strftime("%H:%M:%S")
                                        except Exception:
                                            edit_file_size = len(file_content.encode("utf-8"))
                                        hotplug_note = ""
                                        if target_file.name.upper() == "SKILL.MD":
                                            target_file = skill_loader.normalize_skill_file(target_file)
                                            skill_updated_name = target_file.parent.name
                                            hotplug_note = "\n🎯 [SKILL 标准校验与热插拔已激活]: 已自动规范化 YAML Frontmatter 与 kebab-case 命名，前端输入框 `/` 菜单现已可实时调用并查看该技能说明。"
                                        elif target_file.name.lower() == "mcp_servers.json":
                                            created_docs = skill_loader.ensure_mcp_companion_skills()
                                            active_tools, mcp_catalog_prompt = await self._assemble_agent_tools(
                                                conv_id=conv_id,
                                                user_message=user_message,
                                                collapse_anchor_turn_id=int(ctx.get("collapse_anchor_turn_id") or conv_collapse_anchor),
                                            )
                                            doc_tip = f"（已自动补全 {created_docs} 个 MCP 伴生 SKILL 说明文档）" if created_docs else ""
                                            hotplug_note = f"\n🔌 [MCP 热插拔已重载]: 已重新扫描 mcp_servers.json，当前可用工具总数: {len(active_tools)}{doc_tip}。"
                                        tool_output = (
                                            f"✅ 成功物理落盘文件: `{target_file}`\n"
                                            f"文件大小: {edit_file_size} 字节\n"
                                            f"状态: 真实写入磁盘已验证完成。{hotplug_note}"
                                        )
                                        compressed = f"[已真实物理落盘文件 `{rel_display}` ({len(file_content)} 字符)]"

                                        # 自动联动登记到当前工作区资产库
                                        try:
                                            from ..repository.artifact_repository import ArtifactRepository
                                            f_type = ArtifactRepository._detect_file_type(target_file)
                                            await ArtifactRepository.register_artifact(
                                                workspace_id=ws_id,
                                                file_path=str(target_file),
                                                file_name=target_file.name,
                                                file_type=f_type,
                                                file_size=edit_file_size,
                                                conversation_id=conv_id,
                                                source="ai_generated",
                                                summary=f"AI 自主编写落盘: {target_file.name}",
                                            )
                                        except Exception as art_err:
                                            logger.warning(f"资产库落盘自动登记异常: {art_err}")
                                except Exception as write_err:
                                    tool_output = f"Error: 写入文件 `{target_file}` 失败: {write_err}"
                                    compressed = f"[写入文件异常: {str(write_err)[:40]}]"

                                yield {
                                    "type": "tool_end",
                                    "call_id": call_id,
                                    "name": func_name,
                                    "command": f"write_file({rel_display})",
                                    "reason": f"写入本地文件 {rel_display}",
                                    "status": "completed",
                                    "output": tool_output,
                                    "compressed": compressed,
                                    "cwd": str(current_work_dir),
                                    "file_path": rel_display if write_succeeded else "",
                                    "edit_action": ("modified" if existed_before else "created") if write_succeeded else "",
                                    "file_size": edit_file_size if write_succeeded else 0,
                                    "edit_time": edit_time_str if write_succeeded else "",
                                    "snapshot_tree_hash": write_snapshot_hash or "",
                                    "skill_updated": bool(write_succeeded and skill_updated_name),
                                    "skill_name": skill_updated_name if write_succeeded else "",
                                }
                                if write_succeeded and skill_updated_name:
                                    yield {
                                        "type": "skills_updated",
                                        "skill_name": skill_updated_name,
                                        "file_path": rel_display,
                                    }

                    # ── 工具: 精准局部补丁修改 (replace_in_file - 代码手术刀与语法门禁) ──
                    elif func_name == "replace_in_file":
                        raw_path = str(args.get("file_path") or args.get("path") or "").strip()
                        diff_blocks = str(args.get("diff_blocks") or "").strip()
                        current_work_dir = Path(ctx.get("current_cwd") or os.getcwd()).resolve()
                        ws_guard_root = Path(target_cwd or current_work_dir).resolve()

                        if not raw_path or not diff_blocks:
                            tool_output = "Error: 缺少必要参数 `file_path` 或 `diff_blocks`。"
                            compressed = "[局部修改失败: 缺少文件路径或补丁块]"
                            yield {
                                "type": "tool_end",
                                "call_id": call_id,
                                "name": func_name,
                                "command": "replace_in_file()",
                                "reason": "参数缺失拒绝执行局部补丁",
                                "status": "completed",
                                "output": tool_output,
                                "compressed": compressed,
                                "cwd": str(current_work_dir),
                            }
                        else:
                            target_file = Path(raw_path)
                            if not target_file.is_absolute():
                                target_file = (current_work_dir / target_file).resolve()
                            else:
                                target_file = target_file.resolve()

                            rel_display = str(target_file)
                            try:
                                rel_display = str(target_file.relative_to(current_work_dir))
                            except Exception:
                                pass

                            # 只读保护区拦截
                            if (
                                attachment_service.is_path_in_attachments_dir(target_file, workspace_root=ws_guard_root)
                                or attachment_service.is_path_in_attachments_dir(target_file, workspace_root=current_work_dir)
                            ):
                                tool_output = f"Denied: 工作区输入附件目录为只读保护区，禁止修改 `{rel_display}`。"
                                yield {
                                    "type": "tool_end",
                                    "call_id": call_id,
                                    "name": func_name,
                                    "command": f"replace_in_file({rel_display})",
                                    "reason": "只读区安全拦截",
                                    "status": "completed",
                                    "output": tool_output,
                                    "compressed": f"[安全拦截修改附件 `{rel_display}`]",
                                    "cwd": str(current_work_dir),
                                }
                            elif allowed_write_dirs and not self._is_path_in_allowed_dirs(
                                target_file, allowed_write_dirs, current_work_dir
                            ):
                                tool_output = (
                                    f"Denied: 【目录权限隔离拦截】目标路径 `{rel_display}` 不在当前 Agent 的授权写入目录 "
                                    f"({allowed_write_dirs}) 范围内，已拒绝修改。"
                                )
                                yield {
                                    "type": "tool_end",
                                    "call_id": call_id,
                                    "name": func_name,
                                    "command": f"replace_in_file({rel_display})",
                                    "reason": "授权写入目录越界拦截",
                                    "status": "denied",
                                    "output": tool_output,
                                    "compressed": f"[目录隔离拦截修改 `{rel_display}`]",
                                    "cwd": str(current_work_dir),
                                }
                            elif not target_file.exists() or not target_file.is_file():
                                tool_output = (
                                    f"Error: 目标文件不存在: `{target_file}`。\n"
                                    "⚠️ replace_in_file 仅用于修改已有文件；若是新建文件，请调用 write_local_file。"
                                )
                                yield {
                                    "type": "tool_end",
                                    "call_id": call_id,
                                    "name": func_name,
                                    "command": f"replace_in_file({rel_display})",
                                    "reason": "目标文件不存在",
                                    "status": "completed",
                                    "output": tool_output,
                                    "compressed": f"[修改失败: 文件不存在 `{rel_display}`]",
                                    "cwd": str(current_work_dir),
                                }
                            else:
                                yield {
                                    "type": "tool_start",
                                    "call_id": call_id,
                                    "name": func_name,
                                    "command": f"replace_in_file({rel_display})",
                                    "reason": f"精准局部补丁修改 {rel_display}",
                                    "security": "L1",
                                }
                                # 1. 执行写盘前无痕快照
                                snapshot_hash = create_shadow_snapshot(current_work_dir)
                                ok = False
                                syntax_ok = False
                                unified_diff = ""
                                replace_skill_updated_name = ""

                                try:
                                    orig_text = target_file.read_text(encoding="utf-8", errors="replace")
                                    # 2. 应用局部补丁算子 (三级容错)
                                    ok, new_text_or_err, unified_diff = apply_search_replace_blocks(orig_text, diff_blocks)

                                    if not ok:
                                        tool_output = f"Error: 局部补丁应用失败:\n{new_text_or_err}"
                                        compressed = f"[补丁匹配失败: {rel_display}]"
                                    else:
                                        # 3. AST 语法安全门禁校验 (防代码损毁)
                                        syntax_ok, syntax_err = validate_syntax_before_write(target_file.suffix, new_text_or_err)
                                        if not syntax_ok:
                                            tool_output = syntax_err
                                            compressed = f"[AST 门禁拦截: {rel_display} 语法错误]"
                                        else:
                                            # 4. 真正物理落盘（若为 SKILL.md 则先留存历史快照再规范化并失效缓存）
                                            is_skill_md = target_file.name.upper() == "SKILL.MD"
                                            if is_skill_md:
                                                from ..skills.skill_version_service import snapshot_skill_before_write
                                                snapshot_skill_before_write(target_file.parent, source="replace_in_file")
                                            target_file.write_text(new_text_or_err, encoding="utf-8")
                                            self._invalidate_file_read_cache(conv_id, target_file)
                                            if is_skill_md:
                                                target_file = skill_loader.normalize_skill_file(target_file)
                                                replace_skill_updated_name = target_file.parent.name
                                            tool_output = (
                                                f"[成功应用局部补丁到 `{rel_display}`]\n"
                                                f"```diff\n{unified_diff}\n```\n"
                                                f"快照 Hash: {snapshot_hash or 'None'}"
                                            )
                                            compressed = f"[已局部修改 `{rel_display}`: 生成 Diff 共 {len(unified_diff)} 字符]"

                                            # 自动注册资产到工作区资产库
                                            try:
                                                from ..repository.artifact_repository import ArtifactRepository
                                                f_type = ArtifactRepository._detect_file_type(target_file)
                                                await ArtifactRepository.register_artifact(
                                                    workspace_id=ws_id,
                                                    file_path=str(target_file),
                                                    file_name=target_file.name,
                                                    file_type=f_type,
                                                    file_size=len(new_text_or_err.encode("utf-8")),
                                                    conversation_id=conv_id,
                                                    source="ai_generated",
                                                    summary=f"AI 精准局部修改: {target_file.name}",
                                                )
                                            except Exception as art_err:
                                                logger.warning(f"资产库登记异常: {art_err}")
                                except Exception as e:
                                    tool_output = f"Error: 局部修改执行异常: {e}"
                                    compressed = f"[修改异常: {str(e)[:40]}]"

                                replace_file_size = 0
                                replace_edit_time = datetime.now().strftime("%H:%M:%S")
                                if ok and syntax_ok and target_file.exists():
                                    try:
                                        st = target_file.stat()
                                        replace_file_size = int(st.st_size)
                                        replace_edit_time = datetime.fromtimestamp(st.st_mtime).strftime("%H:%M:%S")
                                    except Exception:
                                        replace_file_size = 0

                                yield {
                                    "type": "tool_end",
                                    "call_id": call_id,
                                    "name": func_name,
                                    "command": f"replace_in_file({rel_display})",
                                    "reason": f"局部修改 {rel_display}",
                                    "status": "completed",
                                    "output": tool_output,
                                    "compressed": compressed,
                                    "cwd": str(current_work_dir),
                                    "file_path": rel_display,
                                    "edit_action": "modified" if (ok and syntax_ok) else "",
                                    "file_size": replace_file_size,
                                    "edit_time": replace_edit_time,
                                    "unified_diff": unified_diff if (ok and syntax_ok) else "",
                                    "snapshot_tree_hash": snapshot_hash or "",
                                    "skill_updated": bool(ok and syntax_ok and replace_skill_updated_name),
                                    "skill_name": replace_skill_updated_name if (ok and syntax_ok) else "",
                                }
                                if ok and syntax_ok and replace_skill_updated_name:
                                    yield {
                                        "type": "skills_updated",
                                        "skill_name": replace_skill_updated_name,
                                        "file_path": rel_display,
                                    }

                    # ── 工具: AST 代码骨架大纲提取 (get_repo_skeleton - 防盲读爆Token) ──
                    elif func_name == "get_repo_skeleton":
                        target_arg = str(args.get("target_path") or args.get("path") or ".").strip()
                        current_work_dir = Path(ctx.get("current_cwd") or os.getcwd()).resolve()

                        yield {
                            "type": "tool_start",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"get_repo_skeleton({target_arg})",
                            "reason": f"提取目标代码大纲与函数签名: {target_arg}",
                            "security": "SAFE",
                        }
                        skeleton_res = get_workspace_skeleton(current_work_dir, target_arg)
                        if skeleton_res["status"] == "success":
                            tool_output = str(skeleton_res.get("skeleton") or "")
                            scanned = skeleton_res.get("scanned_files", 1)
                            compressed = f"[成功提取骨架: {target_arg} (包含 {scanned} 个文件大纲)]"
                        else:
                            tool_output = f"Error: 提取骨架失败: {skeleton_res.get('error', '未知错误')}"
                            compressed = f"[骨架提取失败: {target_arg}]"

                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"get_repo_skeleton({target_arg})",
                            "reason": f"代码骨架大纲: {target_arg}",
                            "status": "completed",
                            "output": tool_output,
                            "compressed": compressed,
                            "cwd": str(current_work_dir),
                        }

                    # ── 工具: 物理读取本地文件 (read_local_file - 原生支持文本与 Office 结构化解析) ──
                    elif func_name == "read_local_file":
                        raw_path = str(args.get("file_path") or args.get("path") or "").strip()
                        current_work_dir = Path(ctx.get("current_cwd") or os.getcwd()).resolve()
                        ws_search_root = Path(target_cwd or current_work_dir).resolve()

                        if not raw_path:
                            tool_output = "Error: 缺少必要参数 `file_path`。"
                            compressed = "[读取失败: 未指定目标路径]"
                            yield {
                                "type": "tool_end",
                                "call_id": call_id,
                                "name": func_name,
                                "command": "read_file()",
                                "reason": "缺少文件路径参数",
                                "status": "completed",
                                "output": tool_output,
                                "compressed": compressed,
                            }
                        else:
                            target_file = Path(raw_path)
                            if not target_file.is_absolute():
                                target_file = (current_work_dir / target_file).resolve()

                            if not target_file.exists() or not target_file.is_file():
                                # 回退检索：依次尝试工作区沙盒附件目录与项目底座根目录
                                proj_root = Path(__file__).resolve().parent.parent.parent.parent
                                pure_name = Path(raw_path).name
                                fallback_candidates = [
                                    (ws_search_root / "attachments" / pure_name).resolve(),
                                    (ws_search_root / ".agenthub" / "attachments" / pure_name).resolve(),
                                    (current_work_dir / "attachments" / pure_name).resolve(),
                                    (current_work_dir / ".agenthub" / "attachments" / pure_name).resolve(),
                                    (proj_root / raw_path).resolve(),
                                ]
                                for cand in fallback_candidates:
                                    if cand.exists() and cand.is_file():
                                        target_file = cand
                                        break

                            rel_display = str(target_file)
                            try:
                                rel_display = str(target_file.relative_to(current_work_dir))
                            except Exception:
                                pass

                            yield {
                                "type": "tool_start",
                                "call_id": call_id,
                                "name": func_name,
                                "command": f"read_file({rel_display})",
                                "reason": f"读取本地文件内容: {rel_display}",
                                "security": "SAFE",
                            }
                            if not target_file.exists() or not target_file.is_file():
                                sandbox_atts = attachment_service.list_workspace_sandbox_attachments(ws_search_root, limit=10)
                                if sandbox_atts:
                                    att_hint = "\n当前工作区沙盒真实存在的附件:\n" + "\n".join(
                                        f"- `{item['rel_path']}` ({item['size_str']}, 绝对路径: `{item['file_path']}`)"
                                        for item in sandbox_atts
                                    )
                                else:
                                    att_hint = "\n当前工作区附件目录暂为空。"
                                tool_output = (
                                    f"Error: 目标文件不存在: `{target_file}`{att_hint}\n"
                                    "⚠️ 严禁在文件不存在时自行创建 Mock 或模拟数据替代！请核对上述清单或向用户报错。"
                                )
                                compressed = f"[读取失败: 文件不存在 `{rel_display}`]"
                            else:
                                try:
                                    ext, category, canonical_mime = attachment_service.classify_extension(target_file.name)
                                    is_office_doc = category == "office" or ext in (".xlsx", ".docx", ".pptx", ".pdf")
                                    start_l = args.get("start_line")
                                    end_l = args.get("end_line")
                                    if not is_office_doc and (start_l is not None or end_l is not None):
                                        s_num = int(start_l) if start_l is not None else 1
                                        e_num = int(end_l) if end_l is not None else 250
                                        cache_slice_key = (s_num, e_num)
                                    else:
                                        cache_slice_key = (None, None)

                                    abs_read_path = str(target_file.resolve())
                                    read_cache_key = (abs_read_path, cache_slice_key[0], cache_slice_key[1])
                                    force_refresh_raw = args.get("force_refresh", False)
                                    force_refresh = (
                                        force_refresh_raw.strip().lower() in ("true", "1", "yes")
                                        if isinstance(force_refresh_raw, str)
                                        else bool(force_refresh_raw)
                                    )
                                    stat_res = target_file.stat()
                                    cur_mtime_ns = int(stat_res.st_mtime_ns)
                                    cur_size = int(stat_res.st_size)
                                    effective_collapse_anchor = int(
                                        ctx.get("collapse_anchor_turn_id") or conv_collapse_anchor or 0
                                    )
                                    # 清理已随分块坍缩归档出热区的过期读缓存
                                    stale_read_keys = [
                                        ck for ck, cv in session_file_read_cache.items()
                                        if cv[2] <= effective_collapse_anchor
                                    ]
                                    for sk in stale_read_keys:
                                        session_file_read_cache.pop(sk, None)

                                    cached_entry = session_file_read_cache.get(read_cache_key)
                                    if (
                                        not force_refresh
                                        and cached_entry is not None
                                        and cached_entry[0] == cur_mtime_ns
                                        and cached_entry[1] == cur_size
                                        and cached_entry[2] > effective_collapse_anchor
                                    ):
                                        cached_step_ref = cached_entry[3]
                                        cached_line_count = cached_entry[4]
                                        tool_output = (
                                            f"[文件未修改：该片段与 {cached_step_ref} 读取的完全一致（共 {cached_line_count} 行），"
                                            f"请直接复用上文内容；若确需重看请传 force_refresh=true]"
                                        )
                                        compressed = f"[命中会话读缓存 `{rel_display}` ({cached_step_ref}, 共 {cached_line_count} 行)]"
                                    elif is_office_doc:
                                        ctx_info = attachment_service.extract_attachment_context(
                                            target_file,
                                            canonical_mime,
                                            target_file.name,
                                        )
                                        preview_txt = ctx_info.get("preview_text") or "(未提取到可读内容)"
                                        tool_output = (
                                            f"【结构化文档读取: `{target_file.name}` (分类: {category}, 引擎: {ctx_info.get('extractor')})】\n"
                                            f"物理路径: `{target_file}`\n\n"
                                            f"{preview_txt}"
                                        )
                                        compressed = f"[已结构化读取文档 `{rel_display}` ({ctx_info.get('extractor')}): 共 {len(tool_output)} 字符]"
                                        session_file_read_cache[read_cache_key] = (
                                            cur_mtime_ns,
                                            cur_size,
                                            current_turn_id,
                                            f"Turn {current_turn_id} Step {step_idx + 1}",
                                            len(tool_output.splitlines()),
                                        )
                                    else:
                                        # 针对纯文本/源码文件，支持行号切片读取
                                        if cache_slice_key[0] is not None or cache_slice_key[1] is not None:
                                            sliced_res = read_local_file_sliced(
                                                current_work_dir,
                                                str(target_file),
                                                start_line=cache_slice_key[0],
                                                end_line=cache_slice_key[1],
                                            )
                                            if sliced_res["status"] == "success":
                                                tool_output = str(sliced_res.get("content") or "")
                                                compressed = f"[切片读取 `{rel_display}` (L{sliced_res['shown_range'][0]}-L{sliced_res['shown_range'][1]} / 共 {sliced_res['total_lines']} 行)]"
                                                session_file_read_cache[read_cache_key] = (
                                                    cur_mtime_ns,
                                                    cur_size,
                                                    current_turn_id,
                                                    f"Turn {current_turn_id} Step {step_idx + 1}",
                                                    int(sliced_res.get("total_lines") or len(tool_output.splitlines())),
                                                )
                                            else:
                                                tool_output = f"Error: {sliced_res.get('error', '切片读取失败')}"
                                                compressed = f"[切片读取失败: {rel_display}]"
                                        else:
                                            raw_bytes = target_file.read_bytes()
                                            content_text = attachment_service.decode_text_bytes(raw_bytes)
                                            total_raw_lines = len(content_text.splitlines())
                                            if len(content_text) > 120000:
                                                content_text = (
                                                    content_text[:80000]
                                                    + f"\n\n... [文件过长，中间已省略 {len(content_text) - 100000} 字符；建议用脚本分段处理] ...\n\n"
                                                    + content_text[-20000:]
                                                )
                                            tool_output = content_text
                                            compressed = f"[成功读取文件 `{rel_display}`: 共 {len(content_text)} 字符]"
                                            session_file_read_cache[read_cache_key] = (
                                                cur_mtime_ns,
                                                cur_size,
                                                current_turn_id,
                                                f"Turn {current_turn_id} Step {step_idx + 1}",
                                                total_raw_lines,
                                            )
                                except Exception as read_err:
                                    tool_output = f"Error: 读取文件失败: {read_err}"
                                    compressed = f"[读取文件异常: {str(read_err)[:40]}]"

                            yield {
                                "type": "tool_end",
                                "call_id": call_id,
                                "name": func_name,
                                "command": f"read_file({rel_display})",
                                "reason": f"读取本地文件 {rel_display}",
                                "status": "completed",
                                "output": tool_output,
                                "compressed": compressed,
                            }

                    # ── 工具: 实时网络正文抓取与微服务健康探测 (web_fetch) ──
                    elif func_name == "web_fetch":
                        target_url = str(args.get("url", "")).strip()
                        extract_main = bool(args.get("extract_main_text", True))
                        from ..extensions.web_tools import web_tools

                        yield {
                            "type": "tool_start",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"web_fetch({target_url})",
                            "reason": f"访问并提纯目标网页/探测服务: {target_url}",
                            "security": "SAFE",
                        }
                        fetch_res = await web_tools.fetch_url(target_url, extract_main_text=extract_main)
                        tool_output = str(fetch_res.get("content") or "")
                        st_code = fetch_res["status_code"]
                        compressed = f"[已抓取 `{target_url[:45]}`: HTTP {st_code}, 共 {len(tool_output)} 字符]"

                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"web_fetch({target_url})",
                            "reason": f"抓取网络正文: {target_url}",
                            "status": "completed",
                            "output": tool_output,
                            "compressed": compressed,
                        }

                    # ── 工具: Windows UI 自动化操作 (ui_action) ──
                    elif func_name == "ui_action":
                        act = str(args.get("action", "list")).strip().lower()
                        target_win = str(args.get("target_window", "")).strip()
                        selector = str(args.get("control_selector", "")).strip()
                        val = str(args.get("value", ""))

                        is_read_only = act in ["list", "inspect"]
                        sec_level = "SAFE" if is_read_only else "AUTO_CONFIRMED"

                        yield {
                            "type": "tool_start",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"ui_action({act}, target='{target_win}', selector='{selector}')",
                            "reason": f"Windows 桌面 UI 操作: {act}",
                            "security": sec_level,
                        }

                        if not is_read_only:
                            await audit_service.log(
                                event_type=f"ui_{act.lower()}",
                                actor="agent",
                                detail={
                                    "action": act,
                                    "window": target_win,
                                    "selector": selector,
                                    "value": val,
                                    "security_level": sec_level,
                                },
                                approved=True,
                            )

                        if act == "list":
                            wins = ui_operator.list_windows(pattern=target_win)
                            if not wins:
                                tool_output = f"当前未找到匹配 '{target_win}' 的可见窗口。"
                            else:
                                win_lines = [f"| HWND | 标题 | 窗口类名 |", "| :--- | :--- | :--- |"]
                                for w in wins:
                                    win_lines.append(f"| {w['hwnd']} | {w['title']} | `{w['class_name']}` |")
                                tool_output = "### 🖥️ 当前可见桌面窗口列表：\n" + "\n".join(win_lines)
                            compressed = f"[已枚举可见窗口: 共 {len(wins)} 个]"

                        elif act == "inspect":
                            tool_output = ui_operator.inspect_window(target_win)
                            compressed = f"[已分析窗口 `{target_win}` 控件树: {len(tool_output.splitlines())} 行]"

                        elif act in ["click", "input", "focus"]:
                            action_res = ui_operator.perform_action(
                                target=target_win,
                                control_selector=selector,
                                action=act,
                                value=val,
                                press_enter=bool(args.get("press_enter", False)),
                            )
                            tool_output = json.dumps(action_res, ensure_ascii=False, indent=2)
                            st_txt = "成功" if action_res.get("success") else "失败"
                            compressed = f"[UI {act} {st_txt}: {action_res.get('message') or action_res.get('error')}]"

                        else:
                            tool_output = f"Error: 不支持的 UI action: '{act}'。有效选项为 list, inspect, click, input, focus。"
                            compressed = f"[UI 操作参数错误: {act}]"

                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"ui_action({act})",
                            "reason": f"桌面 UI 操作: {act}",
                            "status": "completed",
                            "output": tool_output,
                            "compressed": compressed,
                        }

                    # ── 工具 3.5: 按行切片回查超长工具原文引用 (read_tool_ref) ──
                    elif func_name == "read_tool_ref":
                        target_ref_id = str(args.get("ref_id") or "").strip()
                        s_line = int(args.get("start_line") or 1)
                        e_line = int(args.get("end_line") or (s_line + 119))
                        kw = str(args.get("keyword") or "").strip() or None
                        yield {
                            "type": "tool_start",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"read_tool_ref({target_ref_id}, lines={s_line}..{e_line})",
                            "reason": f"按需切片回查脱水工具原文引用 {target_ref_id}",
                            "security": "SAFE",
                        }
                        if not target_ref_id:
                            tool_output = "Error: 缺少必要参数 `ref_id`。"
                            compressed = "[回查引用失败: 缺少 ref_id]"
                        else:
                            slice_res = None
                            if active_incognito:
                                slice_res = read_ephemeral_tool_ref(
                                    target_ref_id,
                                    start_line=s_line,
                                    end_line=e_line,
                                    keyword=kw,
                                )
                            if slice_res is None:
                                slice_res = await ToolRefRepository.read_slice(
                                    ref_id=target_ref_id,
                                    start_line=s_line,
                                    end_line=e_line,
                                    keyword=kw,
                                    conversation_id=conv_id,
                                )
                            if slice_res.get("found"):
                                header = (
                                    f"[引用原文切片 | Ref: {target_ref_id} | 工具: {slice_res.get('tool_name', '')} | "
                                    f"范围: L{slice_res.get('returned_start', 0)}~L{slice_res.get('returned_end', 0)} / 共 {slice_res.get('total_lines', 0)} 行]"
                                )
                                tool_output = f"{header}\n{slice_res.get('content', '')}"
                                compressed = f"[已切片回查 `{target_ref_id}` L{slice_res.get('returned_start', 0)}~L{slice_res.get('returned_end', 0)}]"
                            else:
                                tool_output = f"Error: {slice_res.get('error', '未找到引用记录')}"
                                compressed = f"[引用回查未命中 `{target_ref_id}`]"
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"read_tool_ref({target_ref_id})",
                            "reason": f"回查引用 {target_ref_id}",
                            "status": "completed",
                            "output": tool_output,
                            "compressed": compressed,
                        }

                    # ── 工具 3.9: MCP 工具按需发现与动态激活 (discover_mcp_tools) ──
                    elif func_name == "discover_mcp_tools":
                        raw_servers = args.get("server_names")
                        if isinstance(raw_servers, str):
                            req_servers = [s.strip() for s in raw_servers.split(",") if s.strip()]
                        elif isinstance(raw_servers, list):
                            req_servers = [str(s).strip() for s in raw_servers if str(s).strip()]
                        else:
                            req_servers = []
                        yield {
                            "type": "tool_start",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"discover_mcp_tools({req_servers})",
                            "reason": "按需加载 MCP 服务完整工具 Schema",
                            "security": "READ_ONLY",
                        }
                        if not req_servers:
                            tool_output = "Error: 请提供至少一个有效的 MCP 服务名称（server_names）。"
                            compressed = "[discover_mcp_tools 参数缺失]"
                        else:
                            discovered_tools = await mcp_manager.get_tools_by_servers(req_servers)
                            active_tools, mcp_catalog_prompt = await self._assemble_agent_tools(
                                conv_id=conv_id,
                                user_message=user_message,
                                collapse_anchor_turn_id=int(ctx.get("collapse_anchor_turn_id") or conv_collapse_anchor),
                                extra_activate_servers=req_servers,
                            )
                            if discovered_tools:
                                tool_output = (
                                    f"已成功激活并加载以下 MCP 服务工具 Schema（{len(discovered_tools)} 个工具）：\n"
                                    + json.dumps(discovered_tools, ensure_ascii=False, indent=2)
                                )
                                compressed = f"[已按需挂载 {len(discovered_tools)} 个 MCP 工具 Schema]"
                            else:
                                catalog_info = await mcp_manager.get_mcp_catalog_summary()
                                avail = sorted((catalog_info.get("servers") or {}).keys())
                                tool_output = f"未找到已启用的 MCP 服务 {req_servers}。当前可用服务: {avail}"
                                compressed = "[discover_mcp_tools 未匹配到可用服务]"
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"discover_mcp_tools({req_servers})",
                            "reason": "按需加载 MCP 服务完整工具 Schema",
                            "status": "completed",
                            "output": tool_output,
                            "compressed": compressed,
                        }

                    # ── 工具 4: MCP 扩展工具 (mcp__<server>__<tool>) ──
                    elif func_name.startswith("mcp__"):
                        yield {
                            "type": "tool_start",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"{func_name}({json.dumps(args, ensure_ascii=False)})",
                            "reason": "调用 MCP 外部扩展工具",
                            "security": "MCP",
                        }
                        tool_output = await mcp_manager.call_mcp_tool(func_name, args)
                        is_err = tool_output.startswith("Error:")
                        # 直调合法 mcp__srv__tool 放行后，顺便将该 srv 纳入当前会话单调递增激活集合
                        mcp_parts = func_name.split("__", 2)
                        if len(mcp_parts) == 3 and not is_err:
                            active_tools, mcp_catalog_prompt = await self._assemble_agent_tools(
                                conv_id=conv_id,
                                user_message=user_message,
                                collapse_anchor_turn_id=int(ctx.get("collapse_anchor_turn_id") or conv_collapse_anchor),
                                extra_activate_servers=[mcp_parts[1]],
                            )
                        compressed = shell_executor.compress_output(
                            command=f"{func_name}({json.dumps(args, ensure_ascii=False)[:30]})",
                            raw_output=tool_output,
                            exit_code=1 if is_err else 0,
                        )
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": func_name,
                            "reason": "调用 MCP 外部扩展工具",
                            "status": "error" if is_err else "completed",
                            "output": tool_output,
                            "compressed": compressed,
                        }

                    # ── 工具 4.1: 安全上传工作区沙盒文件至远程 MCP ──
                    elif func_name == "mcp_upload_workspace_file":
                        srv_name = str(args.get("server_name") or "").strip()
                        raw_local_fp = str(args.get("local_file_path") or "").strip()
                        current_work_dir = Path(ctx.get("current_cwd") or os.getcwd()).resolve()
                        ws_guard_root = Path(target_cwd or current_work_dir).resolve()

                        yield {
                            "type": "tool_start",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"mcp_upload({srv_name}: {raw_local_fp})",
                            "reason": f"安全流式上传工作区文件至远程 MCP [{srv_name}]",
                            "security": "SAFE",
                        }
                        tool_output, bridge_meta = await upload_workspace_file_to_mcp(
                            server_name=srv_name,
                            local_file_path=raw_local_fp,
                            guard_root=ws_guard_root,
                            current_cwd=current_work_dir,
                        )
                        bridge_status = str(bridge_meta.get("status") or "error")
                        if bridge_status == "denied":
                            await audit_service.log(
                                event_type="mcp_file_upload_denied",
                                actor="agent",
                                detail={"server_name": srv_name, "local_file_path": raw_local_fp, "reason": tool_output[:200]},
                                approved=False,
                            )
                            compressed = f"[安全拦截上传 `{raw_local_fp}` -> `{srv_name}`]"
                        elif bridge_status == "completed":
                            compressed = f"[已上传 `{bridge_meta.get('file_name', raw_local_fp)}` 至 MCP `{srv_name}`]"
                        else:
                            compressed = f"[上传至 MCP `{srv_name}` 失败]"

                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"mcp_upload({srv_name}: {raw_local_fp})",
                            "reason": f"上传工作区文件至 MCP [{srv_name}]",
                            "status": bridge_status,
                            "output": tool_output,
                            "compressed": compressed,
                        }

                    # ── 工具 4.2: 安全下载远程 MCP 产物至工作区输出目录 ──
                    elif func_name == "mcp_download_artifact_to_workspace":
                        srv_name = str(args.get("server_name") or "").strip()
                        dl_url = str(args.get("artifact_url") or "").strip()
                        save_rel = str(args.get("save_rel_path") or "").strip()
                        current_work_dir = Path(ctx.get("current_cwd") or os.getcwd()).resolve()
                        ws_guard_root = Path(target_cwd or current_work_dir).resolve()

                        yield {
                            "type": "tool_start",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"mcp_download({srv_name} -> {save_rel})",
                            "reason": f"安全下载远程 MCP 产物至工作区 `{save_rel}`",
                            "security": "AUTO_CONFIRMED",
                        }
                        tool_output, bridge_meta = await download_mcp_artifact_to_workspace(
                            server_name=srv_name,
                            artifact_url=dl_url,
                            save_rel_path=save_rel,
                            guard_root=ws_guard_root,
                            current_cwd=current_work_dir,
                        )
                        bridge_status = str(bridge_meta.get("status") or "error")
                        downloaded_abs = str(bridge_meta.get("abs_path") or "")
                        downloaded_rel = str(bridge_meta.get("rel_path") or save_rel)

                        if bridge_status == "denied":
                            await audit_service.log(
                                event_type="mcp_artifact_download_denied",
                                actor="agent",
                                detail={"server_name": srv_name, "artifact_url": dl_url[:240], "save_rel_path": save_rel},
                                approved=False,
                            )
                            compressed = f"[安全拦截下载远程产物 `{save_rel}`]"
                        elif bridge_status == "completed" and downloaded_abs:
                            downloaded_size = int(bridge_meta.get("file_size") or 0)
                            downloaded_name = str(bridge_meta.get("file_name") or Path(downloaded_abs).name)
                            try:
                                from ..repository.artifact_repository import ArtifactRepository
                                f_type = ArtifactRepository._detect_file_type(Path(downloaded_abs))
                                await ArtifactRepository.register_artifact(
                                    workspace_id=ws_id,
                                    file_path=downloaded_abs,
                                    file_name=downloaded_name,
                                    file_type=f_type,
                                    file_size=downloaded_size,
                                    conversation_id=conv_id,
                                    source="mcp_generated",
                                    summary=f"远程 MCP [{srv_name}] 产物落盘: {downloaded_name}",
                                )
                            except Exception as art_err:
                                logger.warning(f"MCP 产物资产库登记异常: {art_err}")
                            compressed = f"[已安全下载 MCP 产物 `{downloaded_rel}` ({downloaded_size} 字节)]"
                        else:
                            compressed = f"[下载 MCP 产物 `{save_rel}` 失败]"

                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"mcp_download({downloaded_rel})",
                            "reason": f"下载远程 MCP 产物 {downloaded_rel}",
                            "status": bridge_status,
                            "output": tool_output,
                            "compressed": compressed,
                            "file_path": downloaded_rel if bridge_status == "completed" else "",
                            "edit_action": "created" if bridge_status == "completed" else "",
                            "file_size": int(bridge_meta.get("file_size") or 0) if bridge_status == "completed" else 0,
                        }

                    # ── 工具: 本地工作区混合检索 (workspace_hybrid_search) ──
                    elif func_name == "workspace_hybrid_search":
                        from ..tools.workspace_fts import workspace_hybrid_search
                        q_str = str(args.get("query") or "").strip()
                        top_k_val = int(args.get("top_k") or 8)
                        pod_id_val = str(args.get("pod_id") or "global")
                        inc_arch = bool(args.get("include_archived", False))
                        ws_root = target_cwd or "."

                        res_text = await workspace_hybrid_search(
                            workspace_root=ws_root,
                            query=q_str,
                            top_k=top_k_val,
                            pod_id=pod_id_val,
                            include_archived=inc_arch,
                        )
                        if res_text and "未在工作区找到" not in res_text:
                            retrieved_evidence_chunks.append({
                                "source_uri": f"workspace://{q_str}",
                                "content_text": res_text[:1200],
                                "authority_tier": 2,
                                "score": 0.85,
                            })
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"workspace_hybrid_search({q_str})",
                            "reason": f"工作区混合语义检索: {q_str}",
                            "status": "completed",
                            "output": res_text,
                            "compressed": f"[混合检索完成: 关键词 '{q_str}']",
                        }

                    # ── 工具: 云端 LLM Wiki 规程检索 (search_cloud_wiki) ──
                    elif func_name == "search_cloud_wiki":
                        from ..tools.workspace_fts import search_cloud_wiki
                        q_str = str(args.get("query") or "").strip()
                        top_k_val = int(args.get("top_k") or 6)
                        pod_id_val = str(args.get("pod_id") or "global")
                        ws_root = target_cwd or "."

                        res_text = await search_cloud_wiki(
                            workspace_root=ws_root,
                            query=q_str,
                            top_k=top_k_val,
                            pod_id=pod_id_val,
                        )
                        if res_text and "未检索到" not in res_text:
                            retrieved_evidence_chunks.append({
                                "source_uri": f"cloud_wiki://{q_str}",
                                "content_text": res_text[:1200],
                                "authority_tier": 1,
                                "score": 0.95,
                            })
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"search_cloud_wiki({q_str})",
                            "reason": f"云端知识库规程检索: {q_str}",
                            "status": "completed",
                            "output": res_text,
                            "compressed": f"[云端规程检索完成: '{q_str}']",
                        }

                    # ── 工具: 云端章节精读 (read_cloud_wiki_section) ──
                    elif func_name == "read_cloud_wiki_section":
                        from ..tools.workspace_fts import read_cloud_wiki_section
                        r_uri = str(args.get("remote_uri") or "").strip()
                        s_path = str(args.get("section_path") or "").strip()
                        ws_root = target_cwd or "."

                        res_text = await read_cloud_wiki_section(
                            remote_uri=r_uri,
                            section_path=s_path,
                            workspace_root=ws_root,
                        )
                        if res_text and "读取云端切片失败" not in res_text:
                            retrieved_evidence_chunks.append({
                                "source_uri": r_uri or s_path,
                                "content_text": res_text[:1200],
                                "authority_tier": 1,
                                "score": 0.98,
                            })
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"read_cloud_wiki_section({r_uri} § {s_path})",
                            "reason": f"精读云端章节: {s_path}",
                            "status": "completed",
                            "output": res_text,
                            "compressed": f"[云端章节精读: {s_path}]",
                        }

                    # ── 工具: 知识图谱关系推理 (query_knowledge_graph) ──
                    elif func_name == "query_knowledge_graph":
                        from ..tools.workspace_fts import query_knowledge_graph
                        target_ent = str(args.get("entity") or "").strip()
                        hops = int(args.get("max_hops") or 2)
                        pod_id_val = str(args.get("pod_id") or "global")
                        ws_root = target_cwd or "."

                        res_text = query_knowledge_graph(
                            workspace_root=ws_root,
                            entity=target_ent,
                            max_hops=hops,
                            pod_id=pod_id_val,
                        )
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"query_knowledge_graph({target_ent}, hops={hops})",
                            "reason": f"查询知识图谱实体关系: {target_ent}",
                            "status": "completed",
                            "output": res_text,
                            "compressed": f"[知识图谱查询完成: '{target_ent}']",
                        }

                    # ── 工具: 离线语音转写 (transcribe_audio_file) ──
                    elif func_name == "transcribe_audio_file":
                        from ..multimodal.audio_transcriber import AudioTranscriber
                        a_path = str(args.get("audio_path") or "").strip()
                        a_lang = str(args.get("language") or "zh").strip()
                        try:
                            t_res = AudioTranscriber.transcribe(audio_file_path=a_path, language=a_lang)
                            res_text = f"### 🎙️ 语音转写结果 ({t_res.get('file_name')}):\n\n{t_res.get('text', '')}"
                        except Exception as a_err:
                            res_text = f"语音转写失败: {str(a_err)}"
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"transcribe_audio_file({a_path})",
                            "reason": f"转写音频文件: {a_path}",
                            "status": "completed",
                            "output": res_text,
                            "compressed": f"[音频转写完成: '{a_path}']",
                        }

                    # ── 工具: 浏览器多模态页面审查 (browser_inspect_page) ──
                    elif func_name == "browser_inspect_page":
                        from ..tools.browser_agent import browser_inspect_page
                        t_url = str(args.get("url") or "").strip()
                        w_sec = float(args.get("wait_seconds") or 2.0)
                        res_text = await browser_inspect_page(url=t_url, wait_seconds=w_sec)
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"browser_inspect_page({t_url})",
                            "reason": f"无头浏览器页面审查: {t_url}",
                            "status": "completed",
                            "output": res_text,
                            "compressed": f"[浏览器审查完成: '{t_url}']",
                        }

                    # ── 工具: 浏览器拟人化交互操作 (browser_interact_action) ──
                    elif func_name == "browser_interact_action":
                        from ..tools.browser_agent import browser_interact_action
                        b_act = str(args.get("action") or "").strip()
                        b_tgt = str(args.get("target") or "").strip()
                        b_txt = str(args.get("text") or "").strip()
                        res_text = await browser_interact_action(action=b_act, target=b_tgt, text=b_txt)
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"browser_interact_action({b_act} on {b_tgt})",
                            "reason": f"浏览器交互操作: {b_act}",
                            "status": "completed",
                            "output": res_text,
                            "compressed": f"[浏览器操作完成: {b_act}]",
                        }

                    # ── Swarm 四原语执行分支 (swarm_control / swarm_post / swarm_read / swarm_wait) ──
                    elif func_name == "swarm_control":
                        from .swarm_manager import swarm_manager
                        from ..repository.swarm_repository import SwarmRepository

                        action = str(args.get("action") or "").strip().lower()
                        eff_swarm_id = swarm_id
                        if not eff_swarm_id:
                            sw_obj = await swarm_manager.get_or_create_swarm(
                                session_id=conv_id,
                                workspace_id=ws_id,
                                cwd=target_cwd,
                                default_model=model_name,
                            )
                            eff_swarm_id = sw_obj["id"]
                            swarm_id = eff_swarm_id

                        if swarm_role == "worker":
                            tool_output = "Denied: Worker 成员无权调用 `swarm_control` 管理蜂群拓扑。"
                            compressed = "[Swarm 权限拦截: worker 禁止调用 swarm_control]"
                        elif action == "add":
                            m_name = str(args.get("name") or "").strip()
                            m_brief = str(args.get("brief") or "").strip()
                            m_agent_id = str(args.get("agent_id") or "").strip() or None
                            m_model = str(args.get("model") or "").strip() or None
                            if not m_name:
                                tool_output = "Error: `swarm_control(action='add')` 缺少参数 `name`。"
                            else:
                                mem = await swarm_manager.add_member(
                                    eff_swarm_id,
                                    name=m_name,
                                    brief=m_brief,
                                    agent_id=m_agent_id,
                                    model=m_model,
                                    workspace_id=ws_id,
                                    cwd=target_cwd,
                                )
                                tool_output = json.dumps({"status": "added", "swarm_id": eff_swarm_id, "member": mem}, ensure_ascii=False)
                            compressed = f"[Swarm 添加成员 `{m_name}`]"
                        elif action == "status":
                            members = await SwarmRepository.list_members(eff_swarm_id)
                            tool_output = json.dumps({"swarm_id": eff_swarm_id, "members": members}, ensure_ascii=False)
                            compressed = f"[Swarm 状态查询: {len(members)} 名成员]"
                        elif action == "kill":
                            m_name = str(args.get("name") or "").strip()
                            if not m_name:
                                tool_output = "Error: `swarm_control(action='kill')` 缺少参数 `name`。"
                            else:
                                mem = await swarm_manager.kill_member(eff_swarm_id, m_name)
                                tool_output = json.dumps({"status": "killed", "member": mem}, ensure_ascii=False)
                            compressed = f"[Swarm 终止成员 `{m_name}`]"
                        elif action == "title":
                            new_title = str(args.get("title") or "").strip()
                            sw_obj = await SwarmRepository.update_swarm(eff_swarm_id, title=new_title)
                            tool_output = json.dumps({"status": "updated", "swarm": sw_obj}, ensure_ascii=False)
                            compressed = f"[Swarm 更新标题 `{new_title}`]"
                        else:
                            tool_output = f"Error: 未知的 swarm_control action `{action}`。"
                            compressed = f"[Swarm 未知动作 `{action}`]"

                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"swarm_control({action})",
                            "reason": "蜂群拓扑控制",
                            "status": "denied" if tool_output.startswith("Denied:") else "completed",
                            "output": tool_output,
                            "compressed": compressed,
                        }

                    elif func_name == "swarm_post":
                        from .swarm_manager import swarm_manager

                        eff_swarm_id = swarm_id
                        if not eff_swarm_id:
                            sw_obj = await swarm_manager.get_or_create_swarm(
                                session_id=conv_id,
                                workspace_id=ws_id,
                                cwd=target_cwd,
                                default_model=model_name,
                            )
                            eff_swarm_id = sw_obj["id"]
                            swarm_id = eff_swarm_id

                        post_text = str(args.get("text") or "").strip()
                        post_kind = str(args.get("kind") or "message").strip() or "message"
                        sender_name = swarm_member_name or (swarm_role or "coordinator")
                        if not post_text:
                            tool_output = "Error: `swarm_post` 缺少参数 `text`。"
                            compressed = "[Swarm 发帖失败: 空消息]"
                        else:
                            msg = await swarm_manager.post_message(
                                eff_swarm_id,
                                sender=sender_name,
                                text=post_text,
                                kind=post_kind,
                            )
                            tool_output = json.dumps({"status": "posted", "message": msg}, ensure_ascii=False)
                            compressed = f"[Swarm 发帖 #{msg['seq']} by {sender_name}]"
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"swarm_post({sender_name})",
                            "reason": "向蜂群消息板发帖",
                            "status": "completed",
                            "output": tool_output,
                            "compressed": compressed,
                        }

                    elif func_name == "swarm_read":
                        from .swarm_manager import swarm_manager
                        from ..repository.swarm_repository import SwarmRepository

                        eff_swarm_id = swarm_id
                        if not eff_swarm_id:
                            sw_obj = await swarm_manager.get_or_create_swarm(
                                session_id=conv_id,
                                workspace_id=ws_id,
                                cwd=target_cwd,
                                default_model=model_name,
                            )
                            eff_swarm_id = sw_obj["id"]
                            swarm_id = eff_swarm_id

                        if bool(args.get("status", False)):
                            members = await SwarmRepository.list_members(eff_swarm_id)
                            tool_output = json.dumps({"swarm_id": eff_swarm_id, "members": members}, ensure_ascii=False)
                            compressed = f"[Swarm 读取成员状态: {len(members)} 名]"
                        else:
                            since_seq = int(args.get("since") or 0)
                            read_limit = int(args.get("limit") or 20)
                            msgs, head_seq = await SwarmRepository.read_messages(
                                eff_swarm_id, since=since_seq, limit=read_limit
                            )
                            tool_output = json.dumps({"messages": msgs, "head": head_seq}, ensure_ascii=False)
                            compressed = f"[Swarm 读取消息 {len(msgs)} 条 (head={head_seq})]"
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": "swarm_read()",
                            "reason": "读取蜂群消息板",
                            "status": "completed",
                            "output": tool_output,
                            "compressed": compressed,
                        }

                    elif func_name == "swarm_wait":
                        from .swarm_manager import swarm_manager

                        eff_swarm_id = swarm_id
                        if not eff_swarm_id:
                            sw_obj = await swarm_manager.get_or_create_swarm(
                                session_id=conv_id,
                                workspace_id=ws_id,
                                cwd=target_cwd,
                                default_model=model_name,
                            )
                            eff_swarm_id = sw_obj["id"]
                            swarm_id = eff_swarm_id

                        since_seq = int(args.get("since") or 0)
                        mentions_only = bool(args.get("mentions_only", True))
                        timeout_sec = float(args.get("timeout_seconds") or 120.0)
                        waiter_name = swarm_member_name or (swarm_role or "coordinator")

                        wait_res = await swarm_manager.wait_for_messages(
                            eff_swarm_id,
                            member_name=waiter_name,
                            since=since_seq,
                            mentions_only=mentions_only,
                            timeout_seconds=timeout_sec,
                        )
                        tool_output = json.dumps(wait_res, ensure_ascii=False)
                        compressed = f"[Swarm 唤醒: 收到 {len(wait_res.get('messages') or [])} 条消息 (head={wait_res.get('head')})]"
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": f"swarm_wait(since={since_seq})",
                            "reason": "零轮询挂起等待蜂群消息",
                            "status": "completed",
                            "output": tool_output,
                            "compressed": compressed,
                        }

                    else:
                        tool_output = f"Error: 未知的工具名称 `{func_name}`。"
                        compressed = f"[未知工具调用 `{func_name}`]"

                    # 智能异常探测：判断该步骤是否遇到报错，并为下一步自愈循环激活调度器
                    tool_output_str = (
                        tool_output
                        if isinstance(tool_output, str)
                        else ("" if tool_output is None else str(tool_output))
                    )
                    if (
                        func_name in (
                            "execute_local_command",
                            "write_local_file",
                            "replace_in_file",
                            "run_skill_tool",
                            "mcp_download_artifact_to_workspace",
                        )
                        and not tool_output_str.startswith(("Denied:", "Error:"))
                    ):
                        has_workspace_mutations = True
                    prev_step_error = last_step_error_info
                    out_lower = tool_output_str.lower()
                    # 后台命令刚转入后台或正在活跃轮询(running)以及 Swarm 挂起等待时，豁免异常探测与 LoopBreaker 熔断
                    if is_bg_detached or bg_poll_running or func_name == "swarm_wait":
                        is_step_abnormal = False
                    else:
                        is_step_abnormal = (
                            (tool_exit_code is not None and tool_exit_code != 0)
                            or tool_output_str.startswith(("Error:", "Denied:"))
                            or any(
                                err_kw in out_lower
                                for err_kw in (
                                    "npm error", "enoent", "error:", "traceback", "syntaxerror",
                                    "command not found", "cannot find module", "failed", "denied",
                                    "commandnotfoundexception", "fullyqualifiederrorid",
                                    "无法将", "不是内部或外部命令",
                                    "[停滞告警]", "[网络异常警告]",
                                )
                            )
                        )
                    attempt_key = (
                        re.sub(r"\s+", " ", str(args.get("command", "")).strip())
                        if func_name == "execute_local_command"
                        else args_hash
                    )
                    if is_step_abnormal:
                        # S6 自动升档护栏：在 low/medium 档位发生工具报错时，立即锁定当前轮剩余步骤升回 high
                        if step_thinking_level in ("low", "medium"):
                            lock_high_thinking = True
                            logger.info(
                                f"[AgentRuntime] S6 自动升档护栏触发（{step_thinking_level} -> high）: 工具 `{func_name}` 执行异常"
                            )
                        # S10: 未拦截的失败调用写入签名窗口，防止连续第 2 次无意义重试
                        if not s10_intercepted and not args_parse_failed:
                            turn_tool_sig_cache[sig_key] = step_idx + 1
                        # 语义级死循环判定 (LoopBreaker)：同一方案重复尝试失败超过 3 次（或同一报错指纹连续失败超过 3 次）才触发熔断
                        is_circuit_breaker = False
                        attempt_sig = (func_name, attempt_key)
                        failed_attempt_counts[attempt_sig] = failed_attempt_counts.get(attempt_sig, 0) + 1

                        err_lines = [ln.strip() for ln in tool_output_str.splitlines() if ln.strip()]
                        raw_err_line = err_lines[-1] if err_lines else tool_output_str.strip()
                        norm_err_tail = re.sub(r"\d+", "#", raw_err_line)[:100]
                        norm_err_fp = f"{func_name}:{norm_err_tail}"
                        if consecutive_err_fp.get("fp") == norm_err_fp:
                            consecutive_err_fp["count"] = int(consecutive_err_fp.get("count") or 0) + 1
                        else:
                            consecutive_err_fp["fp"] = norm_err_fp
                            consecutive_err_fp["count"] = 1

                        if func_name == "execute_local_command":
                            command_history.append({"cmd": attempt_key, "abnormal": True})

                        if (
                            failed_attempt_counts[attempt_sig] > 3
                            or int(consecutive_err_fp["count"]) > 3
                        ):
                            is_circuit_breaker = True
                            allow_step_extension = False
                            circuit_breaker_triggered = True
                            logger.warning(
                                f"[AgentRuntime] 触发死循环熔断 (LoopBreaker): 方案 `{func_name}` 已重复失败超过 3 次 "
                                f"(attempt_fails={failed_attempt_counts[attempt_sig]}, consecutive_same_err={consecutive_err_fp['count']})"
                            )

                        last_step_error_info = {
                            "tool": func_name,
                            "summary": compressed or (tool_output_str.strip().split("\n")[0][:120]),
                            "circuit_breaker": is_circuit_breaker,
                            "failed_cmd": re.sub(r"\s+", " ", str(args.get("command", "")).strip()) if func_name == "execute_local_command" else "",
                        }
                        # 🚑 盘中报错即时急救：触发异常时立即从 L5 经验库检索相似踩坑避坑解
                        if not active_incognito and active_scene != "oneshot":
                            try:
                                rescue_cases = await episodic_experience_store.search_by_error(
                                    tool_output_str,
                                    workspace_id=ws_id,
                                    limit=2,
                                    min_score=0.65,
                                )
                                if rescue_cases:
                                    rescue_lines = ["\n\n💡 **[L5 盘中历史避坑急救建议]**（匹配历史已验证解法，请优先采纳）："]
                                    for rc in rescue_cases:
                                        if rc.id and rc.id not in injected_case_ids:
                                            injected_case_ids.append(rc.id)
                                        safe_scene_txt, _ = MemoryPolicyGuard.sanitize_text(rc.scene_summary)
                                        safe_sol_txt, _ = MemoryPolicyGuard.sanitize_text(rc.verified_solution)
                                        rescue_lines.append(
                                            f"- (匹配度:{rc.match_score:.2f}) **{safe_scene_txt}** -> {safe_sol_txt}"
                                        )
                                    rescue_addon = "\n".join(rescue_lines)
                            except Exception as rescue_err:
                                logger.debug(f"[AgentRuntime] 盘中 L5 报错急救检索跳过: {rescue_err}")
                    else:
                        consecutive_err_fp["fp"] = ""
                        consecutive_err_fp["count"] = 0
                        failed_attempt_counts.pop((func_name, attempt_key), None)
                        if not s10_intercepted:
                            is_mutating_tool = func_name in (
                                "write_local_file",
                                "replace_in_file",
                                "mcp_download_artifact_to_workspace",
                                "run_skill_tool",
                            )
                            is_mutating_shell = False
                            if func_name == "execute_local_command":
                                cmd_entry = str(args.get("command", "")).strip()
                                norm_cmd_entry = re.sub(r"\s+", " ", cmd_entry)
                                command_history.append({"cmd": norm_cmd_entry, "abnormal": False})
                                if self._WRITE_SHELL_HINT_RE.search(cmd_entry):
                                    executed_mutating_shell = True
                                    is_mutating_shell = True
                                elif last_executed_shell_cmd and norm_cmd_entry != last_executed_shell_cmd:
                                    # 命令文本变更且成功执行，清空防重窗口以保障“执行变更 -> 重新验证”闭环
                                    is_mutating_shell = True
                                last_executed_shell_cmd = norm_cmd_entry

                            if is_mutating_tool or is_mutating_shell:
                                turn_tool_sig_cache.clear()
                                if func_name == "mcp_download_artifact_to_workspace":
                                    dl_path = str(args.get("save_rel_path") or "").strip()
                                    if dl_path:
                                        ws_dl_root = Path(ctx.get("current_cwd") or os.getcwd()).resolve()
                                        self._invalidate_file_read_cache(conv_id, (ws_dl_root / dl_path).resolve())
                            elif not args_parse_failed and func_name != "check_background_command":
                                turn_tool_sig_cache[sig_key] = step_idx + 1
                        last_step_error_info = None
                        if not str(tool_output or "").startswith(("Error:", "Denied:")):
                            total_tool_steps += 1
                            if func_name in ("write_local_file", "replace_in_file"):
                                fp = str(args.get("file_path") or args.get("path") or "").strip()
                                if fp and fp not in produced_artifacts:
                                    produced_artifacts.append(fp)
                            elif func_name == "mcp_download_artifact_to_workspace":
                                fp = str(args.get("save_rel_path") or "").strip()
                                if fp and fp not in produced_artifacts:
                                    produced_artifacts.append(fp)
                            if prev_step_error is not None:
                                recovered_error_count += 1

                    # 旁路影子清洗 (Context Scrubber)：按“工具语义 + 通道上下文上限”自适应判定阈值，
                    # 确保 Skill SOP 与常规文件读取一次看全、零多轮翻页浪费；仅对超限大输出截流转 Ref；
                    # 严格遵守 Write-Once 铁律：一次决定后再写入 messages.content，且保留前端人类可读单行 `compressed`。
                    scrub_threshold = resolve_scrub_char_threshold(
                        tool_name=func_name,
                        primary_provider_info=resolved_primary_provider,
                        model_name=model_name,
                    )
                    if len(tool_output or "") > scrub_threshold:
                        try:
                            scrub_res = await LocalContextCompressor.scrub_tool_output(
                                conversation_id=conv_id,
                                turn_id=current_turn_id,
                                tool_name=func_name,
                                tool_args_summary=json.dumps(args, ensure_ascii=False)[:180],
                                raw_output=tool_output,
                                incognito=active_incognito,
                                primary_provider_info=resolved_primary_provider,
                            )
                            if scrub_res.get("was_scrubbed"):
                                tool_output = str(scrub_res["content"])
                                # 仅当此前无可读单行摘要时兜底填充，保留如 `[已结构化读取文档 ...]` 等友好UI展示
                                if not compressed:
                                    compressed = tool_output.splitlines()[0][:120]
                                saved_est = int(scrub_res.get("saved_tokens") or 0)
                                scrubber_m = scrub_res.get("scrubber_metrics")
                                if isinstance(scrubber_m, dict):
                                    scrubber_record = dict(scrubber_m)
                                    scrubber_record["turn_id"] = current_turn_id
                                    scrubber_record["step_index"] = step_idx
                                    scrubber_record["agent_role"] = "scrubber"
                                    scrubber_record["saved_tokens"] = saved_est
                                    in_memory_step_metrics.append(scrubber_record)
                                    if not active_incognito:
                                        await TokenUsageRepository.record_usage(
                                            conversation_id=conv_id,
                                            turn_id=current_turn_id,
                                            step_index=step_idx,
                                            agent_role="scrubber",
                                            provider_id=str(scrubber_record.get("provider_id") or ""),
                                            provider_name=str(scrubber_record.get("provider_name") or ""),
                                            provider_type=str(scrubber_record.get("provider_type") or ""),
                                            model_name=str(scrubber_record.get("model_name") or ""),
                                            prompt_tokens=int(scrubber_record.get("prompt_tokens") or 0),
                                            cached_tokens=int(scrubber_record.get("cached_tokens") or 0),
                                            eval_tokens=int(scrubber_record.get("eval_tokens") or 0),
                                            completion_tokens=int(scrubber_record.get("completion_tokens") or 0),
                                            saved_tokens=saved_est,
                                            context_limit=int(scrubber_record.get("context_limit") or 0),
                                            ttft_ms=float(scrubber_record.get("ttft_ms") or 0.0),
                                            prefill_ms=float(scrubber_record.get("prefill_ms") or 0.0),
                                            tps=float(scrubber_record.get("tps") or 0.0),
                                            is_estimated=bool(scrubber_record.get("is_estimated", False)),
                                        )
                                elif saved_est > 0:
                                    det_record = {
                                        "turn_id": current_turn_id,
                                        "step_index": step_idx,
                                        "agent_role": "scrubber",
                                        "provider_type": "local_rule",
                                        "provider_name": "ContextScrubber",
                                        "model_name": "deterministic_scrubber",
                                        "prompt_tokens": 0,
                                        "cached_tokens": 0,
                                        "eval_tokens": 0,
                                        "completion_tokens": 0,
                                        "saved_tokens": saved_est,
                                        "context_limit": 0,
                                        "ttft_ms": 0.0,
                                        "prefill_ms": 0.0,
                                        "tps": 0.0,
                                        "is_estimated": True,
                                    }
                                    in_memory_step_metrics.append(det_record)
                                    if not active_incognito:
                                        await TokenUsageRepository.record_usage(
                                            conversation_id=conv_id,
                                            turn_id=current_turn_id,
                                            step_index=step_idx,
                                            agent_role="scrubber",
                                            provider_name="ContextScrubber",
                                            provider_type="local_rule",
                                            model_name="deterministic_scrubber",
                                            saved_tokens=saved_est,
                                            is_estimated=True,
                                        )
                        except Exception as scrub_err:
                            logger.debug(f"[AgentRuntime] 旁路工具清洗跳过: {scrub_err}")

                    final_tool_output = (
                        tool_output
                        if isinstance(tool_output, str)
                        else ("" if tool_output is None else str(tool_output))
                    )
                    if rescue_addon:
                        final_tool_output += rescue_addon

                    # S4 联动回补：工具因参数格式错误失败时，附带单工具完整 JSON Schema 说明
                    tool_output = self._append_schema_hint_if_arg_error(
                        func_name,
                        final_tool_output,
                        args_parse_failed=args_parse_failed,
                    )

                    # 🛡️ 记录单轮工具调用状态与结果
                    is_err = "Denied:" in tool_output or "Error" in tool_output or "Traceback" in tool_output
                    is_empty_res = bool(
                        tool_output.strip() in ("[]", "{}", '{"results": []}', "None")
                        or re.search(r"(?:未检索到|未找到|未查到|无相关记录|FileNotFoundError|ENOENT|0 条匹配|0 items)", tool_output, re.IGNORECASE)
                    )
                    executed_tools_record.append({
                        "name": func_name,
                        "status": "error" if is_err else "success",
                        "is_empty": is_empty_res,
                    })

                    # 🛡️ 空数据 Fail-Fast 熔断机制：针对数据/日志查询类工具，若结果为空硬性注入防脑补提醒
                    if (
                        effective_grounding
                        and effective_grounding.get("fail_fast_on_missing")
                        and is_empty_res
                        and func_name in ("search_memory_history", "read_local_file", "search_skill_knowledge", "web_fetch", "run_skill_tool")
                    ):
                        tool_output += (
                            "\n[防幻觉熔断提示]: 数据源查询结果为空或目标文件不存在。根据当前专家防幻觉与严谨度契约，"
                            "严禁自行编造任何模拟/测试假数据，请立即如实向用户说明“未检索到相关真实记录/文件不存在”，并终止后续虚构操作。"
                        )

                    # 将工具输出与单行折叠摘要同时落盘
                    await MessageRepository.save_message(
                        conversation_id=conv_id,
                        role="tool",
                        content=tool_output,
                        compressed_content=compressed,
                        turn_id=current_turn_id,
                        tool_call_id=call_id,
                    )

                    # 追加写入 Event Ledger（tool_executed / artifact_produced / tool_error_recovered）
                    if not active_incognito and active_scene != "oneshot":
                        step_skill_id = (
                            str(args.get("skill_name") or "").strip().lstrip("/")
                            or (active_skill_ids[0] if active_skill_ids else None)
                        )
                        current_loop_id = tracked_loop_id or (active_loop_obj.id if active_loop_obj else None)
                        try:
                            await event_ledger.append_event(
                                workspace_id=ws_id,
                                conversation_id=conv_id,
                                turn_id=current_turn_id,
                                loop_id=current_loop_id,
                                event_type="tool_executed",
                                actor="agent",
                                skill_id=step_skill_id,
                                payload={
                                    "tool": func_name,
                                    "args": args,
                                    "compressed": compressed,
                                    "is_error": is_step_abnormal,
                                    "output_preview": (tool_output or "")[:400],
                                },
                                incognito=False,
                            )
                            if (
                                func_name in ("write_local_file", "replace_in_file", "mcp_download_artifact_to_workspace")
                                and not is_step_abnormal
                                and not str(tool_output).startswith(("Error:", "Denied:"))
                            ):
                                ledger_fp = (
                                    str(args.get("save_rel_path") or "")
                                    if func_name == "mcp_download_artifact_to_workspace"
                                    else str(args.get("file_path") or args.get("path") or "")
                                )
                                await event_ledger.append_event(
                                    workspace_id=ws_id,
                                    conversation_id=conv_id,
                                    turn_id=current_turn_id,
                                    loop_id=current_loop_id,
                                    event_type="artifact_produced",
                                    actor="agent",
                                    skill_id=step_skill_id,
                                    payload={
                                        "file_path": ledger_fp,
                                        "compressed": compressed,
                                    },
                                    incognito=False,
                                )
                            if prev_step_error is not None and not is_step_abnormal:
                                await event_ledger.append_event(
                                    workspace_id=ws_id,
                                    conversation_id=conv_id,
                                    turn_id=current_turn_id,
                                    loop_id=current_loop_id,
                                    event_type="tool_error_recovered",
                                    actor="agent",
                                    skill_id=step_skill_id,
                                    payload={
                                        "failed_tool": prev_step_error.get("tool"),
                                        "failed_summary": prev_step_error.get("summary"),
                                        "recovery_tool": func_name,
                                        "recovery_args": args,
                                        "recovery_compressed": compressed,
                                    },
                                    incognito=False,
                                )
                        except Exception as ledger_err:
                            logger.debug(f"[AgentRuntime] 写入工具账本事件跳过: {ledger_err}")

                    # ── 具身状态联动：推进 Loop 轮次并动态记事板 ──
                    if active_loop_obj:
                        try:
                            summary_text = compressed or (str(args.get("command", "")) if func_name == "execute_local_command" else f"{func_name}")
                            is_succ = not is_step_abnormal
                            updated_loop = await TaskLoopRepository.record_tool_step(
                                loop_id=active_loop_obj.id,
                                tool_name=func_name,
                                summary=summary_text,
                                is_success=is_succ,
                            )
                            if updated_loop:
                                active_loop_obj = updated_loop
                                if target_cwd:
                                    try:
                                        ws_sp = Path(target_cwd) / ".agenthub" / "scratchpad.md"
                                        ws_sp.parent.mkdir(parents=True, exist_ok=True)
                                        ws_sp.write_text(updated_loop.scratchpad_md or "", encoding="utf-8")
                                    except Exception:
                                        pass
                                yield {
                                    "type": "loop_step",
                                    "conversation_id": conv_id,
                                    "loop_id": updated_loop.id,
                                    "iteration": updated_loop.current_iteration,
                                    "max_iterations": updated_loop.max_iterations,
                                    "status": updated_loop.loop_status,
                                    "scratchpad_md": updated_loop.scratchpad_md,
                                }
                        except Exception as e:
                            logger.warning(f"自主循环步进同步异常: {e}")

                if was_cancelled or await _check_cancelled():
                    was_cancelled = True
                    break

                # 若已触发死循环熔断（同一方案重复失败超过 3 次），立即切换至纯文本收尾向用户说明卡点
                if circuit_breaker_triggered:
                    force_final_text_only_step = True
                    current_max_steps = min(step_idx + 2, self.MAX_REACT_STEPS)
                    step_idx += 1
                    continue

                # 显式完成收尾：仅当本步无待消化实质工具结果且已包含完整交付正文时直接拆条收尾；否则推进纯文本最终汇总步
                if task_marked_completed and not last_step_error_info:
                    if has_substantive_tools_in_step or not self._is_substantive_deliverable_text(clean_text):
                        logger.info(
                            "[AgentRuntime] 检测到归档工具与实质性工具并发或尚缺完整终稿，推进至独立纯文本汇总步"
                        )
                        force_final_text_only_step = True
                        if step_idx + 1 >= current_max_steps:
                            current_max_steps += 1
                        step_idx += 1
                        continue

                    logger.info("[AgentRuntime] 模型通过 update_working_memory 显式标记任务完成，拆分为独立交付消息安全收尾")
                    clean_deliverable = ToolProtocolAdapter.strip_pseudo_transcript_prefixes(clean_text).strip()
                    if step_process_msg_id:
                        try:
                            await MessageRepository.update_message_content(
                                step_process_msg_id,
                                formatted_tc_xml,
                                token_count=len(formatted_tc_xml) // 3,
                            )
                        except Exception as upd_err:
                            logger.debug(f"[AgentRuntime] 清理过程消息残留正文跳过: {upd_err}")

                    await MessageRepository.save_message(
                        conversation_id=conv_id,
                        role="model",
                        content=clean_deliverable,
                        executed_by_account=final_account,
                        token_count=len(clean_deliverable) // 3,
                        turn_id=current_turn_id,
                        tool_calls_json=None,
                    )
                    await ConversationRepository.touch(conv_id)
                    if conv_id and not active_incognito:
                        long_term_memory.schedule_extraction(conv_id, delay_seconds=180)

                    yield {
                        "type": "start",
                        "step_index": step_idx + 1,
                        "clear_prev_process_text": True,
                        "account": final_account,
                        "model": effective_model_name,
                    }
                    rollover_task_completed = True
                    summary_snippet = clean_deliverable.split("\n")[0][:80]
                    async for tail_ev in _emit_turn_tail_events(
                        summary_snippet or "任务标记为已完成，工作记忆已归档",
                    ):
                        yield tail_ev
                    # 阶段四：输出前自我批评与多维置信度治理 (v1.5.5)
                    try:
                        from ..governance.self_critique import SelfCritiqueCritic
                        from ..governance.confidence_evaluator import ConfidenceEvaluator

                        critique_res = SelfCritiqueCritic.evaluate_draft(
                            user_query=content_prompt,
                            retrieved_evidence=retrieved_evidence_chunks,
                            draft_response=clean_deliverable,
                            has_version_conflict=False,
                        )
                        if not critique_res.is_passed and critique_retry_count < 1:
                            critique_retry_count += 1
                            yield {
                                "type": "critique_redraft",
                                "reasons": critique_res.critique_points,
                                "message": f"自审检出潜在缺陷: {'; '.join(critique_res.critique_points)}，正在触发一次自动纠偏重写...",
                            }
                            critique_prompt = (
                                f"[系统自审质量拦截]: 前置交付草稿经自审查验发现以下缺陷:\n"
                                + "\n".join(f"- {p}" for p in critique_res.critique_points)
                                + "\n请严格依据真实检索到的事实依据修正上述问题，重新输出严谨准确的最终回答。"
                            )
                            await MessageRepository.save_message(
                                conversation_id=conv_id,
                                role="user",
                                content=critique_prompt,
                            )
                            step_idx += 1
                            continue

                        conf_rep = ConfidenceEvaluator.evaluate(
                            retrieved_chunks=retrieved_evidence_chunks,
                            critique_res=critique_res,
                            tools_executed=executed_tools_record,
                            strict_mode=False,
                        )
                        yield {
                            "type": "confidence_report",
                            "score": conf_rep.score,
                            "tier_level": conf_rep.tier_level,
                            "reasons": conf_rep.reasons,
                            "requires_confirmation": conf_rep.requires_confirmation,
                            "evidence_sources": conf_rep.evidence_sources,
                        }
                    except Exception as conf_err:
                        logger.debug(f"[AgentRuntime] 置信度治理自审跳过: {conf_err}")

                    stream_completed_normally = True
                    yield {
                        "type": "done",
                        "full_content": accumulated_visible_text.strip() or clean_deliverable,
                        "final_step_content": clean_deliverable,
                        "account": final_account,
                        "model": effective_model_name,
                    }
                    break

                step_idx += 1

                # 多步 ReAct 执行中途若单步上下文水位已达 90%，提前熔断蒸馏并切换至同工作区接力新会话续跑
                if (
                    step_idx < current_max_steps
                    and not active_incognito
                    and not rollover_triggered
                    and in_memory_step_metrics
                ):
                    latest_step_m = in_memory_step_metrics[-1]
                    latest_ctx_tokens = int(latest_step_m.get("prompt_tokens") or 0)
                    latest_ctx_limit = int(latest_step_m.get("context_limit") or 0)
                    if (
                        latest_ctx_limit > 0
                        and latest_ctx_tokens > 0
                        and (latest_ctx_tokens / latest_ctx_limit) >= get_effective_rollover_ratio()
                    ):
                        rollover_pct = min(100.0, (latest_ctx_tokens / latest_ctx_limit) * 100.0)
                        threshold_pct = int(round(get_effective_rollover_ratio() * 100))
                        rollover_notice = (
                            f"🔁 当前会话上下文水位已达 {rollover_pct:.1f}%（阈值 {threshold_pct}%），"
                            "已提前蒸馏关键工作上下文并在同工作区创建接力新对话继续执行。"
                        )
                        await MessageRepository.save_message(
                            conversation_id=conv_id,
                            role="model",
                            content=rollover_notice,
                            executed_by_account=final_account,
                            token_count=max(1, len(rollover_notice) // 3),
                            turn_id=current_turn_id,
                        )
                        await ConversationRepository.touch(conv_id)
                        rollover_task_completed = False
                        async for tail_ev in _emit_turn_tail_events(
                            f"上下文水位达 {rollover_pct:.1f}%，自动蒸馏交接至同工作区新会话续跑",
                        ):
                            yield tail_ev
                        stream_completed_normally = True
                        yield {
                            "type": "done",
                            "full_content": (
                                f"{accumulated_visible_text.strip()}\n\n{rollover_notice}".strip()
                            ),
                            "final_step_content": rollover_notice,
                            "account": final_account,
                            "model": effective_model_name,
                        }
                        break

                # 若已到达自适应最大步数上限（最高动态扩展至 40 步），追加独立收尾汇总气泡避免仅剩工具过程卡片
                if step_idx >= current_max_steps:
                    clean_deliverable = ToolProtocolAdapter.strip_pseudo_transcript_prefixes(clean_text).strip()
                    promoted_prev_text = bool(
                        not has_substantive_tools_in_step
                        and self._is_substantive_deliverable_text(clean_deliverable)
                    )
                    closing_summary = (
                        clean_deliverable
                        if promoted_prev_text
                        else f"已完成阶段性自主执行（共执行 {step_idx} 步），上方执行步骤中已记录全部工具调用输出。如需继续推进后续操作，请告知继续。"
                    )
                    if step_process_msg_id and promoted_prev_text:
                        try:
                            await MessageRepository.update_message_content(
                                step_process_msg_id,
                                formatted_tc_xml,
                                token_count=len(formatted_tc_xml) // 3,
                            )
                        except Exception:
                            pass
                    await MessageRepository.save_message(
                        conversation_id=conv_id,
                        role="model",
                        content=closing_summary,
                        executed_by_account=final_account,
                        token_count=len(closing_summary) // 3,
                        turn_id=current_turn_id,
                        tool_calls_json=None,
                    )
                    await ConversationRepository.touch(conv_id)
                    yield {
                        "type": "start",
                        "step_index": step_idx,
                        "clear_prev_process_text": promoted_prev_text,
                        "account": final_account,
                        "model": effective_model_name,
                    }
                    async for tail_ev in _emit_turn_tail_events(
                        f"已达当前执行步数上限 ({step_idx} 步)，已输出阶段性成果",
                        is_waiting_user_decision=is_planning_phase,
                    ):
                        yield tail_ev
                    stream_completed_normally = True
                    yield {
                        "type": "done",
                        "full_content": accumulated_visible_text.strip() or closing_summary,
                        "final_step_content": closing_summary,
                        "account": final_account,
                        "model": effective_model_name,
                    }
                    break

            if was_cancelled:
                cancel_cli_processes(conv_id)
                shell_executor.cancel_session_processes(conv_id)
                partial_clean = (
                    ToolProtocolAdapter.parse_xml_tool_calls(step_raw_text)[0].strip()
                    if step_raw_text
                    else ""
                )
                stopped_notice = (
                    f"{partial_clean}\n\n（已手动停止生成）"
                    if partial_clean
                    else "（已手动停止生成）"
                )
                if not active_incognito and conv_id:
                    try:
                        await MessageRepository.save_message(
                            conversation_id=conv_id,
                            role="model",
                            content=stopped_notice,
                            executed_by_account=final_account,
                            token_count=len(stopped_notice) // 3,
                            turn_id=current_turn_id,
                        )
                        await ConversationRepository.touch(conv_id)
                    except Exception as save_err:
                        logger.warning(f"[AgentRuntime] 保存手动停止消息失败: {save_err}")
                if active_scene == "agent" and ws_id and not active_loop_obj and not is_chitchat_turn:
                    try:
                        await _ensure_active_loop()
                    except Exception:
                        pass
                if active_loop_obj and active_loop_obj.loop_status in (
                    "planning",
                    "executing",
                    "verifying",
                    "reflecting",
                ):
                    try:
                        await TaskLoopRepository.update_loop_status(
                            loop_id=active_loop_obj.id,
                            status="paused",
                        )
                        yield {
                            "type": "loop_step",
                            "conversation_id": conv_id,
                            "loop_id": active_loop_obj.id,
                            "iteration": active_loop_obj.current_iteration,
                            "max_iterations": active_loop_obj.max_iterations,
                            "status": "paused",
                            "pending_approval_call": None,
                            "scratchpad_md": active_loop_obj.scratchpad_md,
                        }
                        active_loop_obj = None
                    except Exception as pause_err:
                        logger.warning(f"[AgentRuntime] 手动停止收敛 Loop 为 paused 失败: {pause_err}")
                yield {"type": "cancelled", "content": "已手动停止生成"}

        except GeneratorExit:
            # 客户端断开导致 async generator aclose() 时：仅执行清理与独立落库，严禁 yield
            await _cleanup_and_pause_loop_on_abort("SSE断连(GeneratorExit)")
            return

        except asyncio.CancelledError:
            await _cleanup_and_pause_loop_on_abort("任务取消(CancelledError)")
            raise

        except ConversationDeletedError:
            logger.info(f"[AgentRuntime] 会话 {conv_id} 已被删除，安全终止后台执行流。")
            cancel_cli_processes(conv_id)
            shell_executor.cancel_session_processes(conv_id)
            yield {"type": "done", "full_content": ""}

        except Exception as unexpected_err:
            logger.error(f"[AgentRuntime] chat_stream 发生未捕获异常: {unexpected_err}", exc_info=True)
            cancel_cli_processes(conv_id)
            shell_executor.cancel_session_processes(conv_id)
            err_text = f"⚠️ 执行发生未预期异常: {unexpected_err}"
            if not active_incognito and conv_id:
                try:
                    await MessageRepository.save_message(
                        conversation_id=conv_id,
                        role="model",
                        content=err_text,
                        executed_by_account=final_account,
                        token_count=len(err_text) // 3,
                        turn_id=current_turn_id,
                    )
                    await ConversationRepository.touch(conv_id)
                except Exception:
                    pass
            if active_scene == "agent" and ws_id:
                try:
                    eff_err_loop = await _ensure_active_loop()
                    if eff_err_loop:
                        await TaskLoopRepository.update_loop_status(
                            loop_id=eff_err_loop.id,
                            status="failed",
                        )
                        yield {
                            "type": "loop_failed",
                            "conversation_id": conv_id,
                            "loop_id": eff_err_loop.id,
                            "iteration": eff_err_loop.current_iteration,
                            "max_iterations": eff_err_loop.max_iterations,
                            "status": "failed",
                            "error": str(unexpected_err),
                        }
                        active_loop_obj = None
                except Exception:
                    pass
            yield {"type": "error", "content": err_text}

        finally:
            current_entry = self._active_streams.get(conv_id)
            if current_entry and current_entry.get("token") == run_token:
                self._active_streams.pop(conv_id, None)

        # 7. 回合后异步记忆提炼与自然衰减维护（仅正常完成时触发；长程上下文分块坍缩已由 LocalContextCompressor 守护前缀复用）
        if stream_completed_normally and active_scene != "oneshot" and not active_incognito:
            asyncio.create_task(self._post_turn_memory_maintenance(user_message))
            window_limit = 24 if active_scene == "chat" else 20
            asyncio.create_task(
                session_summary_manager.maybe_update_summary(
                    conv_id,
                    workspace_id=ws_id,
                    current_turn_id=current_turn_id,
                    window_turns=window_limit,
                    incognito=active_incognito,
                )
            )
            round_clean_success = (last_step_error_info is None) and (not circuit_breaker_triggered)
            micro_trace = ExecutionTrace(
                workspace_id=ws_id,
                conversation_id=conv_id,
                loop_id=tracked_loop_id,
                scene_mode="plan_first" if is_planning_phase else active_scene,
                incognito=active_incognito,
                user_goal=user_message,
                active_skill_ids=active_skill_ids,
                recovered_error_count=recovered_error_count,
                had_user_intervention=bool(confirmed_facts or approved_plan_steps),
                total_tool_steps=total_tool_steps,
                circuit_breaker_triggered=circuit_breaker_triggered,
                final_success=round_clean_success,
                final_summary=(accumulated_visible_text or "")[:400],
                produced_artifacts=produced_artifacts,
                workspace_root=target_cwd,
            )
            asyncio.create_task(
                self._post_turn_l5_feedback_and_distill(
                    injected_case_ids=list(injected_case_ids),
                    round_clean_success=round_clean_success,
                    trace=micro_trace,
                    turn_id=current_turn_id,
                )
            )

    @staticmethod
    async def _post_turn_l5_feedback_and_distill(
        *,
        injected_case_ids: list[str],
        round_clean_success: bool,
        trace: ExecutionTrace,
        turn_id: int,
    ) -> None:
        """后台异步执行 L5 微经验闭环赏罚反馈与盘后静默微经验萃取。"""
        try:
            if injected_case_ids:
                await episodic_experience_store.record_feedback(
                    injected_case_ids,
                    success=round_clean_success,
                )
            if round_clean_success:
                await experience_distiller.distill_micro_case_silently(
                    trace,
                    turn_id=turn_id,
                )
        except Exception as exc:
            logger.warning(f"[AgentRuntime] 回合后 L5 闭环反馈或静默复盘跳过: {exc}")

    @staticmethod
    async def _post_turn_memory_maintenance(user_message: str) -> None:
        """
        回合后后台轻量级长期记忆捕获与 30 天指数衰减维护。
        当用户在对话中明确表达个人技术栈偏好、工作规范或环境特征时自动入库（经过隐私脱敏）。
        """
        try:
            text = (user_message or "").strip()
            if not text or len(text) > 500:
                return

            # 匹配用户显式声明偏好或规范的句式（零额外 Token 消耗）
            explicit_patterns = [
                (r"(?:请记住|记住|以后都|默认使用|我的偏好是|统一使用|严禁使用)[：:\s]*(.+)", "rule", 0.92),
                (r"(?:我习惯用|我喜欢用|我常用|我们项目用的是)[：:\s]*(.+)", "preference", 0.85),
                (r"(?:我的本机环境是|我的系统是|本地路径在|开发环境是)[：:\s]*(.+)", "env_info", 0.88),
            ]
            for pat, cat, conf in explicit_patterns:
                m = re.search(pat, text)
                if m:
                    raw_fact = m.group(0).strip()[:160]
                    fact, _ = MemoryPolicyGuard.sanitize_text(raw_fact)
                    if not fact.strip():
                        break
                    # 查重避免重复写入完全相同的句子
                    existing = await long_term_memory.list_all()
                    if not any(e["content"] == fact for e in existing):
                        await long_term_memory.add_memory(category=cat, content=fact, confidence=conf)
                        logger.info(f"[LongTermMemory] 自动捕获用户长期画像 [{cat}]: {fact}")
                    break

            await long_term_memory.apply_time_decay()
        except Exception as e:
            logger.debug(f"后台记忆维护异常: {e}")


# 全局单例
agent_runtime = AgentRuntime()

