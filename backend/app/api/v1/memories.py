"""
长期记忆画像管理 (Memory Hub)、多协议算力池 (LLM Providers)、四步跨平台导入向导 (Unified Importer) 与 Skill/MCP 扩展 API
"""
from typing import Any
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field

from ...memory.long_term import long_term_memory
from ...extensions.skill_loader import skill_loader
from ...extensions.mcp_manager import mcp_manager
from ...core.provider_adapter import provider_adapter
from ...core.oauth_google import google_oauth_manager
from ...service.unified_importer import unified_importer

router = APIRouter(tags=["Memories, Providers & Importer"])


# ──────────────────── 1. 长期记忆画像 (Memory Hub) ────────────────────

class MemoryCreateRequest(BaseModel):
    """手动新增一条长期记忆"""
    category: str = Field(default="preference", description="分类: env_info | preference | rule")
    content: str = Field(..., description="记忆内容描述")
    confidence: float = Field(default=0.90, ge=0.1, le=1.0, description="初始置信度权重")
    is_pinned: int | bool = Field(default=0, description="是否置顶豁免30天衰减")
    source: str = Field(default="manual", description="记忆来源标记")


class MemoryUpdateRequest(BaseModel):
    """修改已有长期记忆"""
    category: str | None = None
    content: str | None = None
    confidence: float | None = Field(default=None, ge=0.1, le=1.0)
    is_pinned: int | bool | None = None


class MCPConfigUpdateRequest(BaseModel):
    """更新 mcp_servers.json 配置"""
    mcpServers: dict[str, Any] = Field(default_factory=dict)


@router.get("/memories")
async def list_long_term_memories():
    """获取所有长期记忆画像列表"""
    items = await long_term_memory.list_all()
    return {"items": items, "total": len(items)}


@router.post("/memories")
async def create_long_term_memory(req: MemoryCreateRequest):
    """新增一条长期记忆画像"""
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="记忆内容不能为空")
    item = await long_term_memory.add_memory(
        category=req.category,
        content=req.content,
        confidence=req.confidence,
        source=req.source,
        is_pinned=req.is_pinned,
    )
    return item


@router.patch("/memories/{mem_id}")
async def update_long_term_memory(mem_id: str, req: MemoryUpdateRequest):
    """修改单条长期记忆内容、分类、置信度或置顶状态"""
    ok = await long_term_memory.update_memory(
        mem_id=mem_id,
        content=req.content,
        category=req.category,
        confidence=req.confidence,
        is_pinned=req.is_pinned,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="未找到该条长期记忆")
    return {"status": "ok", "id": mem_id}


@router.delete("/memories/{mem_id}")
async def delete_long_term_memory(mem_id: str):
    """删除单条长期记忆"""
    ok = await long_term_memory.delete_memory(mem_id)
    if not ok:
        raise HTTPException(status_code=404, detail="未找到该条长期记忆")
    return {"status": "ok", "id": mem_id}


@router.delete("/memories")
async def clear_all_long_term_memories():
    """一键清空所有长期记忆画像"""
    deleted = await long_term_memory.clear_all()
    return {"status": "ok", "deleted_count": deleted}


# ──────────────────── 2. Skill & MCP 扩展总线 ────────────────────

class SkillSaveRequest(BaseModel):
    """创建或更新自定义本地 Skill"""
    id: str | None = Field(default=None, description="可选 Skill 目录 ID")
    name: str = Field(..., description="Skill 唯一名称标识，如 docker-deploy-helper")
    description: str = Field(..., description="一句话技能触发与用途说明")
    content: str = Field(..., description="Skill 详细 Markdown 操作规程正文")


class SkillImportRequest(BaseModel):
    """通过 SKILL.md 原始文本或文件内容导入 Skill"""
    filename: str = Field(default="SKILL.md")
    content: str = Field(..., description="完整的 SKILL.md 内容（支持 YAML Frontmatter）")


class MCPServerSaveRequest(BaseModel):
    """添加或更新单个 MCP Server"""
    name: str = Field(..., description="MCP 服务唯一标识，如 github / sqlite / fetch")
    command: str = Field(..., description="启动命令，如 npx / uvx / python / node")
    args: list[str] = Field(default_factory=list, description="启动参数列表")
    env: dict[str, str] = Field(default_factory=dict, description="附加环境变量字典")
    description: str = Field(default="", description="服务说明")
    enabled: bool = Field(default=True, description="是否立即启用")


class MCPImportRequest(BaseModel):
    """通过 JSON 配置批量导入 MCP Servers"""
    raw_json: str = Field(..., description="兼容 mcp_servers.json 或 claude_desktop_config.json 的 JSON 字符串")


