"""Paths and constants. Runtime data (models, logs, sessions) lives outside the repo."""

from __future__ import annotations

import os
from pathlib import Path

SAMPLE_RATE = 16_000


def app_dir() -> Path:
    """~/.meeting-scribe by default.

    Not %LOCALAPPDATA%: when launched from a packaged (MSIX) app such as a Store-installed
    terminal or IDE, Windows silently redirects AppData writes into that app's private
    store, so models/venv written there are invisible to every other terminal.
    """
    override = os.environ.get("MEETING_SCRIBE_HOME")
    if override:
        return Path(override)
    return Path.home() / ".meeting-scribe"


def models_dir() -> Path:
    return _ensure(app_dir() / "models")


def logs_dir() -> Path:
    return _ensure(app_dir() / "logs")


PROJECT_ROOT = Path(__file__).resolve().parents[2]  # src/scribe/config.py -> repo root


def transcripts_dir() -> Path:
    """Default output: <project>/회의록/<시작시각>/... no matter where `scribe` is run from."""
    return PROJECT_ROOT / "회의록"


def _ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


# Whisper (final pass). GPU only — see asr/whisper_final.py.
WHISPER_MODEL = "large-v3-turbo"
# selectable final-pass models (faster-whisper names); all run on the GPU only
FINAL_MODELS = ["large-v3-turbo", "large-v3", "large-v2"]
WHISPER_COMPUTE_TYPE = "int8_float16"
VRAM_BUDGET_MB = 3072
