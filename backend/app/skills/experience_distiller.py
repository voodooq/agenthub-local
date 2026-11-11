"""
任务后经验提炼器与 Skill 自进化服务 (ExperienceDistiller & SkillExperienceService)
核心机制：
1. 规则触发：仅当任务满足「自愈恢复 >= 1 次」「人工介入纠偏」或「无 Skill 指导下成功执行 >= 4 步有效工具调用」时才触发经验提炼
2. 幂等防重：以 loop_id 为主键防重，避免 AgentRuntime 与 TaskLoopRunner 双入口重复提炼或重复落库
3. HITL Diff 安全门控：所有提炼结果先写入 experience_proposals（pending）并生成统一 \n 换行的 Unified Diff，经人工确认后再物理落盘至 Skill 目录
4. 0ms 热加载闭环：合入经验或新建四元组 Skill 后，立即同步至 SQLite FTS5 并清理前后端 Skill 缓存
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import difflib
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any
import uuid

from ..core.logging_config import logger
from ..extensions.skill_loader import bump_semver, normalize_version_str, skill_loader
from ..memory.experience_store import ExperienceCase, episodic_experience_store
from ..memory.fact_manager import fact_manager
from ..memory.ledger import event_ledger
from ..memory.policy_guard import MemoryPolicyGuard
from ..repository.db import db_manager
from ..repository.workspace_repository import WorkspaceRepository
from ..security.skill_auditor import audit_skill_package
from ..tools.ast_skeleton import validate_syntax_before_write
from .skill_service import SkillService, invalidate_skill_file_cache


@dataclass
class ExecutionTrace:
    """单次任务闭环或回合的执行轨迹上下文，用于判断是否具备经验沉淀价值"""

    workspace_id: str
    conversation_id: str
    loop_id: str | None = None
    scene_mode: str = "agent"
    incognito: bool = False
    user_goal: str = ""
    active_skill_ids: list[str] = field(default_factory=list)
    recovered_error_count: int = 0
    had_user_intervention: bool = False
    total_tool_steps: int = 0
    circuit_breaker_triggered: bool = False
    final_success: bool = True
    final_summary: str = ""
    produced_artifacts: list[str] = field(default_factory=list)
    workspace_root: str | None = None


def _normalize_kebab_id(raw_name: str, fallback: str = "auto-skill") -> str:
    """将任意名称归一化为合法的 kebab-case Skill ID"""
    cleaned = re.sub(r"[^a-zA-Z0-9\-_]+", "-", (raw_name or "").strip().lower())
    cleaned = cleaned.replace("_", "-")
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    if not cleaned:
        return fallback
    return cleaned[:48]


def _normalize_newlines(text: str) -> str:
    """统一替换为 Unix \\n 换行符，彻底消除 Windows 环境下 \\r\\n 导致的 Diff 伪差异"""
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def _build_unified_diff(old_text: str, new_text: str, file_label: str) -> str:
    """生成标准化 Unified Diff 预览字符串"""
    old_lines = _normalize_newlines(old_text).splitlines()
    new_lines = _normalize_newlines(new_text).splitlines()
    diff_lines = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{file_label}",
            tofile=f"b/{file_label}",
            lineterm="",
        )
    )
    return "\n".join(diff_lines)


# 纯只读查询工具集合：单次或浅层调用不具备工作流 SOP 沉淀价值
_READONLY_QUERY_TOOLS = frozenset(
    {
        "read_local_file",
        "list_local_directory",
        "list_dir",
        "find_by_name",
        "grep_search",
        "search_files",
        "search_web",
        "web_search",
        "get_current_time",
        "search_memory",
        "query_memory",
        "get_workspace_info",
    }
)


def evaluate_skill_distill_eligibility(
    ledger_events: list[dict[str, Any]],
    *,
    loop_status: str = "completed",
    has_pending_error: bool = False,
) -> dict[str, Any]:
    """
    评估一次任务循环是否具备合法、无误、值得沉淀为可复用 Skill 的资格：
    1. 必须是已完成状态（completed），且尾部无未恢复报错；
    2. 必须至少有 1 步有效成功的工具执行（纯闲聊直接否决）；
    3. 过滤单纯 1~2 步纯只读查询噪音（除非伴随产物输出、故障自愈、人工纠偏或 >= 3 步组合链）。
    """
    if loop_status != "completed":
        return {
            "distillable": False,
            "reason": "仅完整闭环且无错误交付的任务流程可沉淀为 Skill",
            "effective_tool_count": 0,
            "recovery_count": 0,
            "intervention_count": 0,
            "artifact_count": 0,
            "skill_hints": [],
            "has_trailing_error": False,
        }

    if has_pending_error:
        return {
            "distillable": False,
            "reason": "流程末尾仍存在未修复报错，禁止沉淀带病 Skill",
            "effective_tool_count": 0,
            "recovery_count": 0,
            "intervention_count": 0,
            "artifact_count": 0,
            "skill_hints": [],
            "has_trailing_error": True,
        }

    effective_tools: list[dict[str, Any]] = []
    recoveries: list[dict[str, Any]] = []
    interventions: list[str] = []
    artifacts: list[str] = []
    skill_hints: list[str] = []
    has_trailing_error = False

    for evt in ledger_events or []:
        etype = evt.get("event_type")
        payload = evt.get("payload") if isinstance(evt.get("payload"), dict) else {}
        s_id = str(evt.get("skill_id") or "").strip()
        if s_id and s_id not in skill_hints:
            skill_hints.append(s_id)

        if etype == "tool_executed":
            tool_name = str(payload.get("tool") or "").strip()
            if not tool_name:
                continue
            args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
            arg_sk = str(args.get("skill_name") or "").strip()
            if arg_sk and arg_sk not in skill_hints:
                skill_hints.append(arg_sk)
            if payload.get("is_error"):
                has_trailing_error = True
            else:
                has_trailing_error = False
                effective_tools.append(
                    {
                        "tool": tool_name,
                        "args": args,
                        "compressed": str(payload.get("compressed") or ""),
                    }
                )
        elif etype == "tool_error_recovered":
            has_trailing_error = False
            recoveries.append(payload)
        elif etype == "user_intervention":
            interventions.append(str(payload.get("guidance") or payload.get("action") or "intervention"))
        elif etype == "artifact_produced":
            fp = str(payload.get("file_path") or payload.get("path") or "").strip()
            if fp and fp not in artifacts:
                artifacts.append(fp)

    base_stats = {
        "effective_tool_count": len(effective_tools),
        "recovery_count": len(recoveries),
        "intervention_count": len(interventions),
        "artifact_count": len(artifacts),
        "skill_hints": skill_hints,
        "has_trailing_error": has_trailing_error,
    }

    if has_trailing_error:
        return {
            **base_stats,
            "distillable": False,
            "reason": "执行链路结尾存在未恢复报错，禁止沉淀带病 Skill",
        }

    if not effective_tools:
        return {
            **base_stats,
            "distillable": False,
            "reason": "纯文本对话无实际工具调用链路，不满足 Skill 提炼门槛",
        }

    has_action_tool = any(t["tool"] not in _READONLY_QUERY_TOOLS for t in effective_tools)
    has_workflow_value = (
        has_action_tool
        or bool(artifacts)
        or bool(recoveries)
        or bool(interventions)
        or len(effective_tools) >= 3
    )
    if not has_workflow_value:
        return {
            **base_stats,
            "distillable": False,
            "reason": "仅包含轻量只读检索，缺乏可复用工作流与状态变更价值",
        }

    return {**base_stats, "distillable": True, "reason": None}


def determine_semver_bump_level(
    *,
    has_schema_change: bool = False,
    do_steps: list[str] | None = None,
    has_new_script: bool = False,
    has_new_artifacts: bool = False,
    force_patch_only: bool = False,
) -> str:
    """
    判定语义化版本递增粒度：
    - 新增或变更外部入参契约 (`schema.json`)、核心执行步骤、新交付产物或主干脚本时返回 `minor`；
    - 仅补充避坑提示、微调正文或补充上下文文档时默认返回 `patch`。
    """
    if force_patch_only:
        return "patch"
    if (
        has_schema_change
        or has_new_script
        or has_new_artifacts
        or bool(do_steps and len(do_steps) > 0)
    ):
        return "minor"
    return "patch"


def _build_troubleshooting_bullets(recoveries: list[dict[str, str]]) -> list[str]:
    """将轨迹中的失败自愈记录脱敏后转化为 Skill 避坑指南条款，供容错沉淀"""
    notes: list[str] = []
    seen: set[str] = set()
    for rec in recoveries:
        failed_tool = str(rec.get("failed_tool") or "").strip()
        failed_sum = str(rec.get("failed_summary") or "").strip()
        rec_tool = str(rec.get("recovery_tool") or "").strip()
        rec_sum = str(rec.get("recovery_compressed") or "").strip()
        if not (failed_sum or rec_sum):
            continue
        raw_line = (
            f"若调用 `{failed_tool or 'tool'}` 遇到 `{failed_sum[:120]}`，"
            f"应改为通过 `{rec_tool or failed_tool or 'tool'}` 处理：{rec_sum[:140] or '按已验证路径执行'}"
        )
        safe_line, _ = MemoryPolicyGuard.sanitize_text(raw_line)
        key = safe_line.lower()
        if safe_line and key not in seen:
            seen.add(key)
            notes.append(safe_line)
    return notes[:6]


def _append_dedup_playbook_entry(pb_path: Path, new_entry: str) -> None:
    """去重增量追加经验条目到 knowledge/experience_playbook.md，不覆写既有条目"""
    clean_entry = _normalize_newlines(new_entry).strip()
    if not clean_entry:
        return
    old_pb = _normalize_newlines(pb_path.read_text(encoding="utf-8", errors="replace")) if pb_path.is_file() else ""
    if old_pb.strip():
        entry_sig = clean_entry.splitlines()[0].strip()
        # 若完全相同的正文已存在则跳过重复写入；同日标题微调差异时仍安全保留新条目
        if clean_entry in old_pb:
            return
        if entry_sig.startswith("## ") and entry_sig in old_pb and len(clean_entry) < 180:
            return
        final_pb = old_pb.rstrip() + "\n\n" + clean_entry + "\n"
    else:
        final_pb = (
            "# 实战避坑守则与演进经验台账 (Experience Playbook)\n\n"
            + clean_entry
            + "\n"
        )
    pb_path.parent.mkdir(parents=True, exist_ok=True)
    pb_path.write_text(final_pb, encoding="utf-8")


def _atomic_replace_skill_dir(staging_dir: Path, target_dir: Path) -> None:
    """
    通过临时备份 + 目录原子移动替换目标 Skill 目录，避免中断产生残损或误删用户自定义资产；
    若替换过程中发生异常自动将备份回滚恢复。
    """
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    backup_dir: Path | None = None
    if target_dir.exists():
        backup_dir = target_dir.parent / f".backup_{target_dir.name}_{uuid.uuid4().hex[:6]}"
        if backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)
        shutil.move(str(target_dir), str(backup_dir))

    try:
        shutil.move(str(staging_dir), str(target_dir))
    except Exception:
        if backup_dir and backup_dir.exists() and not target_dir.exists():
            try:
                shutil.move(str(backup_dir), str(target_dir))
            except Exception as rollback_err:
                logger.error(f"[ExperienceDistiller] 回滚既有 Skill 目录失败: {rollback_err}")
        raise
    else:
        if backup_dir and backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)


def _safe_json_loads(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, type(fallback)) else fallback
    except Exception:
        return fallback


def _serialize_proposal_row(row: Any) -> dict[str, Any]:
    """将数据库 experience_proposals 行记录转换为结构化字典"""
    d = dict(row)
    triple = _safe_json_loads(d.pop("experience_triple_json", "{}"), {})
    payload = _safe_json_loads(d.pop("payload_json", "{}"), {})
    d["experience_triple"] = {
        "problem": str(triple.get("problem") or ""),
        "root_cause": str(triple.get("root_cause") or ""),
        "solution": str(triple.get("solution") or ""),
    }
    d["payload"] = payload
    return d


class ExperienceDistiller:
    """Post-Task 经验提炼引擎（支持轻量模型提纯与确定性启发式降级）"""

    _ANTI_PATTERN_REGEX = re.compile(
        r"(?:mock_binary_content|create_mock_sheets|自动创建\s*mock|生成\s*mock\s*(?:测试)?(?:文件|数据).*兜底"
        r"|缺失.*(?:生成|创建).*(?:模拟|mock|测试).*(?:文件|数据|excel)|模拟测试数据(?:进行)?兜底"
        r"|创建模拟.{0,16}(?:excel|附件|数据)|工作区输入附件目录.*只读)",
        re.IGNORECASE | re.DOTALL,
    )

    def __init__(self) -> None:
        self._processed_loop_ids: set[str] = set()

    @classmethod
    def _detect_mock_antipattern(
        cls,
        *,
        trace: ExecutionTrace,
        ledger_events: list[dict[str, Any]],
        llm_data: dict[str, Any] | None = None,
        triple: dict[str, str] | None = None,
    ) -> str | None:
        """
        检测轨迹或拟生成提案中是否包含“找不到输入文件时自行创建 Mock/模拟文件兜底”的反模式，
        防止 AI 幻觉违规行为污染工作区或全局 Skill 知识库。
        """
        for evt in ledger_events:
            etype = evt.get("event_type")
            payload = evt.get("payload") if isinstance(evt.get("payload"), dict) else {}
            if etype == "tool_executed":
                tool_name = str(payload.get("tool") or "").strip()
                args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
                out_prev = str(payload.get("output_preview") or "")
                if tool_name == "write_local_file":
                    fp = str(args.get("file_path") or args.get("path") or "").replace("\\", "/").lower()
                    if re.search(r"(?:^|/)attachments/", fp):
                        return f"禁止把向只读 attachments/ 目录写入文件的轨迹沉淀为经验 ({fp})"
                for text_val in (
                    out_prev,
                    json.dumps(args, ensure_ascii=False),
                    str(payload.get("compressed") or ""),
                ):
                    if cls._ANTI_PATTERN_REGEX.search(text_val):
                        return "轨迹包含编造 Mock 替代输入兜底的违规信号"

        check_texts = [trace.user_goal, trace.final_summary]
        if llm_data:
            check_texts.extend([
                str(llm_data.get("title") or ""),
                str(llm_data.get("summary") or ""),
                str(llm_data.get("problem") or ""),
                str(llm_data.get("root_cause") or ""),
                str(llm_data.get("solution") or ""),
            ])
        if triple:
            check_texts.extend([
                str(triple.get("problem") or ""),
                str(triple.get("root_cause") or ""),
                str(triple.get("solution") or ""),
            ])

        for txt in check_texts:
            if txt and cls._ANTI_PATTERN_REGEX.search(txt):
                return "提炼结果命中严禁生成的 Mock 兜底反模式规范"
        return None

    @staticmethod
    def evaluate_trigger(trace: ExecutionTrace) -> tuple[bool, str]:
        """评估当前轨迹是否满足经验沉淀触发阈值并返回触发原因"""
        if trace.incognito:
            return False, "incognito"
        if trace.scene_mode in ("oneshot", "plan_first"):
            return False, f"scene_{trace.scene_mode}"
        if not trace.final_success or trace.circuit_breaker_triggered:
            return False, "unsuccessful_or_fused"

        if trace.recovered_error_count >= 1:
            return True, "self_healed"
        if trace.had_user_intervention:
            return True, "user_intervention"
        if not trace.active_skill_ids and trace.total_tool_steps >= 4:
            return True, "complex_unskilled_workflow"
        return False, "threshold_not_met"

    @classmethod
    def should_distill(cls, trace: ExecutionTrace) -> bool:
        """判断是否满足提炼触发条件"""
        should_run, _ = cls.evaluate_trigger(trace)
        return should_run

    @staticmethod
    async def _get_existing_proposal_by_loop(
        loop_id: str,
        *,
        status_filter: str | None = None,
    ) -> dict[str, Any] | None:
        clean_loop = (loop_id or "").strip()
        if not clean_loop:
            return None
        query = "SELECT * FROM experience_proposals WHERE loop_id = ?"
        params: list[Any] = [clean_loop]
        if status_filter:
            query += " AND status = ?"
            params.append(status_filter)
        query += " ORDER BY created_at DESC LIMIT 1"

        async with db_manager.get_connection() as conn:
            async with conn.execute(query, tuple(params)) as cur:
                row = await cur.fetchone()
        return _serialize_proposal_row(row) if row else None

    @staticmethod
    def _extract_effective_steps(ledger_events: list[dict[str, Any]]) -> dict[str, Any]:
        """从账本事件中剔除失败试错弯路，提取成功算子链路、报错自愈对与人工介入指令"""
        effective_tools: list[dict[str, Any]] = []
        recoveries: list[dict[str, str]] = []
        interventions: list[str] = []
        artifacts: list[str] = []
        skill_hints: list[str] = []

        for evt in ledger_events:
            etype = evt.get("event_type")
            payload = evt.get("payload") if isinstance(evt.get("payload"), dict) else {}
            s_id = str(evt.get("skill_id") or "").strip()
            if s_id and s_id not in skill_hints:
                skill_hints.append(s_id)

            if etype == "tool_executed":
                tool_name = str(payload.get("tool") or "").strip()
                if not tool_name:
                    continue
                args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
                arg_sk = str(args.get("skill_name") or "").strip()
                if arg_sk and arg_sk not in skill_hints:
                    skill_hints.append(arg_sk)
                if not payload.get("is_error"):
                    effective_tools.append({
                        "tool": tool_name,
                        "args": args,
                        "compressed": str(payload.get("compressed") or ""),
                    })
            elif etype == "tool_error_recovered":
                recoveries.append({
                    "failed_tool": str(payload.get("failed_tool") or ""),
                    "failed_summary": str(payload.get("failed_summary") or ""),
                    "recovery_tool": str(payload.get("recovery_tool") or ""),
                    "recovery_compressed": str(payload.get("recovery_compressed") or ""),
                })
            elif etype == "user_intervention":
                msg = str(
                    payload.get("guidance")
                    or payload.get("action")
                    or payload.get("message")
                    or "人工介入调整执行方向"
                ).strip()
                interventions.append(msg)
            elif etype == "artifact_produced":
                fp = str(payload.get("file_path") or payload.get("path") or "").strip()
                if fp and fp not in artifacts:
                    artifacts.append(fp)

        return {
            "effective_tools": effective_tools,
            "recoveries": recoveries,
            "interventions": interventions,
            "artifacts": artifacts,
            "skill_hints": skill_hints,
        }

    @staticmethod
    def _build_skill_patch_entry(
        *,
        title: str,
        trigger_reason: str,
        problem: str,
        root_cause: str,
        solution: str,
        do_steps: list[str],
        version_transition: str = "",
    ) -> str:
        """构造标准化的 experience_playbook.md 追加条目 Markdown"""
        now_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        reason_label = {
            "self_healed": "故障自愈沉淀",
            "user_intervention": "人工纠偏沉淀",
            "manual_distill": "人工手动归档",
            "complex_unskilled_workflow": "复杂链路总结",
        }.get(trigger_reason, trigger_reason)

        ver_prefix = f"[{version_transition}] " if version_transition else ""
        lines = [
            f"## [{now_date}] {ver_prefix}{title.strip()} ({reason_label})",
            f"- **触发场景与异常现象**: {problem.strip()}",
            f"- **根因定位**: {root_cause.strip()}",
            f"- **最佳实践与规避方案**: {solution.strip()}",
        ]
        if do_steps:
            lines.append("- **已验证标准动作清单**:")
            for idx, step in enumerate(do_steps[:6], 1):
                lines.append(f"  {idx}. {step}")
        return _normalize_newlines("\n".join(lines).strip())

    @staticmethod
    def build_upgraded_skill_md(
        *,
        skill_id: str,
        workspace_root: str | None,
        title: str,
        goal: str,
        problem: str,
        root_cause: str,
        solution: str,
        do_steps: list[str],
        llm_upgraded_body: str = "",
        next_version_override: str | None = None,
        troubleshooting_notes: list[str] | None = None,
        bump_level: str | None = None,
    ) -> tuple[str, str, str, str, str]:
        """
        基于已有 Skill 的 SKILL.md 融合本次优化经验与 SOP，生成递增版本号后的新版 SKILL.md。
        返回: (old_skill_md, new_skill_md, current_version, next_version, command)
        """
        clean_id = skill_loader._sanitize_skill_id(skill_id)
        existing_dir, _ = skill_loader.resolve_skill_dir_with_scope(
            skill_id,
            workspace_root=workspace_root,
        )
        old_skill_md = ""
        if existing_dir and (existing_dir / "SKILL.md").is_file():
            try:
                old_skill_md = _normalize_newlines(
                    (existing_dir / "SKILL.md").read_text(encoding="utf-8", errors="replace")
                )
            except OSError:
                old_skill_md = ""

        if old_skill_md.strip():
            meta = skill_loader._parse_frontmatter(old_skill_md, existing_dir.name if existing_dir else clean_id)
        else:
            meta = {
                "name": clean_id,
                "command": f"/{clean_id}",
                "version": "1.0.0",
                "type": "skill",
                "mcp_server": "",
                "description": f"{clean_id} 自动化专业技能包",
                "body": f"# {clean_id} 操作规程",
            }

        current_version = normalize_version_str(meta.get("version"))
        effective_bump = bump_level or determine_semver_bump_level(
            do_steps=do_steps,
            force_patch_only=bool(not do_steps and troubleshooting_notes),
        )
        next_version = (
            normalize_version_str(next_version_override)
            if next_version_override
            else bump_semver(current_version, level=effective_bump)
        )
        now_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        clean_llm_body = _normalize_newlines(llm_upgraded_body or "").strip()
        if clean_llm_body.startswith("---"):
            clean_llm_body = re.sub(r"^---\s*\n.*?\n---\s*\n?", "", clean_llm_body, count=1, flags=re.DOTALL).strip()

        if len(clean_llm_body) >= 30:
            new_body = clean_llm_body
        else:
            old_body = _normalize_newlines(meta.get("body") or f"# {meta['name']}").strip()
            iter_lines = [
                f"## 迭代优化规程 (v{next_version} · {now_day})",
                f"- **本次迭代主题**: {title or goal[:80] or '规程优化升级'}",
                f"- **针对场景与问题**: {problem.strip()}",
                f"- **根因诊断结论**: {root_cause.strip()}",
                f"- **升级方案与规范**: {solution.strip()}",
            ]
            if do_steps:
                iter_lines.append("- **标准执行与校验动作**:")
                for idx, st in enumerate(do_steps[:6], 1):
                    iter_lines.append(f"  {idx}. {st}")
            iter_block = "\n".join(iter_lines).strip()
            new_body = f"{old_body}\n\n{iter_block}" if old_body else iter_block

        # 将自愈避坑要点固化为 Troubleshooting 节（已存在相同提示则跳过）
        clean_notes = [n.strip() for n in (troubleshooting_notes or []) if n and n.strip()]
        unseen_notes = [n for n in clean_notes if n not in new_body]
        if unseen_notes:
            ts_section = "## Troubleshooting / 避坑注意事项\n" + "\n".join(f"- {note}" for note in unseen_notes)
            new_body = f"{new_body.rstrip()}\n\n{ts_section}"

        desc_clean, _ = MemoryPolicyGuard.sanitize_text(meta.get("description") or f"{meta['name']} 专业技能")
        body_clean, _ = MemoryPolicyGuard.sanitize_text(new_body)
        mcp_line = f"mcp_server: {meta['mcp_server']}\n" if meta.get("mcp_server") else ""

        new_skill_md = _normalize_newlines(
            f"---\n"
            f"name: {meta['name']}\n"
            f"command: {meta['command']}\n"
            f"version: {next_version}\n"
            f"updated_at: {now_day}\n"
            f"type: {meta.get('type') or 'skill'}\n"
            f"{mcp_line}"
            f"description: {desc_clean}\n"
            f"---\n\n"
            f"{body_clean.strip()}\n"
        )
        return old_skill_md, new_skill_md, current_version, next_version, meta["command"]

    @staticmethod
    def _build_new_skill_files(
        *,
        skill_id: str,
        command: str,
        description: str,
        goal: str,
        triple: dict[str, str],
        effective_tools: list[dict[str, Any]],
        active_facts: list[dict[str, Any]],
        artifacts: list[str],
        trigger_reason: str,
        troubleshooting_notes: list[str] | None = None,
    ) -> tuple[str, dict[str, Any] | None, str]:
        """生成新四元组 Skill 的 SKILL.md、schema.json 与创世 experience_playbook.md"""
        cmd_clean = command if command.startswith("/") else f"/{command}"
        param_lines: list[str] = []
        schema_props: dict[str, Any] = {}
        schema_required: list[str] = []

        for fact in active_facts[:6]:
            fkey = str(fact.get("fact_key") or "").strip()
            fval = str(fact.get("fact_value") or "").strip()
            if not fkey or not fval:
                continue
            safe_val, _ = MemoryPolicyGuard.sanitize_text(fval)
            param_lines.append(f"- `{fkey}`: `{{{{{fkey}:{safe_val}}}}}`")
            schema_props[fkey] = {"type": "string", "default": safe_val}
            schema_required.append(fkey)

        step_lines: list[str] = []
        for idx, tool_item in enumerate(effective_tools[:8], 1):
            tname = tool_item["tool"]
            tcomp = tool_item.get("compressed") or ""
            args_preview = json.dumps(tool_item.get("args") or {}, ensure_ascii=False)[:140]
            step_lines.append(f"{idx}. 调用 `{tname}`（参考参数: `{args_preview}`）{f' — {tcomp}' if tcomp else ''}")

        if not step_lines:
            step_lines.append("1. 检查当前工作区上下文与已激活参数契约，定位目标输入与输出目录。")
            step_lines.append("2. 按任务目标执行必要工具验证并校验产物完整性。")

        now_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        skill_md_parts = [
            "---",
            f"name: {skill_id}",
            f"command: {cmd_clean}",
            "version: 1.0.0",
            f"updated_at: {now_day}",
            "type: skill",
            f"description: {description}",
            "---",
            "",
            f"# {skill_id} 自动化操作技能",
            "",
            "## 1. 适用场景与核心目标",
            f"- **沉淀来源目标**: {goal or description}",
            "",
        ]
        if param_lines:
            skill_md_parts.extend([
                "## 2. 推荐输入参数模板（支持工作区契约自动填充）",
                *param_lines,
                "",
                "## 3. 最短有效执行 SOP（已剔除历史试错弯路）",
                *step_lines,
            ])
        else:
            skill_md_parts.extend([
                "## 2. 最短有效执行 SOP（已剔除历史试错弯路）",
                *step_lines,
            ])

        if troubleshooting_notes:
            skill_md_parts.extend([
                "",
                "## Troubleshooting / 避坑注意事项",
                *[f"- {note}" for note in troubleshooting_notes],
            ])

        if artifacts:
            skill_md_parts.extend([
                "",
                "## 交付验收清单",
                *[f"- 验证产出文件: `{a}`" for a in artifacts[:5]],
            ])

        skill_md = _normalize_newlines("\n".join(skill_md_parts).strip() + "\n")

        schema_json: dict[str, Any] | None = None
        if schema_props:
            schema_json = {
                "type": "object",
                "title": f"{skill_id} Output & Parameter Contract",
                "properties": schema_props,
                "required": schema_required[:4],
            }

        playbook_entry = ExperienceDistiller._build_skill_patch_entry(
            title=f"{skill_id} 创世避坑经验",
            trigger_reason=trigger_reason,
            problem=triple.get("problem") or goal or description,
            root_cause=triple.get("root_cause") or "无既有 Skill 可复用，已提炼最短成功工具链",
            solution=triple.get("solution") or "优先沿用本技能 SOP 规程直接命中核心步骤",
            do_steps=step_lines[:4],
        )
        playbook_md = _normalize_newlines(
            "# 实战避坑守则与演进经验台账 (Experience Playbook)\n\n" + playbook_entry + "\n"
        )
        return skill_md, schema_json, playbook_md

    @staticmethod
    async def _has_active_llm_account() -> bool:
        """快速探测当前是否存在可用于后台轻量经验提炼的算力引擎（严格排除重型 CLI）"""
        from ..core.provider_adapter import provider_adapter

        return await provider_adapter.has_any_available_engine(task_role="light")

    async def _try_llm_distill(
        self,
        *,
        trace: ExecutionTrace,
        extracted: dict[str, Any],
        active_facts: list[dict[str, Any]],
        proposal_type: str,
        trigger_reason: str,
        existing_skill_md: str = "",
    ) -> dict[str, Any] | None:
        """尝试调用轻量模型提炼结构化三元组；若无账号/超时/输出非法则返回 None"""
        if not await self._has_active_llm_account():
            return None

        from ..service.engine_pool import engine_pool

        brief_ctx = {
            "goal": trace.user_goal[:400],
            "trigger_reason": trigger_reason,
            "proposal_type": proposal_type,
            "target_skill_ids": trace.active_skill_ids,
            "existing_skill_md_snippet": existing_skill_md[:2400] if existing_skill_md else "",
            "recoveries": extracted["recoveries"][:4],
            "interventions": extracted["interventions"][:4],
            "effective_steps": [
                {"tool": s["tool"], "compressed": s.get("compressed", "")[:120]}
                for s in extracted["effective_tools"][:8]
            ],
            "active_fact_keys": [f.get("fact_key") for f in active_facts[:6]],
            "artifacts": extracted["artifacts"][:5],
        }
        safe_ctx, _ = MemoryPolicyGuard.sanitize_payload(brief_ctx)

        prompt = (
            "你是 AgentHub 经验自演化提炼器。请将以下执行轨迹凝练为严格合法的 JSON 对象（禁止输出多余说明文字）：\n"
            "【反模式红线】：绝对严禁将“找不到输入文件时自行创建 Mock/模拟测试文件进行兜底”提炼为经验或解决方案！\n"
            "字段规范：\n"
            '{"title": "简短中文经验标题(<=24字)", '
            '"summary": "一句话总结沉淀收益(<=60字)", '
            '"problem": "触发问题或任务难点", '
            '"root_cause": "根本原因分析", '
            '"solution": "可复用的标准解决或避坑方案", '
            '"proposed_skill_id": "若为new_skill，建议kebab-case英文ID（如invoice-checker）", '
            '"skill_description": "若为new_skill，一句话功能说明", '
            '"upgraded_sop_body": "若为skill_patch且有existing_skill_md_snippet，可输出保留原核心并融入本次新增要求/步骤的升级版Markdown正文(不含---Frontmatter)", '
            '"steps": ["提炼后的最短有效步骤1", "步骤2"]}\n\n'
            f"执行轨迹数据：\n{json.dumps(safe_ctx, ensure_ascii=False)}"
        )

        async def _consume() -> str:
            out = ""
            async for evt in engine_pool.execute_stream_with_failover(
                conversation_id=trace.conversation_id,
                user_prompt=prompt,
                model_name="",
                thinking_level="low",
                system_override="你是高密度工程经验提炼器，只返回合法 JSON。",
                temperature=0.2,
                pre_built_history=[],
                task_role="light",
            ):
                etype = evt.get("type")
                if etype == "chunk":
                    out += evt.get("content", "")
                elif etype == "done":
                    out = evt.get("full_content", out)
                    break
                elif etype == "error":
                    return ""
            return out

        try:
            raw = await asyncio.wait_for(_consume(), timeout=12.0)
        except Exception as e:
            logger.debug(f"[ExperienceDistiller] 轻模型提炼超时或异常，回退启发式蒸馏: {e}")
            return None

        match = re.search(r"\{[\s\S]*\}", raw or "")
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    async def distill_and_propose(
        self,
        trace: ExecutionTrace,
        *,
        force: bool = False,
        use_llm: bool = True,
    ) -> dict[str, Any] | None:
        """
        执行任务后经验提炼，并写入 experience_proposals 表等待用户确认 Diff。

        Args:
            trace: 任务闭环执行轨迹对象。
            force: 若为 True，跳过触发阈值检查（供 LoopHUD 人工一键沉淀调用）。
            use_llm: 是否优先尝试轻量模型凝练；为 False 时直接使用确定性规则蒸馏。

        Returns:
            dict[str, Any] | None: 落库后的提议对象；不满足阈值或幂等跳过时返回 None。
        """
        if trace.incognito:
            return None

        if not force:
            should_run, reason = self.evaluate_trigger(trace)
            if not should_run:
                return None
        else:
            _, eval_reason = self.evaluate_trigger(trace)
            reason = eval_reason if eval_reason not in ("threshold_not_met", "unsuccessful_or_fused") else "manual_distill"

        # loop_id 幂等防重检查
        if trace.loop_id:
            if not force and trace.loop_id in self._processed_loop_ids:
                return None
            existing_any = await self._get_existing_proposal_by_loop(
                trace.loop_id,
                status_filter="pending" if force else None,
            )
            if existing_any:
                if not force:
                    self._processed_loop_ids.add(trace.loop_id)
                    return None
                return existing_any

        # 1. 拉取 Ledger 轨迹与活跃事实
        ledger_events = await event_ledger.get_trace_for_distillation(
            trace.conversation_id,
            loop_id=trace.loop_id,
            limit=120,
        )
        anti_reason = self._detect_mock_antipattern(trace=trace, ledger_events=ledger_events)
        if anti_reason and not force:
            logger.warning(f"[ExperienceDistiller] 已拦截 Mock 造假反模式轨迹，跳过提案生成: {anti_reason}")
            if trace.loop_id:
                self._processed_loop_ids.add(trace.loop_id)
            return None

        extracted = self._extract_effective_steps(ledger_events)

        # 补充 active_skill_ids 与 produced_artifacts
        combined_skills: list[str] = []
        for s in [*trace.active_skill_ids, *extracted["skill_hints"]]:
            clean_s = str(s or "").strip()
            if clean_s and clean_s not in combined_skills:
                combined_skills.append(clean_s)

        artifacts = list(dict.fromkeys([*trace.produced_artifacts, *extracted["artifacts"]]))
        active_facts = await fact_manager.get_active_facts(trace.workspace_id)

        ws_root = trace.workspace_root
        if not ws_root and trace.workspace_id and trace.workspace_id != "default":
            ws_root = await WorkspaceRepository.get_root_path_by_id(trace.workspace_id)
        elif not ws_root:
            ws_root = await WorkspaceRepository.get_root_path_by_id(trace.workspace_id)

        scratchpad_text = ""
        if ws_root:
            sp_path = Path(ws_root) / ".agenthub" / "scratchpad.md"
            if sp_path.is_file():
                try:
                    scratchpad_text = sp_path.read_text(encoding="utf-8", errors="replace")[:1500]
                except OSError:
                    pass

        proposal_type = "skill_patch" if combined_skills else "new_skill"
        target_skill_id = combined_skills[0] if proposal_type == "skill_patch" else None

        # 解析目标作用域：工作区目录有效时默认落入 workspace 沙盒，否则落入 global
        target_scope = "workspace" if (ws_root and Path(ws_root).is_dir()) else "global"

        # 2. 轻量模型提纯（可选），失败时自动降级确定性启发式结构化组装
        existing_skill_md_for_llm = ""
        if proposal_type == "skill_patch" and target_skill_id:
            try:
                sk_dir_llm, _ = skill_loader.resolve_skill_dir_with_scope(
                    target_skill_id,
                    workspace_root=ws_root,
                )
                if sk_dir_llm and (sk_dir_llm / "SKILL.md").is_file():
                    existing_skill_md_for_llm = (sk_dir_llm / "SKILL.md").read_text(
                        encoding="utf-8", errors="replace"
                    )[:2400]
            except OSError:
                existing_skill_md_for_llm = ""

        llm_data = None
        if use_llm:
            llm_data = await self._try_llm_distill(
                trace=trace,
                extracted=extracted,
                active_facts=active_facts,
                proposal_type=proposal_type,
                trigger_reason=reason,
                existing_skill_md=existing_skill_md_for_llm,
            )

        fallback_goal = scratchpad_text.splitlines()[0] if scratchpad_text else ""
        clean_goal = (trace.user_goal or trace.final_summary or fallback_goal).strip()
        if not clean_goal:
            clean_goal = "自主多步闭环任务执行链路"

        recoveries = extracted["recoveries"]
        interventions = extracted["interventions"]
        effective_tools = extracted["effective_tools"]

        if llm_data:
            title = str(llm_data.get("title") or "").strip()[:60]
            summary = str(llm_data.get("summary") or "").strip()[:180]
            problem = str(llm_data.get("problem") or "").strip()
            root_cause = str(llm_data.get("root_cause") or "").strip()
            solution = str(llm_data.get("solution") or "").strip()
            raw_steps = llm_data.get("steps") if isinstance(llm_data.get("steps"), list) else []
            steps_list = [str(st).strip() for st in raw_steps if str(st).strip()]
            llm_skill_id = str(llm_data.get("proposed_skill_id") or "").strip()
            llm_desc = str(llm_data.get("skill_description") or "").strip()
            llm_upgraded_body = str(llm_data.get("upgraded_sop_body") or "").strip()
        else:
            title = ""
            summary = ""
            problem = ""
            root_cause = ""
            solution = ""
            steps_list = []
            llm_skill_id = ""
            llm_desc = ""
            llm_upgraded_body = ""

        # 确定性启发式补全缺省字段
        if not problem:
            if recoveries:
                r0 = recoveries[0]
                problem = f"执行 `{r0['failed_tool']}` 时出现异常：{r0['failed_summary'] or '命令返回非零错误'}"
            elif interventions:
                problem = f"执行过程中出现人工干预纠正：{interventions[0][:120]}"
            else:
                problem = f"人工/多步从零完成目标「{clean_goal[:80]}」（共 {max(trace.total_tool_steps, len(effective_tools))} 步，无既有 Skill 复用）"

        if not root_cause:
            if recoveries:
                root_cause = "初始工具调用参数、环境命令格式或边界校验与当前工作区上下文不兼容"
            elif interventions:
                root_cause = "自动化初始策略与用户期望约束或高危审批规则存在偏差"
            else:
                root_cause = "该场景尚无标准化四元组 SOP，每次需重新摸索工具序列与确认参数"

        if not solution:
            if recoveries:
                r0 = recoveries[0]
                solution = f"改为通过 `{r0['recovery_tool']}` 处理（{r0['recovery_compressed'] or '验证成功路径'}），规避原报错方式"
            elif interventions:
                solution = f"遵循人工修正规范：{interventions[-1][:140]}"
            else:
                solution = f"固化最短成功工具链（{len(effective_tools)} 步有效动作）与工作区契约模板，后续输入命令一键复用"

        if not steps_list:
            for item in effective_tools[:6]:
                desc_line = item.get("compressed") or f"调用 {item['tool']}"
                steps_list.append(f"`{item['tool']}`: {desc_line}")

        if not title:
            if proposal_type == "skill_patch":
                title = f"更新 `{target_skill_id}` 实战避坑经验"
            else:
                title = "沉淀为标准可复用自动化 Skill"

        if not summary:
            summary = (
                f"基于 {len(recoveries)} 次自愈修复与 {len(effective_tools)} 步有效工具轨迹凝练，"
                f"经确认后将{'迭代升级 Skill 版本并同步更新 experience_playbook.md' if proposal_type == 'skill_patch' else '创建标准化四元组 Skill'}。"
            )

        # 全量隐私网关过滤，绝不允许敏感凭据/绝对私钥落入提议或 Skill
        title, _ = MemoryPolicyGuard.sanitize_text(title)
        summary, _ = MemoryPolicyGuard.sanitize_text(summary)
        problem, _ = MemoryPolicyGuard.sanitize_text(problem)
        root_cause, _ = MemoryPolicyGuard.sanitize_text(root_cause)
        solution, _ = MemoryPolicyGuard.sanitize_text(solution)
        steps_list = [MemoryPolicyGuard.sanitize_text(s)[0] for s in steps_list]

        triple = {
            "problem": problem,
            "root_cause": root_cause,
            "solution": solution,
        }

        anti_reason_post = self._detect_mock_antipattern(
            trace=trace,
            ledger_events=ledger_events,
            llm_data=llm_data,
            triple=triple,
        )
        if anti_reason_post and not force:
            logger.warning(f"[ExperienceDistiller] 已拦截含 Mock 造假建议的提案生成: {anti_reason_post}")
            if trace.loop_id:
                self._processed_loop_ids.add(trace.loop_id)
            return None

        raw_slug = llm_skill_id or clean_goal[:36]
        alt_skill_name = _normalize_kebab_id(
            raw_slug,
            fallback=f"workflow-{uuid.uuid4().hex[:6]}",
        )
        if alt_skill_name.startswith("workflow-") and len(clean_goal) > 0:
            ascii_tokens = re.findall(r"[a-zA-Z0-9]{2,}", clean_goal)
            if ascii_tokens:
                alt_skill_name = _normalize_kebab_id("-".join(ascii_tokens[:3]))
        alt_command = f"/{alt_skill_name}"
        alt_desc = MemoryPolicyGuard.sanitize_text(
            llm_desc or f"自动沉淀技能：{clean_goal[:70]}"
        )[0]
        troubleshooting_notes = _build_troubleshooting_bullets(recoveries)
        alt_skill_md, alt_schema_json, alt_playbook_md = self._build_new_skill_files(
            skill_id=alt_skill_name,
            command=alt_command,
            description=alt_desc,
            goal=clean_goal[:140],
            triple=triple,
            effective_tools=effective_tools,
            active_facts=active_facts,
            artifacts=artifacts,
            trigger_reason=reason,
            troubleshooting_notes=troubleshooting_notes,
        )
        alt_skill_md, _ = MemoryPolicyGuard.sanitize_text(alt_skill_md)
        alt_playbook_md, _ = MemoryPolicyGuard.sanitize_text(alt_playbook_md)

        distill_context = {
            "title": title,
            "goal": clean_goal[:140],
            "triple": triple,
            "steps": steps_list,
            "artifacts": artifacts,
            "troubleshooting_notes": troubleshooting_notes,
            "trigger_reason": reason,
        }

        if proposal_type == "skill_patch" and target_skill_id:
            proposed_skill_name = target_skill_id
            bump_lvl = determine_semver_bump_level(
                has_schema_change=bool(alt_schema_json),
                do_steps=steps_list,
                has_new_script=False,
                has_new_artifacts=bool(artifacts),
                force_patch_only=bool(
                    not steps_list and not artifacts and (recoveries or troubleshooting_notes)
                ),
            )
            old_skill_md, upgraded_skill_md, current_ver, next_ver, target_cmd = self.build_upgraded_skill_md(
                skill_id=target_skill_id,
                workspace_root=ws_root,
                title=title,
                goal=clean_goal[:140],
                problem=problem,
                root_cause=root_cause,
                solution=solution,
                do_steps=steps_list,
                llm_upgraded_body=llm_upgraded_body,
                troubleshooting_notes=troubleshooting_notes,
                bump_level=bump_lvl,
            )
            markdown_entry = self._build_skill_patch_entry(
                title=title,
                trigger_reason=reason,
                problem=problem,
                root_cause=root_cause,
                solution=solution,
                do_steps=steps_list,
                version_transition=f"v{current_ver} → v{next_ver}",
            )
            markdown_entry, _ = MemoryPolicyGuard.sanitize_text(markdown_entry)
            diff_preview = _build_unified_diff(
                old_skill_md,
                upgraded_skill_md,
                f"skills/{target_skill_id}/SKILL.md",
            )
            payload_obj: dict[str, Any] = {
                "update_mode": "upgrade_skill_md",
                "target_file": "SKILL.md",
                "target_skill_id": target_skill_id,
                "command": target_cmd,
                "current_version": current_ver,
                "next_version": next_ver,
                "skill_md": upgraded_skill_md,
                "markdown_entry": markdown_entry,
                "candidate_skill_ids": combined_skills,
                "new_skill_id": alt_skill_name,
                "new_command": alt_command,
                "new_description": alt_desc,
                "new_skill_md": alt_skill_md,
                "new_schema_json": alt_schema_json,
                "new_playbook_md": alt_playbook_md,
                "distill_context": distill_context,
            }
        else:
            proposed_skill_name = alt_skill_name
            diff_preview = _build_unified_diff(
                "",
                alt_skill_md,
                f"skills/{proposed_skill_name}/SKILL.md",
            )
            payload_obj = {
                "update_mode": "create_new",
                "skill_id": proposed_skill_name,
                "command": alt_command,
                "version": "1.0.0",
                "current_version": "0.0.0",
                "next_version": "1.0.0",
                "description": alt_desc,
                "skill_md": alt_skill_md,
                "schema_json": alt_schema_json,
                "playbook_md": alt_playbook_md,
                "candidate_skill_ids": combined_skills,
                "new_skill_id": proposed_skill_name,
                "new_command": alt_command,
                "new_description": alt_desc,
                "new_skill_md": alt_skill_md,
                "new_schema_json": alt_schema_json,
                "new_playbook_md": alt_playbook_md,
                "distill_context": distill_context,
            }

        proposal_id = f"exp_{uuid.uuid4().hex[:12]}"
        now_ts = datetime.now(timezone.utc).isoformat()
        clean_ws = (trace.workspace_id or "default").strip() or "default"

        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO experience_proposals (
                    id, workspace_id, conversation_id, loop_id,
                    proposal_type, target_skill_id, proposed_skill_name,
                    target_scope, trigger_reason, title, summary,
                    experience_triple_json, diff_preview, payload_json,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    proposal_id,
                    clean_ws,
                    trace.conversation_id,
                    trace.loop_id,
                    proposal_type,
                    target_skill_id,
                    proposed_skill_name,
                    target_scope,
                    reason,
                    title,
                    summary,
                    json.dumps(triple, ensure_ascii=False),
                    diff_preview,
                    json.dumps(payload_obj, ensure_ascii=False),
                    now_ts,
                ),
            )
            await conn.commit()
            async with conn.execute(
                "SELECT * FROM experience_proposals WHERE id = ?",
                (proposal_id,),
            ) as cur:
                saved_row = await cur.fetchone()

        if trace.loop_id:
            self._processed_loop_ids.add(trace.loop_id)

        logger.info(
            f"[ExperienceDistiller] 已生成经验提议 {proposal_id} "
            f"(type={proposal_type}, skill={proposed_skill_name}, scope={target_scope}, reason={reason})"
        )
        return _serialize_proposal_row(saved_row)

    async def on_task_complete(self, trace: ExecutionTrace) -> dict[str, Any] | None:
        """
        统一的任务完成自动经验提炼钩子（以 loop_id 为键保证跨调用方幂等）。
        自动触发时使用确定性规则提取（use_llm=False），零后台 Token 消耗；仅用户手动点击沉淀时才调用 LLM。
        """
        try:
            return await self.distill_and_propose(trace, force=False, use_llm=False)
        except Exception as e:
            logger.warning(f"[ExperienceDistiller] 任务后经验提炼异常（不影响主交付）: {e}")
            return None

    async def _invoke_light_json(
        self,
        *,
        conversation_id: str,
        system_prompt: str,
        user_prompt: str,
        timeout: float = 15.0,
    ) -> dict[str, Any] | None:
        """统一轻量模型 JSON 调用封装（自动检测轻算力池并在超时/故障时优雅降级返回 None）。"""
        if not await self._has_active_llm_account():
            return None

        from ..service.engine_pool import engine_pool

        async def _consume() -> str:
            out = ""
            async for evt in engine_pool.execute_stream_with_failover(
                conversation_id=conversation_id,
                user_prompt=user_prompt,
                model_name="",
                thinking_level="low",
                system_override=system_prompt,
                temperature=0.2,
                pre_built_history=[],
                task_role="light",
            ):
                etype = evt.get("type")
                if etype == "chunk":
                    out += str(evt.get("content") or "")
                elif etype == "done":
                    out = str(evt.get("full_content") or out)
                    break
                elif etype == "error":
                    return ""
            return out

        try:
            raw = await asyncio.wait_for(_consume(), timeout=timeout)
        except (asyncio.TimeoutError, Exception) as exc:
            logger.debug("[ExperienceDistiller] _invoke_light_json 超时或异常，回退启发式提取: %s", exc)
            return None

        match = re.search(r"\{[\s\S]*\}", raw or "")
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None

    @staticmethod
    def _build_heuristic_micro_fields(
        trace: ExecutionTrace,
        extracted: dict[str, Any],
    ) -> dict[str, Any]:
        """不依赖 LLM 的确定性三路正交微经验字段构建器。"""
        effective_tools: list[dict[str, Any]] = extracted.get("effective_tools", [])
        recoveries: list[dict[str, str]] = extracted.get("recoveries", [])
        interventions: list[str] = extracted.get("interventions", [])
        skill_hints: list[str] = list(dict.fromkeys([*(trace.active_skill_ids or []), *(extracted.get("skill_hints") or [])]))

        # 1. 提取实体标签（文件名、工具名、命令行首词、技能名）
        entities: list[str] = []
        for s_id in skill_hints[:3]:
            if s_id:
                entities.append(s_id)
        for step in effective_tools[-6:]:
            t_name = str(step.get("tool") or "").strip()
            args = step.get("args") if isinstance(step.get("args"), dict) else {}
            for key in ("file_path", "path", "target_file", "script"):
                fp = str(args.get(key) or "").strip()
                if fp:
                    entities.append(Path(fp).name)
            cmd = str(args.get("command") or args.get("cmd") or "").strip()
            if cmd:
                head = cmd.split()[0]
                if head:
                    entities.append(Path(head).name)
            if t_name and t_name not in ("execute_local_command",):
                entities.append(t_name)

        # 去重保序
        uniq_entities = list(dict.fromkeys([e for e in entities if e]))[:8]

        # 2. 提取核心错误签名
        error_signature = ""
        if recoveries:
            failed_txt = str(recoveries[0].get("failed_summary") or "").strip()
            sig_match = re.search(r"([A-Za-z_][\w.]*(?:Error|Exception|Warning)[:：][^\n]{1,90})", failed_txt)
            error_signature = (sig_match.group(1) if sig_match else failed_txt)[:140]

        # 3. 提取症状同义词
        symptoms: list[str] = []
        if trace.user_goal:
            symptoms.extend(re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9_.-]{3,24}", trace.user_goal)[:6])
        if error_signature:
            symptoms.extend(re.findall(r"[\u4e00-\u9fff]{2,8}", error_signature)[:3])
        if interventions:
            symptoms.extend(re.findall(r"[\u4e00-\u9fff]{2,8}", interventions[0])[:4])
        uniq_symptoms = list(dict.fromkeys(symptoms))[:10]

        # 4. 组装场景、根因与方案
        scene_summary = (trace.user_goal or trace.final_summary or "实战任务异常修复与避坑").strip()[:60]
        if recoveries:
            root_cause = str(recoveries[0].get("failed_summary") or "执行工具异常中断").strip()[:160]
            rec_tool = str(recoveries[0].get("recovery_tool") or "").strip()
            rec_comp = str(recoveries[0].get("recovery_compressed") or "").strip()
            verified_solution = (
                f"改用/调整 `{rec_tool}` 修复成功：{rec_comp}" if rec_comp else f"使用 `{rec_tool}` 重试并验证通过"
            )
            if interventions:
                verified_solution += f"；人工约束：{interventions[0][:120]}"
        elif interventions:
            root_cause = "初始执行路径偏离用户真实约束"
            verified_solution = "严格遵守人工纠偏约束：" + "；".join(interventions[:2])[:260]
        else:
            root_cause = "多步骤复杂任务需要固定有序调用链路"
            verified_solution = (trace.final_summary or "按有效工具序列分阶段验证交付")[:260]

        effective_cmds = [
            f"{s['tool']}: {str(s.get('compressed') or '')[:80]}".strip(": ")
            for s in effective_tools[-5:]
        ]

        task_category = "bug_fix" if recoveries else ("user_rule" if interventions else "workflow")
        origin_type = "user_correction" if len(interventions) > 0 or trace.had_user_intervention else "auto_recover"

        return {
            "task_category": task_category,
            "entity_tags": ", ".join(uniq_entities),
            "error_signature": error_signature,
            "symptom_keywords": ", ".join(uniq_symptoms),
            "scene_summary": scene_summary,
            "root_cause": root_cause,
            "verified_solution": verified_solution,
            "effective_commands": effective_cmds,
            "origin_type": origin_type,
        }

    async def distill_micro_case_silently(
        self,
        trace: ExecutionTrace,
        *,
        turn_id: int | None = None,
    ) -> ExperienceCase | None:
        """盘后静默萃取 L5 实战微经验（默认零侵入且支持无轻模型下的纯规则降级）。"""
        if trace.incognito or trace.scene_mode in ("oneshot", "plan_first"):
            return None
        if not trace.final_success or trace.circuit_breaker_triggered:
            return None

        ledger_events = await event_ledger.get_trace_for_distillation(
            trace.conversation_id,
            loop_id=trace.loop_id,
            turn_id=turn_id,
            limit=120,
        )
        if not ledger_events and turn_id is not None:
            ledger_events = await event_ledger.get_trace_for_distillation(
                trace.conversation_id,
                loop_id=trace.loop_id,
                turn_id=None,
                limit=80,
            )

        if self._detect_mock_antipattern(trace=trace, ledger_events=ledger_events):
            logger.info("[ExperienceDistiller] 轨迹命中造假反模式红线，静默跳过 L5 沉淀")
            return None

        extracted = self._extract_effective_steps(ledger_events)
        recovered_cnt = max(trace.recovered_error_count, len(extracted["recoveries"]))
        intervene_cnt = (1 if trace.had_user_intervention else 0) + len(extracted["interventions"])
        effective_tools = extracted["effective_tools"]
        skill_hints = trace.active_skill_ids or extracted["skill_hints"]

        # 水位控制：无报错自愈、无人工纠偏且少于 4 步有效复杂调用的普通问答直接跳过
        if recovered_cnt < 1 and intervene_cnt < 1 and not (not skill_hints and len(effective_tools) >= 4):
            return None

        fields = self._build_heuristic_micro_fields(trace, extracted)

        # 若轻算力池可用，尝试用 LLM 精炼泛化三路正交锚点
        safe_brief, _ = MemoryPolicyGuard.sanitize_payload(
            {
                "goal": trace.user_goal[:300],
                "summary": trace.final_summary[:300],
                "recoveries": extracted["recoveries"][:3],
                "interventions": extracted["interventions"][:3],
                "steps": fields["effective_commands"],
            }
        )
        llm_prompt = (
            "请从下列已验证成功的执行轨迹中提纯一条可复用的微经验 JSON（严禁输出 Markdown 以外的解释）：\n"
            '{"task_category": "bug_fix|env_setup|data_audit|build_test|tool_usage", '
            '"entity_tags": "逗号分隔的文件名/库名/命令/模块硬锚点", '
            '"error_signature": "精准报错类名或关键错误码(无报错留空)", '
            '"symptom_keywords": "5~8个口语化同义症状词，逗号分隔", '
            '"scene_summary": "一句话场景描述(<=60字)", '
            '"root_cause": "一句话核心根因(<=120字)", '
            '"verified_solution": "已验证的最短避坑解法与关键命令", '
            '"effective_commands": ["步骤1", "步骤2"]}\n\n'
            f"轨迹摘要：\n{json.dumps(safe_brief, ensure_ascii=False)}"
        )
        llm_data = await self._invoke_light_json(
            conversation_id=trace.conversation_id,
            system_prompt="你是精准的 L5 实战微经验萃取器，仅输出严格 JSON。",
            user_prompt=llm_prompt,
            timeout=12.0,
        )
        if llm_data and not self._detect_mock_antipattern(
            trace=trace, ledger_events=ledger_events, llm_data=llm_data
        ):
            fields["task_category"] = str(llm_data.get("task_category") or fields["task_category"])
            fields["entity_tags"] = str(llm_data.get("entity_tags") or fields["entity_tags"])
            fields["error_signature"] = str(llm_data.get("error_signature") or fields["error_signature"])
            fields["symptom_keywords"] = str(llm_data.get("symptom_keywords") or fields["symptom_keywords"])
            fields["scene_summary"] = str(llm_data.get("scene_summary") or fields["scene_summary"])
            fields["root_cause"] = str(llm_data.get("root_cause") or fields["root_cause"])
            fields["verified_solution"] = str(llm_data.get("verified_solution") or fields["verified_solution"])
            if isinstance(llm_data.get("effective_commands"), list):
                fields["effective_commands"] = [str(c) for c in llm_data["effective_commands"]][:8]

        saved_case = await episodic_experience_store.upsert_case(
            workspace_id=trace.workspace_id or "GLOBAL",
            task_category=fields["task_category"],
            entity_tags=fields["entity_tags"],
            error_signature=fields["error_signature"],
            symptom_keywords=fields["symptom_keywords"],
            scene_summary=fields["scene_summary"],
            root_cause=fields["root_cause"],
            verified_solution=fields["verified_solution"],
            effective_commands=fields["effective_commands"],
            origin_type=fields["origin_type"],
            source_conversation_id=trace.conversation_id,
        )

        # 若绑定了工作区私有 Skill，将避坑记录增量追加至该 Skill 的 experience_playbook.md
        if skill_hints and trace.workspace_root:
            primary_skill = skill_hints[0]
            try:
                sk_dir, scope = skill_loader.resolve_skill_dir_with_scope(
                    primary_skill, workspace_root=trace.workspace_root
                )
                if sk_dir and scope == "workspace":
                    kb_dir = sk_dir / "knowledge"
                    kb_dir.mkdir(parents=True, exist_ok=True)
                    pb_file = kb_dir / "experience_playbook.md"
                    safe_sol, _ = MemoryPolicyGuard.sanitize_text(fields["verified_solution"])
                    safe_scene, _ = MemoryPolicyGuard.sanitize_text(fields["scene_summary"])
                    entry = (
                        f"\n## [{datetime.now(timezone.utc).strftime('%Y-%m-%d')}] {safe_scene}\n"
                        f"- **根因**：{fields['root_cause']}\n"
                        f"- **避坑方案**：{safe_sol}\n"
                    )
                    existing = pb_file.read_text(encoding="utf-8", errors="replace") if pb_file.exists() else "# 实战避坑手册 (Experience Playbook)\n"
                    if safe_sol[:40] not in existing:
                        pb_file.write_text(existing.rstrip() + "\n" + entry, encoding="utf-8")
                        skill_loader.invalidate_cache()
                        invalidate_skill_file_cache()
            except OSError as exc:
                logger.warning("[ExperienceDistiller] 追加 Skill playbook 失败: %s", exc)

        logger.info(
            "[ExperienceDistiller] 静默沉淀 L5 微经验成功: id=%s origin=%s ws=%s",
            saved_case.id,
            saved_case.origin_type,
            saved_case.workspace_id,
        )
        return saved_case

    @staticmethod
    def _build_safe_default_pipeline_py(skill_id: str, description: str) -> str:
        """构建默认幂等安全的三阶段 Gate Skill 执行脚手架（确保 100% 通过 AST 与 SkillAuditor 门禁）。"""
        safe_desc = description.replace('"', "'")[:120]
        return (
            '"""'
            f'{skill_id} - 幂等三阶段流水线脚本 (extract -> validate -> deliver)\n'
            f'说明: {safe_desc}\n'
            '"""\n\n'
            "from __future__ import annotations\n\n"
            "import argparse\n"
            "import json\n"
            "from pathlib import Path\n"
            "from typing import Any\n\n\n"
            "def extract(workspace: Path) -> dict[str, Any]:\n"
            '    """Phase 1: 从工作区只读采集输入清单与元信息。"""\n'
            '    return {"workspace": str(workspace), "exists": workspace.exists(), "files": []}\n\n\n'
            "def validate(payload: dict[str, Any]) -> tuple[bool, list[str]]:\n"
            '    """Phase 2: 校验结构边界与必备条件。"""\n'
            "    errors: list[str] = []\n"
            '    if not payload.get("exists"):\n'
            '        errors.append("工作区目录不存在")\n'
            "    return len(errors) == 0, errors\n\n\n"
            "def deliver(payload: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:\n"
            '    """Phase 3: 幂等输出交付报告。"""\n'
            '    return {"status": "dry_run" if dry_run else "ok", "summary": payload}\n\n\n'
            "def main() -> int:\n"
            '    parser = argparse.ArgumentParser(description="Three-stage idempotent skill pipeline")\n'
            '    parser.add_argument("--workspace", default=".", help="目标工作区路径")\n'
            '    parser.add_argument("--dry-run", action="store_true", help="仅预演不写入最终产物")\n'
            "    args = parser.parse_args()\n\n"
            "    ws = Path(args.workspace).resolve()\n"
            "    extracted = extract(ws)\n"
            "    ok, errs = validate(extracted)\n"
            "    if not ok:\n"
            '        print(json.dumps({"ok": False, "errors": errs}, ensure_ascii=False))\n'
            "        return 1\n"
            "    res = deliver(extracted, dry_run=args.dry_run)\n"
            "    print(json.dumps(res, ensure_ascii=False, indent=2))\n"
            "    return 0\n\n\n"
            'if __name__ == "__main__":\n'
            "    raise SystemExit(main())\n"
        )

    async def dual_distill_skill_and_memory(
        self,
        *,
        skill_name: str,
        conversation_id: str,
        workspace_id: str,
        workspace_root: str,
        user_instruction: str = "",
    ) -> dict[str, Any]:
        """响应 `/distill` 执行正交双提纯：
        1. 通用三阶段 SOP 规范 (`SKILL.md`) 与安全脚手架 (`scripts/pipeline.py`) -> 工作区 `.agenthub/skills/<id>/`；
        2. 私有业务规则与路径契约 -> L4 `FactManager`；
        3. 踩坑避坑微经验 -> L5 `EpisodicExperienceStore`。
        """
        kebab_id = _normalize_kebab_id(skill_name, fallback="distilled-workflow")
        ws_path = Path(workspace_root).resolve()
        ledger_events = await event_ledger.get_trace_for_distillation(conversation_id, limit=120)

        dummy_trace = ExecutionTrace(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            user_goal=user_instruction or f"提纯技能 {kebab_id}",
            workspace_root=str(ws_path),
        )
        anti_reason = self._detect_mock_antipattern(trace=dummy_trace, ledger_events=ledger_events)
        if anti_reason:
            return {"ok": False, "error": f"安全门禁拒绝提纯：{anti_reason}"}

        extracted = self._extract_effective_steps(ledger_events)
        heuristic = self._build_heuristic_micro_fields(dummy_trace, extracted)

        # 构造正交默认产出
        skill_desc = (user_instruction or heuristic["scene_summary"] or f"{kebab_id} 标准工作流").strip()[:120]
        steps: list[str] = [
            "Phase 1 (Extract): 先只读盘点输入上下文与目标文件边界，严禁篡改原始输入或构造模拟数据兜底",
            "Phase 2 (Validate): 校验参数契约、路径规范与前置依赖状态",
            "Phase 3 (Deliver): 执行幂等生成或变更，完成后校验输出产物",
        ]
        if heuristic["effective_commands"]:
            steps.extend([f"参考实操命令：{cmd}" for cmd in heuristic["effective_commands"][:3]])

        fact_rules: list[dict[str, str]] = []
        if extracted["interventions"]:
            fact_rules.append(
                {
                    "fact_key": f"skill.{kebab_id}.constraint",
                    "fact_value": str(extracted["interventions"][0])[:200],
                }
            )
        if user_instruction:
            fact_rules.append(
                {
                    "fact_key": f"skill.{kebab_id}.scope_rule",
                    "fact_value": user_instruction[:200],
                }
            )

        micro_cases: list[dict[str, Any]] = []
        if heuristic["error_signature"] or extracted["recoveries"]:
            micro_cases.append(heuristic)

        pipeline_code = self._build_safe_default_pipeline_py(kebab_id, skill_desc)

        # 若轻算力模型可用，尝试正交三路智能分离
        safe_events, _ = MemoryPolicyGuard.sanitize_payload(
            {
                "skill_id": kebab_id,
                "user_instruction": user_instruction,
                "recoveries": extracted["recoveries"][:4],
                "interventions": extracted["interventions"][:4],
                "steps": heuristic["effective_commands"],
            }
        )
        llm_prompt = (
            "请将以下执行上下文严格执行【正交双提纯】并输出 JSON（禁止输出多余说明文字）：\n"
            "【正交原则】：通用流程进 skill_steps；私有路径/团队契约进 fact_rules；报错避坑进 micro_cases。\n"
            "【安全红线】：pipeline_py 禁止调用 os.remove/shutil.rmtree/subprocess/eval/exec。\n"
            'JSON 结构：\n'
            '{"skill_description": "一句话技能描述", '
            '"skill_steps": ["Phase 1...", "Phase 2...", "Phase 3..."], '
            '"pipeline_py": "可选的合法 Python 三阶段脚本(仅用标准库且幂等)", '
            '"fact_rules": [{"fact_key": "英文点分键名", "fact_value": "业务规则契约"}], '
            '"micro_cases": [{"task_category": "bug_fix|tool_usage", "entity_tags": "...", '
            '"error_signature": "...", "symptom_keywords": "...", "scene_summary": "...", '
            '"root_cause": "...", "verified_solution": "..."}]}\n\n'
            f"上下文：\n{json.dumps(safe_events, ensure_ascii=False)}"
        )
        llm_res = await self._invoke_light_json(
            conversation_id=conversation_id,
            system_prompt="你是正交知识工程提纯引擎，只返回合法 JSON。",
            user_prompt=llm_prompt,
            timeout=15.0,
        )
        if llm_res:
            if llm_res.get("skill_description"):
                skill_desc = _sanitize_text_val(str(llm_res["skill_description"]))[:160]
            if isinstance(llm_res.get("skill_steps"), list) and llm_res["skill_steps"]:
                steps = [_sanitize_text_val(str(s)) for s in llm_res["skill_steps"] if str(s).strip()][:8]
            if isinstance(llm_res.get("fact_rules"), list):
                for fr in llm_res["fact_rules"]:
                    if isinstance(fr, dict) and fr.get("fact_key") and fr.get("fact_value"):
                        fact_rules.append(
                            {
                                "fact_key": str(fr["fact_key"]).strip()[:80],
                                "fact_value": str(fr["fact_value"]).strip()[:300],
                            }
                        )
            if isinstance(llm_res.get("micro_cases"), list):
                for mc in llm_res["micro_cases"]:
                    if isinstance(mc, dict) and mc.get("verified_solution"):
                        micro_cases.append(mc)
            candidate_py = str(llm_res.get("pipeline_py") or "").strip()
            if candidate_py and len(candidate_py) > 40:
                syn_ok, _ = validate_syntax_before_write(".py", candidate_py)
                if syn_ok:
                    pipeline_code = candidate_py

        # 1. 检测工作区或全局是否已存在同名 Skill，支持版本自适应迭代与既定资产完整保留
        existing_dir, existing_scope = skill_loader.resolve_skill_dir_with_scope(
            kebab_id,
            workspace_root=str(ws_path),
        )
        target_skill_dir = (
            existing_dir
            if (existing_dir and existing_scope == "workspace")
            else (ws_path / ".agenthub" / "skills" / kebab_id)
        )
        is_upgrade = bool(existing_dir and (existing_dir / "SKILL.md").is_file())
        troubleshooting_notes = _build_troubleshooting_bullets(extracted["recoveries"])

        safe_desc, _ = MemoryPolicyGuard.sanitize_text(skill_desc)
        steps_md = "\n".join(f"{idx}. {MemoryPolicyGuard.sanitize_text(step)[0]}" for idx, step in enumerate(steps, 1))

        # 判断语义化版本递增粒度：既有 Skill 无新实操命令且仅追加避坑/上下文时升 patch，否则升 minor
        existing_version = "1.0.0"
        if is_upgrade and existing_dir:
            try:
                old_raw = (existing_dir / "SKILL.md").read_text(encoding="utf-8", errors="replace")
                old_meta = skill_loader._parse_frontmatter(old_raw, kebab_id)
                existing_version = normalize_version_str(old_meta.get("version"))
            except OSError:
                existing_version = "1.0.0"
            has_new_action_steps = bool(heuristic["effective_commands"]) or bool(llm_res and llm_res.get("pipeline_py"))
            bump_lvl = determine_semver_bump_level(
                has_schema_change=False,
                do_steps=heuristic["effective_commands"] if has_new_action_steps else [],
                has_new_script=bool(llm_res and llm_res.get("pipeline_py")),
                force_patch_only=not has_new_action_steps,
            )
            skill_version = bump_semver(existing_version, level=bump_lvl)
        else:
            skill_version = "1.0.0"

        now_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        skill_md_sections = [
            "---",
            f"name: {kebab_id}",
            f"description: {safe_desc}",
            f"command: /{kebab_id}",
            f"version: {skill_version}",
            f"updated_at: {now_day}",
            "---",
            "",
            f"# {kebab_id} 标准作业规范",
            "",
            "## 执行门控流程 (Three-Stage Gate)",
            "",
            steps_md,
            "",
        ]
        if troubleshooting_notes:
            skill_md_sections.extend(
                [
                    "## Troubleshooting / 避坑注意事项",
                    "",
                    *[f"- {note}" for note in troubleshooting_notes],
                    "",
                ]
            )
        skill_md_sections.extend(
            [
                "## 可执行流水线脚手架",
                "",
                "- 脚手架脚本：`scripts/pipeline.py`（支持 `--workspace` 与 `--dry-run` 参数）",
            ]
        )
        skill_md_text = _normalize_newlines("\n".join(skill_md_sections).strip() + "\n")

        # 确保 pipeline_code 语法绝对正确，否则自动退回确定性安全模板
        syn_ok, _ = validate_syntax_before_write(".py", pipeline_code)
        if not syn_ok:
            pipeline_code = self._build_safe_default_pipeline_py(kebab_id, safe_desc)

        staging_dir = ws_path / ".agenthub" / ".staging_skills" / f"{kebab_id}_{uuid.uuid4().hex[:6]}"
        try:
            if existing_dir and existing_dir.is_dir():
                # 完整保留原目录下的 knowledge/ 自定义文件、脚本及其他附属资源
                shutil.copytree(
                    existing_dir,
                    staging_dir,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules"),
                )
            (staging_dir / "scripts").mkdir(parents=True, exist_ok=True)
            (staging_dir / "SKILL.md").write_text(skill_md_text, encoding="utf-8")

            pipeline_target = staging_dir / "scripts" / "pipeline.py"
            if not pipeline_target.exists() or bool(llm_res and llm_res.get("pipeline_py")):
                pipeline_target.write_text(pipeline_code, encoding="utf-8")

            if troubleshooting_notes or heuristic["error_signature"] or extracted["recoveries"]:
                pb_entry = self._build_skill_patch_entry(
                    title=f"{kebab_id} /distill 经验增量归档",
                    trigger_reason="manual_distill",
                    problem=heuristic["error_signature"] or safe_desc,
                    root_cause=heuristic["root_cause"] or "执行过程存在可复用约束或避坑节点",
                    solution="; ".join(troubleshooting_notes) if troubleshooting_notes else heuristic["verified_solution"],
                    do_steps=steps[:5],
                    version_transition=(f"v{existing_version} → v{skill_version}" if is_upgrade else "v1.0.0"),
                )
                pb_clean, _ = MemoryPolicyGuard.sanitize_text(pb_entry)
                _append_dedup_playbook_entry(staging_dir / "knowledge" / "experience_playbook.md", pb_clean)

            audit_res = audit_skill_package(staging_dir)
            if not audit_res.get("passed", False):
                # 若是 LLM 生成的脚本触碰了敏感 AST 黑名单，替换为安全标准脚手架再审
                fallback_py = self._build_safe_default_pipeline_py(kebab_id, safe_desc)
                pipeline_target.write_text(fallback_py, encoding="utf-8")
                audit_res = audit_skill_package(staging_dir)
                if not audit_res.get("passed", False):
                    return {
                        "ok": False,
                        "error": f"Skill 安全审计未通过: {audit_res.get('summary')}",
                    }

            _atomic_replace_skill_dir(staging_dir, target_skill_dir)
        finally:
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)

        # 2. 写入 L4 私有事实规则（严格对齐 FactManager.upsert_fact 异步契约）
        facts_saved = 0
        for fr in fact_rules[:6]:
            fk, _ = MemoryPolicyGuard.sanitize_text(fr["fact_key"])
            fv, _ = MemoryPolicyGuard.sanitize_text(fr["fact_value"])
            if not fk or not fv:
                continue
            await fact_manager.upsert_fact(
                fact_key=fk[:80],
                fact_value=fv[:300],
                workspace_id=workspace_id or "GLOBAL",
                category="biz_rule",
                source_type="explicit_cmd",
                verified=True,
            )
            facts_saved += 1

        # 3. 写入 L5 实战微经验案例
        cases_saved = 0
        for mc in micro_cases[:4]:
            if self._detect_mock_antipattern(trace=dummy_trace, ledger_events=[], llm_data=mc):
                continue
            await episodic_experience_store.upsert_case(
                workspace_id=workspace_id or "GLOBAL",
                task_category=str(mc.get("task_category") or "tool_usage"),
                entity_tags=str(mc.get("entity_tags") or kebab_id),
                error_signature=str(mc.get("error_signature") or ""),
                symptom_keywords=str(mc.get("symptom_keywords") or kebab_id),
                scene_summary=str(mc.get("scene_summary") or safe_desc),
                root_cause=str(mc.get("root_cause") or ""),
                verified_solution=str(mc.get("verified_solution") or ""),
                effective_commands=mc.get("effective_commands") or [],
                origin_type="distill",
                source_conversation_id=conversation_id,
            )
            cases_saved += 1

        # 4. 同步 FTS5 知识库并 0ms 热更新 Skill 注册缓存
        eff_project_id = skill_loader._build_knowledge_project_id(str(ws_path), "workspace")
        await skill_loader.sync_skill_knowledge(
            target_skill_dir.name,
            project_id=eff_project_id,
            skill_dir_override=target_skill_dir,
        )
        skill_loader.invalidate_cache()
        invalidate_skill_file_cache()

        return {
            "ok": True,
            "skill_id": kebab_id,
            "skill_dir": str(target_skill_dir),
            "version": skill_version,
            "is_upgrade": is_upgrade,
            "facts_saved": facts_saved,
            "cases_saved": cases_saved,
            "audit_summary": str(audit_res.get("summary") or "SAFE"),
        }


