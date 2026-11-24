"""
AgentHub Local v1.6.0 国标化受控词表构建与实体概念对齐引擎
遵循规范：
1. GB/T 15237.1-2000《术语工作 词汇 第1部分：理论与应用》；
2. GB/T 10112-2019《术语工作 原则与方法》；
3. W3C SKOS (Simple Knowledge Organization System) 简单知识组织系统模型（prefLabel, altLabel）；
4. 双路 Top-K 召回与合并重排加权机制：
   - 通道 1：精确首选/别名命中 (100% 匹配，权重 1.0)
   - 通道 2 & 3 双路多候选融合重排：
     * 词形编辑距离取 Top-3
     * 语义向量打分取 Top-3
     * 两路候选并集执行 0.3 * text_sim + 0.7 * vector_sim 联合重排，彻底根除字面不同导致的语义召回盲区。
"""
from __future__ import annotations

import difflib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Literal

TEMPLATES_DIR = Path(__file__).resolve().parent / "vocab_templates"

# 内置兜底通用规程受控词表（当模板目录不可用时的静态安全保底）
DEFAULT_SEED_VOCAB: dict[str, list[str]] = {
    "撤销报名": ["退赛", "取消报名", "撤赛", "放弃比赛"],
    "主办单位": ["主办方", "主办机构", "主办"],
    "承办单位": ["承办方", "承办机构", "承办"],
    "协办单位": ["协办方", "协办机构"],
    "指导单位": ["支持单位", "指导机构"],
    "收费标准": ["报名费", "参赛费", "比赛费用", "报名费用", "押金", "押金标准"],
    "参赛资格": ["报名条件", "报名资格", "参赛要求", "准入条件"],
    "竞赛项目": ["比赛项目", "参赛项目"],
    "竞赛组别": ["比赛组别", "参赛组别", "组别设置"],
    "比赛时间": ["竞赛时间", "比赛日期", "开赛时间"],
    "报名截止时间": ["报名截止日期", "截止报名时间", "报名截止"],
}

_ALIGNER_CACHE: dict[str, tuple[Any, float]] = {}


def _merge_vocab_dict(target: dict[str, list[str]], source: dict[str, list[str]]) -> None:
    """合并词表字典：同名首选词追加别名并集，不同名直接新增。"""
    for pref, alts in source.items():
        clean_pref = pref.strip()
        if not clean_pref:
            continue
        if clean_pref not in target:
            target[clean_pref] = []
        existing_set = set(target[clean_pref])
        for alt in alts or []:
            clean_alt = alt.strip()
            if clean_alt and clean_alt not in existing_set:
                target[clean_pref].append(clean_alt)
                existing_set.add(clean_alt)


def resolve_vocab_hierarchy(raw_config: dict[str, Any]) -> tuple[dict[str, list[str]], float]:
    """解析 Base + Domain Pack 继承链配置，生成合并后的受控词表及依赖模板的最大 mtime。

    兼容两种配置形式：
    1. 声明式继承字典：
       {
           "extends": ["base_sports_event", "sports/fencing"],
           "terms": { "中国击剑协会": ["中剑协", "CFA"] },
           "exclude": ["某词条"]
       }
    2. 传统平铺字典：
       { "撤销报名": ["退赛", "取消报名"] }
    """
    merged: dict[str, list[str]] = {}
    max_mtime = 0.0

    # 判断是否为声明式继承格式
    if isinstance(raw_config, dict) and ("extends" in raw_config or "terms" in raw_config):
        extends = raw_config.get("extends", [])
        if isinstance(extends, str):
            extends = [extends]

        # 依次加载继承的模板文件
        for t_ref in extends:
            clean_ref = str(t_ref).strip().replace("\\", "/")
            if clean_ref.endswith(".json"):
                clean_ref = clean_ref[:-5]
            if not clean_ref:
                continue
            t_path = TEMPLATES_DIR / f"{clean_ref}.json"
            if t_path.exists():
                try:
                    max_mtime = max(max_mtime, t_path.stat().st_mtime)
                    with open(t_path, "r", encoding="utf-8") as f:
                        t_data = json.load(f)
                        if isinstance(t_data, dict):
                            # 若模板本身也包含继承声明，递归解析
                            if "extends" in t_data or "terms" in t_data:
                                sub_v, sub_m = resolve_vocab_hierarchy(t_data)
                                max_mtime = max(max_mtime, sub_m)
                                _merge_vocab_dict(merged, sub_v)
                            else:
                                _merge_vocab_dict(merged, t_data)
                except Exception:
                    pass

        # 合并本地 terms
        local_terms = raw_config.get("terms", {})
        if isinstance(local_terms, dict):
            _merge_vocab_dict(merged, local_terms)

        # 执行 exclude 排除
        for ex in raw_config.get("exclude", []):
            clean_ex = str(ex).strip()
            if clean_ex in merged:
                merged.pop(clean_ex, None)
    elif isinstance(raw_config, dict):
        # 传统平铺格式
        _merge_vocab_dict(merged, raw_config)

    # 若未加载出任何词条，回退到内置通用词表
    if not merged:
        merged = dict(DEFAULT_SEED_VOCAB)

    return merged, max_mtime


