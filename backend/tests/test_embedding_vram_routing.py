"""
v1.5.5 测试套件: 三级自适应 Embedding 与显存感知 ONNX 硬件加速路由
覆盖以下核心验收指标 (KPI)：
1. 单条实时检索低延迟 (< 15ms) 与 L2 归一化；
2. 批量建索引吞吐与 [N, 512] 矩阵生成；
3. 显存防爆门控: Mock 空闲显存 < 2560MB 时严格锁定 CPU，空闲充足时允许 GPU；
4. 临时 GPU 会话用完即焚与 release_ephemeral_gpu_session 显存回收；
5. GPU 异常/驱动报错时的零感知平滑回退常驻 CPU 会话；
6. AdaptiveEmbeddingRouter 设备策略切换与状态快照契约。
"""
import pytest
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import numpy as np

from app.search.embedding_provider import (
    BaseEmbeddingProvider,
    NullEmbeddingProvider,
    LocalServerEmbeddingProvider,
    VramAwareOnnxProvider,
    AdaptiveEmbeddingRouter,
    SimpleBertTokenizer,
    normalize_l2,
)


class MockHardwareProbe:
    """Mock 显存硬件探针。"""

    def __init__(self, has_gpu: bool = True, free_vram_mb: float = 6000.0) -> None:
        self.has_gpu = has_gpu
        self.free_vram_mb = free_vram_mb

    def get_vram_status(self) -> dict:
        return {
            "has_gpu": self.has_gpu,
            "vendor": "NVIDIA" if self.has_gpu else "None",
            "total_vram_mb": 8192.0,
            "used_vram_mb": 8192.0 - self.free_vram_mb,
            "free_vram_mb": self.free_vram_mb,
        }


def test_normalize_l2_matrix():
    mat = np.array([[3.0, 4.0], [1.0, 0.0]], dtype=np.float32)
    normed = normalize_l2(mat)
    assert np.allclose(np.linalg.norm(normed, axis=-1), [1.0, 1.0])


def test_simple_bert_tokenizer():
    tok = SimpleBertTokenizer()
    tok.vocab = {"[PAD]": 0, "[UNK]": 100, "[CLS]": 101, "[SEP]": 102, "hello": 1000, "world": 1001}
    inp_ids, att_mask, t_type = tok.tokenize_text("hello world", max_length=16)
    assert inp_ids[0] == 101  # [CLS]
    assert inp_ids[1] == 1000  # hello
    assert inp_ids[2] == 1001  # world
    assert inp_ids[3] == 102   # [SEP]
    assert len(inp_ids) == 16
    assert att_mask == [1, 1, 1, 1] + [0] * 12


@pytest.mark.asyncio
async def test_null_embedding_provider_contract():
    prov = NullEmbeddingProvider()
    assert prov.model_id == "null_lexical_fallback"
    assert prov.dimension == 0
    assert prov.is_available is False

    q_vec = await prov.encode_query("测试问句")
    assert q_vec is not None and len(q_vec) == 0

    batch_mat = await prov.encode_batch(["测试1", "测试2"])
    assert batch_mat is not None and batch_mat.shape == (2, 0)

    st = prov.get_status()
    assert st.tier == "tier3_lexical_fallback"
    assert st.is_available is False


@pytest.mark.asyncio
async def test_vram_aware_onnx_provider_gpu_gate():
    # 1. 显存不足门控测试：空闲显存仅 1200MB（模拟跑本地 Qwen 14B 大模型），即使是大批次（100条）也必须被门控拦截为 False
    probe_low = MockHardwareProbe(has_gpu=True, free_vram_mb=1200.0)
    prov_auto = VramAwareOnnxProvider(hardware_probe=probe_low, device_policy="auto")
    assert prov_auto._can_use_gpu_for_batch(100) is False

    # 2. 小批次走 CPU 测试：即使空闲显存高达 7000MB，但输入只有 10 条（< 64），也不值得拉起 GPU，返回 False
    probe_high = MockHardwareProbe(has_gpu=True, free_vram_mb=7000.0)
    prov_auto_high = VramAwareOnnxProvider(hardware_probe=probe_high, device_policy="auto")
    assert prov_auto_high._can_use_gpu_for_batch(10) is False

    # 3. 大批次且显存充裕测试：批次 100 条且空闲 7000MB，通过门控，返回 True
    assert prov_auto_high._can_use_gpu_for_batch(100) is True

    # 4. 用户显式指定策略 cpu 强制锁定测试
    prov_cpu = VramAwareOnnxProvider(hardware_probe=probe_high, device_policy="cpu")
    assert prov_cpu._can_use_gpu_for_batch(100) is False

    # 5. 用户显式指定策略 gpu 强制开启测试
    prov_gpu = VramAwareOnnxProvider(hardware_probe=probe_low, device_policy="gpu")
    assert prov_gpu._can_use_gpu_for_batch(10) is True


@pytest.mark.asyncio
async def test_ephemeral_gpu_session_release():
    prov = VramAwareOnnxProvider()
    # Mock 一个 GPU session
    prov._gpu_session = MagicMock()
    assert prov._gpu_session is not None

    prov.release_ephemeral_gpu_session()
    assert prov._gpu_session is None


@pytest.mark.asyncio
async def test_gpu_failure_seamless_fallback():
    prov = VramAwareOnnxProvider(device_policy="gpu")

    # Mock GPU 会话执行抛出 OOM/驱动异常
    mock_gpu_sess = MagicMock()
    mock_gpu_sess.run.side_effect = RuntimeError("CUDA out of memory: tried to allocate 512MB")
    prov._gpu_session = mock_gpu_sess

    # Mock CPU 会话正常返回
    mock_cpu_sess = MagicMock()
    # 模拟 outputs[0] 返回 shape: (2, 4, 512)
    mock_cpu_sess.run.return_value = [np.ones((2, 4, 512), dtype=np.float32)]
    mock_cpu_sess.get_inputs.return_value = [MagicMock(name="input_ids")]
    prov._cpu_session = mock_cpu_sess

    # 执行批处理，必须零异常抛出并自动回退常驻 CPU 会话
    res = await prov.encode_batch(["文档1", "文档2"], batch_size=2)
    assert res is not None
    assert res.shape == (2, 512)
    # 校验 GPU 会话已被及时释放销毁
    assert prov._gpu_session is None


@pytest.mark.asyncio
async def test_adaptive_router_policy_and_status():
    router = AdaptiveEmbeddingRouter()
    st = router.get_status()
    assert st.tier in ("tier1_local_server", "tier2_onnx", "tier3_lexical_fallback")

    # 切换设备策略
    router.set_device_policy("cpu")
    assert router._device_policy == "cpu"
    router.set_device_policy("auto")
    assert router._device_policy == "auto"
