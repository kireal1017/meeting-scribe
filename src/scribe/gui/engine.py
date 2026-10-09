"""Background work for the GUI: model loading, the transcription session, GPU stats.

Nothing here touches widgets. Results travel to the GUI thread through Qt signals
(cross-thread emits are queued automatically).
"""

from __future__ import annotations

import gc
import threading
import traceback
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Signal

from scribe import config
from scribe.models import DEFAULT_PARTIAL_MODEL


class Engines(QObject):
    """Loads sherpa (CPU) + Whisper (GPU) once at startup and keeps them for every session.
    The final (Whisper) model can be swapped later without reloading the draft model."""

    ready = Signal()
    failed = Signal(str, bool)  # message, is_gpu_problem

    def __init__(self, partial_model: str = DEFAULT_PARTIAL_MODEL,
                 final_model: str = config.WHISPER_MODEL) -> None:
        super().__init__()
        self.partial_model = partial_model
        self.final_model = final_model
        self.sherpa = None
        self.whisper = None
        self.embedder = None  # speaker voice embeddings (remote participants A..J)

    def load(self) -> None:
        threading.Thread(target=self._load, name="engine-load", daemon=True).start()

    def switch_final(self, model: str) -> None:
        """Replace the Whisper model (only while no session is running)."""
        self.final_model = model
        threading.Thread(target=self._switch, name="engine-switch", daemon=True).start()

    def _load_whisper(self):
        from scribe.asr.whisper_final import WhisperFinal

        w = WhisperFinal(model=self.final_model)
        # cuBLAS/cuDNN warm-up (~2 s) during loading, so the first confirmed sentence of the
        # meeting is not delayed by it
        w.transcribe(np.zeros(16_000, np.float32))
        return w

    def _report(self, exc: BaseException) -> None:
        from scribe.asr.whisper_final import GpuUnavailableError

        if isinstance(exc, GpuUnavailableError):
            self.failed.emit(str(exc), True)
        else:
            self.failed.emit("".join(traceback.format_exception(type(exc), exc,
                                                                exc.__traceback__)), False)

    def _load(self) -> None:
        """The draft model (CPU, ~9 s: ONNX graph parsing) and Whisper (GPU, ~7 s: CUDA init +
        weights) are independent, so they load in parallel instead of back to back."""
        from scribe.asr.sherpa_stream import SherpaStreaming

        errors: list[BaseException] = []

        def load_sherpa() -> None:
            try:
                self.sherpa = SherpaStreaming(self.partial_model)
                from scribe.diarize import SherpaEmbedder
                from scribe.models import speaker_model_path

                self.embedder = SherpaEmbedder(speaker_model_path())
            except BaseException as e:  # reported below on the loader thread
                errors.append(e)

        t = threading.Thread(target=load_sherpa, name="sherpa-load", daemon=True)
        t.start()
        try:
            self.whisper = self._load_whisper()
        except Exception as e:
            t.join()
            self._report(e)
            return
        t.join()
        if errors:
            self._report(errors[0])
            return
        self.ready.emit()

    def _switch(self) -> None:
        # free the old model first: two large Whisper models do not fit in 4 GB of VRAM
        self.whisper = None
        gc.collect()
        try:
            self.whisper = self._load_whisper()
        except Exception as e:
            self._report(e)
            return
        self.ready.emit()

    def new_speaker_tracker(self):
        """Fresh A..J labels for every meeting (people differ between meetings)."""
        from scribe.diarize import SpeakerTracker

        return SpeakerTracker(self.embedder) if self.embedder is not None else None

    def whisper_factory(self):
        """Hand the preloaded model to a session once; a restart after a GPU error builds a
        fresh one (the session frees the broken one first, so we must not keep a reference)."""
        from scribe.asr.whisper_final import WhisperFinal

        w, self.whisper = self.whisper, None
        return w or WhisperFinal(model=self.final_model)


class SessionRunner(QObject):
    event = Signal(object)  # scribe.pipeline.events.Event
    finished = Signal(str)  # transcript.md path ("" if the session could not start)
    error = Signal(str)

    def __init__(self, engines: Engines) -> None:
        super().__init__()
        self.engines = engines
        self.session = None
        self._thread: threading.Thread | None = None
        self._active = False

    @property
    def running(self) -> bool:
        # an explicit flag, not thread.is_alive(): `finished` is emitted from inside the thread,
        # so when the GUI handles it the thread may still be alive for a moment
        return self._active

    def start(self, sources: dict, out_dir: Path, hotwords: str = "", record: bool = True) -> None:
        self._active = True
        self._thread = threading.Thread(target=self._run, args=(sources, out_dir, hotwords, record),
                                        name="session", daemon=True)
        self._thread.start()

    @property
    def paused(self) -> bool:
        return bool(self.session and self.session.paused)

    def pause(self) -> None:
        if self.session:
            self.session.pause()

    def resume(self) -> None:
        if self.session:
            self.session.resume()

    def stop(self) -> None:
        if self.session:
            self.session.stop()

    def join(self, timeout: float) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _run(self, sources: dict, out_dir: Path, hotwords: str, record: bool = True) -> None:
        from scribe.models import silero_vad_path
        from scribe.pipeline.session import Session

        md = ""
        try:
            self.session = Session(sources, self.engines.whisper_factory, self.engines.sherpa,
                                   silero_vad_path(), out_dir, hotwords=hotwords, record=record,
                                   speakers=self.engines.new_speaker_tracker())
            md = str(self.session.run(self.event.emit))
        except Exception:
            self.error.emit(traceback.format_exc())
        finally:
            if self.session is not None and self.session.whisper is not None:
                self.engines.whisper = self.session.whisper  # keep the model for the next one
            self.session = None
            self._active = False  # before emitting, so handlers see "not running"
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
