"""
通用对象存储云端适配器 (S3 / 阿里云 OSS / MinIO Provider)
基于纯 Python 标准库实现 S3 / 阿里云 OSS 兼容的 REST API 请求与列表流式抽取：
- 零第三方重型 boto3 依赖；
- 智能适配阿里云 OSS (HMAC-SHA1) 与 AWS S3 / MinIO (AWS4-HMAC-SHA256)；
- 智能清洗 Virtual-Hosted 与 Path-Style 域名，彻底杜绝 Endpoint 与 Bucket 重复拼接引发 400 异常；
- 增量 ETag 与流式读取（阅后即焚）。
"""
import base64
from datetime import datetime, timezone
import email.utils
import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
import httpx

from ..core.logging_config import logger
from .base import CloudFileMeta, CloudVFSProvider


class OSSProvider(CloudVFSProvider):
    """S3 / 阿里云 OSS / MinIO 对象存储适配器。"""

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
        self.raw_endpoint = endpoint.strip().rstrip("/")
        self.bucket = bucket.strip()
        self.access_key = access_key.strip()
        self.secret_key = secret_key.strip()
        self.prefix = prefix.strip("/")
        self.is_aliyun = "aliyuncs.com" in self.raw_endpoint.lower()

    def _normalize_request_target(self, object_key: str = "", query: str = "") -> tuple[str, str, str]:
        """智能规范化请求 URL、Host 与规范化路径。

        Returns:
            (req_url, host_header, canonical_resource)
        """
        raw = self.raw_endpoint if "://" in self.raw_endpoint else f"https://{self.raw_endpoint}"
        parsed = urlparse(raw)
        scheme = parsed.scheme or "https"
        clean = (parsed.netloc or parsed.path).split("/")[0].strip()

        if self.is_aliyun:
            # 阿里云 OSS：Virtual Hosted 风格
            # 若用户在 endpoint 已经填写了 bucket 前缀（如 yybasket.oss-cn-beijing.aliyuncs.com）
            if clean.startswith(f"{self.bucket}."):
                host = clean
            else:
                host = f"{self.bucket}.{clean}"

            key_path = object_key.lstrip("/")
            req_url = f"{scheme}://{host}/{key_path}" if key_path else f"{scheme}://{host}/"
            if query:
                req_url += f"?{query}"
            canonical_resource = f"/{self.bucket}/{key_path}" if key_path else f"/{self.bucket}/"
            return req_url, host, canonical_resource
        else:
            # MinIO / 自建 S3：默认采用 Path Style 或 Virtual Hosted
            host = clean
            key_path = object_key.lstrip("/")
            path = f"/{self.bucket}/{key_path}" if key_path else f"/{self.bucket}"
            req_url = f"{scheme}://{host}{path}"
            if query:
                req_url += f"?{query}"
            canonical_resource = path
            return req_url, host, canonical_resource

    def _sign(self, method: str, object_key: str = "", query: str = "") -> tuple[str, dict[str, str]]:
        """根据存储平台自动选择专属签名算法（阿里云 V1 vs AWS S3 V4）。"""
        req_url, host, canon_resource = self._normalize_request_target(object_key, query)
        headers: dict[str, str] = {"Host": host}

        now = datetime.now(timezone.utc)

        if self.is_aliyun:
            # 阿里云 OSS 原生 HMAC-SHA1 签名（采用标准 RFC 2822 GMT 时间，避免系统 locale 干扰）
            date_str = email.utils.formatdate(usegmt=True)
            headers["Date"] = date_str
            # StringToSign = VERB + "\n" + Content-MD5 + "\n" + Content-Type + "\n" + Date + "\n" + CanonicalizedResource
            string_to_sign = f"{method}\n\n\n{date_str}\n{canon_resource}"
            sig = base64.b64encode(
                hmac.new(self.secret_key.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1).digest()
            ).decode("utf-8")
            headers["Authorization"] = f"OSS {self.access_key}:{sig}"
            return req_url, headers

        # AWS S3 / MinIO V4 签名
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        headers["x-amz-date"] = amz_date
        headers["x-amz-content-sha256"] = "UNSIGNED-PAYLOAD"

        canonical_uri = canon_resource or "/"
        canonical_query = query
        canonical_headers = f"host:{host}\nx-amz-content-sha256:UNSIGNED-PAYLOAD\nx-amz-date:{amz_date}\n"
        signed_headers = "host;x-amz-content-sha256;x-amz-date"

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

        headers["Authorization"] = (
            f"{algorithm} Credential={self.access_key}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}"
        )
        return req_url, headers

    @staticmethod
    def _parse_error_msg(resp_text: str, status_code: int) -> str:
        """从对象存储 XML 错误回执中提取 Code 和 Message。"""
        try:
            root = ET.fromstring(resp_text)
            code = root.findtext("Code") or ""
            message = root.findtext("Message") or ""
            if code or message:
                return f"{code}: {message} (HTTP {status_code})"
        except Exception:
            pass
        return f"HTTP {status_code}"

    async def test_connection(self) -> tuple[bool, str]:
        """测试对象存储连通性。"""
        req_url, headers = self._sign("GET", object_key="", query="max-keys=1")
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(req_url, headers=headers)
                if resp.status_code == 200:
                    platform_tag = "阿里云 OSS" if self.is_aliyun else "S3/MinIO"
                    return True, f"{platform_tag} 连通成功，存储桶【{self.bucket}】校验通过"
                err_detail = self._parse_error_msg(resp.text, resp.status_code)
                return False, f"对象存储鉴权失败: {err_detail}"
        except Exception as e:
            return False, f"连接对象存储异常: {str(e)}"

    async def list_files(self, recursive: bool = True) -> list[CloudFileMeta]:
        """列举存储桶内指定前缀的对象。"""
        query = f"prefix={self.prefix}&max-keys=200" if self.prefix else "max-keys=200"
        req_url, headers = self._sign("GET", object_key="", query=query)

        files: list[CloudFileMeta] = []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(req_url, headers=headers)
                if resp.status_code != 200:
                    err_detail = self._parse_error_msg(resp.text, resp.status_code)
                    logger.warning(f"列举 OSS 对象失败: {err_detail}")
                    return []

                root = ET.fromstring(resp.text)
                # 解析 S3 / OSS ListBucketResult
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
        req_url, headers = self._sign("GET", object_key=key)

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(req_url, headers=headers)
            if resp.status_code == 200:
                return resp.content
            err_detail = self._parse_error_msg(resp.text, resp.status_code)
            raise RuntimeError(f"OSS 下载失败: {err_detail}")
