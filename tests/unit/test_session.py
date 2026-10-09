"""Pipeline wiring with fake engines (no GPU, no model downloads beyond the bundled VAD)."""

import threading
import time
from pathlib import Path

import numpy as np
import pytest

from scribe.asr.types import FinalResult
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


class PausingSource:
    """Feeds a fixture in 50 ms blocks and pauses/resumes the session at given seconds."""

    def __init__(self, path, pause_at, resume_at):
        self.blocks = list(FileSource(path, realtime=False))
        self.pause_at, self.resume_at = pause_at, resume_at
        self.session = None

    def __iter__(self):
        for i, b in enumerate(self.blocks):
            t = i * 0.05
            if abs(t - self.pause_at) < 0.025:
                self.session.pause()
            if abs(t - self.resume_at) < 0.025:
                self.session.resume()
            yield b


def test_pause_skips_speech_but_keeps_timeline(tmp_path):
    import json

    import soundfile as sf

    meta = json.loads((FIX / "meeting_tech.json").read_text(encoding="utf-8"))
    s = meta["sentences"]
    # pause from the gap before sentence 3 until the gap after sentence 4
    pause_at = round((s[1]["end"] + s[2]["start"]) / 2, 2)
    resume_at = round((s[3]["end"] + s[4]["start"]) / 2, 2)
    src = PausingSource(FIX / "meeting_tech.wav", pause_at, resume_at)
    sess = Session({"others": src}, FakeWhisper, FakeSherpa(), silero_vad_path(), tmp_path)
    src.session = sess
    events = []
    sess.run(events.append)
    finals = [e for e in events if e.type == "final"]
    assert len(finals) == 5  # sentences 3 and 4 were said while paused
    after = [f for f in finals if f.t_start > resume_at]
    assert [round(f.t_start) for f in after] == [round(x["start"]) for x in s[4:]]
    assert [e.text for e in events if e.type == "status"] == ["일시중지", "기록 재개"]
    audio, _ = sf.read(str(next(tmp_path.glob("audio-others-*.flac"))), dtype="float32")
    assert abs(len(audio) - len(np.concatenate(src.blocks))) < 1600  # aligned with timeline
    paused = audio[int((pause_at + 0.1) * 16000):int((resume_at - 0.1) * 16000)]
    assert np.abs(paused).max() == 0.0  # nothing recorded while paused


class FakeRemote(FakeWhisper):
    """An API backend whose calls fail according to a script ("ok" / an exception)."""

    remote = True
    label = "OpenAI · whisper-1"

    def __init__(self, script):
        super().__init__()
        self.script = list(script)
        self.draining = False

    def set_draining(self):
        self.draining = True

    def transcribe(self, audio, prompt=None):
        step = self.script.pop(0) if self.script else "ok"
        if step != "ok":
            self.prompts.append(prompt)
            raise step
        res = super().transcribe(audio, prompt)
        res.meta = {"provider": "openai", "upload_s": 1.0}
        return res


def _remote_run(tmp_path, script):
    made = []

    def factory():
        made.append(1)
        return FakeRemote(script)

    events, _ = run_session(tmp_path, factory, record=False)
    return events, made


def test_api_failure_leaves_marked_gap_and_never_rebuilds(tmp_path):
    from scribe.asr.types import ApiTransientError

    events, made = _remote_run(tmp_path, ["ok", ApiTransientError("timeout"), "ok"])
    finals = [e for e in events if e.type == "final"]
    assert len(made) == 1  # no restart / reload for an API backend
    gap = finals[1]
    assert gap.text == "" and gap.meta["rejected"] == "api-error"
    assert gap.meta["draft"] == "초안 끝" and "timeout" in gap.meta["api_error"]
    assert all(f.text for i, f in enumerate(finals) if i != 1)
    assert finals[0].engine == "OpenAI · whisper-1" and finals[0].meta["provider"] == "openai"
    status = [e for e in events if e.type == "status"]
    assert [bool(e.meta.get("callout")) for e in status] == [True]  # one callout, no halt
    rows = load(tmp_path / "transcript.jsonl")
    assert sum(r["type"] == "final" and r["meta"].get("rejected") == "api-error" for r in rows) == 1


def test_api_three_strikes_halt_and_success_resets(tmp_path):
    from scribe.asr.types import ApiTransientError

    t = ApiTransientError("HTTP 503")
    events, made = _remote_run(tmp_path, [t, t, "ok", t, t, t])
    finals = [e for e in events if e.type == "final"]
    status = [e for e in events if e.type == "status"]
    assert len(made) == 1
    # fail, fail, ok (streak reset), fail, fail, fail -> halted; the 7th segment is skipped
    assert [f.meta.get("rejected") for f in finals] == ["api-error", "api-error", None,
                                                       "api-error", "api-error", "api-error"]
    halts = [e for e in status if e.meta.get("halted")]
    assert len(halts) == 1 and halts[0].meta.get("remote") and "외부 API" in halts[0].text
    assert "scribe doctor" not in halts[0].text and "GPU" not in halts[0].text
    assert sum(bool(e.meta.get("callout")) for e in status) == 2  # one per failure streak


def test_api_auth_error_halts_immediately(tmp_path):
    from scribe.asr.types import ApiAuthError

    events, made = _remote_run(tmp_path, [ApiAuthError("API 키를 확인해 주세요")])
    finals = [e for e in events if e.type == "final"]
    assert len(made) == 1 and len(finals) == 1 and finals[0].meta["rejected"] == "api-error"
    assert sum(bool(e.meta.get("halted")) for e in events if e.type == "status") == 1
    assert [e for e in events if e.type == "partial"]  # drafts keep flowing


def test_api_failure_does_not_feed_prompt_or_speakers(tmp_path):
    from scribe.asr.types import ApiRequestError

    w = FakeRemote(["ok", ApiRequestError("audio too short")])
    assigned = []

    class Tracker:
        def assign(self, audio):
            assigned.append(len(audio))
            return "A"

    src = FileSource(FIX / "meeting_planning.wav", realtime=False)
    sess = Session({"others": src}, lambda: w, FakeSherpa(), silero_vad_path(), tmp_path,
                   record=False, speakers=Tracker())
    events = []
    sess.run(events.append)
    finals = [e for e in events if e.type == "final"]
    assert len(assigned) == sum(bool(f.text) for f in finals) == 6
    # the failed 2nd segment did not change the context: the 3rd prompt equals the 2nd
    assert "확정" in w.prompts[1] and w.prompts[2] == w.prompts[1]
    assert "speaker" not in finals[1].meta


def test_stop_puts_api_backend_in_draining_mode(tmp_path):
    w = FakeRemote([])
    sess = Session({"others": SilentLiveSource()}, lambda: w, FakeSherpa(), silero_vad_path(),
                   tmp_path, record=False)
    threading.Timer(0.3, sess.stop).start()
    sess.run()
    assert w.draining and sess.whisper is w  # `whisper` stays as an alias of `final`
