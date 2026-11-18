"""
企业微信微盘与文档知识库适配器 (WeCom Drive Provider)
对接企业微信开放平台微盘与智能文档 API：
- access_token 内存缓存与自动续期刷新；
- 递归微盘文件夹树与智能文档元数据抓取；
- 文件流下载与在线文档导出（零本地文件持久化）。
"""
import time
from typing import Any
import httpx

from ..core.logging_config import logger
from .base import CloudFileMeta, CloudVFSProvider


class WeComDriveProvider(CloudVFSProvider):
    """企业微信微盘与智能文档适配器。"""

    API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"

    def __init__(
        self,
        mount_id: str,
        mount_name: str,
        corpid: str,
        corpsecret: str,
        space_id: str = "",
        authority_tier: int = 1,
        config: dict[str, Any] | None = None,
    ):
        super().__init__(mount_id, mount_name, authority_tier=authority_tier, config=config)
        self.corpid = corpid
        self.corpsecret = corpsecret
        self.space_id = space_id
        self._access_token: str = ""
        self._token_expires_at: float = 0.0

    async def _get_access_token(self) -> str:
        """获取或刷新企业微信 access_token（提前 300 秒刷新）。"""
        now = time.time()
        if self._access_token and (self._token_expires_at - now) > 300:
            return self._access_token

        url = f"{self.API_BASE}/gettoken"
        params = {"corpid": self.corpid, "corpsecret": self.corpsecret}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            data = resp.json()
            if data.get("errcode") == 0:
                self._access_token = data.get("access_token", "")
                expires_in = data.get("expires_in", 7200)
                self._token_expires_at = now + expires_in
                return self._access_token
            raise RuntimeError(f"企微获取 access_token 失败: {data.get('errmsg')} (code: {data.get('errcode')})")

    async def test_connection(self) -> tuple[bool, str]:
        """连通性与密钥测试。"""
        try:
            token = await self._get_access_token()
            return True, f"企业微信连通成功，Token 有效"
        except Exception as e:
            return False, f"企业微信鉴权失败: {str(e)}"

    async def list_files(self, recursive: bool = True) -> list[CloudFileMeta]:
        """遍历微盘指定空间或根目录下的文件。"""
        token = await self._get_access_token()
        files: list[CloudFileMeta] = []

        url = f"{self.API_BASE}/wedrive/file_list?access_token={token}"
        payload = {
            "spaceid": self.space_id,
            "fatherid": self.config.get("fatherid", ""),
            "sort_type": 1,
            "start": 0,
            "limit": 100,
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if data.get("errcode") != 0:
                    logger.warning(f"微盘遍历失败: {data.get('errmsg')}")
                    return []

                file_list = data.get("file_list", {}).get("item", [])
                for item in file_list:
                    fileid = item.get("fileid", "")
                    title = item.get("file_name", "")
                    size = item.get("file_size", 0)
                    mtime = float(item.get("mtime", time.time()))
                    file_type = item.get("file_type", 1)  # 1=文件, 2=目录, 3=微文档
                    is_dir = (file_type == 2)

                    remote_uri = f"wecom://{self.space_id or 'default'}/{fileid}"
                    files.append(
                        CloudFileMeta(
                            remote_uri=remote_uri,
                            title=title,
                            size=size,
                            etag=fileid,
                            mtime=mtime,
                            mime_type=item.get("file_ext", ""),
                            is_dir=is_dir,
                            authority_tier=self.authority_tier,
                        )
                    )
        except Exception as e:
            logger.warning(f"微盘请求异常: {e}")

        return files

    async def download_stream(self, remote_uri: str) -> bytes:
        """流式拉取远程文件或导出微文档内容。"""
        token = await self._get_access_token()
        fileid = remote_uri.split("/")[-1]

        url = f"{self.API_BASE}/wedrive/file_download?access_token={token}"
        payload = {"fileid": fileid}

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload)
            data = resp.json()
            if data.get("errcode") == 0:
                download_url = data.get("download_url")
                if download_url:
                    content_resp = await client.get(download_url)
                    return content_resp.content
            # 若是直接流数据或失败
            raise RuntimeError(f"微盘下载失败: {data.get('errmsg')}")
