"""GUI side of the external API final pass: failed-final display, the settings window
(engine choice, model, key), plus the guarantee that a local run never loads the API code."""

import json
import os
import subprocess
import sys
from datetime import datetime

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from scribe.gui.document import HEADER_BLOCKS, TranscriptView  # noqa: E402
from scribe.store.transcript import export_markdown  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def blocks(v):
    doc = v.document()
    return [doc.findBlockByNumber(i).text() for i in range(doc.blockCount())]


def test_failed_final_is_a_marked_gap(app):
    v = TranscriptView()
    v.reset("회의", datetime(2026, 10, 10, 10, 30))
    v.add_final("others", 1.0, "안건 시작합니다", speaker="A")
    v.set_draft("others", "others-000001", 4.0, "배포 일정은")
    v.add_failed("others", 4.0, "배포 일정은")
    v.add_final("others", 8.0, "다음 안건입니다", speaker="A")
    body = blocks(v)[HEADER_BLOCKS:]
    assert len(body) == 3 and "확정 실패" in body[1] and "배포 일정은" in body[1]
    assert "상대 A" not in body[1]  # the gap has no speaker
    assert "상대 A" in body[2]  # after an unattributed gap the speaker is named again
    assert "발언 2개" in blocks(v)[1] and "…" not in "".join(body)  # draft replaced, not counted
    v.set_names({"others:A": "김팀장"})  # survives a redraw
    body = blocks(v)[HEADER_BLOCKS:]
    assert "확정 실패" in body[1] and "김팀장" in body[0]


def _rows():
    return [
        {"type": "final", "channel": "others", "t_start": 1.0, "t_end": 3.0, "text": "시작합니다",
         "meta": {"speaker": "A"}},
        {"type": "final", "channel": "others", "t_start": 4.0, "t_end": 9.0, "text": "",
         "meta": {"rejected": "api-error", "draft": "배포 일정은", "api_error": "timeout"}},
        {"type": "final", "channel": "others", "t_start": 10.0, "t_end": 11.0, "text": "",
         "meta": {"rejected": "no-speech", "draft": ""}},
    ]


