"""Crash-safe transcript storage: one JSON line per final/status event, flushed + fsynced."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from scribe.pipeline.events import Event


def clock(t: float) -> str:
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


SECTION_S = 600  # a "### 10:00" heading every 10 minutes in meetings longer than that


def session_started(session_dir: Path) -> datetime | None:
    """Session folders are named by start time (YYYYmmdd-HHMMSS)."""
    try:
        return datetime.strptime(session_dir.name, "%Y%m%d-%H%M%S")
    except ValueError:
        return None


def _duration(seconds: float) -> str:
    s = int(seconds)
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return f"{h}시간 {m}분" if h else f"{m}분 {s}초"


def export_markdown(jsonl: Path, out: Path) -> Path:
    """Notion-friendly page: title, a property line, then speaker-grouped paragraphs.
    Importing the .md into Notion keeps headings, quote and inline-code timestamps."""
    rows = sorted((r for r in load(jsonl) if r["type"] == "final" and r["text"]),
                  key=lambda r: r["t_start"])
    started = session_started(jsonl.parent)
    title = f"회의록 {started:%Y-%m-%d %H:%M}" if started else "회의록"
    props = []
    if started:
        props.append(f"📅 {started:%Y년 %m월 %d일 %H:%M}")
    if rows:
        props.append(f"⏱ {_duration(rows[-1]['t_end'])}")
        speakers = dict.fromkeys(CHANNEL_LABEL.get(r["channel"], r["channel"]) for r in rows)
        props.append("🗣 " + ", ".join(speakers))
    props.append(f"발언 {len(rows)}개")
    lines = [f"# {title}", "", "> " + " · ".join(props), "", "---", ""]

    sections = bool(rows) and rows[-1]["t_end"] >= SECTION_S
    section = speaker = None
    for r in rows:
        if sections and int(r["t_start"] // SECTION_S) != section:
            section = int(r["t_start"] // SECTION_S)
            lines += [f"### {clock(section * SECTION_S)}", ""]
            speaker = None
        who = CHANNEL_LABEL.get(r["channel"], r["channel"])
        if who != speaker:
            lines += [f"**{who}**", ""]
            speaker = who
        lines += [f"`{clock(r['t_start'])}` {r['text']}", ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
