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
        self.space_id = self._extract_space_id(space_id or (config.get("space_id", "") if config else ""))
        self._access_token: str = ""
        self._token_expires_at: float = 0.0

    @staticmethod
    def _extract_space_id(raw: str) -> str:
        """从用户输入的纯 ID 或分享链接/URL 中提取合法 space_id。"""
        if not raw:
            return ""
        raw = raw.strip()
        import re
        match = re.search(r"(s\.[a-zA-Z0-9_\.\-]+)", raw)
        if match:
            return match.group(1)
        return raw

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

    async def create_space(
        self,
        space_name: str,
        admin_userid: str = "",
        allow_all_departments: bool = True,
    ) -> tuple[bool, str, str]:
        """通过自建应用 API 快速在企业微盘中创建一个专属空间并返回 space_id。

        该应用自动成为该空间的超级管理员，解决权限隔离报 640008 的问题。
        """
        token = await self._get_access_token()
        url = f"{self.API_BASE}/wedrive/space_create?access_token={token}"
        auth_info: list[dict[str, Any]] = []
        if admin_userid:
            auth_info.append({"type": 1, "userid": admin_userid.strip(), "auth": 7})
        if allow_all_departments:
            # 允许企业全员部门(departmentid: 1)访问该空间（auth: 2=可编辑可上传）
            auth_info.append({"type": 2, "departmentid": 1, "auth": 2})

        payload: dict[str, Any] = {
            "space_name": space_name.strip() or "AgentHub-规程知识库",
            "space_sub_type": 0,
        }
        if auth_info:
            payload["auth_info"] = auth_info

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if data.get("errcode") == 0:
                    space_id = data.get("spaceid", "")
                    return True, space_id, "空间创建成功"
                return False, "", f"创建失败: {data.get('errmsg')} (code: {data.get('errcode')})"
        except Exception as e:
            return False, "", f"创建微盘空间网络异常: {str(e)}"

    async def test_connection(self) -> tuple[bool, str]:
        """连通性与密钥/空间权限测试。"""
        try:
            token = await self._get_access_token()
            if not self.space_id:
                return True, "企业微信鉴权成功(Token有效)，但请填写微盘 SpaceId 才能遍历具体文件"

            url = f"{self.API_BASE}/wedrive/file_list?access_token={token}"
            father_id = (self.config.get("fatherid") if self.config else None) or self.space_id
            payload = {"spaceid": self.space_id, "fatherid": father_id, "limit": 1}
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                errcode = data.get("errcode", 0)
                if errcode == 0:
                    return True, "企业微信连通成功，微盘目标空间鉴权通过"
                elif errcode == 640001:
                    return False, f"微盘空间不存在 (space not exist)，请检查 SpaceId ({self.space_id}) 是否正确"
                elif errcode == 640021:
                    return False, f"无效父目录 (invalid fatherid)，空间根目录 fatherid 应等于 spaceid"
                elif errcode in (2000002, 640002, 640003, 640004):
                    return False, "无微盘空间访问权限，请在企业微信微盘中将该自建应用添加为空间成员"
                return False, f"微盘校验失败: {data.get('errmsg')} (code: {errcode})"
        except Exception as e:
            return False, f"企业微信鉴权失败: {str(e)}"

    async def list_files(self, recursive: bool = True) -> list[CloudFileMeta]:
        """遍历微盘指定空间或根目录下的文件（支持多层子文件夹递归）。"""
        if not self.space_id:
            logger.warning("未配置微盘 space_id，企业微信 API 无法遍历空间文件")
            return []

        token = await self._get_access_token()
        files: list[CloudFileMeta] = []
        root_father = (self.config.get("fatherid") if self.config else None) or self.space_id
        folder_queue: list[str] = [root_father]

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                while folder_queue:
                    cur_father = folder_queue.pop(0)
                    url = f"{self.API_BASE}/wedrive/file_list?access_token={token}"
                    payload = {
                        "spaceid": self.space_id,
                        "fatherid": cur_father,
                        "sort_type": 1,
                        "start": 0,
                        "limit": 100,
                    }

                    resp = await client.post(url, json=payload)
                    data = resp.json()
                    errcode = data.get("errcode", 0)
                    if errcode != 0:
                        errmsg = data.get("errmsg", "")
                        if errcode == 640001:
                            logger.warning(f"微盘遍历失败: 空间不存在 (space not exist)。请检查 SpaceId ({self.space_id}) 是否准确。")
                        elif errcode == 640021:
                            logger.warning(f"微盘遍历失败: 无效目录 (invalid fatherid: {cur_father})。")
                        elif errcode in (2000002, 640002, 640003, 640004):
                            logger.warning("微盘遍历失败: 无空间权限。请在企微信微盘中将当前应用添加为空间成员。")
                        else:
                            logger.warning(f"微盘遍历失败: {errmsg} (code: {errcode})")
                        break

                    file_list = data.get("file_list", {}).get("item", [])
                    for item in file_list:
                        fileid = item.get("fileid", "")
                        title = item.get("file_name", "")
                        size = item.get("file_size", 0)
                        mtime = float(item.get("mtime", time.time()))
                        file_type = item.get("file_type", 1)  # 1=文件, 2=目录, 3=微文档
                        is_dir = (file_type == 2)

                        if is_dir and recursive:
                            folder_queue.append(fileid)

                        remote_uri = f"wecom://{self.space_id}/{fileid}"
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
