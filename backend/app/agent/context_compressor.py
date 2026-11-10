"""
本地上下文极致脱水与 KV Cache 守护中间件 (LocalContextCompressor)
核心架构：
1. 三区物理隔离：
   - Zone 1 (Immutable Prefix): 固定人设 + 字典序工具协议 + 已固化历史摘要 (Anchored Summary)，字节级强冻结
   - Zone 2 (Append-Only History): 锚点之后的热区消息，只增不改（Write-Once），绝不逐轮左滑破坏 KV Cache
   - Zone 3 (Tail Injection): 冷区 FTS5 召回 + L4 事实与长期记忆 + 工作草稿纸 + 附件/Skill 规程，仅发送期尾注挂接，跨轮零残留
2. 双模分块坍缩 (Chunk Collapse)：
   - 本地通道 (llama_server / ollama / localhost): 默认 8,192 上下文，满 10 轮或达 70% (5,734 tokens) 时打包最老 6 轮固化进 Zone 1，保留最近 3 轮热窗
   - 云端通道: 大窗口 (75% 水位或满 24 轮) 打包坍缩，减少无谓摘要调用
3. 旁路影子清洗 (Context Scrubber)：
   - 工具返回 > 1,000 字符时自动截留原文至 tool_output_refs（<256KB 存库 / >=256KB 落盘），调度无状态轻模型提炼 <=200 字结论；
   - 内置单槽位 (-np 1) 防互锁嗅探与 6.0s 超时确定性正则提取器降级兜底；
   - 严格遵守 Write-Once 铁律：脱水结论一次写入 messages.content 后在热区永久保持一字不改。
"""
import asyncio
from collections import OrderedDict
import math
import re
from typing import Any
import uuid

from ..core.logging_config import logger
from ..memory.policy_guard import MemoryPolicyGuard
from ..memory.session_summary import session_summary_manager
from ..repository.conversation_repository import ConversationRepository
from ..repository.tool_ref_repository import MAX_SLICE_CHARS, MAX_SLICE_LINES, ToolRefRepository

# 匹配中日韩统一表意文字及日韩假名/谚文，用于高精度双语 Token 估算
_CJK_CHAR_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")

# 默认命令/批处理输出旁路脱水字符阈值（无渠道上下文时的保守基线，下探至 600 字符实现紧凑脱水）
SCRUB_CHAR_THRESHOLD = 600

# 按语义分层划分工具类别：
# 1. EXPLICIT_COMMAND_TOOLS：终端命令 / 脚本运行 / MCP 工具（极易产出数千行安装/构建/全盘扫描刷屏日志，适合积极脱水）
_COMMAND_NOISE_TOOLS = {"execute_local_command", "run_shell", "run_skill_tool"}
# 2. ACTIVE_READ_TOOLS：模型主动点名要查看正文内容的读取工具（只要通道窗口装得下就应一步给全，避免逼模型反复 read_tool_ref 翻页浪费 Token）
_ACTIVE_READ_TOOLS = {"read_local_file", "read_file", "web_fetch", "search_memory_history", "search_skill_knowledge"}
# 3. EXEMPT_SCRUB_TOOLS：技能规程指令与切片回查自身（豁免常规脱水，仅防极端数十 KB 异常）
_EXEMPT_SCRUB_TOOLS = {"load_skill_detail", "read_tool_ref", "update_working_memory", "save_long_term_memory"}

# 无痕会话专用进程内临时引用缓存（仅存内存，上限 32 条，绝不落盘或写库）
_INCOGNITO_TOOL_REFS: OrderedDict[str, dict[str, Any]] = OrderedDict()
_MAX_INCOGNITO_REFS = 32


