"""
系统安全拦截网关 (Security Guard & Process Protection)
提供高危进程保护白名单、沙箱路径校验与操作权限守卫
"""
import os
import psutil
from typing import Tuple
from .logging_config import logger

# 跨平台受保护的操作系统核心与关键守护进程名称（全小写）
PROTECTED_PROCESS_NAMES = {
    # Windows 核心进程
    "system", "system idle process", "smss.exe", "csrss.exe", "wininit.exe",
    "services.exe", "lsass.exe", "svchost.exe", "explorer.exe", "dwm.exe",
    "taskmgr.exe", "winlogon.exe", "fontdrvhost.exe", "sihost.exe",
    # macOS 核心进程
    "kernel_task", "launchd", "windowserver", "finder", "dock", "coreaudiod",
    "logd", "diskarbitrationd", "notifyd", "securityd",
    # Linux 核心进程
    "systemd", "init", "kthreadd", "xorg", "wayland", "gnome-shell",
    "dbus-daemon", "pulseaudio", "pipewire",
    # AgentHub 自身核心与 Python 运行时
    "agenthub", "agenthub.exe", "geminihub", "geminihub.exe", "python", "python.exe", "uvicorn", "uvicorn.exe"
}


def is_process_safe_to_modify(pid: int, current_backend_pid: int = 0) -> Tuple[bool, str]:
    """
    检查指定 PID 的进程是否属于可安全干预（终止、降级、暂停）的对象
    
    Args:
        pid: 目标进程标识
        current_backend_pid: 当前后端进程自己的 PID（防止自杀）
        
    Returns:
        Tuple[bool, str]: (是否安全, 原因说明)
    """
    if pid <= 10:
        return False, f"PID {pid} 为系统级内核或根进程，严禁操作"

    if current_backend_pid > 0 and pid == current_backend_pid:
        return False, "禁止终止或修改 AgentHub 服务自身进程"

    try:
        proc = psutil.Process(pid)
        name_lower = (proc.name() or "").lower()

        if name_lower in PROTECTED_PROCESS_NAMES:
            return False, f"进程 [{proc.name()}] 属于受保护的操作系统核心进程白名单，已安全拦截"

        # 检查是否为当前进程的父进程或自身
        my_pid = os.getpid()
        if pid == my_pid:
            return False, "禁止干预 AgentHub 后端进程"

        try:
            parent = proc.parent()
            if parent and parent.pid == my_pid:
                # 自身派生的安全子进程，允许管理
                pass
        except Exception:
            pass

        return True, "OK"
    except psutil.NoSuchProcess:
        return False, f"目标进程 PID {pid} 已不存在"
    except psutil.AccessDenied:
        return False, f"对目标进程 PID {pid} 无操作系统权限"
    except Exception as e:
        return False, f"进程安全性校验异常: {str(e)}"

