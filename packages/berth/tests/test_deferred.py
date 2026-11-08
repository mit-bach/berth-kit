"""A session can have itself cycled, and anyone can ask for a close "when it is idle" or force one."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from berth.activity import Activity, assess
from berth.deferred import pending, run_due
from berth.mcp import Api, ToolError
from berth.model import Workspace
from tests.fakes import FakeCmux
from tests.test_state import _insert

BUSY = Activity(busy=True, reasons=["a turn is running"])
IDLE = Activity()


@pytest.fixture(autouse=True)
def sessions_on_disk(monkeypatch):
    monkeypatch.setattr("berth.state.session_on_disk", lambda root, session: True)


def _live(catalog, cmux, berth_id):
    _insert(catalog, id=berth_id, title=berth_id.title(), kind="grok", agent_name="grok", agent_session_id=f"s-{berth_id}",
            state="live", cmux_workspace_id=f"ws-{berth_id}", argv=["grok", "-r", f"s-{berth_id}"], display="grok")
    cmux.workspaces[f"ws-{berth_id}"] = Workspace(id=f"ws-{berth_id}", title=berth_id, cwd="/tmp")


def _later(minutes: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


def _events(catalog, berth_id):
    return [event["kind"] for event in catalog.events_for(berth_id)]


def test_a_session_cycling_itself_is_queued_and_the_watcher_cycles_it_once_its_turn_ends(catalog):
    """2026-10-29: Settings Agent asked to be cycled to load a new MCP server; Berth refused (PROTECTED)."""
    cmux = FakeCmux()
    _live(catalog, cmux, "config")
    api = Api(catalog.path, cmux=cmux, caller_workspace="ws-config")
    result = api("control", {"action": "cycle", "sessions": "me"})
    assert result["done"] == [] and "failed" not in result
    [queued] = result["queued"]
    assert queued["when"] == "idle" and "as soon as this turn ends" in queued["note"]
    assert api("session", {"session": "me"})["queued"]["action"] == "cycle"
    assert run_due(catalog, cmux, now=_later(0.1), observe_fn=lambda row: BUSY) == []  # its turn is still running
    assert cmux.closed == []
    outcome = run_due(catalog, cmux, now=_later(0.2), observe_fn=lambda row: IDLE)
    assert outcome == [{"id": "config", "result": "done", "forced": False}]
    assert cmux.closed == ["ws-config"] and catalog.require_berth("config")["state"] == "live"  # reopened
    assert pending(catalog) == [] and "deferred_done" in _events(catalog, "config")


def test_closing_yourself_now_needs_force_and_then_waits_only_for_the_call_to_return(catalog):
    cmux = FakeCmux()
    _live(catalog, cmux, "config")
    api = Api(catalog.path, cmux=cmux, caller_workspace="ws-config")
    refused = api("control", {"action": "cycle", "sessions": "me", "when": "now"})
    assert refused["failed"][0]["code"] == "BUSY" and pending(catalog) == []
    forced = api("control", {"action": "cycle", "sessions": "me", "when": "now", "force": True})
    assert forced["queued"][0]["force"] is True and "cutting this turn off" in forced["queued"][0]["note"]
    assert run_due(catalog, cmux, now=datetime.now(timezone.utc), observe_fn=lambda row: BUSY) == []  # the grace
    assert run_due(catalog, cmux, now=_later(0.1), observe_fn=lambda row: BUSY)[0]["result"] == "done"
    assert cmux.closed == ["ws-config"]


def test_a_named_busy_session_is_refused_with_a_hint_and_force_closes_it(catalog):
    cmux = FakeCmux()
    _live(catalog, cmux, "orch")
    _live(catalog, cmux, "worker")
    cmux.screens["ws-worker"] = "─" * 60 + " W ─\n❯ \n" + "─" * 70 + "\n  ⏵⏵ auto mode on · esc to interrupt\n"
    api = Api(catalog.path, cmux=cmux, caller_workspace="ws-orch")
    refused = api("control", {"action": "cycle", "sessions": "worker"})
    assert refused["failed"][0]["code"] == "BUSY" and "when idle" in refused["failed"][0]["hint"]
    forced = api("control", {"action": "cycle", "sessions": "worker", "force": True})
    assert forced["done"][0]["result"] == "cycled" and cmux.closed == ["ws-worker"]


def test_when_idle_gives_up_by_the_deadline_unless_forced(catalog):
    cmux = FakeCmux()
    for berth_id in ("orch", "slow", "stuck"):
        _live(catalog, cmux, berth_id)
    api = Api(catalog.path, cmux=cmux, caller_workspace="ws-orch")
    api("control", {"action": "park", "sessions": "slow", "when": "idle", "wait": 5})
    api("control", {"action": "sleep", "sessions": "stuck", "when": "idle", "wait": 5, "force": True})
    assert run_due(catalog, cmux, now=_later(3), observe_fn=lambda row: BUSY) == []
    outcomes = {item["id"]: item for item in run_due(catalog, cmux, now=_later(6), observe_fn=lambda row: BUSY)}
    assert outcomes["slow"]["result"] == "gave up" and outcomes["slow"]["why"] == ["a turn is running"]
    assert outcomes["stuck"] == {"id": "stuck", "result": "done", "forced": True}
    assert catalog.require_berth("slow")["state"] == "live" and catalog.require_berth("stuck")["park_reason"] == "sleep"
    assert "deferred_gave_up" in _events(catalog, "slow")


def test_a_scope_when_idle_queues_busy_sessions_too_but_never_you(catalog):
    cmux = FakeCmux()
    for berth_id in ("orch", "a", "b"):
        _live(catalog, cmux, berth_id)
    cmux.screens["ws-b"] = "─" * 60 + " B ─\n❯ \n" + "─" * 70 + "\n  ⏵⏵ auto mode on · 2 shells\n"
    api = Api(catalog.path, cmux=cmux, caller_workspace="ws-orch")
    plan = api("control", {"action": "cycle", "scope": {"folder": "/"}, "when": "idle"})
    assert {row["id"] for row in plan["would"]} == {"a", "b"} and plan["when"].startswith("idle")
    done = api("control", {"action": "cycle", "scope": {"folder": "/"}, "when": "idle", "confirm": True})
    assert {item["id"] for item in done["queued"]} == {"a", "b"} and cmux.closed == []
    assert {item["berth_id"] for item in pending(catalog)} == {"a", "b"}


def test_a_queued_close_is_dropped_when_the_session_already_closed(catalog):
    cmux = FakeCmux()
    _live(catalog, cmux, "orch")
    _live(catalog, cmux, "gone")
    api = Api(catalog.path, cmux=cmux, caller_workspace="ws-orch")
    api("control", {"action": "cycle", "sessions": "gone", "when": "idle"})
    api("control", {"action": "park", "sessions": "gone", "force": True})
    assert run_due(catalog, cmux, now=_later(0.1), observe_fn=lambda row: IDLE) == [{"id": "gone", "result": "dropped"}]


def test_the_command_line_queues_a_cycle_of_its_own_pane(catalog, monkeypatch, capsys):
    import json

    from berth.cli import main

    cmux = FakeCmux()
    _live(catalog, cmux, "config")
    monkeypatch.setenv("CMUX_WORKSPACE_ID", "ws-config")
    assert main(["--catalog", str(catalog.path), "--json", "cycle", "config"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["queued"] is True and out["when"] == "idle" and pending(catalog)[0]["berth_id"] == "config"


def test_claudes_grey_suggestion_is_not_a_draft():
    rule = "─" * 120
    screen = f"x\n{rule[:-12]} Config ─\n❯\xa0Try \"create a util logging.py that...\"\n{rule}\n  ⏵⏵ bypass permissions on\n"
    activity = assess(screen, "other-agent", "claude")
    assert activity.draft is None and activity.busy is False


def test_a_queued_close_can_be_taken_back(catalog):
    cmux = FakeCmux()
    _live(catalog, cmux, "orch")
    _live(catalog, cmux, "worker")
    api = Api(catalog.path, cmux=cmux, caller_workspace="ws-orch")
    api("control", {"action": "cycle", "sessions": "worker", "when": "idle"})
    done = api("control", {"action": "cancel", "sessions": ["worker", "orch"]})
    assert done["cancelled"] == [{"id": "worker", "title": "Worker", "action": "cycle"}]
    assert done["nothing_queued"] == [{"id": "orch", "title": "Orch"}]
    assert pending(catalog) == [] and "deferred_cancelled" in _events(catalog, "worker")
    assert run_due(catalog, cmux, now=_later(1), observe_fn=lambda row: IDLE) == [] and cmux.closed == []


def test_many_idle_cycles_queued_at_once_start_two_per_tick_in_the_order_queued(catalog):
    """2026-10-31: an account switch may queue a cycle of every live Claude (34) at once."""
    from berth.deferred import CYCLES_PER_TICK, queue

    cmux = FakeCmux()
    names = [f"s{index}" for index in range(5)]
    for name in names:
        _live(catalog, cmux, name)
        queue(catalog, name, "cycle", idle=True)
    first = run_due(catalog, cmux, now=_later(0.1), observe_fn=lambda row: IDLE)
    assert CYCLES_PER_TICK == 2 and [item["id"] for item in first] == ["s0", "s1"]
    second = run_due(catalog, cmux, now=_later(0.2), observe_fn=lambda row: IDLE)
    third = run_due(catalog, cmux, now=_later(0.3), observe_fn=lambda row: IDLE)
    assert [item["id"] for item in second + third] == ["s2", "s3", "s4"] and pending(catalog) == []


def test_a_cycle_cut_off_by_a_watcher_restart_heals_and_runs_again(catalog, monkeypatch):
    """2026-11-05: the watcher restarted between a cycle's flush and its close. UNI Notes Sync stayed "pausing" on
    its open pane, the new watcher dropped the queued cycle, and every wake and cycle then answered IN_PROGRESS."""
    from berth import observer as observer_mod
    from berth.observer import Observer

    cmux = FakeCmux()
    _live(catalog, cmux, "sync")
    api = Api(catalog.path, cmux=cmux, caller_workspace=None)
    api("control", {"action": "cycle", "sessions": "sync", "when": "idle"})
    with catalog.immediate():  # the old watcher flushed it, then died before the close
        catalog.conn.execute("UPDATE berth SET state = 'pausing' WHERE id = 'sync'")
    assert run_due(catalog, cmux, now=_later(0.1), observe_fn=lambda row: IDLE) == []  # waits; not dropped
    assert [item["action"] for item in pending(catalog)] == ["cycle"]
    watcher = Observer()
    watcher.tick(catalog, cmux, list(cmux.workspaces.values()), {})
    assert catalog.require_berth("sync")["state"] == "pausing"  # a pause gets its time to close the pane
    monkeypatch.setattr(observer_mod, "STALE_PAUSE_SECONDS", 0)
    actions = watcher.tick(catalog, cmux, list(cmux.workspaces.values()), {})
    assert {"berth_id": "sync", "action": "live", "why": "its pause never closed the pane"} in actions
    assert catalog.require_berth("sync")["state"] == "live"
    outcome = run_due(catalog, cmux, now=_later(0.2), observe_fn=lambda row: IDLE)
    assert outcome == [{"id": "sync", "result": "done", "forced": False}]
    assert cmux.closed == ["ws-sync"] and catalog.require_berth("sync")["state"] == "live"  # cycled after all


def test_a_cycle_still_waiting_on_a_change_past_its_deadline_is_dropped(catalog):
    cmux = FakeCmux()
    _live(catalog, cmux, "slow")
    Api(catalog.path, cmux=cmux, caller_workspace=None)("control", {"action": "cycle", "sessions": "slow", "when": "idle"})
    with catalog.immediate():
        catalog.conn.execute("UPDATE berth SET state = 'playing' WHERE id = 'slow'")
    assert run_due(catalog, cmux, now=_later(24 * 60 + 1), observe_fn=lambda row: IDLE) == [
        {"id": "slow", "result": "dropped"}]


def test_a_cycle_whose_watcher_stopped_after_the_close_reopens_the_session(catalog):
    """The other half of the same cut-off: the pane closed, the reopen never ran. The cycle finishes, not drops."""
    from berth.state import pause_berth

    cmux = FakeCmux()
    _live(catalog, cmux, "sync")
    Api(catalog.path, cmux=cmux, caller_workspace=None)("control", {"action": "cycle", "sessions": "sync", "when": "idle"})
    [item] = pending(catalog)
    with catalog.immediate():  # the old watcher began it, closed the pane, and stopped
        catalog.insert_event("sync", "deferred_started", {"action": "cycle", "item": item["id"]})
    pause_berth(catalog, cmux, "sync", force=True, confirm=True, hook=None, caller_workspace_id=None,
                pid_alive_fn=lambda pid: False)
    assert catalog.require_berth("sync")["state"] == "parked"
    outcome = run_due(catalog, cmux, now=_later(0.1), observe_fn=lambda row: IDLE)
    assert outcome == [{"id": "sync", "result": "done", "resumed": True}]
    assert catalog.require_berth("sync")["state"] == "live" and pending(catalog) == []
