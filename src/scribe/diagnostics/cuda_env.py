"""Make the pip-installed CUDA 12 runtime (nvidia-cublas-cu12, nvidia-cudnn-cu12) loadable on Windows.

CTranslate2 resolves cuBLAS/cuDNN through the normal DLL search path, so the wheel `bin`
folders are put first on PATH and registered with add_dll_directory. Putting them *first*
also shields us from a system-wide CUDA 13 / older cuDNN being picked up instead.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REQUIRED_DLLS = [
    "cublas64_12.dll",
    "cublasLt64_12.dll",
    "cudnn64_9.dll",
    "cudnn_ops64_9.dll",
    "cudnn_cnn64_9.dll",
]

_registered: list[Path] | None = None


def nvidia_bin_dirs() -> list[Path]:
    """`<site-packages>/nvidia/<lib>/bin` folders shipped by the nvidia-*-cu12 wheels."""
    dirs: list[Path] = []
    for entry in sys.path:
        root = Path(entry) / "nvidia"
        if root.is_dir():
            dirs.extend(sorted(p for p in root.glob("*/bin") if p.is_dir()))
    return dirs


def register() -> list[Path]:
    global _registered
    if _registered is not None:
        return _registered
    dirs = nvidia_bin_dirs()
    if sys.platform == "win32":
        for d in dirs:
            os.add_dll_directory(str(d))
        os.environ["PATH"] = os.pathsep.join([*map(str, dirs), os.environ.get("PATH", "")])
    _registered = dirs
    return dirs


def register_onnxruntime() -> None:
    """sherpa-onnx's Windows wheel loads `onnxruntime.dll` by name. Windows 11 ships an old
    one (1.17) in System32 that wins the search and fails with "API version [28] is not
    available"; point the loader at the pip onnxruntime build first."""
    if sys.platform != "win32":
        return
    import onnxruntime

    os.add_dll_directory(str(Path(onnxruntime.__file__).parent / "capi"))


def find_dll(name: str) -> Path | None:
    for d in nvidia_bin_dirs():
        if (d / name).exists():
            return d / name
    return None


def foreign_copies(name: str) -> list[Path]:
    """Other copies of a DLL on PATH that are not ours (possible version conflicts)."""
    ours = {p.resolve() for p in nvidia_bin_dirs()}
    hits = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        p = Path(entry)
        try:
            if p.resolve() in ours:
                continue
            if (p / name).exists():
                hits.append(p / name)
        except OSError:
            continue
    return hits
