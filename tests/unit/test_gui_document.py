"""TranscriptView logic (rendering is checked by screenshots; here: structure and behaviour)."""

import os
from datetime import datetime

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from scribe.gui.document import HEADER_BLOCKS, TranscriptView  # noqa: E402
from scribe.gui.engine import Engines  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def view(app):
    v = TranscriptView()
    v.reset("회의록 2026-10-10 10:30", datetime(2026, 10, 10, 10, 30))
    return v


def blocks(v):
    doc = v.document()
    return [doc.findBlockByNumber(i).text() for i in range(doc.blockCount())]


def test_finals_go_above_drafts_and_drafts_are_replaced(view):
    view.set_draft("others", "others-000000", 1.0, "안녕하")
    view.set_draft("me", "me-000000", 2.0, "네")
    view.add_final("others", 1.0, "안녕하세요.")
    body = blocks(view)[HEADER_BLOCKS:]
    assert body[0].endswith("안녕하세요.")
    assert body[0].startswith("00:00:01")
    # the others draft is gone (its final arrived); the me draft is still live, at the end
    assert len(body) == 2 and body[1].endswith("네 …")
    view.add_final("me", 2.0, "네 맞아요.")
    assert blocks(view)[HEADER_BLOCKS:][-1].endswith("네 맞아요.")
    assert len(blocks(view)) == HEADER_BLOCKS + 2


def test_speaker_chip_only_on_speaker_change(view):
    view.add_final("others", 1.0, "하나")
    view.add_final("others", 5.0, "둘")
    view.add_final("me", 9.0, "셋")
    a, b, c = blocks(view)[HEADER_BLOCKS:]
    assert "상대" in a and "상대" not in b and "나" in c


def test_properties_line_updates(view):
    view.add_final("others", 1.0, "하나")
    view.add_final("me", 3.0, "둘")
    view.update_properties(125)
    props = blocks(view)[1]
    assert "2026년 10월 10일 10:30" in props and "2분 5초" in props
    assert "상대, 나" in props and "발언 2개" in props


def test_drop_draft_for_rejected_final(view):
    view.set_draft("others", "others-000003", 1.0, "음")
    view.drop_draft("others", "others-000003")
    assert len(blocks(view)) == HEADER_BLOCKS


def test_placeholder_only_while_empty(view):
    view.set_placeholder("기록 시작을 누르세요")
    assert blocks(view)[-1] == "기록 시작을 누르세요"
    view.add_final("others", 1.0, "시작")
    assert "기록 시작을 누르세요" not in blocks(view)


def test_search_counts_and_load_rows(view):
    rows = [
        {"type": "final", "channel": "others", "t_start": 3.0, "t_end": 4.0, "text": "배포는 목요일"},
        {"type": "status", "channel": "others", "t_start": 3.5, "t_end": 3.5, "text": "x"},
        {"type": "final", "channel": "me", "t_start": 1.0, "t_end": 2.0, "text": "배포 일정 공유"},
        {"type": "final", "channel": "me", "t_start": 5.0, "t_end": 6.0, "text": ""},
    ]
    view.load_rows(rows)
    body = blocks(view)[HEADER_BLOCKS:]
    assert [b.split("\t")[-1] for b in body] == ["배포 일정 공유", "배포는 목요일"]  # sorted by time
    assert view.highlight("배포") == 2
    assert view.highlight("") == 0


def test_engine_hands_out_preloaded_whisper_once(monkeypatch):
    import scribe.asr.whisper_final as wf

    built = []
    monkeypatch.setattr(wf, "WhisperFinal", lambda **k: built.append(k) or "fresh")
    e = Engines(final_model="large-v3")
    e.whisper = "preloaded"
    assert e.whisper_factory() == "preloaded"
    assert e.whisper is None  # session owns it now; nothing pins the VRAM
    assert e.whisper_factory() == "fresh"
    assert built == [{"model": "large-v3"}]  # a restart rebuilds the *selected* model


