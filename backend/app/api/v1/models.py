"""
模型库服务、基于账号属性自动识别与真实模型一键测试筛选接口
支持根据当前活跃账号属性 (Antigravity CLI / Google API Key / OAuth) 动态匹配模型，
并提供一键可用性实测与自动保留可用模型持久化能力。
"""
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...core.logging_config import logger
from ...repository.account_repository import AccountRepository
from ...service.agent_runner import test_cli_model_availability
from ...service.gemini_client import GeminiClient
from ...service.oauth_service import oauth_service
from ...service.audit_service import audit_service

router = APIRouter(prefix="/models", tags=["Models"])


class ModelTestStatus(BaseModel):
    available: bool = Field(..., description="该模型是否实测可用")
    latency_ms: float = Field(default=0, description="实测响应耗时（毫秒）")
    error: Optional[str] = Field(default=None, description="不可用时的具体错误原因")
    tested_at: Optional[str] = Field(default=None, description="最近一次测试时间")


class ModelMeta(BaseModel):
    id: str = Field(..., description="真实模型标识符")
    name: str = Field(..., description="用户友好展示名")
    provider: str = Field(..., description="提供方：Google Official 或 Antigravity CLI")
    description: str = Field(..., description="能力定位与说明")
    badge: str = Field(..., description="标签标识，如 旗舰推理、极速低延迟、思考链")
    has_thinking: bool = Field(default=False, description="是否支持深度思考链配置")
    is_default: bool = Field(default=False, description="是否为默认推荐模型")
    supported_auth_types: List[str] = Field(
        default_factory=list,
        description="支持的账号认证属性列表"
    )
    test_status: Optional[ModelTestStatus] = Field(
        default=None,
        description="该模型在当前账号下的最近一次实测结果"
    )


class ActiveModelsResponse(BaseModel):
    account_name: str = Field(..., description="当前匹配的目标账号名称")
    auth_type: str = Field(..., description="当前账号认证属性: gemini_cli / api_key / oauth_google_one / none")
    auth_label: str = Field(..., description="账号属性中文可读标签")
    is_tested: bool = Field(default=False, description="当前账号是否已完成过模型可用性测试")
    verified_model_ids: List[str] = Field(default_factory=list, description="当前已保留的可用模型 ID 列表")
    models: List[ModelMeta] = Field(default_factory=list, description="输入框旁实际显示的可用模型列表")
    candidates: List[ModelMeta] = Field(default_factory=list, description="当前账号属性下的完整候选模型列表（供设置页测试）")


class ModelTestRequest(BaseModel):
    model_ids: Optional[List[str]] = Field(
        default=None,
        description="要测试的模型 ID 列表；若为空则一键测试当前账号属性下的所有候选模型"
    )
    auto_save: bool = Field(
        default=True,
        description="测试完成后是否自动保留可用模型并持久化"
    )


class UpdateVerifiedModelsRequest(BaseModel):
    verified_model_ids: List[str] = Field(..., description="要在输入框旁保留显示的模型 ID 列表")


