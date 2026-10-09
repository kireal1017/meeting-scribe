"""After a meeting: one listenable file with everyone ("전체 녹음.flac").

Both channel recordings start at the session start and are gap-padded to the wall clock, so
they are already aligned; they are summed block by block (a 5-hour meeting is ~1.2 GB of
float32 per channel, too much to load at once).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import soundfile as sf

from scribe.config import SAMPLE_RATE

FULL_RECORDING = "전체 녹음.flac"
BLOCK = SAMPLE_RATE * 10


def _blocks(parts: list[Path]) -> Iterator[np.ndarray]:
    """Concatenation of the rolled-over FLAC parts of one channel, in fixed-size blocks."""
    carry = np.zeros(0, np.float32)
    for part in parts:
        with sf.SoundFile(str(part)) as f:
            for b in f.blocks(blocksize=BLOCK, dtype="float32"):
                carry = np.concatenate([carry, b])
                while len(carry) >= BLOCK:
                    yield carry[:BLOCK]
                    carry = carry[BLOCK:]
    if len(carry):
        yield carry


def mixdown(session_dir: Path) -> Path | None:
    channels = [sorted(session_dir.glob(f"audio-{ch}-*.flac")) for ch in ("others", "me")]
    channels = [parts for parts in channels if parts]
    if not channels:
        return None
    out = session_dir / FULL_RECORDING
    tmp = out.with_suffix(".part.flac")
    streams = [_blocks(parts) for parts in channels]
    with sf.SoundFile(str(tmp), "w", samplerate=SAMPLE_RATE, channels=1,
                      format="FLAC", subtype="PCM_16") as dst:
        while True:
            blocks = [next(s, None) for s in streams]
            live = [b for b in blocks if b is not None]
            if not live:
                break
            n = max(len(b) for b in live)
            mix = np.zeros(n, np.float32)
            for b in live:
                mix[:len(b)] += b
            dst.write(np.clip(mix, -1.0, 1.0))
    tmp.replace(out)
    return out
