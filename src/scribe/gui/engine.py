"""Background work for the GUI: model loading, the transcription session, GPU stats.

Nothing here touches widgets. Results travel to the GUI thread through Qt signals
(cross-thread emits are queued automatically).
"""

from __future__ import annotations

import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from scribe.models import DEFAULT_PARTIAL_MODEL


class Engines(QObject):
    """Loads sherpa (CPU) + Whisper (GPU) once at startup and keeps them for every session."""

    ready = Signal()
    failed = Signal(str, bool)  # message, is_gpu_problem

    def __init__(self, partial_model: str = DEFAULT_PARTIAL_MODEL) -> None:
        super().__init__()
        self.partial_model = partial_model
        self.sherpa = None
        self.whisper = None

    def load(self) -> None:
        threading.Thread(target=self._load, name="engine-load", daemon=True).start()

    def _load(self) -> None:
        from scribe.asr.sherpa_stream import SherpaStreaming
        from scribe.asr.whisper_final import GpuUnavailableError, WhisperFinal

        try:
            self.sherpa = SherpaStreaming(self.partial_model)
            self.whisper = WhisperFinal()
        except GpuUnavailableError as e:
            self.failed.emit(str(e), True)
            return
        except Exception:
            self.failed.emit(traceback.format_exc(), False)
            return
        self.ready.emit()

    def whisper_factory(self):
        """Hand the preloaded model to a session once; a restart after a GPU error builds a
        fresh one (the session frees the broken one first, so we must not keep a reference)."""
        from scribe.asr.whisper_final import WhisperFinal

        w, self.whisper = self.whisper, None
        return w or WhisperFinal()


class SessionRunner(QObject):
    event = Signal(object)  # scribe.pipeline.events.Event
    finished = Signal(str)  # transcript.md path ("" if the session could not start)
    error = Signal(str)

    def __init__(self, engines: Engines) -> None:
        super().__init__()
        self.engines = engines
        self.session = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, sources: dict, out_dir: Path, hotwords: str = "") -> None:
        self._thread = threading.Thread(target=self._run, args=(sources, out_dir, hotwords),
                                        name="session", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self.session:
            self.session.stop()

    def join(self, timeout: float) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _run(self, sources: dict, out_dir: Path, hotwords: str) -> None:
        from scribe.models import silero_vad_path
        from scribe.pipeline.session import Session

        md = ""
        try:
            self.session = Session(sources, self.engines.whisper_factory, self.engines.sherpa,
                                   silero_vad_path(), out_dir, hotwords=hotwords)
            md = str(self.session.run(self.event.emit))
        except Exception:
            self.error.emit(traceback.format_exc())
        finally:
            if self.session is not None and self.session.whisper is not None:
                self.engines.whisper = self.session.whisper  # keep the model for the next one
            self.session = None
            self.finished.emit(md)


class GpuMonitor(QObject):
    """Polls nvidia-smi off the GUI thread (a subprocess call can take ~100 ms)."""

    stats = Signal(object)  # dict | None

    def __init__(self, interval_s: float = 3.0) -> None:
        super().__init__()
        self.interval = interval_s
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name="gpu-monitor", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        from scribe.diagnostics.gpu_monitor import gpu_query

        while not self._stop.is_set():
            q = gpu_query("name,memory.used,memory.total,temperature.gpu")
            self.stats.emit(None if q is None else {
                "name": q[0], "used_mb": int(q[1]), "total_mb": int(q[2]), "temp_c": int(q[3])})
            self._stop.wait(self.interval)


class DoctorRunner(QObject):
    done = Signal(bool, str, str)  # ok, full text, report path

    def start(self) -> None:
        threading.Thread(target=self._run, name="doctor", daemon=True).start()

    def _run(self) -> None:
        from scribe.diagnostics.gpu_doctor import run_doctor

        lines: list[str] = []
        try:
            ok, _results, path = run_doctor(echo=lines.append)
        except Exception:
            self.done.emit(False, traceback.format_exc(), "")
            return
        self.done.emit(ok, "\n".join(lines), path)
