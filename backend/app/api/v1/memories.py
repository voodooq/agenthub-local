"""
长期记忆画像管理 (Memory Hub)、多协议算力池 (LLM Providers)、四步跨平台导入向导 (Unified Importer) 与 Skill/MCP 扩展 API
"""
import asyncio
import copy
import json
import re
from typing import Any, Literal
from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from pydantic import BaseModel, Field

from ...memory.long_term import long_term_memory
from ...memory.fact_manager import fact_manager
from ...memory.ledger import event_ledger
from ...memory.policy_guard import MemoryAdmissionError
from ...extensions.skill_loader import skill_loader
from ...extensions.mcp_manager import (
    extract_mcp_servers_map,
    extract_origin,
    is_valid_mcp_server_name,
    mcp_manager,
    normalize_transport_name,
    parse_mcp_server_entry,
)
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


# ──────────────────── 1B. 双时间轴结构化事实视图与事件账本 (v1.1.6) ────────────────────

class MemoryFactUpsertRequest(BaseModel):
    """新增或更新确定性事实（触发双时间轴冲突消解）"""
    fact_key: str = Field(..., description="标准化事实键，如 refund_fee_rate / export_format")
    fact_value: str = Field(..., description="确定性事实值")
    workspace_id: str = Field(default="GLOBAL", description="作用域：GLOBAL 或具体工作区 ID")
    category: str = Field(default="biz_rule", description="分类: user_pref | biz_rule | env_config")
    source_type: str = Field(default="manual", description="准入来源: manual | explicit_cmd | inquiry | verified_env")
    source_event_id: str | None = Field(default=None, description="可选关联的 event_ledger.id 溯源")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