def resolve_scrub_char_threshold(
    tool_name: str,
    primary_provider_info: dict[str, Any] | None = None,
    model_name: str = "",
) -> int:
    """根据“工具语义分类 + 当前推理通道真实上下文窗口”动态计算旁路脱水字符阈值。

    核心原则：必须在优先保证 1~2 步高效完成任务的前提下实现无损压缩，
    严禁在大窗口（如 DeepSeek 65.5K / Gemini 1M / Claude 200K）下过度截断仅有几千字符的文件读取或 Skill SOP，
    否则会迫使模型连续发起多轮 `read_tool_ref` 回查翻页，造成严重的时间与 Token 双重浪费。
    """
    tname = (tool_name or "").strip().lower()
    if tname in _EXEMPT_SCRUB_TOOLS:
        return 24_000

    max_ctx = infer_context_limit(primary_provider_info, model_name=model_name) if primary_provider_info else 8192
    local_mode = is_local_provider(primary_provider_info) if primary_provider_info else True

    if tname in _ACTIVE_READ_TOOLS:
        if primary_provider_info is None:
            # 未传入通道元信息时沿用 4,000 字符兼容阈（超大日志仍会正常落盘 Ref）
            return 4_000
        if local_mode or max_ctx <= 16_384:
            return 4_500
        if max_ctx <= 32_768:
            return 10_000
        return 20_000

    # 命令执行、脚本工具与 MCP 外部工具：容易刷屏无用日志，下探至 600~1,000 字符实现紧凑脱水
    if primary_provider_info is None:
        return SCRUB_CHAR_THRESHOLD
    if local_mode or max_ctx <= 16_384:
        return 600
    return 1_000


def estimate_tokens(text: str | None) -> int:
    """双语高精度 Token 估算器（零外部 tokenizer 依赖）。

    规则：
    - CJK 字符按 1.15 token/字 计算；
    - 其余 ASCII/符号按 0.28 token/字符 计算；
    - 非空文本保底返回 1。
    """
    if not text:
        return 0
    total_chars = len(text)
    cjk_count = len(_CJK_CHAR_RE.findall(text))
    non_cjk_count = max(0, total_chars - cjk_count)
    est = cjk_count * 1.15 + non_cjk_count * 0.28
    return max(1, int(math.ceil(est)))


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """估算结构化消息数组的 Token 总数（含每条消息 +8 tokens 角色与模板封装开销）"""
    total = 0
    for msg in messages:
        content = str(msg.get("content") or "")
        total += estimate_tokens(content) + 8
    return total


def is_local_provider(provider_info: dict[str, Any] | None) -> bool:
    """判别当前推理通道是否属于本地模型服务（需启用 8K 显存敏感型收紧策略）"""
    if not provider_info:
        return False
    ptype = str(provider_info.get("provider_type") or "").strip().lower()
    if ptype in {"llama_server", "ollama"}:
        return True
    base_url = str(provider_info.get("base_url") or "").strip().lower()
    if any(host in base_url for host in ("127.0.0.1", "localhost", "0.0.0.0", "host.docker.internal")):
        return True
    return False


def infer_context_limit(provider_info: dict[str, Any] | None, model_name: str = "") -> int:
    """推算当前推理通道的单槽位最大上下文 Token 上限"""
    if provider_info:
        explicit_limit = int(provider_info.get("max_context_tokens") or 0)
        if explicit_limit > 0:
            return explicit_limit
        if is_local_provider(provider_info):
            return 8192

    low_model = (model_name or (provider_info or {}).get("model_name") or "").strip().lower()
    ptype = str((provider_info or {}).get("provider_type") or "").strip().lower()

    if "claude" in low_model or ptype == "anthropic":
        return 200_000
    if "gemini" in low_model or ptype in {"google_oauth", "gemini_api", "gemini_cli"}:
        return 1_000_000
    if "deepseek" in low_model:
        return 65_536
    if any(tag in low_model for tag in ("gpt-4o", "gpt-4.1", "o1", "o3", "o4", "gpt-4-turbo")):
        return 128_000
    if ptype in {"llama_server", "ollama"}:
        return 8192
    return 32_768


def resolve_compression_policy(
    provider_info: dict[str, Any] | None,
    model_name: str = "",
    context_rounds_override: int | None = None,
) -> dict[str, Any]:
    """根据本地/云端双模特性解析分块坍缩策略参数（落实 A2 拍板口径）"""
    local_mode = is_local_provider(provider_info)
    max_ctx = infer_context_limit(provider_info, model_name=model_name)

    if local_mode:
        # 本地模式：8,192 默认窗口下 70% = 5,734 tokens，或锚点后满 10 轮打包前 6 轮，保底留最近 3 轮原文
        trigger_ratio = 0.70
        max_window_turns = 10
        hot_retain_turns = 3
        default_batch_turns = 6
        trigger_tokens = int(max_ctx * trigger_ratio)
    else:
        # 云端大窗口模式：引入 36,000 经济软上限与 8 轮热区判定，解决云端 1M 窗口下分块坍缩恒为 0 的问题
        trigger_ratio = 0.65
        base_retain = max(3, int(context_rounds_override)) if context_rounds_override else 3
        hot_retain_turns = base_retain
        max_window_turns = max(8, hot_retain_turns + 5)
        default_batch_turns = 5
        trigger_tokens = min(int(max_ctx * trigger_ratio), 36_000)

    return {
        "is_local": local_mode,
        "max_context_tokens": max_ctx,
        "trigger_ratio": trigger_ratio,
        "trigger_tokens": trigger_tokens,
        "max_window_turns": max_window_turns,
        "hot_retain_turns": hot_retain_turns,
        "default_batch_turns": default_batch_turns,
    }