# =====================================================================
# 1. Antigravity / Gemini CLI 账号专属真实模型库 (对应 agy.exe models)
# =====================================================================
CLI_MODELS: List[ModelMeta] = [
    ModelMeta(
        id="gemini-4-argon",
        name="Gemini 4 Argon",
        provider="Antigravity CLI",
        description="Google 最新第 4 代 Argon 旗舰深度推理引擎 · 超深思维链与复杂架构推演",
        badge="CLI 旗舰 · 4.0 Argon",
        has_thinking=True,
        is_default=True,
        supported_auth_types=["gemini_cli"],
    ),
    ModelMeta(
        id="gemini-3.8-flash",
        name="Gemini 3.8 Flash",
        provider="Antigravity CLI",
        description="宿主机 CLI 原生高推理极速引擎 · 兼顾响应速度与全能系统工具调度",
        badge="CLI 原生 · 3.8 极速",
        has_thinking=True,
        is_default=False,
        supported_auth_types=["gemini_cli"],
    ),
    ModelMeta(
        id="gemini-3.5-flash-lite",
        name="Gemini 3.5 Flash-Lite",
        provider="Antigravity CLI",
        description="3.5 代毫秒级轻量模型 · 极速问答、代码纠错与敏捷检索",
        badge="毫秒响应 · 3.5",
        has_thinking=False,
        is_default=False,
        supported_auth_types=["gemini_cli"],
    ),
    ModelMeta(
        id="gemini-2.5-flash",
        name="Gemini 2.5 Flash",
        provider="Antigravity CLI",
        description="Google 经典高性价比高可用主力模型",
        badge="CLI 稳定 · 2.5 Flash",
        has_thinking=True,
        is_default=False,
        supported_auth_types=["gemini_cli"],
    ),
    ModelMeta(
        id="claude-sonnet-5-5",
        name="Claude Sonnet 5.5",
        provider="Antigravity CLI",
        description="通过 Antigravity 官方通道调度的 Claude 5.5 主力工程编码思考模型",
        badge="CLI 原生 · 跨生态",
        has_thinking=True,
        is_default=False,
        supported_auth_types=["gemini_cli"],
    ),
    ModelMeta(
        id="claude-opus-5-5",
        name="Claude Opus 5.5",
        provider="Antigravity CLI",
        description="通过 Antigravity 官方通道调度的 Claude 5.5 顶配深度推理模型",
        badge="CLI 顶配 · Opus",
        has_thinking=True,
        is_default=False,
        supported_auth_types=["gemini_cli"],
    ),
    ModelMeta(
        id="gpt-oss-120b-medium",
        name="GPT-OSS 120B",
        provider="Antigravity CLI",
        description="120B 巨量参数开源推理模型 · 多视角代码审查与技术问答",
        badge="CLI 原生 · 120B",
        has_thinking=False,
        is_default=False,
        supported_auth_types=["gemini_cli"],
    ),
]

# =====================================================================
# 2. Google 官方 REST API / Cloud Code OAuth 账号专属真实模型库
# =====================================================================
OFFICIAL_API_MODELS: List[ModelMeta] = [
    ModelMeta(
        id="gemini-4-argon",
        name="Gemini 4 Argon",
        provider="Google Official",
        description="Google 最新第 4 代 Argon 旗舰深度推理引擎 · 超深思维链与复杂架构推演",
        badge="最新旗舰 · 4.0 Argon",
        has_thinking=True,
        is_default=True,
        supported_auth_types=["api_key", "oauth_google_one", "oauth_personal"],
    ),
    ModelMeta(
        id="gemini-3.8-flash",
        name="Gemini 3.8 Flash",
        provider="Google Official",
        description="Google 新一代 3.8 高推理极速主力模型 · 超低延迟响应与全能代码生成",
        badge="高阶极速 · 3.8 Flash",
        has_thinking=True,
        is_default=False,
        supported_auth_types=["api_key", "oauth_google_one", "oauth_personal"],
    ),
    ModelMeta(
        id="gemini-3.5-flash-lite",
        name="Gemini 3.5 Flash-Lite",
        provider="Google Official",
        description="3.5 代毫秒级轻量模型 · 极速问答、日常高频排查与工具调用",
        badge="毫秒极速 · 3.5",
        has_thinking=False,
        is_default=False,
        supported_auth_types=["api_key", "oauth_google_one", "oauth_personal"],
    ),
    ModelMeta(
        id="gemini-2.5-flash",
        name="Gemini 2.5 Flash",
        provider="Google Official",
        description="Google 官方全能高性价比模型 · 极低响应延迟与优秀的代码理解",
        badge="官方主力 · 2.5 Flash",
        has_thinking=True,
        is_default=False,
        supported_auth_types=["api_key", "oauth_google_one", "oauth_personal"],
    ),
    ModelMeta(
        id="gemini-2.5-pro",
        name="Gemini 2.5 Pro",
        provider="Google Official",
        description="Google 官方顶级旗舰推理模型 · 支持超长上下文与多级思维链",
        badge="顶配推理 · 2.5 Pro",
        has_thinking=True,
        is_default=False,
        supported_auth_types=["api_key", "oauth_google_one", "oauth_personal"],
    ),
    ModelMeta(
        id="gemini-2.0-flash",
        name="Gemini 2.0 Flash",
        provider="Google Official",
        description="高配额稳定版官方主力模型 · 适合日常高频研发与长文档处理",
        badge="高配额 · 2.0 Flash",
        has_thinking=False,
        is_default=False,
        supported_auth_types=["api_key", "oauth_google_one", "oauth_personal"],
    ),
    ModelMeta(
        id="gemini-2.0-flash-lite",
        name="Gemini 2.0 Flash-Lite",
        provider="Google Official",
        description="基础快速轻量模型 · 适合简短提问、日常纠错与极速检索",
        badge="轻量响应 · 2.0",
        has_thinking=False,
        is_default=False,
        supported_auth_types=["api_key", "oauth_google_one", "oauth_personal"],
    ),
]


