# meeting-scribe

로컬에서 동작하는 실시간 한국어 회의 전사기 (PoC).

- **임시 자막**: sherpa-onnx 스트리밍 Zipformer 한국어 모델 (CPU, 즉시 표시)
- **확정 자막**: faster-whisper large-v3-turbo int8_float16 (NVIDIA GPU 필수, CPU 폴백 없음)
- Zoom 등 시스템 출력은 WASAPI 루프백으로 캡처, 원본 오디오는 FLAC으로 상시 보존

## 요구 사항
- Windows 10/11, NVIDIA GPU (VRAM 4GB 이상), 드라이버 525 이상
- [uv](https://docs.astral.sh/uv/)

## 바로 실행 (더블클릭)

1. [uv](https://docs.astral.sh/uv/) 설치 (한 번만, PowerShell):
   `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
2. 프로젝트 폴더의 **`meeting-scribe.bat`** 더블클릭

처음에는 필요한 패키지(약 2.5GB)를 설치하고, 이후에는 바로 창이 열립니다(콘솔 창 없음).
`git pull` 로 코드를 받은 뒤에도 그대로 더블클릭하면 바뀐 패키지가 자동 반영됩니다.
바탕화면에 두려면 `meeting-scribe.bat` 를 우클릭 → **바로 가기 만들기** 후 옮기면 됩니다.
오류가 나면 `%USERPROFILE%\.meeting-scribe\logs\gui-<날짜>.log` 를 확인해 주세요.

## 설치 (명령줄)

```powershell
uv sync
```

가상환경은 프로젝트의 `.venv`, 모델·로그는 `%USERPROFILE%\.meeting-scribe\` 에 저장됩니다
(`MEETING_SCRIBE_HOME` 환경 변수로 변경 가능). 최초 실행 시 모델(약 1.7GB)을 내려받습니다.

> `%LOCALAPPDATA%` 를 쓰지 않는 이유: Store/MSIX 앱(예: 일부 터미널, IDE)에서 실행하면 Windows가
> AppData 쓰기를 그 앱 전용 공간으로 몰래 옮겨, 다른 터미널에서는 모델이 보이지 않게 됩니다.

## 사용법

### 데스크톱 화면 (권장)
```powershell
uv run scribe gui
```
노션 스타일 창이 열립니다([docs/DESIGN-notion.md](docs/DESIGN-notion.md) 참고). 엔진을 미리 불러온 뒤
**기록 시작**을 누르면, 회의록이 문서처럼 쌓입니다. 말하는 동안에는 회색 임시 문장이 맨 아래에 보이고,
문장이 끝나면 확정 문장으로 바뀝니다. 왼쪽 목록에서 지난 회의록을 열고 검색할 수 있습니다.
GPU를 쓸 수 없으면 기록을 시작하지 않고 진단 창(`scribe doctor`)을 띄웁니다.

**미니 모드**: 상단의 **미니 모드** 버튼을 누르면 스마트폰 세로 크기의 작은 창으로 바뀌어 화면 오른쪽 아래,
Zoom 위에 항상 떠 있습니다(위쪽을 잡고 끌어서 이동). 하단 바의 버튼은
**기록 시작 / 일시중지 / 재개**, **기록 중지**, **이전 페이지**(큰 창으로 돌아가기)입니다.
일시중지 중에는 받아적지 않고 녹음 파일에도 무음만 남으며, 재개 후 시각은 회의 시작 기준으로 이어집니다.

> 시스템 소리 전체(루프백)를 받아적으므로, 기록 중에는 다른 영상·음악을 꺼 주세요.
`.venv\Scripts\scribe-gui.exe` 로 콘솔 창 없이 실행할 수도 있습니다.

### 명령줄
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
