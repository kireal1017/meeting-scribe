"""Always-on FLAC recording of the raw (16 kHz mono) audio, rolled over every N minutes so
a crash can lose at most the currently open file's unflushed tail."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from scribe.config import SAMPLE_RATE


class FlacRecorder:
    def __init__(self, directory: Path, prefix: str, rollover_s: float = 600.0) -> None:
        self.dir = directory
        self.prefix = prefix
        self.rollover = int(rollover_s * SAMPLE_RATE)
        self.part = -1
        self._file: sf.SoundFile | None = None
        self._in_part = 0
        self._since_flush = 0
        self.files: list[Path] = []

    def _open_next(self) -> None:
        self.close()
        self.part += 1
        path = self.dir / f"{self.prefix}-{self.part:03d}.flac"
        self._file = sf.SoundFile(str(path), mode="w", samplerate=SAMPLE_RATE, channels=1,
                                  format="FLAC", subtype="PCM_16")
        self.files.append(path)
        self._in_part = 0

    def write(self, block: np.ndarray) -> None:
        while len(block):
            if self._file is None or self._in_part >= self.rollover:
                self._open_next()
            take = min(len(block), self.rollover - self._in_part)
            self._file.write(np.clip(block[:take], -1.0, 1.0))
            self._in_part += take
            self._since_flush += take
            block = block[take:]
        if self._since_flush >= 2 * SAMPLE_RATE:
            self._file.flush()
            self._since_flush = 0

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