@router.get("/memories/facts")
async def list_memory_facts(
    workspace_id: str | None = Query(default=None, description="指定工作区 ID（不传默认仅返回 GLOBAL）"),
    include_history: bool = Query(default=False, description="是否包含已退位的历史版本时间线"),
    include_global: bool = Query(default=True, description="工作区查询时是否同时合并 GLOBAL 全局偏好"),
    category: str | None = Query(default=None, description="过滤分类: user_pref | biz_rule | env_config"),
    fact_key: str | None = Query(default=None, description="按特定 fact_key 精确查看演进时间轴"),
):
    """获取双时间轴结构化事实视图与演进历史"""
    try:
        items = await fact_manager.list_facts(
            workspace_id=workspace_id,
            include_history=include_history,
            include_global=include_global,
            category=category,
            fact_key=fact_key,
        )
        active_view = await fact_manager.get_active_facts(
            workspace_id=workspace_id,
            include_global=include_global,
            category=category,
        )
        return {
            "items": items,
            "active_view": active_view,
            "total": len(items),
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.post("/memories/facts")
async def upsert_memory_fact(req: MemoryFactUpsertRequest):
    """新增或覆盖一条确定性结构化事实（自动关闭旧版本并执行隐私脱敏）"""
    if not req.fact_key.strip() or not req.fact_value.strip():
        raise HTTPException(status_code=400, detail="fact_key 与 fact_value 均不能为空")
    try:
        result = await fact_manager.upsert_fact(
            fact_key=req.fact_key,
            fact_value=req.fact_value,
            workspace_id=req.workspace_id,
            category=req.category,
            source_type=req.source_type,
            source_event_id=req.source_event_id,
            confidence=req.confidence,
        )
        return result
    except MemoryAdmissionError as mae:
        raise HTTPException(status_code=403, detail=str(mae))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.post("/memories/facts/{fact_id}/rollback")
async def rollback_memory_fact(fact_id: str):
    """将指定历史版本事实重新激活为当前有效版本"""
    try:
        return await fact_manager.rollback_fact(fact_id)
    except LookupError as le:
        raise HTTPException(status_code=404, detail=str(le))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.post("/memories/facts/{fact_id}/deactivate")
async def deactivate_memory_fact(fact_id: str):
    """软停用一条当前生效中的结构化事实（保留历史版本记录）"""
    ok = await fact_manager.deactivate_fact(fact_id)
    if not ok:
        raise HTTPException(status_code=404, detail="未找到当前生效的事实记录或已归档")
    return {"status": "ok", "fact_id": fact_id}


@router.get("/memories/ledger")
async def list_event_ledger(
    workspace_id: str | None = Query(default=None),
    conversation_id: str | None = Query(default=None),
    loop_id: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    ascending: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """按会话或工作区分页审计只增不改的 event_ledger 原始交互轨迹"""
    return await event_ledger.list_events(
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        loop_id=loop_id,
        event_type=event_type,
        ascending=ascending,
        limit=limit,
        offset=offset,
    )


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


class SkillRepoInstallRequest(BaseModel):
    """通过 GitHub 简写 (github:owner/repo) 或 URL 一键拉取安装四元组 Skill"""
    source: str = Field(..., description="如 github:owner/repo/skills/my-skill 或 https://.../SKILL.md")


class MCPServerSaveRequest(BaseModel):
    """添加或更新单个 MCP Server（兼容本地 stdio 与远程 Streamable HTTP / SSE）"""
    name: str = Field(..., description="MCP 服务唯一标识，如 github / sqlite / context7（严禁含 __）")
    transport: Literal["stdio", "streamable-http", "streamableHttp", "streamable_http", "http", "sse"] | None = Field(
        default=None,
        description="传输协议类型：stdio | streamable-http | sse（缺省时根据 url/command 自动推断）",
    )
    command: str = Field(default="", description="本地 stdio 启动命令，如 npx / uvx / python / node")
    args: list[str] = Field(default_factory=list, description="本地 stdio 启动参数列表")
    env: dict[str, str] = Field(default_factory=dict, description="本地 stdio 附加环境变量字典")
    url: str = Field(default="", description="远程 MCP 服务端点 URL (http:// 或 https://)")
    headers: dict[str, str] = Field(default_factory=dict, description="远程请求头（可为空 {}，支持 ${ENV_VAR}）")
    timeout_seconds: float | None = Field(default=None, ge=5.0, le=600.0, description="单次工具调用超时秒数（远程默认 60s）")
    upload_endpoint: str = Field(default="", description="可选：跨网文件协同上传端点 URL")
    upload_file_field: str = Field(default="file", description="可选：multipart 上传文件字段名，默认 file")
    download_origins: list[str] = Field(default_factory=list, description="可选：允许下载产物 URL 的额外同源白名单")
    description: str = Field(default="", description="服务说明")
    enabled: bool = Field(default=True, description="是否立即启用")


class MCPServerToggleRequest(BaseModel):
    """单项切换 MCP Server 启用状态"""
    enabled: bool | None = Field(default=None, description="目标启用状态；不传则自动取反")


class MCPServerTestRequest(BaseModel):
    """实时探测单个 MCP Server 连通性与工具数量"""
    server_name: str | None = Field(default=None, description="已保存服务名称；仅传此项时直接按库中已落盘配置测试")
    transport: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    timeout_seconds: float | None = Field(default=None, ge=5.0, le=600.0)
    upload_endpoint: str | None = None
    upload_file_field: str | None = None
    download_origins: list[str] | None = None


class MCPImportRequest(BaseModel):
    """通过 JSON 配置批量导入 MCP Servers"""
    raw_json: str = Field(..., description="兼容 mcp_servers.json / Claude Desktop / Cursor / VS Code 的 JSON 字符串")


@router.get("/extensions/skills")
async def list_installed_skills(
    include_content: bool = Query(False, description="是否一并返回完整 Markdown 正文，默认 False 仅返摘要"),
    refresh: bool = Query(False, description="是否强制绕过列表短窗口快照缓存即时重扫磁盘"),
):
    """获取本地 skills/ 目录下已挂载的所有按需专业技能包（含绑定的 /command 斜杠指令）"""
    skills = await asyncio.to_thread(
        skill_loader.list_skills,
        include_content=include_content,
        force_refresh=refresh,
    )
    return {"skills": skills, "total": len(skills)}


@router.get("/extensions/skills/{skill_id}")
async def get_installed_skill_detail(skill_id: str):
    """按需获取单个扩展技能包或 MCP 说明的完整 SKILL.md 与解析正文"""
    detail = await asyncio.to_thread(skill_loader.get_skill_detail_item, skill_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"未找到技能包: {skill_id}")
    return detail


@router.post("/extensions/skills")
async def create_or_update_skill(req: SkillSaveRequest):
    """手动新建或更新一个本地按需专业技能包 (skills/<id>/SKILL.md)"""
    if not req.name.strip() or not req.content.strip():
        raise HTTPException(status_code=400, detail="技能名称与正文规程不能为空")
    saved = await asyncio.to_thread(
        skill_loader.save_skill,
        name=req.name,
        description=req.description,
        content=req.content,
        skill_id=req.id,
    )
    skills = await asyncio.to_thread(skill_loader.list_skills, include_content=False)
    return {"status": "ok", "skill": saved, "skills": skills}


@router.post("/extensions/skills/import")
async def import_skill_from_markdown(req: SkillImportRequest):
    """导入外部 SKILL.md 文件或粘贴的 Markdown 技能包"""
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="导入的 SKILL.md 内容不能为空")
    saved = await asyncio.to_thread(
        skill_loader.import_skill_markdown,
        raw_md=req.content,
        filename_hint=req.filename,
    )
    skills = await asyncio.to_thread(skill_loader.list_skills, include_content=False)
    return {"status": "ok", "skill": saved, "skills": skills}


@router.post("/extensions/skills/install-repo")
async def install_skill_from_repo(req: SkillRepoInstallRequest):
    """一键从 GitHub 仓库或远程 URL 安装标准四元组 Skill 技能包"""
    try:
        saved = await skill_loader.install_from_repo_or_url(req.source)
        skills = await asyncio.to_thread(skill_loader.list_skills, include_content=False)
        return {"status": "ok", "skill": saved, "skills": skills}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"一键安装 Skill 失败: {str(e)}")


