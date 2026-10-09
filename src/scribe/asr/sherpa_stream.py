"""Partial-pass recognizer: sherpa-onnx streaming Zipformer (Korean) on CPU.

Produces instant draft text while someone is speaking; the Whisper final pass replaces it.
"""

from __future__ import annotations

import numpy as np

from scribe.config import SAMPLE_RATE
from scribe.diagnostics.cuda_env import register_onnxruntime
from scribe.models import DEFAULT_PARTIAL_MODEL, sherpa_streaming_model


class SherpaStreaming:
    def __init__(self, model: str = DEFAULT_PARTIAL_MODEL, num_threads: int = 2) -> None:
        register_onnxruntime()
        import sherpa_onnx

        self.model_name = model
        f = sherpa_streaming_model(model)
        self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(f.tokens),
            encoder=str(f.encoder),
            decoder=str(f.decoder),
            joiner=str(f.joiner),
            num_threads=num_threads,
            sample_rate=SAMPLE_RATE,
            feature_dim=80,
            decoding_method="greedy_search",
            enable_endpoint_detection=False,  # segmentation is driven by our VAD
            provider="cpu",
        )

    def new_stream(self) -> PartialStream:
        return PartialStream(self.recognizer)


class PartialStream:
    """One stream per VAD segment."""

    def __init__(self, recognizer) -> None:
        self.r = recognizer
        self.s = recognizer.create_stream()
        self.text = ""

    def accept(self, audio: np.ndarray) -> str | None:
        """Feed audio; return the new text if it changed, else None."""
        self.s.accept_waveform(SAMPLE_RATE, audio.astype(np.float32, copy=False))
        text = self._decode()
        if text != self.text:
            self.text = text
            return text
        return None

    def finish(self) -> str:
        """Flush the tail (input_finished + tail padding) and return the final draft text."""
        self.s.accept_waveform(SAMPLE_RATE, np.zeros(int(0.3 * SAMPLE_RATE), np.float32))
        self.s.input_finished()
        self.text = self._decode()
        return self.text

    def _decode(self) -> str:
        while self.r.is_ready(self.s):
            self.r.decode_stream(self.s)
        return self.r.get_result(self.s).strip()
