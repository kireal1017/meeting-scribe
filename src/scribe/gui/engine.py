"""Background work for the GUI: model loading, the transcription session, GPU stats.

Nothing here touches widgets. Results travel to the GUI thread through Qt signals
(cross-thread emits are queued automatically).
"""

from __future__ import annotations

import gc
import threading
import traceback
from dataclasses import replace
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Signal

from scribe import config
from scribe.models import DEFAULT_PARTIAL_MODEL


class Engines(QObject):
    """Loads sherpa (CPU) + the final-pass backend once at startup and keeps them for every
    session. The backend is fixed by a FinalSpec (local GPU Whisper or an external API) and only
    changes through switch_final(), i.e. an explicit user choice; errors never switch it."""

    ready = Signal()
    failed = Signal(str, str)  # message, kind: "gpu" | "api" | "other"

    def __init__(self, partial_model: str = DEFAULT_PARTIAL_MODEL,
                 final_model: str = config.WHISPER_MODEL, spec=None) -> None:
        from scribe.asr.backends import FinalSpec

        super().__init__()
        self.partial_model = partial_model
        self.spec = spec if spec is not None else FinalSpec("local", final_model)
        self.spec_error: Exception | None = None  # settings could not be turned into a spec
        self.sherpa = None
        self.final = None
        self.embedder = None  # speaker voice embeddings (remote participants A..J)

    @classmethod
    def from_settings(cls, s, partial_model: str = DEFAULT_PARTIAL_MODEL) -> Engines:
        """A broken backend setting does not stop the draft model from loading: the error is
        reported by load() and the user picks a backend again (nothing falls back silently)."""
        from scribe.asr.backends import spec_from_settings

        try:
            return cls(partial_model, spec=spec_from_settings(s))
        except Exception as e:
            engines = cls(partial_model, final_model=s.final_model)
            engines.spec, engines.spec_error = None, e
            return engines

    # older names, kept for callers/tests written before external APIs existed
    @property
    def whisper(self):
        return self.final

    @whisper.setter
    def whisper(self, value) -> None:
        self.final = value

    @property
    def final_model(self) -> str:
        return self.spec.model if self.spec else ""

    @final_model.setter
    def final_model(self, model: str) -> None:
        self.spec = replace(self.spec, model=model)

    @property
    def remote(self) -> bool:
        return bool(self.spec and self.spec.remote)

    def load(self) -> None:
        threading.Thread(target=self._load, name="engine-load", daemon=True).start()

    def switch_final(self, spec) -> None:
        """Replace the final backend (only while no session is running). Accepts a FinalSpec or
        a local Whisper model name."""
        from scribe.asr.backends import FinalSpec

        self.spec = spec if isinstance(spec, FinalSpec) else FinalSpec("local", spec)
        self.spec_error = None
        threading.Thread(target=self._switch, name="engine-switch", daemon=True).start()

    def _build_final(self):
        from scribe.asr.backends import create_final

        if self.spec is None:
            raise self.spec_error or RuntimeError("확정 자막 엔진이 정해지지 않았습니다.")
        final = create_final(self.spec)
        if not getattr(final, "remote", False):
            # cuBLAS/cuDNN warm-up (~2 s) during loading, so the first confirmed sentence of the
            # meeting is not delayed by it. Never for an API: that would upload on every start.
            final.transcribe(np.zeros(16_000, np.float32))
        return final

    def _report(self, exc: BaseException) -> None:
        from scribe.asr.types import (
            ApiAuthError,
            ApiConfigError,
            ApiRequestError,
            ApiTransientError,
            GpuUnavailableError,
        )

        if isinstance(exc, GpuUnavailableError):
            self.failed.emit(str(exc), "gpu")
        elif isinstance(exc, (ApiConfigError, ApiAuthError, ApiRequestError, ApiTransientError)):
            self.failed.emit(str(exc), "api")
        else:
            self.failed.emit("".join(traceback.format_exception(type(exc), exc,
                                                                exc.__traceback__)), "other")

    def _load_cpu_models(self, errors: list[BaseException]) -> None:
        try:
            from scribe.asr.sherpa_stream import SherpaStreaming

            self.sherpa = SherpaStreaming(self.partial_model)
            from scribe.diarize import SherpaEmbedder
            from scribe.models import speaker_model_path

            self.embedder = SherpaEmbedder(speaker_model_path())
        except BaseException as e:  # reported on the loader thread
            errors.append(e)

    def _load(self) -> None:
        """The draft model (CPU, ~9 s: ONNX graph parsing) and the final backend (GPU Whisper
        ~7 s: CUDA init + weights; an API backend: instant, no network) load in parallel."""
        errors: list[BaseException] = []
        t = threading.Thread(target=self._load_cpu_models, args=(errors,), name="sherpa-load",
                             daemon=True)
        t.start()
        try:
            self.final = self._build_final()
        except Exception as e:
            t.join()  # the draft model still loads, so fixing the backend needs no restart
            self._report(e)
            return
        t.join()
        if errors:
            self._report(errors[0])
            return
        self.ready.emit()

    def _switch(self) -> None:
        # free the old model first: two large Whisper models do not fit in 4 GB of VRAM
        self.final = None
        gc.collect()
        errors: list[BaseException] = []
        if self.sherpa is None or self.embedder is None:  # startup failed before: finish it
            self._load_cpu_models(errors)
        try:
            self.final = self._build_final()
        except Exception as e:
            self._report(e)
            return
        if errors:
            self._report(errors[0])
            return
        self.ready.emit()

    def new_speaker_tracker(self):
        """Fresh A..J labels for every meeting (people differ between meetings)."""
        from scribe.diarize import SpeakerTracker

        return SpeakerTracker(self.embedder) if self.embedder is not None else None

    def final_factory(self):
        """Hand the preloaded backend to a session once; a restart after a GPU error builds a
        fresh one of the *same* spec (the session frees the broken one first, so we must not
        keep a reference)."""
        from scribe.asr.backends import create_final

        f, self.final = self.final, None
        if f is not None:
            return f
        if self.spec is None:
            raise self.spec_error or RuntimeError("확정 자막 엔진이 정해지지 않았습니다.")
        return create_final(self.spec)

    whisper_factory = final_factory  # older name


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
            self.session = Session(sources, self.engines.final_factory, self.engines.sherpa,
                                   silero_vad_path(), out_dir, hotwords=hotwords, record=record,
                                   speakers=self.engines.new_speaker_tracker())
            md = str(self.session.run(self.event.emit))
        except Exception:
            self.error.emit(traceback.format_exc())
        finally:
            if self.session is not None and self.session.final is not None:
                self.engines.final = self.session.final  # keep the backend for the next one
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
