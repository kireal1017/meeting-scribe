"""Reject typical Whisper hallucinations before they reach the transcript."""

from __future__ import annotations

import re
from collections import deque
from difflib import SequenceMatcher

from scribe.asr.whisper_final import FinalResult

# Phrases Whisper is known to invent on silence/noise in Korean (YouTube/broadcast outros).
HALLUCINATION_PATTERNS = [
    r"시청(해\s*주셔서|해주셔서)\s*감사합니다",
    r"구독\s*[과와]?\s*좋아요",
    r"좋아요\s*와?\s*구독",
    r"MBC\s*뉴스",
    r"KBS\s*뉴스",
    r"이\s*영상은.*(자막|제공)",
    r"자막\s*(제공|by)",
    r"다음\s*영상에서\s*만나요",
]
_HALLU = [re.compile(p, re.IGNORECASE) for p in HALLUCINATION_PATTERNS]
# Real people say these too, so only drop them when the decoder was unsure.
_WEAK_HALLU = re.compile(r"^\s*(감사합니다|고맙습니다)\.?\s*$")
# Short acknowledgements ("네", "맞아요") legitimately repeat; only dedupe longer lines.
_MIN_DEDUPE_CHARS = 8


def _norm(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", s).lower()


class FinalFilter:
    def __init__(
        self,
        max_compression_ratio: float = 2.4,
        no_speech_threshold: float = 0.6,
        logprob_threshold: float = -1.0,
        repeat_window: int = 3,
        repeat_similarity: float = 0.9,
    ) -> None:
        self.max_cr = max_compression_ratio
        self.no_speech = no_speech_threshold
        self.logprob = logprob_threshold
        self.recent: deque[str] = deque(maxlen=repeat_window)
        self.repeat_similarity = repeat_similarity

    def check(self, r: FinalResult) -> str | None:
        """Return a rejection reason, or None if the result should be kept."""
        text = r.text.strip()
        if not _norm(text):
            return "empty"
        if any(p.search(text) for p in _HALLU):
            return "hallucination-phrase"
        if _WEAK_HALLU.match(text) and (r.no_speech_prob > 0.2 or r.avg_logprob < -0.7):
            return "hallucination-phrase"
        if r.no_speech_prob > self.no_speech and r.avg_logprob < self.logprob:
            return "no-speech"
        if r.compression_ratio > self.max_cr:
            return "repetitive"
        n = _norm(text)
        if len(n) >= _MIN_DEDUPE_CHARS:
            for prev in self.recent:
                if SequenceMatcher(None, n, prev).ratio() >= self.repeat_similarity:
                    return "duplicate"
            self.recent.append(n)
        return None
