"""Streaming speech segmentation with Silero VAD.

Feed arbitrary-sized 16 kHz float32 blocks; get back events:
  SegmentStart  — speech began (includes pre-roll audio so the partial recognizer sees onset)
  SegmentAudio  — more audio belonging to the open segment
  SegmentEnd    — segment closed (silence, or force-cut at max length) with its full audio
All positions are absolute sample indices since the stream started.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from scribe.config import SAMPLE_RATE

FRAME = 512  # samples per VAD frame (32 ms)
CONTEXT = 64  # Silero v6 context samples prepended to each frame


class SileroVAD:
    def __init__(self, path: Path) -> None:
        import onnxruntime

        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 4
        self.session = onnxruntime.InferenceSession(
            str(path), providers=["CPUExecutionProvider"], sess_options=opts
        )
        self.reset()

    def reset(self) -> None:
        self.h = np.zeros((1, 1, 128), dtype=np.float32)
        self.c = np.zeros((1, 1, 128), dtype=np.float32)
        self.context = np.zeros(CONTEXT, dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        x = np.concatenate([self.context, frame])[None, :].astype(np.float32)
        out, self.h, self.c = self.session.run(None, {"input": x, "h": self.h, "c": self.c})
        self.context = frame[-CONTEXT:]
        return float(np.asarray(out).reshape(-1)[0])


@dataclass
class SegmentStart:
    index: int
    start: int  # sample index (after pre-roll)
    audio: np.ndarray  # pre-roll + onset audio


@dataclass
class SegmentAudio:
    index: int
    audio: np.ndarray


@dataclass
class SegmentEnd:
    index: int
    start: int
    end: int  # sample index where speech ended (silence tail excluded)
    audio: np.ndarray
    forced: bool


@dataclass
class VadParams:
    start_threshold: float = 0.5
    end_threshold: float = 0.35
    min_speech_ms: int = 200
    min_silence_ms: int = 500
    pre_roll_ms: int = 200
    max_segment_s: float = 15.0
    cut_search_s: float = 2.0  # where to look for the quietest point when force-cutting


class Segmenter:
    def __init__(self, vad, params: VadParams | None = None) -> None:
        self.vad = vad
        self.p = params or VadParams()
        ms = SAMPLE_RATE // 1000
        self.min_speech = self.p.min_speech_ms * ms // FRAME
        self.min_silence = self.p.min_silence_ms * ms // FRAME
        self.pre_roll_frames = max(1, self.p.pre_roll_ms * ms // FRAME)
        self.max_frames = int(self.p.max_segment_s * SAMPLE_RATE) // FRAME
        self.cut_search = int(self.p.cut_search_s * SAMPLE_RATE) // FRAME

        self._pending = np.zeros(0, dtype=np.float32)
        self._pos = 0  # sample index of next frame
        self._history: deque[np.ndarray] = deque(maxlen=self.pre_roll_frames + self.min_speech + 2)
        self._index = 0
        self._in_speech = False
        self._speech_run = 0
        self._silence_run = 0
        self._seg_frames: list[np.ndarray] = []
        self._seg_probs: list[float] = []
        self._seg_start = 0

    def process(self, block: np.ndarray) -> list:
        events: list = []
        buf = np.concatenate([self._pending, block.astype(np.float32, copy=False)])
        n = len(buf) // FRAME
        for i in range(n):
            events += self._frame(buf[i * FRAME:(i + 1) * FRAME])
        self._pending = buf[n * FRAME:]
        return events

    def flush(self) -> list:
        """Close any open segment (end of stream)."""
        if not self._in_speech:
            return []
        return [self._close(len(self._seg_frames) - self._silence_run, forced=False)]

    def skip(self, n: int) -> None:
        """Advance the timeline by n samples without listening (recording paused), so
        timestamps after resuming stay relative to the session start. Call flush() first."""
        self._pos += len(self._pending) + n
        self._pending = np.zeros(0, dtype=np.float32)
        self._history.clear()
        self._speech_run = 0
        reset = getattr(self.vad, "reset", None)
        if reset:
            reset()

    # --- internals -------------------------------------------------------
    def _frame(self, frame: np.ndarray) -> list:
        prob = self.vad(frame)
        self._pos += FRAME
        if not self._in_speech:
            self._history.append(frame)
            self._speech_run = self._speech_run + 1 if prob >= self.p.start_threshold else 0
            if self._speech_run >= self.min_speech:
                frames = list(self._history)[-(self._speech_run + self.pre_roll_frames):]
                self._in_speech = True
                self._silence_run = 0
                self._seg_frames = frames
                self._seg_probs = [1.0] * len(frames)
                self._seg_start = self._pos - len(frames) * FRAME
                self._history.clear()
                return [SegmentStart(self._index, self._seg_start, np.concatenate(frames))]
            return []

        self._seg_frames.append(frame)
        self._seg_probs.append(prob)
        events: list = [SegmentAudio(self._index, frame)]
        self._silence_run = self._silence_run + 1 if prob < self.p.end_threshold else 0
        if self._silence_run >= self.min_silence:
            events.append(self._close(len(self._seg_frames) - self._silence_run, forced=False))
        elif len(self._seg_frames) >= self.max_frames:
            events += self._force_cut()
        return events

    def _close(self, keep_frames: int, forced: bool) -> SegmentEnd:
        keep_frames = max(1, keep_frames)
        frames = self._seg_frames[:keep_frames]
        ev = SegmentEnd(
            index=self._index,
            start=self._seg_start,
            end=self._seg_start + keep_frames * FRAME,
            audio=np.concatenate(frames),
            forced=forced,
        )
        self._index += 1
        self._in_speech = False
        self._speech_run = 0
        self._silence_run = 0
        self._seg_frames, self._seg_probs = [], []
        return ev

    def _force_cut(self) -> list:
        """Cut at the quietest frame in the last few seconds; the rest opens the next segment."""
        total = len(self._seg_frames)
        lo = max(1, total - self.cut_search)
        cut = lo + int(np.argmin(self._seg_probs[lo:total]))
        rest_frames = self._seg_frames[cut:]
        rest_probs = self._seg_probs[cut:]
        end_ev = self._close(cut, forced=True)
        # continue speech immediately in a new segment
        self._in_speech = True
        self._seg_frames = rest_frames
        self._seg_probs = rest_probs
        self._seg_start = end_ev.end
        audio = np.concatenate(rest_frames) if rest_frames else np.zeros(0, np.float32)
        return [end_ev, SegmentStart(self._index, self._seg_start, audio)]
