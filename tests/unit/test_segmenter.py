import json
from pathlib import Path

import numpy as np
import soundfile as sf

from scribe.config import SAMPLE_RATE
from scribe.models import silero_vad_path
from scribe.vad.segmenter import FRAME, SegmentEnd, Segmenter, SegmentStart, SileroVAD

FIX = Path(__file__).resolve().parents[1] / "fixtures"


class EnergyVAD:
    """Deterministic stand-in: 'speech' iff the frame is loud."""

    def __call__(self, frame):
        return 0.9 if np.abs(frame).mean() > 0.05 else 0.05


def tone(seconds):
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def silence(seconds):
    return np.zeros(int(seconds * SAMPLE_RATE), np.float32)


def run(seg, audio, block=800):
    evs = []
    for i in range(0, len(audio), block):
        evs += seg.process(audio[i:i + block])
    return evs + seg.flush()


def test_silence_produces_nothing():
    assert run(Segmenter(EnergyVAD()), silence(5)) == []


def test_single_utterance_boundaries():
    audio = np.concatenate([silence(1), tone(2), silence(1.5)])
    evs = run(Segmenter(EnergyVAD()), audio)
    starts = [e for e in evs if isinstance(e, SegmentStart)]
    ends = [e for e in evs if isinstance(e, SegmentEnd)]
    assert len(starts) == 1 and len(ends) == 1
    end = ends[0]
    # start includes ~200 ms pre-roll before the 1.0 s onset; end at the 3.0 s offset
    assert abs(end.start / SAMPLE_RATE - 0.8) < 0.1
    assert abs(end.end / SAMPLE_RATE - 3.0) < 0.1
    assert len(end.audio) == end.end - end.start
    assert not end.forced


def test_two_utterances_get_increasing_indices():
    audio = np.concatenate([tone(1), silence(1), tone(1), silence(1)])
    ends = [e for e in run(Segmenter(EnergyVAD()), audio) if isinstance(e, SegmentEnd)]
    assert [e.index for e in ends] == [0, 1]
    assert ends[0].end <= ends[1].start


def test_long_speech_is_force_cut_under_max_length():
    audio = np.concatenate([tone(40), silence(1)])
    ends = [e for e in run(Segmenter(EnergyVAD()), audio) if isinstance(e, SegmentEnd)]
    assert len(ends) >= 3
    assert all((e.end - e.start) / SAMPLE_RATE <= 15.0 + FRAME / SAMPLE_RATE for e in ends)
    assert any(e.forced for e in ends)
    # contiguous: nothing lost between forced cuts
    for a, b in zip(ends, ends[1:], strict=False):
        assert a.end == b.start


def test_unclosed_segment_is_flushed():
    ends = [e for e in run(Segmenter(EnergyVAD()), tone(2)) if isinstance(e, SegmentEnd)]
    assert len(ends) == 1


def test_silero_on_fixture_finds_every_sentence():
    meta = json.loads((FIX / "meeting_tech.json").read_text(encoding="utf-8"))
    audio, sr = sf.read(str(FIX / "meeting_tech.wav"), dtype="float32")
    assert sr == SAMPLE_RATE
    ends = [e for e in run(Segmenter(SileroVAD(silero_vad_path())), audio)
            if isinstance(e, SegmentEnd)]
    for s in meta["sentences"]:
        covered = any(e.start / SAMPLE_RATE <= s["start"] + 0.3 and
                      e.end / SAMPLE_RATE >= s["end"] - 0.3 for e in ends)
        assert covered, s["text"]


def test_skip_keeps_timestamps_aligned_and_ignores_skipped_audio():
    seg = Segmenter(EnergyVAD())
    run(seg, np.concatenate([tone(1), silence(1)]))  # one utterance at 0..1 s
    seg.skip(int(3 * SAMPLE_RATE))  # 3 s paused (speech there is never seen)
    ends = [e for e in run(seg, np.concatenate([tone(1), silence(1)])) if isinstance(e, SegmentEnd)]
    assert len(ends) == 1
    # resumes at 5 s; no pre-roll exists from before the pause, so the segment starts there
    assert abs(ends[0].start / SAMPLE_RATE - 5.0) < 0.05
