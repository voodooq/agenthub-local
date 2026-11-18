"""
自适应 Embedding 路由引擎与多级提供商适配器 (Adaptive Embedding Router)
实现三级自适应降级：
- Tier 1: 本地推理引擎复用（Ollama / llama-server，零额外内存）
- Tier 2: 本地轻量 ONNX 运行时（bge-small-zh-v1.5-int8，按需加载，限制 2 线程）
- Tier 3: 极限离线纯词法降级（NullEmbeddingProvider，自动切换 FTS5 纯词法权重）
"""
import abc
import asyncio
import json
import os
from pathlib import Path
from typing import Any
import httpx
import numpy as np

from ..core.logging_config import logger


def normalize_l2(vectors: np.ndarray) -> np.ndarray:
    """对嵌入向量进行 L2 归一化，确保点积等价于余弦相似度。

    Args:
        vectors: 形状为 (N, D) 或 (D,) 的二维/一维 float 矩阵。

    Returns:
        np.ndarray: L2 归一化后的 float32 矩阵。
    """
    if vectors.ndim == 1:
        norm = np.linalg.norm(vectors)
        if norm < 1e-9:
            return vectors.astype(np.float32)
        return (vectors / norm).astype(np.float32)
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    norms = np.maximum(norms, 1e-9)
    return (vectors / norms).astype(np.float32)


class BaseEmbeddingProvider(abc.ABC):
    """Embedding 提供商抽象基类。"""

    def __init__(self, model_id: str, dimension: int):
        self.model_id = model_id
        self.dimension = dimension

    @property
    @abc.abstractmethod
    def is_available(self) -> bool:
        """提供商当前是否可用。"""
        pass

    @abc.abstractmethod
    async def embed_texts(self, texts: list[str]) -> np.ndarray:
        """异步批量计算文本 Embedding 向量。

        Args:
            texts: 待计算嵌入的文本列表。

        Returns:
            np.ndarray: 形状为 (N, dimension) 的 float32 矩阵，且已完成 L2 归一化。
        """
        pass


class NullEmbeddingProvider(BaseEmbeddingProvider):
    """极限离线纯词法降级提供商 (Tier 3)。"""

    def __init__(self) -> None:
        super().__init__(model_id="null_lexical_fallback", dimension=0)

    @property
    def is_available(self) -> bool:
        return False

    async def embed_texts(self, texts: list[str]) -> np.ndarray:
        """纯词法模式下返回空向量。"""
        return np.zeros((len(texts), 0), dtype=np.float32)


class OllamaEmbeddingProvider(BaseEmbeddingProvider):
    """Ollama 本地端点提供商 (Tier 1)。"""

    def __init__(self, base_url: str = "http://127.0.0.1:11434", model_name: str = "bge-small"):
        clean_url = base_url.rstrip("/")
        if clean_url.endswith("/v1"):
            clean_url = clean_url[:-3]
        self.base_url = clean_url
        self.model_name = model_name
        self._available: bool = False
        self._cached_dim: int = 512
        super().__init__(model_id=f"ollama:{model_name}", dimension=self._cached_dim)

    @property
    def is_available(self) -> bool:
        return self._available

    async def check_health(self) -> bool:
        """探测 Ollama 服务连通性并确认模型存在。"""
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                if resp.status_code == 200:
                    data = resp.json()
                    models = [m.get("name", "") for m in data.get("models", [])]
                    # 优先匹配当前 model_name，或者第一个模型
                    matched = any(self.model_name in m for m in models)
                    if not matched and models:
                        self.model_name = models[0]
                        self.model_id = f"ollama:{self.model_name}"
                    self._available = True
                    return True
        except Exception:
            pass
        self._available = False
        return False

    async def embed_texts(self, texts: list[str]) -> np.ndarray:
        """调用 Ollama /api/embeddings 接口计算向量。"""
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)

        vectors: list[list[float]] = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            for text in texts:
                try:
                    payload = {"model": self.model_name, "prompt": text}
                    resp = await client.post(f"{self.base_url}/api/embeddings", json=payload)
                    if resp.status_code == 200:
                        emb = resp.json().get("embedding", [])
                        vectors.append(emb)
                    else:
                        vectors.append([0.0] * self.dimension)
                except Exception as e:
                    logger.warning(f"Ollama embedding 失败 ({text[:30]}): {e}")
                    vectors.append([0.0] * self.dimension)

        if vectors and len(vectors[0]) > 0:
            self.dimension = len(vectors[0])
            self._cached_dim = self.dimension
        mat = np.array(vectors, dtype=np.float32)
        return normalize_l2(mat)


