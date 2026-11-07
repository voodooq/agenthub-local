"""
GeminiHub 本地硬件协同技能与待审批提案管理器 (Hardware Skills & Proposal Queue)
遵循人类确认锁原则 (Human-in-the-Loop)：高危动作必须生成待审批提案，由用户在界面点击确认后执行
"""
import os
import uuid
import time
import shutil
import tempfile
from typing import Any
from fastapi import HTTPException
import psutil
from ..core.logging_config import logger
from ..core.security import is_process_safe_to_modify
from ..probe.collector import hardware_probe


class ProposalManager:
    """待审批优化提案状态管理器"""

    def __init__(self) -> None:
        self._proposals: dict[str, dict[str, Any]] = {}

    def create_proposal(
        self,
        scenario: str,
        title: str,
        description: str,
        action_type: str,
        targets: list[dict[str, Any]],
        estimated_gain: str = "未知",
    ) -> dict[str, Any]:
        """创建一个全新的待审批提案"""
        prop_id = f"prop_{uuid.uuid4().hex[:8]}"
        proposal = {
            "id": prop_id,
            "scenario": scenario,
            "title": title,
            "description": description,
            "action_type": action_type,
            "targets": targets,
            "estimated_gain": estimated_gain,
            "status": "pending",  # pending | approved | rejected | failed
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "result_diff": None,
        }
        self._proposals[prop_id] = proposal
        logger.info(f"已创建硬件优化待审批提案 [{prop_id}]: {title}")
        return proposal

    def list_proposals(self, status: str | None = None) -> list[dict[str, Any]]:
        """获取提案列表，按时间倒序排列"""
        res = list(self._proposals.values())
        if status:
            res = [p for p in res if p["status"] == status]
        return sorted(res, key=lambda x: x["created_at"], reverse=True)

    def get_proposal(self, prop_id: str) -> dict[str, Any] | None:
        return self._proposals.get(prop_id)

    def reject_proposal(self, prop_id: str) -> dict[str, Any]:
        prop = self.get_proposal(prop_id)
        if not prop:
            raise HTTPException(status_code=404, detail="提案不存在")
        if prop["status"] != "pending":
            raise HTTPException(status_code=400, detail=f"提案当前状态为 {prop['status']}，无法拒绝")
        prop["status"] = "rejected"
        return prop

    async def execute_proposal(self, prop_id: str) -> dict[str, Any]:
        """人类确认后，真实物理执行提案中的各项原子动作并生成 Before/After 对比"""
        prop = self.get_proposal(prop_id)
        if not prop:
            raise HTTPException(status_code=404, detail="提案不存在")
        if prop["status"] != "pending":
            raise HTTPException(status_code=400, detail=f"提案当前处于 {prop['status']} 状态，无法重复执行")

        # 1. 记录执行前硬件快照
        before_snap = hardware_probe.collect_snapshot()
        my_pid = os.getpid()

        execution_logs: list[str] = []
        action_type = prop["action_type"]
        targets = prop.get("targets", [])

        try:
            if action_type in ("terminate_processes", "free_port"):
                for target in targets:
                    pid = int(target.get("pid", 0))
                    name = target.get("name", "unknown")
                    safe, reason = is_process_safe_to_modify(pid, current_backend_pid=my_pid)
                    if not safe:
                        execution_logs.append(f"跳过保护进程 [{name}] (PID: {pid}): {reason}")
                        continue

                    try:
                        p = psutil.Process(pid)
                        p.terminate()
                        execution_logs.append(f"已成功终止进程 [{name}] (PID: {pid})")
                    except psutil.NoSuchProcess:
                        execution_logs.append(f"进程 [{name}] (PID: {pid}) 已自然退出")
                    except Exception as err:
                        execution_logs.append(f"终止进程 [{name}] (PID: {pid}) 失败: {err}")

            elif action_type == "clean_cache":
                for target in targets:
                    cat = target.get("category", "")
                    path = target.get("path", "")
                    if path and os.path.exists(path):
                        try:
                            # 仅清理目录内部内容，保留目录本身
                            if os.path.isdir(path):
                                for item in os.listdir(path):
                                    item_path = os.path.join(path, item)
                                    if os.path.isfile(item_path) or os.path.islink(item_path):
                                        os.unlink(item_path)
                                    elif os.path.isdir(item_path):
                                        shutil.rmtree(item_path, ignore_errors=True)
                                execution_logs.append(f"已清理缓存路径: {path} ({cat})")
                        except Exception as err:
                            execution_logs.append(f"清理路径 {path} 异常: {err}")

            # 2. 等待系统回收内存与句柄
            time.sleep(0.5)

            # 3. 记录执行后硬件快照并生成复盘对比
            after_snap = hardware_probe.collect_snapshot()
            mem_before = before_snap["memory"]["percent"]
            mem_after = after_snap["memory"]["percent"]
            used_before = before_snap["memory"]["used_gb"]
            used_after = after_snap["memory"]["used_gb"]
            diff_gb = round(max(0.0, used_before - used_after), 2)

            prop["status"] = "approved"
            prop["result_diff"] = {
                "memory_before_percent": mem_before,
                "memory_after_percent": mem_after,
                "memory_freed_gb": diff_gb,
                "execution_logs": execution_logs,
                "executed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            logger.info(f"提案 [{prop_id}] 执行完成，释放内存: {diff_gb} GB")
            return prop

        except Exception as e:
            prop["status"] = "failed"
            prop["result_diff"] = {"error": str(e), "execution_logs": execution_logs}
            logger.error(f"提案 [{prop_id}] 执行异常: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"执行优化动作失败: {str(e)}")


proposal_manager = ProposalManager()


class HardwareSkills:
    """硬件与本地研发/设计环境原子调优工具集"""

    @staticmethod
    def inspect_port(port: int) -> dict[str, Any]:
        """
        排查指定本地端口占用情况，发现死锁时直接生成释放提案
        """
        occupying_procs: list[dict[str, Any]] = []
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == port and conn.status == psutil.CONN_LISTEN:
                pid = conn.pid
                if pid:
                    try:
                        p = psutil.Process(pid)
                        occupying_procs.append({
                            "pid": pid,
                            "name": p.name(),
                            "cpu_percent": p.cpu_percent(interval=None),
                            "memory_mb": round(p.memory_info().rss / (1024**2), 1),
                            "status": p.status(),
                            "create_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.create_time())),
                        })
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass

        if not occupying_procs:
            return {
                "port": port,
                "occupied": False,
                "message": f"端口 {port} 当前空闲，无任何进程监听",
                "proposal": None,
            }

        # 生成端口释放提案
        prop = proposal_manager.create_proposal(
            scenario="dev",
            title=f"释放端口 {port} 占用",
            description=f"检测到端口 {port} 正在被进程 [{occupying_procs[0]['name']}] (PID: {occupying_procs[0]['pid']}) 占用，提请终止以解除端口死锁。",
            action_type="free_port",
            targets=occupying_procs,
            estimated_gain=f"释放端口 {port} 监听权",
        )

        return {
            "port": port,
            "occupied": True,
            "message": f"端口 {port} 已被占用，已生成待审批释放方案",
            "occupying_processes": occupying_procs,
            "proposal": prop,
        }

    @staticmethod
    def propose_process_governance(
        scenario: str,
        targets: list[dict[str, Any]],
        reason: str = "发现脱离终端的僵尸高占用进程",
    ) -> dict[str, Any]:
        """
        针对指定的高内存/僵尸进程生成待审批治理单
        """
        if not targets:
            raise HTTPException(status_code=400, detail="未指定任何治理目标进程")

        total_est_mb = sum(float(t.get("memory_mb", 0)) for t in targets)
        est_gain = f"预计释放约 {round(total_est_mb / 1024, 2)} GB 物理内存"

        return proposal_manager.create_proposal(
            scenario=scenario,
            title=f"治理 {len(targets)} 个异常高资源进程",
            description=reason,
            action_type="terminate_processes",
            targets=targets,
            estimated_gain=est_gain,
        )

    @staticmethod
    def scan_cache_directories() -> list[dict[str, Any]]:
        """
        扫描系统临时文件夹与开发缓存目录的占用情况
        """
        cache_items: list[dict[str, Any]] = []

        # 1. 操作系统临时目录
        temp_dir = tempfile.gettempdir()
        if os.path.exists(temp_dir):
            cache_items.append({
                "category": "system_temp",
                "name": "系统临时文件目录 (Temp)",
                "path": temp_dir,
                "description": "系统及各类应用程序运行时产生的临时缓存文件",
            })

        # 2. 用户主目录下的 npm 缓存 (Windows: %LocalAppData%\npm-cache)
        user_home = os.path.expanduser("~")
        npm_cache = os.path.join(user_home, "AppData", "Local", "npm-cache") if os.name == "nt" else os.path.join(user_home, ".npm")
        if os.path.exists(npm_cache):
            cache_items.append({
                "category": "dev_npm",
                "name": "Node.js npm 全局包缓存",
                "path": npm_cache,
                "description": "前端项目安装依赖时下载的 tarball 与元数据缓存",
            })

        # 3. pip 缓存
        pip_cache = os.path.join(user_home, "AppData", "Local", "pip", "cache") if os.name == "nt" else os.path.join(user_home, ".cache", "pip")
        if os.path.exists(pip_cache):
            cache_items.append({
                "category": "dev_pip",
                "name": "Python pip 依赖安装缓存",
                "path": pip_cache,
                "description": "Python pip install 下载的 wheel 与源码包缓存",
            })

        return cache_items


hardware_skills = HardwareSkills()

