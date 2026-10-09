import json

import numpy as np
import soundfile as sf

from scribe.audio.recorder import FlacRecorder
from scribe.config import SAMPLE_RATE
from scribe.pipeline.events import Event
from scribe.store.transcript import TranscriptStore, load


def test_flac_roundtrip_and_rollover(tmp_path):
    rec = FlacRecorder(tmp_path, "audio-others", rollover_s=1.0)
    rng = np.random.default_rng(0)
    audio = (rng.uniform(-0.5, 0.5, int(2.5 * SAMPLE_RATE))).astype(np.float32)
    for i in range(0, len(audio), 800):
        rec.write(audio[i:i + 800])
    rec.close()
    assert [p.name for p in rec.files] == [
        "audio-others-000.flac", "audio-others-001.flac", "audio-others-002.flac"]
    back = np.concatenate([sf.read(str(p), dtype="float32")[0] for p in rec.files])
    assert len(back) == len(audio)
    assert np.max(np.abs(back - audio)) < 1e-3  # PCM16 quantisation


def ev(type_, text, t=1.0, idx=0):
    return Event(type_, f"others-{idx:06d}", "others", t, t + 1, text, "whisper")


def test_store_writes_finals_only_and_exports_md(tmp_path):
    st = TranscriptStore(tmp_path)
    st.append(ev("partial", "초안"))
    st.append(ev("final", "확정 문장입니다.", t=3725.0))
    st.append(ev("status", "상태"))
    md = st.close()
    rows = load(tmp_path / "transcript.jsonl")
    assert [r["type"] for r in rows] == ["final", "status"]
    text = md.read_text(encoding="utf-8")
    assert "**상대**\n\n`01:02:05` 확정 문장입니다." in text


def test_markdown_is_a_notion_style_page(tmp_path):
    d = tmp_path / "20261010-103000"
    d.mkdir()
    st = TranscriptStore(d)
    st.append(ev("final", "첫 번째 발언", t=5.0))
    st.append(ev("final", "이어서 같은 사람", t=12.0))
    st.append(Event("final", "me-000000", "me", 20.0, 22.0, "네 알겠습니다", "whisper"))
    st.append(ev("final", "", t=30.0))  # rejected final: not exported
    st.append(ev("final", "10분 뒤 발언", t=650.0))
    text = st.close().read_text(encoding="utf-8")
    assert text.startswith("# 회의록 2026-10-10 10:30\n")
    assert "> 📅 2026년 10월 10일 10:30 · ⏱ 10분 51초 · 🗣 상대, 나 · 발언 4개" in text
    # speaker header only when the speaker changes; 10-minute section headings
    assert text.count("**상대**") == 2 and text.count("**나**") == 1  # 상대 → 나 → (new section) 상대
    assert "### 00:00:00" in text and "### 00:10:00" in text
    assert "`00:00:05` 첫 번째 발언\n\n`00:00:12` 이어서 같은 사람" in text


def test_load_survives_torn_last_line(tmp_path):
    p = tmp_path / "transcript.jsonl"
    good = json.dumps(ev("final", "a").to_dict(), ensure_ascii=False)
    p.write_text(good + "\n" + good[:20], encoding="utf-8")
    assert len(load(p)) == 1
