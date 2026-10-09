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
    assert "`01:02:05` **상대**: 확정 문장입니다." in md.read_text(encoding="utf-8")


def test_load_survives_torn_last_line(tmp_path):
    p = tmp_path / "transcript.jsonl"
    good = json.dumps(ev("final", "a").to_dict(), ensure_ascii=False)
    p.write_text(good + "\n" + good[:20], encoding="utf-8")
    assert len(load(p)) == 1
