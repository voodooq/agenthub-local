"""L5 个人实战微经验存储与检索引擎 (Episodic Experience Store)

提供基于 SQLite FTS5 (trigram) 与三路正交锚点（实体标签 + 报错特征码 + 口语同义词簇）
的异步微经验存储、去重合并、分级归一化匹配度召回及闭环赏罚进化能力。
同时在底层数据结构中预留了 `embedding` 字段，便于后续平滑扩展本地向量检索。
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.memory.policy_guard import MemoryPolicyGuard
from app.repository.db import db_manager

logger = logging.getLogger("agenthub.experience_store")

# 常见的中英文高频停用词，避免仅凭虚词造成误匹配
_STOPWORDS: frozenset[str] = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "into", "error",
    "failed", "fail", "cannot", "cant", "not", "none", "true", "false",
    "怎么", "如何", "为什么", "报错", "失败", "问题", "一下", "可以", "这个", "那个",
    "现在", "一直", "还是", "没有", "出现", "时候", "解决", "处理", "帮忙",
})


@dataclass
class ExperienceCase:
    """单条 L5 实战微经验案例模型。"""

    id: str
    workspace_id: str = "GLOBAL"
    task_category: str = "general"
    entity_tags: str = ""
    error_signature: str = ""
    symptom_keywords: str = ""
    scene_summary: str = ""
    root_cause: str = ""
    verified_solution: str = ""
    effective_commands: str = "[]"
    origin_type: str = "auto_recover"
    confidence: float = 0.85
    hit_count: int = 0
    fail_count: int = 0
    scope: str = "private"
    source_conversation_id: str | None = None
    created_at: str = ""
    updated_at: str = ""
    last_used_at: str | None = None
    match_score: float = 0.0

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict[str, Any], match_score: float = 0.0) -> ExperienceCase:
        """从数据库行映射构造经验对象。"""
        data = dict(row)
        return cls(
            id=str(data.get("id", "")),
            workspace_id=str(data.get("workspace_id") or "GLOBAL"),
            task_category=str(data.get("task_category") or "general"),
            entity_tags=str(data.get("entity_tags") or ""),
            error_signature=str(data.get("error_signature") or ""),
            symptom_keywords=str(data.get("symptom_keywords") or ""),
            scene_summary=str(data.get("scene_summary") or ""),
            root_cause=str(data.get("root_cause") or ""),
            verified_solution=str(data.get("verified_solution") or ""),
            effective_commands=str(data.get("effective_commands") or "[]"),
            origin_type=str(data.get("origin_type") or "auto_recover"),
            confidence=float(data.get("confidence") if data.get("confidence") is not None else 0.85),
            hit_count=int(data.get("hit_count") or 0),
            fail_count=int(data.get("fail_count") or 0),
            scope=str(data.get("scope") or "private"),
            source_conversation_id=data.get("source_conversation_id"),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            last_used_at=data.get("last_used_at"),
            match_score=float(match_score),
        )


def _now_iso() -> str:
    """获取当前 UTC ISO-8601 时间戳。"""
    return datetime.now(timezone.utc).isoformat()


def _sanitize(text: str | None) -> str:
    """调用全局隐私脱敏守卫清理敏感密钥或令牌，仅提取脱敏后的文本。"""
    if not text:
        return ""
    clean_text, _ = MemoryPolicyGuard.sanitize_text(str(text))
    return clean_text.strip()


def _normalize_tag_field(value: str | list[str] | None) -> str:
    """将标签或同义词列表规范化为逗号分隔、去重、已脱敏的字符串。"""
    if not value:
        return ""
    if isinstance(value, list):
        raw_items = [str(item) for item in value]
    else:
        raw_items = re.split(r"[,，;\n|]+", str(value))

    seen: set[str] = set()
    cleaned: list[str] = []
    for item in raw_items:
        sanitized = _sanitize(item).strip()
        key = sanitized.lower()
        if sanitized and key not in seen:
            seen.add(key)
            cleaned.append(sanitized)
    return ", ".join(cleaned)


def _split_csv_terms(text: str) -> list[str]:
    """拆分逗号/斜杠/换行分隔的锚点词条。"""
    if not text:
        return []
    parts = re.split(r"[,，;/|\n]+", text)
    return [p.strip() for p in parts if p.strip()]


def _extract_cjk_bigrams(text: str) -> set[str]:
    """提取文本中所有的连续 2 字中文切片，用于无精确子串时的同义症状交叉加分。"""
    grams: set[str] = set()
    for block in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        for i in range(len(block) - 1):
            gram = block[i : i + 2]
            if gram not in _STOPWORDS:
                grams.add(gram)
    return grams


class EpisodicExperienceStore:
    """L5 个人实战微经验库引擎（全异步 aiosqlite + FTS5 trigram）。

    NOTE: `experience_cases_fts` 虚表完全由 `db.py` 中定义的 SQLite 三联触发器
    (`trg_exp_cases_ai` / `trg_exp_cases_ad` / `trg_exp_cases_au`) 自动维护。
    业务代码严禁直接对 `experience_cases_fts` 执行 INSERT / UPDATE / DELETE，
    以防止重复索引计数与事务不一致。
    """

    @staticmethod
    def _extract_query_tokens(query: str) -> tuple[list[str], list[str]]:
        """提取适用于 FTS5 trigram 检索的三字以上特征片，以及用于降级匹配的短特征片。

        Args:
            query: 用户自然语言提问或报错片段。

        Returns:
            (trigram_tokens, fallback_short_tokens) 二元组。
        """
        if not query:
            return [], []

        trigram_tokens: list[str] = []
        seen_tri: set[str] = set()
        short_tokens: list[str] = []
        seen_short: set[str] = set()

        def _add_tri(token: str) -> None:
            norm = token.strip()
            low = norm.lower()
            if len(norm) >= 3 and low not in _STOPWORDS and low not in seen_tri:
                seen_tri.add(low)
                trigram_tokens.append(norm)

        def _add_short(token: str) -> None:
            norm = token.strip()
            low = norm.lower()
            if 2 <= len(norm) and low not in _STOPWORDS and low not in seen_short:
                seen_short.add(low)
                short_tokens.append(norm)

        # 1. 提取英文标识符、文件名、错误码、命令片段
        for word in re.findall(r"[A-Za-z0-9_./:-]{2,}", query):
            cleaned = word.strip(".:/-")
            if len(cleaned) >= 3:
                _add_tri(cleaned)
                # 对如 path/file.py 或 module.func 同时拆出子段增加召回韧性
                if any(sep in cleaned for sep in (".", "/", ":", "-")):
                    for sub in re.split(r"[./:-]+", cleaned):
                        if len(sub) >= 3:
                            _add_tri(sub)
                        elif len(sub) >= 2:
                            _add_short(sub)
            elif len(cleaned) == 2:
                _add_short(cleaned)

        # 2. 提取中文连续片段并切出 3-gram（用于 FTS5 trigram）及 2-gram（用于短词补充）
        for cjk_block in re.findall(r"[\u4e00-\u9fff]{2,}", query):
            if len(cjk_block) == 2:
                _add_short(cjk_block)
            else:
                for i in range(len(cjk_block) - 2):
                    _add_tri(cjk_block[i : i + 3])
                for i in range(len(cjk_block) - 1):
                    _add_short(cjk_block[i : i + 2])

        # 控制查询宽度，优先保留前段高密度关键词
        return trigram_tokens[:18], short_tokens[:12]

    @staticmethod
    def _build_fts_match_expr(tokens: list[str]) -> str:
        """构造防 FTS5 语法注入的 OR 检索表达式。"""
        safe_terms: list[str] = []
        for token in tokens:
            escaped = token.replace('"', '""').strip()
            if len(escaped) >= 3:
                safe_terms.append(f'"{escaped}"')
        return " OR ".join(safe_terms)

    @staticmethod
    def _score_case(
        query: str,
        case: ExperienceCase,
        workspace_id: str,
        raw_bm25: float | None = None,
        trigram_tokens: list[str] | None = None,
    ) -> float:
        """计算综合归一化匹配度得分（严格落在 [0.0, 0.99] 区间）。

        分层策略：
        - 实体标签 + 报错特征码精准碰撞权重最高；
        - 口语同义词与症状 2-gram 交叉作为语义补偿；
        - 结合 BM25 强度、当前工作区亲和度、历史置信度与赏罚战绩微调。
        """
        q_lower = query.lower()
        q_cjk_bigrams = _extract_cjk_bigrams(query)

        # 1. 硬锚点：实体标签命中率
        entity_list = _split_csv_terms(case.entity_tags)
        entity_hits = 0
        for tag in entity_list:
            tag_low = tag.lower()
            if len(tag_low) >= 2 and tag_low in q_lower:
                entity_hits += 1
        tag_score = 0.0
        if entity_hits >= 2:
            tag_score = 0.45
        elif entity_hits == 1:
            tag_score = 0.28

        # 2. 报错锚点：错误签名碰撞（提取主异常类名或整段特征比对）
        err_sig = case.error_signature.strip()
        err_score = 0.0
        if err_sig:
            err_low = err_sig.lower()
            if len(err_low) >= 6 and err_low in q_lower:
                err_score = 0.50
            else:
                # 拆分冒号或空格中的关键异常词（长度 >= 5）比对
                err_parts = [p for p in re.split(r"[\s:()'\"]+", err_low) if len(p) >= 5 and p not in _STOPWORDS]
                matched_err_parts = sum(1 for p in err_parts if p in q_lower)
                if matched_err_parts >= 2:
                    err_score = 0.42
                elif matched_err_parts == 1:
                    err_score = 0.26

        # 3. 语义锚点：口语同义词簇命中与中文 2-gram 语义交叉
        symptom_list = _split_csv_terms(case.symptom_keywords)
        symptom_hits_weight = 0.0
        for sym in symptom_list:
            sym_low = sym.lower()
            if not sym_low:
                continue
            if sym_low in q_lower:
                symptom_hits_weight += 1.0
            else:
                sym_bigrams = _extract_cjk_bigrams(sym)
                if sym_bigrams and q_cjk_bigrams:
                    overlap = len(sym_bigrams & q_cjk_bigrams)
                    if overlap >= 2 or (len(sym_bigrams) <= 2 and overlap >= 1):
                        symptom_hits_weight += 0.65
        symptom_score = min(0.38, symptom_hits_weight * 0.16)

        # 4. 场景/解法摘要辅助交叉
        summary_text = f"{case.scene_summary} {case.root_cause}"
        summary_bigrams = _extract_cjk_bigrams(summary_text)
        summary_overlap = len(summary_bigrams & q_cjk_bigrams) if (summary_bigrams and q_cjk_bigrams) else 0
        tri_hit_cnt = 0
        if trigram_tokens:
            combined_low = (
                f"{case.entity_tags} {case.error_signature} {case.symptom_keywords} {case.scene_summary}"
            ).lower()
            tri_hit_cnt = sum(1 for t in trigram_tokens if t.lower() in combined_low)

        text_bonus = min(0.24, summary_overlap * 0.05 + tri_hit_cnt * 0.05)

        # 5. BM25 相关性贡献（SQLite bm25 值越小相关性越强，通常为负数）
        bm25_contrib = 0.0
        if raw_bm25 is not None:
            abs_bm25 = abs(float(raw_bm25))
            bm25_contrib = 0.28 * (abs_bm25 / (abs_bm25 + 2.2))

        raw_relevance = min(1.0, tag_score + err_score + symptom_score + text_bonus + bm25_contrib)

        # 防噪音门槛：文本特征相关性过低时直接压低分值，防止仅凭置信度浑水摸鱼
        if raw_relevance < 0.22:
            return round(raw_relevance * 1.5, 4)

        # 6. 工作区亲和加分 & 历史可靠性奖惩
        ws_bonus = 0.0
        if workspace_id and workspace_id != "GLOBAL" and case.workspace_id == workspace_id:
            ws_bonus = 0.08
        elif case.workspace_id == "GLOBAL":
            ws_bonus = 0.03

        trust_bonus = (
            max(0.0, min(1.0, case.confidence)) * 0.16
            + min(0.05, case.hit_count * 0.015)
            - min(0.25, case.fail_count * 0.06)
        )

        final_score = raw_relevance * 0.78 + ws_bonus + trust_bonus
        return round(max(0.0, min(0.99, final_score)), 4)

    async def upsert_case(
        self,
        *,
        workspace_id: str = "GLOBAL",
        task_category: str = "general",
        entity_tags: str | list[str] = "",
        error_signature: str = "",
        symptom_keywords: str | list[str] = "",
        scene_summary: str = "",
        root_cause: str = "",
        verified_solution: str = "",
        effective_commands: list[str] | str | None = None,
        origin_type: str = "auto_recover",
        confidence: float | None = None,
        scope: str = "private",
        source_conversation_id: str | None = None,
    ) -> ExperienceCase:
        """写入或原地去重合并一条 L5 实战微经验。

        - 所有文本字段与命令序列强制经过 `MemoryPolicyGuard.sanitize_text(...)[0]` 脱敏；
        - 若同工作区/GLOBAL 存在相同 `error_signature` 或同场景摘要记录，则原地合并增强置信度；
        - 否则插入新记录并由数据库触发器自动同步 `experience_cases_fts`。
        """
        ws = (workspace_id or "GLOBAL").strip() or "GLOBAL"
        clean_entity_tags = _normalize_tag_field(entity_tags)
        clean_error_sig = _sanitize(error_signature)[:240]
        clean_symptoms = _normalize_tag_field(symptom_keywords)
        clean_scene = _sanitize(scene_summary)[:180]
        clean_root = _sanitize(root_cause)[:360]
        clean_solution = _sanitize(verified_solution)[:1500]
        clean_category = _sanitize(task_category)[:40] or "general"

        # 规范化并脱敏有效命令数组
        cmd_list: list[str] = []
        if isinstance(effective_commands, str):
            try:
                parsed_cmds = json.loads(effective_commands)
                if isinstance(parsed_cmds, list):
                    cmd_list = [str(c) for c in parsed_cmds]
            except (json.JSONDecodeError, TypeError):
                if effective_commands.strip():
                    cmd_list = [effective_commands.strip()]
        elif isinstance(effective_commands, list):
            cmd_list = [str(c) for c in effective_commands]

        safe_cmds = [_sanitize(c) for c in cmd_list if _sanitize(c)][:12]
        cmds_json = json.dumps(safe_cmds, ensure_ascii=False)

        if confidence is None:
            if origin_type == "user_correction":
                init_conf = 0.95
            elif origin_type == "distill":
                init_conf = 0.90
            else:
                init_conf = 0.85
        else:
            init_conf = max(0.10, min(0.99, float(confidence)))

        now = _now_iso()

        async with db_manager.get_connection() as conn:
            existing_row: sqlite3.Row | None = None

            # 1. 优先按有效错误特征码精确查重
            if len(clean_error_sig) >= 6:
                cursor = await conn.execute(
                    """
                    SELECT * FROM experience_cases
                    WHERE workspace_id = ? AND error_signature = ?
                    ORDER BY confidence DESC, updated_at DESC LIMIT 1
                    """,
                    (ws, clean_error_sig),
                )
                existing_row = await cursor.fetchone()

            # 2. 次选按同工作区完全一致的场景摘要查重
            if existing_row is None and len(clean_scene) >= 8:
                cursor = await conn.execute(
                    """
                    SELECT * FROM experience_cases
                    WHERE workspace_id = ? AND scene_summary = ?
                    ORDER BY confidence DESC, updated_at DESC LIMIT 1
                    """,
                    (ws, clean_scene),
                )
                existing_row = await cursor.fetchone()

            if existing_row is not None:
                old = ExperienceCase.from_row(existing_row)
                merged_entities = _normalize_tag_field(
                    f"{old.entity_tags}, {clean_entity_tags}"
                )
                merged_symptoms = _normalize_tag_field(
                    f"{old.symptom_keywords}, {clean_symptoms}"
                )
                merged_err = clean_error_sig if len(clean_error_sig) >= len(old.error_signature) else old.error_signature
                merged_scene = clean_scene if len(clean_scene) >= len(old.scene_summary) else old.scene_summary
                merged_root = clean_root if len(clean_root) >= len(old.root_cause) else old.root_cause
                merged_solution = clean_solution if len(clean_solution) >= len(old.verified_solution) else old.verified_solution
                merged_cmds = cmds_json if len(safe_cmds) > 0 else old.effective_commands
                merged_conf = round(min(0.99, max(old.confidence, init_conf) + 0.05), 4)

                await conn.execute(
                    """
                    UPDATE experience_cases
                    SET task_category = ?,
                        entity_tags = ?,
                        error_signature = ?,
                        symptom_keywords = ?,
                        scene_summary = ?,
                        root_cause = ?,
                        verified_solution = ?,
                        effective_commands = ?,
                        origin_type = ?,
                        confidence = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        clean_category or old.task_category,
                        merged_entities,
                        merged_err,
                        merged_symptoms,
                        merged_scene,
                        merged_root,
                        merged_solution,
                        merged_cmds,
                        origin_type or old.origin_type,
                        merged_conf,
                        now,
                        old.id,
                    ),
                )
                await conn.commit()
                cursor = await conn.execute("SELECT * FROM experience_cases WHERE id = ?", (old.id,))
                updated_row = await cursor.fetchone()
                return ExperienceCase.from_row(updated_row) if updated_row else old

            # 3. 写入全新案例（由 trg_exp_cases_ai 自动同步 FTS5）
            case_id = f"exp_{uuid.uuid4().hex[:12]}"
            await conn.execute(
                """
                INSERT INTO experience_cases (
                    id, workspace_id, task_category, entity_tags, error_signature,
                    symptom_keywords, scene_summary, root_cause, verified_solution,
                    effective_commands, origin_type, confidence, hit_count, fail_count,
                    scope, source_conversation_id, created_at, updated_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, NULL)
                """,
                (
                    case_id,
                    ws,
                    clean_category,
                    clean_entity_tags,
                    clean_error_sig,
                    clean_symptoms,
                    clean_scene,
                    clean_root,
                    clean_solution,
                    cmds_json,
                    origin_type,
                    init_conf,
                    scope,
                    source_conversation_id,
                    now,
                    now,
                ),
            )
            await conn.commit()
            return ExperienceCase(
                id=case_id,
                workspace_id=ws,
                task_category=clean_category,
                entity_tags=clean_entity_tags,
                error_signature=clean_error_sig,
                symptom_keywords=clean_symptoms,
                scene_summary=clean_scene,
                root_cause=clean_root,
                verified_solution=clean_solution,
                effective_commands=cmds_json,
                origin_type=origin_type,
                confidence=init_conf,
                hit_count=0,
                fail_count=0,
                scope=scope,
                source_conversation_id=source_conversation_id,
                created_at=now,
                updated_at=now,
                last_used_at=None,
                match_score=0.0,
            )

    async def search_for_context(
        self,
        query: str,
        *,
        workspace_id: str = "GLOBAL",
        limit: int = 3,
        min_score: float = 0.65,
    ) -> list[ExperienceCase]:
        """为 Zone 3 上下文构建检索高匹配度的历史微经验。

        过滤策略：
        - 仅检索当前 `workspace_id` 与 `GLOBAL` 下的案例（防止跨工程污染）；
        - 先经 FTS5 trigram BM25 召回，再结合短特征片 LIKE 兜底，最后统一重排并过滤掉 `< min_score` 的噪声。
        """
        clean_query = _sanitize(query)
        if not clean_query:
            return []

        ws = (workspace_id or "GLOBAL").strip() or "GLOBAL"
        tri_tokens, short_tokens = self._extract_query_tokens(clean_query)
        fts_expr = self._build_fts_match_expr(tri_tokens)

        candidates: dict[str, tuple[sqlite3.Row, float | None]] = {}

        async with db_manager.get_connection() as conn:
            # 1. FTS5 trigram 召回
            if fts_expr:
                try:
                    cursor = await conn.execute(
                        """
                        SELECT c.*, bm25(experience_cases_fts) AS rank_score
                        FROM experience_cases_fts f
                        JOIN experience_cases c ON f.case_id = c.id
                        WHERE experience_cases_fts MATCH ?
                          AND c.workspace_id IN (?, 'GLOBAL')
                        ORDER BY rank_score ASC
                        LIMIT ?
                        """,
                        (fts_expr, ws, max(12, limit * 5)),
                    )
                    for row in await cursor.fetchall():
                        cid = str(row["id"])
                        candidates[cid] = (row, float(row["rank_score"]))
                except sqlite3.OperationalError as exc:
                    logger.warning("FTS5 检索表达式异常，降级为短词兜底: %s", exc)

            # 2. 短词与同义词切片兜底召回（防止 <3 字符短词或纯同义表达漏召）
            if len(candidates) < max(6, limit * 2):
                search_terms = short_tokens[:6] or tri_tokens[:4]
                if search_terms:
                    like_clauses: list[str] = []
                    like_params: list[Any] = [ws]
                    for term in search_terms:
                        pat = f"%{term}%"
                        like_clauses.append(
                            "(entity_tags LIKE ? OR symptom_keywords LIKE ? OR error_signature LIKE ? OR scene_summary LIKE ?)"
                        )
                        like_params.extend([pat, pat, pat, pat])
                    sql = f"""
                        SELECT * FROM experience_cases
                        WHERE workspace_id IN (?, 'GLOBAL')
                          AND ({' OR '.join(like_clauses)})
                        ORDER BY confidence DESC, hit_count DESC
                        LIMIT ?
                    """
                    like_params.append(max(12, limit * 4))
                    cursor = await conn.execute(sql, like_params)
                    for row in await cursor.fetchall():
                        cid = str(row["id"])
                        if cid not in candidates:
                            candidates[cid] = (row, None)

        scored: list[ExperienceCase] = []
        for row, bm25_val in candidates.values():
            case = ExperienceCase.from_row(row)
            score = self._score_case(
                clean_query,
                case,
                workspace_id=ws,
                raw_bm25=bm25_val,
                trigram_tokens=tri_tokens,
            )
            if score >= min_score:
                case.match_score = score
                scored.append(case)

        scored.sort(key=lambda item: (item.match_score, item.confidence, item.hit_count), reverse=True)
        return scored[:limit]

    async def search_by_error(
        self,
        error_text: str,
        *,
        workspace_id: str = "GLOBAL",
        limit: int = 2,
        min_score: float = 0.65,
    ) -> list[ExperienceCase]:
        """盘中工具报错即时急救检索。

        提取报错堆栈末尾的关键异常签名与文件/模块上下文，精准召回历史自愈或避坑解法。
        """
        clean_err = _sanitize(error_text)
        if not clean_err:
            return []

        ws = (workspace_id or "GLOBAL").strip() or "GLOBAL"
        # 取首尾最具辨识度的报错信息避免长堆栈稀释权重
        lines = [line.strip() for line in clean_err.splitlines() if line.strip()]
        focused_err = "\n".join(lines[-6:]) if len(lines) > 6 else clean_err
        focused_err = focused_err[-800:]

        # 先尝试直接子串命中 error_signature
        direct_hits: list[ExperienceCase] = []
        seen_ids: set[str] = set()
        async with db_manager.get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM experience_cases
                WHERE workspace_id IN (?, 'GLOBAL')
                  AND length(error_signature) >= 5
                ORDER BY confidence DESC, hit_count DESC
                LIMIT 50
                """,
                (ws,),
            )
            rows = await cursor.fetchall()
            err_low = clean_err.lower()
            for row in rows:
                sig = str(row["error_signature"] or "").strip()
                if sig and sig.lower() in err_low:
                    case = ExperienceCase.from_row(row)
                    case.match_score = self._score_case(focused_err, case, workspace_id=ws)
                    case.match_score = max(case.match_score, min(0.99, 0.84 + case.confidence * 0.12))
                    if case.match_score >= min_score:
                        direct_hits.append(case)
                        seen_ids.add(case.id)

        fts_hits = await self.search_for_context(
            focused_err,
            workspace_id=ws,
            limit=limit + 2,
            min_score=min_score,
        )
        for item in fts_hits:
            if item.id not in seen_ids:
                direct_hits.append(item)
                seen_ids.add(item.id)

        direct_hits.sort(key=lambda c: (c.match_score, c.confidence, c.hit_count), reverse=True)
        return direct_hits[:limit]

    async def record_feedback(self, case_ids: list[str], *, success: bool) -> None:
        """执行闭环赏罚打分反馈。

        - 复用成功：`hit_count + 1`，置信度 `+0.03`（上限 `0.99`）
        - 注入后仍失败：`fail_count + 1`，置信度 `-0.15`（下限 `0.10`）
        NOTE: 仅更新打分列与 `last_used_at`，不触动触发器监控列，避免无意义的 FTS5 索引重建。
        """
        unique_ids = [cid for cid in dict.fromkeys(case_ids) if cid]
        if not unique_ids:
            return

        now = _now_iso()
        async with db_manager.get_connection() as conn:
            for cid in unique_ids:
                if success:
                    await conn.execute(
                        """
                        UPDATE experience_cases
                        SET hit_count = hit_count + 1,
                            confidence = MIN(0.99, confidence + 0.03),
                            last_used_at = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (now, now, cid),
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE experience_cases
                        SET fail_count = fail_count + 1,
                            confidence = MAX(0.10, confidence - 0.15),
                            last_used_at = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (now, now, cid),
                    )
            await conn.commit()

    async def list_cases(
        self,
        *,
        workspace_id: str | None = None,
        limit: int = 50,
    ) -> list[ExperienceCase]:
        """按置信度与更新时间列出经验库记录。"""
        async with db_manager.get_connection() as conn:
            if workspace_id:
                cursor = await conn.execute(
                    """
                    SELECT * FROM experience_cases
                    WHERE workspace_id IN (?, 'GLOBAL')
                    ORDER BY confidence DESC, updated_at DESC
                    LIMIT ?
                    """,
                    (workspace_id, limit),
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT * FROM experience_cases
                    ORDER BY confidence DESC, updated_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            rows = await cursor.fetchall()
            return [ExperienceCase.from_row(r) for r in rows]

    async def delete_case(self, case_id: str) -> bool:
        """删除指定微经验案例（由 trg_exp_cases_ad 自动清理 FTS 索引）。"""
        async with db_manager.get_connection() as conn:
            cursor = await conn.execute("DELETE FROM experience_cases WHERE id = ?", (case_id,))
            await conn.commit()
            return (cursor.rowcount or 0) > 0


# 全局唯一微经验库单例，供 ContextBuilder / Runtime / Distiller 共享调用
episodic_experience_store = EpisodicExperienceStore()