@router.delete("/extensions/skills/{skill_id}")
async def delete_installed_skill(skill_id: str):
    """删除指定的本地 Skill 技能包"""
    ok = await asyncio.to_thread(skill_loader.delete_skill, skill_id)
    if not ok:
        raise HTTPException(status_code=404, detail="未找到该技能包或已删除")
    skills = await asyncio.to_thread(skill_loader.list_skills, include_content=False)
    return {"status": "ok", "id": skill_id, "skills": skills}


_SENSITIVE_HEADER_KEYWORDS = (
    "authorization",
    "token",
    "key",
    "secret",
    "cookie",
    "password",
    "credential",
    "signature",
    "auth",
)
_ENV_PLACEHOLDER_TOKEN_RE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")
_AUTH_SCHEME_PREFIXES = ("bearer", "basic", "token", "apikey", "digest")
_MASK_MARKER = "****"


def _is_sensitive_header_key(header_key: str) -> bool:
    """判断请求头名称是否属于认证凭证等敏感字段。"""
    lowered = (header_key or "").strip().lower()
    return any(kw in lowered for kw in _SENSITIVE_HEADER_KEYWORDS)


def _is_env_placeholder_only_value(value: str) -> bool:
    """判断 Header 值是否仅由可选授权前缀与 ${ENV_VAR} 环境变量占位符组成（此类配置应豁免脱敏）。"""
    stripped = (value or "").strip()
    if not stripped or not _ENV_PLACEHOLDER_TOKEN_RE.search(stripped):
        return False
    without_vars = _ENV_PLACEHOLDER_TOKEN_RE.sub("", stripped).strip()
    if not without_vars:
        return True
    return without_vars.lower() in _AUTH_SCHEME_PREFIXES


def _is_masked_secret_value(value: str) -> bool:
    """判断字符串是否为前端回传的脱敏掩码占位符。"""
    return _MASK_MARKER in (value or "")


def _mask_secret_header_value(value: str) -> str:
    """对敏感请求头实际明文做局部掩码脱敏，保留 ${ENV_VAR} 占位符原样输出。"""
    text = str(value or "")
    if not text or _is_env_placeholder_only_value(text):
        return text
    if _is_masked_secret_value(text):
        return text

    stripped = text.strip()
    parts = stripped.split(None, 1)
    if len(parts) == 2 and parts[0].lower() in _AUTH_SCHEME_PREFIXES:
        scheme, secret = parts[0], parts[1].strip()
        if len(secret) <= 6:
            return f"{scheme} {_MASK_MARKER}"
        return f"{scheme} {secret[:2]}{_MASK_MARKER}{secret[-4:]}"

    if len(stripped) <= 6:
        return _MASK_MARKER
    return f"{stripped[:2]}{_MASK_MARKER}{stripped[-4:]}"


def _mask_mcp_server_entry(entry: Any) -> Any:
    """生成单个 MCP 服务配置的安全响应副本，对敏感 Header 执行掩码脱敏。"""
    if not isinstance(entry, dict):
        return entry
    cloned = copy.deepcopy(entry)
    headers = cloned.get("headers")
    if isinstance(headers, dict):
        masked_headers: dict[str, str] = {}
        for k, v in headers.items():
            key_str = str(k)
            val_str = str(v if v is not None else "")
            masked_headers[key_str] = (
                _mask_secret_header_value(val_str) if _is_sensitive_header_key(key_str) else val_str
            )
        cloned["headers"] = masked_headers
    return cloned


