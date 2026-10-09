"""Mini mode: a phone-portrait window that floats above Zoom.

  ╭──────────────────────────╮
  │          ────            │  <- drag handle (move the window)
  │ meeting-scribe  ● 00:12:34│  <- state pill
  │ 회의록 2026-10-10 10:30   │
  │ 00:12:01  상대            │  <- compact transcript (same live content)
  │ 배포는 목요일 오후에 …    │
  ├──────────────────────────┤
  │  ▶       ■        ←      │  <- bottom navigation
  │ 기록 시작 기록 중지 이전 페이지│
  ╰──────────────────────────╯
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from scribe.gui import theme
from scribe.gui.document import TranscriptView

PHONE = QSize(390, 760)
SHADOW = 14  # transparent margin around the phone for its soft shadow


def nav_icon(kind: str, color: str, size: int = 44) -> QIcon:
    """Simple filled glyphs drawn in code (no icon assets to ship)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    s = size
    if kind == "play":
        path = QPainterPath(QPointF(s * 0.34, s * 0.24))
        path.lineTo(s * 0.78, s * 0.5)
        path.lineTo(s * 0.34, s * 0.76)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "pause":
        p.drawRoundedRect(QRectF(s * 0.28, s * 0.24, s * 0.15, s * 0.52), 2, 2)
        p.drawRoundedRect(QRectF(s * 0.57, s * 0.24, s * 0.15, s * 0.52), 2, 2)
    elif kind == "stop":
        p.drawRoundedRect(QRectF(s * 0.28, s * 0.28, s * 0.44, s * 0.44), 4, 4)
    elif kind in ("back", "trash"):
        pen = p.pen()
        pen.setStyle(Qt.PenStyle.SolidLine)
        pen.setColor(QColor(color))
        pen.setWidthF(s * 0.075)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
    if kind == "trash":
        p.drawLine(QPointF(s * 0.2, s * 0.28), QPointF(s * 0.8, s * 0.28))  # lid
        p.drawLine(QPointF(s * 0.4, s * 0.17), QPointF(s * 0.6, s * 0.17))  # handle
        body = QPainterPath(QPointF(s * 0.28, s * 0.28))
        body.lineTo(s * 0.32, s * 0.82)
        body.lineTo(s * 0.68, s * 0.82)
        body.lineTo(s * 0.72, s * 0.28)
        p.drawPath(body)
    elif kind == "back":
        p.drawLine(QPointF(s * 0.74, s * 0.5), QPointF(s * 0.28, s * 0.5))
        path = QPainterPath(QPointF(s * 0.46, s * 0.3))
        path.lineTo(s * 0.26, s * 0.5)
        path.lineTo(s * 0.46, s * 0.7)
        p.drawPath(path)
    p.end()
    return QIcon(pm)


class NavButton(QToolButton):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.setObjectName("navButton")
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.setIconSize(QSize(26, 26))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(theme.font(12, QFont.Weight.Medium))
        self.setText(text)
        self.setMinimumHeight(58)
        self._look: tuple | None = None

    def set(self, text: str, kind: str, color: str, enabled: bool = True) -> None:
        color = color if enabled else theme.INK_FAINT
        if self._look == (text, kind, color, enabled):
            return  # called every second by the clock tick: skip re-rendering and re-styling
        self._look = (text, kind, color, enabled)
        self.setText(text)
        self.setIcon(nav_icon(kind, color))
        self.setStyleSheet(f"color: {color};")  # label in the icon's colour
        self.setEnabled(enabled)


class DragHeader(QWidget):
    """Frameless window: pressing on the header moves the whole window (native move)."""

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.window().windowHandle().startSystemMove()
        super().mousePressEvent(e)


