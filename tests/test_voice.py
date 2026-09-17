from types import SimpleNamespace

import pytest

from bearbless import voice


class FakeModel:
    def transcribe(self, path, **kwargs):
        assert path.endswith(".wav")
        assert kwargs["language"] == "zh"
        return iter((SimpleNamespace(text=" 打开美团，"), SimpleNamespace(text="找猪脚饭。"))), None


def test_transcribe_audio_joins_segments(monkeypatch):
    monkeypatch.setattr(voice, "_load_model", lambda _: FakeModel())
    assert voice.transcribe_audio(b"fake audio") == "打开美团，找猪脚饭。"


def test_transcribe_audio_rejects_empty_input():
    with pytest.raises(voice.VoiceTranscriptionError, match="没有收到录音"):
        voice.transcribe_audio(b"")


def test_to_simplified_normalizes_whitespace():
    assert voice.to_simplified("  打開美團  找一家  ") == "打开美团 找一家"
