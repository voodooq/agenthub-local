"""
跨平台硬件探针服务 (Cross-Platform Hardware Probe)
支持 Windows、macOS (Apple Silicon) 与 Linux 异构环境的硬件指标采集与 Top 进程分类
"""
import asyncio
import copy
import importlib
import platform
import subprocess
import threading
import time
import psutil
from typing import Any
from ..core.logging_config import logger
from ..core.runtime_paths import get_silent_subprocess_kwargs


class CrossPlatformHardwareProbe:
    """硬件监控指标采集器"""

    _SNAPSHOT_CACHE_TTL: float = 2.0

    def __init__(self) -> None:
        self.os_type: str = platform.system()
        self._snapshot_lock = threading.Lock()
        self._snapshot_cache_ts: float = 0.0
        self._snapshot_cache_data: dict[str, Any] | None = None
        # 避免每次采集重复调用 nvmlInit()，仅在进程内幂等初始化一次
        self._nvml_init_lock = threading.Lock()
        self._nvml_init_attempted: bool = False
        self._nvml_module: Any = None
        # 网络上下行瞬时速率差分基准状态
        self._net_lock = threading.Lock()
        self._last_net_ts: float = 0.0
        self._last_bytes_sent: int = 0
        self._last_bytes_recv: int = 0
        try:
            net_io = psutil.net_io_counters()
            if net_io is not None:
                self._last_net_ts = time.monotonic()
                self._last_bytes_sent = int(net_io.bytes_sent)
                self._last_bytes_recv = int(net_io.bytes_recv)
        except Exception:
            pass
        # 预热非阻塞 CPU 采样
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass

    def _ensure_nvml(self) -> Any:
        """进程级幂等初始化 pynvml，避免高频率反复初始化驱动句柄导致卡顿。"""
        if self._nvml_init_attempted:
            return self._nvml_module
        with self._nvml_init_lock:
            if not self._nvml_init_attempted:
                self._nvml_init_attempted = True
                try:
                    # pynvml 为可选 GPU 探测依赖（未列入核心依赖），按模块名动态加载以在未安装时优雅降级并避免静态导入告警
                    pynvml = importlib.import_module("pynvml")
                    pynvml.nvmlInit()
                    self._nvml_module = pynvml
                except Exception:
                    self._nvml_module = None
        return self._nvml_module

    def get_gpu_metrics(self) -> dict[str, Any]:
        """
        跨平台 GPU 与显存信息采集
        支持：
        1. NVIDIA 独显 (pynvml)
        2. Apple Silicon (统一内存架构探测)
        3. 优雅降级 (核显或未知显卡)
        """
        # 1. 尝试探测 NVIDIA GPU
        try:
            pynvml = self._ensure_nvml()
            if pynvml is not None:
                device_count = pynvml.nvmlDeviceGetCount()
                if device_count > 0:
                    handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                    raw_name = pynvml.nvmlDeviceGetName(handle)
                    name = raw_name.decode("utf-8") if isinstance(raw_name, bytes) else str(raw_name)
                    mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                    temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                    return {
                        "detected": True,
                        "vendor": "NVIDIA",
                        "name": name,
                        "load_percent": util.gpu,
                        "vram_used_mb": round(mem.used / (1024**2), 1),
                        "vram_total_mb": round(mem.total / (1024**2), 1),
                        "temperature_c": temp,
                    }
        except Exception:
            pass

        # 1.5 尝试通过 nvidia-smi CLI 无窗探测 NVIDIA 独显（兼容未安装 pynvml 的常规环境）
        try:
            res = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2.5,
                **get_silent_subprocess_kwargs(),
            )
            if res.returncode == 0 and res.stdout.strip():
                first_line = res.stdout.strip().splitlines()[0]
                parts = [p.strip() for p in first_line.split(",")]
                if len(parts) >= 5:
                    def _safe_float(val: str, default: float = 0.0) -> float:
                        try:
                            return float(val)
                        except (TypeError, ValueError):
                            return default

                    return {
                        "detected": True,
                        "vendor": "NVIDIA",
                        "name": parts[0] or "NVIDIA GPU",
                        "load_percent": round(_safe_float(parts[1]), 1),
                        "vram_used_mb": round(_safe_float(parts[2]), 1),
                        "vram_total_mb": round(_safe_float(parts[3]), 1),
                        "temperature_c": round(_safe_float(parts[4])),
                    }
        except Exception:
            pass

        # 2. 尝试探测 macOS Apple Silicon 统一内存架构
        if self.os_type == "Darwin":
            try:
                subprocess.check_output(
                    ["system_profiler", "SPDisplaysDataType"],
                    text=True,
                    timeout=2.0,
                )
                vm = psutil.virtual_memory()
                return {
                    "detected": True,
                    "vendor": "Apple Silicon",
                    "name": "Apple M-Series Unified GPU",
                    "load_percent": None,
                    "vram_used_mb": round(vm.used / (1024**2), 1),
                    "vram_total_mb": round(vm.total / (1024**2), 1),
                    "temperature_c": None,
                }
            except Exception:
                pass

        # 3. 优雅降级（核芯显卡或未接入独立显卡库）
        return {
            "detected": False,
            "vendor": "Integrated",
            "name": "集成显卡 / 系统通用显示单元",
            "load_percent": None,
            "vram_used_mb": 0.0,
            "vram_total_mb": 0.0,
            "temperature_c": None,
        }

    def get_network_metrics(self) -> dict[str, Any]:
        """采集宿主机实时网络上下行速率（KB/s）与累计收发流量（GB）。"""
        try:
            counters = psutil.net_io_counters()
            if counters is None:
                return {
                    "upload_kbps": 0.0,
                    "download_kbps": 0.0,
                    "total_sent_gb": 0.0,
                    "total_recv_gb": 0.0,
                    "packets_sent": 0,
                    "packets_recv": 0,
                }

            now = time.monotonic()
            with self._net_lock:
                if self._last_net_ts > 0.0 and now > self._last_net_ts:
                    dt = max(0.001, now - self._last_net_ts)
                    delta_sent = max(0, int(counters.bytes_sent) - self._last_bytes_sent)
                    delta_recv = max(0, int(counters.bytes_recv) - self._last_bytes_recv)
                    upload_kbps = round(delta_sent / 1024.0 / dt, 1)
                    download_kbps = round(delta_recv / 1024.0 / dt, 1)
                else:
                    upload_kbps = 0.0
                    download_kbps = 0.0
                self._last_net_ts = now
                self._last_bytes_sent = int(counters.bytes_sent)
                self._last_bytes_recv = int(counters.bytes_recv)

            return {
                "upload_kbps": max(0.0, upload_kbps),
                "download_kbps": max(0.0, download_kbps),
                "total_sent_gb": round(counters.bytes_sent / (1024**3), 2),
                "total_recv_gb": round(counters.bytes_recv / (1024**3), 2),
                "packets_sent": int(counters.packets_sent),
                "packets_recv": int(counters.packets_recv),
            }
        except Exception as e:
            logger.debug(f"网络指标采集受限: {e}")
            return {
                "upload_kbps": 0.0,
                "download_kbps": 0.0,
                "total_sent_gb": 0.0,
                "total_recv_gb": 0.0,
                "packets_sent": 0,
                "packets_recv": 0,
            }

    def get_disk_metrics(self) -> list[dict[str, Any]]:
        """获取各磁盘挂载分区的使用容量与剩余可用空间"""
        disks: list[dict[str, Any]] = []
        try:
            partitions = psutil.disk_partitions(all=False)
            for part in partitions:
                # 过滤只读或虚拟分区
                if "cdrom" in part.opts or part.fstype == "":
                    continue
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    disks.append({
                        "mount": part.mountpoint,
                        "device": part.device,
                        "fstype": part.fstype,
                        "total_gb": round(usage.total / (1024**3), 1),
                        "used_gb": round(usage.used / (1024**3), 1),
                        "free_gb": round(usage.free / (1024**3), 1),
                        "percent": usage.percent,
                    })
                except (PermissionError, OSError):
                    continue
        except Exception as e:
            logger.debug(f"磁盘指标采集受限: {e}")
        return disks

    def get_categorized_processes(self, limit: int = 15) -> list[dict[str, Any]]:
        """
        采集 Top 高资源占用进程并自动标记职能分类
        分类规则：
        - dev: 研发编译工具链 (node, python, java, docker, wsl, code, rust-analyzer, go, cargo)
        - design: 设计与图形渲染 (photoshop, illustrator, afterfx, premiere, blender, figma, comfyui)
        - general: 常规系统或办公进程
        """
        dev_keywords = {"node", "python", "java", "docker", "wsl", "code", "rust-analyzer", "go", "cargo", "git"}
        design_keywords = {"photoshop", "illustrator", "afterfx", "premiere", "blender", "figma", "comfyui", "c4d", "maya"}

        procs: list[dict[str, Any]] = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "status"]):
            try:
                info = p.info
                name_str = (info.get("name") or "").lower()
                mem_bytes = info.get("memory_info").rss if info.get("memory_info") else 0
                mem_mb = round(mem_bytes / (1024**2), 1)

                category = "general"
                if any(k in name_str for k in dev_keywords):
                    category = "dev"
                elif any(k in name_str for k in design_keywords):
                    category = "design"

                procs.append({
                    "pid": info["pid"],
                    "name": info.get("name") or "unknown",
                    "category": category,
                    "cpu_percent": round(info.get("cpu_percent") or 0.0, 1),
                    "memory_mb": mem_mb,
                    "status": info.get("status") or "running",
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        # 按内存使用量降序排序，提取 Top N
        procs.sort(key=lambda x: (x["memory_mb"], x["cpu_percent"]), reverse=True)
        return procs[:limit]

    def collect_snapshot(self, use_cache: bool = True) -> dict[str, Any]:
        """采集一份完整的宿主机系统与硬件运行快照（内置短 TTL 缓存防止并发阻塞）。

        Args:
            use_cache: 是否允许命中 2 秒内进程缓存；变更前后精细对比场景可传 False 强制现采。
        """
        now = time.monotonic()
        if (
            use_cache
            and self._snapshot_cache_data is not None
            and (now - self._snapshot_cache_ts) < self._SNAPSHOT_CACHE_TTL
        ):
            return copy.deepcopy(self._snapshot_cache_data)

        with self._snapshot_lock:
            now_locked = time.monotonic()
            if (
                use_cache
                and self._snapshot_cache_data is not None
                and (now_locked - self._snapshot_cache_ts) < self._SNAPSHOT_CACHE_TTL
            ):
                return copy.deepcopy(self._snapshot_cache_data)

            vm = psutil.virtual_memory()
            swap = psutil.swap_memory()
            freq = psutil.cpu_freq()

            snap = {
                "os": self.os_type,
                "cpu": {
                    "usage_percent": round(psutil.cpu_percent(interval=None), 1),
                    "cores_physical": psutil.cpu_count(logical=False) or 1,
                    "cores_logical": psutil.cpu_count(logical=True) or 1,
                    "frequency_mhz": round(freq.current, 0) if freq else 0,
                },
                "memory": {
                    "total_gb": round(vm.total / (1024**3), 2),
                    "used_gb": round(vm.used / (1024**3), 2),
                    "available_gb": round(vm.available / (1024**3), 2),
                    "percent": vm.percent,
                    "swap_percent": swap.percent,
                },
                "disk": self.get_disk_metrics(),
                "gpu": self.get_gpu_metrics(),
                "network": self.get_network_metrics(),
                "top_processes": self.get_categorized_processes(limit=15),
            }
            self._snapshot_cache_data = snap
            self._snapshot_cache_ts = time.monotonic()
            return copy.deepcopy(snap)

    async def collect_snapshot_async(self, use_cache: bool = True) -> dict[str, Any]:
        """在独立工作线程执行硬件快照采集，避免冻结 FastAPI 主事件循环。"""
        return await asyncio.to_thread(self.collect_snapshot, use_cache)


hardware_probe = CrossPlatformHardwareProbe()

