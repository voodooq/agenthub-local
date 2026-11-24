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
                "Chrome/124.0.0.0 Safari/537.36 AgentHub/1.1"
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

    @staticmethod
    def _build_client_kwargs(url: str, timeout: float = 12.0) -> dict[str, Any]:
        """构造统一 httpx 客户端参数（非本地地址自动继承系统代理）。"""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36 AgentHub/1.1"
            ),
            "Accept": "application/json,text/html,text/plain;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        client_kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(timeout, connect=6.0),
            "follow_redirects": True,
            "headers": headers,
        }
        is_local = any(h in url for h in ("localhost", "127.0.0.1", "0.0.0.0", "::1"))
        if not is_local:
            proxy_url = get_system_proxy_url()
            if proxy_url:
                client_kwargs["proxy"] = proxy_url
        return client_kwargs

    @staticmethod
    def _wmo_code_to_zh(code: Any) -> str:
        """将 WMO 标准天气代码映射为简体中文描述。"""
        try:
            c = int(code)
        except Exception:
            return "未知"
        mapping = {
            0: "晴",
            1: "大部晴朗",
            2: "多云",
            3: "阴",
            45: "雾",
            48: "冻雾",
            51: "毛毛雨",
            53: "小雨",
            55: "中等细雨",
            56: "冻毛毛雨",
            57: "冻雨",
            61: "小雨",
            63: "中雨",
            65: "大雨",
            66: "冻雨",
            67: "强冻雨",
            71: "小雪",
            73: "中雪",
            75: "大雪",
            77: "米雪",
            80: "阵雨",
            81: "中阵雨",
            82: "强阵雨",
            85: "小阵雪",
            86: "大阵雪",
            95: "雷阵雨",
            96: "雷阵雨伴冰雹",
            99: "强雷阵雨伴冰雹",
        }
        return mapping.get(c, f"天气码({c})")

    @classmethod
    async def get_weather(cls, city: str, days: int = 3) -> dict[str, Any]:
        """
        免 API Key 查询城市实时天气与未来预报（Open-Meteo 主源 + wttr.in 兜底）。

        Args:
            city: 城市中文或英文名（如 北京、Shanghai）
            days: 预报天数（1~7，默认 3）
        """
        from urllib.parse import quote

        target_city = (city or "").strip()
        if not target_city:
            return {"success": False, "error": "请提供需要查询天气的城市名称。"}

        forecast_days = max(1, min(7, int(days or 3)))
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={quote(target_city)}&count=1&language=zh&format=json"

        try:
            async with httpx.AsyncClient(**cls._build_client_kwargs(geo_url, timeout=10.0)) as client:
                geo_resp = await client.get(geo_url)
                if geo_resp.status_code == 200:
                    geo_data = geo_resp.json()
                    results = geo_data.get("results") or []
                    if results:
                        loc = results[0]
                        lat = loc.get("latitude")
                        lon = loc.get("longitude")
                        resolved_name = str(loc.get("name") or target_city)
                        admin = str(loc.get("admin1") or "")
                        display_city = (
                            f"{resolved_name}（{admin}）"
                            if admin and admin != resolved_name
                            else resolved_name
                        )
                        fc_url = (
                            f"https://api.open-meteo.com/v1/forecast"
                            f"?latitude={lat}&longitude={lon}"
                            f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m"
                            f"&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
                            f"&timezone=auto&forecast_days={forecast_days}"
                        )
                        fc_resp = await client.get(fc_url)
                        if fc_resp.status_code == 200:
                            fc = fc_resp.json()
                            cur = fc.get("current") or {}
                            daily = fc.get("daily") or {}
                            cur_desc = cls._wmo_code_to_zh(cur.get("weather_code"))
                            cur_info = {
                                "condition": cur_desc,
                                "temp_c": cur.get("temperature_2m"),
                                "apparent_temp_c": cur.get("apparent_temperature"),
                                "humidity": cur.get("relative_humidity_2m"),
                                "wind_kmh": cur.get("wind_speed_10m"),
                            }
                            dates = daily.get("time") or []
                            codes = daily.get("weather_code") or []
                            tmaxs = daily.get("temperature_2m_max") or []
                            tmins = daily.get("temperature_2m_min") or []
                            pops = daily.get("precipitation_probability_max") or []
                            daily_list: list[dict[str, Any]] = []
                            lines = [
                                f"{display_city} 实时天气：{cur_desc}，气温 {cur_info['temp_c']}°C"
                                f"（体感 {cur_info['apparent_temp_c']}°C，湿度 {cur_info['humidity']}%，风速 {cur_info['wind_kmh']} km/h）。"
                            ]
                            for idx, d_str in enumerate(dates):
                                d_desc = cls._wmo_code_to_zh(codes[idx] if idx < len(codes) else None)
                                tmax = tmaxs[idx] if idx < len(tmaxs) else None
                                tmin = tmins[idx] if idx < len(tmins) else None
                                pop = pops[idx] if idx < len(pops) else None
                                item = {
                                    "date": d_str,
                                    "condition": d_desc,
                                    "temp_min_c": tmin,
                                    "temp_max_c": tmax,
                                    "precip_prob": pop,
                                }
                                daily_list.append(item)
                                pop_note = f"，降水概率 {pop}%" if pop is not None else ""
                                lines.append(f"- {d_str}：{d_desc}，{tmin}~{tmax}°C{pop_note}")
                            return {
                                "success": True,
                                "source": "open-meteo",
                                "city": display_city,
                                "current": cur_info,
                                "daily": daily_list,
                                "summary_text": "\n".join(lines),
                            }
        except Exception as om_err:
            logger.debug(f"[WebTools] Open-Meteo 查询失败，切换 wttr.in 兜底: {om_err}")

        # 兜底源：wttr.in
        wttr_url = f"https://wttr.in/{quote(target_city)}?format=j1&lang=zh"
        try:
            async with httpx.AsyncClient(**cls._build_client_kwargs(wttr_url, timeout=8.0)) as client:
                resp = await client.get(wttr_url)
                if resp.status_code == 200:
                    data = resp.json()
                    cur_raw = (data.get("current_condition") or [{}])[0]
                    lang_zh = (cur_raw.get("lang_zh") or cur_raw.get("weatherDesc") or [{}])[0]
                    cur_desc = str(lang_zh.get("value") or "未知")
                    cur_info = {
                        "condition": cur_desc,
                        "temp_c": cur_raw.get("temp_C"),
                        "apparent_temp_c": cur_raw.get("FeelsLikeC"),
                        "humidity": cur_raw.get("humidity"),
                        "wind_kmh": cur_raw.get("windspeedKmph"),
                    }
                    daily_list = []
                    lines = [
                        f"{target_city} 实时天气：{cur_desc}，气温 {cur_info['temp_c']}°C"
                        f"（体感 {cur_info['apparent_temp_c']}°C，湿度 {cur_info['humidity']}%）。"
                    ]
                    for w_day in (data.get("weather") or [])[:forecast_days]:
                        d_str = str(w_day.get("date") or "")
                        tmin = w_day.get("mintempC")
                        tmax = w_day.get("maxtempC")
                        hourly = w_day.get("hourly") or []
                        mid_h = hourly[len(hourly) // 2] if hourly else {}
                        h_desc_obj = (mid_h.get("lang_zh") or mid_h.get("weatherDesc") or [{}])[0]
                        d_desc = str(h_desc_obj.get("value") or "")
                        daily_list.append(
                            {
                                "date": d_str,
                                "condition": d_desc,
                                "temp_min_c": tmin,
                                "temp_max_c": tmax,
                            }
                        )
                        lines.append(f"- {d_str}：{d_desc}，{tmin}~{tmax}°C")
                    return {
                        "success": True,
                        "source": "wttr.in",
                        "city": target_city,
                        "current": cur_info,
                        "daily": daily_list,
                        "summary_text": "\n".join(lines),
                    }
        except Exception as wttr_err:
            logger.warning(f"[WebTools] wttr.in 兜底查询失败 ({target_city}): {wttr_err}")

        return {
            "success": False,
            "city": target_city,
            "error": f"暂时无法连接到在线天气服务，请检查服务器网络或代理设置后重试查询 {target_city} 天气。",
        }

    @classmethod
    async def web_search(cls, query: str, max_results: int = 5) -> dict[str, Any]:
        """
        免 API Key 轻量联网搜索引擎（DuckDuckGo HTML 主源 + Bing 搜索兜底）。

        Args:
            query: 搜索关键词或问题
            max_results: 返回结果条数（1~10，默认 5）
        """
        from urllib.parse import parse_qs, unquote, urlparse

        q = (query or "").strip()
        if not q:
            return {"success": False, "error": "搜索关键词不能为空。", "results": []}

        limit = max(1, min(10, int(max_results or 5)))
        results: list[dict[str, str]] = []

        # 1. 主源：DuckDuckGo HTML 接口（较短超时以便国内环境快速降级到 Bing）
        ddg_url = "https://html.duckduckgo.com/html/"
        try:
            async with httpx.AsyncClient(**cls._build_client_kwargs(ddg_url, timeout=6.0)) as client:
                resp = await client.post(ddg_url, data={"q": q})
                if resp.status_code == 200 and resp.text:
                    link_matches = list(
                        re.finditer(
                            r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>',
                            resp.text,
                            re.IGNORECASE,
                        )
                    )
                    snip_matches = list(
                        re.finditer(
                            r'class="[^"]*result__snippet[^"]*"[^>]*>([\s\S]*?)</(?:a|div|span)>',
                            resp.text,
                            re.IGNORECASE,
                        )
                    )
                    for idx, lm in enumerate(link_matches[:limit]):
                        raw_href = html.unescape(lm.group(1)).strip()
                        if "uddg=" in raw_href:
                            qs = parse_qs(urlparse(raw_href).query)
                            if qs.get("uddg"):
                                raw_href = unquote(qs["uddg"][0])
                        elif raw_href.startswith("//"):
                            raw_href = "https:" + raw_href
                        title_txt = re.sub(r"<[^>]+>", "", html.unescape(lm.group(2))).strip()
                        snip_txt = ""
                        if idx < len(snip_matches):
                            snip_txt = re.sub(r"<[^>]+>", "", html.unescape(snip_matches[idx].group(1))).strip()
                        if title_txt or raw_href:
                            results.append({"title": title_txt, "url": raw_href, "snippet": snip_txt})
        except Exception as ddg_err:
            logger.debug(f"[WebTools] DuckDuckGo 搜索失败，尝试 Bing 兜底: {ddg_err}")

        # 2. 兜底源：cn.bing.com
        if not results:
            from urllib.parse import quote

            bing_url = f"https://cn.bing.com/search?q={quote(q)}"
            try:
                async with httpx.AsyncClient(**cls._build_client_kwargs(bing_url, timeout=12.0)) as client:
                    resp = await client.get(bing_url)
                    if resp.status_code == 200 and resp.text:
                        blocks = re.findall(r'<li[^>]*class="[^"]*\bb_algo\b[^"]*"[^>]*>([\s\S]*?)</li>', resp.text, re.IGNORECASE)
                        for blk in blocks[:limit]:
                            m_link = re.search(r'<h2[^>]*>[\s\S]*?<a[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>', blk, re.IGNORECASE)
                            if not m_link:
                                continue
                            href = html.unescape(m_link.group(1)).strip()
                            title_txt = re.sub(r"<[^>]+>", "", html.unescape(m_link.group(2))).strip()
                            m_p = re.search(r"<p[^>]*>([\s\S]*?)</p>", blk, re.IGNORECASE)
                            snip_txt = re.sub(r"<[^>]+>", "", html.unescape(m_p.group(1))).strip() if m_p else ""
                            results.append({"title": title_txt, "url": href, "snippet": snip_txt})
            except Exception as bing_err:
                logger.warning(f"[WebTools] Bing 搜索兜底失败 ({q}): {bing_err}")

        if not results:
            return {
                "success": False,
                "query": q,
                "results": [],
                "formatted": f"未检索到与「{q}」相关的公开网页结果（或当前网络受限）。",
            }

        fmt_lines = [f"联网搜索「{q}」共命中 {len(results)} 条结果："]
        for i, item in enumerate(results, 1):
            fmt_lines.append(f"{i}. {item['title']}\n   链接: {item['url']}\n   摘要: {item['snippet']}")
        return {
            "success": True,
            "query": q,
            "results": results,
            "formatted": "\n".join(fmt_lines),
        }


# 全局单例
web_tools = WebTools()

