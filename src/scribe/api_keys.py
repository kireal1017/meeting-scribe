"""API keys for the external final-pass providers.

Stored per provider in ~/.meeting-scribe/api-keys.json, encrypted with Windows DPAPI
(CryptProtectData): only the same Windows user on the same machine can decrypt them. The plain
key is never written to disk or logs. An environment variable per provider overrides the stored
key; each provider only ever reads its own variable, so a key cannot reach the other provider.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path

from scribe import config
from scribe.asr.types import ApiConfigError

ENV_VARS = {p: f"MEETING_SCRIBE_{p.upper()}_API_KEY" for p in config.API_PROVIDERS}
CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    if sys.platform != "win32":
        raise ApiConfigError("API 키 저장은 Windows에서만 지원합니다. 환경 변수를 사용해 주세요.")
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))  # kept alive for the call below
    src = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN,
              ctypes.byref(out)):
        raise ApiConfigError("저장된 API 키를 읽을 수 없습니다. 키를 다시 입력해 주세요.")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def _path() -> Path:
    return config.app_dir() / "api-keys.json"


def _load() -> dict[str, str]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}


def _write(data: dict[str, str]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(p)


def _check(provider: str) -> None:
    if provider not in config.API_PROVIDERS:
        raise ApiConfigError(f"지원하지 않는 API 제공자입니다: {provider}")


def save_api_key(provider: str, key: str) -> None:
    _check(provider)
    key = key.strip()
    if not key:
        raise ApiConfigError("API 키가 비어 있습니다.")
    data = _load()
    data[provider] = base64.b64encode(_dpapi(key.encode("utf-8"), protect=True)).decode("ascii")
    _write(data)


def delete_api_key(provider: str) -> None:
    data = _load()
    if data.pop(provider, None) is not None:
        _write(data)


def has_api_key(provider: str) -> bool:
    return bool(os.environ.get(ENV_VARS.get(provider, ""), "").strip()) or provider in _load()


def api_key(provider: str) -> str:
    _check(provider)
    env = os.environ.get(ENV_VARS[provider], "").strip()
    if env:
        return env
    stored = _load().get(provider)
    if not stored:
        label = config.API_PROVIDERS[provider]["label"]
        raise ApiConfigError(f"{label} API 키가 없습니다. 왼쪽 아래 ‘설정’에서 키를 입력해 주세요.")
    try:
        return _dpapi(base64.b64decode(stored), protect=False).decode("utf-8")
    except ValueError as e:
        raise ApiConfigError("저장된 API 키가 손상되었습니다. 키를 다시 입력해 주세요.") from e
