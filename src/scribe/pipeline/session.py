"""Wires sources -> VAD -> (sherpa partials, whisper finals) -> events.

Threads:
  one ChannelWorker per audio source (record FLAC, VAD, sherpa partial decoding — all CPU)
  one FinalWorker shared by all channels (Whisper on the GPU; 4 GB VRAM fits one model)
  the caller's thread drains the event queue (store + UI) in Session.run()
"""

from __future__ import annotations

import gc
import queue
import threading
import time
import traceback
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from scribe.asr.filters import FinalFilter
from scribe.audio.mixdown import mixdown
from scribe.audio.recorder import FlacRecorder
from scribe.config import SAMPLE_RATE
from scribe.pipeline.events import Event, segment_id
from scribe.store.transcript import TranscriptStore
from scribe.vad.segmenter import (
    SegmentAudio,
    SegmentEnd,
    Segmenter,
    SegmentStart,
    SileroVAD,
    VadParams,
)

BACKLOG_WARN_S = 20.0
PROMPT_TAIL_CHARS = 100


@dataclass
class FinalJob:
    channel: str
    index: int
    start: int
    end: int
    audio: np.ndarray
    draft: str
    queued_mono: float


class Session:
    def __init__(
        self,
        sources: dict[str, Iterable[np.ndarray]],
        whisper_factory: Callable[[], object],
        sherpa,
        vad_path: Path,
        session_dir: Path,
        hotwords: str = "",
        vad_params: VadParams | None = None,
        record: bool = True,
        speakers=None,
    ) -> None:
        """record: keep per-channel FLAC + a mixed "전체 녹음.flac" at the end.
        speakers: a SpeakerTracker labelling remote participants A..J (None = no labels)."""
        self.sources = sources
        self.whisper_factory = whisper_factory
        self.whisper = whisper_factory()
        self.sherpa = sherpa
        self.vad_path = vad_path
        self.dir = session_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.hotwords = hotwords.strip()
        self.vad_params = vad_params
        self.record = record
        self.speakers = speakers
        self.store = TranscriptStore(self.dir)
        self.events: queue.Queue[Event | None] = queue.Queue()
        self.jobs: queue.Queue[FinalJob | None] = queue.Queue()
        self.filter = FinalFilter()
        self._last_final: dict[str, str] = {}
        self._backlog_s = 0.0
        self._backlog_lock = threading.Lock()
        self._paused = threading.Event()
        self.started_mono = 0.0

    # --- public ----------------------------------------------------------
    def run(self, on_event: Callable[[Event], None] | None = None) -> Path:
        """Block until all sources end (or stop() is called). Returns transcript.md path."""
        # live devices must be opened from one thread (WASAPI); do it here, not in the workers
        opened = []
        try:
            for src in self.sources.values():
                opener = getattr(src, "open", None)
                if opener:
                    opener()
                    opened.append(src)
        except Exception:
            for src in opened:
                src.close()
            self.store.close()
            raise
        self.started_mono = time.monotonic()
        channel_threads = [
            threading.Thread(target=self._channel_worker, args=(ch, src), name=f"ch-{ch}",
                             daemon=True)
            for ch, src in self.sources.items()
        ]
        final_thread = threading.Thread(target=self._final_worker, name="final", daemon=True)
        final_thread.start()
        for t in channel_threads:
            t.start()

        def closer() -> None:
            for t in channel_threads:
                t.join()
            self.jobs.put(None)
            final_thread.join()
            self.events.put(None)

        threading.Thread(target=closer, name="closer", daemon=True).start()
        try:
            while True:
                try:
                    # timeout: on Windows a blocking get() is not interrupted by Ctrl+C, and
                    # during silence no events arrive to wake the main thread
                    ev = self.events.get(timeout=0.2)
                except queue.Empty:
                    continue
                if ev is None:
                    break
                self.store.append(ev)
                if on_event:
                    on_event(ev)
        finally:
            self.stop()
            md = self.store.close()
        if self.record:
            try:
                mixdown(self.dir)
            except Exception:  # the transcript is already safe; the per-channel FLACs remain
                traceback.print_exc()
        return md

    @property
    def backlog_s(self) -> float:
        """Seconds of speech waiting for the GPU final pass."""
        return max(0.0, self._backlog_s)

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def pause(self) -> None:
        """Stop listening without ending the session: open utterances are finalized, nothing
        is transcribed, and the FLAC gets silence so it stays aligned with the timestamps."""
        if not self._paused.is_set():
            self._paused.set()
            self._status("all", "일시중지", paused=True)

    def resume(self) -> None:
        if self._paused.is_set():
            self._paused.clear()
            self._status("all", "기록 재개", paused=False)

    def stop(self) -> None:
        for src in self.sources.values():
            stop = getattr(src, "stop", None)
            if stop:
                stop()

    # --- workers ---------------------------------------------------------
    def _emit(self, **kw) -> None:
        kw.setdefault("meta", {})["emit_mono"] = time.monotonic()
        self.events.put(Event(**kw))

    def _status(self, channel: str, text: str, **meta) -> None:
        t = time.monotonic() - self.started_mono
        self._emit(type="status", segment_id=f"{channel}-status", channel=channel,
                   t_start=t, t_end=t, text=text, engine="system", meta=meta)

    def _channel_worker(self, channel: str, source: Iterable[np.ndarray]) -> None:
        seg = Segmenter(SileroVAD(self.vad_path), self.vad_params)
        rec = FlacRecorder(self.dir, f"audio-{channel}") if self.record else None
        stream = None
        cur_start = 0

        def handle(ev) -> None:
            nonlocal stream, cur_start
            if isinstance(ev, SegmentStart):
                stream = self.sherpa.new_stream()
                cur_start = ev.start
                self._partial(channel, ev.index, cur_start, stream.accept(ev.audio))
            elif isinstance(ev, SegmentAudio) and stream is not None:
                self._partial(channel, ev.index, cur_start, stream.accept(ev.audio))
            elif isinstance(ev, SegmentEnd):
                draft = stream.finish() if stream is not None else ""
                self._partial(channel, ev.index, ev.start, draft, end=ev.end)
                stream = None
                with self._backlog_lock:
                    self._backlog_s += len(ev.audio) / SAMPLE_RATE
                self.jobs.put(FinalJob(channel, ev.index, ev.start, ev.end, ev.audio, draft,
                                       time.monotonic()))

        was_paused = False
        try:
            for block in source:
                if self._paused.is_set():
                    if not was_paused:  # finish whatever was being said when pause was hit
                        for ev in seg.flush():
                            handle(ev)
                        was_paused = True
                    if rec:
                        rec.write(np.zeros_like(block))
                    seg.skip(len(block))
                    continue
                was_paused = False
                if rec:
                    rec.write(block)
                for ev in seg.process(block):
                    handle(ev)
            for ev in seg.flush():
                handle(ev)
        except Exception:
            self._status(channel, "오디오 처리 오류", error=traceback.format_exc())
        finally:
            if rec:
                rec.close()
            close = getattr(source, "close", None)
            if close:  # idempotent; releases the device even if iteration never started
                close()

    def _partial(self, channel: str, index: int, start: int, text: str | None,
                 end: int | None = None) -> None:
        if not text:
            return
        self._emit(type="partial", segment_id=segment_id(channel, index), channel=channel,
                   t_start=start / SAMPLE_RATE, t_end=(end or start) / SAMPLE_RATE,
                   text=text, engine="sherpa")

    def _prompt(self, channel: str) -> str:
        tail = self._last_final.get(channel, "")[-PROMPT_TAIL_CHARS:]
        return " ".join(p for p in (self.hotwords, tail) if p)

    def _final_worker(self) -> None:
        reinit_used = False
        while True:
            job = self.jobs.get()
            if job is None:
                return
            with self._backlog_lock:
                self._backlog_s -= len(job.audio) / SAMPLE_RATE
                backlog = self._backlog_s
            if self.whisper is None:
                continue  # GPU halted: drafts + FLAC keep going, finals come from re-transcription
            if backlog > BACKLOG_WARN_S:
                self._status(job.channel, f"확정 자막 지연: 대기 {backlog:.0f}초", backlog_s=backlog)
            t0 = time.monotonic()
            try:
                res = self.whisper.transcribe(job.audio, prompt=self._prompt(job.channel))
            except Exception as e:
                self._status(job.channel, f"GPU 전사 오류: {e}", error=traceback.format_exc())
                # free the failed model first: two copies do not fit in 4 GB of VRAM
                self.whisper = None
                gc.collect()
                if not reinit_used:  # one restart attempt; the FLAC keeps the audio regardless
                    reinit_used = True
                    try:
                        self.whisper = self.whisper_factory()
                        self._status(job.channel, "Whisper 재시작 완료")
                        continue
                    except Exception as e2:
                        self._status(job.channel, f"Whisper 재시작 실패: {e2}")
                # no CPU fallback: halt finals once, loudly
                self._status(job.channel, "GPU 확정 자막 중단 — 녹음과 임시 자막은 계속됩니다. "
                             "회의 후 `scribe doctor`를 실행해 주세요.", halted=True)
                continue
            infer_s = time.monotonic() - t0
            reason = self.filter.check(res)
            text = "" if reason else res.text
            if text:
                self._last_final[job.channel] = text
            speaker = None
            if text and job.channel == "others" and self.speakers is not None:
                try:  # only kept text feeds the voice clusters (noise must not create people)
                    speaker = self.speakers.assign(job.audio)
                except Exception:
                    traceback.print_exc()
            self._emit(
                type="final", segment_id=segment_id(job.channel, job.index), channel=job.channel,
                t_start=job.start / SAMPLE_RATE, t_end=job.end / SAMPLE_RATE, text=text,
                engine="whisper",
                meta={"draft": job.draft, "rejected": reason, "infer_s": round(infer_s, 3),
                      "speaker": speaker,
                      "queue_s": round(t0 - job.queued_mono, 3),
                      "avg_logprob": round(res.avg_logprob, 3),
                      "no_speech_prob": round(res.no_speech_prob, 3)},
            )
