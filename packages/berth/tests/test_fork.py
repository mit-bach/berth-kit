"""Grok forks the way Claude does: one path for F, `berth fork`, and the MCP tool."""

from __future__ import annotations

import json
from urllib.parse import quote

import pytest

from berth.identity import promote_shells
from berth.model import HookRecord, Workspace
from berth.ops import fork_berth, mkdir_path
from berth.place import grok_resume_argv
from berth.state import play_berth
from tests.fakes import FakeCmux
from tests.test_state import _insert

CWD = "/tmp/desk"
FLAGS = ["--always-approve", "--no-subagents", "--reasoning-effort", "xhigh"]


@pytest.fixture(autouse=True)
def grok_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def _session(home, session_id, title="Parent work"):
    folder = home / ".grok" / "sessions" / quote(CWD, safe="") / session_id
    folder.mkdir(parents=True)
    (folder / "summary.json").write_text(json.dumps({"generated_title": title}), encoding="utf-8")


def _grok_row(catalog, berth_id, session_id, *, state="parked", folder=None, title="Desk", argv=None):
    argv = argv or ["grok", "-r", session_id, *FLAGS]
    return _insert(
        catalog, id=berth_id, title=title, state=state, kind="grok", agent_name="grok",
        agent_session_id=session_id, cwd=CWD, cmux_workspace_id="ws-1" if state == "live" else None,
        folder_id=folder or catalog.ensure_inbox(), argv=argv, display=" ".join(argv),
    )


def test_a_grok_fork_opens_next_to_the_original_with_its_flags_and_its_name(catalog, grok_home):
    desks = mkdir_path(catalog, "Desks/Left")["id"]
    _session(grok_home, "parent")
    _grok_row(catalog, "parent-row", "parent", state="live", folder=desks)
    cmux = FakeCmux()
    record = fork_berth(catalog, cmux, "parent-row", focus=False)
    fork_id = record["agent_session_id"]
    assert fork_id and fork_id != "parent"
    assert record["title"] == "Desk ⑂" and record["folder_id"] == desks and record["state"] == "live"
    assert record["kind"] == "grok" and record["attachments"]["fork_of"] == "parent"
    # Flags before the ids: cmux's hook keeps a launch's arguments only up to the first id.
    assert record["recipe_argv"] == ["grok", *FLAGS, "-r", "parent", "--fork-session", "-s", fork_id]
    assert f"xhigh -r parent --fork-session -s {fork_id}" in cmux.created[-1]["command"]
    # Grok has no name flag: Berth types /rename once, and the watcher does not type it again.
    assert (record["cmux_workspace_id"], f"/rename Desk ⑂\n") in cmux.sent
    assert record["attachments"]["name_sent"] == "Desk ⑂"
    assert catalog.require_berth("parent-row")["state"] == "live"


def test_a_parked_grok_fork_resumes_itself_or_forks_its_parent_again(catalog, grok_home):
    _session(grok_home, "parent")
    _session(grok_home, "kept")
    fork_argv = ["grok", *FLAGS, "-r", "parent", "--fork-session", "-s", "{id}"]
    for berth_id, session_id in (("kept-row", "kept"), ("lost-row", "lost")):
        argv = [token.replace("{id}", session_id) for token in fork_argv]
        _grok_row(catalog, berth_id, session_id, title=berth_id, argv=argv)
        with catalog.immediate():
            catalog.update_berth(berth_id, attachments=json.dumps({"fork_of": "parent"}))
    cmux = FakeCmux()
    play_berth(catalog, cmux, "kept-row", focus=False)
    command = cmux.created[-1]["command"]
    assert command.endswith("--always-approve --no-subagents --reasoning-effort xhigh -r kept")
    play_berth(catalog, cmux, "lost-row", focus=False)
    assert cmux.created[-1]["command"].endswith("xhigh -r parent --fork-session -s lost")


def test_only_claude_and_grok_rows_fork(catalog):
    from berth.errors import BerthError

    _insert(catalog, id="codex", title="c", state="parked", kind="other-agent", agent_name="codex",
            agent_session_id="x", cmux_workspace_id=None)
    with pytest.raises(BerthError) as caught:
        fork_berth(catalog, FakeCmux(), "codex")
    assert caught.value.code == "KIND_UNSAFE"


def test_a_resume_recipe_drops_one_shot_flags():
    hook = HookRecord(
        agent_name="grok", session_id="fork-id", workspace_id="ws-1", surface_id=None, pid=1, cwd=CWD,
        runtime_status="idle", last_body=None, executable="/x/grok", launcher="grok",
        arguments=["/x/grok", "--fork-session", "-s", "--permission-mode", "bypassPermissions", "-p", "hi"],
    )
    assert grok_resume_argv(hook) == ["grok", "--permission-mode", "bypassPermissions", "-r", "fork-id"]


