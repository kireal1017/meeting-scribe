"""The one place that builds a final-pass backend.

Every caller (GUI engines, session restarts, CLI) goes through `create_final(spec)` with a spec
fixed when loading starts, so the backend can never change on its own: a GPU failure never
uploads audio, an API failure never loads the GPU model (the user's explicit choice only).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from scribe import config
from scribe.asr.types import ApiConfigError, FinalResult


class FinalBackend(Protocol):
    remote: bool
    label: str

    def transcribe(self, audio: np.ndarray, prompt: str | None = None) -> FinalResult: ...

    def set_draining(self) -> None: ...


@dataclass(frozen=True)
class FinalSpec:
    backend: str  # "local" | "openai" | "openrouter"
    model: str  # faster-whisper model (local) or the provider's model id
    zdr: bool = True  # OpenRouter: zero-data-retention endpoints only

    @property
    def remote(self) -> bool:
        return self.backend != "local"


def spec_from_settings(s, model: str | None = None, zdr: bool | None = None) -> FinalSpec:
    """Snapshot of the saved choice. Overrides are for the CLI (one run, not persisted)."""
    if s.final_backend not in config.FINAL_BACKENDS:
        raise ApiConfigError(f"알 수 없는 확정 자막 엔진 설정입니다: {s.final_backend!r}. "
                             "설정에서 엔진을 다시 골라 주세요.")
    if s.final_backend == "local":
        return FinalSpec("local", model or s.final_model)
    return FinalSpec(
        s.final_backend,
        model or s.api_models.get(s.final_backend)
        or config.API_PROVIDERS[s.final_backend]["models"][0],
        zdr=s.openrouter_zdr if zdr is None else zdr,
    )


def create_final(spec: FinalSpec, api_key: str | None = None) -> FinalBackend:
    """api_key: a key typed in the settings dialog but not saved yet (connection test);
    otherwise the stored key / environment variable of that provider is used."""
    if spec.backend == "local":
        from scribe.asr.whisper_final import WhisperFinal

        return WhisperFinal(model=spec.model)
    if spec.backend not in config.API_PROVIDERS:
        raise ApiConfigError(f"알 수 없는 확정 자막 엔진입니다: {spec.backend!r}")
    from scribe import api_keys
    from scribe.asr.remote_final import RemoteFinal  # the only import site of remote_final

    return RemoteFinal(spec, api_key or api_keys.api_key(spec.backend))
