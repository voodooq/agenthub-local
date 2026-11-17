#!/usr/bin/env python3
"""
通用邮件管理客户端工具 (支持 163、QQ、Gmail、Outlook 等标准 IMAP/SMTP 协议)
基于 Python 标准库 imaplib、email、smtplib，完全自包含，无第三方库依赖。
"""
import imaplib
import smtplib
import email
from email.header import decode_header
import argparse
import sys
import json

SERVERS = {
    "163": {"imap": "imap.163.com", "port": 993},
    "qq": {"imap": "imap.qq.com", "port": 993},
    "outlook": {"imap": "outlook.office365.com", "port": 993},
    "gmail": {"imap": "imap.gmail.com", "port": 993},
}

def decode_mime_words(raw_header):
    if not raw_header:
        return ""
    decoded_parts = decode_header(raw_header)
    result = []
    for text, encoding in decoded_parts:
        if isinstance(text, bytes):
            result.append(text.decode(encoding or "utf-8", errors="ignore"))
        else:
            result.append(str(text))
    return "".join(result)

def check_connection(service: str, user: str, password: str) -> dict:
    srv = SERVERS.get(service.lower(), {"imap": f"imap.{service}.com", "port": 993})
    try:
        mail = imaplib.IMAP4_SSL(srv["imap"], srv["port"], timeout=10.0)
        mail.login(user, password)
        status, counts = mail.select("INBOX")
        mail.logout()
        return {"status": "ok", "message": f"成功连接至 {service}，收件箱状态: {status}"}
    except Exception as e:
        return {"status": "error", "message": f"连接失败: {str(e)}"}

def main():
    parser = argparse.ArgumentParser(description="AgentHub 邮件管理工具")
    parser.add_argument("--test", action="store_true", help="测试连接")
    parser.add_argument("--service", default="163", help="服务商 (163/qq/outlook/gmail)")
    parser.add_argument("--user", default="", help="邮箱账号")
    parser.add_argument("--password", default="", help="授权码/密码")
    
    args = parser.parse_args()
    if args.test:
        if not args.user or not args.password:
            print(json.dumps({"status": "error", "message": "需提供 --user 和 --password"}, ensure_ascii=False))
            sys.exit(1)
        res = check_connection(args.service, args.user, args.password)
        print(json.dumps(res, ensure_ascii=False))

if __name__ == "__main__":
    main()