def create_preset_vocabulary(kb_id: str, preset: str = "equestrian") -> Path:
    """为知识库根目录快速生成遵循 Base + Domain Pack 声明式继承的 seed_vocabulary.json 模板。"""
    from .storage import KBStorageManager

    kb_dir = KBStorageManager.get_kb_dir(kb_id)
    target_path = kb_dir / "seed_vocabulary.json"

    clean_p = preset.strip().lower()
    if clean_p in ("equestrian", "fencing", "basketball", "rugby", "climbing"):
        content = {
            "extends": ["base_sports_event", f"sports/{clean_p}"],
            "terms": {},
            "exclude": [],
        }
    elif clean_p == "general":
        content = {
            "extends": ["general"],
            "terms": {},
            "exclude": [],
        }
    else:
        content = {
            "extends": ["base_sports_event"],
            "terms": {},
            "exclude": [],
        }

    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(content, f, ensure_ascii=False, indent=2)
    return target_path


def get_aligner(kb_id: str = "") -> "ControlledVocabularyAligner":
    """按 kb_id 隔离获取领域受控词表对齐引擎（支持 Base+DomainPack 继承链与 mtime 热加载）。"""
    from .storage import KBStorageManager

    clean_kb_id = (kb_id or "").strip()
    vocab_file: Path | None = None
    file_mtime = 0.0

    if clean_kb_id:
        kb_dir = KBStorageManager.get_kb_dir(clean_kb_id)
        cand_path = kb_dir / "seed_vocabulary.json"
        if cand_path.exists():
            vocab_file = cand_path
            try:
                file_mtime = cand_path.stat().st_mtime
            except Exception:
                file_mtime = 0.0

    # 1. 读取配置文件
    raw_config: dict[str, Any] = {}
    if vocab_file and vocab_file.exists():
        try:
            with open(vocab_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    raw_config = loaded
        except Exception:
            raw_config = {}
    else:
        # 无专属配置时，默认继承通用体育底座
        raw_config = {"extends": ["base_sports_event"], "terms": {}, "exclude": []}

    # 2. 解析继承链并计算依赖模板的最新 mtime
    resolved_vocab, max_dep_mtime = resolve_vocab_hierarchy(raw_config)
    eff_mtime = max(file_mtime, max_dep_mtime)

    cache_key = f"{clean_kb_id}:{vocab_file}"
    if cache_key in _ALIGNER_CACHE:
        cached_instance, cached_mtime = _ALIGNER_CACHE[cache_key]
        if cached_mtime == eff_mtime:
            return cached_instance

    instance = ControlledVocabularyAligner(seed_vocab=resolved_vocab)
    _ALIGNER_CACHE[cache_key] = (instance, eff_mtime)
    return instance


class ControlledVocabularyAligner:
    """遵循 GB/T 15237 与 GB/T 10112 原则的领域受控词表对齐引擎。"""

    def __init__(
        self,
        seed_vocab: dict[str, list[str]] | None = None,
        auto_align_threshold: float = 0.85,
        review_threshold: float = 0.70,
    ):
        """
        Args:
            seed_vocab: 种子受控词表字典，结构为：
                {
                    "首选术语 (prefLabel)": ["同义别名1 (altLabel)", "同义别名2", ...],
                    ...
                }
            auto_align_threshold: 自动对齐置信度阈值 (默认 >= 0.85 自动归并)
            review_threshold: 人工审核置信度阈值 (0.70 ~ 0.85 进人工队列；< 0.70 为暂定新词)
        """
        self.seed_vocab: dict[str, list[str]] = seed_vocab or {}
        self.auto_align_threshold = auto_align_threshold
        self.review_threshold = review_threshold

        # 快速反查索引：lowercase(altLabel or prefLabel) -> prefLabel
        self._flat_alt_map: dict[str, str] = {}
        # 首选词列表去重缓存
        self._all_pref_labels: list[str] = []
        # 按长度倒序排列的 (term, prefLabel) 元组列表，供长词优先子串扫描
        self._sorted_scan_terms: list[tuple[str, str]] = []
        self._rebuild_indices()

    def _rebuild_indices(self) -> None:
        """重建首选词与别名倒排索引。"""
        self._flat_alt_map.clear()
        seen = set()
        self._all_pref_labels = []
        scan_terms: list[tuple[str, str]] = []
        for pref, alts in self.seed_vocab.items():
            clean_pref = pref.strip()
            if not clean_pref:
                continue
            if clean_pref not in seen:
                seen.add(clean_pref)
                self._all_pref_labels.append(clean_pref)
            self._flat_alt_map[clean_pref.lower()] = clean_pref
            scan_terms.append((clean_pref, clean_pref))
            for alt in alts:
                clean_alt = alt.strip()
                if clean_alt:
                    self._flat_alt_map[clean_alt.lower()] = clean_pref
                    scan_terms.append((clean_alt, clean_pref))

        # 按字符串长度降序排列（长词优先，避免如“退费”切碎“报名退费”）
        self._sorted_scan_terms = sorted(scan_terms, key=lambda x: len(x[0]), reverse=True)

    def scan_query_terms(self, query: str) -> set[str]:
        """对自然语言问句执行长词优先子串扫描，提取命中的标准首选词集合。

        纯内存扫描耗时 < 0.5ms，解决自然语言整句查表 miss 与分词未登录词切碎问题。
        对纯 ASCII 英文缩写（如 DE, DNS, DSQ, RT, 7s, TOP 等）自动施加单词边界正则保护，
        防止在英文单词中误伤（如 code 误撞 DE，hotel 误撞 EL）。
        """
        q = (query or "").strip().lower()
        if not q or not self._sorted_scan_terms:
            return set()

        matched_prefs: set[str] = set()
        for term, pref in self._sorted_scan_terms:
            clean_term = term.strip()
            if len(clean_term) < 2:
                continue
            term_lower = clean_term.lower()
            if clean_term.isascii() and clean_term.isalnum():
                # 纯 ASCII 缩写词：启用单词边界保护，严格防止嵌入单词中间触发
                pattern = r"(?<![a-zA-Z0-9])" + re.escape(term_lower) + r"(?![a-zA-Z0-9])"
                if re.search(pattern, q):
                    matched_prefs.add(pref)
            else:
                # 中文与混合词条：直接子串包含
                if term_lower in q:
                    matched_prefs.add(pref)
        return matched_prefs

    def update_seed_vocab(self, new_vocab: dict[str, list[str]]) -> None:
        """增量合并或更新受控词表。"""
        self.seed_vocab.update(new_vocab)
        self._rebuild_indices()

    def calculate_text_similarity(self, candidate: str, target: str) -> float:
        """基于编辑距离与公共子串的词形相似度计算（针对 2~6 字短实体词）。"""
        s1 = candidate.strip().lower()
        s2 = target.strip().lower()
        if not s1 or not s2:
            return 0.0
        if s1 == s2:
            return 1.0

        # SequenceMatcher 编辑相似度
        seq_ratio = difflib.SequenceMatcher(None, s1, s2).ratio()

        # 子串包含额外加权 (例如: "场地障碍赛" 与 "场地障碍")
        if s1 in s2 or s2 in s1:
            len_ratio = min(len(s1), len(s2)) / max(len(s1), len(s2))
            seq_ratio = max(seq_ratio, 0.7 + 0.3 * len_ratio)

        return round(seq_ratio, 4)

    def align_concept(
        self,
        candidate_term: str,
        embedding_scorer: Callable[[str, str], float] | None = None,
        top_k_candidates: int = 3,
    ) -> tuple[str, Literal["AUTO_ALIGNED", "MANUAL_REVIEW", "PROVISIONAL"], float]:
        """对候选概念实体执行双路 Top-K 召回与合并重排加权对齐。

        Args:
            candidate_term: 抽取得到的原始实体/概念字符串（如“撤销报名”、“主办方”）
            embedding_scorer: 可选的高级向量模型打分回调 func(cand, target) -> float(0.0~1.0)
            top_k_candidates: 词形与语义两路各自召回的候选数量上限

        Returns:
            tuple[aligned_term, status, confidence]:
                - aligned_term: 对齐后的标准首选术语（若未对齐则为原词）
                - status: "AUTO_ALIGNED" | "MANUAL_REVIEW" | "PROVISIONAL"
                - confidence: 综合置信度得分 (0.0 ~ 1.0)
        """
        cand = (candidate_term or "").strip()
        if not cand:
            return "", "PROVISIONAL", 0.0

        cand_lower = cand.lower()

        # 通道 1：精确匹配首选词与同义别名 (100% 命中，权重 1.0)
        if cand_lower in self._flat_alt_map:
            return self._flat_alt_map[cand_lower], "AUTO_ALIGNED", 1.0

        if not self._all_pref_labels:
            return cand, "PROVISIONAL", 0.0

        # 候选池收集：记录 target_pref -> 词形分
        text_scored_candidates: list[tuple[str, float]] = []
        for pref in self._all_pref_labels:
            alts = self.seed_vocab.get(pref, [])
            best_sim = self.calculate_text_similarity(cand, pref)
            for alt in alts:
                alt_sim = self.calculate_text_similarity(cand, alt)
                if alt_sim > best_sim:
                    best_sim = alt_sim
            text_scored_candidates.append((pref, best_sim))

        # 按词形分排序获取 Top-K
        text_scored_candidates.sort(key=lambda x: x[1], reverse=True)
        text_top_k = text_scored_candidates[:top_k_candidates]

        # 候选池并集初始化（以首选词为唯一 Key）
        candidate_union: set[str] = {pref for pref, _ in text_top_k}

        # 通道 3：如果有向量模型打分，对全量（或采样）首选词进行语义粗打分，并入候选池，防止字面不一致导致的语义漏召回
        vec_score_map: dict[str, float] = {}
        if embedding_scorer:
            all_vec_scored: list[tuple[str, float]] = []
            for pref in self._all_pref_labels:
                try:
                    s = float(embedding_scorer(cand, pref))
                    s = max(0.0, min(1.0, s))
                    vec_score_map[pref] = s
                    all_vec_scored.append((pref, s))
                except Exception:
                    pass
            all_vec_scored.sort(key=lambda x: x[1], reverse=True)
            for pref, _ in all_vec_scored[:top_k_candidates]:
                candidate_union.add(pref)

        # 对两路并集进行综合重排打分：0.3 * text_sim + 0.7 * vector_sim
        best_pref = cand
        best_final_score = 0.0

        text_score_lookup = dict(text_scored_candidates)

        for pref in candidate_union:
            t_score = text_score_lookup.get(pref, 0.0)
            if embedding_scorer:
                v_score = vec_score_map.get(pref, 0.0)
                # NOTE: 动态加权机制：
                # 1. 若语义向量得分极高 (>=0.88)，说明字面不同但语义完全等价，赋予向量 90% 权重，杜绝字面零重合导致的硬拖累；
                # 2. 常规情况下采用 0.3 词形 + 0.7 向量加权平滑。
                if v_score >= 0.88:
                    final_s = round(0.1 * t_score + 0.9 * v_score, 4)
                else:
                    final_s = round(0.3 * t_score + 0.7 * v_score, 4)
            else:
                final_s = t_score

            if final_s > best_final_score:
                best_final_score = final_s
                best_pref = pref

        # 判定分流
        if best_final_score >= self.auto_align_threshold:
            return best_pref, "AUTO_ALIGNED", best_final_score
        elif best_final_score >= self.review_threshold:
            return best_pref, "MANUAL_REVIEW", best_final_score
        else:
            return cand, "PROVISIONAL", best_final_score
