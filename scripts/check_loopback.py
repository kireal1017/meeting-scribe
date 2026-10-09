"""Loopback capture check: play a fixture through the default output device while capturing it
back with WASAPI loopback, then report level, timing and gap padding.

Usage: uv run python scripts/check_loopback.py [seconds]
"""

from __future__ import annotations

import sys
import threading
import time
import winsound
from pathlib import Path

import numpy as np

from scribe.audio.capture import LoopbackSource, default_devices
from scribe.config import SAMPLE_RATE

WAV = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "meeting_planning.wav"


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    loop, _ = default_devices()
    print(f"loopback device: {loop.name} ({loop.channels}ch {loop.rate}Hz)")
    src = LoopbackSource(loop)
    blocks: list[np.ndarray] = []

    def capture() -> None:
        for b in src:
            blocks.append(b)

    t = threading.Thread(target=capture, daemon=True)
    t.start()
    time.sleep(1.0)  # 1 s of nothing playing -> must be padded with silence
    winsound.PlaySound(str(WAV), winsound.SND_FILENAME | winsound.SND_ASYNC)
    time.sleep(seconds)
    winsound.PlaySound(None, winsound.SND_PURGE)
    time.sleep(0.5)
    src.stop()
    t.join(timeout=2)

    audio = np.concatenate(blocks) if blocks else np.zeros(0, np.float32)
    dur = len(audio) / SAMPLE_RATE
    wall = seconds + 1.5
    rms_first = float(np.sqrt(np.mean(audio[: int(0.8 * SAMPLE_RATE)] ** 2))) if len(audio) else 0
    rms_play = float(np.sqrt(np.mean(audio[int(2 * SAMPLE_RATE):] ** 2))) if len(audio) else 0
    print(f"captured {dur:.2f}s of 16 kHz audio over ~{wall:.1f}s wall clock")
    print(f"padded silence (device idle): {src.dropped_s:.2f}s")
    print(f"RMS before playback: {rms_first:.5f}, during playback: {rms_play:.4f}")
    ok = rms_play > 0.005 and abs(dur - wall) < 0.6
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