@router.get("/extensions/skills")
async def list_installed_skills():
    """获取本地 skills/ 目录下已挂载的所有按需专业技能包"""
    skills = skill_loader.list_skills()
    return {"skills": skills, "total": len(skills)}


@router.post("/extensions/skills")
async def create_or_update_skill(req: SkillSaveRequest):
    """手动新建或更新一个本地按需专业技能包 (skills/<id>/SKILL.md)"""
    if not req.name.strip() or not req.content.strip():
        raise HTTPException(status_code=400, detail="技能名称与正文规程不能为空")
    saved = skill_loader.save_skill(
        name=req.name,
        description=req.description,
        content=req.content,
        skill_id=req.id,
    )
    return {"status": "ok", "skill": saved, "skills": skill_loader.list_skills()}


@router.post("/extensions/skills/import")
async def import_skill_from_markdown(req: SkillImportRequest):
    """导入外部 SKILL.md 文件或粘贴的 Markdown 技能包"""
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="导入的 SKILL.md 内容不能为空")
    saved = skill_loader.import_skill_markdown(raw_md=req.content, filename_hint=req.filename)
    return {"status": "ok", "skill": saved, "skills": skill_loader.list_skills()}


@router.delete("/extensions/skills/{skill_id}")
async def delete_installed_skill(skill_id: str):
    """删除指定的本地 Skill 技能包"""
    ok = skill_loader.delete_skill(skill_id)
    if not ok:
        raise HTTPException(status_code=404, detail="未找到该技能包或已删除")
    return {"status": "ok", "id": skill_id, "skills": skill_loader.list_skills()}


@router.get("/extensions/mcp")
async def get_mcp_servers_config():
    """获取本地 mcp_servers.json 配置"""
    return mcp_manager.load_config()


@router.put("/extensions/mcp")
async def update_mcp_servers_config(req: MCPConfigUpdateRequest):
    """保存并更新 mcp_servers.json 配置"""
    mcp_manager.save_config({"mcpServers": req.mcpServers})
    return {"status": "ok", "mcpServers": req.mcpServers}


@router.post("/extensions/mcp/server")
async def add_or_update_mcp_server(req: MCPServerSaveRequest):
    """新增或更新单个自定义 MCP 服务节点"""
    srv_name = req.name.strip()
    if not srv_name or not req.command.strip():
        raise HTTPException(status_code=400, detail="MCP 服务名称与启动命令不能为空")
    cfg = mcp_manager.load_config()
    servers = cfg.setdefault("mcpServers", {})
    servers[srv_name] = {
        "command": req.command.strip(),
        "args": [a.strip() for a in req.args if a.strip()],
        "env": req.env or {},
        "description": req.description.strip() or f"自定义 MCP 服务 ({srv_name})",
        "enabled": req.enabled,
    }
    mcp_manager.save_config({"mcpServers": servers})
    return {"status": "ok", "mcpServers": servers}


@router.post("/extensions/mcp/import")
async def import_mcp_servers_json(req: MCPImportRequest):
    """从粘贴或上传的 JSON 配置中批量导入 MCP 服务节点"""
    import json
    try:
        parsed = json.loads(req.raw_json.strip())
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"JSON 格式解析失败: {str(e)}")

    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="MCP 配置必须为 JSON 对象")

    incoming = parsed.get("mcpServers") if isinstance(parsed.get("mcpServers"), dict) else parsed
    cfg = mcp_manager.load_config()
    servers = cfg.setdefault("mcpServers", {})
    imported_count = 0

    for k, v in incoming.items():
        if isinstance(v, dict) and v.get("command"):
            servers[k.strip()] = {
                "command": str(v.get("command", "")).strip(),
                "args": [str(a) for a in (v.get("args") or [])],
                "env": v.get("env") if isinstance(v.get("env"), dict) else {},
                "description": str(v.get("description") or f"导入的 MCP 服务 ({k})"),
                "enabled": bool(v.get("enabled", True)),
            }
            imported_count += 1

    if imported_count == 0:
        raise HTTPException(
            status_code=400,
            detail="未在 JSON 中识别到包含 'command' 字段的有效 MCP 服务配置",
        )

    mcp_manager.save_config({"mcpServers": servers})
    return {"status": "ok", "imported_count": imported_count, "mcpServers": servers}


@router.delete("/extensions/mcp/{server_name}")
async def delete_mcp_server(server_name: str):
    """删除指定的 MCP 服务节点"""
    cfg = mcp_manager.load_config()
    servers = cfg.get("mcpServers", {})
    if server_name in servers:
        del servers[server_name]
        mcp_manager.save_config({"mcpServers": servers})
    return {"status": "ok", "mcpServers": servers}


