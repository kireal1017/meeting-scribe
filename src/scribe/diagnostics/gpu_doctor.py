"""`scribe doctor`: step-by-step GPU environment check (D1..D6).

Stops at the first failing step and prints: what failed, the raw error, likely causes,
what the user has to do, and what an assistant/maintainer can do. The full report is also
written to logs/doctor-<timestamp>.txt for bug reports. No personal data is collected.
"""

from __future__ import annotations

import ctypes
import importlib.metadata as md
import platform
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from scribe import config
from scribe.diagnostics import cuda_env
from scribe.diagnostics.gpu_monitor import VramSampler

MIN_DRIVER_MAJOR = 525  # first driver branch that supports the CUDA 12 runtime


@dataclass
class CheckResult:
    id: str
    title: str
    ok: bool
    detail: str = ""
    error: str = ""
    causes: list[str] = field(default_factory=list)
    user_fixes: list[str] = field(default_factory=list)
    agent_fixes: list[str] = field(default_factory=list)


def _fail(id_: str, title: str, error: str, causes, user_fixes, agent_fixes) -> CheckResult:
    return CheckResult(id_, title, False, error=error, causes=causes,
                       user_fixes=user_fixes, agent_fixes=agent_fixes)


def d1_driver() -> CheckResult:
    title = "NVIDIA 드라이버 (nvidia-smi)"
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError as e:
        return _fail("D1", title, str(e),
                     ["NVIDIA 드라이버 미설치", "드라이버 설치가 손상됨"],
                     ["NVIDIA 홈페이지 또는 노트북 제조사(LG) 지원 페이지에서 최신 드라이버 설치 후 재부팅"],
                     [])
    if out.returncode != 0:
        return _fail("D1", title, (out.stderr or out.stdout).strip(),
                     ["드라이버와 GPU 통신 실패", "GPU가 장치 관리자에서 비활성화됨"],
                     ["장치 관리자 > 디스플레이 어댑터에서 RTX GPU 활성화 확인",
                      "드라이버 재설치(클린 설치) 후 재부팅"],
                     [])
    name, driver, mem = [v.strip() for v in out.stdout.strip().splitlines()[0].split(",")]
    major = int(driver.split(".")[0])
    if major < MIN_DRIVER_MAJOR:
        return _fail("D1", title, f"driver {driver} < {MIN_DRIVER_MAJOR}",
                     ["드라이버가 CUDA 12 런타임을 지원하지 않는 구버전"],
                     [f"드라이버를 {MIN_DRIVER_MAJOR} 이상으로 업데이트"], [])
    return CheckResult("D1", title, True, f"{name}, driver {driver}, VRAM {mem} MiB")


def d2_packages() -> CheckResult:
    title = "CUDA 12 런타임 pip 패키지"
    pkgs = ["nvidia-cublas-cu12", "nvidia-cudnn-cu12", "ctranslate2", "faster-whisper"]
    found, missing = [], []
    for p in pkgs:
        try:
            found.append(f"{p}=={md.version(p)}")
        except md.PackageNotFoundError:
            missing.append(p)
    if missing:
        return _fail("D2", title, f"missing: {', '.join(missing)}",
                     ["의존성 설치 누락", "다른 가상환경의 python으로 실행 중"],
                     ["`uv run scribe doctor`로 실행했는지 확인"],
                     ["`uv sync` 재실행 (UV_PROJECT_ENVIRONMENT 확인)"])
    dirs = cuda_env.nvidia_bin_dirs()
    if not dirs:
        return _fail("D2", title, "site-packages/nvidia/*/bin 폴더 없음",
                     ["nvidia-*-cu12 휠이 DLL 없이 설치됨(비 Windows 휠)"],
                     [], ["`uv pip install --reinstall nvidia-cublas-cu12 nvidia-cudnn-cu12`"])
    return CheckResult("D2", title, True, "; ".join(found) + f"; {len(dirs)} bin dirs")


