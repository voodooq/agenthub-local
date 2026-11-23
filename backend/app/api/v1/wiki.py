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
    source_type: Literal["kb_doc", "meeting_note", "chat_decision", "sql_snapshot", "data_snapshot", "camera_ocr"]
    content: str = Field(..., description="待编译摄入的原始文本内容")
    source_meta: dict[str, Any] = Field(default_factory=dict, description="来源元数据")
    target_slug: str | None = Field(default=None, description="指定合并目标词条 slug")
    auto_publish: bool = Field(default=False, description="是否跳过审阅直接收藏发布（不覆写既有官方词条）")


class ImportMarkdownSplitRequest(BaseModel):
    wiki_space_id: str = Field(..., description="目标 Wiki 空间 ID")
    filename: str = Field(..., description="导入的 Markdown 文件名")
    content: str = Field(..., description="Markdown 完整正文")
    auto_split: bool = Field(default=True, description="是否按二级标题 (##) 自动拆解归类")
    auto_publish: bool = Field(default=True, description="是否直接发布为正式词条")
    kb_id: str = Field(default="", description="可选关联知识库 ID")


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


@router.delete("/spaces/{space_id}")
async def delete_wiki_space(space_id: str) -> dict[str, Any]:
    """删除整个 Wiki 空间及其下所有词条与索引。"""
    try:
        res = await WikiStorageManager.delete_space(space_id)
        return {"success": True, "data": res}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


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


@router.post("/import/markdown-split")
async def import_markdown_split_to_wiki(req: ImportMarkdownSplitRequest) -> dict[str, Any]:
    """导入总纲 Markdown 文档，原件存入 raw/ 并按二级标题自动拆解归类落盘。"""
    try:
        res = await WikiStorageManager.import_markdown_split(
            wiki_space_id=req.wiki_space_id,
            filename=req.filename,
            content=req.content,
            auto_split=req.auto_split,
            auto_publish=req.auto_publish,
            kb_id=req.kb_id,
        )
        return {"success": True, "data": res}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/pages/{page_id}")
async def delete_wiki_page(
    page_id: str,
    hard_delete: bool = Query(default=False, description="True 为物理彻底删除，False 为软删除移入 .trash/"),
    reason: str = Query(default="", description="删除原因说明"),
) -> dict[str, Any]:
    """删除 Wiki 词条（支持软删除与物理硬删除）。"""
    try:
        res = await WikiStorageManager.delete_page(page_id, hard_delete=hard_delete, reason=reason)
        return {"success": True, "data": res}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/export/ontology-bundle")
async def export_wiki_ontology_bundle(
    wiki_space_id: str = Query(default="", description="指定 Wiki 空间 ID，留空则检索全部空间"),
    query: str = Query(default="", description="可选过滤关键词"),
    limit: int = Query(default=12, ge=1, le=30),
) -> dict[str, Any]:
    """导出已发布业务口径与 SQL 红线精简包。"""
    res = await WikiStorageManager.export_ontology_bundle(
        wiki_space_id=wiki_space_id,
        query=query,
        limit=limit,
    )
    return {"success": True, "data": res}


class SaveStatsSnapshotRequest(BaseModel):
    """用户确认将已通过红线审计的统计结果沉淀为百科统计快照。"""

    agent_id: str = Field(..., min_length=1, description="归属数字专家 ID")
    title: str = Field(..., min_length=1, max_length=120, description="快照标题/主题")
    markdown_content: str = Field(..., min_length=1, description="完整统计结果 Markdown 内容")
    query_scope: str = Field(default="", description="统计查询条件与口径范围说明")
    verified_sqls: list[str] = Field(default_factory=list, description="已通过审计验证的 SQL 列表")
    profile_id: str = Field(default="", description="关联 Schema Profile ID")
    captured_at: str = Field(default="", description="统计时间字符串")


@router.post("/stats-snapshot")
async def save_wiki_stats_snapshot(req: SaveStatsSnapshotRequest) -> dict[str, Any]:
    """沉淀百科统计快照（若专家尚未绑定百科空间，自动按专家名称创建专属空间并自动绑定）。"""
    try:
        res = await WikiStorageManager.save_stats_snapshot(
            agent_id=req.agent_id,
            title=req.title,
            markdown_content=req.markdown_content,
            query_scope=req.query_scope,
            verified_sqls=req.verified_sqls,
            profile_id=req.profile_id,
            captured_at=req.captured_at,
        )
        return {"success": True, "data": res}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class QuickSaveWikiRequest(BaseModel):
    """沙盒对话一键存到 Wiki 请求（自动归属当前沙盒/专家空间并自动判断来源类型）。"""

    content: str = Field(..., min_length=1, description="待存入的对话内容 Markdown")
    workspace_id: str = Field(default="default", description="当前沙盒工作区 ID")
    workspace_name: str = Field(default="", description="当前沙盒工作区名称")
    agent_id: str = Field(default="", description="当前沙盒或对话绑定的数字专家 ID")
    has_sql: bool = Field(default=False, description="本轮是否包含业务库 SQL 查询结果")
    title_hint: str = Field(default="", description="可选标题提示")


@router.post("/quick-save")
async def quick_save_to_wiki(req: QuickSaveWikiRequest) -> dict[str, Any]:
    """沙盒对话一键存到当前沙盒/专家 Wiki 空间，自动创建空间并自动识别来源类型。"""
    try:
        space = await WikiStorageManager.ensure_workspace_wiki_space(
            workspace_id=req.workspace_id,
            workspace_name=req.workspace_name,
            agent_id=req.agent_id,
        )
        source_type, source_type_label = WikiStorageManager.infer_wiki_source_type(
            req.content, has_sql=req.has_sql
        )
        source_meta: dict[str, Any] = {
            "workspace_id": req.workspace_id,
            "workspace_name": req.workspace_name,
            "agent_id": req.agent_id,
            "auto_inferred_type": source_type,
        }
        if req.title_hint.strip():
            source_meta["title_hint"] = req.title_hint.strip()[:80]

        compiled = await WikiIncrementalCompiler.ingest_and_compile(
            wiki_space_id=space["wiki_space_id"],
            source_type=source_type,
            raw_content=req.content,
            source_meta=source_meta,
            auto_publish=False,
        )
        page_info = compiled.get("page") if isinstance(compiled, dict) else {}
        return {
            "success": True,
            "data": {
                "wiki_space_id": space["wiki_space_id"],
                "wiki_space_name": space.get("name", ""),
                "source_type": source_type,
                "source_type_label": source_type_label,
                "page_title": (page_info or {}).get("title") or req.title_hint or "已沉淀词条",
                "page_id": (page_info or {}).get("page_id") or "",
                "compiled": compiled,
            },
        }
    except Exception as e:
        logger.error(f"[WikiAPI] 一键存入沙盒 Wiki 异常: {e}")
        raise HTTPException(status_code=500, detail=f"存入 Wiki 失败: {e}")

