"""The settings window (sidebar ‘설정’): recording, and which engine confirms the captions —
the local GPU or an external API (OpenAI / OpenRouter) with its model and key.

  ┌ 설정 ─────────────────────────────────────┐
  │ 녹음      [x] 전체 음성 녹음 저장           │
  │ 확정 자막 엔진 [로컬 GPU ▾]  모델 [turbo ▾] │
  │ ┌ API (외부 API를 고른 경우) ─────────────┐ │
  │ │ 키 [••••••]  저장된 키 있음  [삭제]      │ │
  │ │ [x] ZDR (OpenRouter)   [연결 테스트]     │ │
  │ └───────────────────────────────────────┘ │
  │                          [취소] [저장]     │
  └───────────────────────────────────────────┘
Nothing is applied until 저장; the main window then rebuilds the engine (an explicit user
action — the app never switches engines on its own).
"""

from __future__ import annotations

import threading
import time

import numpy as np
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from scribe import api_keys, config
from scribe.gui import theme

# local Whisper choices: (label, faster-whisper model, tooltip)
FINAL_CHOICES = [
    ("빠름 · large-v3-turbo", "large-v3-turbo",
     "권장. 문장 확정 약 1.5초, VRAM 약 1.3GB."),
    ("정확 · large-v3", "large-v3",
     "대화체 인식이 조금 더 좋을 수 있지만 문장 확정 약 3초, VRAM 최대 약 3.2GB.\n"
     "Zoom·브라우저가 GPU를 함께 쓰면 메모리가 부족해 확정 자막이 멈출 수 있습니다.\n"
     "처음 선택하면 모델(약 3GB)을 내려받습니다."),
]
# final-pass engines: (label, settings.final_backend, description)
ENGINE_CHOICES = [
    ("로컬 GPU", "local", "이 컴퓨터의 NVIDIA GPU에서 Whisper로 확정합니다. 음성이 밖으로 나가지 않습니다."),
    ("외부 API · OpenAI", "openai",
     "말한 구간(문장마다 1~15초)을 OpenAI로 보내 확정합니다. GPU가 필요 없고 사용량만큼 요금이 듭니다."),
    ("외부 API · OpenRouter", "openrouter",
     "말한 구간(문장마다 1~15초)을 OpenRouter로 보내 확정합니다. GPU가 필요 없고 요청별 요금이 "
     "표시됩니다."),
]


