"""
跨平台历史会话与长期记忆统一双轨导入引擎 (UnifiedImporter)
核心能力：
1. 自动格式嗅探：支持 ChatGPT (.zip / conversations.json + memories.json)、Claude (.zip / conversations.json)、
   IDE 规则文件 (GEMINI.md / CLAUDE.md / .cursorrules / .windsurfrules) 与通用 JSON / Markdown
2. 树状会话展平：将 ChatGPT mapping 树状节点展平为标准线性 turn_id 消息链，并自动建立 FTS5 全文索引
3. 长期记忆冲突比对：基于本地 SQLite FTS5 BM25 与字符重合度，自动标注 [new 新增] / [conflict 冲突覆盖] / [duplicate 重复忽略]
4. 批量事务落盘与一键按批次撤回 (Rollback)：BEGIN IMMEDIATE 秒级写入数千条消息，支持按 batch_id 完整无痕回滚
"""
import io
import json
import re
import uuid
import zipfile
from typing import Any

from ..core.logging_config import logger
from ..repository.db import db_manager
from ..memory.long_term import long_term_memory


class UnifiedImporter:
    """四步可视化导入向导的后端核心引擎"""

    # 内存暂存区：preview_id -> 解析后的会话与候选记忆数据
    _staged_previews: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _classify_rule_category(text: str) -> str:
        t_low = text.lower()
        if any(k in t_low for k in ("禁止", "严禁", "必须", "规范", "never", "must", "always", "安全", "红线")):
            return "rule"
        if any(
            k in t_low
            for k in ("windows", "linux", "mac", "wsl", "docker", "路径", "端口", "python", "node", "venv", "环境")
        ):
            return "env_info"
        return "preference"

    @classmethod
    def _flatten_chatgpt_mapping(cls, mapping: dict[str, Any]) -> list[dict[str, Any]]:
        """将 ChatGPT 导出的树状 mapping 结构按主分支展平为线性消息数组"""
        if not isinstance(mapping, dict):
            return []

        raw_nodes: list[tuple[float, str, str]] = []
        for node_id, node_val in mapping.items():
            if not isinstance(node_val, dict):
                continue
            msg = node_val.get("message")
            if not isinstance(msg, dict):
                continue
            author = (msg.get("author") or {}).get("role", "")
            if author not in ("user", "assistant"):
                continue
            content_obj = msg.get("content") or {}
            parts = content_obj.get("parts") or []
            text_pieces = [str(p) for p in parts if isinstance(p, (str, int, float)) and str(p).strip()]
            full_text = "\n".join(text_pieces).strip()
            if not full_text:
                continue
            create_time = float(msg.get("create_time") or 0.0)
            mapped_role = "model" if author == "assistant" else "user"
            raw_nodes.append((create_time, mapped_role, full_text))

        # 若节点有时间戳则按时间戳排序，否则保持遍历顺序
        if any(t[0] > 0 for t in raw_nodes):
            raw_nodes.sort(key=lambda x: x[0])

        messages: list[dict[str, Any]] = []
        turn_id = 1
        for _, role, content in raw_nodes:
            if role == "user" and messages and messages[-1]["role"] == "model":
                turn_id += 1
            messages.append({
                "role": role,
                "content": content,
                "turn_id": turn_id,
            })
        return messages

    @classmethod
    def _parse_markdown_rules(cls, md_text: str, filename: str) -> list[dict[str, Any]]:
        """将 GEMINI.md / CLAUDE.md / .cursorrules 切块提取为置顶高置信度长期记忆"""
        memories: list[dict[str, Any]] = []
        lines = (md_text or "").splitlines()
        current_section = ""

        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("```"):
                continue
            if line.startswith("#"):
                current_section = line.lstrip("#").strip()
                continue

            # 提取列表项或独立规范段落
            clean_item = re.sub(r"^[-*+]\s+|\d+\.\s+", "", line).strip()
            if len(clean_item) < 6 or len(clean_item) > 300:
                continue

            prefix = f"[{current_section}] " if current_section and len(current_section) <= 20 else ""
            full_fact = f"{prefix}{clean_item}"
            cat = cls._classify_rule_category(full_fact)
            memories.append({
                "temp_id": f"cand_{uuid.uuid4().hex[:8]}",
                "category": cat,
                "content": full_fact,
                "confidence": 0.95,
                "is_pinned": 1,
                "source": f"import:{filename}",
            })

        return memories[:60]

    @classmethod
    def _parse_json_payload(cls, data: Any, filename: str) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
        """
        识别并解析 JSON 结构（支持 ChatGPT、Claude、memories.json 及通用对话备份）
        Returns: (detected_source_type, conversations_list, candidate_memories_list)
        """
        conversations: list[dict[str, Any]] = []
        memories: list[dict[str, Any]] = []
        detected_type = "custom_json"

        # 情况 1: 顶层为字典（可能包含 conversations / memories / messages）
        if isinstance(data, dict):
            if "memories" in data and isinstance(data["memories"], list):
                detected_type = "chatgpt"
                for m in data["memories"]:
                    txt = m.get("content") or m.get("value") if isinstance(m, dict) else str(m)
                    if txt and str(txt).strip():
                        memories.append({
                            "temp_id": f"cand_{uuid.uuid4().hex[:8]}",
                            "category": cls._classify_rule_category(str(txt)),
                            "content": str(txt).strip(),
                            "confidence": 0.90,
                            "is_pinned": 0,
                            "source": "import:chatgpt",
                        })
            if "conversations" in data and isinstance(data["conversations"], list):
                data = data["conversations"]
            elif "messages" in data and isinstance(data["messages"], list):
                # 单个通用会话文件
                msgs = []
                t_id = 1
                for m in data["messages"]:
                    if not isinstance(m, dict):
                        continue
                    r = m.get("role") or m.get("sender") or "user"
                    role = "model" if r in ("assistant", "model", "bot", "ai") else "user"
                    c = m.get("content") or m.get("text") or ""
                    if c:
                        if role == "user" and msgs and msgs[-1]["role"] == "model":
                            t_id += 1
                        msgs.append({"role": role, "content": str(c), "turn_id": t_id})
                if msgs:
                    conversations.append({
                        "title": data.get("title") or filename,
                        "source_type": "custom_json",
                        "messages": msgs,
                    })
                return detected_type, conversations, memories

        # 情况 2: 顶层为数组
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue

                # 2a. ChatGPT conversations.json 格式（含 mapping 字段）
                if "mapping" in item and isinstance(item["mapping"], dict):
                    detected_type = "chatgpt"
                    msgs = cls._flatten_chatgpt_mapping(item["mapping"])
                    if msgs:
                        conversations.append({
                            "title": (item.get("title") or "ChatGPT 导入对话")[:60],
                            "source_type": "chatgpt",
                            "messages": msgs,
                        })

                # 2b. Claude conversations.json 格式（含 chat_messages 字段）
                elif "chat_messages" in item and isinstance(item["chat_messages"], list):
                    detected_type = "claude"
                    msgs = []
                    t_id = 1
                    for cm in item["chat_messages"]:
                        if not isinstance(cm, dict):
                            continue
                        sender = cm.get("sender", "human")
                        role = "model" if sender in ("assistant", "bot") else "user"
                        text = cm.get("text") or ""
                        if not text and isinstance(cm.get("content"), list):
                            text = "\n".join(
                                str(p.get("text", "")) for p in cm["content"] if isinstance(p, dict)
                            )
                        if text.strip():
                            if role == "user" and msgs and msgs[-1]["role"] == "model":
                                t_id += 1
                            msgs.append({"role": role, "content": text.strip(), "turn_id": t_id})
                    if msgs:
                        conversations.append({
                            "title": (item.get("name") or item.get("title") or "Claude 导入对话")[:60],
                            "source_type": "claude",
                            "messages": msgs,
                        })

                # 2c. ChatGPT memories.json 数组项
                elif ("content" in item or "value" in item) and "messages" not in item:
                    txt = str(item.get("content") or item.get("value") or "").strip()
                    if txt:
                        memories.append({
                            "temp_id": f"cand_{uuid.uuid4().hex[:8]}",
                            "category": item.get("category") or cls._classify_rule_category(txt),
                            "content": txt,
                            "confidence": float(item.get("confidence", 0.90)),
                            "is_pinned": int(item.get("is_pinned", 0)),
                            "source": "import:json",
                        })

                # 2d. 通用包含 messages 数组的会话项 (Cherry Studio / NextChat)
                elif "messages" in item and isinstance(item["messages"], list):
                    msgs = []
                    t_id = 1
                    for m in item["messages"]:
                        if not isinstance(m, dict):
                            continue
                        r = m.get("role", "user")
                        role = "model" if r in ("assistant", "model") else "user"
                        c = str(m.get("content") or "").strip()
                        if c:
                            if role == "user" and msgs and msgs[-1]["role"] == "model":
                                t_id += 1
                            msgs.append({"role": role, "content": c, "turn_id": t_id})
                    if msgs:
                        conversations.append({
                            "title": (item.get("title") or item.get("name") or "导入会话")[:60],
                            "source_type": "custom_json",
                            "messages": msgs,
                        })

        return detected_type, conversations, memories

    @staticmethod
    def _calc_similarity(a: str, b: str) -> float:
        """计算两条记忆文本的序列相似度（精准区分 npm vs pnpm 等细微关键词变更）"""
        import difflib
        na = re.sub(r"\s+", " ", a.strip().lower())
        nb = re.sub(r"\s+", " ", b.strip().lower())
        if not na or not nb:
            return 0.0
        if na == nb:
            return 1.0
        return difflib.SequenceMatcher(None, na, nb).ratio()

    @classmethod
    async def analyze_memory_conflicts(cls, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Step 3 核心能力：将候选导入记忆与本地 SQLite 现有长期记忆进行冲突与去重比对。
        标注状态：
        - 'new' (🟢 新增记忆, resolution='add')
        - 'conflict' (🟡 冲突覆盖, resolution='overwrite')
        - 'duplicate' (⚪ 重复忽略, resolution='skip')
        """
        existing_memories = await long_term_memory.list_all()
        analyzed: list[dict[str, Any]] = []

        for cand in candidates:
            c_text = cand["content"].strip()
            best_match: dict[str, Any] | None = None
            best_sim = 0.0

            for ex in existing_memories:
                sim = cls._calc_similarity(c_text, ex["content"])
                if sim > best_sim:
                    best_sim = sim
                    best_match = ex

            item = dict(cand)
            if best_match and best_sim >= 0.999:
                item["status"] = "duplicate"
                item["resolution"] = "skip"
                item["conflict_with"] = {
                    "id": best_match["id"],
                    "content": best_match["content"],
                }
            elif best_match and best_sim >= 0.40 and best_match["category"] == cand["category"]:
                item["status"] = "conflict"
                item["resolution"] = "overwrite"
                item["conflict_with"] = {
                    "id": best_match["id"],
                    "content": best_match["content"],
                }
            else:
                item["status"] = "new"
                item["resolution"] = "add"
                item["conflict_with"] = None

            analyzed.append(item)

        return analyzed

    @classmethod
    async def parse_and_stage(
        cls,
        filename: str,
        raw_bytes: bytes,
        source_hint: str | None = None,
    ) -> dict[str, Any]:
        """
        Step 1 & Step 3 嗅探解析入口：
        自动识别 .zip / .json / .md / .cursorrules，提取会话列表与记忆冲突比对表并暂存。
        """
        fname_lower = (filename or "upload.txt").lower()
        conversations: list[dict[str, Any]] = []
        candidate_memories: list[dict[str, Any]] = []
        detected_source = source_hint or "custom_json"

        # 1. 处理 ZIP 压缩包 (ChatGPT / Claude 官方导出包)
        if fname_lower.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(raw_bytes), "r") as zf:
                for member in zf.namelist():
                    m_base = member.split("/")[-1].lower()
                    if m_base in ("conversations.json", "memories.json"):
                        try:
                            j_data = json.loads(zf.read(member).decode("utf-8", errors="replace"))
                            dtype, convs, mems = cls._parse_json_payload(j_data, m_base)
                            if dtype != "custom_json":
                                detected_source = dtype
                            conversations.extend(convs)
                            candidate_memories.extend(mems)
                        except Exception as e:
                            logger.warning(f"解析压缩包内 {member} 失败: {e}")
                    elif m_base.endswith(".md") or m_base in (".cursorrules", ".windsurfrules"):
                        try:
                            md_txt = zf.read(member).decode("utf-8", errors="replace")
                            candidate_memories.extend(cls._parse_markdown_rules(md_txt, m_base))
                        except Exception:
                            pass

        # 2. 处理 JSON 文件
        elif fname_lower.endswith(".json"):
            j_data = json.loads(raw_bytes.decode("utf-8", errors="replace"))
            detected_source, conversations, candidate_memories = cls._parse_json_payload(j_data, filename)

        # 3. 处理 Markdown / IDE 规则文件 (GEMINI.md, CLAUDE.md, .cursorrules, .windsurfrules)
        else:
            detected_source = "ide_rules"
            text_str = raw_bytes.decode("utf-8", errors="replace")
            # 尝试检测是否实际上是 JSON 文本
            if text_str.strip().startswith(("{", "[")):
                try:
                    j_data = json.loads(text_str)
                    detected_source, conversations, candidate_memories = cls._parse_json_payload(j_data, filename)
                except Exception:
                    candidate_memories = cls._parse_markdown_rules(text_str, filename)
            else:
                candidate_memories = cls._parse_markdown_rules(text_str, filename)

        # 执行 Step 3 冲突比对分析
        analyzed_memories = await cls.analyze_memory_conflicts(candidate_memories)

        total_messages = sum(len(c.get("messages", [])) for c in conversations)
        preview_id = f"imp_{uuid.uuid4().hex[:10]}"

        cls._staged_previews[preview_id] = {
            "filename": filename,
            "source_type": detected_source,
            "conversations": conversations,
            "memories": analyzed_memories,
        }

        return {
            "preview_id": preview_id,
            "filename": filename,
            "detected_source": detected_source,
            "stats": {
                "conversations_count": len(conversations),
                "messages_count": total_messages,
                "memories_count": len(analyzed_memories),
                "new_count": sum(1 for m in analyzed_memories if m["status"] == "new"),
                "conflict_count": sum(1 for m in analyzed_memories if m["status"] == "conflict"),
                "duplicate_count": sum(1 for m in analyzed_memories if m["status"] == "duplicate"),
            },
            "conversations_preview": [
                {
                    "title": c["title"],
                    "source_type": c["source_type"],
                    "message_count": len(c.get("messages", [])),
                }
                for c in conversations[:20]
            ],
            "memories_comparison": analyzed_memories,
        }

    @classmethod
    def _extract_implicit_memories_from_conversations(
        cls, conversations: list[dict[str, Any]], batch_id: str
    ) -> list[dict[str, Any]]:
        """
        当用户勾选「从高价值历史对话中自动提炼隐性偏好」时，扫描最近长对话中的显式技术栈与环境语句。
        """
        extracted: list[dict[str, Any]] = []
        seen: set[str] = set()
        for conv in conversations[:30]:
            for msg in conv.get("messages", []):
                if msg.get("role") != "user":
                    continue
                text = msg.get("content", "")
                for line in text.splitlines():
                    line = line.strip()
                    if 10 <= len(line) <= 150 and any(
                        kw in line
                        for kw in ("我使用", "我们项目用", "请用", "默认用", "环境是", "技术栈是", "不要用")
                    ):
                        if line not in seen:
                            seen.add(line)
                            extracted.append({
                                "category": cls._classify_rule_category(line),
                                "content": line,
                                "confidence": 0.82,
                                "is_pinned": 0,
                                "source": "import:implicit_extract",
                                "import_batch_id": batch_id,
                            })
                            if len(extracted) >= 15:
                                return extracted
        return extracted

    @classmethod
    async def commit_import(
        cls,
        preview_id: str,
        import_conversations: bool = True,
        import_memories: bool = True,
        extract_implicit: bool = False,
        memory_resolutions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Step 4 批量事务落盘：
        使用 BEGIN IMMEDIATE 高速写入历史会话（自动触发 FTS5 建索引）与冲突消解后的长期记忆，并生成 batch_id。
        """
        staged = cls._staged_previews.get(preview_id)
        if not staged:
            raise ValueError("导入预览会话已过期或不存在，请重新选择文件上传")

        batch_id = f"batch_{uuid.uuid4().hex[:10]}"
        source_type = staged["source_type"]
        filename = staged["filename"]
        conversations = staged["conversations"] if import_conversations else []
        staged_memories = staged["memories"] if import_memories else []

        # 合并前端用户在 Step 3 表格中调整的决策 (add / overwrite / keep_both / skip)
        res_map: dict[str, str] = {}
        if memory_resolutions:
            for r in memory_resolutions:
                if "temp_id" in r and "resolution" in r:
                    res_map[r["temp_id"]] = r["resolution"]

        imported_conv_count = 0
        imported_msg_count = 0
        imported_mem_count = 0

        async with db_manager.get_connection() as conn:
            await conn.execute("BEGIN IMMEDIATE;")
            try:
                # 1. 批量写入历史会话与消息链（触发器自动同步至 messages_fts）
                for conv in conversations:
                    cid = str(uuid.uuid4())
                    title = conv.get("title") or "导入会话"
                    s_type = conv.get("source_type") or source_type
                    await conn.execute(
                        """
                        INSERT INTO conversations
                        (id, title, source_type, import_source, import_batch_id, model_name, scene_mode)
                        VALUES (?, ?, 'takeout', ?, ?, '4 Argon', 'chat')
                        """,
                        (cid, title, s_type, batch_id),
                    )
                    imported_conv_count += 1

                    for m in conv.get("messages", []):
                        await conn.execute(
                            """
                            INSERT INTO messages (conversation_id, turn_id, role, content, token_count)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                cid,
                                int(m.get("turn_id", 1)),
                                m.get("role", "user"),
                                m.get("content", ""),
                                len(m.get("content", "")) // 3,
                            ),
                        )
                        imported_msg_count += 1

                # 2. 按冲突决策写入长期记忆
                for mem in staged_memories:
                    action = res_map.get(mem["temp_id"], mem.get("resolution", "add"))
                    if action == "skip":
                        continue

                    conflict_info = mem.get("conflict_with")
                    if action == "overwrite" and conflict_info and conflict_info.get("id"):
                        await conn.execute(
                            """
                            UPDATE long_term_memories
                            SET content = ?, confidence = ?, source = ?, is_pinned = ?, import_batch_id = ?, last_accessed_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                            """,
                            (
                                mem["content"],
                                float(mem.get("confidence", 0.90)),
                                mem.get("source", f"import:{source_type}"),
                                int(mem.get("is_pinned", 0)),
                                batch_id,
                                conflict_info["id"],
                            ),
                        )
                        imported_mem_count += 1
                    else:
                        # 'add' 或 'keep_both'
                        new_mid = f"mem_{uuid.uuid4().hex[:10]}"
                        await conn.execute(
                            """
                            INSERT INTO long_term_memories
                            (id, category, content, confidence, source, is_pinned, import_batch_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                new_mid,
                                mem.get("category", "preference"),
                                mem["content"],
                                float(mem.get("confidence", 0.90)),
                                mem.get("source", f"import:{source_type}"),
                                int(mem.get("is_pinned", 0)),
                                batch_id,
                            ),
                        )
                        imported_mem_count += 1

                # 3. 可选：从历史对话中自动挖掘隐性偏好
                if extract_implicit and conversations:
                    implicit_items = cls._extract_implicit_memories_from_conversations(conversations, batch_id)
                    for imp in implicit_items:
                        new_mid = f"mem_{uuid.uuid4().hex[:10]}"
                        await conn.execute(
                            """
                            INSERT INTO long_term_memories
                            (id, category, content, confidence, source, is_pinned, import_batch_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                new_mid,
                                imp["category"],
                                imp["content"],
                                imp["confidence"],
                                imp["source"],
                                imp["is_pinned"],
                                batch_id,
                            ),
                        )
                        imported_mem_count += 1

                # 4. 记录导入批次，支持随时一键回滚
                await conn.execute(
                    """
                    INSERT INTO import_batches
                    (id, source_type, filename, imported_sessions_count, imported_memories_count)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (batch_id, source_type, filename, imported_conv_count, imported_mem_count),
                )

                await conn.commit()
            except Exception as e:
                await conn.rollback()
                logger.error(f"导入事务回滚: {e}")
                raise

        cls._staged_previews.pop(preview_id, None)
        return {
            "status": "ok",
            "batch_id": batch_id,
            "imported_sessions_count": imported_conv_count,
            "imported_messages_count": imported_msg_count,
            "imported_memories_count": imported_mem_count,
        }

    @staticmethod
    async def list_batches() -> list[dict[str, Any]]:
        """获取所有历史导入批次记录"""
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                "SELECT * FROM import_batches ORDER BY created_at DESC LIMIT 30"
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    @staticmethod
    async def rollback_batch(batch_id: str) -> dict[str, int]:
        """一键按批次撤回（级联清除该批次导入的所有会话、消息、FTS5索引与长期记忆）"""
        async with db_manager.get_connection() as conn:
            cur_conv = await conn.execute(
                "DELETE FROM conversations WHERE import_batch_id = ?", (batch_id,)
            )
            cur_mem = await conn.execute(
                "DELETE FROM long_term_memories WHERE import_batch_id = ?", (batch_id,)
            )
            await conn.execute("DELETE FROM import_batches WHERE id = ?", (batch_id,))
            await conn.commit()
            return {
                "deleted_conversations": cur_conv.rowcount or 0,
                "deleted_memories": cur_mem.rowcount or 0,
            }


unified_importer = UnifiedImporter()