def test_archive_and_markdown_show_failed_rows(app, tmp_path):
    d = tmp_path / "20261010-103000"
    d.mkdir()
    jsonl = d / "transcript.jsonl"
    jsonl.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in _rows()), "utf-8")
    md = export_markdown(jsonl, d / "transcript.md").read_text(encoding="utf-8")
    assert "`00:00:04` ⚠ 확정 실패 (임시 자막: 배포 일정은)" in md
    assert "발언 1개" in md and "⏱ 0분 3초" in md and "🗣 상대 A ·" in md
    assert "00:00:10" not in md  # other rejected rows are still dropped
    v = TranscriptView()
    v.reset("회의")
    v.load_rows(_rows())
    body = blocks(v)[HEADER_BLOCKS:]
    assert len(body) == 2 and "확정 실패" in body[1] and "발언 1개" in blocks(v)[1]


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    import scribe.gui.app as gui_app

    monkeypatch.setenv("MEETING_SCRIBE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(gui_app.Engines, "load", lambda self: None)
    monkeypatch.setattr(gui_app.GpuMonitor, "start", lambda self: None)
    monkeypatch.setattr(gui_app.Engines, "switch_final",
                        lambda self, spec: setattr(self, "spec", spec))
    w = gui_app.MainWindow(tmp_path / "out")
    w._engine_state = "ready"
    w._sync_controls()
    yield gui_app, w
    w.mini.allow_close = True
    w.mini.close()


def _settings_exec(monkeypatch, edit, accept=True):
    """Run the real settings window without showing it: `edit(dlg)` plays the user."""
    from PySide6.QtWidgets import QDialog

    import scribe.gui.settings_dialog as sd

    seen = []

    def fake_exec(dlg):
        seen.append(dlg)
        edit(dlg)
        return QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected

    monkeypatch.setattr(sd.SettingsDialog, "exec", fake_exec)
    return seen


def test_settings_window_switches_to_an_api_and_back(window, monkeypatch):
    gui_app, w = window
    assert not hasattr(w, "engine_combo")  # the choice lives in the settings window now

    def pick_openrouter(dlg):
        dlg.engine.setCurrentIndex(dlg.engine.findData("openrouter"))
        assert dlg.api_box.isVisibleTo(dlg) and dlg.zdr.isVisibleTo(dlg) and dlg.model.isEditable()
        assert dlg.model.currentText() == "openai/whisper-large-v3-turbo"
        assert dlg.key_state.text() == "· 저장된 키 없음"
        dlg.model.setEditText("openai/whisper-large-v3")
        dlg.record.setChecked(False)

    _settings_exec(monkeypatch, pick_openrouter)
    w._open_settings()
    assert w.settings.final_backend == "openrouter" and not w.settings.record_audio
    assert w.settings.api_models["openrouter"] == "openai/whisper-large-v3"
    assert w.engines.spec.backend == "openrouter" and w.engines.spec.model == "openai/whisper-large-v3"
    assert w.monitor is None and "OpenRouter" in w.gpu.text() and "요금" in w.gpu.text()
    saved = json.loads(gui_app.settings._path().read_text(encoding="utf-8"))
    assert saved["final_backend"] == "openrouter" and "consent" not in json.dumps(saved)

    def pick_local(dlg):
        assert dlg.engine.currentData() == "openrouter"  # opens on the saved choice
        dlg.engine.setCurrentIndex(dlg.engine.findData("local"))
        assert not dlg.api_box.isVisibleTo(dlg) and not dlg.model.isEditable()

    _settings_exec(monkeypatch, pick_local)
    w._open_settings()
    assert w.engines.spec.backend == "local" and w.monitor is not None


def test_settings_cancel_changes_nothing(window, monkeypatch):
    gui_app, w = window
    switches = []
    monkeypatch.setattr(gui_app.Engines, "switch_final", lambda self, spec: switches.append(spec))

    def pick_and_cancel(dlg):
        dlg.engine.setCurrentIndex(dlg.engine.findData("openai"))
        dlg.record.setChecked(False)

    _settings_exec(monkeypatch, pick_and_cancel, accept=False)
    w._open_settings()
    assert w.settings.final_backend == "local" and w.settings.record_audio and switches == []


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_settings_saves_typed_key_encrypted(window, monkeypatch, tmp_path):
    from scribe import api_keys

    gui_app, w = window
    key = "sk-test-not-a-real-key-987654"

    def enter_key(dlg):
        dlg.engine.setCurrentIndex(dlg.engine.findData("openai"))
        dlg.key.setText(key)

    _settings_exec(monkeypatch, enter_key)
    w._open_settings()
    assert api_keys.api_key("openai") == key and w.engines.spec.backend == "openai"
    home = tmp_path / "home"
    for f in home.rglob("*"):
        if f.is_file():
            assert key not in f.read_text(encoding="utf-8", errors="ignore"), f


def test_settings_only_rebuilds_engine_when_it_changed(window, monkeypatch):
    gui_app, w = window
    switches = []
    monkeypatch.setattr(gui_app.Engines, "switch_final", lambda self, spec: switches.append(spec))
    _settings_exec(monkeypatch, lambda dlg: dlg.record.setChecked(False))
    w._open_settings()
    assert not w.settings.record_audio and switches == []  # recording toggle needs no reload


def test_engine_controls_locked_while_recording(window, monkeypatch):
    gui_app, w = window
    monkeypatch.setattr(type(w.runner), "running", property(lambda self: True))
    w._sync_controls()
    assert not w.settings_btn.isEnabled()


def test_api_failure_events_reach_views_once(window):
    from scribe.pipeline.events import Event

    _, w = window
    w.engines.spec = __import__("scribe.asr.backends", fromlist=["FinalSpec"]).FinalSpec(
        "openai", "whisper-1")
    w._on_event(Event(type="partial", segment_id="others-000000", channel="others", t_start=1.0,
                      t_end=1.0, text="배포는", engine="sherpa", meta={}))
    w._on_event(Event(type="final", segment_id="others-000000", channel="others", t_start=1.0,
                      t_end=3.0, text="", engine="OpenAI · whisper-1",
                      meta={"rejected": "api-error", "draft": "배포는"}))
    w._on_event(Event(type="status", segment_id="others-status", channel="others", t_start=3.0,
                      t_end=3.0, text="외부 API 응답 실패: timeout", engine="system",
                      meta={"callout": True}))
    assert "확정 실패" in w.live.toPlainText() and "확정 실패" in w.mini.view.toPlainText()
    assert not w.callout.isHidden() and "외부 API" in w.callout.text()
    w.callout.hide()
    w._on_event(Event(type="status", segment_id="others-status", channel="others", t_start=4.0,
                      t_end=4.0, text="확정 자막 지연: 대기 25초", engine="system", meta={}))
    assert w.callout.isHidden()  # plain status lines never pop a callout


def test_api_errors_never_open_the_gpu_dialog(window, monkeypatch):
    gui_app, w = window
    monkeypatch.setattr(gui_app.GpuDialog, "exec", lambda self: pytest.fail("GPU dialog"))
    w._engines_failed("OpenAI API 키가 없습니다.", "api")
    assert "API 키" in w.callout.text() and w._engine_state == "unavailable"


def test_local_run_never_loads_api_code(tmp_path):
    """A local session in a fresh interpreter: the API backend and key store are never imported."""
    code = r"""
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import scribe.cli, scribe.gui.app, scribe.gui.engine
from pathlib import Path
from scribe.asr.types import FinalResult
from scribe.audio.capture import FileSource
from scribe.models import silero_vad_path
from scribe.pipeline.session import Session

class Stub:
    remote = False
    label = "GPU · stub"
    def transcribe(self, audio, prompt=None):
        return FinalResult("확정", -0.1, 0.01, 1.1)

class Sherpa:
    def new_stream(self):
        class S:
            def accept(self, a): return "초안"
            def finish(self): return "초안"
        return S()

fx = Path(sys.argv[1]) / "meeting_tech.wav"
Session({"others": FileSource(fx)}, Stub, Sherpa(), silero_vad_path(), Path(sys.argv[2]),
        record=False).run()
bad = [m for m in ("scribe.asr.remote_final", "scribe.api_keys", "scribe.gui.settings_dialog")
       if m in sys.modules]
print("LOADED", bad)
"""
    fixtures = os.path.join(os.path.dirname(__file__), "..", "fixtures")
    out = subprocess.run([sys.executable, "-c", code, fixtures, str(tmp_path / "s")],
                         capture_output=True, text=True, encoding="utf-8",
                         env={**os.environ, "MEETING_SCRIBE_HOME": str(tmp_path / "home")},
                         timeout=120)
    assert "LOADED []" in out.stdout, out.stdout + out.stderr
