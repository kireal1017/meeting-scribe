"""The event contract shared by the pipeline, transcript store and any UI."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

EventType = Literal["partial", "final", "status"]


@dataclass
class Event:
    type: EventType
    segment_id: str  # "<channel>-<index>", partial->final replacement key
    channel: str  # "others" (loopback) | "me" (mic)
    t_start: float  # seconds since session start
    t_end: float
    text: str
    engine: str  # "sherpa" | "whisper" | "system" | API label ("OpenAI · whisper-1")
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def segment_id(channel: str, index: int) -> str:
    return f"{channel}-{index:06d}"
