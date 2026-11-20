"""
移动端领域热词语音转写 (ASR) 接口模块
支持按 expert_id / kb_id 自动提取绑定领域 Profile 中的专业术语作为 initial_prompt 热词，
无可用后端 ASR 节点时返回 501 + web_speech_api 降级标识。
"""
from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from ...core.logging_config import logger
from ...multimodal.audio_transcriber import AudioTranscriber
from ...providers.light_llm_router import light_llm_router
from ...repository.db import db_manager
from ...search.rule_extractor import UniversalRuleExtractor

router = APIRouter(prefix="/audio", tags=["audio-asr"])

MAX_AUDIO_BYTES = 10 * 1024 * 1024  # 单次上传严格限制 <= 10MB


async def build_domain_hotwords_prompt(
    expert_id: str | None = None,
    kb_id: str | None = None,
) -> tuple[str, bool]:
    """根据专家或知识库 ID 查询领域模板，生成 ASR 热词提示 initial_prompt。"""
    target_kb_id = (kb_id or "").strip()
    domain_type = ""
    rule_profile_json = ""

    async with db_manager.get_connection() as conn:
        if not target_kb_id and expert_id and expert_id.strip():
            async with conn.execute(
                """
                SELECT kb.kb_id, kb.domain_type, kb.rule_profile_json
                FROM expert_knowledge_bases ekb
                JOIN knowledge_bases kb ON ekb.kb_id = kb.kb_id
                WHERE ekb.expert_id = ?
                ORDER BY ekb.priority_weight DESC, ekb.created_at ASC
                LIMIT 1
                """,
                (expert_id.strip(),),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    target_kb_id = row["kb_id"]
                    domain_type = row["domain_type"] or "general"
                    rule_profile_json = row["rule_profile_json"] or ""

        if target_kb_id and not domain_type:
            async with conn.execute(
                "SELECT domain_type, rule_profile_json FROM knowledge_bases WHERE kb_id = ?",
                (target_kb_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    domain_type = row["domain_type"] or "general"
                    rule_profile_json = row["rule_profile_json"] or ""

    if not domain_type and not rule_profile_json:
        return "", False

    profile = UniversalRuleExtractor.resolve_profile(domain_type or "general", rule_profile_json)
    hotwords: list[str] = []
    for key in ("condition_tag_entities", "trigger_keywords"):
        items = profile.get(key) or []
        if isinstance(items, list):
            for w in items:
                clean_w = str(w or "").strip()
                if clean_w and clean_w not in hotwords:
                    hotwords.append(clean_w)

    if not hotwords:
        return "", False

    prompt = "以下是常见业务专业术语：" + "、".join(hotwords[:40])
    return prompt, True


async def _find_http_asr_node() -> dict[str, Any] | None:
    """从算力连接池中查找已启用的 Whisper / SenseVoice / ASR HTTP 节点。"""
    accounts = await light_llm_router.fetch_pool_accounts()
    for acc in accounts:
        if not acc.get("is_enabled", True):
            continue
        if acc.get("provider_type") not in ("openai_compat", "ollama", "llama_server"):
            continue
        model_lower = str(acc.get("model_name") or "").lower()
        name_lower = str(acc.get("name") or "").lower()
        if any(k in model_lower or k in name_lower for k in ("whisper", "sensevoice", "asr", "audio", "stt")):
            base_url = str(acc.get("base_url") or "").strip().rstrip("/")
            if base_url:
                return {
                    "base_url": base_url,
                    "api_key": acc.get("api_key") or "sk-local",
                    "model": acc.get("model_name") or "whisper-1",
                    "source": acc.get("name") or acc.get("id") or "http_asr",
                }
    return None


async def _dispatch_asr_transcription(
    audio_bytes: bytes,
    filename: str,
    content_type: str,
    initial_prompt: str,
) -> tuple[str, str] | None:
    """调用可用 ASR 引擎执行转写，返回 (text, engine_name)；无可用引擎返回 None。"""
    http_node = await _find_http_asr_node()
    if http_node is not None:
        headers = {"Authorization": f"Bearer {http_node['api_key']}"}
        data: dict[str, str] = {"model": http_node["model"], "language": "zh"}
        if initial_prompt:
            data["prompt"] = initial_prompt
        files = {"file": (filename or "audio.webm", audio_bytes, content_type or "audio/webm")}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{http_node['base_url']}/audio/transcriptions",
                    data=data,
                    files=files,
                    headers=headers,
                )
                resp.raise_for_status()
                text = str(resp.json().get("text") or "").strip()
                return text, str(http_node["source"])
        except Exception as err:
            logger.warning(f"[AudioASR] HTTP ASR 节点 [{http_node['source']}] 调用失败: {err}")

    # 尝试本地 faster-whisper（仅当已安装且可用时生效）
    suffix = Path(filename or "audio.webm").suffix or ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_f:
        tmp_f.write(audio_bytes)
        tmp_path = tmp_f.name
    try:
        res = await asyncio.to_thread(
            AudioTranscriber.transcribe,
            tmp_path,
            "zh",
            initial_prompt or None,
        )
        if res.get("success"):
            return str(res.get("full_text") or "").strip(), "local_whisper"
    except Exception as local_err:
        logger.debug(f"[AudioASR] 本地 Whisper 不可用: {local_err}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return None


@router.post("/transcriptions")
async def transcribe_audio(
    file: UploadFile = File(..., description="音频文件 (audio/webm, audio/mp4, audio/wav，<= 10MB)"),
    expert_id: str | None = Form(default=None, description="当前对话专家 ID，用于提取领域热词"),
    kb_id: str | None = Form(default=None, description="可选知识库 ID，用于提取领域热词"),
) -> Any:
    """领域热词语音转写接口，未配置后端 ASR 时返回 501 引导前端降级。"""
    t0 = time.perf_counter()
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="上传的音频文件为空")
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="单次语音文件大小不能超过 10MB")

    initial_prompt, hotwords_applied = await build_domain_hotwords_prompt(
        expert_id=expert_id,
        kb_id=kb_id,
    )

    result = await _dispatch_asr_transcription(
        audio_bytes=audio_bytes,
        filename=file.filename or "voice.webm",
        content_type=file.content_type or "audio/webm",
        initial_prompt=initial_prompt,
    )
    if result is None:
        return JSONResponse(
            status_code=501,
            content={
                "fallback": "web_speech_api",
                "detail": "Backend ASR not configured",
                "hotwords_applied": hotwords_applied,
            },
        )

    text, engine_name = result
    duration_ms = int((time.perf_counter() - t0) * 1000)
    return {
        "text": text,
        "engine": engine_name,
        "hotwords_applied": hotwords_applied,
        "duration_ms": duration_ms,
    }
