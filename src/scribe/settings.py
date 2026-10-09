"""User settings persisted in ~/.meeting-scribe/settings.json (shared by GUI and CLI)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from scribe import config


@dataclass
class Settings:
    final_model: str = config.WHISPER_MODEL
    # keep audio: per-channel FLAC during the meeting + one mixed "전체 녹음.flac" at the end;
    # off = no audio is written at all (transcript only)
    record_audio: bool = True


def _path() -> Path:
    return config.app_dir() / "settings.json"


def load() -> Settings:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    known = {f.name for f in fields(Settings)}
    s = Settings(**{k: v for k, v in data.items() if k in known})
    if s.final_model not in config.FINAL_MODELS:  # e.g. edited by hand
        s.final_model = config.WHISPER_MODEL
    s.record_audio = bool(s.record_audio)
    return s


def save(s: Settings) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(s), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
