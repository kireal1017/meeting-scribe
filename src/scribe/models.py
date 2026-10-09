"""Model download/lookup. Everything lands in %LOCALAPPDATA%/meeting-scribe/models."""

from __future__ import annotations

import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from scribe.config import models_dir

SHERPA_RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"
# speaker embedding for telling remote participants apart (3D-Speaker CAM++, Apache-2.0, 27 MB)
SPEAKER_MODEL = "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
SPEAKER_MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
                     f"speaker-recongition-models/{SPEAKER_MODEL}")  # sic: upstream tag spelling

# Streaming (partial) models.
# Official sherpa-onnx release tarballs:
SHERPA_STREAMING_MODELS = {
    "zipformer-ko": "sherpa-onnx-streaming-zipformer-korean-2024-06-16",
}
# Community icefall model (Apache-2.0, KsponSpeech) on Hugging Face, one file set per chunk size.
HF_STREAMING_REPO = "kangkyu/icefall-asr-ko-streaming-zipformer-174m"
HF_STREAMING_MODELS = {
    "kspon174m-c16": "chunk-16-left-128",
    "kspon174m-c32": "chunk-32-left-128",
    "kspon174m-c64": "chunk-64-left-128",
}
# kspon174m-c16 had the best draft accuracy in docs/BENCHMARKS.md (CER ~10% vs ~25%)
DEFAULT_PARTIAL_MODEL = "kspon174m-c16"
PARTIAL_MODELS = [*SHERPA_STREAMING_MODELS, *HF_STREAMING_MODELS]


@dataclass(frozen=True)
class TransducerFiles:
    encoder: Path
    decoder: Path
    joiner: Path
    tokens: Path


def _download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, dir=dest.parent, suffix=".part") as tmp:
        tmp_path = Path(tmp.name)
    try:
        print(f"[download] {url}")
        urllib.request.urlretrieve(url, tmp_path)
        tmp_path.replace(dest)
    finally:
        tmp_path.unlink(missing_ok=True)
    return dest


def silero_vad_path() -> Path:
    """Silero VAD v6 as bundled (MIT) inside faster-whisper; no separate download."""
    import faster_whisper

    return Path(faster_whisper.__file__).parent / "assets" / "silero_vad_v6.onnx"


def sherpa_streaming_model(key: str = DEFAULT_PARTIAL_MODEL) -> TransducerFiles:
    if key in HF_STREAMING_MODELS:
        return _hf_streaming_model(HF_STREAMING_MODELS[key])
    name = SHERPA_STREAMING_MODELS[key]
    root = models_dir() / "sherpa" / name
    if not root.exists():
        archive = _download(f"{SHERPA_RELEASE}/{name}.tar.bz2", models_dir() / f"{name}.tar.bz2")
        with tarfile.open(archive, "r:bz2") as tf:
            tf.extractall(models_dir() / "sherpa", filter="data")
        archive.unlink()
    return _find_transducer(root)


def _hf_streaming_model(variant: str) -> TransducerFiles:
    from huggingface_hub import hf_hub_download

    root = models_dir() / "sherpa" / HF_STREAMING_REPO.split("/")[1]
    names = {
        part: f"{part}-epoch-99-avg-1-{variant}.int8.onnx"
        for part in ("encoder", "decoder", "joiner")
    }
    names["tokens"] = "tokens.txt"
    paths = {}
    for key, name in names.items():
        local = root / name
        # already downloaded -> no network round-trip (meetings must work offline)
        paths[key] = local if local.exists() else Path(
            hf_hub_download(HF_STREAMING_REPO, name, local_dir=root))
    return TransducerFiles(paths["encoder"], paths["decoder"], paths["joiner"], paths["tokens"])


def _find_transducer(root: Path) -> TransducerFiles:
    def pick(prefix: str, int8: bool) -> Path:
        cands = sorted(root.glob(f"{prefix}*.onnx"))
        if not cands:
            raise FileNotFoundError(f"no {prefix}*.onnx in {root}")
        preferred = [c for c in cands if (".int8." in c.name) == int8]
        return (preferred or cands)[0]

    # int8 encoder/joiner are faster on CPU; the tiny decoder stays fp32 (int8 hurts accuracy)
    return TransducerFiles(
        encoder=pick("encoder", int8=True),
        decoder=pick("decoder", int8=False),
        joiner=pick("joiner", int8=True),
        tokens=root / "tokens.txt",
    )


def speaker_model_path() -> Path:
    p = models_dir() / "speaker" / SPEAKER_MODEL
    if not p.exists():
        _download(SPEAKER_MODEL_URL, p)
    return p


def whisper_download_root() -> Path:
    p = models_dir() / "whisper"
    p.mkdir(parents=True, exist_ok=True)
    return p
