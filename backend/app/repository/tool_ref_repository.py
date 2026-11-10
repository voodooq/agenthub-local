"""
工具超长原始输出按需引用仓储层 (Tool Output Reference Repository)
职责：
1. 将旁路脱水截留的原始超长输出按体积分流存储：
   - < 256KB 直接存 SQLite (storage_mode='inline_db')
   - >= 256KB 落盘至 Path(db_manager.db_path).parent / "tool_refs" / conv_id / f"{ref_id}.txt" (storage_mode='disk_file')
2. 提供按行号区间 (`start_line` ~ `end_line`) 与关键词上下文定位切片读取能力，供 `read_tool_ref` 工具按需回查原文。
"""
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any
from .db import db_manager


# NOTE: 256KB 阈值：平衡 SQLite WAL 写入效率与大日志/源码磁盘按需流式读取开销
INLINE_BYTE_THRESHOLD = 256 * 1024
# 单次切片最大返回行与字符上限，防止 Agent 误读整份超大日志文件再次撑爆上下文
MAX_SLICE_LINES = 200
MAX_SLICE_CHARS = 8000


@dataclass
class ToolOutputRefModel:
    """工具超长输出引用实体"""
    ref_id: str
    conversation_id: str
    turn_id: int
    tool_name: str
    tool_args_summary: str
    storage_mode: str
    raw_content: str | None
    disk_path: str | None
    raw_chars: int
    raw_tokens_est: int
    scrubbed_summary: str
    scrubbed_tokens_est: int
    saved_tokens_est: int
    created_at: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "ToolOutputRefModel":
        data = dict(row)
        return cls(
            ref_id=str(data.get("ref_id", "")),
            conversation_id=str(data.get("conversation_id", "")),
            turn_id=int(data.get("turn_id") or 1),
            tool_name=str(data.get("tool_name", "")),
            tool_args_summary=str(data.get("tool_args_summary", "") or ""),
            storage_mode=str(data.get("storage_mode", "inline_db") or "inline_db"),
            raw_content=data.get("raw_content"),
            disk_path=data.get("disk_path"),
            raw_chars=int(data.get("raw_chars") or 0),
            raw_tokens_est=int(data.get("raw_tokens_est") or 0),
            scrubbed_summary=str(data.get("scrubbed_summary", "") or ""),
            scrubbed_tokens_est=int(data.get("scrubbed_tokens_est") or 0),
            saved_tokens_est=int(data.get("saved_tokens_est") or 0),
            created_at=data.get("created_at"),
        )


def _sanitize_segment(raw: str) -> str:
    """将路径段净化为安全文件名，防止路径穿越攻击"""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(raw).name)
    if not cleaned or cleaned in {".", ".."}:
        return "invalid"
    return cleaned