def _mask_mcp_servers_map(servers: dict[str, Any]) -> dict[str, Any]:
    """批量生成脱敏后的 mcpServers 响应映射。"""
    if not isinstance(servers, dict):
        return {}
    return {k: _mask_mcp_server_entry(v) for k, v in servers.items()}


def _merge_masked_headers(
    incoming_headers: dict[str, Any] | None,
    existing_headers: dict[str, Any] | None,
) -> dict[str, str]:
    """写盘前合并请求头：若敏感 Header 为留空或脱敏掩码格式，沿用旧配置真实值防覆写。"""
    if not isinstance(incoming_headers, dict):
        return {}
    old_map: dict[str, str] = {}
    old_lower_map: dict[str, str] = {}
    if isinstance(existing_headers, dict):
        for ek, ev in existing_headers.items():
            eks = str(ek).strip()
            evs = str(ev if ev is not None else "")
            if eks:
                old_map[eks] = evs
                old_lower_map[eks.lower()] = evs

    merged: dict[str, str] = {}
    for raw_k, raw_v in incoming_headers.items():
        k = str(raw_k).strip()
        if not k:
            continue
        v = str(raw_v if raw_v is not None else "")
        if _is_sensitive_header_key(k) and (v.strip() == "" or _is_masked_secret_value(v)):
            preserved = old_map.get(k) if k in old_map else old_lower_map.get(k.lower())
            if preserved is not None and not _is_masked_secret_value(preserved):
                merged[k] = preserved
            elif not _is_masked_secret_value(v):
                merged[k] = v
            # NOTE: 若既无旧明文又是掩码占位符，则丢弃以免把 '****' 写入配置
            continue
        merged[k] = v
    return merged


def _validate_and_normalize_server_payload(
    srv_name: str,
    raw_payload: dict[str, Any],
    *,
    existing_entry: dict[str, Any] | None = None,
    default_description: str | None = None,
) -> dict[str, Any]:
    """统一校验与构建可安全落盘的单个 MCP 服务字典，兼容 stdio 与远程 HTTP/SSE。"""
    if not is_valid_mcp_server_name(srv_name):
        raise HTTPException(
            status_code=400,
            detail=f"MCP 服务名称 '{srv_name}' 非法：不能为空且严禁包含双下划线 `__`（避免工具路由命名冲突）",
        )

    existing_headers = existing_entry.get("headers") if isinstance(existing_entry, dict) else None
    merged_raw = dict(raw_payload)
    merged_raw["headers"] = _merge_masked_headers(raw_payload.get("headers"), existing_headers)

    norm = parse_mcp_server_entry(srv_name, merged_raw)
    transport = norm["transport"]
    desc = norm["description"] or default_description or f"自定义 MCP 服务 ({srv_name})"

    if transport == "stdio":
        if not norm["command"]:
            raise HTTPException(status_code=400, detail=f"MCP 服务 [{srv_name}] 为 stdio 模式时启动命令 command 不能为空")
        entry: dict[str, Any] = {
            "transport": "stdio",
            "command": norm["command"],
            "args": [a.strip() for a in norm["args"] if a.strip()],
            "env": norm["env"],
            "description": desc,
            "enabled": bool(raw_payload.get("enabled", True)),
        }
        if raw_payload.get("timeout_seconds") is not None:
            entry["timeout_seconds"] = norm["timeout_seconds"]
        return entry

    url = norm["url"]
    if not url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail=f"远程 MCP 服务 [{srv_name}] 的 url 必须以 http:// 或 https:// 开头",
        )

    raw_upload_ep = str(raw_payload.get("upload_endpoint") or "").strip()
    if raw_upload_ep and not raw_upload_ep.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail=f"远程 MCP 服务 [{srv_name}] 的 upload_endpoint 必须以 http:// 或 https:// 开头",
        )

    raw_origins = raw_payload.get("download_origins")
    validated_origins: list[str] = []
    if isinstance(raw_origins, list):
        for item in raw_origins:
            text_item = str(item or "").strip()
            if not text_item:
                continue
            origin = extract_origin(text_item)
            if not origin:
                raise HTTPException(
                    status_code=400,
                    detail=f"download_origins 包含无效源地址 '{text_item}'，示例格式：https://files.example.com",
                )
            if origin not in validated_origins:
                validated_origins.append(origin)

    entry = {
        "transport": transport,
        "url": url,
        "headers": norm["headers"],
        "timeout_seconds": norm["timeout_seconds"],
        "description": desc,
        "enabled": bool(raw_payload.get("enabled", True)),
    }
    if norm["upload_endpoint"]:
        entry["upload_endpoint"] = norm["upload_endpoint"]
    if norm["upload_file_field"] and norm["upload_file_field"] != "file":
        entry["upload_file_field"] = norm["upload_file_field"]
    elif norm["upload_endpoint"]:
        entry["upload_file_field"] = norm["upload_file_field"] or "file"
    if validated_origins:
        entry["download_origins"] = validated_origins
    return entry


