"""
企业微信 / 微信客服官方渠道适配器（Phase 2）。
支持：
1. 官方回调 URL 校验（SHA1 签名校验 + AES-256-CBC 解密 echostr）；
2. 回调 XML（加密/明文）与 JSON 联调双模式入站解析；
3. 自动缓存企微 access_token 并异步逐片主动回推消息（彻底规避 5 秒同步回调超时）。
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import struct
import time
import xml.etree.ElementTree as ET
from typing import Any

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ...core.logging_config import logger
from ..base import BaseChannelAdapter, InboundMessage, OutboundMessage

# 进程级 access_token 缓存: (corpid, corpsecret) -> (token, expire_ts)
_WECOM_TOKEN_CACHE: dict[tuple[str, str], tuple[str, float]] = {}


def compute_wecom_signature(token: str, timestamp: str, nonce: str, encrypt_msg: str) -> str:
    """计算企业微信官方 SHA1 消息签名。"""
    parts = sorted([token or "", timestamp or "", nonce or "", encrypt_msg or ""])
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()


def decrypt_wecom_message(encoding_aes_key: str, encrypt_b64: str, expected_corpid: str = "") -> str:
    """使用企业微信官方 AES-256-CBC 规范解密回调密文并校验 CorpID。"""
    key_b64 = encoding_aes_key.strip()
    if len(key_b64) == 43:
        key_b64 += "="
    aes_key = base64.b64decode(key_b64)
    if len(aes_key) != 32:
        raise ValueError("EncodingAESKey 长度不合法，解码后必须为 32 字节")

    iv = aes_key[:16]
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    raw_plain = decryptor.update(base64.b64decode(encrypt_b64)) + decryptor.finalize()

    # 去除 PKCS#7 填充
    pad_len = raw_plain[-1]
    if pad_len < 1 or pad_len > 32:
        raise ValueError("企微密文 PKCS#7 填充校验失败")
    unpadded = raw_plain[:-pad_len]

    # 结构：16 字节随机数 + 4 字节网络序明文长度 + 明文 + CorpID
    if len(unpadded) < 20:
        raise ValueError("企微解密明文长度异常")
    msg_len = struct.unpack("!I", unpadded[16:20])[0]
    msg_bytes = unpadded[20 : 20 + msg_len]
    corp_bytes = unpadded[20 + msg_len :]
    if expected_corpid and corp_bytes.decode("utf-8", errors="ignore") != expected_corpid:
        raise ValueError("企微密文 CorpID 校验不匹配")
    return msg_bytes.decode("utf-8")


def encrypt_wecom_message(encoding_aes_key: str, plain_text: str, corpid: str) -> str:
    """按企业微信规范加密明文（主要用于单元测试与回调联调）。"""
    key_b64 = encoding_aes_key.strip()
    if len(key_b64) == 43:
        key_b64 += "="
    aes_key = base64.b64decode(key_b64)
    iv = aes_key[:16]

    msg_bytes = plain_text.encode("utf-8")
    corp_bytes = corpid.encode("utf-8")
    body = secrets.token_bytes(16) + struct.pack("!I", len(msg_bytes)) + msg_bytes + corp_bytes
    pad_len = 32 - (len(body) % 32)
    padded = body + bytes([pad_len] * pad_len)

    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(encrypted).decode("ascii")


def _parse_xml_to_dict(xml_str: str) -> dict[str, str]:
    root = ET.fromstring(xml_str.strip())
    return {child.tag: (child.text or "") for child in root}


class WeComChannelAdapter(BaseChannelAdapter):
    """企业微信自建应用 / 微信客服适配器。"""

    channel_type = "wecom"
    display_name = "企业微信 / 微信客服"
    default_mode = "async_push"
    max_chunk_chars = 1500

    def verify_url_challenge(
        self,
        *,
        msg_signature: str,
        timestamp: str,
        nonce: str,
        echostr: str,
        channel_config: dict[str, Any],
    ) -> str:
        """处理企微后台配置回调 URL 时的 GET 验签与解密，返回明文 echostr。"""
        token = str(channel_config.get("token") or "").strip()
        aes_key = str(channel_config.get("encoding_aes_key") or "").strip()
        corpid = str(channel_config.get("corpid") or "").strip()
        if not token or not aes_key:
            raise ValueError("当前链接尚未配置企微 Token 与 EncodingAESKey")

        expected_sig = compute_wecom_signature(token, timestamp, nonce, echostr)
        if expected_sig != (msg_signature or "").strip():
            raise ValueError("企微回调 URL 签名校验失败")
        return decrypt_wecom_message(aes_key, echostr, expected_corpid=corpid)

    async def parse_inbound(
        self,
        *,
        endpoint_token: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        link_config: dict[str, Any] | None = None,
    ) -> InboundMessage:
        cfg = (link_config or {}).get("channel_config") or {}
        raw_xml = str(payload.get("_raw_xml") or "").strip()

        if raw_xml:
            xml_dict = _parse_xml_to_dict(raw_xml)
            # 若为加密 XML，先验签并解密内层 XML
            if xml_dict.get("Encrypt"):
                token = str(cfg.get("token") or "").strip()
                aes_key = str(cfg.get("encoding_aes_key") or "").strip()
                corpid = str(cfg.get("corpid") or "").strip()
                q_params = payload.get("_query_params") or {}
                if token and q_params.get("msg_signature"):
                    sig = compute_wecom_signature(
                        token,
                        str(q_params.get("timestamp") or ""),
                        str(q_params.get("nonce") or ""),
                        xml_dict["Encrypt"],
                    )
                    if sig != str(q_params.get("msg_signature")):
                        raise ValueError("企微入站消息签名校验失败")
                inner_xml = decrypt_wecom_message(aes_key, xml_dict["Encrypt"], expected_corpid=corpid)
                xml_dict = _parse_xml_to_dict(inner_xml)

            content = str(xml_dict.get("Content") or "").strip()
            if not content:
                raise ValueError("忽略非文本或空内容企微事件")
            sender_id = str(xml_dict.get("FromUserName") or "wecom_user").strip()
            msg_id = str(xml_dict.get("MsgId") or f"wc_{secrets.token_hex(8)}").strip()
            return InboundMessage(
                channel_type=self.channel_type,
                endpoint_token=endpoint_token,
                external_msg_id=msg_id,
                sender_id=sender_id,
                sender_name=sender_id,
                chat_id=sender_id,
                text=content,
                reply_context={"touser": sender_id, "agentid": xml_dict.get("AgentID") or cfg.get("agent_id")},
                raw_payload=xml_dict,
            )

        # 兼容在线模拟 JSON 联调格式
        text = str(payload.get("text") or payload.get("message") or payload.get("content") or "").strip()
        if not text:
            raise ValueError("企微入站消息内容不能为空")
        sender_id = str(payload.get("sender_id") or (link_config or {}).get("target_user_id") or "wecom_user").strip()
        sender_name = str(payload.get("sender_name") or (link_config or {}).get("target_user_label") or sender_id).strip()
        msg_id = str(payload.get("external_msg_id") or payload.get("msg_id") or f"wc_{secrets.token_hex(8)}").strip()
        return InboundMessage(
            channel_type=self.channel_type,
            endpoint_token=endpoint_token,
            external_msg_id=msg_id,
            sender_id=sender_id,
            sender_name=sender_name,
            chat_id=str(payload.get("chat_id") or sender_id),
            text=text,
            reply_context={"touser": sender_id, "agentid": cfg.get("agent_id")},
            raw_payload=payload,
        )

    async def _get_access_token(self, corpid: str, corpsecret: str) -> str:
        cache_key = (corpid, corpsecret)
        cached = _WECOM_TOKEN_CACHE.get(cache_key)
        now = time.time()
        if cached and cached[1] > now + 60:
            return cached[0]

        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(
                "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                params={"corpid": corpid, "corpsecret": corpsecret},
            )
            data = resp.json()
            if data.get("errcode") != 0 or not data.get("access_token"):
                raise ValueError(f"获取企微 access_token 失败: {data.get('errmsg')}")
            token = str(data["access_token"])
            expires_in = int(data.get("expires_in") or 7200)
            _WECOM_TOKEN_CACHE[cache_key] = (token, now + expires_in)
            return token

    async def send_reply(
        self,
        *,
        inbound: InboundMessage,
        outbound: OutboundMessage,
        link_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cfg = (link_config or {}).get("channel_config") or {}
        corpid = str(cfg.get("corpid") or "").strip()
        corpsecret = str(cfg.get("corpsecret") or "").strip()
        agentid = str(inbound.reply_context.get("agentid") or cfg.get("agent_id") or "").strip()
        touser = str(inbound.reply_context.get("touser") or inbound.sender_id).strip()

        if not corpid or not corpsecret or not agentid:
            return {"pushed": False, "mode": "unconfigured_inline_only"}

        try:
            access_token = await self._get_access_token(corpid, corpsecret)
            sent_chunks = 0
            async with httpx.AsyncClient(timeout=15.0) as client:
                for chunk in (outbound.chunks or [outbound.formatted_text]):
                    if not chunk:
                        continue
                    r = await client.post(
                        "https://qyapi.weixin.qq.com/cgi-bin/message/send",
                        params={"access_token": access_token},
                        json={
                            "touser": touser,
                            "msgtype": "text",
                            "agentid": int(agentid) if agentid.isdigit() else agentid,
                            "text": {"content": chunk},
                        },
                    )
                    rj = r.json()
                    if rj.get("errcode") == 0:
                        sent_chunks += 1
                    else:
                        logger.warning(f"[WeComAdapter] 推送分片失败: {rj}")
            return {"pushed": sent_chunks > 0, "sent_chunks": sent_chunks}
        except Exception as err:
            logger.warning(f"[WeComAdapter] 主动回推异常: {err}")
            return {"pushed": False, "error": str(err)}

    async def get_connection_status(
        self, link_config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        cfg = (link_config or {}).get("channel_config") or {}
        configured = bool(cfg.get("corpid") and cfg.get("corpsecret") and cfg.get("agent_id"))
        return {
            "channel_type": self.channel_type,
            "connected": configured,
            "mode": self.default_mode,
            "configured_fields": {
                "corpid": bool(cfg.get("corpid")),
                "corpsecret": bool(cfg.get("corpsecret")),
                "agent_id": bool(cfg.get("agent_id")),
                "token": bool(cfg.get("token")),
                "encoding_aes_key": bool(cfg.get("encoding_aes_key")),
            },
        }

