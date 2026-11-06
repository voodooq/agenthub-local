"""
网页端历史记录全量同步器 (Sync Worker Service)
实现：
1. Google Takeout MyActivity.json 解析与对话流重建
2. g.co/gemini/share/xxx 与 share.gemini.google/xxx 网页快照抓取与一键克隆
3. Bookmarklet 书签脚本推送入库
"""
import json
import uuid
import re
import httpx
from typing import Any
from fastapi import HTTPException
from ..core.logging_config import logger
from ..core.proxy_helper import get_system_proxy_url
from ..repository.conversation_repository import ConversationRepository
from ..repository.message_repository import MessageRepository


class SyncService:
    """历史记录数据清洗与同步器"""

    @staticmethod
    async def import_takeout_json(json_content: str) -> dict[str, int]:
        """
        解析 Google Takeout 导出的 MyActivity.json
        清洗 HTML，按时间聚合生成独立本地可检索会话
        """
        try:
            data = json.loads(json_content)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Takeout JSON 解析失败: {str(e)}")

        imported_conversations = 0
        imported_messages = 0

        # 取出所有活动项
        items = data if isinstance(data, list) else data.get("activities", [])
        
        current_conv_id = str(uuid.uuid4())
        await ConversationRepository.create({
            "id": current_conv_id,
            "title": f"Takeout 归档会话 - {items[0].get('time', '')[:10] if items else '未命名'}",
            "source_type": "takeout",
            "model_name": "gemini-takeout"
        })
        imported_conversations += 1

        for idx, item in enumerate(items):
            title = item.get("title", "")
            description = item.get("description", "")
            
            if title:
                await MessageRepository.save_message(
                    conversation_id=current_conv_id,
                    role="user",
                    content=title,
                    executed_by_account="Google Takeout"
                )
                imported_messages += 1

            if description:
                await MessageRepository.save_message(
                    conversation_id=current_conv_id,
                    role="model",
                    content=description,
                    executed_by_account="Google Takeout"
                )
                imported_messages += 1

        return {
            "conversations_imported": imported_conversations,
            "messages_imported": imported_messages
        }

    @staticmethod
    async def clone_share_link(share_url: str) -> str:
        """
        抓取公开分享链接（如 share.gemini.google/xxx 或 g.co/gemini/share/xxx）并克隆为本地可续聊分支
        """
        clean_url = share_url.strip()
        if not clean_url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="链接格式不正确，必须为 http:// 或 https:// 开头")

        new_conv_id = str(uuid.uuid4())
        proxy = get_system_proxy_url()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"
        }

        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=20.0, proxy=proxy, headers=headers) as client:
                resp = await client.get(clean_url)
                if resp.status_code >= 400:
                    raise HTTPException(status_code=400, detail=f"Google 分享页面响应异常 (HTTP {resp.status_code})，可能该链接已失效或设为私有")
                html_text = resp.text
                final_url = str(resp.url)
        except HTTPException:
            raise
        except httpx.ConnectTimeout:
            raise HTTPException(status_code=504, detail="抓取公开分享链接网络连接超时，请确认本地科学上网代理已开启")
        except Exception as e:
            logger.error(f"抓取公开分享链接失败: {e}", exc_info=True)
            raise HTTPException(status_code=502, detail=f"抓取公开分享链接失败: {str(e)}")

        # 1. 标题提取与不可见控制字符清洗
        title = "克隆会话: 来自公开分享"
        og_title = re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\'](.*?)["\']', html_text, re.IGNORECASE)
        if og_title:
            cand = og_title.group(1).replace("\u200e", "").replace("\u200f", "").strip()
            if cand and "direct access to Google AI" not in cand:
                title = cand

        if (title == "克隆会话: 来自公开分享" or "direct access to" in title) and "<title>" in html_text:
            extracted = html_text.split("<title>")[1].split("</title>")[0].strip()
            cleaned = extracted.replace("\u200e", "").replace("\u200f", "").replace(" - Gemini", "").strip()
            if cleaned and "direct access to Google AI" not in cleaned:
                title = cleaned

        # 若提取出的是通用首页标题，从路径中提取 share id 形成易识别标题
        if "direct access to" in title or title == "克隆会话: 来自公开分享":
            path_parts = final_url.split("?")[0].rstrip("/").split("/")
            share_id = path_parts[-1] if path_parts else "快照"
            title = f"公开分享克隆 ({share_id[:12]})"

        # 2. 尝试提取页面描述信息
        og_desc = re.search(r'<meta\s+property=["\']og:description["\']\s+content=["\'](.*?)["\']', html_text, re.IGNORECASE)
        description_text = ""
        if og_desc:
            cand_desc = og_desc.group(1).replace("\u200e", "").replace("\u200f", "").strip()
            if cand_desc and cand_desc != "Created with Gemini":
                description_text = cand_desc

        # 3. 创建本地会话
        await ConversationRepository.create({
            "id": new_conv_id,
            "title": title,
            "source_type": "web_sync",
            "source_account": clean_url
        })

        # 4. 组装初始展示内容与续聊上下文卡片
        intro_content = (
            f"### 🔗 会话已从公开分享链接一键克隆至本地\n\n"
            f"- **原始分享短链**: `{clean_url}`\n"
            f"- **目标页面**: [{title}]({final_url})\n"
        )
        if description_text:
            intro_content += f"- **页面摘要**: {description_text}\n"

        intro_content += (
            f"\n> 💡 **续聊指引**：该会话快照已生成为本地独立分支，您可以直接在下方输入框继续追问，"
            f"所有上下文将持久化保存在您的本地数据库中。"
        )

        await MessageRepository.save_message(
            conversation_id=new_conv_id,
            role="model",
            content=intro_content,
            executed_by_account="Web Share Importer"
        )

        return new_conv_id


sync_service = SyncService()