def _load_sync(engines):
    got = []
    engines.ready.connect(lambda: got.append(("ready",)))
    engines.failed.connect(lambda msg, kind: got.append(("failed", kind, msg)))
    engines._load()  # run the loader inline (signals are direct within one thread)
    return got


class _FakeWhisper:
    def __init__(self, *a, **k):
        pass

    def transcribe(self, audio):
        return None


def _stub_speaker_model(monkeypatch):
    import scribe.diarize as dz
    import scribe.models as models

    monkeypatch.setattr(models, "speaker_model_path", lambda: "spk.onnx")
    monkeypatch.setattr(dz, "SherpaEmbedder", lambda path: f"embedder({path})")


def test_engines_load_both_in_parallel_and_report_ready(app, monkeypatch):
    import scribe.asr.sherpa_stream as ss
    import scribe.asr.whisper_final as wf

    monkeypatch.setattr(ss, "SherpaStreaming", lambda *a, **k: "sherpa")
    monkeypatch.setattr(wf, "WhisperFinal", _FakeWhisper)
    _stub_speaker_model(monkeypatch)
    e = Engines()
    assert _load_sync(e) == [("ready",)]
    assert e.sherpa == "sherpa" and isinstance(e.whisper, _FakeWhisper)


def test_engines_gpu_failure_is_reported_as_gpu_problem(app, monkeypatch):
    import scribe.asr.sherpa_stream as ss
    import scribe.asr.whisper_final as wf

    def no_gpu(*a, **k):
        raise wf.GpuUnavailableError("CUDA GPU를 찾지 못했습니다")

    monkeypatch.setattr(ss, "SherpaStreaming", lambda *a, **k: "sherpa")
    monkeypatch.setattr(wf, "WhisperFinal", no_gpu)
    _stub_speaker_model(monkeypatch)
    got = _load_sync(Engines())
    assert got == [("failed", "gpu", "CUDA GPU를 찾지 못했습니다")]


def test_engines_draft_model_failure_is_reported(app, monkeypatch):
    import scribe.asr.sherpa_stream as ss
    import scribe.asr.whisper_final as wf

    def broken(*a, **k):
        raise FileNotFoundError("tokens.txt")

    monkeypatch.setattr(ss, "SherpaStreaming", broken)
    monkeypatch.setattr(wf, "WhisperFinal", _FakeWhisper)
    got = _load_sync(Engines())
    assert got[0][0] == "failed" and got[0][1] == "other" and "tokens.txt" in got[0][2]


def test_switch_final_frees_old_model_then_loads_selected(app, monkeypatch):
    import scribe.asr.whisper_final as wf

    loaded = []

    class Recorder(_FakeWhisper):
        def __init__(self, model=None, **k):
            loaded.append(model)

    monkeypatch.setattr(wf, "WhisperFinal", Recorder)
    e = Engines(final_model="large-v3-turbo")
    e.whisper = "old"
    got = []
    e.ready.connect(lambda: got.append("ready"))
    e.final_model = "large-v3"
    e._switch()
    assert loaded == ["large-v3"] and isinstance(e.whisper, Recorder) and got == ["ready"]


def test_settings_roundtrip_and_bad_values(tmp_path, monkeypatch):
    from scribe import settings

    monkeypatch.setenv("MEETING_SCRIBE_HOME", str(tmp_path))
    assert settings.load().final_model == "large-v3-turbo"  # default, no file yet
    settings.save(settings.Settings(final_model="large-v3"))
    assert settings.load().final_model == "large-v3"
    (tmp_path / "settings.json").write_text('{"final_model": "tiny", "x": 1}', encoding="utf-8")
    assert settings.load().final_model == "large-v3-turbo"  # unknown model -> default
    (tmp_path / "settings.json").write_text("{broken", encoding="utf-8")
    assert settings.load().final_model == "large-v3-turbo"


