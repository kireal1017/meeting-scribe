"""Final pass through an external speech-to-text API: OpenAI or OpenRouter (opt-in).

Built only by `asr.backends.create_final` after the user chose the provider and agreed to upload.
Each finished utterance (1-15 s) is sent as 16 kHz mono FLAC. Failures raise; the session decides
what to do (marked gap, halt) and never switches to another backend.

  OpenAI      multipart /audio/transcriptions; whisper-1 -> verbose_json with Whisper metrics,
              gpt-4o*-transcribe -> json (text only); previous sentence sent as `prompt`
  OpenRouter  JSON /audio/transcriptions with base64 `input_audio`; text only (no metrics, no
              prompt support); `provider.zdr` keeps audio on zero-data-retention endpoints;
              the response carries the request cost
"""

from __future__ import annotations

import base64
import io
import json
import re
import time
import urllib.error
import urllib.request
import uuid
import zlib

import numpy as np
import soundfile as sf

from scribe import config
from scribe.asr.types import (
    ApiAuthError,
    ApiConfigError,
    ApiRequestError,
    ApiTransientError,
    FinalResult,
)

DEADLINE_S = 20.0  # per utterance, including retries
DRAINING_DEADLINE_S = 5.0  # after the meeting was stopped
BACKOFF_S = (1.0, 2.0)
VERBOSE_MODELS = {"whisper-1"}  # OpenAI models that return per-segment Whisper metrics


def encode_flac(audio: np.ndarray) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.clip(audio, -1.0, 1.0), config.SAMPLE_RATE, format="FLAC", subtype="PCM_16")
    return buf.getvalue()


