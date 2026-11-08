"""ISO-8601 UTC timestamps."""

from __future__ import annotations

from datetime import datetime, timezone

STALE_PLAY_SECONDS = 120


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_time(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def age_seconds(value: str, now: datetime | None = None) -> float:
    current = now or datetime.now(timezone.utc)
    return (current - parse_time(value)).total_seconds()