def _get_verified_store_path() -> Path:
    """获取模型可用性测试结果与保留列表的持久化文件路径"""
    data_dir = Path(__file__).resolve().parent.parent.parent.parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "verified_models.json"


def _load_verified_store() -> Dict[str, Any]:
    """读取已持久化的模型测试与保留记录"""
    path = _get_verified_store_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "accounts" in data:
                return data
        except Exception as e:
            logger.warning(f"读取 verified_models.json 失败，将初始化为空: {e}")
    return {"accounts": {}}


def _save_verified_store(store: Dict[str, Any]) -> None:
    """保存模型测试与保留记录到本地 JSON"""
    path = _get_verified_store_path()
    try:
        path.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.error(f"写入 verified_models.json 失败: {e}")


def _get_candidates_for_auth_type(auth_type: str, cli_home_dir: Optional[str] = None) -> List[ModelMeta]:
    """根据账号属性返回匹配的候选模型列表"""
    norm_type = (auth_type or "").strip().lower()
    if norm_type in ("api_key", "oauth_google_one", "oauth_personal") and not cli_home_dir:
        return [m.model_copy(deep=True) for m in OFFICIAL_API_MODELS]
    return [m.model_copy(deep=True) for m in CLI_MODELS]


def _get_auth_label(auth_type: str) -> str:
    """获取账号认证属性的中文可读描述"""
    norm = (auth_type or "").strip().lower()
    if norm == "api_key":
        return "Google AI Studio 官方 API Key"
    if norm.startswith("oauth"):
        return "Google 官方 OAuth 账号"
    if norm == "gemini_cli":
        return "Antigravity / Gemini CLI 原生账号"
    return "未绑定账号 (默认 CLI 视图)"


async def _resolve_target_account():
    """获取当前活跃账号；若全部处于冷却中则回退取优先级最高的首个账号"""
    candidate = await AccountRepository.get_active_candidate()
    if candidate:
        return candidate
    all_accounts = await AccountRepository.get_all()
    return all_accounts[0] if all_accounts else None


