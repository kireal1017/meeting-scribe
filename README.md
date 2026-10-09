# meeting-scribe

로컬에서 동작하는 실시간 한국어 회의 전사기 (PoC).

- **임시 자막**: sherpa-onnx 스트리밍 Zipformer 한국어 모델 (CPU, 즉시 표시)
- **확정 자막**: faster-whisper large-v3-turbo int8_float16 (NVIDIA GPU 필수, CPU 폴백 없음)
- Zoom 등 시스템 출력은 WASAPI 루프백으로 캡처, 원본 오디오는 FLAC으로 상시 보존

## 요구 사항
- Windows 10/11, NVIDIA GPU (VRAM 4GB 이상), 드라이버 525 이상
- [uv](https://docs.astral.sh/uv/)

## 설치

```powershell
uv sync
```

가상환경은 프로젝트의 `.venv`, 모델·로그는 `%USERPROFILE%\.meeting-scribe\` 에 저장됩니다
(`MEETING_SCRIBE_HOME` 환경 변수로 변경 가능). 최초 실행 시 모델(약 1.7GB)을 내려받습니다.

> `%LOCALAPPDATA%` 를 쓰지 않는 이유: Store/MSIX 앱(예: 일부 터미널, IDE)에서 실행하면 Windows가
> AppData 쓰기를 그 앱 전용 공간으로 몰래 옮겨, 다른 터미널에서는 모델이 보이지 않게 됩니다.

## 사용법

```powershell
uv run scribe doctor              # GPU 환경 점검 (D1~D6)
uv run scribe devices             # 오디오 장치 목록
uv run scribe run                 # Zoom 출력(루프백) 실시간 전사, Ctrl+C로 종료
uv run scribe run --mic           # 내 마이크도 '나' 채널로 함께 전사 (헤드셋 권장)
uv run scribe run --out "$env:USERPROFILE\Documents\회의록"  # 결과 저장 폴더 지정 (기본: 프로젝트의 회의록 폴더)
uv run scribe run --hotwords terms.txt      # 회의 용어(줄마다 1개)를 Whisper 프롬프트에 사용
uv run scribe run --file a.wav --realtime   # 파일을 실시간 속도로 재생하며 전사
uv run scribe bench whisper|sherpa|e2e      # 성능 측정 (docs/BENCHMARKS.md)
```

화면에는 말하는 동안 인식된 조각이 회색으로 이어 붙고(약 1초 지연), 문장이 끝나면 그 아래에 확정 자막(약 1.5초 후)이 찍힙니다.

```
  상대 ▸ 에이 이제와서 그랬다가지고 봤는데 깜짝놀랐
[00:01:50] 상대: 예예 이제 와서 그래가지고 봤는데 깜짝 놀랐습니다
```
임시 자막 모델은 `--partial-model`로 바꿀 수 있습니다(기본값 `kspon174m-c16`).

## 테스트
```powershell
uv run python scripts/make_fixtures.py   # TTS 테스트 음성 생성 (최초 1회)
uv run pytest                            # 단위 테스트 (GPU 불필요)
uv run pytest -m gpu                     # 실제 모델로 전체 파이프라인 테스트
uv run python scripts/check_loopback.py  # 루프백 캡처 확인 (기본 출력 장치로 소리 재생)
```

결과는 프로젝트의 `회의록\<시작시각>\` 폴더(어디서 실행하든 동일, `--out` 으로 변경 가능)에
`transcript.jsonl`, `transcript.md`, `audio-<채널>-NNN.flac`(10분 단위) 로 쌓입니다.
`회의록/` 은 git에서 제외되어 있습니다.

## GPU 문제가 생기면
`scribe doctor` 가 실패한 단계, 원인 후보, 조치 방법을 출력하고
`%USERPROFILE%\.meeting-scribe\logs\doctor-*.txt` 에 리포트를 남깁니다. 이슈 등록 시 첨부해 주세요.

## 녹취 동의
회의를 기록하기 전에 참석자에게 알리고 동의를 받으세요.

## 라이선스
코드는 MIT. 사용하는 모델의 라이선스는 [NOTICE.md](NOTICE.md) 참고.
