"""Audio sources that yield 16 kHz mono float32 blocks.

LoopbackSource — what the speakers play (Zoom participants) via WASAPI loopback.
MicSource      — the local microphone (you).
FileSource     — a file, optionally paced at real-time speed (testing / replays).

WASAPI loopback delivers *nothing* while no sound is being rendered, so live sources pad
the gap with silence based on the wall clock; otherwise timestamps would drift and the VAD
would never see the silence that closes a segment.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

from scribe.config import SAMPLE_RATE

BLOCK_S = 0.05  # 50 ms output blocks


@dataclass
class DeviceInfo:
    index: int
    name: str
    channels: int
    rate: int
    loopback: bool


def list_devices() -> list[DeviceInfo]:
    import pyaudiowpatch as pa

    p = pa.PyAudio()
    try:
        wasapi = p.get_host_api_info_by_type(pa.paWASAPI)
        out = []
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if d["hostApi"] != wasapi["index"] or d["maxInputChannels"] < 1:
                continue
            out.append(DeviceInfo(i, d["name"], d["maxInputChannels"],
                                  int(d["defaultSampleRate"]), bool(d.get("isLoopbackDevice"))))
        return out
    finally:
        p.terminate()


def default_devices() -> tuple[DeviceInfo, DeviceInfo | None]:
    """(default speaker loopback, default WASAPI microphone)."""
    import pyaudiowpatch as pa

    p = pa.PyAudio()
    try:
        lb = p.get_default_wasapi_loopback()
        loop = DeviceInfo(lb["index"], lb["name"], lb["maxInputChannels"],
                          int(lb["defaultSampleRate"]), True)
        wasapi = p.get_host_api_info_by_type(pa.paWASAPI)
        mic = None
        if wasapi.get("defaultInputDevice", -1) >= 0:
            m = p.get_device_info_by_index(wasapi["defaultInputDevice"])
            mic = DeviceInfo(m["index"], m["name"], m["maxInputChannels"],
                             int(m["defaultSampleRate"]), False)
        return loop, mic
    finally:
        p.terminate()


# PortAudio initialisation is not thread-safe: two channels creating PyAudio() at once from
# their worker threads crashes the process (access violation). Share one instance.
_pa_lock = threading.Lock()
_pa_instance = None
_pa_users = 0


def _pa_acquire():
    global _pa_instance, _pa_users
    import pyaudiowpatch as pa

    with _pa_lock:
        if _pa_instance is None:
            _pa_instance = pa.PyAudio()
        _pa_users += 1
        return _pa_instance


def _pa_release() -> None:
    global _pa_instance, _pa_users
    with _pa_lock:
        _pa_users -= 1
        if _pa_users == 0 and _pa_instance is not None:
            _pa_instance.terminate()
            _pa_instance = None


class _LiveSource:
    """PyAudioWPatch callback stream -> resample -> gap-padded 16 kHz blocks."""

    def __init__(self, device: DeviceInfo) -> None:
        self.device = device
        self._q: queue.Queue[np.ndarray] = queue.Queue()
        self._stop = threading.Event()
        self._resampler = soxr.ResampleStream(device.rate, SAMPLE_RATE, 1, dtype="float32")
        self.dropped_s = 0.0  # time padded with silence (no data from device)
        self._stream = None
        self._t0 = 0.0

    def _callback(self, in_data, frame_count, time_info, status):
        import pyaudiowpatch as pa

        x = np.frombuffer(in_data, dtype=np.float32).reshape(-1, self.device.channels)
        self._q.put(x.mean(axis=1))
        return (None, pa.paContinue)

    def stop(self) -> None:
        self._stop.set()

    def open(self) -> None:
        """Open the device stream. Call this from the thread that will also open the other
        sources (Session.run does it before spawning workers): WASAPI fails with
        "Unanticipated host error" when streams are opened from different threads."""
        if self._stream is not None:
            return
        import pyaudiowpatch as pa

        p = _pa_acquire()
        try:
            with _pa_lock:
                self._stream = p.open(
                    format=pa.paFloat32,
                    channels=self.device.channels,
                    rate=self.device.rate,
                    input=True,
                    input_device_index=self.device.index,
                    frames_per_buffer=int(self.device.rate * BLOCK_S),
                    stream_callback=self._callback,
                )
        except Exception:
            _pa_release()
            raise
        self._t0 = time.monotonic()

    def __iter__(self) -> Iterator[np.ndarray]:
        self.open()
        t0 = self._t0
        produced = 0  # 16 kHz samples handed out
        try:
            while not self._stop.is_set():
                try:
                    raw = self._q.get(timeout=0.1)
                except queue.Empty:
                    # device silent (loopback with nothing playing): pad up to wall clock,
                    # keeping a 100 ms margin so late-arriving real data is not double counted
                    expected = int((time.monotonic() - t0) * SAMPLE_RATE)
                    gap = expected - produced - int(0.1 * SAMPLE_RATE)
                    if gap > 0:
                        produced += gap
                        self.dropped_s += gap / SAMPLE_RATE
                        yield np.zeros(gap, np.float32)
                    continue
                y = self._resampler.resample_chunk(raw)
                if len(y):
                    produced += len(y)
                    yield y
        finally:
            self.close()

    def close(self) -> None:
        """Idempotent: stop/close the device stream and release the shared PyAudio."""
        with _pa_lock:
            stream, self._stream = self._stream, None
            if stream is None:
                return
            stream.stop_stream()
            stream.close()
        _pa_release()


class LoopbackSource(_LiveSource):
    pass


class MicSource(_LiveSource):
    pass


class FileSource:
    def __init__(self, path: Path, realtime: bool = False, tail_silence_s: float = 1.5) -> None:
        audio, sr = sf.read(str(path), dtype="float32", always_2d=True)
        audio = audio.mean(axis=1)
        if sr != SAMPLE_RATE:
            audio = soxr.resample(audio, sr, SAMPLE_RATE).astype(np.float32)
        self.audio = np.concatenate([audio, np.zeros(int(tail_silence_s * SAMPLE_RATE), np.float32)])
        self.realtime = realtime
        self._stop = threading.Event()
        self.started_at: float | None = None  # monotonic time of sample 0

    def stop(self) -> None:
        self._stop.set()

    def __iter__(self) -> Iterator[np.ndarray]:
        n = int(BLOCK_S * SAMPLE_RATE)
        self.started_at = time.monotonic()
        for i in range(0, len(self.audio), n):
            if self._stop.is_set():
                return
            if self.realtime:
                # deliver a block once its last sample "has been spoken"
                due = self.started_at + (i + n) / SAMPLE_RATE
                delay = due - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
            yield self.audio[i:i + n]