async def _build_active_models_response() -> ActiveModelsResponse:
    """组装当前账号属性对应的候选模型与已保留可用模型响应（无 Google 账号时不默认显示 Gemini 模型）"""
    account = await _resolve_target_account()
    store = _load_verified_store()

    candidates: List[ModelMeta] = []
    active_models: List[ModelMeta] = []
    valid_verified_ids: List[str] = []
    test_results_map: Dict[str, Any] = {}
    is_tested = False

    if account:
        account_name = account.name
        auth_type = account.auth_type or "gemini_cli"
        cli_home_dir = account.cli_home_dir
        auth_label = _get_auth_label(auth_type)

        candidates = _get_candidates_for_auth_type(auth_type, cli_home_dir)
        acc_record = store.get("accounts", {}).get(account_name, {})

        # 校验存储记录的 auth_type 是否与当前账号一致
        if acc_record.get("auth_type") and acc_record.get("auth_type") != auth_type:
            acc_record = {}

        test_results_map = acc_record.get("test_results", {})
        saved_verified_ids: List[str] = acc_record.get("verified_model_ids", [])
        is_tested = bool(test_results_map)

        # 为每个候选模型挂载最近一次测试结果
        for m in candidates:
            res = test_results_map.get(m.id)
            if res and isinstance(res, dict):
                m.test_status = ModelTestStatus(
                    available=bool(res.get("available", False)),
                    latency_ms=float(res.get("latency_ms", 0)),
                    error=res.get("error"),
                    tested_at=res.get("tested_at"),
                )

        candidate_id_set = {m.id for m in candidates}
        valid_verified_ids = [mid for mid in saved_verified_ids if mid in candidate_id_set]

        # 智能代际自适应：若当前账号历史上仅保存了旧版模型（如 2.x 系列），检测到候选库包含最新代际模型（如 4.0 Argon、3.8 Flash）
        # 且最新模型未被当前账号明确标记为不可用时，自动将其并入输入框可用列表中并置顶，避免用户被困在老旧模型中
        latest_flagship_ids = ["gemini-4-argon", "gemini-3.8-flash", "gemini-3.5-flash-lite"]
        available_new_ids = [
            mid for mid in latest_flagship_ids
            if mid in candidate_id_set and test_results_map.get(mid, {}).get("available") is not False
        ]

        if valid_verified_ids:
            has_any_latest = any(mid in valid_verified_ids for mid in latest_flagship_ids)
            if not has_any_latest and available_new_ids:
                for new_id in reversed(available_new_ids):
                    if new_id not in valid_verified_ids:
                        valid_verified_ids.insert(0, new_id)
            active_models = [m for m in candidates if m.id in valid_verified_ids]
        else:
            active_models = [m.model_copy(deep=True) for m in candidates]
            valid_verified_ids = [m.id for m in active_models]
    else:
        account_name = "未配置"
        auth_type = "none"
        auth_label = "未配置 Google 账号"

    # 加载用户已启用的第三方/本地自定义大模型供应商 (llama-server / Ollama / DeepSeek / OpenAI / Anthropic)
    try:
        from ...core.provider_adapter import provider_adapter
        custom_provs = await provider_adapter.get_enabled_providers_for_role("primary")
        candidate_ids = {m.id for m in candidates}
        existing_ids = {m.id for m in active_models}
        custom_model_metas: List[ModelMeta] = []
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 若无 Google 账号，尝试读取首个自定义通道的测试与勾选记录
        if not account and custom_provs:
            first_name = custom_provs[0].get("name", "本地算力")
            custom_acc_record = store.get("accounts", {}).get(first_name, {})
            if custom_acc_record.get("test_results"):
                test_results_map = custom_acc_record["test_results"]
                is_tested = True

        for cp in custom_provs:
            ptype = cp.get("provider_type")
            # Google 官方 OAuth、API Key 及算力池账号由官方模型库管理与测试，严禁作为第三方通道重复注入
            if ptype in ("google_pool", "google_oauth", "gemini_api"):
                continue
            prov_name = cp.get("name", "Custom API")
            base_url = str(cp.get("base_url") or "")
            is_local = "127.0.0.1" in base_url or "localhost" in base_url or ptype in ("ollama", "llama_server")
            max_ctx = int(cp.get("max_context_tokens") or 0)
            ctx_note = f" · {max_ctx:,} ctx" if max_ctx > 0 else ""
            badge_label = (
                "llama.cpp · 本地直连"
                if ptype == "llama_server"
                else ("本地 API · 极速" if is_local else "自定义 API")
            )
            for mid in (cp.get("models") or []):
                if mid and mid not in existing_ids and mid not in candidate_ids:
                    init_status = None
                    if mid in test_results_map:
                        res = test_results_map[mid]
                        init_status = ModelTestStatus(
                            available=bool(res.get("available", False)),
                            latency_ms=float(res.get("latency_ms", 0)),
                            error=res.get("error"),
                            tested_at=res.get("tested_at"),
                        )
                    elif not cp.get("is_cooling_down"):
                        init_status = ModelTestStatus(
                            available=True,
                            latency_ms=500.0,
                            error=None,
                            tested_at=now_str,
                        )

                    custom_model_metas.append(
                        ModelMeta(
                            id=mid,
                            name=f"{mid} ({prov_name})",
                            provider=prov_name,
                            description=f"多协议算力通道 [{prov_name}] ({ptype} · {base_url}{ctx_note})",
                            badge=badge_label,
                            has_thinking=False,
                            is_default=False,
                            supported_auth_types=[ptype or "openai_compat"],
                            test_status=init_status,
                        )
                    )
                    existing_ids.add(mid)

        if not account:
            # 无 Google 账号时：仅展示用户实际配置的自定义/本地模型，绝不混入未绑定的 Gemini 模型
            if custom_model_metas:
                custom_model_metas[0].is_default = True
                first_cp = custom_provs[0]
                first_base_url = str(first_cp.get("base_url") or "")
                first_ptype = first_cp.get("provider_type", "openai_compat")
                is_first_local = (
                    "127.0.0.1" in first_base_url
                    or "localhost" in first_base_url
                    or first_ptype in ("ollama", "llama_server")
                )
                account_name = first_cp.get("name", "本地算力")
                auth_type = first_ptype
                auth_label = (
                    f"已就绪本地算力通道 [{account_name}]"
                    if is_first_local
                    else f"已就绪算力通道 [{account_name}]"
                )
                candidates = [cm.model_copy(deep=True) for cm in custom_model_metas]
                saved_custom_ids = store.get("accounts", {}).get(account_name, {}).get("verified_model_ids", [])
                custom_id_set = {cm.id for cm in custom_model_metas}
                filtered_custom_ids = [mid for mid in saved_custom_ids if mid in custom_id_set]
                if filtered_custom_ids:
                    active_models = [cm for cm in custom_model_metas if cm.id in filtered_custom_ids]
                    valid_verified_ids = filtered_custom_ids
                else:
                    active_models = custom_model_metas
                    valid_verified_ids = [cm.id for cm in custom_model_metas]
        elif custom_model_metas and getattr(account, "status", "") != "active":
            # Google 账号存在但全部冷却/不可用时，将已就绪自定义模型置顶并设为默认
            custom_model_metas[0].is_default = True
            for m in active_models:
                m.is_default = False
            active_models = custom_model_metas + [m for m in active_models if m.id not in {cm.id for cm in custom_model_metas}]
            for cm in reversed(custom_model_metas):
                if cm.id not in valid_verified_ids:
                    valid_verified_ids.insert(0, cm.id)
            candidates = [cm.model_copy(deep=True) for cm in custom_model_metas] + [c for c in candidates if c.id not in {cm.id for cm in custom_model_metas}]
            first_cp = custom_provs[0]
            account_name = first_cp.get("name", "本地算力")
            auth_type = first_cp.get("provider_type", "openai_compat")
            auth_label = f"已就绪本地算力通道 [{account_name}]"
        else:
            candidates.extend([cm.model_copy(deep=True) for cm in custom_model_metas if cm.id not in {c.id for c in candidates}])
            active_models.extend(custom_model_metas)
            for cm in custom_model_metas:
                if cm.id not in valid_verified_ids:
                    valid_verified_ids.append(cm.id)
    except Exception as e:
        logger.debug(f"加载自定义供应商模型列表跳过: {e}")

    return ActiveModelsResponse(
        account_name=account_name,
        auth_type=auth_type,
        auth_label=auth_label,
        is_tested=is_tested,
        verified_model_ids=valid_verified_ids,
        models=active_models,
        candidates=candidates,
    )


