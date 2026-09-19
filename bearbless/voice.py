from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import tempfile
from typing import Any


class VoiceTranscriptionError(RuntimeError):
    """Raised when an audio task cannot be transcribed locally."""


MAX_AUDIO_BYTES = 15 * 1024 * 1024
_BASIC_T2S = str.maketrans({
    "開": "开", "團": "团", "時": "时", "鐘": "钟", "訊": "讯",
    "發": "发", "聯": "联", "繫": "系", "樂": "乐", "與": "与",
})


def to_simplified(text: str) -> str:
    """Normalize recognized Chinese to Simplified Chinese when OpenCC is available."""
    cleaned = " ".join(text.split())
    try:
        from opencc import OpenCC
    except ImportError:
        # Keep the core voice path useful in the minimal installation. OpenCC
        # remains the authoritative converter when the voice extra is present;
        # this small product-vocabulary fallback avoids leaking common app/task
        # terms through as Traditional Chinese.
        return cleaned.translate(_BASIC_T2S)
    return OpenCC("t2s").convert(cleaned)


@lru_cache(maxsize=2)
def _load_model(model_name: str) -> Any:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise VoiceTranscriptionError(
            "尚未安装本地语音模型，请运行 pip install -e '.[voice]'"
        ) from exc
    return WhisperModel(model_name, device="cpu", compute_type="int8")


def transcribe_audio(audio: bytes, *, suffix: str = ".wav") -> str:
    if not audio:
        raise VoiceTranscriptionError("没有收到录音")
    if len(audio) > MAX_AUDIO_BYTES:
        raise VoiceTranscriptionError("录音超过 15 MB，请缩短后重试")

    model_name = os.getenv("BEARBLESS_WHISPER_MODEL", "tiny")
    language = os.getenv("BEARBLESS_SPEECH_LANGUAGE", "zh") or None
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            handle.write(audio)
            temp_path = Path(handle.name)
        segments, _ = _load_model(model_name).transcribe(
            str(temp_path),
            language=language,
            vad_filter=True,
            beam_size=5,
        )
        transcript = to_simplified("".join(str(segment.text) for segment in segments))
        if not transcript:
            raise VoiceTranscriptionError("没有识别到清晰语音，请靠近麦克风后重试")
        return transcript
    except VoiceTranscriptionError:
        raise
    except Exception as exc:
        raise VoiceTranscriptionError(f"本地语音识别失败：{exc}") from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