# ──────────────────── 3. 多协议大模型供应商池 (LLM Providers) ────────────────────

class LLMProviderSaveRequest(BaseModel):
    """保存或更新大模型供应商请求"""
    id: str | None = None
    name: str = Field(..., description="供应商显示名称，如 DeepSeek 官方 / 本地 Ollama")
    provider_type: str = Field(default="openai_compat", description="google_pool | gemini_api | openai_compat | anthropic")
    base_url: str = Field(default="", description="API Base URL，如 https://api.deepseek.com/v1 或 http://127.0.0.1:11434/v1")
    api_key: str = Field(default="", description="API Key（留空则在更新时保持原加密 Key）")
    models: list[str] = Field(default_factory=list, description="该通道支持的模型列表")
    role_binding: str = Field(default="both", description="primary(主力推理) | light(后台记忆提炼) | both(通用)")
    tool_call_mode: str = Field(default="auto", description="auto | native | xml_prompt")
    priority: int = Field(default=5, description="调度优先级（越小越优先）")
    is_enabled: int = Field(default=1, description="1=启用, 0=禁用")


class GoogleOAuthInitRequest(BaseModel):
    """发起 Google OAuth PKCE 授权会话请求"""
    remark: str | None = Field(default=None, description="账号备注名称，如 用户A的主力账号")
    role_binding: str = Field(default="both", description="primary | light | both")
    priority: int = Field(default=5, description="故障转移优先级（数字越小越优先）")


class GoogleOAuthManualCallbackRequest(BaseModel):
    """手动粘贴 Google OAuth 回调 URL 完成授权"""
    callback_url: str = Field(..., description="浏览器地址栏完整回调 URL (含 code 和 state)")
    state: str | None = Field(default=None, description="可选兜底 state")
    remark: str | None = Field(default=None, description="账号备注名称")
    role_binding: str = Field(default="both", description="primary | light | both")
    priority: int = Field(default=5, description="故障转移优先级")


@router.get("/providers")
@router.get("/providers/list")
async def list_llm_providers():
    """获取所有已配置的大模型供应商列表（含冷却状态与令牌标识）"""
    items = await provider_adapter.list_providers()
    return {"providers": items, "total": len(items)}


@router.post("/providers/oauth/init")
async def init_google_oauth_provider(req: GoogleOAuthInitRequest | None = None):
    """
    发起 Google 官方桌面端 PKCE OAuth 授权：
    生成带 code_challenge 的授权链接，同时尝试在本地 1455 端口启动自动回环监听。
    """
    r = req or GoogleOAuthInitRequest()
    return await google_oauth_manager.create_auth_session(
        remark=r.remark,
        role_binding=r.role_binding,
        priority=r.priority,
    )


@router.get("/providers/oauth/status/{state}")
async def get_google_oauth_session_status(state: str):
    """轮询查询 1455 端口自动回环是否已完成指定 state 的 Google OAuth 授权"""
    return google_oauth_manager.get_session_status(state)


@router.post("/providers/oauth/manual-callback")
async def manual_callback_google_oauth_provider(req: GoogleOAuthManualCallbackRequest):
    """
    模式 B 手动粘贴完整回调 URL 兜底：
    从 URL 中提取 code 与 state，完成 PKCE 令牌换取并加密写入 llm_providers 表。
    """
    try:
        saved = await google_oauth_manager.complete_by_manual_url(
            callback_url=req.callback_url,
            fallback_state=req.state,
            remark=req.remark,
            role_binding=req.role_binding,
            priority=req.priority,
        )
        return {"status": "ok", "provider": saved}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Google OAuth 令牌换取失败: {str(e)}")


class GoogleOAuthTokenImportRequest(BaseModel):
    token_or_json: str = Field(..., description="Refresh Token 字符串或包含 client_id/refresh_token 的 JSON 片段")
    remark: str | None = Field(default=None)
    role_binding: str = Field(default="both")
    priority: int = Field(default=5)


@router.post("/providers/oauth/import-token")
async def import_google_oauth_token(req: GoogleOAuthTokenImportRequest):
    """方式 C (Token/JSON 直填)：验证 Refresh Token 有效性并直接导入入库"""
    try:
        saved = await google_oauth_manager.complete_by_refresh_token(
            raw_token_or_json=req.token_or_json,
            remark=req.remark,
            role_binding=req.role_binding,
            priority=req.priority,
        )
        return {"status": "ok", "provider": saved}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Refresh Token 验证失败: {str(e)}")


