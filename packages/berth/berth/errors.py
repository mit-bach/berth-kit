"""Error object shared by every command."""

from __future__ import annotations

CODES = (
    "BUSY",
    "PROTECTED",
    "SESSION_MISSING",
    "IN_PROGRESS",
    "NOT_FOUND",
    "NOT_LIVE",
    "CMUX_DOWN",
    "CMUX_REJECTED",
    "FOLDER_NONEMPTY",
    "FOLDER_LIVE",
    "KIND_UNSAFE",
    "COMPACT_FAILED",  # berth/compact.py: Claude recorded no compaction
    "DUPLICATE",  # berth/attention.py: another session's flag is up for the same need
    "DISMISSED",  # berth/attention.py: Dominik dismissed this follow-up (⌘⇧X); the same one is not raised again
    "SESSION_IN_USE",
    "BAD_COLOR",
    "BAD_TRANSITION",
    "BAD_ARGUMENT",
    "FOLDER_EXISTS",
    "AMBIGUOUS",
)


class BerthError(Exception):
    """One failed command. The CLI prints this as the contract error object."""

    def __init__(self, code: str, message: str, details: dict | None = None) -> None:
        if code not in CODES:
            raise ValueError(f"unknown berth error code {code}")
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)

    def object(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class QueryAmbiguous(Exception):
    """A query matched more than one berth. The command does not act."""

    def __init__(self, matches: list[dict]) -> None:
        self.matches = matches
        super().__init__(f"{len(matches)} matches")
