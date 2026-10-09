"""Crash-safe transcript storage: one JSON line per final/status event, flushed + fsynced."""

from __future__ import annotations

import json
import os
from pathlib import Path

from scribe.pipeline.events import Event


def _clock(t: float) -> str:
    t = int(t)
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


CHANNEL_LABEL = {"others": "상대", "me": "나"}


class TranscriptStore:
    def __init__(self, session_dir: Path) -> None:
        self.dir = session_dir
        self.jsonl = session_dir / "transcript.jsonl"
        self._f = open(self.jsonl, "a", encoding="utf-8")  # noqa: SIM115 (long-lived handle)

    def append(self, ev: Event) -> None:
        if ev.type == "partial":
            return  # drafts are UI-only; the disk keeps confirmed text
        self._f.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
        self._f.flush()
        os.fsync(self._f.fileno())

    def close(self) -> Path:
        self._f.close()
        return export_markdown(self.jsonl, self.dir / "transcript.md")


def load(jsonl: Path) -> list[dict]:
    rows = []
    with open(jsonl, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                break  # torn last line after a crash
    return rows


def export_markdown(jsonl: Path, out: Path) -> Path:
    rows = [r for r in load(jsonl) if r["type"] == "final"]
    rows.sort(key=lambda r: r["t_start"])
    lines = ["# 회의 전사", ""]
    for r in rows:
        who = CHANNEL_LABEL.get(r["channel"], r["channel"])
        lines.append(f"- `{_clock(r['t_start'])}` **{who}**: {r['text']}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
