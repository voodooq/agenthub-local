"""
自适应 Embedding 路由引擎与显存感知 ONNX 硬件加速架构
Adaptive Embedding & VRAM-Aware ONNX Acceleration Architecture (v1.5.5)

实现核心机制：
1. 三级自适应路由：
   - Tier 1: 本地推理服务复用 (LocalServerEmbeddingProvider: Ollama / llama-server，零额外内存)
   - Tier 2: 显存感知轻量 ONNX 引擎 (VramAwareOnnxProvider: bge-small-zh-v1.5-int8)
   - Tier 3: 极限离线纯词法降级 (NullEmbeddingProvider: 纯 FTS5 trigram 兜底)
2. 显存感知与双会话生命周期管理 (Dual-Session Lifecycle)：
   - 常驻轻量 CPU Session：单条/小批次实时查询编码，延迟 < 10ms，零 GPU 上下文初始化开销
   - 临时 GPU 批处理 Session：空闲显存 >= 2.5GB 且大批次时按需拉起，硬限制 768MB 显存，用完即焚 (15s 闲置自动回收)
   - 异常自愈：GPU OOM 或驱动错误自动捕获并无缝平滑回退常驻 CPU 会话
"""
from __future__ import annotations

import abc
import asyncio
import gc
import hashlib
import json
import logging
import os
import platform
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

class _DummyNumPy:
    """无 numpy 环境下的类型占位与静态分析安全桩。"""
    ndarray: Any = Any
    float32: Any = float
    int64: Any = int

    @staticmethod
    def zeros(*args: Any, **kwargs: Any) -> Any:
        return []

    @staticmethod
    def array(*args: Any, **kwargs: Any) -> Any:
        return []

    @staticmethod
    def expand_dims(*args: Any, **kwargs: Any) -> Any:
        return None

    @staticmethod
    def sum(*args: Any, **kwargs: Any) -> Any:
        return 0.0

    @staticmethod
    def clip(*args: Any, **kwargs: Any) -> Any:
        return None

    @staticmethod
    def maximum(*args: Any, **kwargs: Any) -> Any:
        return None

    @staticmethod
    def concatenate(*args: Any, **kwargs: Any) -> Any:
        return []

    class linalg:
        @staticmethod
        def norm(*args: Any, **kwargs: Any) -> float:
            return 1.0


try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    np = _DummyNumPy()  # type: ignore

from ..core.logging_config import logger

DevicePolicy = Literal["auto", "cpu", "gpu", "directml"]


def normalize_l2(vectors: Any) -> Any:
    """对嵌入向量进行 L2 归一化，确保点积等价于余弦相似度。

    支持 np.ndarray、list[float]、list[list[float]] 及 array.array，
    在无 numpy 环境下完全由 Python 标准库 math 提供原生无损精度计算。
    """
    if vectors is None:
        return None

    # 1. 若为 numpy ndarray 且 numpy 库可用，采用向量化加速
    if HAS_NUMPY and np is not None and isinstance(vectors, np.ndarray):
        if vectors.size == 0:
            return vectors.astype(np.float32)
        if vectors.ndim == 1:
            norm = float(np.linalg.norm(vectors))
            if norm < 1e-9:
                return vectors.astype(np.float32)
            return (vectors / norm).astype(np.float32)
        norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
        norms = np.maximum(norms, 1e-9)
        return (vectors / norms).astype(np.float32)

    # 2. 纯 Python 原生无损处理二维嵌套列表 / 矩阵
    import math

    if isinstance(vectors, (list, tuple)) and vectors and isinstance(vectors[0], (list, tuple)):
        result = []
        for row in vectors:
            sq_sum = sum(float(x) * float(x) for x in row)
            norm = math.sqrt(sq_sum)
            if norm < 1e-9:
                result.append([float(x) for x in row])
            else:
                inv = 1.0 / norm
                result.append([float(x) * inv for x in row])
        return result

    # 3. 纯 Python 原生无损处理单条一维向量
    sq_sum = sum(float(x) * float(x) for x in vectors)
    norm = math.sqrt(sq_sum)
    if norm < 1e-9:
        return [float(x) for x in vectors]
    inv = 1.0 / norm
    return [float(x) * inv for x in vectors]


