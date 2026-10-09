"""App icon (assets/icon.svg) for the title bar and the Windows taskbar."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ICON_SVG = Path(__file__).parent / "assets" / "icon.svg"
APP_ID = "meeting-scribe.desktop"
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def _artwork_bounds(svg: str) -> QRectF | None:
    """Bounding box of the path coordinates. The artwork fills only about half of its canvas,
    so cropping to it keeps the glyph legible at 16-32 px."""
    pairs = re.findall(r"[ML]\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)", svg)
    if not pairs:
        return None
    xs = [float(x) for x, _ in pairs]
    ys = [float(y) for _, y in pairs]
    return QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def app_icon() -> QIcon:
    if not ICON_SVG.exists():
        return QIcon()
    svg = ICON_SVG.read_text(encoding="utf-8")
    renderer = QSvgRenderer(svg.encode("utf-8"))
    box = _artwork_bounds(svg)
    if box is not None:  # square viewBox around the artwork with a small margin
        side = max(box.width(), box.height()) * 1.08
        renderer.setViewBox(QRectF(box.center().x() - side / 2, box.center().y() - side / 2,
                                   side, side))
    icon = QIcon()
    for size in SIZES:
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(p)
        p.end()
        icon.addPixmap(pm)
    return icon


def set_windows_app_id() -> None:
    """Without an explicit AppUserModelID, Windows groups the window under pythonw.exe and
    shows Python's icon in the taskbar instead of ours. Must run before any window is shown."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
