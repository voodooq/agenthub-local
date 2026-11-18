"""
通用 WebDAV 云端虚拟知识库适配器 (WebDAV Provider)
支持 Alist / 坚果云 / 群晖 NAS / Nextcloud 等标准 WebDAV 服务：
- PROPFIND 深度遍历目录与多级解析；
- ETag 与 Last-Modified 提取；
- 流式下载（阅后即焚）。
"""
from datetime import datetime
import re
from typing import Any
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
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.root_path = "/" + root_path.strip("/")
        self._auth = (username, password) if username else None

    async def test_connection(self) -> tuple[bool, str]:
        """连通性与 HTTP Basic 鉴权验证。"""
        url = f"{self.base_url}{self.root_path}"
        try:
            async with httpx.AsyncClient(auth=self._auth, timeout=5.0) as client:
                resp = await client.request("PROPFIND", url, headers={"Depth": "0"})
                if resp.status_code in (200, 207):
                    return True, "WebDAV 服务连接成功"
                return False, f"WebDAV 鉴权或路径异常 (HTTP {resp.status_code})"
        except Exception as e:
            return False, f"连接 WebDAV 失败: {str(e)}"

    async def list_files(self, recursive: bool = True) -> list[CloudFileMeta]:
        """通过 PROPFIND 递归遍历 WebDAV 目录树。"""
        url = f"{self.base_url}{self.root_path}"
        depth = "infinity" if recursive else "1"
        files: list[CloudFileMeta] = []

        propfind_body = """<?xml version="1.0" encoding="utf-8" ?>
        <D:propfind xmlns:D="DAV:">
            <D:prop>
                <D:displayname/>
                <D:getcontentlength/>
                <D:getlastmodified/>
                <D:getetag/>
                <D:resourcetype/>
            </D:prop>
        </D:propfind>"""

        try:
            async with httpx.AsyncClient(auth=self._auth, timeout=30.0) as client:
                resp = await client.request(
                    "PROPFIND",
                    url,
                    headers={"Depth": depth, "Content-Type": "application/xml"},
                    content=propfind_body.encode("utf-8"),
                )
                if resp.status_code not in (200, 207):
                    logger.warning(f"WebDAV PROPFIND 返回非正常状态: {resp.status_code}")
                    return []

                root = ET.fromstring(resp.text)
                # 遍历所有 response 节点
                for resp_node in root.findall(".//{DAV:}response"):
                    href_node = resp_node.find("{DAV:}href")
                    if href_node is None or not href_node.text:
                        continue
                    href = href_node.text

                    # 检查是否为目录
                    is_dir = False
                    res_type = resp_node.find(".//{DAV:}resourcetype")
                    if res_type is not None and res_type.find("{DAV:}collection") is not None:
                        is_dir = True

                    # 长度与修改时间
                    length_node = resp_node.find(".//{DAV:}getcontentlength")
                    size = int(length_node.text) if (length_node is not None and length_node.text) else 0

                    etag_node = resp_node.find(".//{DAV:}getetag")
                    etag = etag_node.text.strip('"') if (etag_node is not None and etag_node.text) else ""

                    mtime_node = resp_node.find(".//{DAV:}getlastmodified")
                    mtime_val = 0.0
                    if mtime_node is not None and mtime_node.text:
                        try:
                            dt = datetime.strptime(mtime_node.text, "%a, %d %b %Y %H:%M:%S %Z")
                            mtime_val = dt.timestamp()
                        except Exception:
                            mtime_val = 0.0

                    title = href.rstrip("/").split("/")[-1] or "root"
                    remote_uri = f"webdav://{self.mount_id}{href}"

                    files.append(
                        CloudFileMeta(
                            remote_uri=remote_uri,
                            title=title,
                            size=size,
                            etag=etag,
                            mtime=mtime_val,
                            is_dir=is_dir,
                            authority_tier=self.authority_tier,
                        )
                    )
        except Exception as e:
            logger.warning(f"WebDAV 遍历异常: {e}")

        return files

    async def download_stream(self, remote_uri: str) -> bytes:
        """流式拉取 WebDAV 文件内容。"""
        # 从 remote_uri 中还原相对路径
        path_part = remote_uri.replace(f"webdav://{self.mount_id}", "")
        url = f"{self.base_url}{path_part}"

        async with httpx.AsyncClient(auth=self._auth, timeout=60.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.content
            raise RuntimeError(f"WebDAV 下载失败: HTTP {resp.status_code}")
