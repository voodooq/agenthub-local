"""
通用 WebDAV 云端虚拟知识库适配器 (WebDAV Provider)
支持 Alist / 坚果云 / 群晖 NAS / Nextcloud 等标准 WebDAV 服务：
- 内网/本地回环自动绕过系统全局网络代理，杜绝代理拦截与握手超时；
- 采用 RFC 4918 兼容的标准 BFS 广度优先目录遍历 (Depth: 1)，彻底规避 Alist 等服务对 Depth: infinity 的拒绝；
- 命名空间中立 (Namespace-Agnostic) XML 解析，自适应各服务 XML 结构；
- 中文文件名自动 URL 解码与 302 重定向直链追踪；
- 流式下载（阅后即焚）。
"""
from collections import deque
from datetime import datetime
import ipaddress
from typing import Any
from urllib.parse import quote, unquote, urlparse
import xml.etree.ElementTree as ET
import httpx

from ..core.logging_config import logger
from .base import CloudFileMeta, CloudVFSProvider


class WebDAVProvider(CloudVFSProvider):
    """标准 WebDAV 协议适配器。"""

    def __init__(
        self,
        mount_id: str,
        mount_name: str,
        base_url: str,
        username: str = "",
        password: str = "",
        root_path: str = "/",
        authority_tier: int = 2,
        config: dict[str, Any] | None = None,
    ):
        super().__init__(mount_id, mount_name, authority_tier=authority_tier, config=config)
        self.base_url = base_url.strip().rstrip("/")
        self.username = username.strip()
        self.password = password.strip()
        clean_root = "/" + root_path.strip().strip("/")
        self.root_path = clean_root if clean_root != "/" else "/"
        self._auth = (self.username, self.password) if self.username else None
        self._is_local = self._check_is_local(self.base_url)

    @staticmethod
    def _check_is_local(url_str: str) -> bool:
        """检查目标服务是否为本地回环或局域网私有地址。"""
        try:
            parsed = urlparse(url_str if "://" in url_str else f"http://{url_str}")
            hostname = (parsed.hostname or "").lower()
            if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
                return True
            # 检测私有局域网 IP (10.x, 172.16-31.x, 192.168.x)
            try:
                ip = ipaddress.ip_address(hostname)
                return ip.is_private or ip.is_loopback
            except ValueError:
                pass
            return False
        except Exception:
            return False

    def _get_http_client(self, timeout: float = 15.0) -> httpx.AsyncClient:
        """获取具备代理隔离与重定向跟踪的 AsyncClient。"""
        # 若为本地或私有网络，强制关闭环境代理 (trust_env=False)，避免本地请求被外部翻墙代理拦截
        trust_env = not self._is_local
        client_timeout = httpx.Timeout(connect=5.0, read=timeout, write=10.0, pool=5.0)
        return httpx.AsyncClient(
            auth=self._auth,
            timeout=client_timeout,
            trust_env=trust_env,
            follow_redirects=True,
        )

    @staticmethod
    def _strip_ns(tag: str) -> str:
        """剔除 XML 命名空间前缀（如 {DAV:}response -> response）。"""
        return tag.split("}")[-1].lower() if "}" in tag else tag.lower()

    @classmethod
    def _find_first_child(cls, elem: ET.Element, target_name: str) -> ET.Element | None:
        """在直接子节点中查找指定名称的节点（忽略命名空间与大小写）。"""
        target = target_name.lower()
        for child in elem:
            if cls._strip_ns(child.tag) == target:
                return child
        return None

    @classmethod
    def _find_descendant(cls, elem: ET.Element, target_name: str) -> ET.Element | None:
        """递归查找第一个匹配的子孙节点（忽略命名空间与大小写）。"""
        target = target_name.lower()
        for child in elem.iter():
            if cls._strip_ns(child.tag) == target:
                return child
        return None

    def _build_url(self, raw_path: str) -> str:
        """安全构建带中文字符与空格标准 URL 编码的 WebDAV 请求地址。"""
        # 先 unquote 避免如 %20 被重复编码为 %2520，再标准 quote 编码保留路径斜杠
        clean_p = "/" + unquote(raw_path.strip()).strip("/")
        if clean_p == "/":
            encoded_p = ""
        else:
            encoded_p = quote(clean_p, safe="/")
        base = self.base_url.rstrip("/")
        return f"{base}{encoded_p}"

    async def test_connection(self) -> tuple[bool, str]:
        """连通性与 HTTP Basic 鉴权验证。"""
        url = self._build_url(self.root_path)
        if not url.endswith("/"):
            url += "/"
        try:
            async with self._get_http_client(timeout=8.0) as client:
                resp = await client.request("PROPFIND", url, headers={"Depth": "0"})
                if resp.status_code in (200, 207):
                    return True, "WebDAV 服务连接成功"
                if resp.status_code == 401:
                    return False, "WebDAV 鉴权失败: 用户名或密码错误 (HTTP 401)"
                if resp.status_code == 403:
                    return False, "WebDAV 访问被拒绝 (HTTP 403)"
                if resp.status_code == 405:
                    # 针对用户常见手误 (/dev 与 /dav 混淆、或漏填 /dav) 的智能诊断
                    if "/dev" in self.base_url:
                        candidate = self.base_url.replace("/dev", "/dav")
                        cand_url = url.replace(self.base_url, candidate)
                        try:
                            cand_resp = await client.request("PROPFIND", cand_url, headers={"Depth": "0"})
                            if cand_resp.status_code in (200, 207, 401):
                                return False, f"WebDAV 端点路径拼写错误 (HTTP 405): 您输入的 URL 包含「/dev」，Alist 等服务的标准 WebDAV 路径为「/dav」（字母 a）。已探测到目标服务，请将 URL 修改为: {candidate}"
                        except Exception:
                            pass
                        return False, "WebDAV 服务返回 HTTP 405 (方法不被允许)。请检查: Alist 的标准 WebDAV 路径为 /dav（字母 a），而非 /dev。"
                    elif not self.base_url.endswith("/dav") and not self.base_url.endswith("/webdav"):
                        return False, "WebDAV 服务返回 HTTP 405。若您使用的是 Alist，请在端口后加上 /dav 路径（例如: http://127.0.0.1:5244/dav）。"
                return False, f"WebDAV 服务异常 (HTTP {resp.status_code})"
        except Exception as e:
            err_msg = str(e) or repr(e) or type(e).__name__
            return False, f"连接 WebDAV 失败: {err_msg}"

    async def list_files(self, recursive: bool = True) -> list[CloudFileMeta]:
        """通过 BFS 广度优先递归遍历 WebDAV 目录树 (Depth: 1 逐层展开，完美兼容 Alist/NAS)。"""
        files: list[CloudFileMeta] = []
        visited_dirs: set[str] = set()
        queue: deque[str] = deque([self.root_path])
        max_dirs = 200  # 防止死循环保护阈值

        # 标准紧凑 XML Body（无任何前置空白或换行，严格遵循 XML 1.0 语法）
        propfind_body = (
            '<?xml version="1.0" encoding="utf-8" ?>'
            '<D:propfind xmlns:D="DAV:">'
            '<D:prop>'
            '<D:displayname/>'
            '<D:getcontentlength/>'
            '<D:getlastmodified/>'
            '<D:getetag/>'
            '<D:resourcetype/>'
            '</D:prop>'
            '</D:propfind>'
        ).encode("utf-8")

        try:
            async with self._get_http_client(timeout=30.0) as client:
                while queue and len(visited_dirs) < max_dirs:
                    current_path = queue.popleft()
                    # 规范化当前目录路径
                    norm_path = "/" + current_path.strip("/")
                    if norm_path in visited_dirs:
                        continue
                    visited_dirs.add(norm_path)

                    target_url = self._build_url(norm_path)
                    if not target_url.endswith("/"):
                        target_url += "/"

                    headers = {
                        "Depth": "1",
                        "Content-Type": "application/xml; charset=utf-8",
                    }

                    try:
                        resp = await client.request("PROPFIND", target_url, headers=headers, content=propfind_body)
                        if resp.status_code not in (200, 207):
                            # 若带 body 被拒绝，尝试不带 body 发送纯 PROPFIND (RFC 4918 allprop 缺省模式)
                            resp = await client.request("PROPFIND", target_url, headers={"Depth": "1"})
                    except httpx.TimeoutException as te:
                        logger.warning(f"WebDAV 目录 [{norm_path}] PROPFIND 超时，跳过该子目录继续处理其余目录: {te}")
                        continue
                    except Exception as req_err:
                        logger.warning(f"WebDAV 目录 [{norm_path}] 请求失败: {req_err}")
                        continue

                    if resp.status_code not in (200, 207):
                        logger.warning(f"WebDAV 目录 [{norm_path}] PROPFIND 失败: HTTP {resp.status_code}")
                        continue

                    try:
                        root = ET.fromstring(resp.text)
                    except Exception as parse_err:
                        logger.warning(f"WebDAV XML 解析异常 ({norm_path}): {parse_err}")
                        continue

                    # 提取所有 response 节点
                    for resp_node in root.iter():
                        if self._strip_ns(resp_node.tag) != "response":
                            continue

                        href_node = self._find_first_child(resp_node, "href")
                        if href_node is None or not href_node.text:
                            continue

                        raw_href = href_node.text.strip()
                        # URL 解码
                        decoded_href = unquote(raw_href)

                        # 统一解析路径部分
                        parsed_href = urlparse(decoded_href)
                        href_path = parsed_href.path if parsed_href.path else decoded_href

                        # 去除末尾斜杠以比对是否为当前目录本身
                        clean_href_path = "/" + href_path.strip("/")
                        clean_current = "/" + norm_path.strip("/")
                        # 兼容 base_url 自身自带的前缀（例如 /dav）
                        base_path = urlparse(self.base_url).path.rstrip("/")
                        clean_href_sub = clean_href_path
                        if base_path and clean_href_path.startswith(base_path):
                            clean_href_sub = clean_href_path[len(base_path):]
                        clean_href_sub = "/" + clean_href_sub.strip("/")

                        if clean_href_sub == clean_current or clean_href_path == clean_current:
                            # 当前目录本身的元数据，跳过
                            continue

                        # 判断是否为目录 (resourcetype 含有 collection)
                        is_dir = False
                        res_type = self._find_descendant(resp_node, "resourcetype")
                        if res_type is not None:
                            for child in res_type:
                                if self._strip_ns(child.tag) == "collection":
                                    is_dir = True
                                    break
                        if not is_dir and raw_href.endswith("/"):
                            is_dir = True

                        if is_dir:
                            if recursive:
                                # 将子目录加入遍历队列
                                sub_dir_path = clean_href_sub if clean_href_sub else clean_href_path
                                if sub_dir_path not in visited_dirs and sub_dir_path not in queue:
                                    queue.append(sub_dir_path)
                            continue

                        # 长度与修改时间
                        length_node = self._find_descendant(resp_node, "getcontentlength")
                        size = 0
                        if length_node is not None and length_node.text:
                            try:
                                size = int(length_node.text)
                            except ValueError:
                                size = 0

                        etag_node = self._find_descendant(resp_node, "getetag")
                        etag = etag_node.text.strip('"') if (etag_node is not None and etag_node.text) else ""

                        mtime_node = self._find_descendant(resp_node, "getlastmodified")
                        mtime_val = 0.0
                        if mtime_node is not None and mtime_node.text:
                            try:
                                dt = datetime.strptime(mtime_node.text, "%a, %d %b %Y %H:%M:%S %Z")
                                mtime_val = dt.timestamp()
                            except Exception:
                                mtime_val = 0.0

                        # 规范化文件名与下载路径
                        title = clean_href_path.rstrip("/").split("/")[-1] or "file"
                        # 保持原始相对路径供下载
                        rel_path = clean_href_sub if clean_href_sub else clean_href_path
                        remote_uri = f"webdav://{self.mount_id}{rel_path}"

                        files.append(
                            CloudFileMeta(
                                remote_uri=remote_uri,
                                title=title,
                                size=size,
                                etag=etag,
                                mtime=mtime_val,
                                is_dir=False,
                                authority_tier=self.authority_tier,
                            )
                        )
        except Exception as e:
            err_msg = str(e) or repr(e) or type(e).__name__
            logger.warning(f"WebDAV 遍历异常: {err_msg}", exc_info=True)

        return files

    async def download_stream(self, remote_uri: str) -> bytes:
        """流式拉取 WebDAV 文件内容（支持 302 外部重定向与中文/空格编码）。"""
        path_part = remote_uri.replace(f"webdav://{self.mount_id}", "")
        url = self._build_url(path_part)

        async with self._get_http_client(timeout=60.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.content
            raise RuntimeError(f"WebDAV 下载失败: HTTP {resp.status_code} ({url})")
