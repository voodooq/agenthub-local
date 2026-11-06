"""
智能多账号引擎池调度器（支持标准 Google OAuth 个人通道 + Antigravity CLI 双引擎）
实现：429/认证失效时多账号自动冷却切换，支持任何 Gmail 个人 OAuth 账号免开通直接使用。
"""
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import AsyncGenerator, Any

from ..core.config import settings
from ..core.logging_config import logger
from ..repository.account_repository import AccountRepository
from ..repository.message_repository import MessageRepository
from .quota_monitor import quota_monitor
from .agent_runner import run_cli_stream
from .oauth_service import oauth_service
from .gemini_client import GeminiClient, GeminiAPIException
from .audit_service import audit_service


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
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        带零感知热故障转移与可配置上下文记忆轮数的流式生成执行器。
        当账号遇到 rate_limited 或 auth_error 时，自动冷却并切换到下一个可用账号。
        """
        retries_left = max_retries or settings.auto_failover_retry

        if pre_built_history is not None:
            effective_history_dicts = pre_built_history
        else:
            history_messages = await MessageRepository.get_by_conversation(conversation_id)
            slice_count = max(0, context_rounds * 2) if context_rounds > 0 else 0
            effective_history_dicts = [
                {"role": m.role, "content": m.content}
                for m in (history_messages[-slice_count:] if slice_count > 0 else [])
            ]

        # 检查是否有图片或文档附件
        attachment_guidance = ""
        inline_images: list[dict[str, str]] = []

        if attachments:
            for att in attachments:
                att_dict = att if isinstance(att, dict) else att.dict()
                file_path = att_dict.get("file_path")
                mime_type = att_dict.get("mime_type", "image/png")
                name = att_dict.get("name", "附件")

                if file_path and os.path.exists(file_path):
                    if mime_type.startswith("image/"):
                        attachment_guidance += (
                            f"\n\n【用户附带上传的本地图片文件】:\n"
                            f"- 文件名: {name}\n"
                            f"- 磁盘绝对路径: {file_path}\n"
                            f"- 指引: 用户希望你详细分析此图片。请立即读取该图片并对画面中的人物、文本、场景、图表或代码细节进行深度视觉分析解答。\n"
                        )
                        try:
                            import base64
                            with open(file_path, "rb") as img_f:
                                b64 = base64.b64encode(img_f.read()).decode("utf-8")
                                inline_images.append({"mime_type": mime_type, "data": b64})
                        except Exception as e:
                            logger.warning(f"读取图片 base64 失败: {e}")
                    else:
                        attachment_guidance += f"\n\n【用户附带上传的文档附件】: {name} (路径: {file_path})\n"

        prompt_with_attachments = user_prompt + attachment_guidance

        # 组装消息列表 (官方 Gemini REST API 格式)
        contents: list[dict[str, Any]] = []
        for msg in effective_history_dicts:
            r_raw = msg.get("role", "user")
            role = "model" if r_raw in ("model", "assistant") else "user"
            prefix = "[工具执行结果]: " if r_raw == "tool" else ""
            contents.append({"role": role, "parts": [{"text": f"{prefix}{msg.get('content', '')}"}]})

        # 用户当前提问 parts（支持原生多模态 inline_data）
        user_parts: list[dict[str, Any]] = []
        for img in inline_images:
            user_parts.append({"inline_data": {"mime_type": img["mime_type"], "data": img["data"]}})
        user_parts.append({"text": user_prompt})
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
            lines.append(f"[{role_label}]: {msg.get('content', '')}")
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
        try:
            from ..core.provider_adapter import provider_adapter
            from ..core.oauth_google import google_oauth_manager
            custom_providers = await provider_adapter.get_enabled_providers_for_role("primary")
            prev_failed_prov_name = None
            for prov in custom_providers:
                ptype = prov.get("provider_type")
                if ptype == "google_pool":
                    continue
                prov_models = prov.get("models") or []
                # 当模型名显式匹配该供应商的模型列表，或该供应商为 google_oauth / 优先级高于默认池(priority < 1)时优先路由
                if model_name in prov_models or ptype == "google_oauth" or int(prov.get("priority", 10)) < 1:
                    prov_name = prov.get("name", ptype)
                    target_m = model_name if (model_name in prov_models or ptype == "google_oauth") else (prov_models[0] if prov_models else model_name)
                    if prev_failed_prov_name:
                        yield {
                            "type": "relay",
                            "from_account": prev_failed_prov_name,
                            "to_account": prov_name,
                            "reason": "429_rate_limit",
                        }
                    else:
                        yield {"type": "start", "account": prov_name, "model": target_m}
                    custom_msgs = list(effective_history_dicts) + [{"role": "user", "content": prompt_with_attachments}]
                    full_custom_text = ""
                    try:
                        if ptype == "google_oauth":
                            valid_token = await google_oauth_manager.ensure_valid_access_token(prov)
                            async for ch in GeminiClient.stream_generate_content(
                                api_key=valid_token,
                                model_name=target_m,
                                contents=contents,
                                system_instruction=system_instruction,
                                thinking_level=thinking_level,
                                temperature=temperature,
                                auth_type="oauth_google_one",
                            ):
                                full_custom_text += ch
                                yield {"type": "chunk", "content": ch, "account": prov_name}
                        elif ptype in ("openai_compat", "ollama"):
                            async for ch in provider_adapter.stream_openai_compatible(
                                base_url=prov.get("base_url") or ("http://127.0.0.1:11434/v1" if ptype == "ollama" else ""),
                                api_key=prov.get("api_key") or ("ollama" if ptype == "ollama" else ""),
                                model_name=target_m,
                                messages=custom_msgs,
                                system_instruction=system_instruction,
                                temperature=temperature,
                            ):
                                full_custom_text += ch
                                yield {"type": "chunk", "content": ch, "account": prov_name}
                        elif ptype == "anthropic":
                            async for ch in provider_adapter.stream_anthropic(
                                base_url=prov.get("base_url") or "",
                                api_key=prov.get("api_key") or "",
                                model_name=target_m,
                                messages=custom_msgs,
                                system_instruction=system_instruction,
                                temperature=temperature,
                            ):
                                full_custom_text += ch
                                yield {"type": "chunk", "content": ch, "account": prov_name}
                        elif ptype == "gemini_api" and prov.get("api_key"):
                            async for ch in GeminiClient.stream_generate_content(
                                api_key=prov["api_key"],
                                model_name=target_m,
                                contents=contents,
                                system_instruction=system_instruction,
                                thinking_level=thinking_level,
                                temperature=temperature,
                                auth_type="api_key",
                            ):
                                full_custom_text += ch
                                yield {"type": "chunk", "content": ch, "account": prov_name}

                        if full_custom_text:
                            yield {
                                "type": "done",
                                "full_content": full_custom_text,
                                "account": prov_name,
                                "usage": {"totalTokenCount": len(full_custom_text) // 3},
                            }
                            return
                    except GeminiAPIException as g_err:
                        if g_err.status_code in (429, 503):
                            await provider_adapter.mark_provider_cooldown(prov["id"], cooldown_seconds=60)
                            prev_failed_prov_name = prov_name
                        logger.warning(f"算力通道 [{prov_name}] 触发限流或异常 ({g_err})，<50ms 无缝接力至下一节点")
                    except Exception as custom_err:
                        if "429" in str(custom_err):
                            await provider_adapter.mark_provider_cooldown(prov["id"], cooldown_seconds=60)
                            prev_failed_prov_name = prov_name
                        logger.warning(f"自定义算力通道 [{prov_name}] 异常 ({custom_err})，自动故障转移至下一节点")
        except Exception as e:
            logger.debug(f"检查多协议供应商跳过: {e}")

        relayed_count = 0
        last_failed_account = None

        while retries_left > 0:
            account = await AccountRepository.get_active_candidate()
            if not account:
                logger.error("算力池告警：所有账号均处于 Cooldown 或已用尽配额！")
                yield {
                    "type": "error",
                    "content": "【GeminiHub】当前所有账号均处于冷却或配额用尽状态，请稍候再试或添加新账号。"
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
                    detail={"reason": "429_or_auth_failover", "model": model_name, "from": last_failed_account, "to": account.name},
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
                    detail={"model": model_name, "thinking_level": thinking_level, "auth_type": account.auth_type},
                    account_name=account.name,
                    approved=True
                )
                yield {
                    "type": "start",
                    "account": account.name,
                    "model": model_name
                }

            tracker.record_request_start()
            full_response_text = ""
            encountered_rate_limit = False
            encountered_auth_error = False
            start_time = time.time()

            # ──────────────────── 通道 1: 标准 Google 官方 REST 通道 (API Key / OAuth) ────────────────────
            if account.auth_type in ("oauth_google_one", "oauth_personal", "api_key"):
                try:
                    # 若为 OAuth 通道，自动检查并续期 Access Token；若为 API Key，直接使用 credentials
                    if account.auth_type.startswith("oauth"):
                        valid_token = await oauth_service.ensure_valid_token(account)
                        active_auth_type = "oauth_google_one"
                    else:
                        valid_token = (account.credentials or "").strip()
                        active_auth_type = "api_key"
                    
                    async for chunk in GeminiClient.stream_generate_content(
                        api_key=valid_token,
                        model_name=model_name,
                        contents=contents,
                        system_instruction=system_instruction,
                        thinking_level=thinking_level,
                        temperature=temperature,
                        auth_type=active_auth_type
                    ):
                        full_response_text += chunk
                        yield {"type": "chunk", "content": chunk, "account": account.name}

                    total_tokens = len(full_response_text) // 3
                    latency_ms = (time.time() - start_time) * 1000
                    tracker.record_request_end(latency_ms, tokens=total_tokens)
                    await AccountRepository.record_usage(account.name, tokens_spent=total_tokens)
                    yield {
                        "type": "done",
                        "full_content": full_response_text,
                        "account": account.name,
                        "usage": {"totalTokenCount": total_tokens}
                    }
                    return

                except GeminiAPIException as g_err:
                    logger.warning(f"OAuth 账号 [{account.name}] 响应异常 [{g_err.status_code}]: {g_err.message}")
                    if g_err.status_code in (429, 503):
                        encountered_rate_limit = True
                    elif g_err.status_code in (401, 403):
                        encountered_auth_error = True
                    else:
                        yield {"type": "error", "content": f"Google API 报错: {g_err.message}"}
                        return
                except Exception as e:
                    logger.error(f"OAuth 账号 [{account.name}] 生成异常: {e}")
                    encountered_rate_limit = True

            # ──────────────────── 通道 2: Antigravity / Gemini CLI 引擎 ────────────────────
            elif account.cli_home_dir:
                async for event in run_cli_stream(
                    cli_home_dir=account.cli_home_dir,
                    prompt=full_cli_prompt,
                    model=model_name if model_name != "default" else None,
                    thinking_level=thinking_level,
                    work_dir=work_dir,
                    approval_mode="auto_edit",
                ):
                    etype = event.get("type")

                    if etype == "content":
                        text = event.get("text", "")
                        full_response_text += text
                        yield {"type": "chunk", "content": text, "account": account.name}

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
                        usage = event.get("usage", {})
                        total_tokens = usage.get("totalTokenCount", len(full_response_text) // 3)
                        latency_ms = (time.time() - start_time) * 1000
                        tracker.record_request_end(latency_ms, tokens=total_tokens)
                        await AccountRepository.record_usage(account.name, tokens_spent=total_tokens)
                        yield {
                            "type": "done",
                            "full_content": full_response_text,
                            "account": account.name,
                            "usage": usage,
                        }
                        return
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


engine_pool = EnginePoolService()