def _store_incognito_ref(ref_id: str, tool_name: str, raw_content: str) -> None:
    """将无痕会话的超长工具输出暂存于进程 LRU 内存，绝不触碰数据库与磁盘"""
    if ref_id in _INCOGNITO_TOOL_REFS:
        _INCOGNITO_TOOL_REFS.move_to_end(ref_id)
    _INCOGNITO_TOOL_REFS[ref_id] = {
        "ref_id": ref_id,
        "tool_name": tool_name,
        "raw_content": raw_content,
    }
    while len(_INCOGNITO_TOOL_REFS) > _MAX_INCOGNITO_REFS:
        _INCOGNITO_TOOL_REFS.popitem(last=False)


def read_ephemeral_tool_ref(
    ref_id: str,
    start_line: int = 1,
    end_line: int = 120,
    keyword: str | None = None,
) -> dict[str, Any] | None:
    """从无痕内存 LRU 缓存中切片读取临时引用（未命中则返回 None 交由数据库仓储查询）"""
    entry = _INCOGNITO_TOOL_REFS.get(ref_id)
    if not entry:
        return None
    _INCOGNITO_TOOL_REFS.move_to_end(ref_id)

    raw_text = str(entry.get("raw_content") or "")
    lines = raw_text.splitlines()
    total_lines = len(lines)
    if total_lines == 0:
        return {
            "found": True,
            "ref_id": ref_id,
            "tool_name": entry.get("tool_name", ""),
            "total_lines": 0,
            "returned_start": 0,
            "returned_end": 0,
            "content": "(空文本)",
        }

    clean_kw = (keyword or "").strip()
    if clean_kw:
        low_kw = clean_kw.lower()
        hits = [i for i, line in enumerate(lines) if low_kw in line.lower()]
        if not hits:
            return {
                "found": True,
                "ref_id": ref_id,
                "tool_name": entry.get("tool_name", ""),
                "total_lines": total_lines,
                "matched_count": 0,
                "returned_start": 0,
                "returned_end": 0,
                "content": f"在总计 {total_lines} 行无痕缓存原文中未检索到关键词 `{clean_kw}`。",
            }
        first_hit = hits[0]
        w_start = max(0, first_hit - 10)
        w_end = min(total_lines - 1, first_hit + 10)
        snippet = "\n".join(f"L{i + 1}: {lines[i]}" for i in range(w_start, w_end + 1))[:MAX_SLICE_CHARS]
        return {
            "found": True,
            "ref_id": ref_id,
            "tool_name": entry.get("tool_name", ""),
            "total_lines": total_lines,
            "matched_count": len(hits),
            "returned_start": w_start + 1,
            "returned_end": w_end + 1,
            "content": snippet,
        }

    s_line = max(1, min(int(start_line or 1), total_lines))
    e_line = max(s_line, min(int(end_line or (s_line + 119)), total_lines))
    if (e_line - s_line + 1) > MAX_SLICE_LINES:
        e_line = s_line + MAX_SLICE_LINES - 1
    out_lines: list[str] = []
    chars = 0
    actual_end = s_line
    for idx in range(s_line - 1, e_line):
        row_txt = f"L{idx + 1}: {lines[idx]}"
        if out_lines and chars + len(row_txt) > MAX_SLICE_CHARS:
            break
        out_lines.append(row_txt)
        chars += len(row_txt) + 1
        actual_end = idx + 1

    return {
        "found": True,
        "ref_id": ref_id,
        "tool_name": entry.get("tool_name", ""),
        "total_lines": total_lines,
        "returned_start": s_line,
        "returned_end": actual_end,
        "content": "\n".join(out_lines),
    }