def test_runner_reports_not_running_inside_finished_handler(app, monkeypatch):
    """finished is emitted from the session thread; handlers must already see running=False
    (otherwise the UI stays in "recording" and ignores model changes after a save)."""
    import scribe.pipeline.session as sess_mod
    from scribe.gui.engine import SessionRunner

    class FakeSession:
        def __init__(self, *a, **k):
            self.final = None

        def run(self, on_event):
            return "x/transcript.md"

    monkeypatch.setattr(sess_mod, "Session", FakeSession)
    r = SessionRunner(Engines())
    seen = []
    r.finished.connect(lambda md: seen.append((md, r.running)))
    r._active = True
    r._run({}, None, "")
    assert seen == [("x/transcript.md", False)]


def test_chips_show_speaker_letters_names_and_are_clickable(view):
    view.add_final("others", 1.0, "안건 시작합니다", speaker="A")
    view.add_final("others", 4.0, "네 좋습니다", speaker="B")
    view.add_final("me", 7.0, "동의합니다")
    body = blocks(view)[HEADER_BLOCKS:]
    assert "상대 A" in body[0] and "상대 B" in body[1] and "나" in body[2]
    assert "상대 A, 상대 B, 나" in blocks(view)[1]  # properties line
    clicked = []
    view.speakerClicked.connect(clicked.append)
    view.anchorClicked.emit(__import__("PySide6.QtCore", fromlist=["QUrl"]).QUrl("spk:others:B"))
    assert clicked == ["others:B"]
    view.set_draft("others", "others-000009", 9.0, "다음")  # live draft survives a rename
    view.set_names({"others:A": "김팀장"})
    body = blocks(view)[HEADER_BLOCKS:]
    assert "김팀장" in body[0] and "상대 A" not in "".join(body)
    assert body[-1].endswith("다음 …")
    assert "김팀장, 상대 B, 나" in blocks(view)[1]


def test_new_meeting_starts_without_previous_names(view):
    view.set_names({"others:A": "김팀장"})
    view.reset("다음 회의", names={})
    view.add_final("others", 1.0, "안녕하세요", speaker="A")
    assert "상대 A" in blocks(view)[HEADER_BLOCKS]


