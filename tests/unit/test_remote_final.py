"""External API final pass: settings, keys, the single constructor and both provider adapters.

A local fake server (stdlib http.server) stands in for OpenAI / OpenRouter; nothing leaves the
machine. Timings are injected small so retries and deadlines run in milliseconds.
"""

import ast
import base64
import io
import json
import sys
import threading
import time
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from scribe import api_keys, config, settings
from scribe.asr import backends
from scribe.asr.types import (
    ApiAuthError,
    ApiConfigError,
    ApiRequestError,
    ApiTransientError,
)

SRC = Path(__file__).resolve().parents[2] / "src" / "scribe"
KEY = "sk-test-not-a-real-key-123456"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MEETING_SCRIBE_HOME", str(tmp_path))
    for var in api_keys.ENV_VARS.values():
        monkeypatch.delenv(var, raising=False)
    return tmp_path


class FakeServer:
    """Replies from a queue of (status, body, headers); records every request."""

    def __init__(self):
        self.replies, self.requests, self.delay = [], [], 0.0
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers["Content-Length"]))
                server.requests.append({"path": self.path, "headers": dict(self.headers),
                                        "body": body})
                if server.delay:
                    time.sleep(server.delay)
                status, payload, headers = (server.replies.pop(0) if server.replies
                                            else (200, {"text": "기본 응답"}, {}))
                data = json.dumps(payload).encode()
                try:
                    self.send_response(status)
                    for k, v in headers.items():
                        self.send_header(k, v)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass  # client gave up (deadline test)

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.close()


def remote(server, provider="openai", model=None, zdr=True, **kw):
    from scribe.asr.remote_final import RemoteFinal

    spec = backends.FinalSpec(provider, model or config.API_PROVIDERS[provider]["models"][0],
                              zdr=zdr)
    kw.setdefault("deadline_s", 1.0)
    kw.setdefault("backoff_s", (0.05, 0.05))
    return RemoteFinal(spec, KEY, base_url=server.url, **kw)


AUDIO = (0.1 * np.sin(np.linspace(0, 2000, 24_000))).astype(np.float32)  # 1.5 s


def multipart_fields(req):
    msg = BytesParser(policy=email_policy).parsebytes(
        f"Content-Type: {req['headers']['Content-Type']}\r\n\r\n".encode() + req["body"])
    out = {}
    for p in msg.iter_parts():
        raw = p.get_payload(decode=True)
        name = p.get_param("name", header="content-disposition")
        out[name] = raw if p.get_filename() else raw.decode("utf-8")  # servers read fields as UTF-8
    return out


# --- settings ------------------------------------------------------------------------
def test_settings_api_fields_roundtrip_and_sanitising(home):
    s = settings.load()
    assert s.final_backend == "local" and s.openrouter_zdr is True
    assert s.api_models == {"openai": "whisper-1", "openrouter": "openai/whisper-large-v3-turbo"}
    s.final_backend = "openrouter"
    s.api_models["openrouter"] = "openai/whisper-large-v3"
    settings.save(s)
    back = settings.load()
    assert back.final_backend == "openrouter"
    assert back.api_models["openrouter"] == "openai/whisper-large-v3"
    (home / "settings.json").write_text(json.dumps({
        "final_backend": "cloud", "api_models": {"openai": "", "x": "y"}}),
        encoding="utf-8")
    bad = settings.load()
    assert bad.final_backend == "cloud"  # kept, never coerced to local
    assert bad.api_models == {"openai": "whisper-1",
                              "openrouter": "openai/whisper-large-v3-turbo"}
    with pytest.raises(ApiConfigError):
        backends.spec_from_settings(bad)


def test_spec_from_settings(home):
    s = settings.Settings()
    assert backends.spec_from_settings(s) == backends.FinalSpec("local", "large-v3-turbo")
    s.final_backend = "openai"
    spec = backends.spec_from_settings(s)
    assert spec.remote and spec.model == "whisper-1"
    s.final_backend = "openrouter"
    assert backends.spec_from_settings(s).model == "openai/whisper-large-v3-turbo"
    assert backends.spec_from_settings(s, zdr=False).zdr is False