class LlamaServerEmbeddingProvider(BaseEmbeddingProvider):
    """llama-server 本地端点提供商 (Tier 1)。"""

    def __init__(self, base_url: str = "http://127.0.0.1:8080/v1", model_name: str = "default"):
        clean_url = base_url.rstrip("/")
        if not clean_url.endswith("/v1"):
            clean_url = f"{clean_url}/v1"
        self.base_url = clean_url
        self.model_name = model_name
        self._available: bool = False
        super().__init__(model_id=f"llama_server:{model_name}", dimension=512)

    @property
    def is_available(self) -> bool:
        return self._available

    async def check_health(self) -> bool:
        """探测 llama-server /v1/models 或 /health。"""
        try:
            host_root = self.base_url.rsplit("/v1", 1)[0]
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{host_root}/health")
                if resp.status_code == 200:
                    self._available = True
                    return True
                models_resp = await client.get(f"{self.base_url}/models")
                if models_resp.status_code == 200:
                    self._available = True
                    return True
        except Exception:
            pass
        self._available = False
        return False

    async def embed_texts(self, texts: list[str]) -> np.ndarray:
        """调用 /v1/embeddings 接口计算向量。"""
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                payload = {"model": self.model_name, "input": texts}
                resp = await client.post(f"{self.base_url}/embeddings", json=payload)
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    vectors = [item.get("embedding", []) for item in data]
                    if vectors and len(vectors[0]) > 0:
                        self.dimension = len(vectors[0])
                    mat = np.array(vectors, dtype=np.float32)
                    return normalize_l2(mat)
        except Exception as e:
            logger.warning(f"llama-server embedding 失败: {e}")

        return np.zeros((len(texts), self.dimension), dtype=np.float32)


class SimpleBertTokenizer:
    """轻量纯 Python WordPiece Tokenizer，无需依赖 tokenizers 第三方原生包。"""

    def __init__(self, vocab_file: Path):
        self.vocab: dict[str, int] = {}
        self.inv_vocab: dict[int, str] = {}
        self._load_vocab(vocab_file)
        self.unk_id = self.vocab.get("[UNK]", 100)
        self.cls_id = self.vocab.get("[CLS]", 101)
        self.sep_id = self.vocab.get("[SEP]", 102)
        self.pad_id = self.vocab.get("[PAD]", 0)

    def _load_vocab(self, vocab_file: Path) -> None:
        if not vocab_file.exists():
            return
        with open(vocab_file, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f):
                token = line.strip()
                self.vocab[token] = idx
                self.inv_vocab[idx] = token

    def tokenize_text(self, text: str, max_length: int = 512) -> tuple[list[int], list[int], list[int]]:
        """纯 Python 基础分词与 WordPiece 匹配。"""
        tokens = [self.cls_id]
        clean_text = text.lower().strip()
        
        # 逐字符 / 单词切分
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


