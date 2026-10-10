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
NAMES_FILE = "speakers.json"
META_FILE = "meeting.json"  # {"title": ...} when the user renamed the page


def speaker_key(channel: str, speaker: str | None = None) -> str:
    """Stable id of who spoke: "me", "others:A".."others:J", or "others" when unknown."""
    return f"{channel}:{speaker}" if speaker else channel


def is_failed(row: dict) -> bool:
    """A final the external API could not confirm (kept as a marked gap with its draft)."""
    return not row.get("text") and (row.get("meta") or {}).get("rejected") == "api-error"


def row_speaker_key(row: dict) -> str:
    return speaker_key(row["channel"], (row.get("meta") or {}).get("speaker"))


def default_label(key: str) -> str:
    channel, _, letter = key.partition(":")
    base = CHANNEL_LABEL.get(channel, channel)
    return f"{base} {letter}" if letter else base


def load_names(session_dir: Path) -> dict[str, str]:
    """User-given names per speaker key, e.g. {"others:A": "김팀장"} (per meeting)."""
    try:
        data = json.loads((session_dir / NAMES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str) and v.strip()}


def save_names(session_dir: Path, names: dict[str, str]) -> None:
    _write_json(session_dir / NAMES_FILE,
                {k: v.strip() for k, v in names.items() if v and v.strip()})


def display_name(key: str, names: dict[str, str]) -> str:
    return names.get(key) or default_label(key)


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def default_title(started: datetime | None) -> str:
    return f"회의록 {started:%Y-%m-%d %H:%M}" if started else "회의록"


def load_title(session_dir: Path) -> str | None:
    """User-given page title, or None for the default one (per meeting)."""
    try:
        title = json.loads((session_dir / META_FILE).read_text(encoding="utf-8")).get("title")
    except (OSError, ValueError, AttributeError):
        return None
    return title.strip() if isinstance(title, str) and title.strip() else None


def save_title(session_dir: Path, title: str) -> None:
    """Blank title -> back to the default ("회의록 YYYY-MM-DD HH:MM")."""
    path = session_dir / META_FILE
    if title.strip():
        _write_json(path, {"title": title.strip()})
    else:
        path.unlink(missing_ok=True)


def session_title(session_dir: Path) -> str:
    return load_title(session_dir) or default_title(session_started(session_dir))


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
    rows = sorted((r for r in load(jsonl) if r["type"] == "final" and (r["text"] or is_failed(r))),
                  key=lambda r: r["t_start"])
    confirmed = [r for r in rows if r["text"]]  # failed gaps never count as utterances
    names = load_names(jsonl.parent)  # renamed speakers ("상대 A" -> "김팀장") show up here too
    started = session_started(jsonl.parent)
    title = session_title(jsonl.parent)
    props = []
    if started:
        props.append(f"📅 {started:%Y년 %m월 %d일 %H:%M}")
    if confirmed:
        props.append(f"⏱ {_duration(confirmed[-1]['t_end'])}")
        speakers = dict.fromkeys(display_name(row_speaker_key(r), names) for r in confirmed)
        props.append("🗣 " + ", ".join(speakers))
    props.append(f"발언 {len(confirmed)}개")
    lines = [f"# {title}", "", "> " + " · ".join(props), "", "---", ""]

    sections = bool(rows) and rows[-1]["t_end"] >= SECTION_S
    section = speaker = None
    for r in rows:
        if sections and int(r["t_start"] // SECTION_S) != section:
            section = int(r["t_start"] // SECTION_S)
            lines += [f"### {clock(section * SECTION_S)}", ""]
            speaker = None
        if not r["text"]:  # the external API could not confirm it: a marked gap, no speaker
            draft = (r.get("meta") or {}).get("draft") or ""
            lines += [f"`{clock(r['t_start'])}` ⚠ 확정 실패"
                      + (f" (임시 자막: {draft})" if draft else ""), ""]
            continue
        who = display_name(row_speaker_key(r), names)
        if who != speaker:
            lines += [f"**{who}**", ""]
            speaker = who
        lines += [f"`{clock(r['t_start'])}` {r['text']}", ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
