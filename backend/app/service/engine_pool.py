"""
智能多账号引擎池调度器（支持标准 Google OAuth 个人通道 + Antigravity CLI 双引擎）
实现：429/认证失效时多账号自动冷却切换，支持任何 Gmail 个人 OAuth 账号免开通直接使用。
"""
import asyncio
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import AsyncGenerator, Any

from ..core.config import settings
from ..core.logging_config import logger
from ..core.token_metrics import TokenMetricsCollector
from ..agent.context_compressor import estimate_messages_tokens, estimate_tokens
from ..agent.tool_protocol import ThinkStreamSplitter
from ..repository.account_repository import AccountRepository
from ..repository.message_repository import MessageRepository
from .quota_monitor import quota_monitor
from .agent_runner import run_cli_stream
from .oauth_service import oauth_service
from .gemini_client import GeminiClient, GeminiAPIException
from .audit_service import audit_service
from .attachment_service import attachment_service


class EnginePoolService:
    """智能引擎调度中心（双引擎：标准 Google OAuth 个人通道 + CLI）"""

    @staticmethod
    def read_system_profile() -> str:
        """读取全局个人知识/技术栈背景档案"""
        profile_path = Path(settings.profile_path)
        if profile_path.exists():
            try:
                return profile_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"读取全局个人档案失败: {e}")
        return ""

    @staticmethod
    async def resolve_primary_provider_info(model_name: str = "") -> dict[str, Any] | None:
        """预解析当前优先命中的算力通道元信息（用于 ContextBuilder 判断本地/云端坍缩阈值与 /tokenize）"""
        try:
            from ..core.provider_adapter import provider_adapter

            providers = await provider_adapter.get_enabled_providers_for_role("primary")
            for prov in providers:
                ptype = prov.get("provider_type")
                if ptype == "google_pool":
                    continue
                prov_models = prov.get("models") or []
                is_auto_primary = (model_name in ("", "default")) and bool(prov_models)
                if (
                    model_name in prov_models
                    or ptype == "google_oauth"
                    or int(prov.get("priority", 10)) < 1
                    or is_auto_primary
                ):
                    return prov
        except Exception:
            pass
        return None

    @staticmethod
    def _is_cli_account(account: Any) -> bool:
        """判断账号是否必须经本地 Antigravity/Gemini CLI 子进程执行（非标准 REST 凭据）"""
        auth_type = str(getattr(account, "auth_type", "") or "").strip()
        return auth_type not in ("oauth_google_one", "oauth_personal", "api_key")

    @staticmethod
    def _is_google_model_name(model_name: str | None) -> bool:
        """判断模型标识是否属于 Google/Gemini 原生生态（用于阻止自定义模型报错后误跌入 CLI）"""
        ml = (model_name or "").strip().lower()
        return (not ml) or ml == "default" or ("gemini" in ml) or ml.startswith("google")

    @classmethod
    async def execute_stream_with_failover(
        cls,
        conversation_id: str,
        user_prompt: str,
        model_name: str = "gemini-2.5-pro",
        thinking_level: str = "high",
        system_override: str | None = None,
        temperature: float = 0.7,
        max_retries: int | None = None,
        work_dir: str | None = None,
        context_rounds: int = 10,
        attachments: list[Any] | None = None,
        pre_built_history: list[dict[str, Any]] | None = None,
        task_role: str = "primary",
        attachments_inline_only: bool = False,
        turn_id: int = 1,
        step_index: int = 0,
        agent_role: str | None = None,
        tail_injection: str = "",
        incognito: bool = False,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        带零感知热故障转移、三区尾注动态注入与全通道真实 Token/KV Cache 采集的流式生成执行器。
        """
        retries_left = max_retries or settings.auto_failover_retry
        effective_agent_role = agent_role or ("scrubber" if task_role == "light" else "primary")

        if pre_built_history is not None:
            effective_history_dicts = [dict(item) for item in pre_built_history]
        else:
            history_limit = max(100, context_rounds * 4) if context_rounds > 0 else 100
            history_messages = await MessageRepository.get_by_conversation(
                conversation_id,
                limit=history_limit,
                from_latest=True,
            )
            slice_count = max(0, context_rounds * 2) if context_rounds > 0 else 0
            effective_history_dicts = [
                {"role": m.role, "content": m.content}
                for m in (history_messages[-slice_count:] if slice_count > 0 else [])
            ]

        # 检查是否有图片、PDF、Office 或文本/代码附件，装配多模态 inline_data 与安全正文预览
        attachment_guidance = ""
        inline_blobs: list[dict[str, str]] = []
        has_visual_or_pdf = False

        if attachments:
            from pathlib import Path

            att_sections: list[str] = []
            visual_refs: list[str] = []
            for att in attachments:
                att_dict = att if isinstance(att, dict) else att.model_dump()
                file_path = att_dict.get("file_path") or ""
                rel_path = att_dict.get("rel_path") or ""
                if not rel_path and work_dir and file_path:
                    try:
                        rel_calc = Path(file_path).resolve().relative_to(Path(work_dir).resolve())
                        rel_path = f"./{str(rel_calc).replace(chr(92), '/')}"
                    except Exception:
                        rel_path = ""
                mime_type = att_dict.get("mime_type") or "application/octet-stream"
                name = att_dict.get("name") or "附件"
                _, inferred_cat, canonical_mime = attachment_service.classify_extension(name)
                category = att_dict.get("category") or inferred_cat or ("image" if mime_type.startswith("image/") else "file")
                effective_mime = canonical_mime or mime_type

                rel_line = f"- 工作区相对路径: `{rel_path}`\n" if rel_path else ""

                if file_path and os.path.exists(file_path):
                    if category == "image" or effective_mime.startswith("image/"):
                        has_visual_or_pdf = True
                        if attachments_inline_only:
                            visual_refs.append(f"`{name}` ({rel_path or file_path})")
                        else:
                            att_sections.append(
                                f"【用户附带上传的本地图片文件】:\n"
                                f"- 文件名: `{name}`\n"
                                f"{rel_line}"
                                f"- 磁盘绝对路径: `{file_path}`\n"
                                f"- 指引: 请结合多模态图片画面分析；如后续需通过脚本二次处理该图片，必须使用上述真实沙盒路径，严禁自行生成模拟图片。"
                            )
                        try:
                            import base64
                            with open(file_path, "rb") as img_f:
                                raw_b = img_f.read()
                            if len(raw_b) <= attachment_service.MAX_IMAGE_BYTES:
                                b64 = base64.b64encode(raw_b).decode("utf-8")
                                inline_blobs.append({"mime_type": effective_mime, "data": b64})
                        except Exception as e:
                            logger.warning(f"读取图片 base64 失败: {e}")

                    elif category == "pdf" or effective_mime == "application/pdf":
                        has_visual_or_pdf = True
                        if attachments_inline_only:
                            visual_refs.append(f"`{name}` ({rel_path or file_path})")
                        else:
                            ctx_info = attachment_service.extract_attachment_context(file_path, effective_mime, name)
                            att_sections.append(
                                f"【用户附带上传的 PDF 文档附件】:\n"
                                f"- 文件名: `{name}`\n"
                                f"{rel_line}"
                                f"- 磁盘绝对路径: `{file_path}`\n"
                                f"- 指引: 若需通过脚本抽数，必须直接读取上述真实路径，严禁自行编造文档内容或创建测试替代文件。\n"
                                f"- 说明: {ctx_info.get('preview_text', '')}"
                            )
                        try:
                            import base64
                            with open(file_path, "rb") as pdf_f:
                                raw_b = pdf_f.read()
                            if len(raw_b) <= attachment_service.MAX_PDF_BYTES:
                                b64 = base64.b64encode(raw_b).decode("utf-8")
                                inline_blobs.append({"mime_type": "application/pdf", "data": b64})
                        except Exception as e:
                            logger.warning(f"读取 PDF base64 失败: {e}")

                    elif not attachments_inline_only:
                        ctx_info = attachment_service.extract_attachment_context(file_path, effective_mime, name)
                        preview_txt = ctx_info.get("preview_text") or "(未能提取文本预览)"
                        extractor_name = ctx_info.get("extractor") or "none"
                        att_sections.append(
                            f"【用户附带上传的本地真实文档附件】:\n"
                            f"- 文件名: `{name}` (分类: {category}, 解析引擎: {extractor_name})\n"
                            f"{rel_line}"
                            f"- 磁盘绝对路径: `{file_path}`\n"
                            f"- ⚠️ 真实文件与全量读取约束: 该文件已真实落盘于上述路径。若预览仅展示前几行或大表被截断，严禁仅凭预览推断全表或自行创建 Mock/示例数据替代，必须使用脚本直接读取上述路径全量解析！\n"
                            f"- 文档内容预览:\n```text\n{preview_txt}\n```"
                        )
                elif not attachments_inline_only:
                    att_sections.append(
                        f"【用户提及附件】: `{name}` (本地磁盘暂未定位到文件 `{file_path}`；严禁自造 Mock 文件替代，请核查沙盒清单或向用户报错确认)"
                    )

            if attachments_inline_only and visual_refs:
                attachment_guidance = (
                    "\n\n[多模态视觉附件续传: 本步继续附带参考文件 "
                    + "、".join(visual_refs)
                    + " 的多模态输入（沙盒真实路径保持不变），请结合视觉内容与前置工具输出直接推理并推进任务。]"
                )
            elif att_sections:
                attachment_guidance = "\n\n" + "\n\n".join(att_sections)

        prompt_with_attachments = user_prompt + attachment_guidance

        # NOTE: Zone 3 尾部注入挂接策略：
        # - step_index == 0 时挂在当前 user_prompt 末尾；
        # - step_index >= 1（同轮多步 ReAct）时挂回本轮最初那条 user 消息发送副本末尾，
        #   既实现同轮多步 100% 前缀字节冻结，又确保跨轮次时彻底丢弃不写库、不污染 Zone 2！
        clean_tail = (tail_injection or "").strip()
        if clean_tail:
            tail_block = f"\n\n<agenthub_tail_context>\n{clean_tail}\n</agenthub_tail_context>"
            attached_to_turn_user = False
            if int(step_index or 0) > 0:
                for h_item in effective_history_dicts:
                    if h_item.get("role") == "user" and int(h_item.get("turn_id") or 1) == int(turn_id or 1):
                        h_item["content"] = str(h_item.get("content") or "") + tail_block
                        attached_to_turn_user = True
                        break
            if not attached_to_turn_user:
                prompt_with_attachments = prompt_with_attachments + tail_block

        # 组装消息列表 (官方 Gemini REST API 格式，history 中的附件锚点同样展开为真实路径档案)
        contents: list[dict[str, Any]] = []
        for msg in effective_history_dicts:
            r_raw = msg.get("role", "user")
            role = "model" if r_raw in ("model", "assistant") else "user"
            prefix = "[工具执行结果]: " if r_raw == "tool" else ""
            raw_content = str(msg.get("content", "") or "")
            if r_raw == "user":
                raw_content = attachment_service.render_attachments_for_llm_history(raw_content)
            contents.append({"role": role, "parts": [{"text": f"{prefix}{raw_content}"}]})

        # 用户当前提问 parts（支持图片与 PDF 原生多模态 inline_data + 完整文本附件指引）
        user_parts: list[dict[str, Any]] = []
        for blob in inline_blobs:
            user_parts.append({"inline_data": {"mime_type": blob["mime_type"], "data": blob["data"]}})
        user_parts.append({"text": prompt_with_attachments})
        contents.append({"role": "user", "parts": user_parts})

        # 组装 CLI 用的历史纯文本上下文
        lines = []
        for msg in effective_history_dicts:
            r_raw = msg.get("role", "user")
            if r_raw in ("model", "assistant"):
                role_label = "Gemini"
            elif r_raw == "tool":
                role_label = "终端/工具输出"
            else:
                role_label = "用户"
            raw_content = str(msg.get("content", "") or "")
            if r_raw == "user":
                raw_content = attachment_service.render_attachments_for_llm_history(raw_content)
            lines.append(f"[{role_label}]: {raw_content}")
        context_text = "\n".join(lines) + ("\n\n---\n[当前步骤指令]: " if lines else "")
        full_cli_prompt = context_text + prompt_with_attachments

        system_instruction = system_override or cls.read_system_profile()

        # 针对绘画/生图意图增强 Prompt 指导：要求输出 Markdown 图片以及视觉艺术解析
        image_keywords = ["画图", "画个图", "生成图片", "绘制", "画一张", "插画", "帮我画", "生图"]
        if any(kw in user_prompt for kw in image_keywords):
            import random
            rand_seed = random.randint(1000, 999999)
            image_guidance = (
                "\n[视觉绘图增强指示]: 用户希望生成或绘制图像。"
                "\n1. 请根据用户的描述，设计一段高质量、细节丰富、包含光影质感与艺术风格的英文绘画 Prompt（如 prompt_en）。"
                f"\n2. 在回复正文中必须直接输出免限流极速 Markdown 图像标签（必须带上 model=turbo&seed={rand_seed}&nologo=true 参数，避免触发 45 秒限流）："
                f"\n   ![作品描述](https://image.pollinations.ai/prompt/{{url_encoded_prompt_en}}?model=turbo&seed={rand_seed}&width=1024&height=1024&nologo=true)"
                "\n3. 随后用中文提供画面的构图意境、光影色彩及艺术风格解析。"
            )
            system_instruction = (system_instruction + "\n" + image_guidance) if system_instruction else image_guidance

        if system_instruction:
            full_cli_prompt = f"[系统指令]: {system_instruction}\n\n{full_cli_prompt}"

        # ──────────────────── 优先检查多协议算力池 (Google OAuth / OpenAI兼容 / DeepSeek / Ollama / Anthropic / Gemini API) ────────────────────
        attempted_custom_errors: list[str] = []
        exact_custom_match = False
        try:
            from ..core.provider_adapter import provider_adapter
            from ..core.oauth_google import google_oauth_manager

            custom_providers = await provider_adapter.get_enabled_providers_for_role(task_role)
            if not custom_providers and task_role == "light":
                # 若未单独配置后台轻量模型，优雅降级调度 primary 节点中后备节点
                custom_providers = await provider_adapter.get_enabled_providers_for_role("primary")
                custom_providers.reverse()

            schedulable_custom = [
                p for p in (custom_providers or []) if p.get("provider_type") != "google_pool"
            ]
            exact_custom_match = bool(model_name) and any(
                model_name in (p.get("models") or []) for p in schedulable_custom
            )
            prev_failed_prov_name = None

            for prov in schedulable_custom:
                if cancel_event and cancel_event.is_set():
                    yield {"type": "cancelled", "content": "已中止生成"}
                    return
                ptype = prov.get("provider_type")
                prov_models = prov.get("models") or []
                is_light_fallback = (
                    task_role == "light"
                    and (not model_name or not exact_custom_match)
                    and bool(prov_models)
                )
                is_auto_primary = (
                    task_role == "primary"
                    and (model_name in ("", "default"))
                    and bool(prov_models)
                )

                # 当模型名显式匹配、或为后台轻量通配/空占位回退/OAuth/高优通道时，优先路由多协议供应商
                if (
                    model_name in prov_models
                    or ptype == "google_oauth"
                    or int(prov.get("priority", 10)) < 1
                    or is_light_fallback
                    or is_auto_primary
                ):
                    prov_name = prov.get("name", ptype)
                    if model_name and (model_name in prov_models or ptype == "google_oauth"):
                        target_m = model_name
                    elif task_role == "light":
                        target_m = provider_adapter.pick_light_model(
                            prov_models,
                            fallback=("gemini-2.5-flash" if ptype in ("google_oauth", "gemini_api") else ""),
                        )
                    else:
                        target_m = prov_models[0] if prov_models else (
                            model_name or ("gemini-2.5-pro" if ptype in ("google_oauth", "gemini_api") else "")
                        )

                    if not target_m:
                        continue
                    if prev_failed_prov_name:
                        yield {
                            "type": "relay",
                            "from_account": prev_failed_prov_name,
                            "to_account": prov_name,
                            "reason": "429_rate_limit",
                        }
                    else:
                        yield {"type": "start", "account": prov_name, "model": target_m}
                    non_multimodal_note = (
                        "\n\n[通道提示: 当前使用纯文本兼容协议接口，图片/PDF 二进制视觉字节流未直接传输；"
                        "若需直接识图或读取扫描版 PDF，请切换至原生多模态 Gemini 模型，或在任务模式按上方磁盘绝对路径调用本地工具读取。]"
                        if has_visual_or_pdf
                        else ""
                    )
                    sanitized_history = [
                        {
                            **hm,
                            "content": attachment_service.render_attachments_for_llm_history(str(hm.get("content") or ""))
                            if hm.get("role") == "user"
                            else hm.get("content", ""),
                        }
                        for hm in effective_history_dicts
                    ]
                    custom_msgs = sanitized_history + [{"role": "user", "content": prompt_with_attachments + non_multimodal_note}]
                    est_prompt_tokens = (
                        estimate_tokens(system_instruction)
                        + (8 if system_instruction else 0)
                        + estimate_messages_tokens(custom_msgs)
                    )
                    collector = TokenMetricsCollector(
                        turn_id=turn_id,
                        step_index=step_index,
                        agent_role=effective_agent_role,
                        provider_info=prov,
                        model_name=target_m,
                        estimated_prompt_tokens=est_prompt_tokens,
                    )
                    full_custom_text = ""
                    full_thinking_text = ""
                    think_splitter = ThinkStreamSplitter() if task_role == "primary" else None
                    stream_completed = False
                    should_persist_on_abort = False

                    def _split_custom_segments(raw_piece: str) -> list[tuple[str, str]]:
                        if think_splitter is None:
                            return [("content", raw_piece)] if raw_piece else []
                        return think_splitter.feed(raw_piece)

                    try:
                        if ptype == "google_oauth":
                            valid_token = await google_oauth_manager.ensure_valid_access_token(prov)
                            custom_downgrades: list[dict[str, Any]] = []

                            def on_custom_downgrade(from_m: str, to_m: str, reason: str):
                                custom_downgrades.append({
                                    "type": "model_downgrade",
                                    "original_model": from_m,
                                    "current_model": to_m,
                                    "reason": reason,
                                    "account": prov_name,
                                    "message": f"检测到原模型「{from_m}」当前配额耗尽或容量不足 ({reason})，已自动切换至高可用模型「{to_m}」继续生成。",
                                })

                            should_persist_on_abort = True
                            async for ch in GeminiClient.stream_generate_content(
                                api_key=valid_token,
                                model_name=target_m,
                                contents=contents,
                                system_instruction=system_instruction,
                                thinking_level=thinking_level,
                                temperature=temperature,
                                auth_type="oauth_google_one",
                                on_downgrade=on_custom_downgrade,
                                on_usage=lambda u: collector.record_raw_usage(usage=u),
                                on_first_byte=collector.mark_first_byte,
                            ):
                                if cancel_event and cancel_event.is_set():
                                    break
                                while custom_downgrades:
                                    yield custom_downgrades.pop(0)
                                for seg_kind, seg_text in _split_custom_segments(ch):
                                    collector.mark_chunk(seg_text)
                                    if seg_kind == "thinking":
                                        full_thinking_text += seg_text
                                        yield {"type": "thinking_chunk", "content": seg_text, "account": prov_name}
                                    else:
                                        full_custom_text += seg_text
                                        yield {"type": "chunk", "content": seg_text, "account": prov_name}

                            while custom_downgrades:
                                yield custom_downgrades.pop(0)
                        elif ptype in ("openai_compat", "ollama", "llama_server"):
                            default_local_url = (
                                "http://127.0.0.1:8080/v1"
                                if ptype == "llama_server"
                                else ("http://127.0.0.1:11434/v1" if ptype == "ollama" else "")
                            )
                            default_local_key = "local" if ptype in ("ollama", "llama_server") else ""
                            should_persist_on_abort = True
                            async for ch in provider_adapter.stream_openai_compatible(
                                base_url=prov.get("base_url") or default_local_url,
                                api_key=prov.get("api_key") or default_local_key,
                                model_name=target_m,
                                messages=custom_msgs,
                                system_instruction=system_instruction,
                                temperature=temperature,
                                on_usage=lambda u, t: collector.record_raw_usage(usage=u, timings=t),
                                emit_thinking_tags=(task_role == "primary"),
                            ):
                                if cancel_event and cancel_event.is_set():
                                    break
                                for seg_kind, seg_text in _split_custom_segments(ch):
                                    collector.mark_chunk(seg_text)
                                    if seg_kind == "thinking":
                                        full_thinking_text += seg_text
                                        yield {"type": "thinking_chunk", "content": seg_text, "account": prov_name}
                                    else:
                                        full_custom_text += seg_text
                                        yield {"type": "chunk", "content": seg_text, "account": prov_name}
                        elif ptype == "anthropic":
                            should_persist_on_abort = True
                            async for ch in provider_adapter.stream_anthropic(
                                base_url=prov.get("base_url") or "",
                                api_key=prov.get("api_key") or "",
                                model_name=target_m,
                                messages=custom_msgs,
                                system_instruction=system_instruction,
                                temperature=temperature,
                                on_usage=lambda u, t: collector.record_raw_usage(usage=u, timings=t),
                                emit_thinking_tags=(task_role == "primary"),
                            ):
                                if cancel_event and cancel_event.is_set():
                                    break
                                for seg_kind, seg_text in _split_custom_segments(ch):
                                    collector.mark_chunk(seg_text)
                                    if seg_kind == "thinking":
                                        full_thinking_text += seg_text
                                        yield {"type": "thinking_chunk", "content": seg_text, "account": prov_name}
                                    else:
                                        full_custom_text += seg_text
                                        yield {"type": "chunk", "content": seg_text, "account": prov_name}
                        elif ptype == "gemini_api" and prov.get("api_key"):
                            should_persist_on_abort = True
                            async for ch in GeminiClient.stream_generate_content(
                                api_key=prov["api_key"],
                                model_name=target_m,
                                contents=contents,
                                system_instruction=system_instruction,
                                thinking_level=thinking_level,
                                temperature=temperature,
                                auth_type="api_key",
                                on_usage=lambda u: collector.record_raw_usage(usage=u),
                                on_first_byte=collector.mark_first_byte,
                            ):
                                if cancel_event and cancel_event.is_set():
                                    break
                                for seg_kind, seg_text in _split_custom_segments(ch):
                                    collector.mark_chunk(seg_text)
                                    if seg_kind == "thinking":
                                        full_thinking_text += seg_text
                                        yield {"type": "thinking_chunk", "content": seg_text, "account": prov_name}
                                    else:
                                        full_custom_text += seg_text
                                        yield {"type": "chunk", "content": seg_text, "account": prov_name}

                        if think_splitter is not None:
                            for seg_kind, seg_text in think_splitter.flush():
                                collector.mark_chunk(seg_text)
                                if seg_kind == "thinking":
                                    full_thinking_text += seg_text
                                    yield {"type": "thinking_chunk", "content": seg_text, "account": prov_name}
                                else:
                                    full_custom_text += seg_text
                                    yield {"type": "chunk", "content": seg_text, "account": prov_name}

                        if cancel_event and cancel_event.is_set():
                            should_persist_on_abort = bool(full_custom_text or full_thinking_text)
                            yield {"type": "cancelled", "content": "已中止生成"}
                            return

                        if full_custom_text or full_thinking_text:
                            stream_completed = True
                            step_res = await collector.persist(
                                conversation_id=conversation_id,
                                incognito=incognito,
                                aborted=False,
                            )
                            total_spent = max(1, step_res.prompt_tokens + step_res.completion_tokens)
                            await AccountRepository.record_usage(prov_name, tokens_spent=total_spent)
                            yield {"type": "step_metrics", "metrics": step_res.to_dict()}
                            yield {
                                "type": "done",
                                "full_content": full_custom_text,
                                "thinking": full_thinking_text,
                                "account": prov_name,
                                "usage": {
                                    "promptTokenCount": step_res.prompt_tokens,
                                    "candidatesTokenCount": step_res.completion_tokens,
                                    "cachedContentTokenCount": step_res.cached_tokens,
                                    "totalTokenCount": total_spent,
                                },
                            }
                            return

                        stream_completed = True
                        empty_msg = f"[{prov_name}] 模型 [{target_m}] 返回空响应"
                        attempted_custom_errors.append(empty_msg)
                        prev_failed_prov_name = prov_name
                        logger.warning(f"自定义算力通道 {empty_msg}，继续尝试下一节点")
                    except GeminiAPIException as g_err:
                        should_persist_on_abort = False
                        attempted_custom_errors.append(f"[{prov_name}] {g_err}")
                        if g_err.status_code in (429, 503):
                            await provider_adapter.mark_provider_cooldown(prov["id"], cooldown_seconds=60)
                        prev_failed_prov_name = prov_name
                        if cancel_event and cancel_event.is_set():
                            should_persist_on_abort = bool(full_custom_text or full_thinking_text)
                            yield {"type": "cancelled", "content": "已中止生成"}
                            return

                        if full_custom_text or full_thinking_text:
                            collector.reset_stream_buffer()
                            if think_splitter is not None:
                                think_splitter.reset()
                            full_custom_text = ""
                            full_thinking_text = ""
                            yield {
                                "type": "stream_reset",
                                "reason": "mid_flight_retry",
                                "message": "检测到流式传输异常，正在切换下一节点无缝重连...",
                            }
                        logger.warning(f"算力通道 [{prov_name}] 触发限流或异常 ({g_err})，<50ms 无缝接力至下一节点")
                    except Exception as custom_err:
                        should_persist_on_abort = False
                        err_text = str(custom_err)
                        attempted_custom_errors.append(f"[{prov_name}] {err_text}")
                        # 仅明确命中 HTTP 429 或限流语义才冷却；400/422 参数错误禁止触发冷却以免误判全池耗尽
                        if "(429)" in err_text or "rate limit" in err_text.lower() or "too many requests" in err_text.lower():
                            await provider_adapter.mark_provider_cooldown(prov["id"], cooldown_seconds=60)
                        prev_failed_prov_name = prov_name
                        if cancel_event and cancel_event.is_set():
                            should_persist_on_abort = bool(full_custom_text or full_thinking_text)
                            yield {"type": "cancelled", "content": "已中止生成"}
                            return

                        if full_custom_text or full_thinking_text:
                            collector.reset_stream_buffer()
                            if think_splitter is not None:
                                think_splitter.reset()
                            full_custom_text = ""
                            full_thinking_text = ""
                            yield {
                                "type": "stream_reset",
                                "reason": "mid_flight_retry",
                                "message": "检测到通道断流，正在尝试无缝切换至下一节点...",
                            }
                        logger.warning(f"自定义算力通道 [{prov_name}] 异常 ({custom_err})，自动故障转移至下一节点")
                    finally:
                        # 用户中途点“停止生成”(GeneratorExit / CancelledError) 时强制保底写入 is_estimated=True 记录
                        if not stream_completed and should_persist_on_abort:
                            try:
                                await collector.persist(
                                    conversation_id=conversation_id,
                                    incognito=incognito,
                                    aborted=True,
                                )
                            except Exception:
                                pass
        except Exception as e:
            logger.warning(f"检查多协议供应商异常跳过: {e}")

        # 若多协议供应商已显式尝试并报错，严格阻止跨生态跌入 Google CLI 子进程或被无关模型误导覆盖真实错误
        if attempted_custom_errors:
            should_abort_before_google = (
                task_role == "light"
                or exact_custom_match
                or (bool(model_name) and not cls._is_google_model_name(model_name))
            )
            if not should_abort_before_google:
                # 若只是未指定通用模型名（或 Gemini 系列），仅当池内存在活跃 Google REST 账号时才允许继续接力；纯 CLI/空池直接透传真实报错
                try:
                    all_accs = await AccountRepository.get_all()
                    now_str_check = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    has_rest_google = any(
                        not cls._is_cli_account(a)
                        and (
                            a.status == "active"
                            or (a.status == "cooldown" and str(a.cooldown_until or "") <= now_str_check)
                        )
                        for a in all_accs
                    )
                    if not has_rest_google:
                        should_abort_before_google = True
                except Exception:
                    should_abort_before_google = True

            if should_abort_before_google:
                err_summary = attempted_custom_errors[-1]
                if task_role == "light":
                    logger.info(f"后台轻量多协议通道不可用，已安全跳过 (不唤醒 CLI): {err_summary}")
                else:
                    logger.error(f"多协议算力通道调用失败: {'; '.join(attempted_custom_errors)}")
                yield {
                    "type": "error",
                    "content": f"【AgentHub】算力通道调用失败：{err_summary}",
                }
                return

        google_model_name = model_name or ("gemini-2.5-flash" if task_role == "light" else "gemini-2.5-pro")
        relayed_count = 0
        last_failed_account = None

        while retries_left > 0:
            if cancel_event and cancel_event.is_set():
                yield {"type": "cancelled", "content": "已中止生成"}
                return

            account = await AccountRepository.get_active_candidate()
            # NOTE: 后台轻量旁路 (light) 严禁拉起重量级 CLI 子进程；若首选为 CLI 账号，优先改用可用 Google REST 账号，无则优雅跳过
            if task_role == "light" and account is not None and cls._is_cli_account(account):
                rest_fallback = None
                try:
                    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    for cand in await AccountRepository.get_all():
                        if cls._is_cli_account(cand):
                            continue
                        if cand.status == "active" or (
                            cand.status == "cooldown" and str(cand.cooldown_until or "") <= now_utc
                        ):
                            rest_fallback = cand
                            break
                except Exception:
                    rest_fallback = None
                account = rest_fallback

            if not account:
                if attempted_custom_errors:
                    err_summary = attempted_custom_errors[-1]
                    if task_role == "light":
                        logger.info(f"多协议通道不可用且无可用轻量兜底，已跳过: {err_summary}")
                    else:
                        logger.error(
                            f"多协议算力通道调用失败且无可用 Google 兜底账号: {'; '.join(attempted_custom_errors)}"
                        )
                    yield {
                        "type": "error",
                        "content": f"【AgentHub】算力通道调用失败：{err_summary}",
                    }
                    return

                total_google_accounts: int | None = None
                try:
                    all_google = await AccountRepository.get_all()
                    total_google_accounts = len(all_google)
                except Exception:
                    all_google = []
                    total_google_accounts = None

                if task_role == "light":
                    logger.info(
                        f"后台轻量推理跳过：当前无可用的非 CLI 快速通道 (model={model_name or 'auto'}, google_accounts={total_google_accounts})"
                    )
                    yield {
                        "type": "error",
                        "content": "【AgentHub】当前无可用的后台轻量快速通道，已静默跳过。",
                    }
                    return

                if total_google_accounts == 0:
                    logger.error(
                        f"算力调度失败：未找到模型 [{model_name or 'auto'}] 对应的已启用通道，且当前未配置任何 Google 账号。"
                    )
                    yield {
                        "type": "error",
                        "content": f"【AgentHub】未找到模型 [{model_name or 'auto'}] 对应的已启用算力通道，请前往「账号与算力池」检查通道或模型配置。",
                    }
                    return

                logger.error("算力池告警：所有账号均处于 Cooldown 或已用尽配额！")
                yield {
                    "type": "error",
                    "content": "【AgentHub】当前所有账号均处于冷却或配额用尽状态，请稍候再试或添加新账号。",
                }
                return

            tracker = quota_monitor.get_tracker(account.name, account.rpm_limit)

            if tracker.is_exhausted:
                cooldown_until = (
                    datetime.now(timezone.utc) + timedelta(seconds=15)
                ).strftime("%Y-%m-%d %H:%M:%S")
                await AccountRepository.mark_cooldown(account.name, cooldown_until)
                retries_left -= 1
                continue

            logger.info(f"调度账号 -> {account.name} (类型: {account.auth_type}, 优先级: {account.priority})")

            if relayed_count > 0:
                await audit_service.log(
                    event_type="account_switch",
                    actor="engine_pool",
                    detail={"reason": "429_or_auth_failover", "model": google_model_name, "from": last_failed_account, "to": account.name},
                    account_name=account.name,
                    approved=True
                )
                yield {
                    "type": "relay",
                    "account": account.name,
                    "previous_account": last_failed_account,
                    "message": f"[由 {account.name} 无缝接力生成]"
                }
            else:
                await audit_service.log(
                    event_type="chat_dispatch",
                    actor="user",
                    detail={"model": google_model_name, "thinking_level": thinking_level, "auth_type": account.auth_type},
                    account_name=account.name,
                    approved=True
                )
                yield {
                    "type": "start",
                    "account": account.name,
                    "model": google_model_name
                }

            tracker.record_request_start()
            full_response_text = ""
            full_thinking_text = ""
            legacy_splitter = ThinkStreamSplitter() if task_role == "primary" else None
            encountered_rate_limit = False
            encountered_auth_error = False
            start_time = time.time()

            def _split_legacy_segments(raw_piece: str) -> list[tuple[str, str]]:
                if legacy_splitter is None:
                    return [("content", raw_piece)] if raw_piece else []
                return legacy_splitter.feed(raw_piece)

            # ──────────────────── 通道 1: 标准 Google 官方 REST 通道 (API Key / OAuth) ────────────────────
            if account.auth_type in ("oauth_google_one", "oauth_personal", "api_key"):
                est_prompt_tokens = (
                    estimate_tokens(system_instruction)
                    + (8 if system_instruction else 0)
                    + estimate_messages_tokens(effective_history_dicts)
                    + estimate_tokens(prompt_with_attachments)
                    + 8
                )
                legacy_prov_meta = {
                    "id": f"account_{getattr(account, 'id', None) or account.name}",
                    "name": account.name,
                    "provider_type": "google_oauth" if account.auth_type.startswith("oauth") else "gemini_api",
                }
                collector = TokenMetricsCollector(
                    turn_id=turn_id,
                    step_index=step_index,
                    agent_role=effective_agent_role,
                    provider_info=legacy_prov_meta,
                    model_name=google_model_name,
                    estimated_prompt_tokens=est_prompt_tokens,
                )
                stream_completed = False
                should_persist_on_abort = False
                try:
                    # 若为 OAuth 通道，自动检查并续期 Access Token；若为 API Key，直接使用 credentials
                    if account.auth_type.startswith("oauth"):
                        valid_token = await oauth_service.ensure_valid_token(account)
                        active_auth_type = "oauth_google_one"
                    else:
                        valid_token = (account.credentials or "").strip()
                        active_auth_type = "api_key"
                    
                    downgrade_events: list[dict[str, Any]] = []
                    actual_model = google_model_name

                    def on_model_downgrade(from_m: str, to_m: str, reason: str):
                        nonlocal actual_model
                        actual_model = to_m
                        collector.model_name = to_m
                        downgrade_events.append({
                            "type": "model_downgrade",
                            "original_model": from_m,
                            "current_model": to_m,
                            "reason": reason,
                            "account": account.name,
                            "message": f"检测到原模型「{from_m}」当前配额耗尽或容量不足 ({reason})，已自动为您无缝切换至高可用模型「{to_m}」继续生成。",
                        })

                    should_persist_on_abort = True
                    async for chunk in GeminiClient.stream_generate_content(
                        api_key=valid_token,
                        model_name=google_model_name,
                        contents=contents,
                        system_instruction=system_instruction,
                        thinking_level=thinking_level,
                        temperature=temperature,
                        auth_type=active_auth_type,
                        on_downgrade=on_model_downgrade,
                        on_usage=lambda u: collector.record_raw_usage(usage=u),
                        on_first_byte=collector.mark_first_byte,
                    ):
                        if cancel_event and cancel_event.is_set():
                            break
                        while downgrade_events:
                            yield downgrade_events.pop(0)
                        for seg_kind, seg_text in _split_legacy_segments(chunk):
                            collector.mark_chunk(seg_text)
                            if seg_kind == "thinking":
                                full_thinking_text += seg_text
                                yield {"type": "thinking_chunk", "content": seg_text, "account": account.name, "model": actual_model}
                            else:
                                full_response_text += seg_text
                                yield {"type": "chunk", "content": seg_text, "account": account.name, "model": actual_model}

                    # 若流读取被主动中止，跳过收尾 flush 与 done，确保走中止持久化语义
                    if cancel_event and cancel_event.is_set():
                        should_persist_on_abort = bool(full_response_text or full_thinking_text)
                        yield {"type": "cancelled", "content": "已中止生成"}
                        return

                    if legacy_splitter is not None:
                        for seg_kind, seg_text in legacy_splitter.flush():
                            collector.mark_chunk(seg_text)
                            if seg_kind == "thinking":
                                full_thinking_text += seg_text
                                yield {"type": "thinking_chunk", "content": seg_text, "account": account.name, "model": actual_model}
                            else:
                                full_response_text += seg_text
                                yield {"type": "chunk", "content": seg_text, "account": account.name, "model": actual_model}

                    while downgrade_events:
                        yield downgrade_events.pop(0)

                    stream_completed = True
                    step_res = await collector.persist(
                        conversation_id=conversation_id,
                        incognito=incognito,
                        aborted=False,
                    )
                    total_tokens = max(1, step_res.prompt_tokens + step_res.completion_tokens)
                    latency_ms = (time.time() - start_time) * 1000
                    tracker.record_request_end(latency_ms, tokens=total_tokens)
                    await AccountRepository.record_usage(account.name, tokens_spent=total_tokens)
                    yield {"type": "step_metrics", "metrics": step_res.to_dict()}
                    yield {
                        "type": "done",
                        "full_content": full_response_text,
                        "thinking": full_thinking_text,
                        "account": account.name,
                        "model": actual_model,
                        "usage": {
                            "promptTokenCount": step_res.prompt_tokens,
                            "candidatesTokenCount": step_res.completion_tokens,
                            "cachedContentTokenCount": step_res.cached_tokens,
                            "totalTokenCount": total_tokens,
                        },
                    }
                    return

                except GeminiAPIException as g_err:
                    should_persist_on_abort = False
                    logger.warning(f"OAuth 账号 [{account.name}] 响应异常 [{g_err.status_code}]: {g_err.message}")
                    if g_err.status_code in (429, 503, 502):
                        encountered_rate_limit = True
                        if full_response_text or full_thinking_text:
                            collector.reset_stream_buffer()
                            if legacy_splitter is not None:
                                legacy_splitter.reset()
                            yield {
                                "type": "stream_reset",
                                "reason": "mid_flight_retry",
                                "message": "检测到上游网络流式断连 (EOF)，正在自动清空残缺气泡并无缝重试...",
                            }
                            full_response_text = ""
                            full_thinking_text = ""
                    elif g_err.status_code in (401, 403):
                        encountered_auth_error = True
                    else:
                        yield {"type": "error", "content": f"Google API 报错: {g_err.message}"}
                        return
                except Exception as e:
                    should_persist_on_abort = False
                    logger.error(f"OAuth 账号 [{account.name}] 生成异常: {e}")
                    encountered_rate_limit = True
                    if full_response_text or full_thinking_text:
                        collector.reset_stream_buffer()
                        if legacy_splitter is not None:
                            legacy_splitter.reset()
                        yield {
                            "type": "stream_reset",
                            "reason": "mid_flight_retry",
                            "message": "检测到网络断流，正在无缝重试...",
                        }
                        full_response_text = ""
                        full_thinking_text = ""
                finally:
                    if not stream_completed and should_persist_on_abort:
                        try:
                            await collector.persist(
                                conversation_id=conversation_id,
                                incognito=incognito,
                                aborted=True,
                            )
                        except Exception:
                            pass

            # ──────────────────── 通道 2: Antigravity / Gemini CLI 引擎 ────────────────────
            elif account.cli_home_dir and task_role != "light":
                cli_prov_meta = {
                    "id": f"cli_{account.id or account.name}",
                    "name": account.name,
                    "provider_type": "gemini_cli",
                }
                cli_collector = TokenMetricsCollector(
                    turn_id=turn_id,
                    step_index=step_index,
                    agent_role=effective_agent_role,
                    provider_info=cli_prov_meta,
                    model_name=google_model_name,
                    estimated_prompt_tokens=estimate_tokens(full_cli_prompt),
                )
                cli_completed = False
                try:
                    async for event in run_cli_stream(
                        cli_home_dir=account.cli_home_dir,
                        prompt=full_cli_prompt,
                        model=google_model_name if google_model_name != "default" else None,
                        thinking_level=thinking_level,
                        work_dir=work_dir,
                        approval_mode="auto_edit",
                        conversation_id=conversation_id,
                        cancel_event=cancel_event,
                    ):
                        etype = event.get("type")

                        if etype == "cancelled":
                            yield {"type": "cancelled", "content": event.get("message", "已中断执行")}
                            return

                        if etype == "content":
                            text = event.get("text", "")
                            for seg_kind, seg_text in _split_legacy_segments(text):
                                cli_collector.mark_chunk(seg_text)
                                if seg_kind == "thinking":
                                    full_thinking_text += seg_text
                                    yield {"type": "thinking_chunk", "content": seg_text, "account": account.name}
                                else:
                                    full_response_text += seg_text
                                    yield {"type": "chunk", "content": seg_text, "account": account.name}

                        elif etype == "tool_request":
                            yield {
                                "type": "tool_request",
                                "tool_name": event.get("tool_name"),
                                "tool_input": event.get("tool_input"),
                                "account": account.name,
                            }

                        elif etype == "tool_result":
                            yield {
                                "type": "tool_result",
                                "tool_name": event.get("tool_name"),
                                "output": event.get("output"),
                            }

                        elif etype == "rate_limited":
                            encountered_rate_limit = True
                            logger.warning(f"账号 [{account.name}] 触发限流，准备切号")
                            break

                        elif etype == "auth_error":
                            encountered_auth_error = True
                            logger.error(f"账号 [{account.name}] 认证失效，准备切号")
                            break

                        elif etype == "timeout":
                            yield {"type": "error", "content": "请求超时，请重试"}
                            return

                        elif etype == "error":
                            yield {"type": "error", "content": event.get("message", "未知错误")}
                            return

                        elif etype == "done":
                            if legacy_splitter is not None:
                                for seg_kind, seg_text in legacy_splitter.flush():
                                    cli_collector.mark_chunk(seg_text)
                                    if seg_kind == "thinking":
                                        full_thinking_text += seg_text
                                        yield {"type": "thinking_chunk", "content": seg_text, "account": account.name}
                                    else:
                                        full_response_text += seg_text
                                        yield {"type": "chunk", "content": seg_text, "account": account.name}
                            cli_completed = True
                            usage = event.get("usage", {})
                            if isinstance(usage, dict) and usage:
                                cli_collector.record_raw_usage(usage=usage)
                            step_res = await cli_collector.persist(
                                conversation_id=conversation_id,
                                incognito=incognito,
                                aborted=False,
                            )
                            total_tokens = max(
                                1,
                                usage.get("totalTokenCount")
                                or (step_res.prompt_tokens + step_res.completion_tokens),
                            )
                            latency_ms = (time.time() - start_time) * 1000
                            tracker.record_request_end(latency_ms, tokens=total_tokens)
                            await AccountRepository.record_usage(account.name, tokens_spent=total_tokens)
                            yield {"type": "step_metrics", "metrics": step_res.to_dict()}
                            yield {
                                "type": "done",
                                "full_content": full_response_text,
                                "thinking": full_thinking_text,
                                "account": account.name,
                                "usage": usage,
                            }
                            return
                finally:
                    if not cli_completed and not encountered_rate_limit and not encountered_auth_error and cli_collector.accumulated_text:
                        try:
                            await cli_collector.persist(
                                conversation_id=conversation_id,
                                incognito=incognito,
                                aborted=True,
                            )
                        except Exception:
                            pass
            else:
                logger.error(f"账号 [{account.name}] 未知认证模式且无 cli_home_dir，跳过")
                retries_left -= 1
                continue

            # 处理限流/认证失效：冷却当前账号，尝试切换到下一个
            if encountered_rate_limit or encountered_auth_error:
                cooldown_secs = 300 if encountered_auth_error else settings.default_cooldown_seconds
                cooldown_until = (
                    datetime.now(timezone.utc) + timedelta(seconds=cooldown_secs)
                ).strftime("%Y-%m-%d %H:%M:%S")
                await AccountRepository.mark_cooldown(account.name, cooldown_until)
                await audit_service.log(
                    event_type="account_cooldown",
                    actor="system",
                    detail={"reason": "auth_error" if encountered_auth_error else "rate_limited", "cooldown_seconds": cooldown_secs},
                    account_name=account.name,
                    approved=False
                )
                last_failed_account = account.name
                relayed_count += 1
                retries_left -= 1
                continue

            retries_left -= 1

        yield {
            "type": "error",
            "content": "所有重试均已失败，请检查账号状态或稍后重试"
        }

    @classmethod
    async def stream_chat(cls, *args: Any, **kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
        """别名兼容方法：转发至具备自动故障转移与轻重双轨路由的 execute_stream_with_failover"""
        async for chunk in cls.execute_stream_with_failover(*args, **kwargs):
            yield chunk


engine_pool = EnginePoolService()
