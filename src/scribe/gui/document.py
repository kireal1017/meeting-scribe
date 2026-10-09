"""The transcript rendered as a Notion-like page inside one QTextDocument.

  회의록 2026-10-10 00:19                       <- title block
  📅 2026년 10월 10일 00:19   ⏱ 1분 52초   🗣 상대, 나   <- properties block (updated live)

  00:00:02  상대   안녕하세요 공대생입니다 ...      <- one block per confirmed utterance
  00:00:17         결국 역사 속으로 ...             (speaker chip only when the speaker changes)
  00:01:52  나     네.
  ········  상대   에이이제와서그랬다가지고…        <- live drafts, always kept at the end

Finals are inserted just before the draft region, so drafts can be re-rendered cheaply and
the confirmed text above never moves. Wrapped lines hang under the text column (gutter).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextOption,
)
from PySide6.QtWidgets import QTextBrowser, QTextEdit

from scribe.gui import theme
from scribe.store.transcript import CHANNEL_LABEL, clock

GUTTER = 120  # px: timestamp + speaker chip column
PAGE_WIDTH = 820  # px: centred text column, like a Notion page
HEADER_BLOCKS = 3  # title, properties, spacer


@dataclass
class Draft:
    segment_id: str
    t_start: float
    text: str


def _char(px: int, color: str, weight=QFont.Weight.Normal, bg: str | None = None) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setFont(theme.font(px, weight))
    f.setForeground(QColor(color))
    if bg:
        f.setBackground(QColor(bg))
    return f


class TranscriptView(QTextBrowser):
    followChanged = Signal(bool)  # False when the user scrolled up away from the live end

    def __init__(self, parent=None, compact: bool = False) -> None:
        super().__init__(parent)
        # compact: phone-width layout (mini mode) - time/speaker on a small line above the text
        self.compact = compact
        self.setObjectName("transcript")
        self.setOpenLinks(False)
        self.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(0)
        self._follow = True
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self._query = ""
        self.reset("회의록")

    # --- page -------------------------------------------------------------
    def reset(self, title: str, started: datetime | None = None) -> None:
        doc = self.document()
        doc.clear()
        self._started = started
        self._duration_s: float | None = None
        self._speakers: list[str] = []
        self._count = 0
        self._last_speaker: str | None = None
        self._drafts: dict[str, Draft] = {}
        self._placeholder: str | None = None
        c = QTextCursor(doc)
        title_fmt = QTextBlockFormat()
        title_fmt.setTopMargin(16 if self.compact else 48)
        title_fmt.setBottomMargin(4 if self.compact else 8)
        c.setBlockFormat(title_fmt)
        c.insertText(title, _char(20 if self.compact else 32, theme.INK, QFont.Weight.Bold))
        props_fmt = QTextBlockFormat()
        props_fmt.setBottomMargin(6)
        c.insertBlock(props_fmt)
        spacer = QTextBlockFormat()
        spacer.setTopMargin(0)
        spacer.setBottomMargin(12)
        c.insertBlock(spacer)
        self.update_properties()
        self._follow = True

    def set_placeholder(self, text: str | None) -> None:
        """Grey hint shown in place of the transcript while it is empty (empty state)."""
        self._placeholder = text
        self._render_drafts()

    def update_properties(self, duration_s: float | None = None) -> None:
        if duration_s is not None:
            self._duration_s = duration_s
        parts = []
        if self._started:
            parts.append(f"📅 {self._started:%Y년 %m월 %d일 %H:%M}")
        if self._duration_s is not None:
            s = int(self._duration_s)
            parts.append(f"⏱ {s // 3600}시간 {s % 3600 // 60}분" if s >= 3600
                         else f"⏱ {s // 60}분 {s % 60}초")
        if self._speakers:
            parts.append("🗣 " + ", ".join(self._speakers))
        parts.append(f"발언 {self._count}개")
        c = QTextCursor(self.document().findBlockByNumber(1))
        c.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
        c.insertText(("   " if self.compact else "     ").join(parts),
                     _char(12 if self.compact else 14, theme.INK_MUTED))

    # --- live content -----------------------------------------------------
    def add_final(self, channel: str, t_start: float, text: str, live: bool = True) -> None:
        follow = live and self._at_end()
        label = CHANNEL_LABEL.get(channel, channel)
        if label not in self._speakers:
            self._speakers.append(label)
        c = QTextCursor(self.document())
        c.setPosition(self._draft_pos())
        self._insert_utterance(c, channel, t_start, text, show_chip=label != self._last_speaker,
                               draft=False)
        self._last_speaker = label
        self._count += 1
        self._drafts = {ch: d for ch, d in self._drafts.items() if ch != channel}
        self._render_drafts()
        self.update_properties()
        if live and self._query:
            self.highlight(self._query)
        if follow:
            self.scroll_to_end()

    def set_draft(self, channel: str, segment_id: str, t_start: float, text: str) -> None:
        follow = self._at_end()
        self._drafts[channel] = Draft(segment_id, t_start, text)
        self._render_drafts()
        if follow:
            self.scroll_to_end()

    def drop_draft(self, channel: str, segment_id: str) -> None:
        d = self._drafts.get(channel)
        if d and d.segment_id == segment_id:
            del self._drafts[channel]
            self._render_drafts()

    def load_rows(self, rows: list[dict]) -> None:
        """Render a saved session (rows from transcript.jsonl)."""
        finals = sorted((r for r in rows if r["type"] == "final" and r["text"]),
                        key=lambda r: r["t_start"])
        self.setUpdatesEnabled(False)
        try:
            for r in finals:
                self.add_final(r["channel"], r["t_start"], r["text"], live=False)
            self.update_properties(finals[-1]["t_end"] if finals else None)
        finally:
            self.setUpdatesEnabled(True)
        # open at the title; deferred because the layout of a just-shown page is not final yet
        QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(0))

    # --- search -----------------------------------------------------------
    def highlight(self, query: str) -> int:
        """Highlight every match; returns the count and scrolls to the first one."""
        self._query = query
        sels = []
        if query:
            doc = self.document()
            cur = doc.find(query, 0)
            while not cur.isNull():
                sel = QTextEdit.ExtraSelection()
                sel.cursor = cur
                sel.format.setBackground(QColor(theme.HIGHLIGHT))
                sels.append(sel)
                cur = doc.find(query, cur)
        self.setExtraSelections(sels)
        return len(sels)

    def find_next(self, query: str, backward: bool = False) -> bool:
        flags = QTextDocument.FindFlag.FindBackward if backward else QTextDocument.FindFlag(0)
        if self.find(query, flags):
            return True
        c = self.textCursor()  # wrap around
        c.movePosition(QTextCursor.MoveOperation.End if backward else
                       QTextCursor.MoveOperation.Start)
        self.setTextCursor(c)
        return self.find(query, flags)

    # --- internals --------------------------------------------------------
    def _draft_pos(self) -> int:
        """End of the last confirmed block (header blocks + one block per final);
        everything after it is the re-renderable draft/placeholder region."""
        block = self.document().findBlockByNumber(HEADER_BLOCKS - 1 + self._count)
        return block.position() + block.length() - 1

    def _insert_utterance(self, c: QTextCursor, channel: str, t_start: float, text: str,
                          show_chip: bool, draft: bool) -> None:
        fmt = QTextBlockFormat()
        if not self.compact:  # hanging indent: text column starts after the gutter
            fmt.setLeftMargin(GUTTER)
            fmt.setTextIndent(-GUTTER)
            fmt.setTabPositions([QTextOption.Tab(GUTTER, QTextOption.TabType.LeftTab)])
            fmt.setTopMargin(14 if show_chip else 4)
        else:
            fmt.setTopMargin(14 if show_chip else 8)
        fmt.setLineHeight(125, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        c.insertBlock(fmt)
        meta_px = 12 if self.compact else 13
        c.insertText("········" if draft else clock(t_start), _char(meta_px, theme.INK_FAINT))
        if show_chip:
            bg, fg = theme.SPEAKER_CHIP.get(channel, (theme.CANVAS_SOFT, theme.INK_SECONDARY))
            c.insertText("  ", _char(meta_px, theme.INK_FAINT))
            label = CHANNEL_LABEL.get(channel, channel)
            c.insertText(f" {label} ", _char(meta_px, fg, QFont.Weight.DemiBold, bg))
        # compact: a line separator keeps meta + text in ONE block (the draft region relies on
        # exactly one block per utterance)
        c.insertText(" " if self.compact else "\t", _char(16, theme.INK))
        c.insertText(text, _char(15 if self.compact else 16,
                                 theme.INK_FAINT if draft else theme.INK))

    def _render_drafts(self) -> None:
        c = QTextCursor(self.document())
        c.setPosition(self._draft_pos())
        c.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        c.removeSelectedText()
        for ch, d in self._drafts.items():
            self._insert_utterance(c, ch, d.t_start, d.text + " …", show_chip=True, draft=True)
        if self._placeholder and not self._count and not self._drafts:
            fmt = QTextBlockFormat()
            fmt.setTopMargin(24)
            c.insertBlock(fmt)
            c.insertText(self._placeholder, _char(16, theme.INK_FAINT))

    def _at_end(self) -> bool:
        sb = self.verticalScrollBar()
        return sb.value() >= sb.maximum() - 24

    def scroll_to_end(self) -> None:
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_scroll(self, _value: int) -> None:
        follow = self._at_end()
        if follow != self._follow:
            self._follow = follow
            self.followChanged.emit(follow)

    def resizeEvent(self, e) -> None:  # centre the page column
        side = 18 if self.compact else max(32, (self.width() - PAGE_WIDTH) // 2)
        self.setViewportMargins(side, 0, side, 0)
        super().resizeEvent(e)
