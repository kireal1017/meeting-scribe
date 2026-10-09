"""Terminal view.

Append-only on purpose: drafts stream as grey chunks as they are recognized
("  상대 ▸ 에이 이제와서 그랬다가지고 봤는데"), and the confirmed sentence follows on its own line.
Rewriting a line with "\r" breaks as soon as wide Korean text wraps past the terminal width
(only the last physical row is cleared, so the whole draft piles up again on every update).
"""

from __future__ import annotations

import sys
from typing import TextIO

from scribe.store.transcript import CHANNEL_LABEL as LABEL
from scribe.store.transcript import clock, default_label, speaker_key

GREY, RESET = "\x1b[90m", "\x1b[0m"


def _new_part(shown: str, text: str) -> str:
    """Text not printed yet. Drafts normally only grow; if the recognizer revised earlier
    characters, everything after the common prefix is printed again."""
    k = 0
    for a, b in zip(shown, text, strict=False):
        if a != b:
            break
        k += 1
    return text[k:]


class ConsoleView:
    def __init__(self, out: TextIO | None = None) -> None:
        self.out = out or sys.stdout
        self.drafts: dict[str, tuple[str, str]] = {}  # channel -> (segment_id, text shown)
        self.open_channel: str | None = None  # channel whose grey draft line is unfinished

    def __call__(self, ev) -> None:
        if ev.type == "partial":
            self._draft(ev)
        elif ev.type == "final":
            self._final(ev)
        else:
            self._end_line()
            self.out.write(f"[상태] {ev.text}\n")
        self.out.flush()

    def _draft(self, ev) -> None:
        label = LABEL.get(ev.channel, ev.channel)
        seg, shown = self.drafts.get(ev.channel, (None, ""))
        if seg != ev.segment_id:
            shown = ""  # new utterance on this channel
        if self.open_channel != ev.channel or not shown:
            self._end_line()
            self.out.write(f"{GREY}  {label} ▸{' …' if shown else ''}{RESET}")
            self.open_channel = ev.channel
        new = _new_part(shown, ev.text)
        if new:
            self.out.write(f"{GREY} {new}{RESET}")
        self.drafts[ev.channel] = (ev.segment_id, ev.text)

    def _final(self, ev) -> None:
        self._end_line()
        if self.drafts.get(ev.channel, (None,))[0] == ev.segment_id:
            del self.drafts[ev.channel]
        meta = ev.meta or {}
        if ev.text:
            label = default_label(speaker_key(ev.channel, meta.get("speaker")))
            self.out.write(f"[{clock(ev.t_start)}] {label}: {ev.text}\n")
        elif meta.get("rejected") == "api-error":  # marked gap: the API could not confirm it
            draft = meta.get("draft") or ""
            self.out.write(f"[{clock(ev.t_start)}] {default_label(ev.channel)}: ⚠ 확정 실패"
                           f"{f' (임시: {draft})' if draft else ''}\n")

    def _end_line(self) -> None:
        if self.open_channel is not None:
            self.out.write("\n")
            self.open_channel = None
