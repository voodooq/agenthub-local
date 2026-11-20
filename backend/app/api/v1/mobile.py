"""
移动端「随手拍」统一分析接口 (Mobile Snap Gateway)
支持三类场景：
- equipment: 装备合规核验（视觉识图 + ExpertKnowledgeRouter 发布版本规程校验）
- notice / whiteboard: 通知单据与白板纪要 OCR -> 自动写入 Wiki Inbox
"""
from __future__ import annotations

import base64
import json
import re
from typing import Any, Literal

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ...core.logging_config import logger
from ...providers.light_llm_router import light_llm_router
from ...search.expert_kb_router import ExpertKnowledgeRouter
from ...wiki.incremental_compiler import WikiIncrementalCompiler
from ...wiki.storage_manager import WikiStorageManager

router = APIRouter(prefix="/mobile", tags=["mobile-companion"])

MAX_IMAGE_BYTES = 15 * 1024 * 1024  # 压缩后图片一般 < 1MB，安全上限 15MB


async def _find_vision_node() -> dict[str, Any] | None:
    """从算力连接池查找可用的支持视觉识图的 HTTP 节点。"""
    rows = await light_llm_router.fetch_pool_accounts()
    vision_keywords = ("vl", "vision", "4o", "gemini", "claude", "minicpm", "4v", "omni")

    for acc in rows:
        if not acc.get("is_enabled", True):
            continue
        if acc.get("provider_type") not in ("openai_compat", "openai", "ollama", "llama_server", "custom"):
            continue
        if acc.get("role_binding") == "embedding_only":
            continue
        base_url = str(acc.get("base_url") or "").strip().rstrip("/")
        if not base_url:
            continue

        candidates: list[str] = []
        if acc.get("model_name"):
            candidates.append(str(acc["model_name"]))
        for m in acc.get("models") or []:
            if m and str(m) not in candidates:
                candidates.append(str(m))

        for model_name in candidates:
            model_lower = model_name.lower()
            if any(k in model_lower for k in vision_keywords):
                return {
                    "base_url": base_url,
                    "api_key": acc.get("api_key") or "sk-local",
                    "model": model_name,
                    "source": acc.get("name") or acc.get("id") or "vision_node",
                }
    return None


async def _analyze_image_content(
    image_bytes: bytes,
    content_type: str,
    mode: Literal["equipment", "notice", "whiteboard"],
) -> str:
    """调用多模态视觉节点提取图片中的规格参数或文字内容。"""
    vision_node = await _find_vision_node()
    if vision_node is None:
        raise HTTPException(
            status_code=501,
            detail="算力池中暂未配置支持视觉识别的多模态模型节点，请在桌面端接入视觉模型后使用。",
        )

    b64_img = base64.b64encode(image_bytes).decode("utf-8")
    mime = content_type or "image/jpeg"

    if mode == "equipment":
        prompt = (
            "请仔细识别图片中的运动/业务装备标签、牛数（如 350N/800N/900N）、剑条刻度、型号与安全认证标志。"
            "用简洁准确的中文列出识别到的全部规格参数与关键字段。"
        )
    elif mode == "whiteboard":
        prompt = (
            "请识别白板或手写草图中的所有文字、流程节点、时间节点与会议决策要点，"
            "整理为清晰的 Markdown 条目。"
        )
    else:
        prompt = (
            "请完整准确提取图片中的通知单据、表格或文件正文内容，"
            "保留日期、金额、条件限制并整理为标准 Markdown 格式。"
        )

    payload = {
        "model": vision_node["model"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64_img}"}},
                ],
            }
        ],
        "temperature": 0.2,
        "max_tokens": 1024,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {vision_node['api_key']}"}
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                f"{vision_node['base_url']}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            text = str(resp.json()["choices"][0]["message"]["content"] or "").strip()
            return re.sub(r"<think>.*?(</think>|$)", "", text, flags=re.DOTALL).strip()
    except Exception as err:
        logger.error(f"[MobileSnap] 视觉模型 [{vision_node['source']}] 识别失败: {err}")
        raise HTTPException(status_code=502, detail=f"图像识别失败: {err}")


