"""
无 GPU 服务器轻量多模态预处理中心（MediaProcessor）。
职责：
1. 纯 CPU 语音识别双轨引擎：云端 OpenAI 兼容 Audio API 覆盖 + 默认本地 `sherpa-onnx` SenseVoice-Small (Int8)，限制 2 线程 + 单并发信号量；
2. 微信 `.silk`/`.amr` 等格式通过 `pilk` + `ffmpeg` 解码为 16kHz 单声道 WAV；
3. 纯 CPU 图片文字识别：复用项目现有 2 线程 `RapidOCR`（`document_extractor.get_ocr_engine`）并封装 `image_url` 透传数据；
4. 可选轻量语音合成（`edge_tts`）与 7 天媒体临时文件自动 GC 清理。
"""
from __future__ import annotations

import asyncio
import base64
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from ..core.config import settings
from ..core.logging_config import logger


class MediaProcessor:
    """无 GPU 纯 CPU 环境下安全可控的多模态预处理引擎。"""

    def __init__(self) -> None:
        # 严格单并发门控，禁止多语音/多图片同时拉起 ONNX 推理撑爆 CPU/内存
        self._cpu_infer_sem = asyncio.Semaphore(1)
        self._sherpa_recognizer: Any = None
        self._last_gc_ts: float = 0.0

    @staticmethod
    def get_cache_dir() -> Path:
        """获取并自动创建媒体临时缓存目录。"""
        d = Path(settings.db_path).parent / "media_cache"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ────────────────────────── 1. 音频转码与无 GPU 语音识别 ──────────────────────────

    @staticmethod
    def _convert_to_16k_wav(src_path: Path, wav_path: Path) -> bool:
        """尝试使用 pilk（针对微信 silk）或 ffmpeg 将音频统一转为 16kHz 单声道 PCM WAV。"""
        ext = src_path.suffix.lower()
        pcm_tmp: Path | None = None
        ffmpeg_input = src_path

        try:
            if ext in (".silk", ".slk"):
                try:
                    import pilk  # type: ignore

                    pcm_tmp = src_path.with_suffix(".pcm")
                    pilk.decode(str(src_path), str(pcm_tmp))
                    cmd = [
                        "ffmpeg",
                        "-y",
                        "-f",
                        "s16le",
                        "-ar",
                        "24000",
                        "-ac",
                        "1",
                        "-i",
                        str(pcm_tmp),
                        "-ar",
                        "16000",
                        "-ac",
                        "1",
                        str(wav_path),
                    ]
                    proc = subprocess.run(cmd, capture_output=True, timeout=20)
                    if proc.returncode == 0 and wav_path.exists():
                        return True
                except ImportError:
                    logger.debug("[MediaProcessor] 未安装 pilk，尝试直接调用 ffmpeg 解码 silk")
                except Exception as p_err:
                    logger.debug(f"[MediaProcessor] pilk 解码异常: {p_err}")

            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(ffmpeg_input),
                "-ar",
                "16000",
                "-ac",
                "1",
                str(wav_path),
            ]
            proc = subprocess.run(cmd, capture_output=True, timeout=25)
            return proc.returncode == 0 and wav_path.exists()
        except Exception as err:
            logger.debug(f"[MediaProcessor] 音频转码失败 ({src_path.name}): {err}")
            return False
        finally:
            if pcm_tmp and pcm_tmp.exists():
                try:
                    pcm_tmp.unlink()
                except Exception:
                    pass

    def _get_sherpa_recognizer(self) -> Any:
        """懒加载纯 CPU sherpa-onnx SenseVoice-Small (Int8) 识别器（固定 2 线程）。"""
        if self._sherpa_recognizer is not None:
            return self._sherpa_recognizer if self._sherpa_recognizer is not False else None

        model_dir_str = (
            os.getenv("SHERPA_SENSEVOICE_DIR")
            or str(Path.home() / ".agenthub" / "models" / "sensevoice-small-int8")
        ).strip()
        model_dir = Path(model_dir_str)
        model_file = model_dir / "model.int8.onnx"
        if not model_file.exists():
            model_file = model_dir / "model.onnx"
        tokens_file = model_dir / "tokens.txt"

        if not (model_file.exists() and tokens_file.exists()):
            self._sherpa_recognizer = False
            return None

        try:
            import sherpa_onnx  # type: ignore

            self._sherpa_recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=str(model_file),
                tokens=str(tokens_file),
                num_threads=2,
                use_itn=True,
                debug=False,
            )
            logger.info(f"[MediaProcessor] sherpa-onnx SenseVoice 初始化成功 (threads=2, dir={model_dir})")
            return self._sherpa_recognizer
        except Exception as err:
            logger.debug(f"[MediaProcessor] sherpa-onnx 初始化跳过: {err}")
            self._sherpa_recognizer = False
            return None

    def _run_sherpa_sync(self, wav_path: Path) -> str:
        """读取 16k WAV 并执行纯 CPU SenseVoice 离线识别。"""
        import wave
        import numpy as np  # type: ignore

        rec = self._get_sherpa_recognizer()
        if rec is None:
            return ""
        with wave.open(str(wav_path), "rb") as wf:
            sample_rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
        samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        stream = rec.create_stream()
        stream.accept_waveform(sample_rate, samples)
        rec.decode_stream(stream)
        return str(getattr(stream.result, "text", "") or "").strip()

    async def transcribe_audio_bytes(
        self,
        audio_bytes: bytes,
        filename: str = "voice.silk",
    ) -> dict[str, Any]:
        """无 GPU 语音转文字（云端 ASR 优先覆盖 + 默认本地纯 CPU sherpa-onnx SenseVoice）。"""
        if not audio_bytes:
            return {"success": False, "text": "", "error": "音频内容为空"}

        cache_dir = self.get_cache_dir()
        safe_ext = Path(filename or "voice.silk").suffix or ".silk"
        stem = f"aud_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        src_path = cache_dir / f"{stem}{safe_ext}"
        wav_path = cache_dir / f"{stem}.wav"
        src_path.write_bytes(audio_bytes)

        eff_path = src_path
        if safe_ext.lower() != ".wav":
            converted = await asyncio.to_thread(self._convert_to_16k_wav, src_path, wav_path)
            if converted:
                eff_path = wav_path

        # 轨道 A：若配置了云端 OpenAI Audio 兼容 ASR，优先零本地 CPU 消耗识别
        asr_base = (os.getenv("ASR_API_BASE_URL") or "").strip()
        if asr_base:
            try:
                from ..core.provider_adapter import provider_adapter

                txt = await provider_adapter.transcribe_audio_openai_compatible(
                    base_url=asr_base,
                    api_key=(os.getenv("ASR_API_KEY") or "").strip(),
                    model_name=(os.getenv("ASR_MODEL") or "FunAudioLLM/SenseVoiceSmall").strip(),
                    audio_bytes=eff_path.read_bytes(),
                    filename=eff_path.name,
                )
                if txt:
                    return {"success": True, "engine": "cloud_asr", "text": txt}
            except Exception as cloud_err:
                logger.debug(f"[MediaProcessor] 云端 ASR 调用失败，尝试本地 CPU 兜底: {cloud_err}")

        # 轨道 B：本地纯 CPU sherpa-onnx SenseVoice (单并发 + 2 线程)
        async with self._cpu_infer_sem:
            try:
                txt = await asyncio.to_thread(self._run_sherpa_sync, eff_path)
                if txt:
                    return {"success": True, "engine": "sherpa_onnx_cpu", "text": txt}
            except Exception as local_err:
                logger.debug(f"[MediaProcessor] 本地 sherpa-onnx 识别异常: {local_err}")

        return {
            "success": False,
            "text": "",
            "hint": "语音识别模型未就绪（可在 ~/.agenthub/models/sensevoice-small-int8 放置离线模型或配置 ASR_API_BASE_URL）。",
        }

    # ────────────────────────── 2. 无 GPU 图片文字 OCR 与 Vision 打包 ──────────────────────────

    async def analyze_image_bytes(
        self,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
    ) -> dict[str, Any]:
        """纯 CPU 调用 RapidOCR（2 线程）提取图片文字，同时编码 Base64 供云端 Vision 通道透传。"""
        if not image_bytes:
            return {"ocr_text": "", "base64": "", "mime_type": mime_type}

        cache_dir = self.get_cache_dir()
        ext = ".png" if "png" in mime_type.lower() else ".jpg"
        tmp_img = cache_dir / f"img_{int(time.time())}_{uuid.uuid4().hex[:8]}{ext}"
        try:
            tmp_img.write_bytes(image_bytes)
        except Exception:
            pass

        ocr_text = ""
        async with self._cpu_infer_sem:
            try:
                from ..vfs.document_extractor import get_ocr_engine

                ocr_eng = await asyncio.to_thread(get_ocr_engine)
                if ocr_eng is not None:
                    ocr_res, _ = await asyncio.to_thread(ocr_eng, image_bytes)
                    if ocr_res:
                        ocr_text = "\n".join([str(item[1]) for item in ocr_res if len(item) > 1 and item[1]]).strip()
            except Exception as ocr_err:
                logger.debug(f"[MediaProcessor] RapidOCR 识图跳过: {ocr_err}")

        b64_data = base64.b64encode(image_bytes).decode("ascii")
        return {
            "ocr_text": ocr_text,
            "base64": b64_data,
            "mime_type": mime_type or "image/jpeg",
            "data_uri": f"data:{mime_type or 'image/jpeg'};base64,{b64_data}",
        }

    # ────────────────────────── 3. 可选轻量语音合成 (edge-tts) ──────────────────────────

    async def synthesize_tts_brief(self, text: str) -> str | None:
        """可选极轻量 TTS：将简短结论合成为临时 MP3 路径（未安装 edge-tts 返回 None）。"""
        clean = (text or "").strip()[:180]
        if not clean:
            return None
        try:
            import edge_tts  # type: ignore

            out_path = self.get_cache_dir() / f"tts_{int(time.time())}_{uuid.uuid4().hex[:8]}.mp3"
            comm = edge_tts.Communicate(clean, voice="zh-CN-XiaoxiaoNeural")
            await comm.save(str(out_path))
            return str(out_path)
        except Exception:
            return None

    # ────────────────────────── 4. 统一入站多模态处理 & 7天 GC ──────────────────────────

    @staticmethod
    async def _load_attachment_bytes(att: dict[str, Any]) -> bytes:
        b64 = str(att.get("base64") or att.get("data") or "").strip()
        if b64:
            if "," in b64 and b64.startswith("data:"):
                b64 = b64.split(",", 1)[1]
            return base64.b64decode(b64)
        url = str(att.get("url") or "").strip()
        if url.startswith(("http://", "https://")):
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                r = await client.get(url)
                if r.status_code == 200:
                    return r.content
        return b""

    async def process_inbound_media(self, raw_payload: dict[str, Any] | None) -> dict[str, Any]:
        """统一处理入站语音与图片附件，产出转写文本、OCR 文本与视觉载荷。"""
        payload = raw_payload or {}
        attachments = payload.get("attachments") or []
        transcripts: list[str] = []
        ocr_texts: list[str] = []
        image_attachments: list[dict[str, Any]] = []

        if not isinstance(attachments, list):
            return {"transcripts": transcripts, "ocr_texts": ocr_texts, "image_attachments": image_attachments}

        for idx, att in enumerate(attachments):
            if not isinstance(att, dict):
                continue
            kind = str(att.get("kind") or "").strip().lower()
            mime = str(att.get("mime_type") or "").strip().lower()
            fname = str(att.get("name") or f"media_{idx}")

            if kind == "voice" or mime.startswith("audio/"):
                direct_text = str(att.get("text") or "").strip()
                if direct_text:
                    transcripts.append(direct_text)
                    continue
                raw_bytes = await self._load_attachment_bytes(att)
                if raw_bytes:
                    tr_res = await self.transcribe_audio_bytes(raw_bytes, filename=fname or "voice.silk")
                    if tr_res.get("text"):
                        transcripts.append(str(tr_res["text"]))
                    elif tr_res.get("hint"):
                        transcripts.append(f"[收到语音但暂未转写: {tr_res['hint']}]")

            elif kind == "image" or mime.startswith("image/"):
                raw_bytes = await self._load_attachment_bytes(att)
                if raw_bytes:
                    img_res = await self.analyze_image_bytes(raw_bytes, mime_type=mime or "image/jpeg")
                    if img_res.get("ocr_text"):
                        ocr_texts.append(img_res["ocr_text"])
                    if img_res.get("base64"):
                        image_attachments.append(
                            {
                                "name": fname or f"image_{idx}.jpg",
                                "mime_type": img_res["mime_type"],
                                "data": img_res["base64"],
                            }
                        )

        return {
            "transcripts": transcripts,
            "ocr_texts": ocr_texts,
            "image_attachments": image_attachments,
        }

    def cleanup_expired_media(self, max_age_days: int = 7, min_interval_seconds: float = 86400.0) -> int:
        """清理 media_cache 中超过 max_age_days 天的过期媒体临时文件（默认每 24 小时执行一次）。"""
        now_ts = time.time()
        if min_interval_seconds > 0 and (now_ts - self._last_gc_ts) < min_interval_seconds:
            return 0
        self._last_gc_ts = now_ts

        cache_dir = self.get_cache_dir()
        cutoff = now_ts - max(1, int(max_age_days)) * 86400.0
        removed = 0
        for p in cache_dir.iterdir():
            if not p.is_file():
                continue
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
                    removed += 1
            except Exception:
                pass
        if removed:
            logger.info(f"[MediaProcessor] 定期 GC 已清理 {removed} 个超过 {max_age_days} 天的临时媒体文件")
        return removed


media_processor = MediaProcessor()