@dataclass(frozen=True)
class EmbeddingRuntimeStatus:
    """暴露给前端状态栏与诊断 API 的 Embedding 运行态快照。"""
    tier: Literal["tier1_local_server", "tier2_onnx", "tier3_lexical_fallback"]
    model_id: str
    dimension: int
    active_device: str                  # 如 "ollama:bge-m3" | "onnx:cpu" | "onnx:cuda(ephemeral)" | "none"
    device_policy: DevicePolicy
    is_available: bool
    model_downloading: bool = False
    download_progress: float = 0.0
    last_batch_latency_ms: float = 0.0


class BaseEmbeddingProvider(abc.ABC):
    """统一向量编码器抽象基类。"""

    @property
    @abc.abstractmethod
    def model_id(self) -> str:
        """模型标识。"""
        pass

    @property
    @abc.abstractmethod
    def dimension(self) -> int:
        """向量维度。"""
        pass

    @property
    @abc.abstractmethod
    def is_available(self) -> bool:
        """提供商当前是否可用。"""
        pass

    @abc.abstractmethod
    async def encode_query(self, text: str) -> Any | None:
        """单条实时查询编码（要求 < 10ms，L2 归一化 float32 一维向量或浮点列表）。"""
        pass

    @abc.abstractmethod
    async def encode_batch(self, texts: list[str], batch_size: int = 32) -> Any | None:
        """批量建索引编码（支持显存门控加速，返回 L2 归一化 float32 二维矩阵或浮点二维列表 [N, D]）。"""
        pass

    async def embed_texts(self, texts: list[str]) -> Any:
        """向下兼容历史批量接口。"""
        if not texts:
            if HAS_NUMPY and np is not None:
                return np.zeros((0, self.dimension), dtype=np.float32)
            return []
        res = await self.encode_batch(texts)
        if res is not None:
            return res
        if HAS_NUMPY and np is not None:
            return np.zeros((len(texts), self.dimension), dtype=np.float32)
        return [[0.0] * self.dimension for _ in range(len(texts))]

    @abc.abstractmethod
    def get_status(self) -> EmbeddingRuntimeStatus:
        """获取当前运行态诊断快照。"""
        pass


class NullEmbeddingProvider(BaseEmbeddingProvider):
    """极限离线纯词法降级提供商 (Tier 3)。"""

    def __init__(self) -> None:
        self._dim = 0

    @property
    def model_id(self) -> str:
        return "null_lexical_fallback"

    @property
    def dimension(self) -> int:
        return 0

    @property
    def is_available(self) -> bool:
        return False

    async def encode_query(self, text: str) -> Any | None:
        if not HAS_NUMPY or np is None:
            return None
        return np.zeros(0, dtype=np.float32)

    async def encode_batch(self, texts: list[str], batch_size: int = 32) -> Any | None:
        if not HAS_NUMPY or np is None:
            return None
        return np.zeros((len(texts), 0), dtype=np.float32)

    def get_status(self) -> EmbeddingRuntimeStatus:
        return EmbeddingRuntimeStatus(
            tier="tier3_lexical_fallback",
            model_id=self.model_id,
            dimension=0,
            active_device="none",
            device_policy="auto",
            is_available=False,
        )