def _sanitize_text_val(raw: str) -> str:
    """便捷提取脱敏后文本字符串。"""
    clean, _ = MemoryPolicyGuard.sanitize_text(raw)
    return clean.strip()


class SkillExperienceService:
    """经验提议审批、Skill 写盘与 FTS5 热加载服务"""

    @staticmethod
    def list_available_target_skills(workspace_root: str | None = None) -> list[dict[str, Any]]:
        """合并返回当前可作为迭代更新目标的所有 Skill 简要清单（L2 工作区优先）"""
        merged: dict[str, dict[str, Any]] = {}
        for sk in skill_loader.list_skills(include_content=False):
            sk_id = str(sk.get("id") or sk.get("name") or "").strip()
            if not sk_id or sk.get("type") == "mcp":
                continue
            merged[sk_id] = {
                "id": sk_id,
                "name": str(sk.get("name") or sk_id),
                "command": str(sk.get("command") or f"/{sk_id}"),
                "version": normalize_version_str(sk.get("version")),
                "scope": "global",
                "description": str(sk.get("description") or ""),
            }

        if workspace_root:
            ws_path = Path(workspace_root)
            for ws_root in (ws_path / "skills", ws_path / ".agenthub" / "skills"):
                if not ws_root.is_dir():
                    continue
                try:
                    for item in sorted(ws_root.iterdir(), key=lambda p: p.name):
                        if not item.is_dir() or item.name.startswith("."):
                            continue
                        md_file = item / "SKILL.md"
                        if not md_file.is_file():
                            continue
                        raw = md_file.read_text(encoding="utf-8", errors="replace")[:4096]
                        meta = skill_loader._parse_frontmatter(raw, item.name)
                        merged[item.name] = {
                            "id": item.name,
                            "name": meta["name"],
                            "command": meta["command"],
                            "version": normalize_version_str(meta.get("version")),
                            "scope": "workspace",
                            "description": meta["description"],
                        }
                except OSError:
                    continue

        return sorted(
            merged.values(),
            key=lambda x: (0 if x["scope"] == "workspace" else 1, x["name"]),
        )

    @classmethod
    async def preview_proposal(
        cls,
        proposal_id: str,
        *,
        apply_mode: str = "update_existing",
        target_skill_id: str | None = None,
        target_scope: str | None = None,
    ) -> dict[str, Any]:
        """当用户切换更新目标 Skill 或切换另存为新建模式时，实时重算精确的 Diff 与草案正文"""
        proposal = await cls.get_proposal(proposal_id)
        if not proposal:
            raise KeyError(f"未找到经验提议: {proposal_id}")

        ws_id = proposal["workspace_id"]
        ws_root = await WorkspaceRepository.get_root_path_by_id(ws_id)
        payload = proposal.get("payload") if isinstance(proposal.get("payload"), dict) else {}
        triple = proposal.get("experience_triple") if isinstance(proposal.get("experience_triple"), dict) else {}
        distill_ctx = payload.get("distill_context") if isinstance(payload.get("distill_context"), dict) else {}

        eff_scope = (target_scope or proposal.get("target_scope") or "workspace").strip().lower()
        effective_ws_root = ws_root if eff_scope == "workspace" else None

        if apply_mode == "create_new":
            new_id = str(payload.get("new_skill_id") or payload.get("skill_id") or proposal.get("proposed_skill_name") or "custom-skill").strip()
            new_cmd = str(payload.get("new_command") or payload.get("command") or f"/{new_id}")
            new_md = str(payload.get("new_skill_md") or payload.get("skill_md") or "").strip()
            if not new_md:
                new_md, _, _ = ExperienceDistiller._build_new_skill_files(
                    skill_id=new_id,
                    command=new_cmd,
                    description=str(payload.get("new_description") or proposal.get("summary") or new_id),
                    goal=str(distill_ctx.get("goal") or proposal.get("title") or ""),
                    triple=triple,
                    effective_tools=[],
                    active_facts=[],
                    artifacts=[],
                    trigger_reason=proposal.get("trigger_reason") or "manual_distill",
                )
            diff_preview = _build_unified_diff("", new_md, f"skills/{new_id}/SKILL.md")
            return {
                "apply_mode": "create_new",
                "target_skill_id": new_id,
                "command": new_cmd,
                "current_version": "0.0.0",
                "next_version": "1.0.0",
                "target_file": f"skills/{new_id}/SKILL.md",
                "diff_preview": diff_preview,
                "draft_content": new_md,
            }

        eff_target_id = str(
            target_skill_id
            or proposal.get("target_skill_id")
            or payload.get("target_skill_id")
            or proposal.get("proposed_skill_name")
            or ""
        ).strip()
        if not eff_target_id:
            avail = cls.list_available_target_skills(effective_ws_root)
            if not avail:
                raise ValueError("当前没有可供更新的已有 Skill，请选择另存新建")
            eff_target_id = avail[0]["id"]

        old_md, new_md, cur_ver, nxt_ver, cmd = ExperienceDistiller.build_upgraded_skill_md(
            skill_id=eff_target_id,
            workspace_root=effective_ws_root,
            title=str(distill_ctx.get("title") or proposal.get("title") or ""),
            goal=str(distill_ctx.get("goal") or ""),
            problem=str(triple.get("problem") or ""),
            root_cause=str(triple.get("root_cause") or ""),
            solution=str(triple.get("solution") or ""),
            do_steps=[str(s) for s in (distill_ctx.get("steps") or []) if str(s).strip()],
        )
        diff_preview = _build_unified_diff(old_md, new_md, f"skills/{eff_target_id}/SKILL.md")
        return {
            "apply_mode": "update_existing",
            "target_skill_id": eff_target_id,
            "command": cmd,
            "current_version": cur_ver,
            "next_version": nxt_ver,
            "target_file": f"skills/{eff_target_id}/SKILL.md",
            "diff_preview": diff_preview,
            "draft_content": new_md,
        }

    @staticmethod
    async def list_proposals(
        *,
        workspace_id: str | None = None,
        conversation_id: str | None = None,
        status: str | None = "pending",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """查询经验提议列表"""
        conditions: list[str] = []
        params: list[Any] = []
        if workspace_id:
            conditions.append("workspace_id = ?")
            params.append(workspace_id.strip())
        if conversation_id:
            conditions.append("conversation_id = ?")
            params.append(conversation_id.strip())
        if status:
            conditions.append("status = ?")
            params.append(status.strip())

        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        safe_limit = max(1, min(int(limit), 200))

        async with db_manager.get_connection() as conn:
            async with conn.execute(
                f"SELECT * FROM experience_proposals {where_sql} ORDER BY created_at DESC LIMIT ?",
                (*params, safe_limit),
            ) as cur:
                rows = await cur.fetchall()
        return [_serialize_proposal_row(r) for r in rows]

    @staticmethod
    async def get_proposal(proposal_id: str) -> dict[str, Any] | None:
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                "SELECT * FROM experience_proposals WHERE id = ? LIMIT 1",
                (proposal_id.strip(),),
            ) as cur:
                row = await cur.fetchone()
        return _serialize_proposal_row(row) if row else None

    @staticmethod
    def _ensure_workspace_skill_override(skill_id: str, target_dir: Path) -> None:
        """
        向工作区 L2 目录应用 skill_patch 前，若工作区尚无同名 SKILL.md，
        从全局 L1 技能目录安全复制基准定义（确保 L2 完整独立且不污染 L1 全局底座）。
        """
        if (target_dir / "SKILL.md").is_file():
            return
        l1_dir = skill_loader._find_skill_dir(skill_id)
        if l1_dir and l1_dir.is_dir():
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(
                l1_dir,
                target_dir,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules"),
            )
        else:
            target_dir.mkdir(parents=True, exist_ok=True)
            fallback_md = (
                f"---\n"
                f"name: {skill_id}\n"
                f"command: /{skill_id}\n"
                f"type: skill\n"
                f"description: 工作区演进定制技能 ({skill_id})\n"
                f"---\n\n"
                f"# {skill_id}\n\n工作区定制演进经验规程。\n"
            )
            (target_dir / "SKILL.md").write_text(fallback_md, encoding="utf-8")

    @classmethod
    async def apply_proposal(
        cls,
        proposal_id: str,
        *,
        edited_content: str | None = None,
        target_scope: str | None = None,
        target_skill_name: str | None = None,
        apply_mode: str | None = None,
        target_skill_id: str | None = None,
        next_version: str | None = None,
    ) -> dict[str, Any]:
        """
        人工确认审批后正式落盘迭代升级已有 Skill（版本号递增 + SKILL.md 更新 + playbook 同步）
        或新建独立四元组 Skill，并立即同步 FTS5 与刷新缓存。
        """
        proposal = await cls.get_proposal(proposal_id)
        if not proposal:
            raise KeyError(f"未找到经验提议: {proposal_id}")
        if proposal["status"] == "applied":
            raise ValueError("该经验提议已应用落盘，请勿重复提交")

        ws_id = proposal["workspace_id"]
        ws_root = await WorkspaceRepository.get_root_path_by_id(ws_id)
        has_valid_ws_dir = bool(ws_root and Path(ws_root).is_dir())

        requested_scope = (target_scope or proposal.get("target_scope") or "workspace").strip().lower()
        final_scope = "workspace" if (requested_scope == "workspace" and has_valid_ws_dir) else "global"

        payload = proposal.get("payload") if isinstance(proposal.get("payload"), dict) else {}
        triple = proposal.get("experience_triple") if isinstance(proposal.get("experience_triple"), dict) else {}
        distill_ctx = payload.get("distill_context") if isinstance(payload.get("distill_context"), dict) else {}

        eff_mode = (apply_mode or "").strip().lower()
        if eff_mode not in ("update_existing", "create_new"):
            eff_mode = "update_existing" if proposal["proposal_type"] == "skill_patch" else "create_new"

        final_proposal_type = "skill_patch" if eff_mode == "update_existing" else "new_skill"
        final_target_skill_id: str | None = proposal.get("target_skill_id")
        final_version = "1.0.0"

        if eff_mode == "update_existing":
            raw_skill_id = (
                target_skill_id
                or proposal.get("target_skill_id")
                or payload.get("target_skill_id")
                or proposal.get("proposed_skill_name")
                or "general-skill"
            )
            safe_skill_id = os.path.basename(str(raw_skill_id).strip()) or "general-skill"
            final_target_skill_id = safe_skill_id

            if final_scope == "workspace" and ws_root:
                resolved_existing, existing_scope = skill_loader.resolve_skill_dir_with_scope(
                    safe_skill_id,
                    workspace_root=ws_root,
                )
                if resolved_existing and existing_scope == "workspace":
                    target_dir = resolved_existing
                else:
                    target_dir = Path(ws_root).resolve() / "skills" / safe_skill_id
                    cls._ensure_workspace_skill_override(safe_skill_id, target_dir)
            else:
                l1_existing = skill_loader._find_skill_dir(safe_skill_id)
                target_dir = l1_existing if l1_existing else (skill_loader.skills_dir / safe_skill_id)
                cls._ensure_workspace_skill_override(safe_skill_id, target_dir)

            # 判定前端传来的 edited_content 是完整 SKILL.md 正文，还是单独传给 playbook 的修订片段（兼容旧接口契约）
            raw_edited = _normalize_newlines(edited_content).strip() if (edited_content is not None and edited_content.strip()) else ""
            is_full_skill_md_edit = bool(
                raw_edited
                and (raw_edited.startswith("---") or re.search(r"^#\s+", raw_edited, re.MULTILINE))
            )
            same_as_initial_target = safe_skill_id == (payload.get("target_skill_id") or proposal.get("target_skill_id"))

            ts_ctx_notes = [str(n) for n in (distill_ctx.get("troubleshooting_notes") or []) if str(n).strip()]
            if is_full_skill_md_edit:
                sanitized_skill_md, _ = MemoryPolicyGuard.sanitize_text(raw_edited)
                if not sanitized_skill_md.startswith("---"):
                    _, generated_md, _, nxt_v, _ = ExperienceDistiller.build_upgraded_skill_md(
                        skill_id=safe_skill_id,
                        workspace_root=ws_root if final_scope == "workspace" else None,
                        title=str(distill_ctx.get("title") or proposal.get("title") or ""),
                        goal=str(distill_ctx.get("goal") or ""),
                        problem=str(triple.get("problem") or ""),
                        root_cause=str(triple.get("root_cause") or ""),
                        solution=str(triple.get("solution") or ""),
                        do_steps=[str(s) for s in (distill_ctx.get("steps") or []) if str(s).strip()],
                        llm_upgraded_body=sanitized_skill_md,
                        next_version_override=next_version,
                        troubleshooting_notes=ts_ctx_notes,
                    )
                    sanitized_skill_md = generated_md
                    final_version = nxt_v
                else:
                    meta_edited = skill_loader._parse_frontmatter(sanitized_skill_md, safe_skill_id)
                    existing_meta_ver = "1.0.0"
                    if (target_dir / "SKILL.md").is_file():
                        cur_raw = (target_dir / "SKILL.md").read_text(encoding="utf-8", errors="replace")
                        existing_meta_ver = skill_loader._parse_frontmatter(cur_raw, safe_skill_id).get("version") or "1.0.0"
                    desired_ver = normalize_version_str(next_version) if next_version else meta_edited.get("version") or "1.0.0"
                    if desired_ver == normalize_version_str(existing_meta_ver):
                        desired_ver = bump_semver(existing_meta_ver, "minor")
                    final_version = desired_ver
                    now_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    mcp_line = f"mcp_server: {meta_edited['mcp_server']}\n" if meta_edited.get("mcp_server") else ""
                    sanitized_skill_md = (
                        f"---\n"
                        f"name: {meta_edited['name']}\n"
                        f"command: {meta_edited['command']}\n"
                        f"version: {desired_ver}\n"
                        f"updated_at: {now_day}\n"
                        f"type: {meta_edited.get('type') or 'skill'}\n"
                        f"{mcp_line}"
                        f"description: {meta_edited['description']}\n"
                        f"---\n\n"
                        f"{meta_edited['body']}\n"
                    )
                pb_entry = str(payload.get("markdown_entry") or "").strip()
            else:
                # raw_edited 为空（未手动改框）或为传统 markdown_entry 片段
                if same_as_initial_target and str(payload.get("skill_md") or "").strip() and not next_version:
                    sanitized_skill_md = _normalize_newlines(str(payload.get("skill_md")))
                    final_version = normalize_version_str(payload.get("next_version") or "1.1.0")
                else:
                    _, generated_md, _, nxt_v, _ = ExperienceDistiller.build_upgraded_skill_md(
                        skill_id=safe_skill_id,
                        workspace_root=ws_root if final_scope == "workspace" else None,
                        title=str(distill_ctx.get("title") or proposal.get("title") or ""),
                        goal=str(distill_ctx.get("goal") or ""),
                        problem=str(triple.get("problem") or ""),
                        root_cause=str(triple.get("root_cause") or ""),
                        solution=str(triple.get("solution") or ""),
                        do_steps=[str(s) for s in (distill_ctx.get("steps") or []) if str(s).strip()],
                        next_version_override=next_version,
                        troubleshooting_notes=ts_ctx_notes,
                    )
                    sanitized_skill_md = generated_md
                    final_version = nxt_v
                pb_entry = raw_edited or str(payload.get("markdown_entry") or "").strip()

            skill_md_file = target_dir / "SKILL.md"
            skill_md_file.write_text(sanitized_skill_md.strip() + "\n", encoding="utf-8")
            skill_loader.normalize_skill_file(skill_md_file)

            pb_entry_clean, _ = MemoryPolicyGuard.sanitize_text(_normalize_newlines(pb_entry).strip())
            if pb_entry_clean:
                _append_dedup_playbook_entry(target_dir / "knowledge" / "experience_playbook.md", pb_entry_clean)
            written_path = str(skill_md_file.resolve())
        else:
            raw_new_id = (
                target_skill_name
                or payload.get("new_skill_id")
                or payload.get("skill_id")
                or proposal.get("proposed_skill_name")
                or "auto-skill"
            )
            safe_skill_id = _normalize_kebab_id(raw_new_id, fallback=f"skill-{uuid.uuid4().hex[:6]}")
            final_target_skill_id = None
            final_version = "1.0.0"

            collided_dir, collided_scope = skill_loader.resolve_skill_dir_with_scope(
                safe_skill_id,
                workspace_root=ws_root if final_scope == "workspace" else None,
            )
            if final_scope == "workspace" and ws_root:
                default_target_dir = Path(ws_root).resolve() / "skills" / safe_skill_id
            else:
                default_target_dir = skill_loader.skills_dir / safe_skill_id

            if collided_dir and collided_scope == final_scope and (collided_dir / "SKILL.md").is_file():
                target_dir = collided_dir
            else:
                target_dir = default_target_dir

            has_existing_skill = (target_dir / "SKILL.md").is_file()
            schema_obj = payload.get("new_schema_json") or payload.get("schema_json")
            pb_source = str(
                payload.get("markdown_entry")
                or payload.get("new_playbook_md")
                or payload.get("playbook_md")
                or ""
            ).strip()
            ts_ctx_notes = [str(n) for n in (distill_ctx.get("troubleshooting_notes") or []) if str(n).strip()]

            staging_base = (
                Path(ws_root).resolve() / ".agenthub" / ".staging_skills"
                if (final_scope == "workspace" and ws_root)
                else target_dir.parent / ".staging_skills"
            )
            staging_dir = staging_base / f"{safe_skill_id}_{uuid.uuid4().hex[:6]}"

            try:
                if has_existing_skill:
                    # 同名碰撞防护：自动转为基于既有资产的版本递增融合升级，严禁丢失原 knowledge/ 自定义资产
                    shutil.copytree(
                        target_dir,
                        staging_dir,
                        dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules"),
                    )
                    final_proposal_type = "skill_patch"
                    final_target_skill_id = safe_skill_id
                    do_steps_list = [str(s) for s in (distill_ctx.get("steps") or []) if str(s).strip()]
                    bump_lvl = determine_semver_bump_level(
                        has_schema_change=isinstance(schema_obj, dict) and bool(schema_obj),
                        do_steps=do_steps_list,
                    )
                    _, generated_md, cur_v, nxt_v, _ = ExperienceDistiller.build_upgraded_skill_md(
                        skill_id=safe_skill_id,
                        workspace_root=ws_root if final_scope == "workspace" else None,
                        title=str(distill_ctx.get("title") or proposal.get("title") or ""),
                        goal=str(distill_ctx.get("goal") or ""),
                        problem=str(triple.get("problem") or ""),
                        root_cause=str(triple.get("root_cause") or ""),
                        solution=str(triple.get("solution") or ""),
                        do_steps=do_steps_list,
                        next_version_override=next_version,
                        troubleshooting_notes=ts_ctx_notes,
                        bump_level=bump_lvl,
                    )
                    if edited_content is not None and edited_content.strip():
                        raw_new_edit, _ = MemoryPolicyGuard.sanitize_text(_normalize_newlines(edited_content).strip())
                        if raw_new_edit.startswith("---"):
                            meta_edited = skill_loader._parse_frontmatter(raw_new_edit, safe_skill_id)
                            desired_ver = normalize_version_str(next_version) if next_version else meta_edited.get("version") or nxt_v
                            if desired_ver == normalize_version_str(cur_v):
                                desired_ver = bump_semver(cur_v, bump_lvl)
                            nxt_v = desired_ver
                            now_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                            mcp_line = f"mcp_server: {meta_edited['mcp_server']}\n" if meta_edited.get("mcp_server") else ""
                            generated_md = (
                                f"---\nname: {meta_edited['name']}\ncommand: {meta_edited['command']}\n"
                                f"version: {desired_ver}\nupdated_at: {now_day}\ntype: {meta_edited.get('type') or 'skill'}\n"
                                f"{mcp_line}description: {meta_edited['description']}\n---\n\n{meta_edited['body']}\n"
                            )
                    final_version = nxt_v
                    skill_md_clean = generated_md
                else:
                    staging_dir.mkdir(parents=True, exist_ok=True)
                    skill_md_raw = (
                        edited_content
                        if (edited_content is not None and edited_content.strip())
                        else str(payload.get("new_skill_md") or payload.get("skill_md") or "")
                    )
                    skill_md_clean, _ = MemoryPolicyGuard.sanitize_text(_normalize_newlines(skill_md_raw).strip())
                    if not skill_md_clean:
                        raise ValueError("新建 Skill 的 SKILL.md 内容不能为空")

                staging_skill_md = staging_dir / "SKILL.md"
                staging_skill_md.write_text(skill_md_clean.strip() + "\n", encoding="utf-8")
                skill_loader.normalize_skill_file(staging_skill_md)

                if isinstance(schema_obj, dict) and schema_obj:
                    (staging_dir / "schema.json").write_text(
                        json.dumps(schema_obj, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )

                if pb_source:
                    pb_clean, _ = MemoryPolicyGuard.sanitize_text(_normalize_newlines(pb_source))
                    if has_existing_skill:
                        # 剥离可能带有的顶层一级标题，按节去重追加到既有 playbook
                        stripped_pb = re.sub(r"^#\s+[^\n]+\n+", "", pb_clean.strip()).strip()
                        _append_dedup_playbook_entry(
                            staging_dir / "knowledge" / "experience_playbook.md",
                            stripped_pb or pb_clean,
                        )
                    else:
                        kb_dir = staging_dir / "knowledge"
                        kb_dir.mkdir(parents=True, exist_ok=True)
                        (kb_dir / "experience_playbook.md").write_text(pb_clean.strip() + "\n", encoding="utf-8")

                _atomic_replace_skill_dir(staging_dir, target_dir)
            finally:
                if staging_dir.exists():
                    shutil.rmtree(staging_dir, ignore_errors=True)

            written_path = str((target_dir / "SKILL.md").resolve())

        # 同步切块写入 FTS5 并清理前后端 Skill 缓存，实现 0ms 即时生效
        eff_project_id = skill_loader._build_knowledge_project_id(ws_root, final_scope)
        await skill_loader.sync_skill_knowledge(
            target_dir.name,
            project_id=eff_project_id,
            skill_dir_override=target_dir,
        )
        skill_loader.invalidate_cache()
        invalidate_skill_file_cache()

        now_ts = datetime.now(timezone.utc).isoformat()
        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                UPDATE experience_proposals
                SET status = 'applied',
                    proposal_type = ?,
                    target_skill_id = ?,
                    target_scope = ?,
                    proposed_skill_name = ?,
                    resolved_at = ?
                WHERE id = ?
                """,
                (final_proposal_type, final_target_skill_id, final_scope, target_dir.name, now_ts, proposal_id),
            )
            await conn.commit()
            async with conn.execute(
                "SELECT * FROM experience_proposals WHERE id = ?",
                (proposal_id,),
            ) as cur:
                updated_row = await cur.fetchone()

        updated_dict = _serialize_proposal_row(updated_row)
        updated_dict["written_path"] = written_path
        updated_dict["skill_dir"] = str(target_dir.resolve())
        updated_dict["applied_version"] = final_version
        logger.info(
            f"[SkillExperienceService] 经验提议 {proposal_id} 已合入落盘: {written_path} "
            f"(mode={eff_mode}, version=v{final_version}, scope={final_scope})"
        )
        return updated_dict

    @staticmethod
    async def dismiss_proposal(proposal_id: str) -> dict[str, Any] | None:
        """忽略/拒绝经验提议"""
        now_ts = datetime.now(timezone.utc).isoformat()
        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                UPDATE experience_proposals
                SET status = 'dismissed', resolved_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (now_ts, proposal_id.strip()),
            )
            await conn.commit()
            async with conn.execute(
                "SELECT * FROM experience_proposals WHERE id = ?",
                (proposal_id.strip(),),
            ) as cur:
                row = await cur.fetchone()
        return _serialize_proposal_row(row) if row else None


experience_distiller = ExperienceDistiller()
skill_experience_service = SkillExperienceService()