def test_a_pause_keeps_the_flags_a_lossy_hook_dropped(catalog, monkeypatch):
    """cmux captured `grok -r <id> --always-approve ...` as plain `grok`; the pause kept no flags."""
    from berth.state import pause_berth

    _grok_row(catalog, "row", "sess", state="live")
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(id="ws-1", title="Desk", cwd=CWD)
    hook = HookRecord(
        agent_name="grok", session_id="sess", workspace_id="ws-1", surface_id=None, pid=1, cwd=CWD,
        runtime_status="idle", last_body=None, executable="/x/grok", launcher="grok", arguments=["/x/grok"],
    )
    result = pause_berth(catalog, cmux, "row", hook=hook, pid_alive_fn=lambda pid: False)
    assert result["recipe_argv"] == ["grok", *FLAGS, "-r", "sess"]


def test_a_headless_grok_run_in_a_pane_does_not_take_over_that_row(catalog, grok_home, monkeypatch):
    """2026-10-23: `grok -p` run from a Claude pane's shell re-labelled that Claude row as Grok."""
    _session(grok_home, "headless")
    _insert(catalog, id="claude-row", title="Berth Developer", state="live", kind="other-agent",
            agent_name="claude", agent_session_id="real", cwd=CWD, argv=["claude", "-r", "real"])
    monkeypatch.setattr("berth.place.pid_alive", lambda pid: False)
    hook = HookRecord(
        agent_name="grok", session_id="headless", workspace_id="ws-1", surface_id=None, pid=8079, cwd=CWD,
        runtime_status="idle", last_body="PELICAN", executable="/x/grok", launcher="grok",
        arguments=["/x/grok", "-p", "x"], hook_event="SessionEnd",
    )
    promote_shells(catalog, {"ws-1": hook})
    row = catalog.require_berth("claude-row")
    assert (row["kind"], row["agent_session_id"]) == ("other-agent", "real")


def test_cycle_pauses_then_plays_the_same_session_in_a_new_workspace(catalog, grok_home):
    from berth.drain import drain_once
    from berth.ops import cycle_berth
    from berth.catalog import insert_intent
    from datetime import datetime, timezone

    _session(grok_home, "sess")
    desks = mkdir_path(catalog, "Desks")["id"]
    _grok_row(catalog, "row", "sess", state="live", folder=desks)
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(id="ws-1", title="Desk", cwd=CWD)
    record = cycle_berth(catalog, cmux, "row", pid_alive_fn=lambda pid: False)
    assert cmux.closed == ["ws-1"]
    assert record["state"] == "live" and record["cmux_workspace_id"] == cmux.created[-1]["id"] != "ws-1"
    assert record["agent_session_id"] == "sess" and record["folder_id"] == desks
    assert cmux.created[-1]["focus"] is False
    assert cmux.created[-1]["command"].endswith("--reasoning-effort xhigh -r sess")
    phases = [event["detail"] for event in catalog.events_for("row") if event["kind"] == "cycle"]
    assert ['"off"' in phases[0], '"on"' in phases[1]] == [True, True]
    # The screen's c goes through the intent table and the same function.
    insert_intent(catalog.path, "cycle", "row", {"confirm": False}, datetime.now(timezone.utc))
    outcome = drain_once(catalog, cmux, pid_alive_fn=lambda pid: False)
    assert outcome[0]["result"] == "done" and len(cmux.closed) == 2


def test_cycle_refuses_the_berth_screen_and_a_process_without_confirm(catalog):
    from berth.errors import BerthError
    from berth.ops import cycle_berth

    _insert(catalog, id="screen", title="Berth", kind="shell", state="live", cmux_workspace_id="ws-s")
    _insert(catalog, id="proc", title="dev", kind="process", state="live", cmux_workspace_id="ws-p")
    cmux = FakeCmux()
    cmux.workspaces["ws-p"] = Workspace(id="ws-p", title="dev", cwd="/tmp")
    for berth_id, code in (("screen", "PROTECTED"), ("proc", "KIND_UNSAFE")):
        with pytest.raises(BerthError) as caught:
            cycle_berth(catalog, cmux, berth_id, pid_alive_fn=lambda pid: False)
        assert caught.value.code == code
    assert cmux.closed == [] and catalog.require_berth("proc")["state"] == "live"