def _deterministic_scrub(tool_name: str, raw_output: str, max_chars: int = 460) -> str:
    """确定性高密度规则脱水提取器（跳过无意义分隔线，优先提取报错、标题、表头与实质性首尾摘要）"""
    raw_lines = [ln.strip() for ln in raw_output.splitlines() if ln.strip()]
    if not raw_lines:
        return "工具执行完成（输出为空白）。"

    # 过滤纯符号分隔行（如 YAML frontmatter 的 `---`、`===`、`***` 等，防止出现 `首行: ---`）
    meaningful_lines = [
        ln for ln in raw_lines if not re.fullmatch(r"[-=*_~`#|\s]{2,}", ln)
    ] or raw_lines

    # 1. 优先捕捉错误堆栈、退出码、结构化文档头、Sheet 名或 Markdown 表头/标题
    priority_patterns = re.compile(
        r"(error|exception|traceback|failed|fatal|warning|exit_code|passed|total|inserted|updated|created|deleted|saved|http\s+\d{3}|status[:=]\s*\d+|sheet|工作表|【结构化|^#{1,3}\s+|^\|[^|]+\||^(?:name|description|title)\s*:)",
        re.IGNORECASE,
    )
    key_lines: list[str] = []
    for ln in meaningful_lines:
        if priority_patterns.search(ln):
            compact = re.sub(r"\s+", " ", ln)
            if compact not in key_lines:
                key_lines.append(compact[:110])
        if len(key_lines) >= 4:
            break

    head_preview = [re.sub(r"\s+", " ", ln)[:95] for ln in meaningful_lines[:2]]
    tail_line = re.sub(r"\s+", " ", meaningful_lines[-1])[:95]

    parts: list[str] = []
    if key_lines:
        parts.append("关键摘要: " + " ; ".join(key_lines))
    for hp in head_preview:
        if hp and not any(hp in p for p in parts):
            parts.append(hp)
            break
    if tail_line and not any(tail_line in p for p in parts):
        parts.append(f"末行: {tail_line}")

    summary = f"[{tool_name} 共 {len(raw_lines)} 行] " + " | ".join(parts)
    if len(summary) > max_chars:
        summary = summary[: max_chars - 3] + "..."
    return summary


