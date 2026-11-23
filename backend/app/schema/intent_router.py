"""
KB-First 三层智能意图路由器 & 流式追问尾块防闪烁解析器。
核心职责：
1. 路由优先级严格遵守：ontology_live > stats_wiki_first > kb_only（杜绝混合问题被 kb_only 误杀）；
2. 动态读取专家绑定 Schema Profile 中的业务实体表注释，零行业硬编码；
3. 为 stats_wiki_first 提供年份/月份/关键维度一致性防串期校验；
4. 提供流式尾部缓冲器 FollowupStreamBuffer，彻底杜绝 <!-- FOLLOWUP_SUGGESTIONS: [...] --> 闪烁。
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from typing import Any

# 显式强制实时查数指令
_FORCE_LIVE_RE = re.compile(
    r"(重新查|重查|查最新|最新数据|实时数据|实时查|查库|查后台|拉数|刷新数据|重新实时查询|再查一次|重新统计)"
)

# 强个体身份/订单/实时状态查询特征（必走 ontology_live）
_IDENTITY_OR_STATUS_RE = re.compile(
    r"(1[3-9]\d{9}|\b\d{17}[\dXx]\b|订单号|发票号|注册号|证件号|"
    r"是否认证|认证状态|审核状态|缴费状态|有没有交费|是否缴费|交没交费|"
    r"报名记录|参赛记录|缴费记录|执教记录|成绩查询|积分查询|名次|到期时间|有效期内|"
    r"查一下.{1,10}(状态|记录|认证|缴费|报名|信息))"
)

# 聚合/统计/业务数据查询特征（进入 stats_wiki_first，若无匹配快照或年份不符则降级 ontology_live）
_STATS_AGG_RE = re.compile(
    r"(多少|几人|几家|几个|几名|几条|几场|人数|数量|总数|总计|合计|统计|汇总|"
    r"排名|排行|前\d+|top\s*\d+|占比|比例|分布|同比|环比|趋势|增长率|"
    r"按省|按市|各省|各市|按月|按年|按组别|各级别|按级别|各等级|按等级|分组|"
    r"明细|名单|清单|汇总表|导出名单|导出表格|补查|查数|报名情况|注册情况|参赛情况|缴费情况)"
)

# 强实时时间 + 业务数据动作特征（如“2026年注册教练员”“今天总决赛报名”“最新缴费”）
_TIME_BIZ_RE = re.compile(
    r"(?:20\d{2}\s*年?|今年|去年|今天|今日|昨天|本周|本月|目前|当前|截至|最新)"
    r"[\s\S]{0,16}"
    r"(?:注册|报名|参赛|认证|审核|缴费|收费|订单|发票|运动员|教练|裁判|俱乐部|代表团|团体|会员|积分|成绩)"
)

# 纯规程/制度/流程/常识特征（仅当未命中实时/统计特征时生效）
_RULE_GUIDE_RE = re.compile(
    r"(规程|规则|办法|制度|流程|步骤|标准|要求|条件|怎么报名|如何报名|怎么退费|退费|"
    r"器材|服装|装备|年龄组别|年龄划分|罚则|申诉|资格|怎么办|注意事项|定义|区别|含义|是什么)"
)

_YEAR_RE = re.compile(r"(20\d{2})")
_MONTH_RE = re.compile(r"(\d{1,2})\s*月")

FOLLOWUP_START_MARKER = "<!-- FOLLOWUP_SUGGESTIONS:"
_FOLLOWUP_BLOCK_RE = re.compile(
    r"<!--\s*FOLLOWUP_SUGGESTIONS\s*:\s*([\s\S]*?)\s*-->",
    re.IGNORECASE,
)


@dataclass
class IntentRouteResult:
    """意图分流结果。"""

    route: str  # 'ontology_live' | 'stats_wiki_first' | 'kb_only'
    reason: str
    matched_entities: list[str] = field(default_factory=list)
    allow_ontology_tools: bool = False
    inject_schema_context: bool = False
    snapshot_hit: dict[str, Any] | None = None


def extract_explicit_time_tokens(text: str) -> tuple[set[str], set[str]]:
    """提取文本中显式出现的年份与月份集合，用于快照防串期校验。"""
    years = set(_YEAR_RE.findall(text or ""))
    months = {m.lstrip("0") or "0" for m in _MONTH_RE.findall(text or "")}
    return years, months


def check_snapshot_consistency(user_query: str, snapshot: dict[str, Any]) -> bool:
    """校验用户问题与百科统计快照的显式年份/月份是否一致，防止 2025 串期到 2026。"""
    q_years, q_months = extract_explicit_time_tokens(user_query)
    snap_text = f"{snapshot.get('title', '')} {snapshot.get('query_scope', '')} {snapshot.get('summary_text', '')}"
    s_years, s_months = extract_explicit_time_tokens(snap_text)

    if q_years and s_years and not (q_years <= s_years):
        return False
    if q_years and not s_years:
        return False
    if q_months and s_months and not (q_months <= s_months):
        return False
    return True


async def _load_dynamic_entity_keywords(agent_id: str | None) -> list[str]:
    """从专家绑定的 Schema Profile 动态提取业务实体中文名（零硬编码）。"""
    if not agent_id:
        return []
    try:
        from ..repository.schema_repository import SchemaProfileRepository

        bindings = await SchemaProfileRepository.get_agent_bindings(agent_id)
        if not bindings or not bindings[0].get("profile_id"):
            return []
        graph = await SchemaProfileRepository.get_profile_graph(str(bindings[0]["profile_id"]))
        if not graph:
            return []
        keywords: set[str] = set()
        for ent in graph.get("entities") or []:
            cn_name = str(ent.get("display_name") or ent.get("table_comment") or "").strip()
            if cn_name:
                # 拆分括号与常见分隔符，抽取 2~8 字核心实体词
                for seg in re.split(r"[\s()（）/、,，·\-]+", cn_name):
                    seg = seg.strip()
                    if 2 <= len(seg) <= 8:
                        keywords.add(seg)
        return sorted(keywords, key=len, reverse=True)
    except Exception:
        return []


async def route_query_intent(
    user_query: str,
    agent_id: str | None = None,
    *,
    force_live: bool = False,
    caller: Any | None = None,
) -> IntentRouteResult:
    """执行 KB-First 三层智能分流（优先级：ontology_live > stats_wiki_first > kb_only，前置叠加身份权限硬门控）。"""
    q = (user_query or "").strip()
    if not q:
        return IntentRouteResult(
            route="kb_only",
            reason="空提问默认走本地知识库快通道",
            allow_ontology_tools=False,
            inject_schema_context=False,
        )

    # 铁闸 1：若调用方角色无实时查库权限（如 guest / 未认证外部渠道），强制锁死 kb_only
    if caller is not None:
        from .access_control import get_effective_role_policy

        role_policy = get_effective_role_policy(getattr(caller, "role", "guest"))
        if not role_policy.get("allow_biz_sql"):
            return IntentRouteResult(
                route="kb_only",
                reason=f"当前身份 [{getattr(caller, 'role', 'guest')}] 无实时业务库查询权限，强制锁定本地知识库",
                allow_ontology_tools=False,
                inject_schema_context=False,
            )

    dynamic_entities = await _load_dynamic_entity_keywords(agent_id)
    matched_entities = [kw for kw in dynamic_entities if kw in q]

    # 1. 最高优先级：强制实时查询 或 个体身份/订单/实时状态查询
    if force_live or _FORCE_LIVE_RE.search(q):
        return IntentRouteResult(
            route="ontology_live",
            reason="命中强制实时查数指令",
            matched_entities=matched_entities,
            allow_ontology_tools=True,
            inject_schema_context=True,
        )

    if _IDENTITY_OR_STATUS_RE.search(q):
        return IntentRouteResult(
            route="ontology_live",
            reason="命中用户身份/订单/实时状态查询特征",
            matched_entities=matched_entities,
            allow_ontology_tools=True,
            inject_schema_context=True,
        )

    # 2. 次高优先级：数据统计/聚合/汇总查询或“时间 + 业务实体”实时查数（不会被 kb_only 误杀）
    if _STATS_AGG_RE.search(q) or _TIME_BIZ_RE.search(q):
        # 优先检索该专家绑定的百科统计快照（带防串期校验）
        snapshot_hit: dict[str, Any] | None = None
        if agent_id:
            try:
                from ..wiki.storage_manager import WikiStorageManager

                candidates = await WikiStorageManager.search_stats_snapshots(
                    agent_id=agent_id,
                    user_query=q,
                    limit=3,
                    caller=caller,
                )
                for cand in candidates:
                    if check_snapshot_consistency(q, cand):
                        snapshot_hit = cand
                        break
            except Exception:
                snapshot_hit = None

        if snapshot_hit:
            return IntentRouteResult(
                route="stats_wiki_first",
                reason="命中百科统计快照且年份/维度一致，优先秒回并提示统计时间",
                matched_entities=matched_entities,
                allow_ontology_tools=True,
                inject_schema_context=False,
                snapshot_hit=snapshot_hit,
            )

        return IntentRouteResult(
            route="ontology_live",
            reason="命中数据统计/聚合特征且无匹配的百科快照，开放实时查数通道",
            matched_entities=matched_entities,
            allow_ontology_tools=True,
            inject_schema_context=True,
        )

    # 3. 命中专家绑定数据结构图谱中的真实业务实体名（如教练员/运动员/代表团/订单等）且非纯规程提问 → 开放查数通道
    if matched_entities and not _RULE_GUIDE_RE.search(q):
        return IntentRouteResult(
            route="ontology_live",
            reason=f"命中绑定数据空间业务实体 {matched_entities[:3]}，开放实时查数通道",
            matched_entities=matched_entities,
            allow_ontology_tools=True,
            inject_schema_context=True,
        )

    # 4. 纯规程/定义/流程/常识问答 → 快通道 kb_only（跳过 Schema 拼接与 Ontology 工具）
    if _RULE_GUIDE_RE.search(q):
        return IntentRouteResult(
            route="kb_only",
            reason="命中规程/规则/流程类特征且无统计或个体查询，走本地知识库快通道",
            matched_entities=matched_entities,
            allow_ontology_tools=False,
            inject_schema_context=False,
        )

    # 5. 兜底：对已授权实时查数的专家，未命中纯规程特征时默认保留查数工具（由模型按需决定是否调用，避免误杀）
    return IntentRouteResult(
        route="ontology_live",
        reason="常规业务问答默认保留实时查数通道（由模型按需调用）",
        matched_entities=matched_entities,
        allow_ontology_tools=True,
        inject_schema_context=bool(matched_entities),
    )


def extract_followup_suggestions(raw_text: str) -> tuple[str, list[dict[str, Any]]]:
    """容错解析正文末尾的 <!-- FOLLOWUP_SUGGESTIONS: [...] -->，返回 (干净正文, 追问卡片列表)。"""
    if not raw_text:
        return "", []

    suggestions: list[dict[str, Any]] = []
    match = _FOLLOWUP_BLOCK_RE.search(raw_text)
    if not match:
        # 容错：若模型输出了未闭合的 <!-- FOLLOWUP_SUGGESTIONS: 截断尾部，仍剥离之
        idx = raw_text.find("<!-- FOLLOWUP_SUGGESTIONS")
        if idx != -1:
            return raw_text[:idx].rstrip(), []
        return raw_text, []

    payload_str = match.group(1).strip()
    # 剥离可能夹带的 markdown 代码围栏
    payload_str = re.sub(r"^```(?:json)?\s*", "", payload_str)
    payload_str = re.sub(r"\s*```$", "", payload_str).strip()

    parsed: Any = None
    try:
        parsed = json.loads(payload_str)
    except Exception:
        try:
            parsed = ast.literal_eval(payload_str)
        except Exception:
            parsed = None

    if isinstance(parsed, list):
        for item in parsed[:3]:
            if isinstance(item, str) and item.strip():
                label = item.strip()[:80]
                suggestions.append({"label": label, "prompt": label, "force_live": False})
            elif isinstance(item, dict):
                label = str(item.get("label") or item.get("prompt") or "").strip()[:80]
                if label:
                    suggestions.append(
                        {
                            "label": label,
                            "prompt": str(item.get("prompt") or label).strip(),
                            "force_live": bool(item.get("force_live")),
                        }
                    )

    clean_text = _FOLLOWUP_BLOCK_RE.sub("", raw_text).rstrip()
    return clean_text, suggestions


class FollowupStreamBuffer:
    """零延迟流式尾部缓冲器：正常文本 0 扣留实时直通，仅在检测到 `<` 时暂存防 <!-- FOLLOWUP_SUGGESTIONS: [...] --> 闪烁。"""

    def __init__(self, hold_chars: int = 32) -> None:
        self._hold_chars = max(16, hold_chars)
        self._buf = ""
        self._suppressing = False

    def feed(self, chunk: str) -> str:
        """喂入新流式片段，返回可立即推送给前端的文本片段。"""
        if not chunk:
            return ""
        self._buf += chunk

        if self._suppressing:
            return ""

        # 快速路径：不含 `<` 时零扣留，逐字即时推送给前端实现极致流式体验
        lt_idx = self._buf.find("<")
        if lt_idx == -1:
            out = self._buf
            self._buf = ""
            return out

        # 先把 `<` 之前的全部安全正文立刻推出
        safe_prefix = self._buf[:lt_idx]
        tail = self._buf[lt_idx:]

        # 检查是否命中或可能命中 <!-- FOLLOWUP_SUGGESTIONS: 前缀
        target_prefix = "<!-- FOLLOWUP_SUGGESTIONS:"
        if tail.startswith("<!-- FOLLOWUP") or "FOLLOWUP_SUGGESTIONS" in tail:
            self._buf = tail
            self._suppressing = True
            return safe_prefix

        if target_prefix.startswith(tail):
            # 可能是前缀的半截（如 `<`、`<!`、`<!-- `），暂存等待下一片
            self._buf = tail
            return safe_prefix

        # 已确定不是追问注释标签（例如普通 `<br>` 或数学比较 `< 10`），全部立即放行
        self._buf = ""
        return safe_prefix + tail

    def flush(self) -> tuple[str, list[dict[str, Any]]]:
        """流结束时冲刷剩余缓冲，并提取可能存在的追问卡片。"""
        remaining = self._buf
        self._buf = ""
        self._suppressing = False
        return extract_followup_suggestions(remaining)

