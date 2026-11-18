"""
按需插件化轻量离线语音转写器 (Audio Transcriber)
核心功能：
1. 按需懒加载 faster-whisper (tiny/base INT8 量化模型)；
2. 首次调用按需下载至 ~/.agenthub/models/whisper/（不计入基础安装包体积）；
3. 将语音文件转为带时间戳的结构化文字并返回。
"""
from pathlib import Path
from typing import Any

from ..core.logging_config import logger


class AudioTranscriber:
    """本地离线语音转写器。"""

    MODEL_DIR = Path.home() / ".agenthub" / "models" / "whisper"
    _model_instance: Any = None

    @classmethod
    def _ensure_model(cls, model_size: str = "tiny") -> Any:
        if cls._model_instance is not None:
            return cls._model_instance

        try:
            from faster_whisper import WhisperModel  # type: ignore
            cls.MODEL_DIR.mkdir(parents=True, exist_ok=True)
            # 使用 CPU int8 推理，内存占用极低 (< 150MB)
            cls._model_instance = WhisperModel(
                model_size,
                device="cpu",
                compute_type="int8",
                download_root=str(cls.MODEL_DIR),
            )
            return cls._model_instance
        except ImportError:
            raise RuntimeError("未安装 faster-whisper。可通过 pip install faster-whisper 安装扩展模块。")
        except Exception as e:
            raise RuntimeError(f"初始化 Whisper 模型失败: {str(e)}")

    @classmethod
    def transcribe(cls, audio_file_path: str, language: str = "zh") -> dict[str, Any]:
        """转写本地音频文件。"""
        p = Path(audio_file_path).resolve()
        if not p.exists():
            return {"success": False, "error": f"音频文件不存在: {audio_file_path}"}

        try:
            model = cls._ensure_model()
            segments, info = model.transcribe(str(p), language=language, beam_size=1)

            results: list[dict[str, Any]] = []
            full_texts: list[str] = []

            for seg in segments:
                results.append({
                    "start": round(seg.start, 2),
                    "end": round(seg.end, 2),
                    "text": seg.text.strip(),
                })
                full_texts.append(seg.text.strip())

            return {
                "success": True,
                "language": info.language,
                "duration": round(info.duration, 2),
                "full_text": "".join(full_texts),
                "segments": results,
            }
        except Exception as e:
            return {"success": False, "error": f"语音转写失败: {str(e)}"}
