"""Busy and drafts, read from the pane: what keeps a session busy, and nothing typed is lost on a close."""

from __future__ import annotations

import json
import textwrap

import pytest

from berth.activity import assess
from berth.errors import BerthError
from berth.identity import restore_drafts
from berth.model import Workspace
from berth.state import pause_berth, play_berth
from tests.fakes import FakeCmux
from tests.test_state import _insert

WIDTH = 141  # a pane as wide as the ones on this Mac


def claude_screen(draft: str = "", strip: str = "⏵⏵ auto mode on (shift+tab to cycle) · ← 1 agent", above: str = "⏺ Done.",
                  top: bool = True) -> str:
    """A Claude Code pane: output, the prompt box (wrapped as Claude wraps it), the status strip."""
    rows: list[str] = []
    for index, paragraph in enumerate(draft.split("\n") if draft else [""]):
        wrapped = textwrap.wrap(paragraph, WIDTH - 8) or [""]
        rows.extend(wrapped)
    box = ["❯ " + rows[0]] + ["  " + row for row in rows[1:]]
    lines = [above, ""]
    if top:
        lines.append("─" * (WIDTH - 12) + " Desk ─")
    lines += box + ["─" * WIDTH, "  " + strip]
    return "\n".join(lines) + "\n"


def grok_screen(draft: str = "", below: str = "Shift+Tab:mode  │  Ctrl+.:shortcuts", above: str = "") -> str:
    inner = WIDTH - 6
    body = ("❯ " + draft) if draft else "❯"
    return "\n".join([above, "  ╭" + "─" * (inner - 7) + " Desk ─╮", f"  │ {body:<{inner - 1}}│",
                      "  ╰" + "─" * inner + "╯", "  " + below]) + "\n"


# reading the pane -------------------------------------------------------


def test_an_idle_claude_is_quiet_and_each_kind_of_work_is_busy_with_its_reason():
    assert assess(claude_screen(), "other-agent", "claude").busy is False
    turn = assess(claude_screen(strip="⏵⏵ auto mode on · esc to interrupt · ← 1 agent"), "other-agent", "claude")
    assert turn.busy and turn.reasons == ["a turn is running"]
    shells = assess(claude_screen(strip="⏵⏵ bypass permissions on · 3 shells · ← 1 agent · ↓ to manage"), "other-agent", "claude")
    assert shells.reasons == ["3 background shells running"]
    agents = assess(claude_screen(strip="⏵⏵ auto mode on · ← 3 agents · ↓ to manage"), "other-agent", "claude",
                    subagents_fn=lambda: 2)
    assert agents.reasons == ["2 background agents running"]
    both = assess(claude_screen(strip="⏵⏵ auto mode on · 1 shell · esc to interrupt · ← 1 agent"), "other-agent", "claude")
    assert both.reasons == ["a turn is running", "1 background shell running"]


def test_a_question_or_an_unknown_screen_is_busy_and_a_blank_pane_is_not():
    trust = "Quick safety check: Is this a project you trust?\n\n ❯ No, exit\n   Yes, I trust this folder\n\n Enter to confirm · Esc to cancel\n"
    assert assess(trust, "other-agent", "claude").reasons == ["a question or a dialog is waiting for an answer"]
    odd = assess("Welcome to something new\nloading\n", "other-agent", "claude")
    assert odd.busy and "no prompt on screen" in odd.reasons[0]
    assert assess("\n\n   \n", "other-agent", "claude").busy is False


def test_a_draft_is_read_back_exactly_across_wraps_paragraphs_and_no_break_spaces():
    text = ("You're a fresh session, not forked from anyone, and you're my Research Agent for course 15. " * 3).strip()
    text += "\n\n---\n\nRobin sent me this Linear document, with this Slack message.\nShort line\nanother"
    screen = claude_screen(text).replace("❯ ", "❯\xa0", 1)
    activity = assess(screen, "other-agent", "claude")
    assert activity.busy is False and activity.draft is not None and activity.draft.exact
    assert activity.draft.text == text