def _multipart(fields: dict[str, str], file: tuple[str, bytes, str]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    out = io.BytesIO()
    for name, value in fields.items():
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                  f"{value}\r\n".encode())
    fname, data, ctype = file
    out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
              f'filename="{fname}"\r\nContent-Type: {ctype}\r\n\r\n'.encode())
    out.write(data)
    out.write(f"\r\n--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


# providers echo part of a rejected key ("sk-proj-****abcd"); it must not reach the UI or logs
_KEY_LIKE = re.compile(r"\b(?:sk|or)-[\w*.-]{4,}")


def _error_text(body: bytes) -> str:
    try:
        data = json.loads(body)
        err = data.get("error", data)
        msg = err.get("message") if isinstance(err, dict) else err
        text = str(msg)
    except (ValueError, AttributeError):
        text = body.decode("utf-8", "replace")
    return _KEY_LIKE.sub("[키 가림]", text)[:300]


class RemoteFinal:
    remote = True

    def __init__(self, spec, api_key: str, *, base_url: str | None = None,
                 deadline_s: float = DEADLINE_S, draining_deadline_s: float = DRAINING_DEADLINE_S,
                 backoff_s: tuple[float, ...] = BACKOFF_S) -> None:
        """base_url: tests point this at a local fake server; the app always uses the
        provider's fixed URL from config."""
        if spec.backend not in config.API_PROVIDERS:
            raise ApiConfigError(f"알 수 없는 API 제공자입니다: {spec.backend!r}")
        info = config.API_PROVIDERS[spec.backend]
        self.provider = spec.backend
        self.model = spec.model
        self.zdr = spec.zdr
        self._key = api_key
        self.base_url = (base_url or info["base_url"]).rstrip("/")
        self.deadline_s = deadline_s
        self.draining_deadline_s = draining_deadline_s
        self.backoff_s = backoff_s
        self._draining = False
        self.label = f"{info['label']} · {spec.model}"

    def __repr__(self) -> str:  # never show the key
        return f"RemoteFinal({self.label})"

    def set_draining(self) -> None:
        self._draining = True

    # --- request building -----------------------------------------------------
    def _request(self, flac: bytes, prompt: str | None) -> tuple[bytes, str]:
        if self.provider == "openai":
            fields = {"model": self.model, "language": "ko", "temperature": "0",
                      "response_format": "verbose_json" if self.model in VERBOSE_MODELS else "json"}
            if prompt:
                fields["prompt"] = prompt
            return _multipart(fields, ("utterance.flac", flac, "audio/flac"))
        body = {"model": self.model, "language": "ko", "temperature": 0, "response_format": "json",
                "input_audio": {"data": base64.b64encode(flac).decode("ascii"), "format": "flac"},
                "provider": {"zdr": self.zdr}}
        return json.dumps(body).encode(), "application/json"

    def _parse(self, data: dict, seconds: float) -> FinalResult:
        meta = {"provider": self.provider, "upload_s": round(seconds, 2)}
        usage = data.get("usage") or {}
        if isinstance(usage, dict) and usage.get("cost") is not None:
            meta["cost"] = float(usage["cost"])
        text = str(data.get("text") or "").strip()
        segs = [s for s in data.get("segments") or [] if isinstance(s, dict)]
        if segs and all("avg_logprob" in s for s in segs):
            return FinalResult(
                text,
                avg_logprob=sum(float(s["avg_logprob"]) for s in segs) / len(segs),
                no_speech_prob=max(float(s.get("no_speech_prob", 0.0)) for s in segs),
                compression_ratio=max(float(s.get("compression_ratio", 0.0)) for s in segs),
                metrics=True, meta=meta)
        ratio = len(text.encode()) / max(1, len(zlib.compress(text.encode()))) if text else 0.0
        return FinalResult(text, 0.0, 0.0, ratio, metrics=False, meta=meta)

    def _http_error(self, code: int, body: bytes, label: str) -> Exception:
        detail = _error_text(body)
        if code in (401, 403):
            return ApiAuthError(f"{label} API 키를 확인해 주세요 (HTTP {code}: {detail})")
        if code == 404:
            lowered = detail.lower()
            if self.provider == "openrouter" and self.zdr and (
                    "data policy" in lowered or "no endpoints" in lowered):
                return ApiConfigError(
                    f"이 모델({self.model})은 데이터 미보관(ZDR) 경로가 없습니다. "
                    "ZDR을 끄거나 다른 모델을 골라 주세요.")
            return ApiConfigError(f"모델 ID를 확인해 주세요: {self.model} (HTTP 404: {detail})")
        return ApiRequestError(f"{label} 요청 거부 (HTTP {code}: {detail})")

    # --- transcription ----------------------------------------------------------
    def transcribe(self, audio: np.ndarray, prompt: str | None = None) -> FinalResult:
        label = config.API_PROVIDERS[self.provider]["label"]
        body, ctype = self._request(encode_flac(audio), prompt)
        url = f"{self.base_url}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": ctype,
                   "User-Agent": "meeting-scribe"}
        end = time.monotonic() + (self.draining_deadline_s if self._draining else self.deadline_s)
        attempt = 0
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise ApiTransientError(f"{label} 응답 시간 초과")
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            retry_after = None
            try:
                with urllib.request.urlopen(req, timeout=remaining) as resp:
                    data = json.loads(resp.read())
                return self._parse(data if isinstance(data, dict) else {},
                                   len(audio) / config.SAMPLE_RATE)
            except urllib.error.HTTPError as e:
                payload = e.read()
                if e.code != 429 and e.code < 500:
                    raise self._http_error(e.code, payload, label) from None
                reason = f"HTTP {e.code}: {_error_text(payload)}"
                retry_after = e.headers.get("Retry-After") if e.headers else None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                reason = f"연결 실패: {getattr(e, 'reason', e)}"
            except ValueError as e:  # malformed JSON from the server
                raise ApiRequestError(f"{label} 응답을 읽을 수 없습니다: {e}") from None
            wait = self.backoff_s[min(attempt, len(self.backoff_s) - 1)] if self.backoff_s else 0
            try:
                wait = max(wait, float(retry_after)) if retry_after else wait
            except ValueError:
                pass
            attempt += 1
            if attempt > len(self.backoff_s) or time.monotonic() + wait >= end:
                raise ApiTransientError(f"{label} {reason}")
            time.sleep(wait)