async def _verify_equipment_with_rules(
    recognized_text: str,
    kb_context: str,
) -> dict[str, Any]:
    """使用轻量模型结合已发布规程依据生成结构化 2 列合规判定卡片。"""
    sys_prompt = (
        "你是赛事与业务装备合规审核专家。请根据图片识别到的装备参数与知识库最新规程依据，"
        "输出严格的 JSON 对象：\n"
        '{"verdict": "合规"|"不合规"|"需复核", "summary": "一句简明核验结论", '
        '"key_values": [{"label": "字段名", "value": "字段值"}]}'
    )
    user_prompt = f"图片识别参数：\n{recognized_text}\n\n已发布规程依据：\n{kb_context or '暂无绑定规程'}"
    raw = await light_llm_router.complete(
        prompt=user_prompt,
        system_prompt=sys_prompt,
        temperature=0.1,
        max_tokens=512,
    )
    if raw:
        txt = raw.strip()
        m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", txt)
        if m:
            txt = m.group(1)
        elif "{" in txt and "}" in txt:
            txt = txt[txt.find("{") : txt.rfind("}") + 1]
        try:
            parsed = json.loads(txt)
            if isinstance(parsed, dict):
                return {
                    "verdict": str(parsed.get("verdict") or "需复核"),
                    "summary": str(parsed.get("summary") or recognized_text[:120]),
                    "key_values": parsed.get("key_values") if isinstance(parsed.get("key_values"), list) else [],
                }
        except Exception:
            pass

    return {
        "verdict": "需复核",
        "summary": recognized_text[:150],
        "key_values": [{"label": "识别结果", "value": recognized_text[:120]}],
    }


@router.post("/snap-analyze")
async def mobile_snap_analyze(
    file: UploadFile = File(..., description="压缩后的现场拍摄图片"),
    mode: Literal["equipment", "notice", "whiteboard"] = Form(..., description="分析场景模式"),
    expert_id: str | None = Form(default=None, description="当前选中的专家 ID"),
    wiki_space_id: str | None = Form(default=None, description="目标 Wiki 空间 ID"),
) -> dict[str, Any]:
    """移动端随手拍统一分析接口：装备核验直接返回合规卡片，通知/白板自动入库 Wiki Inbox。"""
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="上传的图片文件为空")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="图片体积过大，请压缩后再上传")

    extracted_text = await _analyze_image_content(
        image_bytes=image_bytes,
        content_type=file.content_type or "image/jpeg",
        mode=mode,
    )

    if mode == "equipment":
        kb_context = ""
        citations: list[dict[str, Any]] = []
        if expert_id and expert_id.strip():
            has_kb, kb_prompt, hits, _ = await ExpertKnowledgeRouter.search_for_expert(
                expert_id=expert_id.strip(),
                query=extracted_text[:400],
                is_external_api=True,  # 严格遵循 is_published = 1 发布隔离门控
            )
            if has_kb:
                kb_context = kb_prompt
                citations = [
                    {
                        "title": h.get("title", ""),
                        "kb_name": h.get("kb_name", ""),
                        "version": h.get("version", ""),
                        "effective_date": h.get("effective_date", ""),
                    }
                    for h in hits[:3]
                ]

        card = await _verify_equipment_with_rules(extracted_text, kb_context)
        return {
            "success": True,
            "mode": "equipment",
            "recognized_text": extracted_text,
            "verdict": card["verdict"],
            "summary": card["summary"],
            "key_values": card["key_values"],
            "citations": citations,
        }

    # notice / whiteboard 模式：写入目标 Wiki 空间的 Inbox
    target_space_id = (wiki_space_id or "").strip()
    if not target_space_id:
        spaces = await WikiStorageManager.list_spaces()
        if spaces:
            target_space_id = spaces[0]["wiki_space_id"]
        else:
            default_space = await WikiStorageManager.create_space(
                name="默认业务百科空间",
                description="由移动端随手拍自动初始化",
            )
            target_space_id = default_space["wiki_space_id"]

    page = await WikiIncrementalCompiler.ingest_and_compile(
        wiki_space_id=target_space_id,
        source_type="camera_ocr",
        raw_content=extracted_text,
        source_meta={
            "source_title": "手机白板转纪要" if mode == "whiteboard" else "手机通知单据采集",
        },
    )
    return {
        "success": True,
        "mode": mode,
        "wiki_space_id": target_space_id,
        "page_id": page.get("page_id"),
        "title": page.get("title"),
        "summary_text": page.get("summary_text"),
        "review_status": page.get("review_status"),
        "recognized_text": extracted_text,
    }