def test_a_draft_berth_cannot_read_is_marked_not_exact():
    chip = assess(claude_screen("look at this [Pasted text #1 +45 lines] please"), "other-agent", "claude")
    assert chip.draft is not None and not chip.draft.exact and "pasted" in chip.draft.why
    # A draft taller than its box: the box shows its middle, not the ❯ line.
    middle = "\n".join("  line %d of a very long draft" % n for n in range(30))
    tall_box = "⏺ Done.\n" + "─" * (WIDTH - 12) + " Desk ─\n" + middle + "\n" + "─" * WIDTH + "\n  ⏵⏵ auto mode on\n"
    tall = assess(tall_box, "other-agent", "claude")
    assert tall.busy is False and tall.draft is not None and not tall.draft.exact and "taller" in tall.draft.why
    scrolled = assess(middle + "\n" + "─" * WIDTH + "\n  ⏵⏵ auto mode on\n", "other-agent", "claude")
    assert scrolled.draft is not None and not scrolled.draft.exact


def test_grok_and_shells():
    assert assess(grok_screen(), "grok", "grok").busy is False
    drafted = assess(grok_screen("fix the tests"), "grok", "grok")
    assert drafted.draft.text == "fix the tests" and not drafted.busy
    working = assess(grok_screen(above="⠋ Working… · send a message to interrupt"), "grok", "grok")
    assert working.busy and working.reasons == ["a turn is running"]
    assert assess("", "shell", foreground=["-zsh"]).busy is False
    running = assess("", "shell", foreground=["-zsh", "python3"])
    assert running.busy and running.reasons == ["running python3"]
    # A row filed as Claude that runs Grok is still read.
    assert assess(grok_screen("x"), "other-agent", "claude").draft.text == "x"


# closing and opening ----------------------------------------------------


def _claude_row(catalog, cmux, screen, berth_id="c1"):
    _insert(catalog, id=berth_id, title="Desk", kind="other-agent", agent_name="claude", agent_session_id="sess-c1",
            state="live", cmux_workspace_id="ws-c1", argv=["claude", "-r", "sess-c1"], display="claude -r sess-c1")
    cmux.workspaces["ws-c1"] = Workspace(id="ws-c1", title="Desk", cwd="/tmp")
    cmux.screens["ws-c1"] = screen
    return berth_id


def test_a_busy_session_is_closed_only_when_confirmed(catalog):
    cmux = FakeCmux()
    _claude_row(catalog, cmux, claude_screen(strip="⏵⏵ auto mode on · 2 shells · ← 1 agent"))
    with pytest.raises(BerthError) as busy:
        pause_berth(catalog, cmux, "c1", pid_alive_fn=lambda pid: False)
    assert busy.value.code == "BUSY" and busy.value.details["why"] == ["2 background shells running"]
    assert cmux.closed == [] and catalog.require_berth("c1")["state"] == "live"
    pause_berth(catalog, cmux, "c1", confirm=True, pid_alive_fn=lambda pid: False)
    assert cmux.closed == ["ws-c1"]


def test_a_close_saves_the_draft_and_the_reopened_session_gets_it_back_unsent(catalog, monkeypatch):
    monkeypatch.setattr("berth.state._claude_play_argv", lambda db, row, attachments: ["claude", "-r", "sess-c1"])
    cmux = FakeCmux()
    draft = "now do the Homestead people\nfrom Slack on X"
    _claude_row(catalog, cmux, claude_screen(draft))
    pause_berth(catalog, cmux, "c1", reason="sleep", pid_alive_fn=lambda pid: False)
    saved = json.loads(catalog.require_berth("c1")["attachments"])["draft"]
    assert saved["text"] == draft and saved["exact"] is True
    events = [row["kind"] for row in catalog.events_for("c1")]
    assert "draft_saved" in events  # a second copy, in the history
    record = play_berth(catalog, cmux, "c1", focus=False)
    workspace = record["cmux_workspace_id"]
    cmux.screens[workspace] = "Claude Code v2\n"  # starting: nothing is pasted yet
    restore_drafts(catalog, cmux, list(cmux.workspaces.values()))
    assert getattr(cmux, "pasted", []) == []
    cmux.screens[workspace] = claude_screen()  # the prompt is ready and empty
    restore_drafts(catalog, cmux, list(cmux.workspaces.values()))
    assert cmux.pasted == [(workspace, draft)]
    cmux.screens[workspace] = claude_screen(draft)  # the paste landed
    assert restore_drafts(catalog, cmux, list(cmux.workspaces.values())) == ["c1"]
    assert "draft" not in json.loads(catalog.require_berth("c1")["attachments"])
    assert len(cmux.pasted) == 1 and catalog.events_for("c1")[-1]["kind"] == "draft_restored"