class LocalServerEmbeddingProvider(BaseEmbeddingProvider):
    """Tier 1: 本地推理服务复用提供商 (Ollama / llama-server，零额外内存)。"""

    EMBEDDING_MODEL_KEYWORDS = ["embed", "bge", "nomic", "m3e", "gte", "e5", "minilm"]

    def __init__(
        self,
        server_type: Literal["ollama", "llama_server"] = "ollama",
        base_url: str = "http://127.0.0.1:11434",
        model_name: str = "bge-small",
    ):
        clean_url = base_url.rstrip("/")
        if clean_url.endswith("/v1"):
            clean_url = clean_url[:-3]
        self.server_type = server_type
        self.base_url = clean_url
        self._model_name = model_name
        self._available: bool = False
        self._dimension: int = 512
        self._last_latency_ms: float = 0.0

    @property
    def model_id(self) -> str:
        return f"{self.server_type}:{self._model_name}"

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def is_available(self) -> bool:
        return self._available

    async def probe_and_activate(self) -> bool:
        """探活本地推理端点并校验 Embedding 能力。"""
        if self.server_type == "ollama":
            return await self._probe_ollama()
        else:
            return await self._probe_llama_server()

    async def _probe_ollama(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                if resp.status_code != 200:
                    self._available = False
                    return False
                models = [m.get("name", "") for m in resp.json().get("models", [])]
                # 优先匹配已知 Embedding 特征模型
                best_model = None
                for m in models:
                    if any(kw in m.lower() for kw in self.EMBEDDING_MODEL_KEYWORDS):
                        best_model = m
                        break
                if not best_model and models:
                    # 检查当前配置的模型是否在列表中
                    if any(self._model_name in m for m in models):
                        best_model = self._model_name
                    else:
                        best_model = models[0]

                if not best_model:
                    self._available = False
                    return False

                self._model_name = best_model
                # 发起 4 字符探针请求测试 /api/embeddings 是否支持
                probe_resp = await client.post(
                    f"{self.base_url}/api/embeddings",
                    json={"model": self._model_name, "prompt": "test"},
                    timeout=3.0,
                )
                if probe_resp.status_code == 200:
                    emb = probe_resp.json().get("embedding", [])
                    if emb and len(emb) > 0:
                        self._dimension = len(emb)
                        self._available = True
                        return True
        except Exception:
            pass
        self._available = False
        return False

    async def _probe_llama_server(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/embeddings",
                    json={"model": self._model_name, "input": "test"},
                )
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    if data and "embedding" in data[0]:
                        self._dimension = len(data[0]["embedding"])
                        self._available = True
                        return True
        except Exception:
            pass
        self._available = False
        return False

    async def encode_query(self, text: str) -> Any | None:
        """实时查询编码。"""
        t0 = time.perf_counter()
        mat = await self.encode_batch([text], batch_size=1)
        self._last_latency_ms = (time.perf_counter() - t0) * 1000.0
        if mat is not None and len(mat) > 0:
            return mat[0]
        if HAS_NUMPY and np is not None:
            return np.zeros(self.dimension, dtype=np.float32)
        return [0.0] * self.dimension

    async def encode_batch(self, texts: list[str], batch_size: int = 32) -> Any | None:
        """批量编码。"""
        if not texts:
            if HAS_NUMPY and np is not None:
                return np.zeros((0, self.dimension), dtype=np.float32)
            return []

        t0 = time.perf_counter()
        vectors: list[list[float]] = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            if self.server_type == "ollama":
                for text in texts:
                    try:
                        resp = await client.post(
                            f"{self.base_url}/api/embeddings",
                            json={"model": self._model_name, "prompt": text},
                        )
                        if resp.status_code == 200:
                            vectors.append(resp.json().get("embedding", []))
                        else:
                            vectors.append([0.0] * self.dimension)
                    except Exception:
                        vectors.append([0.0] * self.dimension)
            else:
                for i in range(0, len(texts), batch_size):
                    batch = texts[i : i + batch_size]
                    try:
                        resp = await client.post(
                            f"{self.base_url}/v1/embeddings",
                            json={"model": self._model_name, "input": batch},
                        )
                        if resp.status_code == 200:
                            for item in resp.json().get("data", []):
                                vectors.append(item.get("embedding", []))
                        else:
                            vectors.extend([[0.0] * self.dimension] * len(batch))
                    except Exception:
                        vectors.extend([[0.0] * self.dimension] * len(batch))

        self._last_latency_ms = (time.perf_counter() - t0) * 1000.0
        if vectors and len(vectors[0]) > 0:
            self._dimension = len(vectors[0])
            if HAS_NUMPY and np is not None:
                mat = np.array(vectors, dtype=np.float32)
                return normalize_l2(mat)
            return normalize_l2(vectors)
        if HAS_NUMPY and np is not None:
            return np.zeros((len(texts), self.dimension), dtype=np.float32)
        return [[0.0] * self.dimension for _ in range(len(texts))]

    def get_status(self) -> EmbeddingRuntimeStatus:
        return EmbeddingRuntimeStatus(
            tier="tier1_local_server",
            model_id=self.model_id,
            dimension=self.dimension,
            active_device=f"{self.server_type}:{self._model_name}",
            device_policy="auto",
            is_available=self.is_available,
            last_batch_latency_ms=self._last_latency_ms,
        )


class SimpleBertTokenizer:
    """轻量纯 Python WordPiece Tokenizer，零原生第三方分词器依赖。"""

    def __init__(self, vocab_file: Path | None = None):
        self.vocab: dict[str, int] = {}
        self.unk_id = 100
        self.cls_id = 101
        self.sep_id = 102
        self.pad_id = 0
        if vocab_file and vocab_file.exists():
            self._load_vocab(vocab_file)

    def _load_vocab(self, vocab_file: Path) -> None:
        try:
            with open(vocab_file, "r", encoding="utf-8") as f:
                for idx, line in enumerate(f):
                    token = line.strip()
                    self.vocab[token] = idx
            self.unk_id = self.vocab.get("[UNK]", 100)
            self.cls_id = self.vocab.get("[CLS]", 101)
            self.sep_id = self.vocab.get("[SEP]", 102)
            self.pad_id = self.vocab.get("[PAD]", 0)
        except Exception as e:
            logger.warning(f"加载词表异常: {e}")

    def tokenize_text(self, text: str, max_length: int = 512) -> tuple[list[int], list[int], list[int]]:
        tokens = [self.cls_id]
        clean_text = text.lower().strip()

        cur_word = ""
        for char in clean_text:
            if "\u4e00" <= char <= "\u9fff" or not char.isalnum():
                if cur_word:
                    self._wordpiece(cur_word, tokens, max_length)
                    cur_word = ""
                if char.strip():
                    tokens.append(self.vocab.get(char, self.unk_id))
            else:
                cur_word += char
            if len(tokens) >= max_length - 1:
                break
        if cur_word and len(tokens) < max_length - 1:
            self._wordpiece(cur_word, tokens, max_length)

        tokens = tokens[: max_length - 1]
        tokens.append(self.sep_id)

        seq_len = len(tokens)
        pad_len = max_length - seq_len
        input_ids = tokens + [self.pad_id] * pad_len
        attention_mask = [1] * seq_len + [0] * pad_len
        token_type_ids = [0] * max_length
        return input_ids, attention_mask, token_type_ids

    def _wordpiece(self, word: str, tokens: list[int], max_length: int) -> None:
        if word in self.vocab:
            tokens.append(self.vocab[word])
            return
        is_bad = False
        start = 0
        sub_tokens: list[int] = []
        while start < len(word):
            end = len(word)
            cur_substr = None
            while start < end:
                substr = word[start:end]
                if start > 0:
                    substr = "##" + substr
                if substr in self.vocab:
                    cur_substr = substr
                    break
                end -= 1
            if cur_substr is None:
                is_bad = True
                break
            sub_tokens.append(self.vocab[cur_substr])
            start = end
        if is_bad:
            tokens.append(self.unk_id)
        else:
            tokens.extend(sub_tokens)


class VramAwareOnnxProvider(BaseEmbeddingProvider):
    """
    Tier 2: 显存感知型轻量 ONNX Runtime 向量编码器 (bge-small-zh-v1.5-int8)
    - 常驻轻量 CPU Session 负责毫秒级实时查询 (< 10ms)，彻底消除 CUDA 上下文初始化显存与延迟损耗；
    - 大批量建索引时探测空闲显存，安全线 >= 2.5GB 时按需拉起受限 GPU Session（硬上限 768MB），用完即焚；
    - 运行时故障自愈：GPU OOM 或驱动异常自动平滑回退常驻 CPU Session 兜底。
    """
    SAFE_VRAM_THRESHOLD_MB = 2560
    GPU_BATCH_TRIGGER_MIN_ITEMS = 64
    GPU_SESSION_IDLE_TTL_SEC = 15.0
    GPU_MEM_LIMIT_BYTES = 768 * 1024 * 1024  # 768MB 硬限额

    MODEL_DIR = Path.home() / ".agenthub" / "models" / "bge-small-zh-v1.5-int8"

    def __init__(
        self,
        model_dir: Path | None = None,
        device_policy: DevicePolicy = "auto",
        hardware_probe: Any = None,
    ) -> None:
        self._model_dir = model_dir or self.MODEL_DIR
        self._device_policy: DevicePolicy = device_policy
        self._hardware_probe = hardware_probe
        self._cpu_session: Any = None
        self._gpu_session: Any = None
        self._gpu_last_used_at: float = 0.0
        self._tokenizer: SimpleBertTokenizer | None = None
        self._lock = asyncio.Lock()
        self._last_latency_ms: float = 0.0
        self._is_downloading: bool = False
        self._download_progress: float = 0.0
        self._reaper_task: asyncio.Task | None = None

    @property
    def model_id(self) -> str:
        return "bge-small-zh-v1.5-int8"

    @property
    def dimension(self) -> int:
        return 512

    @property
    def is_available(self) -> bool:
        return HAS_NUMPY and (self._model_dir / "model_quantized.onnx").exists()

    def set_device_policy(self, policy: DevicePolicy) -> None:
        """动态调整设备策略。"""
        self._device_policy = policy
        if policy == "cpu":
            self.release_ephemeral_gpu_session()

    def _ensure_tokenizer(self) -> SimpleBertTokenizer:
        if self._tokenizer is not None:
            return self._tokenizer
        vocab_path = self._model_dir / "vocab.txt"
        if not vocab_path.exists():
            self._generate_minimal_vocab(vocab_path)
        self._tokenizer = SimpleBertTokenizer(vocab_path)
        return self._tokenizer

    def _generate_minimal_vocab(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tokens = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]"]
        with open(path, "w", encoding="utf-8") as f:
            for t in tokens:
                f.write(t + "\n")

    def _get_cpu_session(self) -> Any:
        """获取或创建常驻轻量 CPU 会话。"""
        if self._cpu_session is not None:
            return self._cpu_session

        model_path = self._model_dir / "model_quantized.onnx"
        if not model_path.exists():
            return None

        import onnxruntime as ort

        opts = ort.SessionOptions()
        # 限制线程数避免争抢主线程与前台推理
        cpu_cores = os.cpu_count() or 4
        opts.intra_op_num_threads = min(4, max(1, cpu_cores // 2))
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self._cpu_session = ort.InferenceSession(
            str(model_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        return self._cpu_session

    def _can_use_gpu_for_batch(self, item_count: int) -> bool:
        """评估当前批次是否允许启用 GPU 硬件加速。"""
        if self._device_policy == "cpu":
            return False
        if self._device_policy in ("gpu", "directml"):
            return True

        # auto 模式门控逻辑：
        # 1. 小批次直接走 CPU，避免 GPU 上下文初始化开销
        if item_count < self.GPU_BATCH_TRIGGER_MIN_ITEMS:
            return False

        # 2. 检查空闲显存红线 (>= 2560MB)
        if self._hardware_probe is None:
            try:
                from ..probe.collector import hardware_probe
                self._hardware_probe = hardware_probe
            except Exception:
                return False

        vram_info = self._hardware_probe.get_vram_status()
        if not vram_info or not vram_info.get("has_gpu"):
            return False

        free_mb = float(vram_info.get("free_vram_mb") or 0.0)
        return free_mb >= self.SAFE_VRAM_THRESHOLD_MB

    def _get_or_create_gpu_session(self) -> Any:
        """按需创建临时受限 GPU 批处理会话。"""
        if self._gpu_session is not None:
            self._gpu_last_used_at = time.time()
            return self._gpu_session

        model_path = self._model_dir / "model_quantized.onnx"
        if not model_path.exists():
            return None

        import onnxruntime as ort

        available_eps = ort.get_available_providers()
        selected_providers: list[Any] = []

        sys_name = platform.system()
        if sys_name == "Windows":
            if self._device_policy == "directml" and "DmlExecutionProvider" in available_eps:
                selected_providers.append("DmlExecutionProvider")
            elif "CUDAExecutionProvider" in available_eps:
                cuda_opts = {
                    "arena_extend_strategy": "kSameAsRequested",
                    "gpu_mem_limit": self.GPU_MEM_LIMIT_BYTES,
                }
                selected_providers.append(("CUDAExecutionProvider", cuda_opts))
            elif "DmlExecutionProvider" in available_eps:
                selected_providers.append("DmlExecutionProvider")
        elif sys_name == "Darwin" and "CoreMLExecutionProvider" in available_eps:
            selected_providers.append("CoreMLExecutionProvider")
        elif "CUDAExecutionProvider" in available_eps:
            cuda_opts = {
                "arena_extend_strategy": "kSameAsRequested",
                "gpu_mem_limit": self.GPU_MEM_LIMIT_BYTES,
            }
            selected_providers.append(("CUDAExecutionProvider", cuda_opts))

        selected_providers.append("CPUExecutionProvider")

        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        try:
            self._gpu_session = ort.InferenceSession(
                str(model_path), sess_options=opts, providers=selected_providers
            )
            self._gpu_last_used_at = time.time()
            logger.info("成功创建临时 ONNX GPU 批处理会话 (显存硬上限 768MB)")

            # 启动空闲回收后台轮询任务
            if self._reaper_task is None or self._reaper_task.done():
                self._reaper_task = asyncio.create_task(self._gpu_idle_reaper())

            return self._gpu_session
        except Exception as e:
            logger.warning(f"创建 GPU 会话失败，平滑降级为 CPU 会话: {e}")
            return None

    async def _gpu_idle_reaper(self) -> None:
        """15 秒无任务自动销毁 GPU 会话归还显存。"""
        while True:
            await asyncio.sleep(5.0)
            if self._gpu_session is not None:
                idle_sec = time.time() - self._gpu_last_used_at
                if idle_sec >= self.GPU_SESSION_IDLE_TTL_SEC:
                    self.release_ephemeral_gpu_session()
                    break
            else:
                break

    def release_ephemeral_gpu_session(self) -> None:
        """显式销毁临时 GPU 会话并触发垃圾回收。"""
        if self._gpu_session is not None:
            self._gpu_session = None
            gc.collect()
            logger.info("已释放临时 ONNX GPU 批处理会话，显存彻底归还。")

    async def encode_query(self, text: str) -> Any | None:
        """单条实时在线查询编码（常驻 CPU 会话，保证 < 10ms 且零 GPU 抢占）。"""
        if not text.strip():
            return np.zeros(self.dimension, dtype=np.float32)

        t0 = time.perf_counter()
        session = self._get_cpu_session()
        if session is None:
            return np.zeros(self.dimension, dtype=np.float32)

        tokenizer = self._ensure_tokenizer()
        inp_ids, att_mask, t_type = tokenizer.tokenize_text(text, max_length=512)

        inputs = {
            "input_ids": np.array([inp_ids], dtype=np.int64),
            "attention_mask": np.array([att_mask], dtype=np.int64),
            "token_type_ids": np.array([t_type], dtype=np.int64),
        }
        model_inputs = {inp.name for inp in session.get_inputs()}
        feed_dict = {k: v for k, v in inputs.items() if k in model_inputs}

        try:
            outputs = session.run(None, feed_dict)
            last_hidden_state = outputs[0]
            if last_hidden_state.ndim == 3:
                seq_len = last_hidden_state.shape[1]
                eff_mask = inputs["attention_mask"][:, :seq_len]
                mask = np.expand_dims(eff_mask, -1)
                sum_embeddings = np.sum(last_hidden_state * mask, axis=1)
                sum_mask = np.clip(mask.sum(axis=1), a_min=1e-9, a_max=None)
                pooled = sum_embeddings / sum_mask
            else:
                pooled = last_hidden_state
            self._last_latency_ms = (time.perf_counter() - t0) * 1000.0
            return normalize_l2(pooled)[0]
        except Exception as e:
            logger.warning(f"CPU 单条编码异常: {e}")
            return np.zeros(self.dimension, dtype=np.float32)

    async def encode_batch(self, texts: list[str], batch_size: int = 32) -> Any | None:
        """批量编码（按需启动 GPU 硬件加速并带异常自愈回退）。"""
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)

        t0 = time.perf_counter()
        use_gpu = self._can_use_gpu_for_batch(len(texts))

        session = None
        if use_gpu:
            session = self._get_or_create_gpu_session()

        # 若 GPU 会话不可用或未满足条件，使用常驻 CPU 会话
        if session is None:
            session = self._get_cpu_session()

        if session is None:
            return np.zeros((len(texts), self.dimension), dtype=np.float32)

        tokenizer = self._ensure_tokenizer()
        all_embeddings: list[Any] = []

        try:
            for i in range(0, len(texts), batch_size):
                batch_texts = texts[i : i + batch_size]
                input_ids_list: list[list[int]] = []
                attention_mask_list: list[list[int]] = []
                token_type_ids_list: list[list[int]] = []

                for t in batch_texts:
                    inp_ids, att_mask, t_type = tokenizer.tokenize_text(t, max_length=512)
                    input_ids_list.append(inp_ids)
                    attention_mask_list.append(att_mask)
                    token_type_ids_list.append(t_type)

                inputs = {
                    "input_ids": np.array(input_ids_list, dtype=np.int64),
                    "attention_mask": np.array(attention_mask_list, dtype=np.int64),
                    "token_type_ids": np.array(token_type_ids_list, dtype=np.int64),
                }
                model_inputs = {inp.name for inp in session.get_inputs()}
                feed_dict = {k: v for k, v in inputs.items() if k in model_inputs}

                outputs = session.run(None, feed_dict)
                last_hidden_state = outputs[0]
                if last_hidden_state.ndim == 3:
                    seq_len = last_hidden_state.shape[1]
                    eff_mask = inputs["attention_mask"][:, :seq_len]
                    mask = np.expand_dims(eff_mask, -1)
                    sum_embeddings = np.sum(last_hidden_state * mask, axis=1)
                    sum_mask = np.clip(mask.sum(axis=1), a_min=1e-9, a_max=None)
                    pooled = sum_embeddings / sum_mask
                else:
                    pooled = last_hidden_state
                all_embeddings.append(pooled)

            if use_gpu:
                self._gpu_last_used_at = time.time()

        except Exception as e:
            # 异常自愈：若在 GPU 模式下出错，释放 GPU 会话并在同一次调用内回退到 CPU
            logger.warning(f"ONNX 批处理执行异常 ({e})，正在自动回退常驻 CPU 会话兜底...")
            self.release_ephemeral_gpu_session()
            cpu_session = self._get_cpu_session()
            if cpu_session is not None:
                # 使用 CPU 会话重试本批次
                return await self._run_cpu_batch_fallback(texts, batch_size, tokenizer, cpu_session)
            return np.zeros((len(texts), self.dimension), dtype=np.float32)

        self._last_latency_ms = (time.perf_counter() - t0) * 1000.0
        if all_embeddings:
            mat = np.concatenate(all_embeddings, axis=0)
            return normalize_l2(mat)
        return np.zeros((len(texts), self.dimension), dtype=np.float32)

    async def _run_cpu_batch_fallback(
        self, texts: list[str], batch_size: int, tokenizer: SimpleBertTokenizer, session: Any
    ) -> Any:
        """CPU 兜底批量运算。"""
        all_embeddings: list[Any] = []
        model_inputs = {inp.name for inp in session.get_inputs()}

        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i : i + batch_size]
            input_ids_list = []
            attention_mask_list = []
            token_type_ids_list = []

            for t in batch_texts:
                inp_ids, att_mask, t_type = tokenizer.tokenize_text(t, max_length=512)
                input_ids_list.append(inp_ids)
                attention_mask_list.append(att_mask)
                token_type_ids_list.append(t_type)

            inputs = {
                "input_ids": np.array(input_ids_list, dtype=np.int64),
                "attention_mask": np.array(attention_mask_list, dtype=np.int64),
                "token_type_ids": np.array(token_type_ids_list, dtype=np.int64),
            }
            feed_dict = {k: v for k, v in inputs.items() if k in model_inputs}
            outputs = session.run(None, feed_dict)
            last_hidden_state = outputs[0]
            if last_hidden_state.ndim == 3:
                seq_len = last_hidden_state.shape[1]
                eff_mask = inputs["attention_mask"][:, :seq_len]
                mask = np.expand_dims(eff_mask, -1)
                sum_embeddings = np.sum(last_hidden_state * mask, axis=1)
                sum_mask = np.clip(mask.sum(axis=1), a_min=1e-9, a_max=None)
                pooled = sum_embeddings / sum_mask
            else:
                pooled = last_hidden_state
            all_embeddings.append(pooled)

        if all_embeddings:
            return normalize_l2(np.concatenate(all_embeddings, axis=0))
        return np.zeros((len(texts), self.dimension), dtype=np.float32)

    def get_status(self) -> EmbeddingRuntimeStatus:
        active_dev = "none"
        if self.is_available:
            if self._gpu_session is not None:
                active_dev = "onnx:gpu(ephemeral)"
            elif self._cpu_session is not None:
                active_dev = "onnx:cpu(resident)"
            else:
                active_dev = "onnx:ready"

        return EmbeddingRuntimeStatus(
            tier="tier2_onnx",
            model_id=self.model_id,
            dimension=self.dimension,
            active_device=active_dev,
            device_policy=self._device_policy,
            is_available=self.is_available,
            model_downloading=self._is_downloading,
            download_progress=self._download_progress,
            last_batch_latency_ms=self._last_latency_ms,
        )


class AdaptiveEmbeddingRouter:
    """自适应 Embedding 路由调度器。

    流水线：
    Tier 1: 优先嗅探复用本机 Ollama 或 llama-server 端点（零额外内存）；
    Tier 2: 降级为本地显存感知轻量 ONNX 模型 (VramAwareOnnxProvider)；
    Tier 3: 极限纯词法降级 (NullEmbeddingProvider)。
    """

    def __init__(self) -> None:
        self._provider: BaseEmbeddingProvider | None = None
        self._device_policy: DevicePolicy = "auto"
        self._lock = asyncio.Lock()

    def set_device_policy(self, policy: DevicePolicy) -> None:
        """设置显存与计算设备调度策略。"""
        self._device_policy = policy
        if isinstance(self._provider, VramAwareOnnxProvider):
            self._provider.set_device_policy(policy)

    async def get_provider(self) -> BaseEmbeddingProvider:
        """获取当前最优可用的 Embedding 提供商。"""
        if self._provider is not None and self._provider.is_available:
            return self._provider

        async with self._lock:
            if self._provider is not None and self._provider.is_available:
                return self._provider

            # 0. Tier 0: 优先从账号算力池读取 role_binding='embedding_only' 的节点
            try:
                from ..repository.db import db_manager
                from datetime import datetime, timezone

                now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                async with db_manager.get_connection() as conn:
                    async with conn.execute(
                        """
                        SELECT name, base_url, models_json FROM llm_providers
                        WHERE is_enabled = 1
                          AND role_binding = 'embedding_only'
                          AND (cooldown_until IS NULL OR cooldown_until <= ?)
                        ORDER BY priority ASC
                        """,
                        (now_utc_str,),
                    ) as cur:
                        embed_accounts = await cur.fetchall()

                for acc in embed_accounts:
                    base_url = (acc["base_url"] or "").rstrip("/")
                    if not base_url:
                        continue
                    models_raw = acc["models_json"] or "[]"
                    try:
                        import json as _json
                        models_list = _json.loads(models_raw) if isinstance(models_raw, str) else models_raw
                    except Exception:
                        models_list = []
                    # 自动嗅探模型名 —— 取第一个带 embed/bge/m3e 关键词的，或首个模型名
                    model_name = "bge-m3"
                    for m in models_list:
                        ml = m.lower()
                        if any(kw in ml for kw in ("embed", "bge", "m3e", "nomic", "e5")):
                            model_name = m
                            break
                    if not model_name and models_list:
                        model_name = models_list[0]

                    # 判断 server_type
                    server_type = "llama_server"
                    if "11434" in base_url or "ollama" in base_url.lower():
                        server_type = "ollama"

                    pool_provider = LocalServerEmbeddingProvider(
                        server_type=server_type,
                        base_url=base_url,
                        model_name=model_name,
                    )
                    if await pool_provider.probe_and_activate():
                        logger.info(
                            f"Embedding 路由命中 Tier 0 (算力池绑定 embedding_only): "
                            f"{acc['name']} ({base_url}, model={model_name}, dim={pool_provider.dimension})"
                        )
                        self._provider = pool_provider
                        return self._provider
            except Exception as pool_err:
                logger.debug(f"算力池 embedding_only 节点探测跳过: {pool_err}")

            # 1. 尝试 Tier 1: 本地 Ollama 探测
            ollama = LocalServerEmbeddingProvider(server_type="ollama")
            if await ollama.probe_and_activate():
                logger.info(f"Embedding 路由命中 Tier 1: Ollama ({ollama.model_id})")
                self._provider = ollama
                return self._provider

            # 2. 尝试 Tier 1: 本地 llama-server 探测
            llama_server = LocalServerEmbeddingProvider(server_type="llama_server")
            if await llama_server.probe_and_activate():
                logger.info("Embedding 路由命中 Tier 1: llama-server")
                self._provider = llama_server
                return self._provider

            # 3. 尝试 Tier 2: 本地 ONNX 推理
            onnx_prov = VramAwareOnnxProvider(device_policy=self._device_policy)
            if onnx_prov.is_available:
                logger.info(f"Embedding 路由命中 Tier 2: VramAwareOnnxProvider (policy={self._device_policy})")
                self._provider = onnx_prov
                return self._provider

            # 4. 降级到 Tier 3: 极限纯词法模式
            logger.info("Embedding 路由命中 Tier 3: NullEmbeddingProvider (离线纯词法模式)")
            self._provider = NullEmbeddingProvider()
            return self._provider

    def invalidate_cache(self) -> None:
        """失效缓存，用于重新探测外部推理引擎。"""
        self._provider = None

    def get_status(self) -> EmbeddingRuntimeStatus:
        """获取当前提供商状态快照。"""
        if self._provider is not None:
            return self._provider.get_status()
        return EmbeddingRuntimeStatus(
            tier="tier3_lexical_fallback",
            model_id="uninitialized",
            dimension=0,
            active_device="none",
            device_policy=self._device_policy,
            is_available=False,
        )


# 全局单例
embedding_router = AdaptiveEmbeddingRouter()

# 向下兼容历史别名
OllamaEmbeddingProvider = LocalServerEmbeddingProvider
LlamaServerEmbeddingProvider = LocalServerEmbeddingProvider
OnnxEmbeddingProvider = VramAwareOnnxProvider
