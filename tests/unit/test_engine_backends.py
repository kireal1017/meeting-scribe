"""Engines with an external API backend: no GPU, no upload at startup, no switching on errors."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from scribe import settings  # noqa: E402
from scribe.asr.backends import FinalSpec  # noqa: E402
from scribe.gui.engine import Engines  # noqa: E402

KEY = "sk-test-not-a-real-key-123456"


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def no_gpu_no_net(monkeypatch, tmp_path):
    """Any attempt to build the GPU model, touch CUDA or open a connection fails the test."""
    import urllib.request

    import scribe.asr.sherpa_stream as ss
    import scribe.asr.whisper_final as wf
    import scribe.diagnostics.cuda_env as ce
    import scribe.diarize as dz
    import scribe.models as models

    monkeypatch.setenv("MEETING_SCRIBE_HOME", str(tmp_path))
    monkeypatch.setenv("MEETING_SCRIBE_OPENAI_API_KEY", KEY)
    monkeypatch.setattr(wf, "WhisperFinal", lambda **k: pytest.fail("GPU model built"))
    monkeypatch.setattr(ce, "register", lambda: pytest.fail("CUDA registered"))
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("network used"))
    monkeypatch.setattr(ss, "SherpaStreaming", lambda *a, **k: "sherpa")
    monkeypatch.setattr(models, "speaker_model_path", lambda: "spk.onnx")
    monkeypatch.setattr(dz, "SherpaEmbedder", lambda path: "embedder")


def _sync(e):
    got = []
    e.ready.connect(lambda: got.append("ready"))
    e.failed.connect(lambda msg, kind: got.append((kind, msg)))
    return got


def test_api_startup_needs_no_gpu_and_sends_nothing(app, no_gpu_no_net):
    e = Engines(spec=FinalSpec("openai", "whisper-1"))
    got = _sync(e)
    e._load()
    assert got == ["ready"] and e.final.remote and e.remote and e.sherpa == "sherpa"
    assert e.final_factory() is not None and e.final is None
    # after a session halted (backend handed back as None) the rebuild is the same API kind
    rebuilt = e.final_factory()
    assert rebuilt.remote and rebuilt.label == "OpenAI · whisper-1"


def test_missing_key_reports_api_problem_and_keeps_draft_model(app, no_gpu_no_net, monkeypatch):
    e = Engines(spec=FinalSpec("openrouter", "openai/whisper-large-v3-turbo"))
    got = _sync(e)
    e._load()
    assert len(got) == 1 and got[0][0] == "api" and "OpenRouter API 키" in got[0][1]
    assert e.sherpa == "sherpa" and e.final is None  # draft model loaded anyway
    monkeypatch.setenv("MEETING_SCRIBE_OPENROUTER_API_KEY", KEY)
    e._switch()  # recovery without restarting the app
    assert got[-1] == "ready" and e.final.remote


def test_broken_backend_setting_is_reported_not_coerced(app, no_gpu_no_net):
    s = settings.Settings(final_backend="cloud")
    e = Engines.from_settings(s)
    got = _sync(e)
    e._load()
    assert e.spec is None and got[0][0] == "api" and "cloud" in got[0][1]
    assert e.sherpa == "sherpa"
    e.switch_final(FinalSpec("openai", "whisper-1"))
    e._switch()
    assert got[-1] == "ready" and e.final.remote


def test_gpu_failure_never_builds_an_api_backend(app, monkeypatch, tmp_path):
    import scribe.asr.remote_final as rf
    import scribe.asr.sherpa_stream as ss
    import scribe.asr.whisper_final as wf
    from scribe.asr.types import GpuUnavailableError

    def no_gpu(**k):
        raise GpuUnavailableError("CUDA GPU를 찾지 못했습니다")

    monkeypatch.setenv("MEETING_SCRIBE_OPENAI_API_KEY", KEY)
    monkeypatch.setattr(wf, "WhisperFinal", no_gpu)
    monkeypatch.setattr(rf, "RemoteFinal", lambda *a, **k: pytest.fail("API backend built"))
    monkeypatch.setattr(ss, "SherpaStreaming", lambda *a, **k: "sherpa")
    e = Engines(final_model="large-v3-turbo")
    got = _sync(e)
    e._load()
    assert got[0] == ("gpu", "CUDA GPU를 찾지 못했습니다")
    with pytest.raises(GpuUnavailableError):
        e.final_factory()  # a session restart after a GPU halt: still GPU, never the API