def test_rename_flow_on_saved_meeting(app, tmp_path, monkeypatch):
    import json

    import scribe.gui.app as gui_app
    from scribe.store.transcript import load_names

    monkeypatch.setenv("MEETING_SCRIBE_HOME", str(tmp_path / "home"))  # never touch real settings
    monkeypatch.setattr(gui_app.Engines, "load", lambda self: None)
    monkeypatch.setattr(gui_app.GpuMonitor, "start", lambda self: None)
    out = tmp_path / "회의록"
    d = out / "20261010-103000"
    d.mkdir(parents=True)
    rows = [
        {"type": "final", "channel": "others", "t_start": 1.0, "t_end": 3.0, "text": "시작합니다",
         "meta": {"speaker": "A"}},
        {"type": "final", "channel": "others", "t_start": 4.0, "t_end": 6.0, "text": "좋습니다",
         "meta": {"speaker": "B"}},
    ]
    (d / "transcript.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                                        encoding="utf-8")
    w = gui_app.MainWindow(out)
    item = w.sessions.item(0)
    w._open_item(item)
    monkeypatch.setattr(gui_app.QInputDialog, "getText", lambda *a, **k: ("김팀장", True))
    w.archive.speakerClicked.emit("others:A")
    assert load_names(d) == {"others:A": "김팀장"}
    assert "김팀장" in w.archive.toPlainText()
    md = (d / "transcript.md").read_text(encoding="utf-8")
    assert "**김팀장**" in md and "**상대 B**" in md
    # clearing the name returns to the default label
    monkeypatch.setattr(gui_app.QInputDialog, "getText", lambda *a, **k: ("", True))
    w.archive.speakerClicked.emit("others:A")
    assert load_names(d) == {} and "상대 A" in w.archive.toPlainText()
    w.mini.allow_close = True
    w.mini.close()


def _window_with_meeting(tmp_path, monkeypatch):
    import json

    import scribe.gui.app as gui_app

    monkeypatch.setenv("MEETING_SCRIBE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(gui_app.Engines, "load", lambda self: None)
    monkeypatch.setattr(gui_app.GpuMonitor, "start", lambda self: None)
    out = tmp_path / "회의록"
    d = out / "20261010-103000"
    d.mkdir(parents=True)
    row = {"type": "final", "channel": "others", "t_start": 1.0, "t_end": 3.0, "text": "시작합니다",
           "meta": {"speaker": "A"}}
    (d / "transcript.jsonl").write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    return gui_app, gui_app.MainWindow(out), d


def _close(w):
    w.mini.allow_close = True
    w.mini.close()


def test_title_click_renames_meeting_everywhere(app, tmp_path, monkeypatch):
    from scribe.store.transcript import load_title

    gui_app, w, d = _window_with_meeting(tmp_path, monkeypatch)
    w._open_item(w.sessions.item(0))
    monkeypatch.setattr(gui_app.QInputDialog, "getText", lambda *a, **k: ("주간 개발 회의", True))
    # the title is a clickable anchor that opens the rename dialog
    w.archive.anchorClicked.emit(__import__("PySide6.QtCore", fromlist=["QUrl"]).QUrl("title:"))
    assert load_title(d) == "주간 개발 회의"
    assert blocks(w.archive)[0] == "주간 개발 회의"
    assert (d / "transcript.md").read_text(encoding="utf-8").startswith("# 주간 개발 회의")
    row = w.sessions.itemWidget(w.sessions.item(0))
    assert row.title.text() == "주간 개발 회의"  # sidebar shows the title (date underneath)
    monkeypatch.setattr(gui_app.QInputDialog, "getText", lambda *a, **k: ("  ", True))
    w._rename_title(w.archive)  # blank -> back to the default
    assert load_title(d) is None and blocks(w.archive)[0] == "회의록 2026-10-10 10:30"
    _close(w)


def test_trash_button_moves_meeting_out_of_list(app, tmp_path, monkeypatch):
    import shutil

    gui_app, w, d = _window_with_meeting(tmp_path, monkeypatch)
    trashed = []
    monkeypatch.setattr(gui_app, "move_to_trash", lambda p: (trashed.append(p), shutil.rmtree(p)))
    w._open_item(w.sessions.item(0))
    w.sessions.itemWidget(w.sessions.item(0)).deleteClicked.emit()
    assert trashed == [d] and w.sessions.count() == 0
    assert w.archive_dir is None and w.stack.currentWidget() is w.live

    def fail(p):
        raise OSError("in use")

    d.mkdir()
    (d / "transcript.jsonl").write_text("", encoding="utf-8")
    w._refresh_sessions()
    monkeypatch.setattr(gui_app, "move_to_trash", fail)
    w.sessions.itemWidget(w.sessions.item(0)).deleteClicked.emit()
    assert w.sessions.count() == 1 and not w.callout.isHidden()
    _close(w)


def test_pause_button_colour_follows_state(app, tmp_path, monkeypatch):
    gui_app, w, _ = _window_with_meeting(tmp_path, monkeypatch)
    w._engine_state = "ready"
    monkeypatch.setattr(type(w.runner), "running", property(lambda self: True))
    monkeypatch.setattr(type(w.runner), "paused", property(lambda self: False), raising=False)
    w._sync_controls()
    assert (w.pause_btn.objectName(), w.start_btn.objectName()) == ("pause", "stop")
    monkeypatch.setattr(type(w.runner), "paused", property(lambda self: True), raising=False)
    w._sync_controls()
    assert w.pause_btn.objectName() == "resume" and w.pause_btn.text() == "재개"
    _close(w)
