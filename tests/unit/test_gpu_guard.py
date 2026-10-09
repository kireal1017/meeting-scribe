"""The final pass must never quietly fall back to CPU."""

import ctranslate2
import faster_whisper
import pytest

from scribe.asr import whisper_final
from scribe.asr.whisper_final import GpuUnavailableError, WhisperFinal


def test_no_cuda_device_raises_instead_of_cpu(monkeypatch):
    calls = []
    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", lambda: 0)
    monkeypatch.setattr(faster_whisper, "WhisperModel", lambda *a, **k: calls.append(k))
    with pytest.raises(GpuUnavailableError, match="doctor"):
        WhisperFinal()
    assert calls == []


def test_cuda_init_exception_is_wrapped(monkeypatch):
    def boom():
        raise RuntimeError("CUDA driver version is insufficient")

    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", boom)
    with pytest.raises(GpuUnavailableError):
        WhisperFinal()


def test_model_is_always_requested_on_cuda(monkeypatch):
    seen = {}

    class FakeModel:
        def __init__(self, *a, **k):
            seen.update(k)

    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", lambda: 1)
    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeModel)
    WhisperFinal()
    assert seen["device"] == "cuda"
    assert whisper_final.config.WHISPER_COMPUTE_TYPE == seen["compute_type"]


def test_model_load_failure_is_wrapped(monkeypatch):
    def fail(*a, **k):
        raise RuntimeError("CUDA failed with error out of memory")

    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", lambda: 1)
    monkeypatch.setattr(faster_whisper, "WhisperModel", fail)
    with pytest.raises(GpuUnavailableError, match="out of memory"):
        WhisperFinal()
