"""Remote speaker labels (A..J), per-meeting names, and the mixed full recording."""

from pathlib import Path

import numpy as np
import soundfile as sf

from scribe.asr.whisper_final import FinalResult
from scribe.audio.capture import FileSource
from scribe.audio.mixdown import FULL_RECORDING, mixdown
from scribe.config import SAMPLE_RATE
from scribe.diarize import MAX_SPEAKERS, SpeakerTracker
from scribe.models import silero_vad_path
from scribe.pipeline.events import Event
from scribe.pipeline.session import Session
from scribe.store.transcript import (
    TranscriptStore,
    default_label,
    display_name,
    load,
    load_names,
    save_names,
    speaker_key,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def voice(i: int, dim: int = 16) -> np.ndarray:
    v = np.zeros(dim, np.float32)
    v[i] = 1.0
    return v


def clip(seconds: float, voice_id: int) -> np.ndarray:
    """Fake audio whose first sample encodes which 'voice' it is (read by FakeEmbed)."""
    a = np.zeros(int(seconds * SAMPLE_RATE), np.float32)
    a[0] = voice_id
    return a


def fake_embed(audio: np.ndarray) -> np.ndarray:
    """ids 0..15: distinct voices; 100+k: a slightly different take of voice k (sim ~0.99)."""
    vid = int(audio[0])
    v = voice(vid - 100) + 0.15 * voice(vid - 99) if vid >= 100 else voice(vid)
    return v / np.linalg.norm(v)


def test_same_voice_same_label_new_voice_new_label():
    t = SpeakerTracker(fake_embed)
    assert [t.assign(clip(5, v)) for v in (0, 0, 1, 0, 2, 1)] == list("AABACB")


def test_short_utterance_never_opens_a_new_speaker_unless_clearly_different():
    t = SpeakerTracker(fake_embed)
    t.assign(clip(5, 0))  # A
    # similar-ish short clip (sim ~0.99 with voice 0 shifted) -> A, does not move the centroid
    assert t.assign(clip(1, 100)) == "A"
    assert t.counts == [1]
    # a short clip that is unlike everyone still opens a new speaker
    assert t.assign(clip(1, 5)) == "B"


def test_caps_at_ten_and_reuses_the_closest():
    t = SpeakerTracker(fake_embed)
    labels = [t.assign(clip(5, v)) for v in range(12)]
    assert labels[:10] == list("ABCDEFGHIJ")
    assert len(t.centroids) == MAX_SPEAKERS
    assert labels[10] in "ABCDEFGHIJ" and labels[11] in "ABCDEFGHIJ"


def test_keys_labels_and_names(tmp_path):
    assert speaker_key("others", "B") == "others:B"
    assert speaker_key("me") == "me"
    assert default_label("others:C") == "상대 C"
    assert default_label("others") == "상대"
    assert default_label("me") == "나"
    save_names(tmp_path, {"others:A": " 김팀장 ", "others:B": "  ", "me": "홍길동"})
    names = load_names(tmp_path)
    assert names == {"others:A": "김팀장", "me": "홍길동"}  # blanks dropped = back to default
    assert display_name("others:A", names) == "김팀장"
    assert display_name("others:B", names) == "상대 B"


def ev(text, t, ch="others", spk=None, idx=0):
    return Event("final", f"{ch}-{idx:06d}", ch, t, t + 2, text, "whisper", {"speaker": spk})


def test_markdown_uses_speaker_letters_and_names(tmp_path):
    d = tmp_path / "20261010-103000"
    d.mkdir()
    st = TranscriptStore(d)
    st.append(ev("안건 시작하겠습니다", 1, spk="A"))
    st.append(ev("네 좋습니다", 5, spk="B"))
    st.append(ev("저도 동의해요", 9, ch="me"))
    st.append(ev("다음 안건입니다", 12, spk="A"))
    save_names(d, {"others:A": "김팀장"})
    text = st.close().read_text(encoding="utf-8")
    assert "🗣 김팀장, 상대 B, 나" in text
    assert text.count("**김팀장**") == 2 and "**상대 B**" in text and "**나**" in text


class FakeSherpa:
    class S:
        def accept(self, a):
            return None

        def finish(self):
            return ""

    def new_stream(self):
        return self.S()


class FakeWhisper:
    def transcribe(self, audio, prompt=None):
        return FinalResult("문장", -0.1, 0.01, 1.1)


def test_session_labels_only_the_others_channel(tmp_path):
    seen = []

    class Tracker:
        def assign(self, audio):
            seen.append(len(audio))
            return "A" if len(seen) % 2 else "B"

    src = {"others": FileSource(FIX / "meeting_tech.wav"), "me": FileSource(FIX / "meeting_review.wav")}
    sess = Session(src, FakeWhisper, FakeSherpa(), silero_vad_path(), tmp_path, record=False,
                   speakers=Tracker())
    sess.run()
    rows = [r for r in load(tmp_path / "transcript.jsonl") if r["type"] == "final"]
    others = [r["meta"]["speaker"] for r in rows if r["channel"] == "others"]
    mine = [r["meta"]["speaker"] for r in rows if r["channel"] == "me"]
    assert set(others) == {"A", "B"} and len(seen) == len(others)
    assert mine and all(s is None for s in mine)
    assert not (tmp_path / FULL_RECORDING).exists()  # record=False -> no audio at all
    assert not list(tmp_path.glob("*.flac"))


def _write_parts(d: Path, ch: str, parts: list[np.ndarray]) -> None:
    for i, a in enumerate(parts):
        sf.write(str(d / f"audio-{ch}-{i:03d}.flac"), a, SAMPLE_RATE, subtype="PCM_16")


def test_mixdown_sums_aligned_channels_across_parts(tmp_path):
    rng = np.random.default_rng(0)
    others = rng.uniform(-0.3, 0.3, int(25 * SAMPLE_RATE)).astype(np.float32)
    me = rng.uniform(-0.3, 0.3, int(18 * SAMPLE_RATE)).astype(np.float32)
    _write_parts(tmp_path, "others", [others[:int(12 * SAMPLE_RATE)], others[int(12 * SAMPLE_RATE):]])
    _write_parts(tmp_path, "me", [me])
    out = mixdown(tmp_path)
    assert out.name == FULL_RECORDING
    mixed, sr = sf.read(str(out), dtype="float32")
    assert sr == SAMPLE_RATE and len(mixed) == len(others)  # longest channel wins
    expect = others.copy()
    expect[:len(me)] += me
    assert np.max(np.abs(mixed - np.clip(expect, -1, 1))) < 2e-3  # PCM16 rounding


def test_mixdown_single_channel_and_nothing(tmp_path):
    assert mixdown(tmp_path) is None
    _write_parts(tmp_path, "others", [np.full(SAMPLE_RATE, 0.1, np.float32)] * 2)
    mixed, _ = sf.read(str(mixdown(tmp_path)), dtype="float32")
    assert len(mixed) == 2 * SAMPLE_RATE