def test_a_restore_never_types_over_new_text_and_waits_for_a_rename(catalog):
    cmux = FakeCmux()
    _claude_row(catalog, cmux, claude_screen("something else I typed"))
    with catalog.immediate():
        catalog.update_berth("c1", attachments=json.dumps({"draft": {"text": "the saved draft", "exact": True},
                                                            "rename_on_open": "Desk"}))
    restore_drafts(catalog, cmux, list(cmux.workspaces.values()))
    assert getattr(cmux, "pasted", []) == []  # the rename goes first
    with catalog.immediate():
        catalog.update_berth("c1", attachments=json.dumps({"draft": {"text": "the saved draft", "exact": True}}))
    restore_drafts(catalog, cmux, list(cmux.workspaces.values()))
    data = json.loads(catalog.require_berth("c1")["attachments"])
    assert getattr(cmux, "pasted", []) == [] and "draft" not in data
    assert data["draft_unrestored"]["text"] == "the saved draft"


def test_a_draft_berth_cannot_read_blocks_a_close_unless_confirmed(catalog):
    cmux = FakeCmux()
    _claude_row(catalog, cmux, claude_screen("see [Pasted text #1 +45 lines]"))
    with pytest.raises(BerthError) as lossy:
        pause_berth(catalog, cmux, "c1", pid_alive_fn=lambda pid: False)
    assert lossy.value.code == "BUSY" and "draft" in lossy.value.details["why"][0]
    pause_berth(catalog, cmux, "c1", confirm=True, pid_alive_fn=lambda pid: False)
    saved = json.loads(catalog.require_berth("c1")["attachments"])["draft"]
    assert saved["exact"] is False and "Pasted text" in saved["text"]


def test_a_delete_keeps_the_draft_in_the_trash(catalog):
    from berth import trash
    from berth.ops import delete_berth

    cmux = FakeCmux()
    _claude_row(catalog, cmux, claude_screen("half a thought"))
    delete_berth(catalog, cmux, "c1", caller_workspace_id=None)
    restored = trash.restore(catalog, "c1")
    assert restored["attachments"]["draft"]["text"] == "half a thought"


def test_the_mcp_skips_busy_sessions_in_a_scope_and_closes_a_named_one_only_when_confirmed(catalog, monkeypatch):
    from berth.mcp import Api

    monkeypatch.setattr("berth.state.session_on_disk", lambda root, session: True)
    cmux = FakeCmux()
    for berth_id, screen in (("idle", claude_screen()), ("turn", claude_screen(strip="⏵⏵ x · esc to interrupt")),
                             ("draft", claude_screen("keep me"))):
        _insert(catalog, id=berth_id, title=berth_id, kind="grok", agent_name="grok", agent_session_id=f"s-{berth_id}",
                state="live", cmux_workspace_id=f"ws-{berth_id}", argv=["grok", "-r", f"s-{berth_id}"], display="grok")
        cmux.workspaces[f"ws-{berth_id}"] = Workspace(id=f"ws-{berth_id}", title=berth_id, cwd="/tmp")
        cmux.screens[f"ws-{berth_id}"] = screen
    api = Api(catalog.path, cmux=cmux, caller_workspace="")
    plan = api("control", {"action": "cycle", "scope": {"folder": "/"}})
    assert {row["id"] for row in plan["would"]} == {"idle", "draft"}
    assert plan["skipped"] == [{"id": "turn", "title": "turn", "why": "busy: a turn is running"}]
    done = api("control", {"action": "cycle", "scope": {"folder": "/"}, "confirm": True})
    assert {row["id"] for row in done["done"]} == {"idle", "draft"}
    assert json.loads(catalog.require_berth("draft")["attachments"])["draft"]["text"] == "keep me"
    named = api("control", {"action": "park", "sessions": "turn"})
    assert named["failed"][0]["code"] == "BUSY" and "ws-turn" not in cmux.closed
    forced = api("control", {"action": "park", "sessions": "turn", "confirm": True})
    assert forced["done"][0]["state"] == "parked" and "ws-turn" in cmux.closed