# --- keys --------------------------------------------------------------------------
@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_api_keys_are_encrypted_per_provider(home):
    api_keys.save_api_key("openai", f"  {KEY}  ")
    raw = (home / "api-keys.json").read_text(encoding="utf-8")
    assert KEY not in raw and "openai" in raw
    assert api_keys.api_key("openai") == KEY
    assert api_keys.has_api_key("openai") and not api_keys.has_api_key("openrouter")
    with pytest.raises(ApiConfigError, match="OpenRouter API 키가 없습니다"):
        api_keys.api_key("openrouter")
    api_keys.delete_api_key("openai")
    assert not api_keys.has_api_key("openai")


def test_env_key_is_bound_to_its_provider(monkeypatch):
    monkeypatch.setenv("MEETING_SCRIBE_OPENAI_API_KEY", "env-openai")
    assert api_keys.api_key("openai") == "env-openai"
    with pytest.raises(ApiConfigError):
        api_keys.api_key("openrouter")  # never borrows the other provider's key
    with pytest.raises(ApiConfigError):
        api_keys.api_key("groq")


# --- single constructor -------------------------------------------------------------
def test_create_final_api_builds_offline_and_needs_a_key(server, monkeypatch):
    import scribe.asr.whisper_final as wf

    monkeypatch.setattr(wf, "WhisperFinal", lambda **k: pytest.fail("GPU model built"))
    with pytest.raises(ApiConfigError, match="API 키가 없습니다"):
        backends.create_final(backends.FinalSpec("openai", "whisper-1"))
    typed = backends.create_final(backends.FinalSpec("openai", "whisper-1"), api_key="typed")
    assert typed.remote  # a key typed in 설정 (connection test) works before it is saved
    monkeypatch.setenv("MEETING_SCRIBE_OPENAI_API_KEY", KEY)
    with pytest.raises(ApiConfigError):
        backends.create_final(backends.FinalSpec("groq", "x"))
    assert server.requests == []
    final = backends.create_final(backends.FinalSpec("openai", "whisper-1"))
    assert final.remote and final.label == "OpenAI · whisper-1" and KEY not in repr(final)
    assert server.requests == []  # building it makes no request (no warm-up upload)


def test_create_final_local_never_touches_remote(monkeypatch):
    import scribe.asr.whisper_final as wf

    monkeypatch.setattr(wf, "WhisperFinal", lambda **k: ("whisper", k))
    assert backends.create_final(backends.FinalSpec("local", "large-v3")) == (
        "whisper", {"model": "large-v3"})


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    return names


def test_only_backends_builds_finals():
    """Structural guard for "no automatic switching": one module imports the API backend and
    one module constructs the GPU model (plus the GPU-only diagnostics)."""
    importers, constructors = set(), set()
    allow = {"asr/backends.py", "bench/run.py", "diagnostics/gpu_doctor.py"}
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if any(n.startswith("scribe.asr.remote_final") for n in _imports(path)):
            importers.add(rel)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "WhisperFinal"):
                constructors.add(rel)
    assert importers == {"asr/backends.py"}
    assert constructors <= allow and "asr/backends.py" in constructors


# --- OpenAI adapter -----------------------------------------------------------------
def test_openai_multipart_and_verbose_metrics(server):
    server.replies.append((200, {"text": " 배포는 목요일입니다. ", "segments": [
        {"avg_logprob": -0.2, "no_speech_prob": 0.01, "compression_ratio": 1.1},
        {"avg_logprob": -0.4, "no_speech_prob": 0.05, "compression_ratio": 1.3}]}, {}))
    res = remote(server).transcribe(AUDIO, prompt="QA, 스프린트")
    assert res.text == "배포는 목요일입니다." and res.metrics
    assert res.avg_logprob == pytest.approx(-0.3) and res.no_speech_prob == 0.05
    assert res.meta == {"provider": "openai", "upload_s": 1.5}
    req = server.requests[0]
    assert req["path"] == "/v1/audio/transcriptions"
    assert req["headers"]["Authorization"] == f"Bearer {KEY}"
    f = multipart_fields(req)
    assert (f["model"], f["language"], f["prompt"], f["response_format"]) == (
        "whisper-1", "ko", "QA, 스프린트", "verbose_json")
    audio, sr = sf.read(io.BytesIO(f["file"]), dtype="float32")
    assert sr == 16_000 and abs(len(audio) - len(AUDIO)) <= 1


def test_openai_gpt4o_is_text_only(server):
    server.replies.append((200, {"text": "네 알겠습니다"}, {}))
    res = remote(server, model="gpt-4o-transcribe").transcribe(AUDIO)
    assert res.text == "네 알겠습니다" and not res.metrics
    f = multipart_fields(server.requests[0])
    assert f["response_format"] == "json" and "prompt" not in f


