import io
import re

from scribe.console import ConsoleView
from scribe.pipeline.events import Event


def ev(type_, text, ch="others", idx=0, t=110.0):
    return Event(type_, f"{ch}-{idx:06d}", ch, t, t + 2, text, "x")


def plain(view):
    return re.sub(r"\x1b\[[0-9;]*m", "", view.out.getvalue())


def test_drafts_append_only_new_chunks_then_final_line():
    v = ConsoleView(io.StringIO())
    for text in ["에이", "에이이제와서", "에이이제와서그랬다가지고", "에이이제와서그랬다가지고봤는데"]:
        v(ev("partial", text))
    v(ev("final", "예예 이제 와서 그래가지고 봤는데"))
    out = plain(v)
    assert out == ("  상대 ▸ 에이 이제와서 그랬다가지고 봤는데\n"
                   "[00:01:50] 상대: 예예 이제 와서 그래가지고 봤는데\n")
    assert "\r" not in v.out.getvalue()  # never rewrites lines


def test_repeated_identical_partial_prints_nothing():
    v = ConsoleView(io.StringIO())
    v(ev("partial", "안녕하세요"))
    v(ev("partial", "안녕하세요"))
    assert plain(v).count("안녕하세요") == 1


def test_revised_draft_prints_only_changed_tail():
    v = ConsoleView(io.StringIO())
    v(ev("partial", "서부"))
    v(ev("partial", "서버응답"))
    assert plain(v) == "  상대 ▸ 서부 버응답"


def test_interleaved_channels_get_their_own_lines():
    v = ConsoleView(io.StringIO())
    v(ev("partial", "가나", ch="others"))
    v(ev("partial", "네", ch="me"))
    v(ev("partial", "가나다라", ch="others"))
    assert plain(v) == "  상대 ▸ 가나\n  나 ▸ 네\n  상대 ▸ … 다라"


def test_new_segment_starts_new_draft_line_and_rejected_final_prints_nothing():
    v = ConsoleView(io.StringIO())
    v(ev("partial", "음", idx=0))
    v(ev("final", "", idx=0))
    v(ev("partial", "다음", idx=1))
    assert plain(v) == "  상대 ▸ 음\n  상대 ▸ 다음"
