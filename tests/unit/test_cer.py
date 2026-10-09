from scribe.eval.cer import cer, edit_distance, normalize


def test_normalize_drops_spaces_punct_and_lowercases():
    assert normalize("안녕하세요, 오늘 QA 시작!") == "안녕하세요오늘qa시작"


def test_edit_distance():
    assert edit_distance("kitten", "sitting") == 3
    assert edit_distance("", "abc") == 3
    assert edit_distance("회의", "회의") == 0


def test_cer_ignores_spacing():
    assert cer("다음 주 금요일", "다음주 금요일") == 0.0


def test_cer_value():
    assert cer("가나다라", "가나다마") == 0.25
    assert cer("", "") == 0.0
    assert cer("", "x") == 1.0