def d3_dlls() -> CheckResult:
    title = "cuBLAS/cuDNN DLL 로드"
    cuda_env.register()
    loaded, errors, conflicts = [], [], []
    for name in cuda_env.REQUIRED_DLLS:
        path = cuda_env.find_dll(name)
        if path is None:
            errors.append(f"{name}: 파일 없음")
            continue
        try:
            ctypes.WinDLL(str(path))
            loaded.append(name)
        except OSError as e:
            errors.append(f"{name}: {e}")
        conflicts += [str(p) for p in cuda_env.foreign_copies(name)]
    if errors:
        return _fail("D3", title, "\n".join(errors),
                     ["DLL 의존성(예: zlibwapi, VC++ 런타임) 누락",
                      "PATH의 다른 CUDA/cuDNN 버전과 충돌"]
                     + ([f"충돌 후보: {c}" for c in conflicts[:5]]),
                     ["Microsoft Visual C++ 2015-2022 재배포 패키지(x64) 설치"],
                     ["nvidia-*-cu12 재설치", "PATH에서 충돌하는 CUDA 경로 확인"])
    detail = f"{len(loaded)} DLL 로드 성공"
    if conflicts:
        detail += f" (참고: PATH에 다른 사본 {len(conflicts)}개 있음, 우리 경로가 우선)"
    return CheckResult("D3", title, True, detail)


def d4_ctranslate2() -> CheckResult:
    title = "CTranslate2 CUDA 인식"
    cuda_env.register()
    try:
        import ctranslate2
        n = ctranslate2.get_cuda_device_count()
        types = ctranslate2.get_supported_compute_types("cuda") if n else set()
    except Exception as e:
        return _fail("D4", title, f"{type(e).__name__}: {e}",
                     ["ctranslate2가 기대하는 CUDA/cuDNN 버전과 불일치"],
                     [], ["ctranslate2 / nvidia-cudnn-cu12 버전 조합 재설치"])
    if n < 1:
        return _fail("D4", title, "get_cuda_device_count() == 0",
                     ["드라이버는 있으나 CUDA 런타임 초기화 실패",
                      "ctranslate2 CPU 전용 빌드 설치됨"],
                     ["재부팅 후 재시도"], ["ctranslate2 재설치(pip 공식 휠은 CUDA 포함)"])
    if config.WHISPER_COMPUTE_TYPE not in types:
        return _fail("D4", title, f"{config.WHISPER_COMPUTE_TYPE} 미지원: {sorted(types)}",
                     ["GPU 아키텍처가 int8 연산 미지원"], [],
                     ["compute_type을 float16으로 변경 검토(VRAM 증가)"])
    return CheckResult("D4", title, True, f"devices={n}, compute types={sorted(types)}")


def d5_d6_inference() -> list[CheckResult]:
    t5, t6 = "Whisper 모델 GPU 로드 + 추론", "실제 GPU 사용 확인 (VRAM)"
    from scribe.asr.whisper_final import WhisperFinal

    rng = np.random.default_rng(0)
    audio = (rng.standard_normal(16_000 * 5) * 0.01).astype(np.float32)
    try:
        with VramSampler() as s:
            t0 = time.perf_counter()
            eng = WhisperFinal()
            load_s = time.perf_counter() - t0
            t0 = time.perf_counter()
            eng.transcribe(audio)
            infer_s = time.perf_counter() - t0
        del eng
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        oom = "out of memory" in str(e).lower()
        return [_fail("D5", t5, msg,
                      (["VRAM 부족: 다른 프로그램(게임, 브라우저 GPU 가속, 다른 AI 앱)이 점유"]
                       if oom else [])
                      + ["모델 다운로드 실패/손상", "CUDA 런타임 오류"],
                      ["GPU를 쓰는 다른 프로그램 종료 후 재시도", "인터넷 연결 확인(최초 1회 다운로드)"],
                      [f"모델 캐시 삭제 후 재다운로드: {config.models_dir() / 'whisper'}"])]
    r5 = CheckResult("D5", t5, True, f"load {load_s:.1f}s, 5s audio inference {infer_s:.2f}s")
    delta = s.peak_delta_mb
    if delta is None:
        r6 = _fail("D6", t6, "nvidia-smi로 VRAM을 읽지 못함", ["nvidia-smi 응답 없음"], [], [])
    elif delta < 300:
        r6 = _fail("D6", t6, f"VRAM 증가 {delta} MiB (<300) — GPU가 실제로 쓰이지 않은 것으로 보임",
                   ["Windows가 python.exe를 내장 그래픽(Intel)에 할당"],
                   ["설정 > 시스템 > 디스플레이 > 그래픽에서 python.exe를 '고성능(NVIDIA)'으로 지정",
                    "NVIDIA 제어판 > 3D 설정 관리 > 프로그램 설정에서 python.exe 고성능 GPU 지정"],
                   [])
    elif delta > config.VRAM_BUDGET_MB:
        r6 = _fail("D6", t6, f"VRAM 사용 {delta} MiB > 예산 {config.VRAM_BUDGET_MB} MiB",
                   ["compute_type 설정 오류"], [], ["compute_type 확인(int8_float16)"])
    else:
        r6 = CheckResult("D6", t6, True, f"peak VRAM +{delta} MiB (예산 {config.VRAM_BUDGET_MB})")
    return [r5, r6]