class MiniWindow(QWidget):
    startPauseClicked = Signal()
    stopClicked = Signal()
    backClicked = Signal()

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("meeting-scribe (미니)")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.allow_close = False
        self.resize(PHONE.width() + 2 * SHADOW, PHONE.height() + 2 * SHADOW)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW, SHADOW, SHADOW, SHADOW)
        phone = QFrame(objectName="phone")
        shadow = QGraphicsDropShadowEffect(phone)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 45))
        phone.setGraphicsEffect(shadow)
        outer.addWidget(phone)

        v = QVBoxLayout(phone)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(0)

        self.header = DragHeader(objectName="miniHeader")
        self.header.setCursor(Qt.CursorShape.SizeAllCursor)
        hv = QVBoxLayout(self.header)
        hv.setContentsMargins(18, 0, 14, 8)
        hv.setSpacing(8)
        handle = QFrame(objectName="handle")
        handle.setFixedSize(40, 5)
        hv.addWidget(handle, 0, Qt.AlignmentFlag.AlignHCenter)
        row = QHBoxLayout()
        brand = QLabel("meeting-scribe")
        brand.setFont(theme.font(14, QFont.Weight.DemiBold))
        self.state = QLabel(objectName="statePill")
        self.state.setFont(theme.font(12, QFont.Weight.Medium))
        row.addWidget(brand)
        row.addStretch(1)
        row.addWidget(self.state)
        hv.addLayout(row)
        v.addWidget(self.header)

        self.view = TranscriptView(compact=True)
        v.addWidget(self.view, 1)

        nav = QFrame(objectName="navbar")
        nh = QHBoxLayout(nav)
        nh.setContentsMargins(6, 4, 6, 6)
        nh.setSpacing(0)
        self.start_pause = NavButton("기록 시작")
        self.stop = NavButton("기록 중지")
        self.back = NavButton("이전 페이지")
        for b in (self.start_pause, self.stop, self.back):
            nh.addWidget(b, 1)
        v.addWidget(nav)

        self.start_pause.clicked.connect(self.startPauseClicked)
        self.stop.clicked.connect(self.stopClicked)
        self.back.clicked.connect(self.backClicked)
        self.back.set("이전 페이지", "back", theme.INK_SECONDARY)
        self.set_state("loading")

    def set_state(self, state: str, elapsed: str = "") -> None:
        """state: loading | idle | recording | paused | saving | unavailable"""
        blue, ink, red = theme.PRIMARY, theme.INK_SECONDARY, theme.STOP
        if state == "recording":
            self.start_pause.set("일시중지", "pause", theme.PAUSE)
            self.stop.set("기록 중지", "stop", red)
            pill = f"●  기록 중 {elapsed}"
        elif state == "paused":
            self.start_pause.set("재개", "play", theme.RESUME)
            self.stop.set("기록 중지", "stop", red)
            pill = f"❚❚  일시중지 {elapsed}"
        elif state == "idle":
            self.start_pause.set("기록 시작", "play", blue)
            self.stop.set("기록 중지", "stop", ink, enabled=False)
            pill = "대기 중"
        elif state == "saving":
            self.start_pause.set("기록 시작", "play", blue, enabled=False)
            self.stop.set("저장 중…", "stop", ink, enabled=False)
            pill = "저장 중…"
        else:  # loading / unavailable
            self.start_pause.set("준비 중" if state == "loading" else "기록 불가", "play", blue,
                                 enabled=False)
            self.stop.set("기록 중지", "stop", ink, enabled=False)
            pill = "엔진 준비 중…" if state == "loading" else "GPU 사용 불가"
        self.state.setText(pill)
        live = state in ("recording", "paused")
        if self.state.property("live") != live:
            self.state.setProperty("live", live)
            self.state.style().unpolish(self.state)
            self.state.style().polish(self.state)

    def place_bottom_right(self) -> None:
        screen = (self.screen() or self.windowHandle().screen()).availableGeometry()
        self.move(screen.right() - self.width() - 12, screen.bottom() - self.height() - 12)

    def closeEvent(self, e) -> None:
        if self.allow_close:  # the app is exiting
            super().closeEvent(e)
            return
        e.ignore()  # Alt+F4 on the mini window behaves like "이전 페이지"
        self.backClicked.emit()