class ToolRefRepository:
    """工具超长原文引用数据访问对象"""

    @staticmethod
    def _get_refs_root() -> Path:
        """动态解析当前数据库同级 `tool_refs/` 根目录，天然兼容单元测试 tmp_path 隔离"""
        return Path(db_manager.db_path).resolve().parent / "tool_refs"

    @staticmethod
    async def create_ref(
        ref_id: str,
        conversation_id: str,
        turn_id: int,
        tool_name: str,
        raw_content: str,
        raw_tokens_est: int,
        scrubbed_summary: str,
        scrubbed_tokens_est: int,
        tool_args_summary: str = "",
    ) -> ToolOutputRefModel:
        """创建并持久化超长工具原始输出引用记录"""
        raw_bytes = raw_content.encode("utf-8", errors="replace")
        raw_chars = len(raw_content)
        saved_tokens = max(0, int(raw_tokens_est) - int(scrubbed_tokens_est))

        storage_mode = "inline_db"
        stored_raw: str | None = raw_content
        disk_path: str | None = None

        if len(raw_bytes) >= INLINE_BYTE_THRESHOLD:
            storage_mode = "disk_file"
            safe_conv = _sanitize_segment(conversation_id)
            safe_ref = _sanitize_segment(ref_id)
            conv_dir = ToolRefRepository._get_refs_root() / safe_conv
            conv_dir.mkdir(parents=True, exist_ok=True)
            target_file = conv_dir / f"{safe_ref}.txt"
            target_file.write_text(raw_content, encoding="utf-8", errors="replace")
            stored_raw = None
            disk_path = str(target_file)

        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO tool_output_refs (
                    ref_id, conversation_id, turn_id, tool_name, tool_args_summary,
                    storage_mode, raw_content, disk_path, raw_chars, raw_tokens_est,
                    scrubbed_summary, scrubbed_tokens_est, saved_tokens_est
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ref_id,
                    conversation_id,
                    int(turn_id),
                    tool_name,
                    tool_args_summary[:300],
                    storage_mode,
                    stored_raw,
                    disk_path,
                    raw_chars,
                    int(raw_tokens_est),
                    scrubbed_summary,
                    int(scrubbed_tokens_est),
                    saved_tokens,
                ),
            )
            await conn.commit()

        return ToolOutputRefModel(
            ref_id=ref_id,
            conversation_id=conversation_id,
            turn_id=int(turn_id),
            tool_name=tool_name,
            tool_args_summary=tool_args_summary[:300],
            storage_mode=storage_mode,
            raw_content=stored_raw,
            disk_path=disk_path,
            raw_chars=raw_chars,
            raw_tokens_est=int(raw_tokens_est),
            scrubbed_summary=scrubbed_summary,
            scrubbed_tokens_est=int(scrubbed_tokens_est),
            saved_tokens_est=saved_tokens,
        )

    @staticmethod
    async def get_ref(ref_id: str, conversation_id: str | None = None) -> ToolOutputRefModel | None:
        """查询引用元信息；传入 conversation_id 时附带会话归属校验防越权读取"""
        async with db_manager.get_connection() as conn:
            if conversation_id:
                sql = "SELECT * FROM tool_output_refs WHERE ref_id = ? AND conversation_id = ?"
                params: tuple[Any, ...] = (ref_id, conversation_id)
            else:
                sql = "SELECT * FROM tool_output_refs WHERE ref_id = ?"
                params = (ref_id,)
            async with conn.execute(sql, params) as cursor:
                row = await cursor.fetchone()
                return ToolOutputRefModel.from_row(row) if row else None

    @staticmethod
    def _load_raw_text(ref: ToolOutputRefModel) -> str:
        """从数据库或磁盘读取完整原始文本"""
        if ref.storage_mode == "disk_file" and ref.disk_path:
            p = Path(ref.disk_path)
            if p.is_file():
                return p.read_text(encoding="utf-8", errors="replace")
            return ""
        return ref.raw_content or ""

    @staticmethod
    async def read_slice(
        ref_id: str,
        start_line: int = 1,
        end_line: int = 120,
        keyword: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """按行号或关键词精准切片回查超长工具原文。

        Returns:
            dict: 包含 found, total_lines, returned_start, returned_end, content, error 等字段。
        """
        ref = await ToolRefRepository.get_ref(ref_id, conversation_id=conversation_id)
        if not ref:
            return {
                "found": False,
                "ref_id": ref_id,
                "error": f"未找到引用 ID [{ref_id}]（可能来自无痕临时缓存已释放，或不属于当前会话）。",
            }

        raw_text = ToolRefRepository._load_raw_text(ref)
        if not raw_text:
            return {
                "found": False,
                "ref_id": ref_id,
                "error": f"引用 ID [{ref_id}] 关联的原始文本文件缺失或为空。",
            }

        lines = raw_text.splitlines()
        total_lines = len(lines)
        if total_lines == 0:
            return {
                "found": True,
                "ref_id": ref_id,
                "tool_name": ref.tool_name,
                "total_lines": 0,
                "returned_start": 0,
                "returned_end": 0,
                "content": "(空文本)",
            }

        clean_keyword = (keyword or "").strip()
        if clean_keyword:
            lower_kw = clean_keyword.lower()
            matched_indices = [idx for idx, line in enumerate(lines) if lower_kw in line.lower()]
            if not matched_indices:
                return {
                    "found": True,
                    "ref_id": ref_id,
                    "tool_name": ref.tool_name,
                    "total_lines": total_lines,
                    "returned_start": 0,
                    "returned_end": 0,
                    "matched_count": 0,
                    "content": f"在总计 {total_lines} 行原文中未检索到关键词 `{clean_keyword}`。可尝试调整 keyword 或指定 start_line/end_line 范围。",
                }

            # 最多取前 5 个命中点各自前后扩展 ±10 行并合并重叠窗口
            windows: list[tuple[int, int]] = []
            for idx in matched_indices[:5]:
                win_start = max(0, idx - 10)
                win_end = min(total_lines - 1, idx + 10)
                if windows and win_start <= windows[-1][1] + 1:
                    windows[-1] = (windows[-1][0], max(windows[-1][1], win_end))
                else:
                    windows.append((win_start, win_end))

            rendered_blocks: list[str] = []
            total_chars = 0
            for win_start, win_end in windows:
                block_lines = [f"L{i + 1}: {lines[i]}" for i in range(win_start, win_end + 1)]
                block_text = "\n".join(block_lines)
                if total_chars + len(block_text) > MAX_SLICE_CHARS:
                    remain = max(200, MAX_SLICE_CHARS - total_chars)
                    rendered_blocks.append(block_text[:remain] + "\n... [关键词上下文已达安全上限截断]")
                    break
                rendered_blocks.append(block_text)
                total_chars += len(block_text) + 12

            return {
                "found": True,
                "ref_id": ref_id,
                "tool_name": ref.tool_name,
                "total_lines": total_lines,
                "matched_count": len(matched_indices),
                "returned_start": windows[0][0] + 1,
                "returned_end": windows[-1][1] + 1,
                "content": "\n---\n".join(rendered_blocks),
            }

        # 普通按行区间读取
        s_line = max(1, int(start_line or 1))
        e_line = max(s_line, int(end_line or (s_line + 119)))
        if (e_line - s_line + 1) > MAX_SLICE_LINES:
            e_line = s_line + MAX_SLICE_LINES - 1
        s_line = min(s_line, total_lines)
        e_line = min(e_line, total_lines)

        sliced_lines: list[str] = []
        char_count = 0
        actual_end = s_line
        for idx in range(s_line - 1, e_line):
            formatted = f"L{idx + 1}: {lines[idx]}"
            if sliced_lines and char_count + len(formatted) > MAX_SLICE_CHARS:
                sliced_lines.append("... [单次切片已达 8,000 字符安全上限，请调小区间或继续翻页]")
                break
            sliced_lines.append(formatted)
            char_count += len(formatted) + 1
            actual_end = idx + 1

        return {
            "found": True,
            "ref_id": ref_id,
            "tool_name": ref.tool_name,
            "total_lines": total_lines,
            "returned_start": s_line,
            "returned_end": actual_end,
            "content": "\n".join(sliced_lines),
        }

    @staticmethod
    async def sum_saved_tokens_for_turn(conversation_id: str, turn_id: int) -> int:
        """汇总某轮工具旁路清洗累计省下的估算 Token 数"""
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                """
                SELECT COALESCE(SUM(saved_tokens_est), 0) AS total_saved
                FROM tool_output_refs
                WHERE conversation_id = ? AND turn_id = ?
                """,
                (conversation_id, int(turn_id)),
            ) as cursor:
                row = await cursor.fetchone()
                return int(row["total_saved"]) if row and row["total_saved"] is not None else 0

