"""Benchmarks against tests/fixtures (TTS clips with known sentence timing)."""

from __future__ import annotations

import json
import statistics
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from scribe import config
from scribe.config import SAMPLE_RATE
from scribe.eval.cer import cer
from scribe.models import DEFAULT_PARTIAL_MODEL

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"


@dataclass
class Fixture:
    name: str
    audio: np.ndarray
    text: str
    sentences: list[dict]
    wav: Path


def load_fixtures(names: list[str] | None = None) -> list[Fixture]:
    out = []
    for meta_path in sorted(FIXTURES.glob("*.json")):
        if names and meta_path.stem not in names:
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        wav = meta_path.with_suffix(".wav")
        audio, sr = sf.read(str(wav), dtype="float32")
        assert sr == SAMPLE_RATE
        out.append(Fixture(meta_path.stem, audio, meta["text"], meta["sentences"], wav))
    if not out:
        raise FileNotFoundError(f"no fixtures in {FIXTURES}; run scripts/make_fixtures.py")
    return out


def _cut(fx: Fixture, s: dict, pad: float = 0.2) -> np.ndarray:
    a = max(0, int((s["start"] - pad) * SAMPLE_RATE))
    b = min(len(fx.audio), int((s["end"] + pad) * SAMPLE_RATE))
    return fx.audio[a:b]


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def bench_whisper(fixtures: list[Fixture], beam_size: int = 3,
                  model: str = config.WHISPER_MODEL) -> dict:
    from scribe.asr.whisper_final import WhisperFinal
    from scribe.diagnostics.gpu_monitor import VramSampler, gpu_query

    with VramSampler() as vram:
        t0 = time.perf_counter()
        eng = WhisperFinal(model=model, beam_size=beam_size)
        load_s = time.perf_counter() - t0
        eng.transcribe(np.zeros(SAMPLE_RATE, np.float32))  # warm-up
        audio_s = infer_s = 0.0
        refs, hyps, per_seg = [], [], []
        for fx in fixtures:
            for s in fx.sentences:
                seg = _cut(fx, s)
                t0 = time.perf_counter()
                r = eng.transcribe(seg)
                dt = time.perf_counter() - t0
                audio_s += len(seg) / SAMPLE_RATE
                infer_s += dt
                per_seg.append(dt)
                refs.append(s["text"])
                hyps.append(r.text)
    q = gpu_query("name,temperature.gpu")
    return {
        "engine": f"faster-whisper {model} {config.WHISPER_COMPUTE_TYPE} (cuda)",
        "beam_size": beam_size,
        "gpu": q[0] if q else "?",
        "load_s": round(load_s, 1),
        "audio_s": round(audio_s, 1),
        "rtf": round(infer_s / audio_s, 3),
        "segment_infer_p50_s": round(statistics.median(per_seg), 3),
        "segment_infer_p95_s": round(_pct(per_seg, 0.95), 3),
        "peak_vram_delta_mb": vram.peak_delta_mb,
        "cer": round(cer(" ".join(refs), " ".join(hyps)), 4),
        "samples": list(zip(refs, hyps, strict=True))[:6],
    }


def bench_sherpa(fixtures: list[Fixture], num_threads: int = 2,
                 model: str = DEFAULT_PARTIAL_MODEL) -> dict:
    from scribe.asr.sherpa_stream import SherpaStreaming

    eng = SherpaStreaming(model, num_threads=num_threads)
    chunk = int(0.05 * SAMPLE_RATE)
    compute = audio_s = 0.0
    first_text_latency, refs, hyps = [], [], []
    pre_roll = 0.2  # same pre-roll the VAD segmenter hands to the partial recognizer
    for fx in fixtures:
        for s in fx.sentences:
            seg = _cut(fx, s, pad=pre_roll)
            onset = min(pre_roll, s["start"])
            st = eng.new_stream()
            first = None
            for i in range(0, len(seg), chunk):
                t0 = time.perf_counter()
                txt = st.accept(seg[i:i + chunk])
                dt = time.perf_counter() - t0
                compute += dt
                if txt and first is None:
                    # speech onset -> audio that had to arrive + time to decode it
                    first = (i + chunk) / SAMPLE_RATE - onset + dt
            t0 = time.perf_counter()
            final = st.finish()
            compute += time.perf_counter() - t0
            audio_s += len(seg) / SAMPLE_RATE
            if first is not None:
                first_text_latency.append(first)
            refs.append(s["text"])
            hyps.append(final)
    return {
        "engine": f"sherpa-onnx streaming {eng.model_name} (cpu)",
        "threads": num_threads,
        "audio_s": round(audio_s, 1),
        "rtf": round(compute / audio_s, 3),
        "first_text_p50_s": round(statistics.median(first_text_latency), 3),
        "first_text_p95_s": round(_pct(first_text_latency, 0.95), 3),
        "cer": round(cer(" ".join(refs), " ".join(hyps)), 4),
        "samples": list(zip(refs, hyps, strict=True))[:6],
    }


def bench_e2e(fixtures: list[Fixture], realtime: bool = True,
              model: str = DEFAULT_PARTIAL_MODEL,
              final_model: str = config.WHISPER_MODEL) -> dict:
    """Run the full pipeline on fixture files paced at real time; measure latency + CER."""
    from scribe.asr.sherpa_stream import SherpaStreaming
    from scribe.asr.whisper_final import WhisperFinal
    from scribe.audio.capture import FileSource
    from scribe.models import silero_vad_path
    from scribe.pipeline.session import Session

    sherpa = SherpaStreaming(model)
    whisper = WhisperFinal(model=final_model)
    whisper.transcribe(np.zeros(SAMPLE_RATE, np.float32))  # warm-up
    partial_lat, final_lat, refs, hyps = [], [], [], []
    per_clip = []
    for fx in fixtures:
        src = FileSource(fx.wav, realtime=realtime)
        events = []
        with tempfile.TemporaryDirectory() as td:
            sess = Session({"others": src}, lambda: whisper, sherpa, silero_vad_path(),
                           Path(td), record=False)
            sess.run(events.append)
        finals = [e for e in events if e.type == "final" and e.text]
        partials = [e for e in events if e.type == "partial"]
        t0 = src.started_at
        for s in fx.sentences:
            def hits(evs, s=s):
                return [e for e in evs if s["start"] - 0.6 <= e.t_start <= s["end"]]
            p = hits(partials)
            if p:
                partial_lat.append(min(e.meta["emit_mono"] for e in p) - (t0 + s["start"]))
            f = hits(finals)
            if f:
                final_lat.append(max(e.meta["emit_mono"] for e in f) - (t0 + s["end"]))
        hyp = " ".join(e.text for e in sorted(finals, key=lambda e: e.t_start))
        c = cer(fx.text, hyp)
        per_clip.append({"clip": fx.name, "cer": round(c, 4), "finals": len(finals)})
        refs.append(fx.text)
        hyps.append(hyp)
    return {
        "partial_model": sherpa.model_name,
        "realtime": realtime,
        "partial_latency_p50_s": round(statistics.median(partial_lat), 3),
        "partial_latency_p95_s": round(_pct(partial_lat, 0.95), 3),
        "final_latency_p50_s": round(statistics.median(final_lat), 3),
        "final_latency_p95_s": round(_pct(final_lat, 0.95), 3),
        "cer": round(cer(" ".join(refs), " ".join(hyps)), 4),
        "clips": per_clip,
        "sentences_with_partial": len(partial_lat),
        "sentences_with_final": len(final_lat),
        "sentences_total": sum(len(fx.sentences) for fx in fixtures),
    }