@router.get("/extensions/mcp")
async def get_mcp_servers_config():
    """获取 MCP 服务配置（对敏感 Header 脱敏但豁免 ${ENV_VAR}，并附带真实配置路径）"""
    cfg = mcp_manager.load_config()
    servers = cfg.get("mcpServers", {}) if isinstance(cfg, dict) else {}
    return {
        "mcpServers": _mask_mcp_servers_map(servers),
        "config_path": str(mcp_manager.config_path),
    }


@router.put("/extensions/mcp")
async def update_mcp_servers_config(req: MCPConfigUpdateRequest):
    """全量保存并更新 MCP 配置（写盘前对掩码/留空敏感 Header 自动沿用原真实密钥）"""
    old_cfg = mcp_manager.load_config()
    old_servers = old_cfg.get("mcpServers", {}) if isinstance(old_cfg, dict) else {}
    new_servers: dict[str, Any] = {}

    for raw_name, raw_entry in (req.mcpServers or {}).items():
        srv_name = str(raw_name).strip()
        if not is_valid_mcp_server_name(srv_name):
            raise HTTPException(
                status_code=400,
                detail=f"MCP 服务名称 '{raw_name}' 非法：不能为空且严禁包含双下划线 `__`",
            )
        if not isinstance(raw_entry, dict):
            raise HTTPException(status_code=400, detail=f"MCP 服务 [{srv_name}] 配置项必须为 JSON 对象")
        merged_entry = copy.deepcopy(raw_entry)
        existing_entry = old_servers.get(srv_name) if isinstance(old_servers, dict) else None
        if isinstance(merged_entry.get("headers"), dict):
            existing_headers = existing_entry.get("headers") if isinstance(existing_entry, dict) else None
            merged_entry["headers"] = _merge_masked_headers(merged_entry["headers"], existing_headers)
        new_servers[srv_name] = merged_entry

    mcp_manager.save_config({"mcpServers": new_servers})
    skill_loader.invalidate_cache()
    return {
        "status": "ok",
        "mcpServers": _mask_mcp_servers_map(new_servers),
        "config_path": str(mcp_manager.config_path),
    }


@router.post("/extensions/mcp/server")
async def add_or_update_mcp_server(req: MCPServerSaveRequest):
    """新增或更新单个自定义 MCP 服务节点（支持 stdio 与远程 Streamable HTTP / SSE）"""
    srv_name = req.name.strip()
    cfg = mcp_manager.load_config()
    servers = cfg.setdefault("mcpServers", {})
    existing_entry = servers.get(srv_name) if isinstance(servers, dict) else None

    raw_payload: dict[str, Any] = {
        "command": req.command,
        "args": req.args,
        "env": req.env,
        "url": req.url,
        "headers": req.headers,
        "upload_endpoint": req.upload_endpoint,
        "upload_file_field": req.upload_file_field,
        "download_origins": req.download_origins,
        "description": req.description,
        "enabled": req.enabled,
    }
    if req.transport is not None:
        raw_payload["transport"] = req.transport
    if req.timeout_seconds is not None:
        raw_payload["timeout_seconds"] = req.timeout_seconds

    servers[srv_name] = _validate_and_normalize_server_payload(
        srv_name,
        raw_payload,
        existing_entry=existing_entry,
    )
    mcp_manager.save_config({"mcpServers": servers})
    skill_loader.invalidate_cache()
    return {
        "status": "ok",
        "mcpServers": _mask_mcp_servers_map(servers),
        "config_path": str(mcp_manager.config_path),
    }


