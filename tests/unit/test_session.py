"""Pipeline wiring with fake engines (no GPU, no model downloads beyond the bundled VAD)."""

import threading
import time
from pathlib import Path

import numpy as np
import pytest

from scribe.asr.whisper_final import FinalResult
from scribe.audio.capture import FileSource
from scribe.models import silero_vad_path
from scribe.pipeline.session import Session
from scribe.store.transcript import load

FIX = Path(__file__).resolve().parents[1] / "fixtures"


class FakeStream:
    def __init__(self):
        self.n = 0

    def accept(self, audio):
        self.n += len(audio)
        return f"초안{self.n // 8000}"

    def finish(self):
        return "초안 끝"


class FakeSherpa:
    def new_stream(self):
        return FakeStream()


class FakeWhisper:
    def __init__(self):
        self.prompts = []

    def transcribe(self, audio, prompt=None):
        self.prompts.append(prompt)
        return FinalResult(f"확정 {len(audio) // 1600}", -0.1, 0.01, 1.1)


class FlakyWhisper(FakeWhisper):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def transcribe(self, audio, prompt=None):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("CUDA error: an illegal memory access")
        return super().transcribe(audio, prompt)


def run_session(tmp_path, whisper_factory, record=True):
    src = FileSource(FIX / "meeting_planning.wav", realtime=False)
    events = []
    sess = Session({"others": src}, whisper_factory, FakeSherpa(), silero_vad_path(), tmp_path,
                   hotwords="QA, 스프린트", record=record)
    md = sess.run(events.append)
    return events, md


def test_partials_then_finals_share_segment_id(tmp_path):
    w = FakeWhisper()
    events, md = run_session(tmp_path, lambda: w)
    finals = [e for e in events if e.type == "final"]
    partial_ids = {e.segment_id for e in events if e.type == "partial"}
    assert len(finals) == 7  # one per fixture sentence
    for f in finals:
        assert f.segment_id in partial_ids
        first_partial = next(i for i, e in enumerate(events) if e.segment_id == f.segment_id)
        assert first_partial < events.index(f)
        assert f.meta["draft"] == "초안 끝"
    # hotwords always in the prompt, previous final appended after the first
    assert w.prompts[0] == "QA, 스프린트"
    assert w.prompts[1].startswith("QA, 스프린트 확정")
    rows = load(tmp_path / "transcript.jsonl")
    assert sum(r["type"] == "final" for r in rows) == 7
    assert md.exists()
    assert list(tmp_path.glob("audio-others-*.flac"))


def test_gpu_error_reports_status_and_restarts_once(tmp_path):
    made = []

    def factory():
        w = FlakyWhisper() if not made else FakeWhisper()
        made.append(w)
        return w

    events, _ = run_session(tmp_path, factory, record=False)
    status = [e.text for e in events if e.type == "status"]
    assert any("GPU 전사 오류" in s for s in status)
    assert any("재시작 완료" in s for s in status)
    assert len(made) == 2
    assert sum(e.type == "final" for e in events) == 6  # the failed segment stays only in FLAC


class AlwaysFailingWhisper(FakeWhisper):
    def transcribe(self, audio, prompt=None):
        raise RuntimeError("CUDA error: device lost")


def test_gpu_failure_after_restart_halts_finals_once(tmp_path):
    made = []

    def factory():
        made.append(1)
        return AlwaysFailingWhisper()

    events, _ = run_session(tmp_path, factory, record=False)
    status = [e for e in events if e.type == "status"]
    assert len(made) == 2  # initial + one restart, never more (and never a CPU model)
    assert sum(bool(e.meta.get("halted")) for e in status) == 1
    assert sum("GPU 전사 오류" in e.text for e in status) == 2  # no per-segment error spam
    assert not [e for e in events if e.type == "final"]
    assert [e for e in events if e.type == "partial"]  # drafts keep flowing


class SilentLiveSource:
    """Mimics a live device in silence: endless zero blocks until stop()."""

    def __init__(self):
        self._stop = threading.Event()
        self.opened = self.closed = False

    def open(self):
        self.opened = True

    def close(self):
        self.closed = True

    def stop(self):
        self._stop.set()

    def __iter__(self):
        while not self._stop.is_set():
            time.sleep(0.05)
            yield np.zeros(800, np.float32)


def test_stop_during_silence_returns_promptly_and_closes(tmp_path):
    src = SilentLiveSource()
    sess = Session({"others": src}, FakeWhisper, FakeSherpa(), silero_vad_path(), tmp_path,
                   record=False)
    threading.Timer(0.5, sess.stop).start()
    t0 = time.monotonic()
    md = sess.run()
    assert time.monotonic() - t0 < 2.0
    assert src.opened and src.closed
    assert md.exists()


def test_store_closed_even_if_consumer_raises(tmp_path):
    def boom(ev):
        raise RuntimeError("ui crashed")

    src = FileSource(FIX / "meeting_tech.wav", realtime=False)
    sess = Session({"others": src}, FakeWhisper, FakeSherpa(), silero_vad_path(), tmp_path,
                   record=False)
    with pytest.raises(RuntimeError):
        sess.run(boom)
    assert (tmp_path / "transcript.md").exists()


def test_file_source_realtime_pacing():
    src = FileSource(FIX / "meeting_tech.wav", realtime=True, tail_silence_s=0)
    src.audio = src.audio[: 16000 // 2]  # 0.5 s
    t0 = time.monotonic()
    n = sum(len(b) for b in src)
    assert n == 8000
    assert 0.45 <= time.monotonic() - t0 < 0.8