STEPS: list[Callable[[], CheckResult | list[CheckResult]]] = [
    d1_driver, d2_packages, d3_dlls, d4_ctranslate2, d5_d6_inference,
]


def run_doctor(echo: Callable[[str], None] = print) -> tuple[bool, list[CheckResult], str]:
    results: list[CheckResult] = []
    ok = True
    for step in STEPS:
        try:
            out = step()
        except Exception as e:  # a crash inside a check is itself a failure to report
            out = _fail(step.__name__.split("_")[0].upper(), step.__name__, traceback.format_exc(),
                        [f"점검 코드 예외: {type(e).__name__}"], [], ["리포트를 개발자에게 전달"])
        for r in out if isinstance(out, list) else [out]:
            results.append(r)
            echo(_format_line(r))
            if not r.ok:
                ok = False
        if not ok:
            break
    report = _format_report(ok, results)
    path = config.logs_dir() / f"doctor-{datetime.now():%Y%m%d-%H%M%S}.txt"
    path.write_text(report, encoding="utf-8")
    if not ok:
        echo("")
        echo(_format_failure(results[-1]))
    echo(f"\n리포트 저장: {path}")
    return ok, results, str(path)


def _format_line(r: CheckResult) -> str:
    mark = "PASS" if r.ok else "FAIL"
    return f"[{mark}] {r.id} {r.title}: {r.detail or r.error.splitlines()[0]}"


def _format_failure(r: CheckResult) -> str:
    lines = [f"=== {r.id} 실패: {r.title} ===", "에러:", *("  " + ln for ln in r.error.splitlines())]
    if r.causes:
        lines += ["원인 후보:", *(f"  {i}. {c}" for i, c in enumerate(r.causes, 1))]
    if r.user_fixes:
        lines += ["직접 하실 조치:", *(f"  - {f}" for f in r.user_fixes)]
    if r.agent_fixes:
        lines += ["도구/개발자가 할 수 있는 조치 (실행 전 확인):", *(f"  - {f}" for f in r.agent_fixes)]
    lines.append("조치 후 `uv run scribe doctor` 를 다시 실행하세요. CPU로 대체 실행하지 않습니다.")
    return "\n".join(lines)


def _format_report(ok: bool, results: list[CheckResult]) -> str:
    env = [
        f"time: {datetime.now().isoformat(timespec='seconds')}",
        f"python: {sys.version.split()[0]} ({sys.executable})",
        f"os: {platform.platform()}",
        f"overall: {'PASS' if ok else 'FAIL'}",
        "",
    ]
    body = [_format_line(r) for r in results]
    if not ok:
        body += ["", _format_failure(results[-1])]
    return "\n".join(env + body) + "\n"