class LocalContextCompressor:
    """本地与混合架构上下文压缩与 KV Cache 守护中间件"""

    @classmethod
    def fold_historical_file_writes(
        cls,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """同路径多次写入历史自动折叠（Zone 2 瘦身守护）：
        倒序扫描历史消息中针对相同 file_path 的多次 write_local_file 写入，
        仅保留最后一次写入的完整代码，前序写入入参内容折叠为 '[旧版本代码已被后续步骤覆盖]'，
        杜绝同文件多次迭代引发的上下文 Token 级数爆炸。
        """
        import json as _json
        import re as _re

        seen_paths: set[str] = set()
        folded_reversed: list[dict[str, Any]] = []

        for m in reversed(messages):
            m_copy = dict(m)
            role = m_copy.get("role")
            content = str(m_copy.get("content") or "")
            tc_json = m_copy.get("tool_calls_json")

            if role in ("assistant", "model"):
                # 1. 若包含结构化 tool_calls_json
                if tc_json:
                    try:
                        calls = _json.loads(tc_json)
                        if isinstance(calls, list):
                            modified = False
                            for call in calls:
                                fn = call.get("function", {})
                                if fn.get("name") == "write_local_file":
                                    raw_args = fn.get("arguments", "{}")
                                    args_dict = _json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                                    if isinstance(args_dict, dict):
                                        fp = str(args_dict.get("file_path") or args_dict.get("path") or "").strip()
                                        if fp:
                                            if fp in seen_paths:
                                                # 已有更新版本，折叠旧代码
                                                args_dict["content"] = "[旧版本代码已被后续步骤覆盖]"
                                                fn["arguments"] = _json.dumps(args_dict, ensure_ascii=False)
                                                modified = True
                                            else:
                                                seen_paths.add(fp)
                            if modified:
                                m_copy["tool_calls_json"] = _json.dumps(calls, ensure_ascii=False)
                                xml_calls = "\n".join(
                                    f'<tool_call name="{tc.get("function", {}).get("name", "")}">{tc.get("function", {}).get("arguments", "{}")}</tool_call>'
                                    for tc in calls
                                )
                                text_clean = _re.sub(r'<tool_call.*?</tool_call>', '', content, flags=_re.DOTALL).strip()
                                m_copy["content"] = (text_clean + "\n" + xml_calls).strip() if text_clean else xml_calls
                    except Exception:
                        pass

                # 2. 检查纯文本中的 <tool_call name="write_local_file">
                elif '<tool_call' in content and 'write_local_file' in content:
                    def _fold_xml_tc(match: _re.Match) -> str:
                        full = match.group(0)
                        tc_body = match.group(1).strip()
                        try:
                            args_dict = _json.loads(tc_body)
                            if isinstance(args_dict, dict):
                                fp = str(args_dict.get("file_path") or args_dict.get("path") or "").strip()
                                if fp:
                                    if fp in seen_paths:
                                        args_dict["content"] = "[旧版本代码已被后续步骤覆盖]"
                                        return f'<tool_call name="write_local_file">{_json.dumps(args_dict, ensure_ascii=False)}</tool_call>'
                                    else:
                                        seen_paths.add(fp)
                        except Exception:
                            pass
                        return full

                    m_copy["content"] = _re.sub(
                        r'<tool_call\s+name=["\']write_local_file["\']>([\s\S]*?)</tool_call>',
                        _fold_xml_tc,
                        content,
                    )

            folded_reversed.append(m_copy)

        return list(reversed(folded_reversed))

    @classmethod
    async def count_payload_tokens(
        cls,
        system_instruction: str,
        history_messages: list[dict[str, Any]],
        tail_injection_prompt: str = "",
        current_user_query: str = "",
        provider_info: dict[str, Any] | None = None,
    ) -> tuple[int, bool]:
        """测算完整待发上下文的 Token 总数。

        若当前通道为 `llama_server`，优先调用原生 `/tokenize` 接口（800ms 超时即回退）；
        其余通道使用高精度双语估算器。
        """
        est_total = (
            estimate_tokens(system_instruction)
            + 8
            + estimate_messages_tokens(history_messages)
            + estimate_tokens(tail_injection_prompt)
            + estimate_tokens(current_user_query)
            + 8
        )

        if provider_info and str(provider_info.get("provider_type") or "").lower() == "llama_server":
            base_url = str(provider_info.get("base_url") or "").strip()
            if base_url:
                try:
                    from ..core.provider_adapter import ProviderAdapter

                    joined_parts = [system_instruction]
                    for m in history_messages:
                        joined_parts.append(f"{m.get('role', 'user')}: {m.get('content', '')}")
                    if current_user_query or tail_injection_prompt:
                        joined_parts.append(f"user: {current_user_query}\n{tail_injection_prompt}")
                    exact_tokens = await ProviderAdapter.tokenize_count(
                        base_url=base_url,
                        content="\n".join(joined_parts),
                        timeout=0.8,
                    )
                    if exact_tokens is not None and exact_tokens > 0:
                        return exact_tokens, True
                except Exception:
                    pass

        return est_total, False

    @classmethod
    async def maybe_collapse_history(
        cls,
        *,
        conversation_id: str,
        current_turn_id: int,
        workspace_id: str = "default",
        incognito: bool = False,
        collapse_anchor_turn_id: int = 0,
        anchored_summary: str = "",
        legacy_rolling_summary: str = "",
        zone1_base_prompt: str = "",
        zone2_messages: list[dict[str, Any]],
        zone3_tail_prompt: str = "",
        current_user_query: str = "",
        provider_info: dict[str, Any] | None = None,
        model_name: str = "",
        context_rounds_override: int | None = None,
    ) -> dict[str, Any]:
        """检查是否触发分块坍缩（Chunk Collapse），并输出最终三区构建素材。

        Returns:
            dict: {
                "collapse_anchor_turn_id": 更新后的锚点 turn_id,
                "anchored_summary": 更新后的固化前缀摘要,
                "zone2_messages": 坍缩后保留的热区消息列表,
                "collapsed_triggered": bool,
                "added_saved_tokens": int,
                "policy": dict,
            }
        """
        anchor_turn = int(collapse_anchor_turn_id or 0)
        summary_text = (anchored_summary or "").strip()

        # 向后兼容存量老会话：首次进入时从 session_summaries 或 rolling_summary 原地平滑对齐
        if anchor_turn == 0 and not summary_text and not incognito:
            existing_l3 = await session_summary_manager.get_summary(conversation_id)
            if existing_l3 and (existing_l3.get("summary_markdown") or "").strip():
                anchor_turn = int(existing_l3.get("watermark_turn_id") or 0)
                summary_text = str(existing_l3.get("summary_markdown") or "").strip()
                if anchor_turn > 0 and summary_text:
                    await ConversationRepository.update_collapse_anchor(
                        conv_id=conversation_id,
                        anchor_turn_id=anchor_turn,
                        anchored_summary=summary_text,
                        added_saved_tokens=0,
                    )
            elif legacy_rolling_summary.strip():
                summary_text = legacy_rolling_summary.strip()

        policy = resolve_compression_policy(
            provider_info=provider_info,
            model_name=model_name,
            context_rounds_override=context_rounds_override,
        )

        # 对热区历史执行同路径写入自动折叠（保留最新版完整代码，前序折叠）
        folded_zone2 = cls.fold_historical_file_writes(zone2_messages)

        # 过滤仅保留锚点之后的活跃消息
        active_msgs = [
            m for m in folded_zone2 if int(m.get("turn_id") or 1) > anchor_turn
        ]

        # 统计已完成历史轮次（strictly < current_turn_id）的有序 turn_id 列表
        completed_turns = sorted(
            {
                int(m.get("turn_id") or 1)
                for m in active_msgs
                if int(m.get("turn_id") or 1) < int(current_turn_id)
            }
        )

        # 若已完成历史轮次不超过保底热留存轮数，直接返回，守护 Prefix Cache 100% 稳定
        hot_retain = int(policy["hot_retain_turns"])
        if len(completed_turns) <= hot_retain:
            return {
                "collapse_anchor_turn_id": anchor_turn,
                "anchored_summary": summary_text,
                "zone2_messages": active_msgs,
                "collapsed_triggered": False,
                "added_saved_tokens": 0,
                "policy": policy,
            }

        # 组装临时预览 Zone 1 以测算水位
        preview_zone1 = zone1_base_prompt
        if summary_text:
            preview_zone1 = (
                f"{zone1_base_prompt}\n\n### 📜 已固化历史摘要 (Anchored Summary)：\n{summary_text}"
                if zone1_base_prompt
                else f"### 📜 已固化历史摘要 (Anchored Summary)：\n{summary_text}"
            )

        total_tokens, _ = await cls.count_payload_tokens(
            system_instruction=preview_zone1,
            history_messages=active_msgs,
            tail_injection_prompt=zone3_tail_prompt,
            current_user_query=current_user_query,
            provider_info=provider_info,
        )

        hit_turn_limit = len(completed_turns) >= int(policy["max_window_turns"])
        hit_token_limit = total_tokens >= int(policy["trigger_tokens"])

        if not hit_turn_limit and not hit_token_limit:
            return {
                "collapse_anchor_turn_id": anchor_turn,
                "anchored_summary": summary_text,
                "zone2_messages": active_msgs,
                "collapsed_triggered": False,
                "added_saved_tokens": 0,
                "policy": policy,
            }

        # 计算新锚点：
        # - 若因 Token 超水位触发，直接截留最新 hot_retain_turns（3 轮），打包更早全部轮次；
        # - 若因轮数满 10 轮触发，按批打包最老 default_batch_turns（6 轮，留最新 4 轮），减少频繁触发。
        if hit_token_limit:
            turns_to_collapse = completed_turns[:-hot_retain]
        else:
            batch_cnt = min(len(completed_turns) - hot_retain, int(policy["default_batch_turns"]))
            turns_to_collapse = completed_turns[: max(1, batch_cnt)]

        new_anchor = turns_to_collapse[-1]
        if new_anchor <= anchor_turn:
            return {
                "collapse_anchor_turn_id": anchor_turn,
                "anchored_summary": summary_text,
                "zone2_messages": active_msgs,
                "collapsed_triggered": False,
                "added_saved_tokens": 0,
                "policy": policy,
            }

        # 统计被移出热区的消息原估算 Token
        collapsed_msgs = [
            m for m in active_msgs if anchor_turn < int(m.get("turn_id") or 1) <= new_anchor
        ]
        old_chunk_tokens = estimate_messages_tokens(collapsed_msgs)
        old_summary_tokens = estimate_tokens(summary_text)

        merged_res = await session_summary_manager.collapse_turn_range(
            conversation_id=conversation_id,
            old_anchor_turn_id=anchor_turn,
            new_anchor_turn_id=new_anchor,
            existing_summary_markdown=summary_text,
            workspace_id=workspace_id,
            incognito=incognito,
            use_llm=True,
        )

        if merged_res and (merged_res.get("summary_markdown") or "").strip():
            new_summary = str(merged_res["summary_markdown"]).strip()
        else:
            # 极端空行兜底
            new_summary = summary_text or f"（已归档 Turn 1 ~ Turn {new_anchor} 历史对话）"

        new_summary_tokens = estimate_tokens(new_summary)
        summary_delta_tokens = max(0, new_summary_tokens - old_summary_tokens)
        added_saved = max(0, old_chunk_tokens - summary_delta_tokens)

        if not incognito:
            await ConversationRepository.update_collapse_anchor(
                conv_id=conversation_id,
                anchor_turn_id=new_anchor,
                anchored_summary=new_summary,
                added_saved_tokens=added_saved,
            )

        remaining_msgs = [
            m for m in active_msgs if int(m.get("turn_id") or 1) > new_anchor
        ]
        logger.info(
            f"[LocalContextCompressor] 会话 {conversation_id} 触发分块坍缩: "
            f"anchor {anchor_turn} -> {new_anchor} (原因: {'token超70%' if hit_token_limit else '满轮次阈值'}), "
            f"释放热区 {len(collapsed_msgs)} 条消息, 净省 ~{added_saved} tokens"
        )

        return {
            "collapse_anchor_turn_id": new_anchor,
            "anchored_summary": new_summary,
            "zone2_messages": remaining_msgs,
            "collapsed_triggered": True,
            "added_saved_tokens": added_saved,
            "policy": policy,
        }

    @classmethod
    async def scrub_tool_output(
        cls,
        *,
        conversation_id: str,
        turn_id: int,
        tool_name: str,
        tool_args_summary: str,
        raw_output: str,
        incognito: bool = False,
        primary_provider_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """旁路影子清洗器 (Context Scrubber)。

        当工具输出 > 1,000 字符时：
        1. 将原始输出登记至 `tool_output_refs`（或无痕内存缓存），生成 `ref_id`；
        2. 优先调用无状态 `light` 通道（6s 超时）提炼 <= 200 字核心结论；
           若检测到与主对话共享单槽位 (`-np 1`) 的本地端点或无可用轻模型，自动降级至确定性高密度提取器；
        3. 输出固定格式精简摘要块（Write-Once，一次写入 `messages.content` 后在热区永不二次改写）。
        """
        raw = raw_output or ""
        threshold = resolve_scrub_char_threshold(
            tool_name=tool_name,
            primary_provider_info=primary_provider_info,
        )
        if len(raw) <= threshold:
            return {
                "was_scrubbed": False,
                "content": raw,
                "compressed_content": None,
                "ref_id": None,
                "raw_tokens": estimate_tokens(raw),
                "scrubbed_tokens": estimate_tokens(raw),
                "saved_tokens": 0,
                "scrubber_metrics": None,
            }

        ref_id = f"tool_ref_{uuid.uuid4().hex[:8]}"
        total_lines = max(1, len(raw.splitlines()))
        raw_tokens = estimate_tokens(raw)

        # 检查是否包含严重报错堆栈
        has_abnormal_error = any(
            kw in raw.lower()
            for kw in ("traceback", "error:", "exception:", "syntaxerror", "failed", "errno", "denied")
        )

        # 两级漏斗机制（彻底扭转清洗倒挂亏损）：
        # 1. 第一级：<= 3,500 字符，或无明确报错特征，100% 走纯 Python 确定性提取器（0 算力、0 延迟、0 Token 开销）
        # 2. 第二级：仅当 > 3,500 字符且包含明确报错堆栈时，才调度无状态轻模型，且采样限制在 1,500 字符内
        should_use_llm = len(raw) > 3500 and has_abnormal_error

        scrubbed_summary: str | None = None
        scrubber_metrics: dict[str, Any] | None = None

        try:
            if should_use_llm:
                from ..service.engine_pool import engine_pool
                from ..core.provider_adapter import ProviderAdapter

                light_candidates = await ProviderAdapter.get_enabled_providers_for_role("light")
                # 筛选非 cooldown 的可用轻节点
                selected_light = light_candidates[0] if light_candidates else None

                can_use_llm = selected_light is not None
                if can_use_llm and selected_light:
                    light_url = str(selected_light.get("base_url") or "").strip().rstrip("/")
                    primary_url = str((primary_provider_info or {}).get("base_url") or "").strip().rstrip("/")
                    if is_local_provider(selected_light) and light_url:
                        slots_info = await ProviderAdapter.probe_local_slots(light_url)
                        total_slots = int(slots_info.get("total_slots") or 0)
                        # 单槽位且与主推理共用同一个本地服务实例，跳过 LLM 清洗以保住主会话 KV Cache
                        if total_slots == 1 and (not primary_url or primary_url == light_url):
                            logger.debug(
                                f"[ContextScrubber] 检测到本地服务 {light_url} 为单槽位 (-np 1)，"
                                "自动启用零显存开销确定性脱水提取器以保护主会话 KV Cache"
                            )
                            can_use_llm = False

                if can_use_llm and selected_light:
                    import json as _json

                    models_list = selected_light.get("models") or _json.loads(selected_light.get("models_json") or "[]")
                    default_fallback = (
                        "gemini-2.5-flash"
                        if selected_light.get("provider_type") in ("google_oauth", "gemini_api", "google_pool")
                        else ""
                    )
                    light_model = ProviderAdapter.pick_light_model(models_list, fallback=default_fallback)
                    if not light_model:
                        can_use_llm = False

                if can_use_llm and selected_light:
                    # 采样严格截断在 1,500 字符以内（头部 600 + 尾部 900），单次开销降至数百 Token 以内
                    if len(raw) > 1500:
                        sample_text = (
                            raw[:600]
                            + f"\n\n... [中间省略 {len(raw) - 1500:,} 字符] ...\n\n"
                            + raw[-900:]
                        )
                    else:
                        sample_text = raw

                    scrub_prompt = (
                        f"工具 `{tool_name}`（参数: {tool_args_summary[:120]}）报错堆栈输出截选（共 {total_lines} 行）：\n\n"
                        f"{sample_text}\n\n"
                        "请用中文提炼出**报错根因、触发异常的函数/行号或文件路径**，"
                        "严格控制在 **120 字以内**，纯文本单行输出，绝不废话："
                    )

                    async def _run_scrubber() -> tuple[str, dict[str, Any] | None]:
                        acc = ""
                        captured_metrics: dict[str, Any] | None = None
                        async for evt in engine_pool.execute_stream_with_failover(
                            conversation_id=conversation_id,
                            user_prompt=scrub_prompt,
                            model_name=light_model,
                            thinking_level="low",
                            system_override="你是高密度日志与工具输出脱水摘要器，只输出最关键的客观事实，绝不废话。",
                            temperature=0.1,
                            pre_built_history=[],
                            task_role="light",
                            incognito=incognito,
                        ):
                            etype = evt.get("type")
                            if etype == "chunk":
                                acc += evt.get("content", "")
                            elif etype == "step_metrics":
                                captured_metrics = evt.get("metrics")
                            elif etype == "done":
                                acc = evt.get("full_content", acc)
                                break
                            elif etype == "error":
                                return "", None
                        return acc.strip(), captured_metrics

                    llm_out, captured = await asyncio.wait_for(_run_scrubber(), timeout=6.0)
                    if llm_out:
                        clean_llm = re.sub(r"\s+", " ", llm_out).strip()
                        scrubbed_summary = clean_llm[:220]
                        scrubber_metrics = captured
        except Exception as e:
            logger.debug(f"[ContextScrubber] 轻模型旁路清洗超时或跳过，回退确定性提取器: {e}")

        if not scrubbed_summary:
            scrubbed_summary = _deterministic_scrub(tool_name, raw, max_chars=460)

        # 持久化或内存注册原文引用
        compact_message = (
            f"[工具输出已截流脱水 | Ref: {ref_id} | 原始长度: {len(raw):,} 字符 ({total_lines} 行)]\n"
            f"核心摘要: {scrubbed_summary}\n"
            f'(提示: 若仅需查找少量局部细节，可调 `read_tool_ref(ref_id="{ref_id}", keyword="...")` 精准检索；'
            f"若需处理/分析超大数据文件，请直接用 Python 脚本读取原物理文件处理，严禁从第 1 行起反复调用 `read_tool_ref` 分页通读！)"
        )
        scrubbed_tokens = estimate_tokens(compact_message)
        saved_tokens = max(0, raw_tokens - scrubbed_tokens)

        if incognito:
            _store_incognito_ref(ref_id=ref_id, tool_name=tool_name, raw_content=raw)
        else:
            await ToolRefRepository.create_ref(
                ref_id=ref_id,
                conversation_id=conversation_id,
                turn_id=int(turn_id),
                tool_name=tool_name,
                raw_content=raw,
                raw_tokens_est=raw_tokens,
                scrubbed_summary=scrubbed_summary,
                scrubbed_tokens_est=scrubbed_tokens,
                tool_args_summary=tool_args_summary,
            )

        return {
            "was_scrubbed": True,
            "content": compact_message,
            "compressed_content": compact_message,
            "ref_id": ref_id,
            "raw_tokens": raw_tokens,
            "scrubbed_tokens": scrubbed_tokens,
            "saved_tokens": saved_tokens,
            "scrubber_metrics": scrubber_metrics,
        }

