"""Types shared by every final-pass backend (local GPU Whisper, external APIs).

Kept apart from `whisper_final` so the API path never imports the GPU module.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FinalResult:
    text: str
    avg_logprob: float
    no_speech_prob: float
    compression_ratio: float
    # False when the backend returns text only (no Whisper confidence metrics); the
    # metric-based hallucination checks are then inactive (see FinalFilter)
    metrics: bool = True
    meta: dict | None = None  # backend extras for the event (e.g. API cost)


class GpuUnavailableError(RuntimeError):
    pass


class ApiConfigError(RuntimeError):
    """Missing key, unknown provider/model, invalid settings: fix in 설정, never retried."""


class ApiAuthError(RuntimeError):
    """401/403: the key is wrong or lacks access."""


class ApiRequestError(RuntimeError):
    """The server rejected this one request (400); later segments may still work."""


class ApiTransientError(RuntimeError):
    """Timeout, connection failure, 429 or 5xx that outlasted the per-job deadline."""
