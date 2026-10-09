"""Telling remote participants apart: online speaker labels A..J on the "others" channel.

Each confirmed utterance gets a voice embedding (3D-Speaker CAM++, CPU, ~14 ms per second of
speech) that is compared with the running centroid of every speaker seen so far:
  similarity >= threshold -> same speaker, otherwise a new one (up to MAX_SPEAKERS).
Short utterances (< SHORT_S) give unreliable embeddings, so they never open a new speaker
unless they are clearly unlike everyone (< SHORT_NEW_THRESHOLD). Thresholds were picked on a
real two-video Korean recording (docs/BENCHMARKS.md); quick back-and-forth inside one
utterance cannot be split live.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from scribe.config import SAMPLE_RATE

LABELS = "ABCDEFGHIJ"
MAX_SPEAKERS = len(LABELS)  # 10 remote participants
THRESHOLD = 0.6
SHORT_S = 2.0
SHORT_NEW_THRESHOLD = 0.3


class SherpaEmbedder:
    """audio (16 kHz float32) -> L2-normalised speaker embedding."""

    def __init__(self, model_path, num_threads: int = 2) -> None:
        from scribe.diagnostics.cuda_env import register_onnxruntime

        register_onnxruntime()
        import sherpa_onnx

        self.ex = sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(model_path), num_threads=num_threads))

    def __call__(self, audio: np.ndarray) -> np.ndarray:
        st = self.ex.create_stream()
        st.accept_waveform(SAMPLE_RATE, audio.astype(np.float32, copy=False))
        st.input_finished()
        v = np.asarray(self.ex.compute(st), dtype=np.float32)
        return v / (np.linalg.norm(v) + 1e-9)


class SpeakerTracker:
    """One per meeting. Not thread-safe (used from the single final-pass worker)."""

    def __init__(self, embed: Callable[[np.ndarray], np.ndarray],
                 max_speakers: int = MAX_SPEAKERS, threshold: float = THRESHOLD,
                 short_s: float = SHORT_S, short_new_threshold: float = SHORT_NEW_THRESHOLD) -> None:
        self.embed = embed
        self.max_speakers = min(max_speakers, MAX_SPEAKERS)
        self.threshold = threshold
        self.short_s = short_s
        self.short_new_threshold = short_new_threshold
        self.centroids: list[np.ndarray] = []
        self.counts: list[int] = []

    def assign(self, audio: np.ndarray) -> str:
        v = self.embed(audio)
        short = len(audio) / SAMPLE_RATE < self.short_s
        if self.centroids:
            sims = np.array([float(v @ c) for c in self.centroids])
            best = int(np.argmax(sims))
            new_needed = sims[best] < (self.short_new_threshold if short else self.threshold)
            if not new_needed or len(self.centroids) >= self.max_speakers:
                self._update(best, v, weight=0.0 if short else 1.0)
                return LABELS[best]
        self.centroids.append(v)
        self.counts.append(1)
        return LABELS[len(self.centroids) - 1]

    def _update(self, i: int, v: np.ndarray, weight: float) -> None:
        """Running mean of the speaker's voice; short clips do not move the centroid."""
        if weight <= 0:
            return
        c = self.centroids[i] * self.counts[i] + v
        self.centroids[i] = c / (np.linalg.norm(c) + 1e-9)
        self.counts[i] += 1
