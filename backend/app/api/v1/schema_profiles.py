"""
通用数据结构图谱（Schema Profiles / Entities / Relations）与专家绑定 API 路由
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...repository.schema_repository import SchemaProfileRepository
from ...schema.context_injector import audit_sql_rules_for_graph
from ...schema.data_cleaner import DataResponseCleaner
from ...schema.profiler_parser import parse_schema_content
from ...schema.query_assembler import QueryInstructionAssembler

router = APIRouter(tags=["SchemaGraph"])


class CreateProfileRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    profile_id: str | None = None
    description: str = ""
    domain_prefix: str = ""
    ontology_base_url: str = ""
    ontology_api_key: str = ""
    is_active: bool = True


class UpdateProfileRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    domain_prefix: str | None = None
    ontology_base_url: str | None = None
    ontology_api_key: str | None = None
    is_active: bool | None = None


class ParsePreviewRequest(BaseModel):
    content: str = Field(..., min_length=1)
    format: str = Field(default="auto", description="auto | sql | markdown")


class ImportParsedRequest(BaseModel):
    entities: list[dict[str, Any]] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)
    business_rules_markdown: str = ""
    replace_existing: bool = False


class UpsertEntityRequest(BaseModel):
    table_name: str = Field(..., min_length=1, max_length=128)
    display_name: str = ""
    category: str = "CORE_MAIN"
    description: str = ""
    primary_key: str = "id"
    pos_x: float | None = None
    pos_y: float | None = None
    fields: list[dict[str, Any]] | None = None


class UpdatePositionsRequest(BaseModel):
    positions: list[dict[str, Any]] = Field(default_factory=list)


class UpsertRelationRequest(BaseModel):
    id: str | None = None
    profile_id: str = Field(..., min_length=1)
    source_entity_id: str = Field(..., min_length=1)
    source_field: str = Field(..., min_length=1)
    target_entity_id: str = Field(..., min_length=1)
    target_field: str = Field(..., min_length=1)
    relation_type: str = "LOGICAL_JOIN"
    cardinality: str = "N:1"
    join_condition: str = ""
    filter_rule: str = ""
    is_warning: bool = False
    warning_message: str = ""


class AgentBindingItem(BaseModel):
    profile_id: str
    is_primary: bool = False
    mounted_entities: str | list[str] = "*"


class SaveAgentBindingsRequest(BaseModel):
    bindings: list[AgentBindingItem] = Field(default_factory=list)


@router.get("/profiles")
async def list_schema_profiles() -> dict[str, Any]:
    """获取所有数据结构定义空间列表。"""
    profiles = await SchemaProfileRepository.list_profiles(masked=True)
    return {"success": True, "data": profiles}


@router.post("/profiles")
async def create_schema_profile(req: CreateProfileRequest) -> dict[str, Any]:
    """创建新的数据结构定义空间。"""
    try:
        profile = await SchemaProfileRepository.create_profile(
            name=req.name,
            profile_id=req.profile_id,
            description=req.description,
            domain_prefix=req.domain_prefix,
            ontology_base_url=req.ontology_base_url,
            ontology_api_key=req.ontology_api_key,
            is_active=req.is_active,
        )
        return {"success": True, "data": profile}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/profiles/{profile_id}")
async def get_schema_profile(profile_id: str) -> dict[str, Any]:
    """获取单个 Schema Profile 详情。"""
    profile = await SchemaProfileRepository.get_profile(profile_id, masked=True)
    if not profile:
        raise HTTPException(status_code=404, detail="Schema Profile 不存在")
    return {"success": True, "data": profile}


@router.put("/profiles/{profile_id}")
async def update_schema_profile(profile_id: str, req: UpdateProfileRequest) -> dict[str, Any]:
    """更新 Schema Profile 元信息与多库连接隔离配置。"""
    profile = await SchemaProfileRepository.update_profile(
        profile_id,
        name=req.name,
        description=req.description,
        domain_prefix=req.domain_prefix,
        ontology_base_url=req.ontology_base_url,
        ontology_api_key=req.ontology_api_key,
        is_active=req.is_active,
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Schema Profile 不存在")
    return {"success": True, "data": profile}


@router.delete("/profiles/{profile_id}")
async def delete_schema_profile(profile_id: str) -> dict[str, Any]:
    """删除 Schema Profile（级联清理实体、关系与专家绑定）。"""
    ok = await SchemaProfileRepository.delete_profile(profile_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Schema Profile 不存在")
    return {"success": True, "data": {"id": profile_id}}


@router.get("/profiles/{profile_id}/graph")
async def get_schema_profile_graph(profile_id: str) -> dict[str, Any]:
    """获取指定 Profile 的完整实体节点、字段与关系边。"""
    graph = await SchemaProfileRepository.get_profile_graph(profile_id)
    if not graph:
        raise HTTPException(status_code=404, detail="Schema Profile 不存在")
    return {"success": True, "data": graph}


@router.post("/profiles/{profile_id}/parse-preview")
async def preview_schema_parse(profile_id: str, req: ParsePreviewRequest) -> dict[str, Any]:
    """解析 SQL DDL 或 Markdown 文档并返回预览结果（不直接写库）。"""
    profile = await SchemaProfileRepository.get_profile(profile_id, masked=True)
    if not profile:
        raise HTTPException(status_code=404, detail="Schema Profile 不存在")
    parsed = parse_schema_content(req.content, fmt=req.format)
    return {"success": True, "data": parsed}


@router.post("/profiles/{profile_id}/import")
async def import_parsed_schema(profile_id: str, req: ImportParsedRequest) -> dict[str, Any]:
    """将用户确认后的解析结果批量写入指定 Profile。"""
    try:
        res = await SchemaProfileRepository.batch_import_parsed(
            profile_id=profile_id,
            entities=req.entities,
            relations=req.relations,
            business_rules_markdown=req.business_rules_markdown,
            replace_existing=req.replace_existing,
        )
        return {"success": True, "data": res}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/profiles/{profile_id}/entities")
async def upsert_schema_entity(profile_id: str, req: UpsertEntityRequest) -> dict[str, Any]:
    """创建或更新单个实体节点及其字段。"""
    profile = await SchemaProfileRepository.get_profile(profile_id, masked=True)
    if not profile:
        raise HTTPException(status_code=404, detail="Schema Profile 不存在")
    try:
        entity = await SchemaProfileRepository.upsert_entity(
            profile_id=profile_id,
            table_name=req.table_name,
            display_name=req.display_name,
            category=req.category,
            description=req.description,
            primary_key=req.primary_key,
            pos_x=req.pos_x,
            pos_y=req.pos_y,
            fields=req.fields,
        )
        return {"success": True, "data": entity}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/profiles/{profile_id}/positions")
async def update_schema_entity_positions(profile_id: str, req: UpdatePositionsRequest) -> dict[str, Any]:
    """批量保存画布节点位置坐标。"""
    count = await SchemaProfileRepository.update_entity_positions(profile_id, req.positions)
    return {"success": True, "data": {"updated": count}}


@router.delete("/entities/{entity_id}")
async def delete_schema_entity(entity_id: str) -> dict[str, Any]:
    """删除实体节点及关联关系边。"""
    ok = await SchemaProfileRepository.delete_entity(entity_id)
    if not ok:
        raise HTTPException(status_code=404, detail="实体节点不存在")
    return {"success": True, "data": {"id": entity_id}}


@router.post("/relations")
async def upsert_schema_relation(req: UpsertRelationRequest) -> dict[str, Any]:
    """创建或更新关系边（支持 FK / LOGICAL_JOIN / JSON_EXTRACT / DO_NOT_JOIN 与红线规则）。"""
    try:
        rel = await SchemaProfileRepository.upsert_relation(
            relation_id=req.id,
            profile_id=req.profile_id,
            source_entity_id=req.source_entity_id,
            source_field=req.source_field,
            target_entity_id=req.target_entity_id,
            target_field=req.target_field,
            relation_type=req.relation_type,
            cardinality=req.cardinality,
            join_condition=req.join_condition,
            filter_rule=req.filter_rule,
            is_warning=req.is_warning,
            warning_message=req.warning_message,
        )
        return {"success": True, "data": rel}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/relations/{relation_id}")
async def delete_schema_relation(relation_id: str) -> dict[str, Any]:
    """删除指定关系边。"""
    ok = await SchemaProfileRepository.delete_relation(relation_id)
    if not ok:
        raise HTTPException(status_code=404, detail="关系边不存在")
    return {"success": True, "data": {"id": relation_id}}


@router.get("/agents/{agent_id}/binding")
async def get_agent_schema_binding(agent_id: str) -> dict[str, Any]:
    """获取数字专家绑定的数据结构空间配置。"""
    bindings = await SchemaProfileRepository.get_agent_bindings(agent_id)
    return {"success": True, "data": bindings}


@router.put("/agents/{agent_id}/binding")
async def update_agent_schema_binding(agent_id: str, req: SaveAgentBindingsRequest) -> dict[str, Any]:
    """更新数字专家绑定的数据结构空间与可访问子集表。"""
    bindings = await SchemaProfileRepository.save_agent_bindings(
        agent_id,
        [b.model_dump() for b in req.bindings],
    )
    return {"success": True, "data": bindings}


class CloneProfileRequest(BaseModel):
    new_profile_id: str | None = None
    new_profile_name: str = Field(..., min_length=1, max_length=128)
    domain_prefix: str = ""


class ImportTemplateRequest(BaseModel):
    new_profile_id: str | None = None
    new_profile_name: str = ""
    template: dict[str, Any]


class AssembleInstructionRequest(BaseModel):
    question: str = Field(..., min_length=1)
    mounted_entities: list[str] | None = None


class AuditAndCleanRequest(BaseModel):
    profile_id: str = Field(..., min_length=1)
    sql_code: str = ""
    raw_rows: list[dict[str, Any]] = Field(default_factory=list)
    question_hint: str = ""
    primary_group_field: str | None = None


@router.get("/profiles/{profile_id}/export")
async def export_schema_profile(profile_id: str) -> dict[str, Any]:
    """导出 Schema Profile 完整结构模板（同时包含可再次导入的 Markdown 文档与 JSON 结构）。"""
    from ...schema.profiler_parser import export_profile_to_markdown

    bundle = await SchemaProfileRepository.export_profile_bundle(profile_id)
    if not bundle:
        raise HTTPException(status_code=404, detail="Schema Profile 不存在")
    md_text = export_profile_to_markdown(bundle)
    prof_name = str((bundle.get("profile") or {}).get("name") or profile_id).strip()
    safe_name = "".join(c if c not in r'\/:*?"<>|' else "_" for c in prof_name)
    return {
        "success": True,
        "data": bundle,
        "markdown": md_text,
        "filename": f"{safe_name}.md",
    }


@router.post("/profiles/{profile_id}/export-md-save")
async def export_and_save_schema_markdown(profile_id: str) -> dict[str, Any]:
    """将 Schema Profile 导出为与导入器同格式的 .md 文档，并调起 Windows 原生另存为对话框供用户保存文件。"""
    import asyncio
    import subprocess
    import sys
    from pathlib import Path
    from ...schema.profiler_parser import export_profile_to_markdown

    bundle = await SchemaProfileRepository.export_profile_bundle(profile_id)
    if not bundle:
        raise HTTPException(status_code=404, detail="Schema Profile 不存在")

    md_text = export_profile_to_markdown(bundle)
    prof_name = str((bundle.get("profile") or {}).get("name") or profile_id).strip()
    safe_name = "".join(c if c not in r'\/:*?"<>|' else "_" for c in prof_name) or profile_id
    default_filename = f"{safe_name}.md"

    def _prompt_save_file_windows() -> tuple[str, str]:
        if sys.platform != "win32":
            return "unsupported", ""
        ps_script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$dlg = New-Object System.Windows.Forms.SaveFileDialog; "
            "$dlg.Title = '导出并保存数据结构与口径 Markdown 文档 (.md)'; "
            "$dlg.Filter = 'Markdown 文档 (*.md)|*.md|所有文件 (*.*)|*.*'; "
            f"$dlg.FileName = '{default_filename.replace(chr(39), '')}'; "
            "$form = New-Object System.Windows.Forms.Form; "
            "$form.TopMost = $true; "
            "$res = $dlg.ShowDialog($form); "
            "if ($res -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $dlg.FileName }"
        )
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Sta", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=120,
            )
            chosen = (proc.stdout or "").strip().splitlines()
            target_path = chosen[-1].strip() if chosen else ""
            if target_path:
                Path(target_path).write_text(md_text, encoding="utf-8")
                return "saved", target_path
            return "cancelled", ""
        except Exception:
            return "unsupported", ""

    status, saved_path = await asyncio.to_thread(_prompt_save_file_windows)
    return {
        "success": True,
        "status": status,
        "saved_path": saved_path,
        "filename": default_filename,
        "markdown": md_text,
    }


@router.post("/profiles/{profile_id}/clone")
async def clone_schema_profile(profile_id: str, req: CloneProfileRequest) -> dict[str, Any]:
    """深度克隆指定 Schema Profile 为新空间。"""
    try:
        res = await SchemaProfileRepository.clone_profile(
            profile_id,
            new_profile_name=req.new_profile_name,
            new_profile_id=req.new_profile_id,
            domain_prefix=req.domain_prefix,
        )
        return {"success": True, "status": "success", "data": res, **res}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/profiles/import-template")
async def import_schema_profile_template(req: ImportTemplateRequest) -> dict[str, Any]:
    """从导出的 JSON 模板快速新建数据结构空间。"""
    tpl = req.template or {}
    prof_meta = tpl.get("profile") or {}
    name = (req.new_profile_name or prof_meta.get("name") or "导入数据结构模板").strip()
    created = await SchemaProfileRepository.create_profile(
        name=name,
        profile_id=req.new_profile_id,
        description=str(prof_meta.get("description") or ""),
        domain_prefix=str(prof_meta.get("domain_prefix") or ""),
    )
    new_pid = created["id"]
    entities = tpl.get("entities") or []
    old_to_new_table = {e.get("id"): e.get("table_name") for e in entities if e.get("id")}
    mapped_relations = []
    for r in tpl.get("relations") or []:
        src_tbl = old_to_new_table.get(r.get("source_entity_id"), str(r.get("source_entity_id") or "").split(".")[-1])
        tgt_tbl = old_to_new_table.get(r.get("target_entity_id"), str(r.get("target_entity_id") or "").split(".")[-1])
        mapped_relations.append(
            {
                **r,
                "source_entity_id": f"{new_pid}.{src_tbl}",
                "target_entity_id": f"{new_pid}.{tgt_tbl}",
            }
        )
    imp = await SchemaProfileRepository.batch_import_parsed(
        profile_id=new_pid,
        entities=entities,
        relations=mapped_relations,
    )
    return {"success": True, "data": {"profile": created, **imp}}


@router.post("/profiles/{profile_id}/assemble-instruction")
async def assemble_profile_instruction(profile_id: str, req: AssembleInstructionRequest) -> dict[str, Any]:
    """O1: 结合 Schema Profile 生成高确定性的 Auto Ontology 提问指令。"""
    graph = await SchemaProfileRepository.get_profile_graph(profile_id, entity_filter=req.mounted_entities)
    if not graph:
        raise HTTPException(status_code=404, detail="Schema Profile 不存在")
    assembled = QueryInstructionAssembler.assemble_instruction(
        question=req.question,
        profile_context=graph,
        selected_tables=req.mounted_entities,
    )
    return {"success": True, "data": assembled, **assembled}


@router.post("/schema/audit-and-clean")
async def audit_and_clean_schema_query(req: AuditAndCleanRequest) -> dict[str, Any]:
    """O1 + O3: 对 SQL 进行深度静态红线审计（R001-R006），并对返回数据行做脏值清洗。"""
    graph = await SchemaProfileRepository.get_profile_graph(req.profile_id)
    if not graph:
        raise HTTPException(status_code=404, detail="Schema Profile 不存在")

    audit_res = audit_sql_rules_for_graph(
        sql_code=req.sql_code,
        graph=graph,
        question_hint=req.question_hint,
    )
    cleaned_res = DataResponseCleaner.clean_query_result(
        rows=req.raw_rows,
        primary_group_field=req.primary_group_field,
    )
    return {
        "success": True,
        "audit_passed": audit_res["audit_passed"],
        "has_hard_block": audit_res["has_hard_block"],
        "warnings": audit_res["warnings"],
        "cleaned_data": cleaned_res,
    }

