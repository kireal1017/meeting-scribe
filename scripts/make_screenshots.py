"""Render README screenshots (docs/images) from the real widgets with example data.

Nothing is captured from the screen: the windows are never shown, devices and meetings are fake,
and numbered markers point at the controls the README explains.

    uv run python scripts/make_screenshots.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

os.environ["MEETING_SCRIBE_HOME"] = tempfile.mkdtemp()  # never touch real settings

from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QPainter, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import scribe.audio.capture as capture  # noqa: E402
import scribe.gui.app as gui_app  # noqa: E402
from scribe.gui import theme  # noqa: E402
from scribe.store.transcript import save_title  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "docs" / "images"
SPEAKERS = capture.DeviceInfo(1, "스피커 (Realtek Audio) [Loopback]", 2, 48000, True)
MIC = capture.DeviceInfo(2, "마이크 배열 (Realtek Audio)", 2, 48000, False)
LINES = [
    ("others", "A", 3, "오늘 안건은 두 가지입니다. 먼저 다음 주 배포 일정부터 보겠습니다."),
    ("others", "A", 11, "QA는 이번 주 금요일까지 마무리하는 걸로 하죠."),
    ("others", "B", 18, "네, 결제 페이지 수정만 끝나면 바로 QA에 들어갈 수 있습니다."),
    ("me", None, 26, "저는 API 문서 업데이트를 맡겠습니다."),
    ("others", "C", 33, "모니터링 알림은 제가 대시보드에 추가해 둘게요."),
    ("others", "A", 41, "좋습니다. 그럼 두 번째 안건으로 넘어가겠습니다."),
]


def badge(p: QPainter, n: int, at: QPoint) -> None:
    r = QRect(at.x() - 13, at.y() - 13, 26, 26)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#e03e3e"))
    p.drawEllipse(r)
    p.setPen(QColor("#ffffff"))
    p.setFont(theme.font(14, QFont.Weight.Bold))
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, str(n))


def pos_in(w, child, x: int = 0, y: int = 0) -> QPoint:
    return child.mapTo(w, QPoint(x, y))


def doc_point(w, view, position: int) -> QPoint:
    c = QTextCursor(view.document())
    c.setPosition(position)
    r = view.cursorRect(c)
    return view.viewport().mapTo(w, r.center())


def main() -> None:
    capture.list_devices = lambda: [SPEAKERS, MIC]
    capture.default_devices = lambda: (SPEAKERS, MIC)
    gui_app.Engines.load = lambda self: None
    gui_app.GpuMonitor.start = lambda self: None
    app = QApplication(sys.argv)
    app.setStyleSheet(theme.STYLESHEET)
    app.setFont(theme.font(14))

    out = Path(tempfile.mkdtemp())
    for name, title in (("20261009-140000", "주간 개발 회의"), ("20261008-091500", None),
                        ("20261007-163000", "고객사 요구사항 정리")):
        d = out / name
        d.mkdir()
        (d / "transcript.jsonl").write_text(json.dumps(
            {"type": "final", "channel": "others", "t_start": 1, "t_end": 2, "text": "-"}), "utf-8")
        if title:
            save_title(d, title)

    w = gui_app.MainWindow(out)
    w.resize(1280, 800)
    started = datetime(2026, 10, 10, 10, 30)
    w.live_dir = out / "20261010-103000"
    type(w.runner).running = property(lambda self: True)
    type(w.runner).paused = property(lambda self: False)
    w._engine_state = "ready"
    for v in w._views():
        v.reset("스프린트 계획 회의", started, names={"others:A": "김팀장"})
        for ch, spk, t, text in LINES:
            v.add_final(ch, t, text, speaker=spk)
        v.set_draft("others", "others-000009", 47, "첫 번째 항목은 로그인 화면 개편인데요")
        v.update_properties(48)
    w._sync_controls()
    w._refresh_sessions()
    for widget in (w.loop_combo, w.mic_combo, w.mic_check, w.record_check, w.model_combo):
        widget.setEnabled(False)
    w.status.setText("●  기록 중  00:00:48   ·   확정 대기 0초")
    w.gpu.setText("RTX 2050   ·   VRAM 2.1 / 4 GB   ·   54°C")
    w.sessions.itemWidget(w.sessions.item(2)).trash.setVisible(True)  # as when hovered
    app.processEvents()

    pm = w.grab()
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    below = w.loop_combo.height() + 18  # markers sit just under the top-bar controls
    badge(p, 1, pos_in(w, w.loop_combo, w.loop_combo.width() // 2, below))
    badge(p, 2, pos_in(w, w.mini_btn, w.mini_btn.width() // 2, below))
    badge(p, 3, pos_in(w, w.pause_btn, w.pause_btn.width(), below))
    badge(p, 4, doc_point(w, w.live, 0) + QPoint(-20, 0))
    chip_block = w.live.document().findBlockByNumber(3)
    badge(p, 5, doc_point(w, w.live, chip_block.position() + 10) + QPoint(52, -16))
    row = w.sessions.itemWidget(w.sessions.item(2))
    badge(p, 6, pos_in(w, row.trash, row.trash.width() + 14, row.trash.height() // 2))
    badge(p, 7, pos_in(w, w.record_check, w.record_check.width() - 30, -10))
    p.end()
    OUT.mkdir(parents=True, exist_ok=True)
    pm.save(str(OUT / "main.png"))

    w.mini.set_state("recording", "00:00:48")
    app.processEvents()
    w.mini.grab().save(str(OUT / "mini.png"))
    w.mini.allow_close = True
    w.mini.close()
    print("saved", OUT / "main.png", OUT / "mini.png")


if __name__ == "__main__":
    main()
