"""
LLM Wiki REST API 路由模块
提供空间管理、词条列表/预览/编辑、多源摄入及 Inbox 审批归档与双向反哺接口（桌面与移动端共享）。
"""
from __future__ import annotations

from typing import Any, Literal
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...core.logging_config import logger
from ...wiki.incremental_compiler import WikiIncrementalCompiler
from ...wiki.storage_manager import WikiStorageManager

router = APIRouter(prefix="/wiki", tags=["llm-wiki"])


class CreateWikiSpaceRequest(BaseModel):
    name: str = Field(..., description="Wiki 空间名称")
    description: str = Field(default="", description="空间描述")
    default_kb_id: str = Field(default="", description="默认关联反哺的知识库 ID")
    auto_ingest_chat: int = Field(default=1, description="是否允许会话闲置自动提炼草稿")


class UpdateWikiPageRequest(BaseModel):
    title: str | None = Field(default=None, description="词条标题")
    slug: str | None = Field(default=None, description="词条唯一 slug")
    markdown_content: str = Field(..., description="Markdown 正文")
    category_path: str | None = Field(default=None, description="分类目录路径")
    authority_level: Literal["official", "decision", "data_snapshot"] | None = Field(default=None)
    effective_date: str | None = Field(default=None, description="生效日期 YYYY-MM-DD")
    summary_text: str | None = Field(default=None, description="预览摘要")
    kb_id: str | None = Field(default=None, description="关联知识库 ID")


class IngestWikiRequest(BaseModel):
    wiki_space_id: str = Field(..., description="目标 Wiki 空间 ID")
    source_type: Literal["kb_doc", "meeting_note", "chat_decision", "sql_snapshot", "camera_ocr"]
    content: str = Field(..., description="待编译摄入的原始文本内容")
    source_meta: dict[str, Any] = Field(default_factory=dict, description="来源元数据")
    target_slug: str | None = Field(default=None, description="指定合并目标词条 slug")
    auto_publish: bool = Field(default=False, description="是否跳过审阅直接收藏发布（不覆写既有官方词条）")


@router.get("/spaces")
async def list_wiki_spaces() -> dict[str, Any]:
    """获取全部 Wiki 空间列表（含已发布数与 Inbox 待审阅计数）。"""
    spaces = await WikiStorageManager.list_spaces()
    return {"success": True, "data": spaces}


@router.post("/spaces")
async def create_wiki_space(req: CreateWikiSpaceRequest) -> dict[str, Any]:
    """创建新的 LLM Wiki 知识空间。"""
    try:
        space = await WikiStorageManager.create_space(
            name=req.name,
            description=req.description,
            default_kb_id=req.default_kb_id,
            auto_ingest_chat=req.auto_ingest_chat,
        )
        return {"success": True, "data": space}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/spaces/{space_id}/pages")
async def list_wiki_pages(
    space_id: str,
    status: str | None = Query(default=None, description="筛选状态：published | inbox | discarded"),
    q: str = Query(default="", description="搜索关键词"),
) -> dict[str, Any]:
    """获取指定 Wiki 空间下的词条列表。"""
    space = await WikiStorageManager.get_space(space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Wiki 空间不存在")
    pages = await WikiStorageManager.list_pages(space_id, status=status, q=q)
    return {"success": True, "data": pages}


@router.get("/spaces/{space_id}/preview")
async def preview_wiki_page(
    space_id: str,
    slug: str = Query(..., description="目标词条 slug"),
) -> dict[str, Any]:
    """双链悬停/底部抽屉极速预览接口。"""
    preview = await WikiStorageManager.preview_by_slug(space_id, slug)
    if not preview:
        raise HTTPException(status_code=404, detail=f"词条 [[{slug}]] 尚未创建或已被丢弃")
    return {"success": True, "data": preview}


@router.get("/pages/{page_id}")
async def get_wiki_page_detail(page_id: str) -> dict[str, Any]:
    """获取词条完整内容、正反向双链、溯源记录及 1-hop 局部图谱。"""
    detail = await WikiStorageManager.get_page_detail(page_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Wiki 词条不存在")
    return {"success": True, "data": detail}


@router.put("/pages/{page_id}")
async def update_wiki_page(page_id: str, req: UpdateWikiPageRequest) -> dict[str, Any]:
    """手工编辑并保存 Wiki 词条，自动重算双链并双向反哺知识库。"""
    existing = await WikiStorageManager.get_page_detail(page_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Wiki 词条不存在")

    updated = await WikiStorageManager.upsert_page(
        wiki_space_id=existing["wiki_space_id"],
        slug=req.slug if req.slug is not None else existing["slug"],
        title=req.title if req.title is not None else existing["title"],
        markdown_content=req.markdown_content,
        category_path=req.category_path if req.category_path is not None else existing.get("category_path", "未分类"),
        authority_level=req.authority_level if req.authority_level is not None else existing.get("authority_level", "decision"),
        review_status=existing.get("review_status") or "published",
        effective_date=req.effective_date if req.effective_date is not None else existing.get("effective_date", ""),
        kb_id=req.kb_id if req.kb_id is not None else existing.get("kb_id", ""),
        summary_text=req.summary_text if req.summary_text is not None else "",
        source_refs=existing.get("source_refs") or [],
        pending_diff_markdown=existing.get("pending_diff_markdown") or "",
        page_id=page_id,
        stitch_links=True,
    )

    sync_res = {"chunks_written": 0, "rules_written": 0}
    if updated.get("review_status") == "published" and updated.get("kb_id"):
        sync_res = await WikiStorageManager.sync_page_to_knowledge_base(updated)
    updated["sync_result"] = sync_res
    return {"success": True, "data": updated}


@router.post("/ingest")
async def ingest_to_wiki(req: IngestWikiRequest) -> dict[str, Any]:
    """统一多源知识摄入与增量编译入口。"""
    try:
        result = await WikiIncrementalCompiler.ingest_and_compile(
            wiki_space_id=req.wiki_space_id,
            source_type=req.source_type,
            raw_content=req.content,
            source_meta=req.source_meta,
            target_slug=req.target_slug,
            auto_publish=req.auto_publish,
        )
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[WikiAPI] 增量摄入编译异常: {e}")
        raise HTTPException(status_code=500, detail=f"Wiki 增量编译失败: {e}")


@router.post("/inbox/{page_id}/approve")
async def approve_wiki_inbox(page_id: str) -> dict[str, Any]:
    """一键批准归档 Inbox 增量或草稿，合并物理文件并反哺知识库。"""
    try:
        updated = await WikiIncrementalCompiler.approve_inbox_page(page_id)
        return {"success": True, "data": updated, "message": "已批准归档并同步更新知识索引"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/inbox/{page_id}/discard")
async def discard_wiki_inbox(page_id: str) -> dict[str, Any]:
    """丢弃 Inbox 待审阅增量。"""
    try:
        res = await WikiIncrementalCompiler.discard_inbox_page(page_id)
        return {"success": True, "data": res, "message": "已丢弃待审阅增量"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

