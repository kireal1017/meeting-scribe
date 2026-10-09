"""`scribe` command line: doctor / devices / run / bench."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
from datetime import datetime
from pathlib import Path


def _enable_ansi() -> None:
    if sys.platform == "win32":
        os.system("")  # turns on VT processing in classic conhost
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except AttributeError:
            pass


def cmd_doctor(_args) -> int:
    from scribe.diagnostics.gpu_doctor import run_doctor

    ok, _results, _path = run_doctor()
    return 0 if ok else 1


def cmd_devices(_args) -> int:
    from scribe.audio.capture import default_devices, list_devices

    loop, mic = default_devices()
    print(f"기본 루프백(상대방): [{loop.index}] {loop.name} ({loop.channels}ch {loop.rate}Hz)")
    print("기본 마이크(나):     " + (f"[{mic.index}] {mic.name}" if mic else "없음"))
    print("\nWASAPI 입력 장치:")
    for d in list_devices():
        kind = "loopback" if d.loopback else "input   "
        print(f"  [{d.index:>3}] {kind} {d.name} ({d.channels}ch {d.rate}Hz)")
    return 0


def cmd_run(args) -> int:
    from scribe.asr.sherpa_stream import SherpaStreaming
    from scribe.asr.whisper_final import GpuUnavailableError, WhisperFinal
    from scribe.audio.capture import FileSource, LoopbackSource, MicSource, default_devices
    from scribe.config import transcripts_dir
    from scribe.console import ConsoleView
    from scribe.models import silero_vad_path
    from scribe.pipeline.session import Session

    # check the output folder before spending ~30 s loading models
    out_root = Path(args.out).resolve() if args.out else transcripts_dir()
    try:
        out_root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"[중단] 결과 저장 폴더를 만들 수 없습니다: {out_root}\n  ({e})\n"
              "존재하는 드라이브/폴더를 --out 으로 지정해 주세요.", file=sys.stderr)
        return 1

    hotwords = ""
    if args.hotwords:
        hotwords = Path(args.hotwords).read_text(encoding="utf-8").replace("\n", ", ")

    if args.file:
        sources = {"others": FileSource(Path(args.file), realtime=args.realtime)}
    else:
        loop, mic = default_devices()
        print(f"상대방(루프백): {loop.name}")
        sources = {"others": LoopbackSource(loop)}
        if args.mic:
            if mic is None:
                print("마이크를 찾지 못했습니다.", file=sys.stderr)
                return 1
            print(f"나(마이크): {mic.name}")
            sources["me"] = MicSource(mic)

    print("sherpa-onnx(CPU) 로딩 중...")
    sherpa = SherpaStreaming(args.partial_model)
    out = out_root / datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        print("Whisper(GPU) 로딩 중...")
        def make_whisper():
            return WhisperFinal(model=args.final_model, beam_size=args.beam_size)

        from scribe import settings
        from scribe.diarize import SherpaEmbedder, SpeakerTracker
        from scribe.models import speaker_model_path

        tracker = SpeakerTracker(SherpaEmbedder(speaker_model_path()))  # 상대 A..J
        session = Session(sources, make_whisper, sherpa, silero_vad_path(), out, hotwords=hotwords,
                          record=settings.load().record_audio, speakers=tracker)
    except GpuUnavailableError as e:
        print(f"\n[중단] {e}\n`uv run scribe doctor` 결과를 공유해 주세요.", file=sys.stderr)
        return 2
    signal.signal(signal.SIGINT, lambda *_: session.stop())
    if args.duration:
        timer = threading.Timer(args.duration, session.stop)
        timer.daemon = True
        timer.start()
    print(f"기록 위치: {out}\n전사 시작 (Ctrl+C로 종료)\n")
    md = session.run(ConsoleView())
    print(f"\n\n저장 완료: {md}")
    return 0


def cmd_gui(args) -> int:
    from scribe.gui.app import main as gui_main

    return gui_main([sys.argv[0]], Path(args.out).resolve() if args.out else None)


def cmd_bench(args) -> int:
    from scribe.bench import run as bench

    fixtures = bench.load_fixtures(args.clips)
    if args.target == "whisper":
        res = bench.bench_whisper(fixtures, beam_size=args.beam_size, model=args.final_model)
    elif args.target == "sherpa":
        res = bench.bench_sherpa(fixtures, num_threads=args.threads, model=args.partial_model)
    else:
        res = bench.bench_e2e(fixtures, realtime=not args.fast, model=args.partial_model,
                              final_model=args.final_model)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(res, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    _enable_ansi()
    from scribe import settings
    from scribe.config import FINAL_MODELS, WHISPER_MODEL

    saved = settings.load()
    from scribe.models import DEFAULT_PARTIAL_MODEL, PARTIAL_MODELS

    p = argparse.ArgumentParser(prog="scribe", description="로컬 실시간 한국어 회의 전사")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="GPU 환경 점검 (D1~D6)").set_defaults(func=cmd_doctor)
    sub.add_parser("devices", help="오디오 장치 목록").set_defaults(func=cmd_devices)

    r = sub.add_parser("run", help="실시간 전사")
    r.add_argument("--file", help="오디오 파일 입력 (기본: 스피커 루프백)")
    r.add_argument("--realtime", action="store_true", help="파일을 실시간 속도로 재생")
    r.add_argument("--mic", action="store_true", help="마이크(나) 채널도 전사")
    r.add_argument("--hotwords", help="용어 목록 파일 (줄마다 1개)")
    r.add_argument("--beam-size", type=int, default=3)
    r.add_argument("--final-model", choices=FINAL_MODELS, default=saved.final_model,
                   help="확정 자막 Whisper 모델 (기본: GUI에서 고른 설정)")
    r.add_argument("--duration", type=float, help="N초 후 자동 종료")
    r.add_argument("--out",
                   help="결과 저장 폴더 (기본: 프로젝트의 회의록 폴더). 그 안에 <시작시각> 폴더가 생깁니다")
    r.add_argument("--partial-model", choices=PARTIAL_MODELS, default=DEFAULT_PARTIAL_MODEL)
    r.set_defaults(func=cmd_run)

    g = sub.add_parser("gui", help="노션 스타일 데스크톱 화면으로 실행")
    g.add_argument("--out", help="결과 저장 폴더 (기본: 프로젝트의 회의록 폴더)")
    g.set_defaults(func=cmd_gui)

    b = sub.add_parser("bench", help="벤치마크 (tests/fixtures 사용)")
    b.add_argument("target", choices=["whisper", "sherpa", "e2e"])
    b.add_argument("--clips", nargs="*", help="특정 fixture 이름만")
    b.add_argument("--beam-size", type=int, default=3)
    b.add_argument("--final-model", choices=FINAL_MODELS, default=WHISPER_MODEL)
    b.add_argument("--threads", type=int, default=2)
    b.add_argument("--partial-model", choices=PARTIAL_MODELS, default=DEFAULT_PARTIAL_MODEL)
    b.add_argument("--fast", action="store_true", help="e2e: 실시간 대기 없이 최대 속도")
    b.add_argument("--json-out")
    b.set_defaults(func=cmd_bench)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