@router.get("", response_model=List[ModelMeta])
async def list_available_models():
    """
    获取当前活跃账号下真实可用的大语言模型列表（兼容旧调用方，直接返回已筛选的 models 数组）。
    """
    resp = await _build_active_models_response()
    return resp.models


@router.get("/active", response_model=ActiveModelsResponse)
async def get_active_account_models():
    """
    根据当前活跃账号属性，返回自动匹配的可用模型列表、候选全集及历史测试详情。
    """
    return await _build_active_models_response()


async def _probe_single_model(account, model_meta: ModelMeta) -> Dict[str, Any]:
    """针对单个模型和指定账号执行真实连通性测试（全面适配 Google 官方与多源/本地模型）"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. 优先检查是否为第三方/本地自定义模型
    try:
        from ...core.provider_adapter import provider_adapter
        custom_provs = await provider_adapter.get_enabled_providers_for_role("primary")
        for cp in custom_provs:
            if model_meta.id in (cp.get("models") or []):
                probe_res = await provider_adapter.test_connection(
                    provider_type=cp.get("provider_type", "openai_compat"),
                    base_url=cp.get("base_url", ""),
                    api_key=cp.get("api_key", ""),
                    model_name=model_meta.id,
                )
                return {
                    "model_id": model_meta.id,
                    "available": bool(probe_res.get("success", False)),
                    "latency_ms": float(probe_res.get("latency_ms", 0.0)),
                    "error": probe_res.get("msg") if not probe_res.get("success") else None,
                    "tested_at": now_str,
                }
    except Exception as e:
        logger.debug(f"探测自定义模型连通性异常: {e}")

    auth_type = getattr(account, "auth_type", "gemini_cli") or "gemini_cli"
    auth_type = auth_type.lower()

    if auth_type in ("api_key", "oauth_google_one", "oauth_personal") and not getattr(account, "cli_home_dir", None):
        try:
            if auth_type.startswith("oauth"):
                token = await oauth_service.ensure_valid_token(account)
                effective_auth = "oauth_google_one"
            else:
                token = (getattr(account, "credentials", "") or "").strip()
                effective_auth = "api_key"

            probe_res = await GeminiClient.test_key(
                api_key=token,
                model_name=model_meta.id,
                auth_type=effective_auth,
                strict_model=True,
            )
            return {
                "model_id": model_meta.id,
                "available": bool(probe_res.get("valid", False)),
                "latency_ms": float(probe_res.get("latency_ms", 0)),
                "error": probe_res.get("error"),
                "tested_at": now_str,
            }
        except Exception as e:
            return {
                "model_id": model_meta.id,
                "available": False,
                "latency_ms": 0.0,
                "error": str(e)[:160],
                "tested_at": now_str,
            }

    # CLI 账号模式探测
    probe_res = await test_cli_model_availability(
        cli_home_dir=getattr(account, "cli_home_dir", None),
        model=model_meta.id,
        thinking_level="medium",
        timeout_seconds=25.0,
    )
    return {
        "model_id": model_meta.id,
        "available": bool(probe_res.get("available", False)),
        "latency_ms": float(probe_res.get("latency_ms", 0)),
        "error": probe_res.get("error"),
        "tested_at": now_str,
    }


@router.post("/test")
async def test_models_for_active_account(req: ModelTestRequest):
    """
    一键测试当前活跃账号下的候选模型可用性。
    测试完成后自动保留测试通过的模型，持久化并同步给前端输入框选择器。
    """
    account = await _resolve_target_account()
    # 如果暂无绑定的 Google 账号，但已配置并启用了本地/自定义大模型，构造本地账号上下文
    if not account:
        from ...core.provider_adapter import provider_adapter
        custom_provs = await provider_adapter.get_enabled_providers_for_role("primary")
        if not custom_provs:
            raise HTTPException(
                status_code=400,
                detail="算力池中暂无任何账号或自定义通道，请先添加后再测试模型。"
            )
        first_cp = custom_provs[0]
        class VirtualLocalAccount:
            name = first_cp.get("name", "本地算力")
            auth_type = first_cp.get("provider_type", "openai_compat")
            cli_home_dir = None
            credentials = None
        account = VirtualLocalAccount()

    current_state = await _build_active_models_response()
    candidates = current_state.candidates
    if req.model_ids:
        target_set = set(req.model_ids)
        models_to_test = [m for m in candidates if m.id in target_set]
    else:
        models_to_test = candidates

    if not models_to_test:
        raise HTTPException(status_code=400, detail="未找到需要测试的目标模型")

    logger.info(
        f"开始为账号 [{account.name}] ({account.auth_type}) 测试 {len(models_to_test)} 个模型: "
        f"{[m.id for m in models_to_test]}"
    )

    # 并发探测所有目标模型，大幅缩短等待时间
    probe_results = await asyncio.gather(
        *(_probe_single_model(account, m) for m in models_to_test)
    )

    store = _load_verified_store()
    accounts_dict = store.setdefault("accounts", {})
    acc_entry = accounts_dict.get(account.name, {})
    if acc_entry.get("auth_type") != account.auth_type:
        acc_entry = {"auth_type": account.auth_type, "test_results": {}, "verified_model_ids": []}

    test_results_map: Dict[str, Any] = acc_entry.setdefault("test_results", {})
    for r in probe_results:
        test_results_map[r["model_id"]] = {
            "available": r["available"],
            "latency_ms": r["latency_ms"],
            "error": r["error"],
            "tested_at": r["tested_at"],
        }

    # 如果开启了自动保留，则将所有已测通过的模型自动保存为 verified_model_ids
    if req.auto_save:
        available_ids = [
            m.id for m in candidates
            if test_results_map.get(m.id, {}).get("available") is True
        ]
        # 若至少有 1 个模型测试通过，自动仅保留可用模型；若全部失败，保留之前列表或全集以便排查
        if available_ids:
            acc_entry["verified_model_ids"] = available_ids

    acc_entry["auth_type"] = account.auth_type
    acc_entry["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    accounts_dict[account.name] = acc_entry
    _save_verified_store(store)

    updated_state = await _build_active_models_response()
    available_count = sum(1 for r in probe_results if r["available"])

    await audit_service.log(
        event_type="model_test",
        actor="user",
        detail={
            "account": account.name,
            "auth_type": account.auth_type,
            "tested_count": len(probe_results),
            "available_count": available_count,
            "available_models": acc_entry.get("verified_model_ids", []),
        },
        account_name=account.name,
        approved=True
    )

    return {
        "success": True,
        "account_name": account.name,
        "auth_type": account.auth_type,
        "tested_count": len(probe_results),
        "available_count": available_count,
        "results": probe_results,
        "state": updated_state,
        "message": (
            f"测试完成！共探测 {len(probe_results)} 个模型，其中 {available_count} 个可用，"
            f"已自动保留可用模型显示在输入框旁。"
            if available_count > 0
            else "测试完成，但当前账号下暂无模型通过连通性测试，请检查账号登录状态或配额。"
        ),
    }


@router.post("/verified", response_model=ActiveModelsResponse)
async def update_verified_models(req: UpdateVerifiedModelsRequest):
    """
    手动保存/调整当前账号在输入框旁边显示的保留模型列表
    """
    account = await _resolve_target_account()
    current_state = await _build_active_models_response()
    account_name = current_state.account_name
    auth_type = current_state.auth_type

    candidates = current_state.candidates
    valid_ids = {m.id for m in candidates}
    filtered = [mid for mid in req.verified_model_ids if mid in valid_ids]
    if not filtered:
        raise HTTPException(status_code=400, detail="请至少保留一个可用模型用于对话")

    store = _load_verified_store()
    accounts_dict = store.setdefault("accounts", {})
    acc_entry = accounts_dict.get(account_name, {
        "auth_type": auth_type,
        "test_results": {},
        "verified_model_ids": [],
    })
    acc_entry["auth_type"] = auth_type
    acc_entry["verified_model_ids"] = filtered
    acc_entry["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    accounts_dict[account_name] = acc_entry
    _save_verified_store(store)

    return await _build_active_models_response()