def _muted(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {theme.INK_MUTED};")
    label.setFont(theme.font(13))
    return label


def _section(text: str) -> QLabel:
    label = QLabel(text, objectName="sidebarTitle")
    label.setFont(theme.font(12, QFont.Weight.DemiBold, 0.125))
    return label


class _Tester(QObject):
    done = Signal(str)

    def run(self, final) -> None:
        threading.Thread(target=self._run, args=(final,), name="api-test", daemon=True).start()

    def _run(self, final) -> None:
        t0 = time.monotonic()
        try:
            res = final.transcribe(np.zeros(16_000, np.float32))
        except Exception as e:
            self.done.emit(f"실패: {e}")
            return
        parts = [f"성공 · 응답 {time.monotonic() - t0:.1f}초",
                 "무음 판별 정보 있음" if res.metrics
                 else "무음 판별 정보 없음 (잡음이 문장으로 남을 수 있음)"]
        cost = (res.meta or {}).get("cost")
        if cost is not None:
            parts.append(f"요금 ${cost:.6f}")
        self.done.emit(" · ".join(parts))


class SettingsDialog(QDialog):
    def __init__(self, s, parent=None) -> None:
        super().__init__(parent)
        self.settings = s
        self.key_deleted = False
        self._models = dict(s.api_models)  # edits per provider, applied on 저장
        self.setWindowTitle("설정")
        self.resize(600, 0)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 20)
        lay.setSpacing(10)
        title = QLabel("설정")
        title.setFont(theme.font(20, QFont.Weight.DemiBold))
        lay.addWidget(title)

        lay.addWidget(_section("녹음"))
        self.record = QCheckBox("전체 음성 녹음 저장")
        self.record.setChecked(s.record_audio)
        lay.addWidget(self.record)
        lay.addWidget(_muted("켜면 회의가 끝날 때 상대와 나를 합친 ‘전체 녹음.flac’을 회의록 폴더에 "
                             "저장합니다. 끄면 회의록(글)만 남습니다."))

        lay.addSpacing(6)
        lay.addWidget(_section("확정 자막"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        self.engine = QComboBox()
        for label, backend, _ in ENGINE_CHOICES:
            self.engine.addItem(label, backend)
        self.model = QComboBox()
        grid.addWidget(QLabel("엔진"), 0, 0)
        grid.addWidget(self.engine, 0, 1)
        grid.addWidget(QLabel("모델"), 1, 0)
        grid.addWidget(self.model, 1, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        self.engine_note = _muted()
        lay.addWidget(self.engine_note)

        # external API: key, ZDR, connection test
        self.api_box = QFrame(objectName="apiBox")
        self.api_box.setStyleSheet(
            f"#apiBox {{ background: {theme.CANVAS_SOFT}; border-radius: 8px; }}")
        api = QVBoxLayout(self.api_box)
        api.setContentsMargins(14, 12, 14, 12)
        api.setSpacing(8)
        key_head = QHBoxLayout()
        self.key_state = QLabel()
        self.key_state.setStyleSheet(f"color: {theme.INK_MUTED};")
        self.delete_btn = QPushButton("키 삭제", objectName="utility")
        key_head.addWidget(QLabel("API 키"))
        key_head.addWidget(self.key_state)
        key_head.addStretch(1)
        key_head.addWidget(self.delete_btn)
        api.addLayout(key_head)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("API 키를 붙여 넣으세요 (저장 후에는 다시 표시되지 않습니다)")
        api.addWidget(self.key)
        self.key_note = _muted()
        api.addWidget(self.key_note)
        test_row = QHBoxLayout()
        self.zdr = QCheckBox("데이터를 보관하지 않는 경로만 사용 (ZDR)")
        self.zdr.setChecked(s.openrouter_zdr)
        self.test_btn = QPushButton("연결 테스트", objectName="utility")
        test_row.addWidget(self.zdr)
        test_row.addStretch(1)
        test_row.addWidget(self.test_btn)
        api.addLayout(test_row)
        self.test_result = _muted()
        self.test_result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        api.addWidget(self.test_result)
        lay.addWidget(self.api_box)

        lay.addSpacing(6)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("취소", objectName="utility")
        save = QPushButton("저장", objectName="primary")
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        lay.addLayout(buttons)
        for b in (self.delete_btn, self.test_btn, cancel, save):
            b.setCursor(Qt.CursorShape.PointingHandCursor)

        self.tester = _Tester()
        self.tester.done.connect(self._tested)
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.accept)
        self.delete_btn.clicked.connect(self._delete_key)
        self.test_btn.clicked.connect(self._test)
        self.model.currentTextChanged.connect(self._remember_model)
        self.engine.currentIndexChanged.connect(self._on_engine)
        # an unknown saved engine shows no selection until the user picks one
        self.engine.setCurrentIndex(self.engine.findData(s.final_backend))
        self._on_engine()

    # --- state ------------------------------------------------------------------
    @property
    def backend(self) -> str | None:
        return self.engine.currentData()

    def _on_engine(self, *_args) -> None:
        backend = self.backend
        remote = backend in config.API_PROVIDERS
        self.engine_note.setText(next((d for _, b, d in ENGINE_CHOICES if b == backend),
                                      "확정 자막 엔진을 골라 주세요."))
        self.model.blockSignals(True)
        self.model.clear()
        self.model.setEditable(backend == "openrouter")  # its catalogue changes: id can be typed
        if remote:
            current = self._models.get(backend, "")
            models = config.API_PROVIDERS[backend]["models"]
            for m in models + ([current] if current and current not in models else []):
                self.model.addItem(m, m)
            self.model.setCurrentIndex(max(0, self.model.findData(current)))
            if self.model.lineEdit() is not None:
                self.model.lineEdit().setCursorPosition(0)
        elif backend == "local":
            for i, (label, model, tip) in enumerate(FINAL_CHOICES):
                self.model.addItem(label, model)
                self.model.setItemData(i, tip, Qt.ItemDataRole.ToolTipRole)
            self.model.setCurrentIndex(max(0, self.model.findData(self.settings.final_model)))
        self.model.setEnabled(backend in config.FINAL_BACKENDS)
        self.model.blockSignals(False)
        self.api_box.setVisible(remote)
        self.zdr.setVisible(backend == "openrouter")
        self.test_result.clear()
        if remote:
            self._refresh_key()
        self.adjustSize()

    def _remember_model(self, text: str) -> None:
        if self.backend in config.API_PROVIDERS and text.strip():
            self._models[self.backend] = text.strip()

    def _refresh_key(self) -> None:
        has = api_keys.has_api_key(self.backend)
        self.key_state.setText("· 저장된 키 있음" if has else "· 저장된 키 없음")
        self.delete_btn.setEnabled(has)
        self.key_note.setText(
            "키는 이 Windows 사용자만 풀 수 있게 암호화해 저장합니다. 환경 변수 "
            f"{api_keys.ENV_VARS[self.backend]}가 있으면 그 값을 먼저 씁니다.")

    def _model_id(self) -> str:
        return (self.model.currentText().strip() if self.backend in config.API_PROVIDERS
                else self.model.currentData() or "")

    # --- API key / test -------------------------------------------------------------
    def _delete_key(self) -> None:
        api_keys.delete_api_key(self.backend)
        self.key_deleted = True
        self._refresh_key()

    def _test(self) -> None:
        """One second of silence to the selected provider, with the typed (or saved) key."""
        from scribe.asr.backends import FinalSpec, create_final

        spec = FinalSpec(self.backend, self._model_id(), zdr=self.zdr.isChecked())
        try:
            final = create_final(spec, api_key=self.key.text().strip() or None)
        except Exception as e:
            self.test_result.setText(f"실패: {e}")
            return
        self.test_btn.setEnabled(False)
        self.test_result.setText("테스트 중… (1초 무음을 보냅니다)")
        self.tester.run(final)

    def _tested(self, text: str) -> None:
        self.test_result.setText(text)
        self.test_btn.setEnabled(True)

    # --- apply ------------------------------------------------------------------------
    def apply(self) -> bool:
        """Write the choices into the settings (and save a typed key). Returns True when the
        final-pass engine has to be rebuilt."""
        s = self.settings
        s.record_audio = self.record.isChecked()
        before = (s.final_backend, s.final_model, dict(s.api_models), s.openrouter_zdr)
        backend = self.backend
        if backend is not None:
            s.final_backend = backend
            if backend == "local":
                s.final_model = self._model_id() or s.final_model
            else:
                self._remember_model(self.model.currentText())
                s.api_models = {**s.api_models, **self._models}
                s.openrouter_zdr = self.zdr.isChecked()
        key_saved = False
        if backend in config.API_PROVIDERS and self.key.text().strip():
            api_keys.save_api_key(backend, self.key.text())
            key_saved = True
        after = (s.final_backend, s.final_model, dict(s.api_models), s.openrouter_zdr)
        uses_key = s.final_backend in config.API_PROVIDERS
        return before != after or (uses_key and (key_saved or self.key_deleted))