def test_a_turn_hidden_by_a_typed_message_is_still_busy_from_the_program_status(tmp_path):
    """2026-10-28: while you type during a turn, Claude hides esc to interrupt; cmux's program status does not."""
    from datetime import datetime, timedelta, timezone

    from berth.activity import program_states, record_program

    typing = claude_screen("I've decided I want a toggle:", strip="⏵⏵ auto mode on (shift+tab to cycle)")
    assert assess(typing, "other-agent", "claude").busy is False  # the screen alone misses it
    now = datetime.now(timezone.utc)

    def event(workspace, state, seconds_ago, kind="report"):
        stamp = (now - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return {"workspace_id": workspace, "occurred_at": stamp,
                "payload": {"event": kind, "record": {"state": state} if kind == "report" else None}}

    record_program(tmp_path, event("WS-A", "working", 5))
    record_program(tmp_path, event("ws-b", "working", 3600))  # a crash left it: stale
    record_program(tmp_path, event("ws-c", "working", 50))
    record_program(tmp_path, event("ws-c", "done", 10))
    record_program(tmp_path, event("ws-d", None, 5, kind="dismissed"))
    record_program(tmp_path, event("ws-e", "blocked", 600))
    states = program_states(tmp_path, now)
    assert states == {"ws-a": "working", "ws-e": "blocked"}
    turn = assess(typing, "other-agent", "claude", program=states["ws-a"])
    assert turn.busy and turn.reasons == ["a turn is running"] and turn.draft.text == "I've decided I want a toggle:"
    asking = assess(claude_screen(), "other-agent", "claude", program="blocked")
    assert asking.reasons == ["a question or a dialog is waiting for an answer"]


def test_the_watcher_records_program_status_events(catalog):
    from berth.activity import program_states
    from berth.watch import WATCH_NAMES, handle_line

    assert "program_status.changed" in WATCH_NAMES
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    line = json.dumps({"type": "event", "name": "program_status.changed", "workspace_id": "WS-X", "occurred_at": stamp,
                       "payload": {"event": "report", "record": {"state": "working"}}})
    assert handle_line(catalog, FakeCmux(), line) is None
    assert program_states(catalog.path.parent) == {"ws-x": "working"}


def test_a_cycled_claude_comes_back_in_the_permission_mode_it_had(catalog, monkeypatch):
    monkeypatch.setattr("berth.state._claude_play_argv", lambda db, row, attachments: None)
    monkeypatch.setattr("berth.place.CMUX_CLAUDE_WRAPPER", "/nonexistent")
    cmux = FakeCmux()
    _claude_row(catalog, cmux, claude_screen(strip="⏵⏵ bypass permissions on (shift+tab to cycle) · ← 1 agent"))
    with catalog.immediate():
        catalog.update_berth("c1", recipe_argv=json.dumps(["claude", "-r", "sess-c1", "--permission-mode", "plan"]))
    pause_berth(catalog, cmux, "c1", pid_alive_fn=lambda pid: False)
    assert json.loads(catalog.require_berth("c1")["attachments"])["permission_mode"] == "bypassPermissions"
    play_berth(catalog, cmux, "c1", focus=False)
    command = cmux.created[-1]["command"]
    assert command.endswith("--permission-mode bypassPermissions -n Desk") and "plan" not in command
    assert "--permission-mode" not in json.loads(catalog.require_berth("c1")["recipe_argv"])  # not stored


def test_a_session_opens_once_in_the_mode_set_for_it_then_keeps_what_it_has(catalog):
    """2026-10-31: sessions made while create forced bypass kept reopening in bypass; Dominik: switch them once."""
    from berth.state import play_berth, set_reopen_mode
    from tests.support import write_claude_conversation

    write_claude_conversation("sess-m", "/tmp")
    _insert(catalog, id="m", title="Desk", kind="other-agent", agent_name="claude", agent_session_id="sess-m",
            state="parked", cmux_workspace_id=None, argv=["claude", "-r", "sess-m"], display="claude -r",
            attachments={"permission_mode": "bypassPermissions"})
    set_reopen_mode(catalog, "m", "auto")
    cmux = FakeCmux()
    play_berth(catalog, cmux, "m", focus=False)
    assert "--permission-mode auto" in cmux.created[-1]["command"]
    data = json.loads(catalog.require_berth("m")["attachments"])
    assert data["permission_mode"] == "auto" and "reopen_mode" not in data  # used once


STATUS_LINE_SCREEN = """
                                                                                                  ◉ xhigh · /effort
──────────────────────────────────────────────────────────────────────────────────────────── statusline-check ─
❯ 
──────────────────────────────────────────────────────────────────────────────────────────────────────────────
  personal · 5h 42% · 7d 63%
  ⏵⏵ auto mode on (shift+tab to cycle) · ← 1 agent
"""


def test_a_status_line_setting_drawn_above_the_mode_line_hides_nothing():
    """2026-10-31: a statusLine (Account Switching's usage line) is drawn first under the box (read off a real pane)."""
    from berth.send import situation

    idle = assess(STATUS_LINE_SCREEN, "other-agent", "claude")
    assert (idle.mode, situation(idle), idle.turn) == ("auto", "ready", False)
    busy = STATUS_LINE_SCREEN.replace("(shift+tab to cycle) · ← 1 agent",
                                      "(shift+tab to cycle) · esc to interrupt") + "  2 background shells\n  ← 2 agents\n"
    working = assess(busy, "other-agent", "claude")
    assert working.mode == "auto" and working.turn and working.shells == 2
    assert working.agents == 0  # "← 2 agents" is Claude's agents view, not this session's subagents (2026-11-07)


def test_a_turn_that_ended_on_an_api_error_has_its_own_status():
    """2026-11-03: Mac Developer's turn ended "API Error: Can't reach the API server" and the sidebar said Ready."""
    from berth.cmux_sidebar import status_of
    from berth.send import situation

    error = "⏺ API Error: Can't reach the API server — check your internet or DNS (ENOTFOUND)\n\n✻ Brewed for 3m 2s · done 7:31 PM"
    stopped = assess(claude_screen(above=error), "other-agent", "claude")
    shown = status_of(stopped, "other-agent")
    assert stopped.api_error.startswith("API Error: Can't reach the API server")
    assert (shown.state, shown.label) == ("api_error", "API error") and not stopped.busy
    assert situation(stopped) == "ready"  # it takes a prompt ("continue") like any idle session
    recovered = assess(claude_screen(above=error + "\n\n> continue\n\n⏺ Done: the recorder is installed."),
                       "other-agent", "claude")
    assert recovered.api_error is None and status_of(recovered, "other-agent").state == "ready"  # an old error
    retrying = assess(claude_screen(above="⏺ API Error: 529 Overloaded · Retrying in 5 seconds…",
                                    strip="⏵⏵ auto mode on (shift+tab to cycle) · esc to interrupt"),
                      "other-agent", "claude")
    assert status_of(retrying, "other-agent").state == "working"  # Claude retries it itself while the turn runs
    overloaded = assess(claude_screen(above="⏺ API Error: 529 {\"type\":\"overloaded_error\"}"), "other-agent", "claude")
    assert status_of(overloaded, "other-agent").state == "api_error"


def test_the_agents_view_count_is_not_this_sessions_subagents():
    """2026-11-07: after a hard sleep, 39 idle panes said "← 2 agents" (Claude Code's agents view: a stale background
    session counted in every pane) and Berth showed "Waiting on 1 subagent" on all of them."""
    from berth.cmux_sidebar import status_of

    idle = assess(claude_screen(strip="⏵⏵ auto mode on (shift+tab to cycle) · ← 2 agents"), "other-agent", "claude",
                  subagents_fn=lambda: 0)
    assert (idle.agents, idle.busy, status_of(idle, "other-agent").label) == (0, False, "Ready")


def test_running_subagents_are_unfinished_and_recently_written(tmp_path, monkeypatch):
    import json as _json
    import os
    import time

    from berth import activity, identity

    transcript = tmp_path / "s-1.jsonl"
    transcript.write_text("")
    folder = tmp_path / "s-1" / "subagents"
    folder.mkdir(parents=True)
    now = time.time()
    for name, finished, age in (("a-running", None, 30), ("a-done", True, 30), ("a-crashed", None, 7200)):
        (folder / f"agent-{name}.meta.json").write_text(_json.dumps({} if finished is None else {"finished": True}))
        log = folder / f"agent-{name}.jsonl"
        log.write_text("{}")
        os.utime(log, (now - age, now - age))
    monkeypatch.setattr(identity, "claude_transcript", lambda session, cwd: transcript)
    row = {"agent_name": "claude", "agent_session_id": "s-1", "cwd": "/tmp"}
    assert activity.running_subagents(row, now=now) == 1  # the finished one and the one a crash left are not
    assert activity.running_subagents({"agent_name": "grok", "agent_session_id": "g", "cwd": ""}) is None
