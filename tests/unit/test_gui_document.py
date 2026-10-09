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
    monkeypatch.setattr(wf, "WhisperFinal", lambda: built.append(1) or "fresh")
    e = Engines()
    e.whisper = "preloaded"
    assert e.whisper_factory() == "preloaded"
    assert e.whisper is None  # session owns it now; nothing pins the VRAM
    assert e.whisper_factory() == "fresh" and built == [1]


def _load_sync(engines):
    got = []
    engines.ready.connect(lambda: got.append(("ready",)))
    engines.failed.connect(lambda msg, gpu: got.append(("failed", gpu, msg)))
    engines._load()  # run the loader inline (signals are direct within one thread)
    return got


class _FakeWhisper:
    def __init__(self, *a, **k):
        pass

    def transcribe(self, audio):
        return None


def test_engines_load_both_in_parallel_and_report_ready(app, monkeypatch):
    import scribe.asr.sherpa_stream as ss
    import scribe.asr.whisper_final as wf

    monkeypatch.setattr(ss, "SherpaStreaming", lambda *a, **k: "sherpa")
    monkeypatch.setattr(wf, "WhisperFinal", _FakeWhisper)
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
    got = _load_sync(Engines())
    assert got == [("failed", True, "CUDA GPU를 찾지 못했습니다")]


def test_engines_draft_model_failure_is_reported(app, monkeypatch):
    import scribe.asr.sherpa_stream as ss
    import scribe.asr.whisper_final as wf

    def broken(*a, **k):
        raise FileNotFoundError("tokens.txt")

    monkeypatch.setattr(ss, "SherpaStreaming", broken)
    monkeypatch.setattr(wf, "WhisperFinal", _FakeWhisper)
    got = _load_sync(Engines())
    assert got[0][0] == "failed" and got[0][1] is False and "tokens.txt" in got[0][2]
