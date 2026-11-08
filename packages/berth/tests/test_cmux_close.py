from pathlib import Path

import pytest

from berth.cmux import Cmux
from berth.errors import BerthError

REFUSAL = "Error: confirmation_required: Workspace has a running process; retry with --force"


def recording(refuse_plain: bool, message: str = REFUSAL):
    calls: list[list[str]] = []

    def run(args: list[str]):
        calls.append(args)
        if refuse_plain and "--force" not in args:
            raise BerthError("CMUX_REJECTED", message, {"args": args})

    return calls, run


def test_close_retries_with_force_on_cmux_065_refusal():
    cmux = Cmux(binary="cmux", socket=Path("/nonexistent.sock"))
    calls, cmux._run = recording(refuse_plain=True)
    cmux.close_workspace("W1")
    assert calls == [["workspace", "close", "W1"], ["workspace", "close", "W1", "--force"]]


def test_close_sends_no_force_when_cmux_064_accepts():
    cmux = Cmux(binary="cmux", socket=Path("/nonexistent.sock"))
    calls, cmux._run = recording(refuse_plain=False)
    cmux.close_workspace("W1")
    assert calls == [["workspace", "close", "W1"]]


def test_close_raises_other_refusals_without_force():
    cmux = Cmux(binary="cmux", socket=Path("/nonexistent.sock"))
    calls, cmux._run = recording(refuse_plain=True, message="Error: not_found: Workspace not found")
    with pytest.raises(BerthError):
        cmux.close_workspace("W1")
    assert calls == [["workspace", "close", "W1"]]


def dropping(times: int, message: str = "Error: Socket closed before reply"):
    """cmux 0.65.2 drops a paste of a few KB and delivers nothing (2026-10-30)."""
    calls: list[tuple[list[str], str | None]] = []

    def run(args: list[str], *, stdin: str | None = None):
        calls.append((args, stdin))
        if len(calls) <= times:
            raise BerthError("CMUX_REJECTED", message, {"args": args})

    return calls, run


def test_a_dropped_draft_paste_is_tried_again(monkeypatch):
    monkeypatch.setattr("berth.cmux.time.sleep", lambda seconds: None)
    cmux = Cmux(binary="cmux", socket=Path("/nonexistent.sock"))
    calls, cmux._run = dropping(2)
    cmux.paste_text("W1", "draft " * 1200)
    assert len(calls) == 3 and all(stdin == "draft " * 1200 for _, stdin in calls)


def test_a_paste_refused_over_a_draft_is_not_retried(monkeypatch):
    monkeypatch.setattr("berth.cmux.time.sleep", lambda seconds: None)
    cmux = Cmux(binary="cmux", socket=Path("/nonexistent.sock"))
    calls, cmux._run = dropping(9, "Error: paste: the agent prompt holds text someone is typing")
    with pytest.raises(BerthError):
        cmux.paste_text("W1", "draft")
    assert len(calls) == 1
