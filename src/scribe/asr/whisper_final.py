"""Final-pass recognizer: faster-whisper on the NVIDIA GPU.

There is deliberately no CPU fallback. If CUDA is unusable we raise GpuUnavailableError
and point the user at `scribe doctor`, so the problem gets fixed instead of silently
degrading accuracy/latency.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from scribe import config
from scribe.diagnostics import cuda_env
from scribe.models import whisper_download_root


class GpuUnavailableError(RuntimeError):
    pass


@dataclass
class FinalResult:
    text: str
    avg_logprob: float
    no_speech_prob: float
    compression_ratio: float


class WhisperFinal:
    def __init__(
        self,
        model: str = config.WHISPER_MODEL,
        compute_type: str = config.WHISPER_COMPUTE_TYPE,
        beam_size: int = 3,
        hotwords: str | None = None,
        device_index: int = 0,
    ) -> None:
        cuda_env.register()
        import ctranslate2

        try:
            n = ctranslate2.get_cuda_device_count()
        except Exception as e:  # driver/runtime failures surface here
            raise GpuUnavailableError(f"CUDA 초기화 실패: {e}. `scribe doctor`로 점검하세요.") from e
        if n < 1:
            raise GpuUnavailableError(
                "CUDA GPU를 찾지 못했습니다 (CPU 폴백 없음). `scribe doctor`로 점검하세요."
            )

        from faster_whisper import WhisperModel

        def load(local_only: bool):
            return WhisperModel(
                model,
                device="cuda",
                device_index=device_index,
                compute_type=compute_type,
                download_root=str(whisper_download_root()),
                local_files_only=local_only,
            )

        from huggingface_hub.errors import LocalEntryNotFoundError

        try:
            try:
                self.model = load(local_only=True)  # cached -> works offline, no HF round-trip
            except LocalEntryNotFoundError:
                self.model = load(local_only=False)  # first run: download
        except RuntimeError as e:
            raise GpuUnavailableError(
                f"Whisper GPU 로드 실패: {e}. `scribe doctor`로 점검하세요."
            ) from e
        self.beam_size = beam_size
        self.hotwords = hotwords

    def transcribe(self, audio: np.ndarray, prompt: str | None = None) -> FinalResult:
        segments, _info = self.model.transcribe(
            audio.astype(np.float32, copy=False),
            language="ko",
            task="transcribe",
            beam_size=self.beam_size,
            vad_filter=False,  # segmentation is done upstream by our own VAD
            condition_on_previous_text=False,  # avoids repetition/hallucination loops
            initial_prompt=prompt or None,
            hotwords=self.hotwords or None,
            # timestamp mode lets Whisper split a long (up to 15 s) segment into sentences;
            # without it, it sometimes stops after the first sentence and drops the rest.
            without_timestamps=False,
            temperature=[0.0, 0.2, 0.4],
        )
        segs = list(segments)
        if not segs:
            return FinalResult("", 0.0, 1.0, 0.0)
        text = "".join(s.text for s in segs).strip()
        n = len(segs)
        return FinalResult(
            text=text,
            avg_logprob=sum(s.avg_logprob for s in segs) / n,
            no_speech_prob=max(s.no_speech_prob for s in segs),
            compression_ratio=max(s.compression_ratio for s in segs),
        )
