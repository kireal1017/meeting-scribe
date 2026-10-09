"""meeting-scribe desktop window (PySide6), styled after docs/DESIGN-notion.md.

  ┌ sidebar ───────┬ top bar: 상대 [장치▾]  나 [장치▾]   [검색]   (● 기록 시작) ┐
  │ 회의록          │ [callout: GPU problems]                                  │
  │ ● 기록 중       │                                                          │
  │ 10월 10일 00:19 │        page: title / properties / utterance blocks       │
  │ 10월 9일 23:46  │                                                          │
  │ [폴더 열기]     ├ footer: status · GPU ──────────────────────────────────── ┤
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from scribe.gui import theme
from scribe.gui.document import TranscriptView
from scribe.gui.engine import DoctorRunner, Engines, GpuMonitor, SessionRunner
from scribe.store.transcript import clock, load, session_started

WEEKDAY = "월화수목금토일"
LIVE = "__live__"


def _session_label(started: datetime) -> str:
    return f"{started:%m월 %d일}({WEEKDAY[started.weekday()]}) {started:%H:%M}"


def _button(text: str, name: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName(name)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFont(theme.font(14, QFont.Weight.Medium))
    return b


def _restyle(w: QWidget, name: str) -> None:
    w.setObjectName(name)
    w.style().unpolish(w)
    w.style().polish(w)


class GpuDialog(QDialog):
    """Shown when the GPU cannot be used. No CPU fallback: diagnose and fix together."""

    def __init__(self, message: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("GPU를 사용할 수 없습니다")
        self.resize(640, 460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(12)
        title = QLabel("GPU를 사용할 수 없습니다")
        title.setFont(theme.font(20, QFont.Weight.DemiBold))
        body = QLabel(f"{message}\n\n확정 자막은 GPU에서만 동작하며 CPU로 대신 실행하지 않습니다. "
                      "아래 진단을 실행해 실패한 단계와 조치 방법을 확인해 주세요.")
        body.setWordWrap(True)
        body.setStyleSheet(f"color: {theme.INK_SECONDARY};")
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(theme.font(13))
        self.output.setPlaceholderText("진단 결과가 여기에 표시됩니다.")
        row = QHBoxLayout()
        self.folder_btn = _button("리포트 폴더 열기", "utility")
        self.folder_btn.setEnabled(False)
        self.run_btn = _button("진단 실행", "primary")
        close = _button("닫기", "utility")
        row.addWidget(self.folder_btn)
        row.addStretch(1)
        row.addWidget(close)
        row.addWidget(self.run_btn)
        lay.addWidget(title)
        lay.addWidget(body)
        lay.addWidget(self.output, 1)
        lay.addLayout(row)
        close.clicked.connect(self.accept)
        self.run_btn.clicked.connect(self._run)
        self.folder_btn.clicked.connect(self._open_folder)
        self._report = ""
        self.doctor = DoctorRunner()
        self.doctor.done.connect(self._done)

    def _run(self) -> None:
        self.run_btn.setEnabled(False)
        self.output.setPlainText("진단 중입니다… (모델 로드를 포함해 최대 1분 정도 걸릴 수 있습니다)")
        self.doctor.start()

    def _done(self, ok: bool, text: str, report: str) -> None:
        self.output.setPlainText(("모든 점검을 통과했습니다. 앱을 다시 실행해 주세요.\n\n" if ok else "")
                                 + text)
        self._report = report
        self.folder_btn.setEnabled(bool(report))
        self.run_btn.setEnabled(True)

    def _open_folder(self) -> None:
        if self._report:
            os.startfile(Path(self._report).parent)  # noqa: S606 (local folder, Windows only)


class MainWindow(QMainWindow):
    def __init__(self, out_root: Path) -> None:
        super().__init__()
        self.out_root = out_root
        self.setWindowTitle("meeting-scribe")
        self.engines = Engines()
        self.runner = SessionRunner(self.engines)
        self.monitor = GpuMonitor()
        self.started: datetime | None = None
        self.live_dir: Path | None = None
        self._halted = False

        self._build()
        self._load_devices()
        self._refresh_sessions()

        self.engines.ready.connect(self._engines_ready)
        self.engines.failed.connect(self._engines_failed)
        self.runner.event.connect(self._on_event)
        self.runner.finished.connect(self._on_finished)
        self.runner.error.connect(self._on_error)
        self.monitor.stats.connect(self._on_gpu)
        self.tick = QTimer(self, interval=1000)
        self.tick.timeout.connect(self._on_tick)

        self.live.reset("새 회의록")
        self.live.set_placeholder("엔진을 불러오는 중입니다… (처음 한 번은 30초 정도 걸립니다)")
        self.status.setText("엔진 준비 중…")
        self.engines.load()
        self.monitor.start()

    # --- layout -------------------------------------------------------------
    def _build(self) -> None:
        root = QWidget(objectName="canvas")
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._build_sidebar())

        main = QWidget(objectName="page")
        v = QVBoxLayout(main)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._build_topbar())

        self.callout = QLabel(objectName="callout")
        self.callout.setWordWrap(True)
        self.callout.setFont(theme.font(14))
        self.callout.hide()
        callout_wrap = QHBoxLayout()
        callout_wrap.setContentsMargins(24, 12, 24, 0)
        callout_wrap.addWidget(self.callout)
        v.addLayout(callout_wrap)

        self.live = TranscriptView()
        self.archive = TranscriptView()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.live)
        self.stack.addWidget(self.archive)
        self.jump = _button("↓ 최신 내용으로", "jump")
        self.jump.hide()
        self.jump.clicked.connect(self.live.scroll_to_end)
        self.live.followChanged.connect(lambda f: self.jump.setVisible(
            not f and self.runner.running and self.stack.currentWidget() is self.live))
        overlay = QGridLayout()
        overlay.addWidget(self.stack, 0, 0)
        overlay.addWidget(self.jump, 0, 0,
                          Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
        overlay.setContentsMargins(0, 0, 0, 16)
        v.addLayout(overlay, 1)
        v.addWidget(self._build_footer())
        h.addWidget(main, 1)
        self.setCentralWidget(root)

    def _build_sidebar(self) -> QWidget:
        side = QWidget(objectName="sidebar")
        side.setFixedWidth(232)
        v = QVBoxLayout(side)
        v.setContentsMargins(10, 16, 10, 12)
        v.setSpacing(6)
        brand = QLabel("meeting-scribe")
        brand.setFont(theme.font(15, QFont.Weight.DemiBold))
        brand.setContentsMargins(8, 0, 0, 10)
        title = QLabel("회의록", objectName="sidebarTitle")
        title.setFont(theme.font(12, QFont.Weight.DemiBold, 0.125))
        self.sessions = QListWidget(objectName="sessionList")
        self.sessions.setFont(theme.font(14))
        self.sessions.itemClicked.connect(self._open_item)
        folder = _button("폴더 열기", "utility")
        folder.clicked.connect(lambda: os.startfile(self.out_root))  # noqa: S606
        v.addWidget(brand)
        v.addWidget(title)
        v.addWidget(self.sessions, 1)
        v.addWidget(folder)
        return side

    def _build_topbar(self) -> QWidget:
        bar = QWidget(objectName="topbar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 10, 16, 10)
        h.setSpacing(8)
        self.loop_combo = QComboBox()
        self.loop_combo.setMinimumWidth(200)
        self.mic_check = QCheckBox("내 마이크도 기록")
        self.mic_combo = QComboBox()
        self.mic_combo.setMinimumWidth(180)
        self.mic_check.toggled.connect(self.mic_combo.setEnabled)
        self.mic_check.setChecked(True)
        self.search = QLineEdit()
        self.search.setPlaceholderText("회의록에서 검색")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(220)
        self.search.textChanged.connect(self._on_search)
        self.search.returnPressed.connect(
            lambda: self._current_view().find_next(self.search.text()))
        self.start_btn = _button("준비 중…", "primary")
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._toggle)
        for w in (QLabel("상대"), self.loop_combo, self.mic_check, self.mic_combo):
            w.setFont(theme.font(14))
            h.addWidget(w)
        h.addStretch(1)
        h.addWidget(self.search)
        h.addSpacing(8)
        h.addWidget(self.start_btn)
        return bar

    def _build_footer(self) -> QWidget:
        foot = QWidget(objectName="footer")
        h = QHBoxLayout(foot)
        h.setContentsMargins(16, 6, 16, 6)
        self.status = QLabel()
        self.gpu = QLabel()
        for w in (self.status, self.gpu):
            w.setFont(theme.font(13))
        h.addWidget(self.status, 1)
        h.addWidget(self.gpu)
        return foot

    # --- data ---------------------------------------------------------------
    def _load_devices(self) -> None:
        try:
            from scribe.audio.capture import default_devices, list_devices

            loop_default, mic_default = default_devices()
            devices = list_devices()
        except Exception as e:
            self._show_callout(f"오디오 장치를 불러오지 못했습니다: {e}")
            return
        for d in devices:
            combo = self.loop_combo if d.loopback else self.mic_combo
            combo.addItem(d.name.replace(" [Loopback]", ""), d)
        for combo, default in ((self.loop_combo, loop_default), (self.mic_combo, mic_default)):
            for i in range(combo.count()):
                if default is not None and combo.itemData(i).index == default.index:
                    combo.setCurrentIndex(i)
        if self.mic_combo.count() == 0:
            self.mic_check.setChecked(False)
            self.mic_check.setEnabled(False)

    def _refresh_sessions(self, select: Path | None = None) -> None:
        self.sessions.clear()
        if self.runner.running and self.live_dir:
            item = QListWidgetItem("●  기록 중")
            item.setData(Qt.ItemDataRole.UserRole, LIVE)
            item.setForeground(Qt.GlobalColor.black)
            self.sessions.addItem(item)
        dirs = sorted((p for p in self.out_root.glob("*") if (p / "transcript.jsonl").exists()
                       and p != self.live_dir), reverse=True) if self.out_root.exists() else []
        for d in dirs:
            started = session_started(d)
            item = QListWidgetItem(_session_label(started) if started else d.name)
            item.setData(Qt.ItemDataRole.UserRole, str(d))
            item.setToolTip(str(d))
            self.sessions.addItem(item)
            if select and d == select:
                self.sessions.setCurrentItem(item)
        if self.runner.running:
            self.sessions.setCurrentRow(0)

    def _open_item(self, item: QListWidgetItem) -> None:
        key = item.data(Qt.ItemDataRole.UserRole)
        if key == LIVE:
            self.stack.setCurrentWidget(self.live)
        else:
            d = Path(key)
            started = session_started(d)
            self.archive.reset(f"회의록 {started:%Y-%m-%d %H:%M}" if started else d.name, started)
            self.archive.load_rows(load(d / "transcript.jsonl"))
            self.stack.setCurrentWidget(self.archive)
        self.jump.hide()
        self._on_search(self.search.text())

    def _current_view(self) -> TranscriptView:
        return self.stack.currentWidget()

    # --- engine / session ---------------------------------------------------
    def _engines_ready(self) -> None:
        self.start_btn.setText("기록 시작")
        self.start_btn.setEnabled(True)
        self.live.set_placeholder("‘기록 시작’을 누르면 Zoom에서 들리는 소리를 받아적습니다.")
        self.status.setText("준비 완료")

    def _engines_failed(self, message: str, gpu: bool) -> None:
        self.start_btn.setText("기록 불가")
        self.live.set_placeholder("엔진을 불러오지 못했습니다.")
        if gpu:
            self._show_callout("GPU를 사용할 수 없어 기록을 시작할 수 없습니다. "
                               "CPU로 대신 실행하지 않습니다 — 진단 창의 안내를 확인해 주세요.")
            GpuDialog(message, self).exec()
        else:
            self._show_callout("엔진을 불러오지 못했습니다:\n" + message.strip().splitlines()[-1])

    def _toggle(self) -> None:
        if self.runner.running:
            self.start_btn.setEnabled(False)
            self.start_btn.setText("저장 중…")
            self.runner.stop()
            return
        from scribe.audio.capture import LoopbackSource, MicSource

        loop = self.loop_combo.currentData()
        if loop is None:
            self._show_callout("상대방 소리를 받을 출력 장치(루프백)가 없습니다.")
            return
        sources = {"others": LoopbackSource(loop)}
        if self.mic_check.isChecked() and self.mic_combo.currentData() is not None:
            sources["me"] = MicSource(self.mic_combo.currentData())
        self.started = datetime.now()
        self.live_dir = self.out_root / f"{self.started:%Y%m%d-%H%M%S}"
        try:
            self.out_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self._show_callout(f"저장 폴더를 만들 수 없습니다: {self.out_root} ({e})")
            return
        self._halted = False
        self.callout.hide()
        self.live.reset(f"회의록 {self.started:%Y-%m-%d %H:%M}", self.started)
        self.live.set_placeholder("듣고 있습니다… 말소리가 들리면 여기에 바로 적힙니다.")
        self.stack.setCurrentWidget(self.live)
        self.runner.start(sources, self.live_dir)
        for w in (self.loop_combo, self.mic_combo, self.mic_check):
            w.setEnabled(False)
        self.start_btn.setText("■  기록 중지")
        _restyle(self.start_btn, "stop")
        self._refresh_sessions()
        self.tick.start()
        self._on_tick()

    def _on_event(self, ev) -> None:
        if ev.type == "partial":
            self.live.set_draft(ev.channel, ev.segment_id, ev.t_start, ev.text)
        elif ev.type == "final":
            if ev.text:
                self.live.add_final(ev.channel, ev.t_start, ev.text)
            else:
                self.live.drop_draft(ev.channel, ev.segment_id)
        elif ev.meta.get("halted"):
            self._halted = True
            self._show_callout(ev.text)
        elif "오류" in ev.text or "실패" in ev.text:
            self._show_callout(ev.text)
        else:
            self.status.setText(ev.text)

    def _on_finished(self, md: str) -> None:
        self.tick.stop()
        saved = self.live_dir
        self.live_dir = None
        self.start_btn.setText("기록 시작")
        self.start_btn.setEnabled(True)
        _restyle(self.start_btn, "primary")
        self.mic_check.setEnabled(self.mic_combo.count() > 0)
        self.loop_combo.setEnabled(True)
        self.mic_combo.setEnabled(self.mic_check.isChecked())
        self.jump.hide()
        self._refresh_sessions(select=saved)
        self.status.setText(f"저장 완료 · {Path(md).parent}" if md else "기록이 저장되지 않았습니다")

    def _on_error(self, tb: str) -> None:
        self._show_callout("기록 중 오류가 발생했습니다:\n" + tb.strip().splitlines()[-1])

    def _on_tick(self) -> None:
        if not self.started:
            return
        elapsed = (datetime.now() - self.started).total_seconds()
        self.live.update_properties(elapsed)
        session = self.runner.session
        backlog = session.backlog_s if session else 0.0
        state = "GPU 중단됨" if self._halted else f"확정 대기 {backlog:.0f}초"
        self.status.setText(f"●  기록 중  {clock(elapsed)}   ·   {state}")

    def _on_gpu(self, s) -> None:
        if s is None:
            self.gpu.setText("GPU 정보를 읽을 수 없음")
            return
        self.gpu.setText(f"{s['name'].replace('NVIDIA GeForce ', '')}   ·   "
                         f"VRAM {s['used_mb'] / 1024:.1f} / {s['total_mb'] / 1024:.0f} GB   ·   "
                         f"{s['temp_c']}°C")

    def _on_search(self, text: str) -> None:
        n = self._current_view().highlight(text.strip())
        if text.strip():
            self.status.setText(f"‘{text.strip()}’ {n}건")

    def _show_callout(self, text: str) -> None:
        self.callout.setText("⚠  " + text)
        self.callout.show()

    def closeEvent(self, e) -> None:
        if self.runner.running:
            ans = QMessageBox.question(self, "기록 중", "기록을 중지하고 저장한 뒤 종료할까요?")
            if ans != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
            self.runner.stop()
            self.runner.join(15)
        self.monitor.stop()
        super().closeEvent(e)


def main(argv: list[str] | None = None, out_root: Path | None = None) -> int:
    from scribe.config import transcripts_dir

    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setStyleSheet(theme.STYLESHEET)
    app.setFont(theme.font(14))
    win = MainWindow(out_root or transcripts_dir())
    win.resize(1280, 820)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
