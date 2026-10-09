"""Poll nvidia-smi for VRAM usage (works on laptop WDDM drivers where per-process numbers are N/A)."""

from __future__ import annotations

import subprocess
import threading
import time


def gpu_query(fields: str = "memory.used,temperature.gpu,clocks.sm") -> list[str] | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return [v.strip() for v in out.stdout.strip().splitlines()[0].split(",")]


def vram_used_mb() -> int | None:
    q = gpu_query("memory.used")
    return int(q[0]) if q else None


class VramSampler:
    """Background sampler: `with VramSampler() as s: ...; s.peak_delta_mb`."""

    def __init__(self, interval: float = 0.2) -> None:
        self.interval = interval
        self.baseline: int | None = None
        self.peak: int | None = None
        self._stop = threading.Event()
        self._t: threading.Thread | None = None

    def __enter__(self) -> VramSampler:
        self.baseline = vram_used_mb()
        self.peak = self.baseline
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()
        return self

    def _run(self) -> None:
        while not self._stop.is_set():
            v = vram_used_mb()
            if v is not None and (self.peak is None or v > self.peak):
                self.peak = v
            time.sleep(self.interval)

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._t:
            self._t.join()

    @property
    def peak_delta_mb(self) -> int | None:
        if self.baseline is None or self.peak is None:
            return None
        return self.peak - self.baseline
