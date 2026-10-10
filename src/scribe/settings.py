"""User settings persisted in ~/.meeting-scribe/settings.json (shared by GUI and CLI)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from scribe import config


def _default_api_models() -> dict[str, str]:
    return {p: info["models"][0] for p, info in config.API_PROVIDERS.items()}


@dataclass
class Settings:
    final_model: str = config.WHISPER_MODEL
    # keep audio: per-channel FLAC during the meeting + one mixed "전체 녹음.flac" at the end;
    # off = no audio is written at all (transcript only)
    record_audio: bool = True
    # final pass: "local" (GPU Whisper) or an external API ("openai" / "openrouter"). Never
    # changed by the app on its own; an unknown value is kept and reported, not coerced.
    final_backend: str = "local"
    api_models: dict[str, str] = field(default_factory=_default_api_models)  # provider -> model
    openrouter_zdr: bool = True  # route only to providers that keep no data (OpenRouter ZDR)


def _path() -> Path:
    return config.app_dir() / "settings.json"


def _str_dict(value) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str) and v}


def load() -> Settings:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    known = {f.name for f in fields(Settings)}
    s = Settings(**{k: v for k, v in data.items() if k in known})
    if s.final_model not in config.FINAL_MODELS:  # e.g. edited by hand
        s.final_model = config.WHISPER_MODEL
    s.record_audio = bool(s.record_audio)
    s.openrouter_zdr = bool(s.openrouter_zdr)
    if not isinstance(s.final_backend, str):
        s.final_backend = repr(s.final_backend)  # still invalid -> reported by spec_from_settings
    models = _default_api_models()
    models.update({p: m for p, m in _str_dict(s.api_models).items() if p in models})
    s.api_models = models
    return s


def save(s: Settings) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(s), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
