"""Generate reproducible Korean test clips with ground truth using the Windows TTS voice
"Microsoft Heami" (ko-KR). Each clip = sentences separated by pauses, written as
tests/fixtures/<name>.wav (16 kHz mono) + <name>.json (text + per-sentence timing).

TTS audio is cleaner than a real meeting, so treat these as a smoke/regression set,
not as the accuracy benchmark for real Zoom audio.

Usage: uv run python scripts/make_fixtures.py
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 16_000
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures"

CLIPS: dict[str, list[str]] = {
    "meeting_planning": [
        "안녕하세요, 오늘 회의 시작하겠습니다.",
        "먼저 지난주 스프린트 결과부터 공유드릴게요.",
        "로그인 기능은 개발이 끝났고 지금 QA 단계에 있습니다.",
        "결제 페이지는 디자인 수정 요청이 있어서 일정이 조금 밀렸어요.",
        "다음 주 금요일이 deadline이니까 그 전에 테스트를 마쳐야 합니다.",
        "혹시 이 부분에 대해서 의견 있으신 분 계신가요?",
        "없으시면 다음 안건으로 넘어가겠습니다.",
    ],
    "meeting_tech": [
        "서버 응답 속도가 평균 이백 밀리초 정도 나오고 있습니다.",
        "데이터베이스 쿼리를 최적화하면 절반 정도 줄일 수 있을 것 같아요.",
        "API 문서는 이번 주 안에 업데이트하겠습니다.",
        "코드 리뷰는 PR 올리면 바로 확인해 주세요.",
        "배포는 목요일 오후에 진행하는 걸로 하죠.",
        "모니터링 대시보드에 알림 설정도 추가해야 합니다.",
        "장애가 생기면 바로 슬랙 채널에 공유해 주시면 됩니다.",
    ],
    "meeting_review": [
        "이번 분기 매출은 목표 대비 구십오 퍼센트를 달성했습니다.",
        "신규 고객 유입은 늘었는데 재구매율이 조금 떨어졌어요.",
        "고객 피드백을 보면 배송 속도에 대한 불만이 가장 많았습니다.",
        "마케팅 팀에서는 다음 달에 프로모션을 준비하고 있다고 합니다.",
        "예산은 지난 분기와 비슷한 수준으로 잡으면 될 것 같습니다.",
        "회의록은 제가 정리해서 오후에 공유드리겠습니다.",
        "오늘 회의는 여기까지 하겠습니다. 수고하셨습니다.",
    ],
}

PS_TEMPLATE = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice('Microsoft Heami Desktop')
$s.Rate = 1
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$items = Get-Content -Raw -Encoding UTF8 '{manifest}' | ConvertFrom-Json
foreach ($it in $items) {{
  $s.SetOutputToWaveFile($it.path, $fmt)
  $s.Speak($it.text)
}}
$s.SetOutputToNull()
"""


def synth(sentences: list[str], tmp: Path) -> list[np.ndarray]:
    items = [{"path": str(tmp / f"s{i}.wav"), "text": t} for i, t in enumerate(sentences)]
    manifest = tmp / "manifest.json"
    manifest.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    ps = tmp / "synth.ps1"
    ps.write_text(PS_TEMPLATE.format(manifest=manifest), encoding="utf-8-sig")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps)],
                   check=True)
    out = []
    for it in items:
        audio, sr = sf.read(it["path"], dtype="float32")
        assert sr == SR, sr
        out.append(trim(audio))
    return out


def trim(audio: np.ndarray, threshold: float = 0.01, margin_s: float = 0.03) -> np.ndarray:
    """Drop the TTS engine's leading/trailing silence so sentence timings mark real speech
    (latency is measured from these timings)."""
    loud = np.flatnonzero(np.abs(audio) > threshold)
    if not len(loud):
        return audio
    m = int(margin_s * SR)
    return audio[max(0, loud[0] - m):loud[-1] + m]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)
    for name, sentences in CLIPS.items():
        with tempfile.TemporaryDirectory() as td:
            parts = synth(sentences, Path(td))
        pieces = [np.zeros(int(0.8 * SR), np.float32)]
        timeline = []
        pos = len(pieces[0])
        for text, audio in zip(sentences, parts, strict=True):
            timeline.append({"text": text, "start": pos / SR, "end": (pos + len(audio)) / SR})
            pieces.append(audio)
            pos += len(audio)
            gap = np.zeros(int(rng.uniform(0.9, 1.6) * SR), np.float32)
            pieces.append(gap)
            pos += len(gap)
        clip = np.concatenate(pieces)
        # light noise floor so VAD/Whisper see something closer to a real call than digital silence
        clip = clip + rng.normal(0, 0.002, len(clip)).astype(np.float32)
        sf.write(OUT / f"{name}.wav", clip, SR, subtype="PCM_16")
        meta = {"sample_rate": SR, "duration": len(clip) / SR,
                "text": " ".join(sentences), "sentences": timeline}
        (OUT / f"{name}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
        print(f"{name}: {len(clip) / SR:.1f}s, {len(sentences)} sentences")


if __name__ == "__main__":
    main()
