"""
双轨网络交互与网页提纯扩展 (WebTools)
核心职责：
1. 轻量级网页 Markdown 提纯：剥离无用标签，提炼干净的 Markdown 正文，低 Token 高信噪比
2. 本地后台服务探针：秒级探测 http://localhost:<port> 或远程 API 状态、响应头与返回摘要
3. 原生自动继承系统网络代理，杜绝因代理配置不通导致的内网/外网请求阻断
"""
import html
import re
from typing import Any
import httpx

from ..core.logging_config import logger
from ..core.proxy_helper import get_system_proxy_url


class WebTools:
    """轻量级网络感知与正文提纯工具集"""

    @classmethod
    def _html_to_clean_markdown(cls, html_content: str, max_chars: int = 7000) -> str:
        """
        高信噪比 HTML 到 Markdown 轻量提纯器：
        无外部重型依赖，安全剥离脚本、样式、广告与导航，提取高价值核心正文。
        """
        if not html_content:
            return "(网页内容为空)"

        text = html_content

        # 1. 提取 <title>
        title = ""
        m_title = re.search(r"<title\b[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
        if m_title:
            title = html.unescape(m_title.group(1).strip())

        # 2. 剥离脚本、样式与无用容器
        text = re.sub(r"<(script|style|svg|noscript|iframe|nav|footer|header)\b[^>]*>[\s\S]*?</\1>", "", text, flags=re.IGNORECASE)

        # 3. 优先尝试截取 <article> 或 <main>
        m_main = re.search(r"<(article|main)\b[^>]*>([\s\S]*?)</\1>", text, re.IGNORECASE)
        if m_main:
            text = m_main.group(2)

        # 4. 转换常用排版标签为 Markdown 标记
        text = re.sub(r"<h([1-6])\b[^>]*>(.*?)</h\1>", r"\n\n#\1 \2\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<p\b[^>]*>(.*?)</p>", r"\n\n\1\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<li\b[^>]*>(.*?)</li>", r"\n- \1", text, flags=re.IGNORECASE)
        text = re.sub(r"<pre\b[^>]*>(.*?)</pre>", r"\n```\n\1\n```\n", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<code\b[^>]*>(.*?)</code>", r"`\1`", text, flags=re.IGNORECASE)
        text = re.sub(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', r"[\2](\1)", text, flags=re.IGNORECASE)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)

        # 5. 清理所有剩余 HTML 标签与实体
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)

        # 6. 清理多余空行
        clean_lines = [line.strip() for line in text.splitlines()]
        condensed: list[str] = []
        last_blank = False
        for line in clean_lines:
            if not line:
                if not last_blank:
                    condensed.append("")
                    last_blank = True
            else:
                condensed.append(line)
                last_blank = False

        result = "\n".join(condensed).strip()
        if title:
            result = f"# {title}\n\n{result}"

        # 截断过长内容保护上下文
        if len(result) > max_chars:
            result = result[:max_chars] + f"\n\n... (正文过长，已自动截取前 {max_chars} 字符) ..."

        return result or "(提取完成，未探测到有效文本内容)"

    @classmethod
    async def fetch_url(
        cls,
        url: str,
        extract_main_text: bool = True,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        """
        异步抓取目标 URL 内容或探测本地服务接口。

        Args:
            url: 目标地址（如 https://github.com/issue 或 http://localhost:8080/health）
            extract_main_text: 是否将 HTML 提纯为干净的 Markdown 正文
            timeout: 超时时间（秒）

        Returns:
            dict: {
                "status_code": HTTP 状态码,
                "content_type": 响应类型,
                "content": 提纯后的 Markdown 正文或 JSON 字符串,
                "url": 最终重定向后的 URL
            }
        """
        target_url = (url or "").strip()
        if not target_url.startswith(("http://", "https://")):
            target_url = "http://" + target_url

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36 GeminiHub/1.0"
            ),
            "Accept": "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

        # 针对本地回路地址跳过系统代理直连，远程外网地址自动继承系统代理
        client_kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(timeout, connect=6.0),
            "follow_redirects": True,
            "headers": headers,
        }
        is_local = any(h in target_url for h in ("localhost", "127.0.0.1", "0.0.0.0", "::1"))
        if not is_local:
            proxy_url = get_system_proxy_url()
            if proxy_url:
                client_kwargs["proxy"] = proxy_url

        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                resp = await client.get(target_url)
                status_code = resp.status_code
                content_type = resp.headers.get("content-type", "")

                # 区分纯 JSON / 纯文本与 HTML
                if "application/json" in content_type:
                    body_text = resp.text[:6000]
                elif "text/html" in content_type and extract_main_text:
                    body_text = cls._html_to_clean_markdown(resp.text)
                else:
                    body_text = resp.text[:6000]

                return {
                    "status_code": status_code,
                    "content_type": content_type,
                    "content": body_text,
                    "url": str(resp.url),
                }

        except httpx.ConnectError:
            return {
                "status_code": 503,
                "content_type": "text/plain",
                "content": f"ConnectionFailed: 无法建立连接到 `{target_url}`（若为本地端口请确认服务是否已启动）。",
                "url": target_url,
            }
        except httpx.TimeoutException:
            return {
                "status_code": 504,
                "content_type": "text/plain",
                "content": f"Timeout: 访问 `{target_url}` 超过 {int(timeout)} 秒无响应。",
                "url": target_url,
            }
        except Exception as e:
            logger.warning(f"[WebTools] 抓取 URL `{target_url}` 异常: {e}")
            return {
                "status_code": 500,
                "content_type": "text/plain",
                "content": f"FetchError: 请求失败: {str(e)}",
                "url": target_url,
            }


# 全局单例
web_tools = WebTools()