@router.post("/providers")
async def save_llm_provider(req: LLMProviderSaveRequest):
    """新增或更新一个大模型供应商配置"""
    saved = await provider_adapter.save_provider(
        name=req.name,
        provider_type=req.provider_type,
        base_url=req.base_url,
        api_key=req.api_key,
        models=req.models,
        role_binding=req.role_binding,
        tool_call_mode=req.tool_call_mode,
        priority=req.priority,
        is_enabled=req.is_enabled,
        provider_id=req.id,
    )
    return saved


class LLMProviderTestRequest(BaseModel):
    provider_type: str = Field(default="openai_compat")
    base_url: str = Field(default="")
    api_key: str = Field(default="")
    model_name: str = Field(default="")


@router.post("/providers/test-connection")
async def test_provider_connection(req: LLMProviderTestRequest):
    """探测指定大模型端点或本地 Ollama 的实时连通性、网络延迟与模型列表"""
    res = await provider_adapter.test_connection(
        provider_type=req.provider_type,
        base_url=req.base_url,
        api_key=req.api_key,
        model_name=req.model_name,
    )
    return res


@router.delete("/providers/{provider_id}")
async def delete_llm_provider(provider_id: str):
    """删除大模型供应商通道（完全放开权限，无任何死锁）"""
    ok = await provider_adapter.delete_provider(provider_id)
    return {"status": "ok", "deleted": ok, "id": provider_id}


# ──────────────────── 4. 四步可视化跨平台数据导入向导 (Unified Importer) ────────────────────

class ImportTextParseRequest(BaseModel):
    """直接粘贴 Markdown 规则或 JSON 文本进行解析嗅探"""
    filename: str = Field(default="GEMINI.md")
    content: str = Field(..., description="粘贴的规则或 JSON 文本")
    source_hint: str | None = Field(default=None)


class ImportCommitRequest(BaseModel):
    """Step 4 确认执行事务导入"""
    preview_id: str = Field(..., description="Step 1 解析返回的 preview_id")
    import_conversations: bool = Field(default=True, description="是否导入历史聊天记录并建立 FTS5 索引")
    import_memories: bool = Field(default=True, description="是否导入显式规则与长期记忆")
    extract_implicit: bool = Field(default=False, description="是否从历史长对话中自动挖掘隐性偏好")
    memory_resolutions: list[dict[str, Any]] | None = Field(
        default=None,
        description="用户在 Step 3 冲突比对表中指定的处理动作列表 [{'temp_id': '...', 'resolution': 'add|overwrite|keep_both|skip'}]",
    )


@router.post("/import/parse")
async def parse_import_file(
    file: UploadFile = File(...),
    source_hint: str | None = Form(default=None),
):
    """
    Step 1 上传文件嗅探与 Step 3 冲突比对预览：
    支持拖拽上传 .zip / .json / .md / .cursorrules 文件。
    """
    try:
        raw_bytes = await file.read()
        result = await unified_importer.parse_and_stage(
            filename=file.filename or "import_bundle.json",
            raw_bytes=raw_bytes,
            source_hint=source_hint,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"解析导入文件失败: {str(e)}")


@router.post("/import/parse-text")
async def parse_import_raw_text(req: ImportTextParseRequest):
    """Step 1 支持直接粘贴 GEMINI.md / .cursorrules 或 JSON 文本进行解析预览"""
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="粘贴内容不能为空")
    try:
        result = await unified_importer.parse_and_stage(
            filename=req.filename,
            raw_bytes=req.content.encode("utf-8"),
            source_hint=req.source_hint,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"解析粘贴文本失败: {str(e)}")


@router.post("/import/commit")
async def commit_staged_import(req: ImportCommitRequest):
    """Step 4 开启 BEGIN IMMEDIATE 批量事务落盘并生成可回滚批次记录"""
    try:
        res = await unified_importer.commit_import(
            preview_id=req.preview_id,
            import_conversations=req.import_conversations,
            import_memories=req.import_memories,
            extract_implicit=req.extract_implicit,
            memory_resolutions=req.memory_resolutions,
        )
        return res
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"事务导入失败: {str(e)}")


@router.get("/import/batches")
async def list_import_batches():
    """获取所有历史导入批次（供一键撤回某次导入使用）"""
    batches = await unified_importer.list_batches()
    return {"batches": batches, "total": len(batches)}


@router.delete("/import/batches/{batch_id}")
async def rollback_import_batch(batch_id: str):
    """一键按批次撤回导入的所有会话、FTS5索引与长期记忆"""
    stats = await unified_importer.rollback_batch(batch_id)
    return {"status": "ok", "batch_id": batch_id, **stats}
