from scribe.asr.filters import FinalFilter
from scribe.asr.whisper_final import FinalResult


def r(text, lp=-0.2, ns=0.01, cr=1.2):
    return FinalResult(text, lp, ns, cr)


def test_keeps_normal_text():
    assert FinalFilter().check(r("다음 주 금요일까지 마무리하겠습니다.")) is None


def test_drops_known_hallucinations():
    f = FinalFilter()
    assert f.check(r("시청해주셔서 감사합니다.")) == "hallucination-phrase"
    assert f.check(r("MBC 뉴스 이덕영입니다.")) == "hallucination-phrase"
    assert f.check(r("구독과 좋아요 부탁드립니다")) == "hallucination-phrase"


def test_weak_phrase_only_dropped_when_unsure():
    f = FinalFilter()
    assert f.check(r("감사합니다.", lp=-0.1, ns=0.01)) is None
    assert f.check(r("감사합니다.", lp=-0.9, ns=0.01)) == "hallucination-phrase"


def test_drops_empty_no_speech_repetitive():
    f = FinalFilter()
    assert f.check(r("  ...  ")) == "empty"
    assert f.check(r("음 그러니까", lp=-1.5, ns=0.9)) == "no-speech"
    assert f.check(r("네 네 네 네 네 네 네 네", cr=3.0)) == "repetitive"


def test_duplicate_long_lines_but_not_short_acks():
    f = FinalFilter()
    line = "서버 배포는 목요일 오후에 진행하겠습니다"
    assert f.check(r(line)) is None
    assert f.check(r(line + ".")) == "duplicate"
    assert f.check(r("네.")) is None
    assert f.check(r("네.")) is None
