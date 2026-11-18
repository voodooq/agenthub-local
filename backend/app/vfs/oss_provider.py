"""
通用对象存储云端适配器 (S3 / OSS / MinIO Provider)
基于纯 Python 标准库实现 S3 / 阿里云 OSS 兼容的 REST API 请求与列表流式抽取：
- 零第三方重型 boto3 依赖；
- 支持 MinIO / 阿里云 OSS / AWS S3 标准签名；
- 增量 ETag 与流式读取（阅后即焚）。
"""
from datetime import datetime, timezone
import hashlib
import hmac
from typing import Any
import xml.etree.ElementTree as ET
import httpx

from ..core.logging_config import logger
from .base import CloudFileMeta, CloudVFSProvider


class OSSProvider(CloudVFSProvider):
    """S3 / OSS / MinIO 对象存储适配器。"""

    def __init__(
        self,
        mount_id: str,
        mount_name: str,
        endpoint: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        prefix: str = "",
        authority_tier: int = 1,
        config: dict[str, Any] | None = None,
    ):
        super().__init__(mount_id, mount_name, authority_tier=authority_tier, config=config)
        self.endpoint = endpoint.rstrip("/")
        self.bucket = bucket
        self.access_key = access_key
        self.secret_key = secret_key
        self.prefix = prefix.strip("/")

    def _sign_s3_v4(self, method: str, path: str, query: str = "", headers: dict[str, str] | None = None) -> dict[str, str]:
        """纯 Python 标准库实现 S3 V4 签名。"""
        headers = headers or {}
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")

        host = self.endpoint.replace("http://", "").replace("https://", "")
        headers["host"] = host
        headers["x-amz-date"] = amz_date
        headers["x-amz-content-sha256"] = "UNSIGNED-PAYLOAD"

        canonical_uri = path or "/"
        canonical_query = query
        canonical_headers = "".join(f"{k.lower()}:{v}\n" for k, v in sorted(headers.items()))
        signed_headers = ";".join(k.lower() for k in sorted(headers.keys()))

        canonical_request = f"{method}\n{canonical_uri}\n{canonical_query}\n{canonical_headers}\n{signed_headers}\nUNSIGNED-PAYLOAD"

        algorithm = "AWS4-HMAC-SHA256"
        credential_scope = f"{date_stamp}/us-east-1/s3/aws4_request"
        string_to_sign = f"{algorithm}\n{amz_date}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"

        def sign(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

        k_date = sign(("AWS4" + self.secret_key).encode("utf-8"), date_stamp)
        k_region = sign(k_date, "us-east-1")
        k_service = sign(k_region, "s3")
        k_signing = sign(k_service, "aws4_request")
        signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        authorization_header = f"{algorithm} Credential={self.access_key}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}"
        headers["Authorization"] = authorization_header
        return headers

    async def test_connection(self) -> tuple[bool, str]:
        """测试对象存储连通性。"""
        url = f"{self.endpoint}/{self.bucket}?max-keys=1"
        try:
            headers = self._sign_s3_v4("GET", f"/{self.bucket}", query="max-keys=1")
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    return True, "对象存储连接成功"
                return False, f"对象存储请求异常: HTTP {resp.status_code}"
        except Exception as e:
            return False, f"连接对象存储失败: {str(e)}"

    async def list_files(self, recursive: bool = True) -> list[CloudFileMeta]:
        """列举存储桶内指定前缀的对象。"""
        url = f"{self.endpoint}/{self.bucket}"
        query = f"prefix={self.prefix}&max-keys=200" if self.prefix else "max-keys=200"
        headers = self._sign_s3_v4("GET", f"/{self.bucket}", query=query)

        files: list[CloudFileMeta] = []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(f"{url}?{query}", headers=headers)
                if resp.status_code != 200:
                    logger.warning(f"列举 OSS 对象失败: {resp.status_code}")
                    return []

                root = ET.fromstring(resp.text)
                # 解析 S3 ListBucketResult
                for content in root.findall(".//{http://s3.amazonaws.com/doc/2006-03-01/}Contents") or root.findall(".//Contents"):
                    key_node = content.find("{http://s3.amazonaws.com/doc/2006-03-01/}Key") or content.find("Key")
                    if key_node is None or not key_node.text:
                        continue
                    key = key_node.text

                    size_node = content.find("{http://s3.amazonaws.com/doc/2006-03-01/}Size") or content.find("Size")
                    size = int(size_node.text) if size_node is not None and size_node.text else 0

                    etag_node = content.find("{http://s3.amazonaws.com/doc/2006-03-01/}ETag") or content.find("ETag")
                    etag = etag_node.text.strip('"') if etag_node is not None and etag_node.text else ""

                    remote_uri = f"oss://{self.bucket}/{key}"
                    title = key.split("/")[-1]

                    files.append(
                        CloudFileMeta(
                            remote_uri=remote_uri,
                            title=title,
                            size=size,
                            etag=etag,
                            mtime=time.time(),
                            is_dir=key.endswith("/"),
                            authority_tier=self.authority_tier,
                        )
                    )
        except Exception as e:
            logger.warning(f"列举 OSS 异常: {e}")

        return files

    async def download_stream(self, remote_uri: str) -> bytes:
        """流式拉取 OSS 单个对象内容。"""
        key = remote_uri.replace(f"oss://{self.bucket}/", "")
        path = f"/{self.bucket}/{key}"
        url = f"{self.endpoint}{path}"
        headers = self._sign_s3_v4("GET", path)

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.content
            raise RuntimeError(f"OSS 下载失败: HTTP {resp.status_code}")
