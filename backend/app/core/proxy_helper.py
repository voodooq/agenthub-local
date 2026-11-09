"""
网络代理自适应检测模块
自动探测 Windows 注册表、macOS scutil 与系统环境变量，
实现 Go CLI 子进程与后端 HTTP 客户端在代理网络环境下的秒级连通。
"""
import os
import re
import subprocess
import sys
from typing import Optional
from .logging_config import logger


def _detect_macos_scutil_proxy() -> Optional[str]:
    """通过 macOS 原生 scutil --proxy 检测系统 HTTPS / HTTP 代理设置。"""
    try:
        proc = subprocess.run(
            ["scutil", "--proxy"],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
        out = proc.stdout
        for prefix in ("HTTPS", "HTTP"):
            en_match = re.search(rf"{prefix}Enable\s*:\s*1\b", out)
            host_match = re.search(rf"{prefix}Proxy\s*:\s*(\S+)", out)
            port_match = re.search(rf"{prefix}Port\s*:\s*(\d+)", out)
            if en_match and host_match and port_match:
                return f"http://{host_match.group(1)}:{port_match.group(1)}"
    except Exception as e:
        logger.debug(f"读取 macOS scutil 系统代理跳过: {e}")
    return None


def get_system_proxy_url() -> Optional[str]:
    """
    自适应获取当前系统的 HTTP/S 代理地址。
    优先读取环境变量，未配置时按宿主平台读取 Windows 注册表或 macOS scutil。
    """
    for var in ["HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"]:
        val = os.environ.get(var)
        if val:
            return val if "://" in val else f"http://{val}"

    if sys.platform == "win32":
        try:
            import winreg

            reg_key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
            )
            proxy_enable, _ = winreg.QueryValueEx(reg_key, "ProxyEnable")
            if proxy_enable == 1:
                proxy_server, _ = winreg.QueryValueEx(reg_key, "ProxyServer")
                winreg.CloseKey(reg_key)
                if proxy_server:
                    # proxy_server 格式可能是 "127.0.0.1:10808" 或 "http=127.0.0.1:10808;https=127.0.0.1:10808"
                    if "=" in proxy_server:
                        parts = proxy_server.split(";")
                        for part in parts:
                            if part.startswith("http=") or part.startswith("https="):
                                addr = part.split("=")[1]
                                return f"http://{addr}" if "://" not in addr else addr
                    addr = proxy_server.strip()
                    return f"http://{addr}" if "://" not in addr else addr
            else:
                winreg.CloseKey(reg_key)
        except Exception as e:
            logger.debug(f"读取 Windows 注册表代理失败: {e}")
    elif sys.platform == "darwin":
        return _detect_macos_scutil_proxy()

    return None


def get_system_proxy_env() -> dict[str, str]:
    """生成适用于子进程的代理环境变量映射"""
    proxy_url = get_system_proxy_url()
    if not proxy_url:
        return {}

    return {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "ALL_PROXY": proxy_url,
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "all_proxy": proxy_url,
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }


def apply_system_proxy_to_process() -> None:
    """将检测到的代理写入当前 Python 进程环境变量（避免本地回环受影响）"""
    proxy_env = get_system_proxy_env()
    if proxy_env:
        for k, v in proxy_env.items():
            if k not in os.environ:
                os.environ[k] = v
        logger.info(f"已自动加载并注入系统网络代理: {proxy_env.get('HTTP_PROXY')}")