# --- OpenRouter adapter -------------------------------------------------------------
def test_openrouter_json_body_zdr_and_cost(server):
    server.replies.append((200, {"text": "회의를 시작하겠습니다", "usage": {"cost": 0.00042}}, {}))
    res = remote(server, "openrouter").transcribe(AUDIO, prompt="무시되는 문맥")
    assert res.text == "회의를 시작하겠습니다" and not res.metrics
    assert res.meta["cost"] == pytest.approx(0.00042) and res.meta["provider"] == "openrouter"
    body = json.loads(server.requests[0]["body"])
    assert body["model"] == "openai/whisper-large-v3-turbo" and body["language"] == "ko"
    assert body["provider"] == {"zdr": True} and "prompt" not in body
    assert body["input_audio"]["format"] == "flac"
    raw = base64.b64decode(body["input_audio"]["data"])  # plain base64, not a data URI
    assert abs(len(sf.read(io.BytesIO(raw))[0]) - len(AUDIO)) <= 1
    remote(server, "openrouter", zdr=False).transcribe(AUDIO)
    assert json.loads(server.requests[1]["body"])["provider"] == {"zdr": False}


def test_openrouter_404_messages(server):
    server.replies.append((404, {"error": {"message": "No endpoints found matching your data "
                                                      "policy"}}, {}))
    with pytest.raises(ApiConfigError, match="ZDR"):
        remote(server, "openrouter").transcribe(AUDIO)
    server.replies.append((404, {"error": {"message": "Model not found"}}, {}))
    with pytest.raises(ApiConfigError, match="모델 ID"):
        remote(server, "openrouter").transcribe(AUDIO)


# --- failures (both adapters) --------------------------------------------------------
@pytest.mark.parametrize("provider", ["openai", "openrouter"])
def test_retry_auth_and_request_errors(server, provider):
    server.replies += [(429, {"error": "slow down"}, {}), (200, {"text": "됐습니다"}, {})]
    assert remote(server, provider).transcribe(AUDIO).text == "됐습니다"
    assert len(server.requests) == 2
    echoed = "Incorrect API key provided: sk-proj-****************ab12. See your keys."
    server.replies.append((401, {"error": {"message": echoed}}, {}))
    with pytest.raises(ApiAuthError, match="API 키") as err:
        remote(server, provider).transcribe(AUDIO)
    assert "sk-" not in str(err.value) and "ab12" not in str(err.value)
    assert "[키 가림]" in str(err.value)
    server.replies.append((400, {"error": {"message": "audio too short"}}, {}))
    with pytest.raises(ApiRequestError, match="audio too short"):
        remote(server, provider).transcribe(AUDIO)
    server.replies += [(500, {}, {})] * 3
    with pytest.raises(ApiTransientError, match="HTTP 500"):
        remote(server, provider).transcribe(AUDIO)
    for req in server.requests:  # the key only ever goes to this provider's endpoint
        assert req["headers"]["Authorization"] == f"Bearer {KEY}"


def test_deadline_is_enforced(server):
    server.delay = 2.0
    t0 = time.monotonic()
    with pytest.raises(ApiTransientError):
        remote(server, deadline_s=0.5).transcribe(AUDIO)
    assert time.monotonic() - t0 < 0.5 + 0.3
    final = remote(server, deadline_s=5.0, draining_deadline_s=0.3)
    final.set_draining()
    t0 = time.monotonic()
    with pytest.raises(ApiTransientError):
        final.transcribe(AUDIO)
    assert time.monotonic() - t0 < 0.3 + 0.3


def test_connection_refused_is_transient():
    from scribe.asr.remote_final import RemoteFinal

    final = RemoteFinal(backends.FinalSpec("openai", "whisper-1"), KEY,
                        base_url="http://127.0.0.1:9/v1", deadline_s=0.5, backoff_s=(0.05,))
    with pytest.raises(ApiTransientError, match="연결 실패"):
        final.transcribe(AUDIO)


def test_default_timings():
    from scribe.asr import remote_final as rf

    final = rf.RemoteFinal(backends.FinalSpec("openai", "whisper-1"), KEY)
    assert (final.deadline_s, final.draining_deadline_s, final.backoff_s) == (20.0, 5.0, (1.0, 2.0))
    assert final.base_url == "https://api.openai.com/v1"
    assert rf.RemoteFinal(backends.FinalSpec("openrouter", "m"), KEY).base_url == (
        "https://openrouter.ai/api/v1")