@router.patch("/extensions/mcp/{server_name}/toggle")
async def toggle_mcp_server(server_name: str, req: MCPServerToggleRequest | None = None):
    """单项启停 MCP 服务，避免前端仅开关状态时全表回传覆盖已保存密钥"""
    cfg = mcp_manager.load_config()
    servers = cfg.get("mcpServers", {})
    if server_name not in servers or not isinstance(servers.get(server_name), dict):
        raise HTTPException(status_code=404, detail=f"未找到 MCP 服务: {server_name}")

    current_enabled = bool(servers[server_name].get("enabled", False))
    next_enabled = req.enabled if (req is not None and req.enabled is not None) else (not current_enabled)
    servers[server_name]["enabled"] = next_enabled
    mcp_manager.save_config({"mcpServers": servers})
    skill_loader.invalidate_cache()
    return {
        "status": "ok",
        "name": server_name,
        "enabled": next_enabled,
        "mcpServers": _mask_mcp_servers_map(servers),
    }


@router.post("/extensions/mcp/test")
async def test_mcp_server_connection(req: MCPServerTestRequest):
    """实时探测 MCP 服务连通性，严格隔离临时 URL 与库内已保存敏感密钥"""
    cfg = mcp_manager.load_config()
    servers = cfg.get("mcpServers", {}) if isinstance(cfg, dict) else {}
    req_name = (req.server_name or "").strip()
    saved_entry = servers.get(req_name) if (req_name and isinstance(servers.get(req_name), dict)) else None

    has_override = any(
        val is not None
        for val in (
            req.transport,
            req.command,
            req.args,
            req.env,
            req.url,
            req.headers,
            req.timeout_seconds,
            req.upload_endpoint,
            req.upload_file_field,
            req.download_origins,
        )
    )

    if not has_override:
        if not saved_entry:
            raise HTTPException(status_code=404, detail=f"未找到已保存的 MCP 服务: {req_name or '(empty)'}")
        return await mcp_manager.test_server_connection(req_name, saved_entry)

    target_name = req_name or "test-mcp"
    if not is_valid_mcp_server_name(target_name):
        raise HTTPException(status_code=400, detail="MCP 服务名称不能包含双下划线 `__`")

    saved_url = str(saved_entry.get("url") or saved_entry.get("serverUrl") or "").strip() if saved_entry else ""
    req_url = req.url.strip() if req.url is not None else None
    # NOTE: 一旦更改了目标 URL，严格禁止复用旧服务已保存的敏感请求头，防止测活接口将库内凭证发往第三方端点
    url_changed = bool(saved_entry) and (req_url is not None and req_url != saved_url)

    effective_headers: dict[str, str] = {}
    if url_changed:
        incoming = req.headers or {}
        for k, v in incoming.items():
            ks, vs = str(k).strip(), str(v if v is not None else "")
            if _is_sensitive_header_key(ks) and _is_masked_secret_value(vs):
                raise HTTPException(
                    status_code=400,
                    detail="修改远程端点 URL 后禁止沿用脱敏凭证测试，请重新填写完整请求头或清空免鉴权字段",
                )
            if ks:
                effective_headers[ks] = vs
    elif saved_entry:
        saved_headers = saved_entry.get("headers") if isinstance(saved_entry.get("headers"), dict) else {}
        if req.headers is not None:
            effective_headers = _merge_masked_headers(req.headers, saved_headers)
        else:
            effective_headers = dict(saved_headers)
    else:
        incoming = req.headers or {}
        for k, v in incoming.items():
            ks, vs = str(k).strip(), str(v if v is not None else "")
            if _is_sensitive_header_key(ks) and _is_masked_secret_value(vs):
                raise HTTPException(status_code=400, detail="测试临时配置时请填写完整未脱敏的请求头")
            if ks:
                effective_headers[ks] = vs

    # 若未修改 URL 且存在已存配置，其余未显式传入字段从 saved_entry 安全补齐
    base_cfg = copy.deepcopy(saved_entry) if (saved_entry and not url_changed) else {}
    if req.transport is not None:
        base_cfg["transport"] = req.transport
    if req.command is not None:
        base_cfg["command"] = req.command
    if req.args is not None:
        base_cfg["args"] = req.args
    if req.env is not None:
        base_cfg["env"] = req.env
    if req_url is not None:
        base_cfg["url"] = req_url
    base_cfg["headers"] = effective_headers
    if req.timeout_seconds is not None:
        base_cfg["timeout_seconds"] = req.timeout_seconds
    if req.upload_endpoint is not None:
        base_cfg["upload_endpoint"] = req.upload_endpoint
    if req.upload_file_field is not None:
        base_cfg["upload_file_field"] = req.upload_file_field
    if req.download_origins is not None:
        base_cfg["download_origins"] = req.download_origins

    return await mcp_manager.test_server_connection(target_name, base_cfg)