class OnnxEmbeddingProvider(BaseEmbeddingProvider):
    """轻量本地 ONNX 运行时提供商 (Tier 2)。
    默认使用 bge-small-zh-v1.5-int8（约 24MB），限制 2 线程防止争抢 CPU。
    """

    MODEL_DIR = Path.home() / ".agenthub" / "models" / "bge-small-zh-v1.5-int8"

    def __init__(self) -> None:
        super().__init__(model_id="bge-small-zh-v1.5-int8", dimension=512)
        self.model_path = self.MODEL_DIR / "model_quantized.onnx"
        self.vocab_path = self.MODEL_DIR / "vocab.txt"
        self._session: Any = None
        self._tokenizer: SimpleBertTokenizer | None = None
        self._lock = asyncio.Lock()

    @property
    def is_available(self) -> bool:
        return self.model_path.exists()

    def _ensure_session(self) -> bool:
        """懒加载 ONNX InferenceSession 与分词器。"""
        if self._session is not None and self._tokenizer is not None:
            return True
        if not self.model_path.exists():
            return False

        try:
            import onnxruntime as ort

            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 2
            opts.inter_op_num_threads = 1
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self._session = ort.InferenceSession(str(self.model_path), sess_options=opts, providers=["CPUExecutionProvider"])
            
            # 若没有 vocab.txt，生成一个基础 vocab 文件
            if not self.vocab_path.exists():
                self._generate_minimal_vocab(self.vocab_path)
            self._tokenizer = SimpleBertTokenizer(self.vocab_path)
            return True
        except Exception as e:
            logger.warning(f"ONNX 模型初始化失败: {e}")
            return False

    def _generate_minimal_vocab(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tokens = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]"]
        with open(path, "w", encoding="utf-8") as f:
            for t in tokens:
                f.write(t + "\n")

    async def embed_texts(self, texts: list[str]) -> np.ndarray:
        """使用 ONNX 模型与 mean pooling 计算 512 维向量。"""
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)

        async with self._lock:
            if not self._ensure_session() or self._session is None or self._tokenizer is None:
                return np.zeros((len(texts), self.dimension), dtype=np.float32)

            batch_size = 16
            all_embeddings: list[np.ndarray] = []

            for i in range(0, len(texts), batch_size):
                batch_texts = texts[i : i + batch_size]
                input_ids_list: list[list[int]] = []
                attention_mask_list: list[list[int]] = []
                token_type_ids_list: list[list[int]] = []

                for t in batch_texts:
                    inp_ids, att_mask, t_type = self._tokenizer.tokenize_text(t, max_length=512)
                    input_ids_list.append(inp_ids)
                    attention_mask_list.append(att_mask)
                    token_type_ids_list.append(t_type)

                inputs = {
                    "input_ids": np.array(input_ids_list, dtype=np.int64),
                    "attention_mask": np.array(attention_mask_list, dtype=np.int64),
                    "token_type_ids": np.array(token_type_ids_list, dtype=np.int64),
                }

                # 适配部分 ONNX 模型仅接收 input_ids 与 attention_mask
                model_inputs = {inp.name for inp in self._session.get_inputs()}
                feed_dict = {k: v for k, v in inputs.items() if k in model_inputs}

                try:
                    outputs = self._session.run(None, feed_dict)
                    # output 0: token_embeddings or last_hidden_state (shape: N, seq_len, dim)
                    last_hidden_state = outputs[0]
                    if last_hidden_state.ndim == 3:
                        mask = np.expand_dims(inputs["attention_mask"], -1)
                        sum_embeddings = np.sum(last_hidden_state * mask, axis=1)
                        sum_mask = np.clip(mask.sum(axis=1), a_min=1e-9, a_max=None)
                        pooled = sum_embeddings / sum_mask
                    else:
                        pooled = last_hidden_state
                    all_embeddings.append(pooled)
                except Exception as e:
                    logger.warning(f"ONNX 推理批次失败: {e}")
                    all_embeddings.append(np.zeros((len(batch_texts), self.dimension), dtype=np.float32))

            if all_embeddings:
                mat = np.concatenate(all_embeddings, axis=0)
                return normalize_l2(mat)
            return np.zeros((len(texts), self.dimension), dtype=np.float32)


class AdaptiveEmbeddingRouter:
    """自适应 Embedding 路由调度器。
    
    流水线：
    Tier 1: 优先嗅探复用本机 Ollama 或 llama-server 端点；
    Tier 2: 降级为本地 ONNX 模型 (bge-small-zh-v1.5-int8)；
    Tier 3: 极限纯词法降级 (NullEmbeddingProvider)。
    """

    def __init__(self) -> None:
        self._provider: BaseEmbeddingProvider | None = None
        self._lock = asyncio.Lock()

    async def get_provider(self) -> BaseEmbeddingProvider:
        """获取当前最优可用的 Embedding 提供商。"""
        if self._provider is not None and self._provider.is_available:
            return self._provider

        async with self._lock:
            # 双检锁
            if self._provider is not None and self._provider.is_available:
                return self._provider

            # 1. 尝试 Tier 1: 本地 Ollama 探测
            ollama = OllamaEmbeddingProvider()
            if await ollama.check_health():
                logger.info(f"Embedding 路由命中 Tier 1: Ollama ({ollama.model_name})")
                self._provider = ollama
                return self._provider

            # 2. 尝试 Tier 1: 本地 llama-server 探测
            llama_server = LlamaServerEmbeddingProvider()
            if await llama_server.check_health():
                logger.info("Embedding 路由命中 Tier 1: llama-server")
                self._provider = llama_server
                return self._provider

            # 3. 尝试 Tier 2: 本地 ONNX 推理
            onnx_prov = OnnxEmbeddingProvider()
            if onnx_prov.is_available:
                logger.info("Embedding 路由命中 Tier 2: ONNX bge-small-zh-v1.5-int8")
                self._provider = onnx_prov
                return self._provider

            # 4. 降级到 Tier 3: 极限纯词法
            logger.info("Embedding 路由命中 Tier 3: NullEmbeddingProvider (离线纯词法模式)")
            self._provider = NullEmbeddingProvider()
            return self._provider

    def invalidate_cache(self) -> None:
        """失效缓存，用于重新探测外部推理引擎。"""
        self._provider = None


# 全局单例
embedding_router = AdaptiveEmbeddingRouter()