@router.post("/extensions/mcp/import")
async def import_mcp_servers_json(req: MCPImportRequest):
    """从粘贴或上传的 JSON 配置中批量导入 MCP 服务节点（兼容本地 stdio 与主流远程 HTTP/SSE JSON）"""
    try:
        parsed = json.loads(req.raw_json.strip())
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"JSON 格式解析失败: {str(e)}")

    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="MCP 配置必须为 JSON 对象")

    incoming = extract_mcp_servers_map(parsed)
    cfg = mcp_manager.load_config()
    servers = cfg.setdefault("mcpServers", {})
    imported_count = 0

    for k, v in incoming.items():
        if not isinstance(v, dict):
            continue
        has_cmd = bool(str(v.get("command") or "").strip())
        has_url = bool(str(v.get("url") or v.get("serverUrl") or "").strip())
        if not (has_cmd or has_url):
            continue

        srv_name = str(k).strip()
        if not is_valid_mcp_server_name(srv_name):
            raise HTTPException(
                status_code=400,
                detail=f"导入失败：MCP 服务名称 '{k}' 非法（不能为空且严禁包含双下划线 `__`）",
            )

        candidate = dict(v)
        if "enabled" not in candidate:
            candidate["enabled"] = True

        servers[srv_name] = _validate_and_normalize_server_payload(
            srv_name,
            candidate,
            existing_entry=servers.get(srv_name) if isinstance(servers, dict) else None,
            default_description=f"导入的 MCP 服务 ({srv_name})",
        )
        imported_count += 1

    if imported_count == 0:
        raise HTTPException(
            status_code=400,
            detail="未在 JSON 中识别到包含 'command' 或 'url' / 'serverUrl' 字段的有效 MCP 服务配置",
        )

    mcp_manager.save_config({"mcpServers": servers})
    skill_loader.invalidate_cache()
    return {
        "status": "ok",
        "imported_count": imported_count,
        "mcpServers": _mask_mcp_servers_map(servers),
        "config_path": str(mcp_manager.config_path),
    }


@router.delete("/extensions/mcp/{server_name}")
async def delete_mcp_server(server_name: str):
    """删除指定的 MCP 服务节点"""
    cfg = mcp_manager.load_config()
    servers = cfg.get("mcpServers", {})
    if server_name in servers:
        del servers[server_name]
        mcp_manager.save_config({"mcpServers": servers})
        skill_loader.invalidate_cache()
    return {
        "status": "ok",
        "mcpServers": _mask_mcp_servers_map(servers),
        "config_path": str(mcp_manager.config_path),
    }


# ──────────────────── 3. 多协议大模型供应商池 (LLM Providers) ────────────────────

class LLMProviderSaveRequest(BaseModel):
    """保存或更新大模型供应商请求"""
    id: str | None = None
    name: str = Field(..., description="供应商显示名称，如 DeepSeek 官方 / 本地 Ollama / 本地 llama-server")
    provider_type: str = Field(default="openai_compat", description="google_pool | gemini_api | openai_compat | anthropic | ollama | llama_server")
    base_url: str = Field(default="", description="API Base URL，如 https://api.deepseek.com/v1 或 http://127.0.0.1:8080/v1")
    api_key: str = Field(default="", description="API Key（留空则在更新时保持原加密 Key）")
    models: list[str] = Field(default_factory=list, description="该通道支持的模型列表")
    role_binding: str = Field(default="both", description="primary(主力推理) | light(后台记忆提炼) | both(通用)")
    tool_call_mode: str = Field(default="auto", description="auto | native | xml_prompt")
    priority: int = Field(default=5, description="调度优先级（越小越优先）")
    is_enabled: int = Field(default=1, description="1=启用, 0=禁用")
    max_context_tokens: int = Field(default=0, ge=0, description="单槽位上下文窗口上限 (0=按通道自动适配，本地建议8192)")


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
        max_context_tokens=req.max_context_tokens,
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
