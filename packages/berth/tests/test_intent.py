"""Intent drain. The fake cmux never opens the live socket."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from berth.app import App
from berth.catalog import insert_intent
from berth.cli import main
from berth.drain import drain_once, pending_rows
from berth.figures import refresh_figures
from berth.model import HookRecord, Workspace
from berth.state import pause_berth
from berth.timeutil import utc_now
from tests.fakes import FakeCmux
from tests.support import OPENED, frozen_clock


def _insert(db, **overrides) -> str:
    now = utc_now()
    fields = {
        "id": overrides.get("id", "berth-1"),
        "folder_id": overrides.get("folder_id", db.ensure_inbox()),
        "title": overrides.get("title", "cedar"),
        "kind": overrides.get("kind", "shell"),
        "state": overrides.get("state", "live"),
        "protect": overrides.get("protect", 0),
        "cwd": overrides.get("cwd", "/tmp"),
        "note": overrides.get("note", ""),
        "recipe_argv": json.dumps(overrides.get("argv", ["sleep", "3600"])),
        "recipe_display": overrides.get("display", "sleep 3600"),
        "agent_name": overrides.get("agent_name"),
        "agent_session_id": overrides.get("agent_session_id"),
        "agent_version": overrides.get("agent_version"),
        "cmux_workspace_id": overrides.get("cmux_workspace_id", "ws-1"),
        "cmux_surface_id": None,
        "cmux_pid": None,
        "attachments": "{}",
        "excerpt": "",
        "excerpt_at": None,
        "created_at": now,
        "updated_at": now,
        "parked_at": overrides.get("parked_at"),
        "played_at": overrides.get("played_at"),
    }
    with db.immediate():
        db.insert_berth(fields)
    return fields["id"]


def _live(cmux, workspace_id="ws-1", title="cedar") -> None:
    cmux.workspaces[workspace_id] = Workspace(
        id=workspace_id, title=title, cwd="/tmp", ref="workspace:1"
    )
    cmux.screens[workspace_id] = "quiet shell\n"


def _hook(**overrides) -> HookRecord:
    values = dict(
        agent_name="grok",
        session_id="sess-1",
        workspace_id="ws-1",
        surface_id="surface-1",
        pid=4242,
        cwd="/tmp",
        runtime_status="running",
        last_body="tool call in progress",
        executable="/usr/local/bin/grok",
        arguments=["grok", "--always-approve"],
        launcher="grok",
        updated_at=None,
    )
    values.update(overrides)
    return HookRecord(**values)


def _intent(path, op: str, berth_id: str, detail: dict) -> None:
    insert_intent(path, op, berth_id, detail, datetime(2026, 10, 2, 22, 30, tzinfo=timezone.utc))


def _count(db, table: str) -> int:
    return int(db.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def _kinds(db, berth_id: str) -> list[str]:
    return [row["kind"] for row in db.events_for(berth_id)]


def _drain(db, cmux, **kwargs):
    kwargs.setdefault("pid_alive_fn", lambda pid: False)
    return drain_once(db, cmux, **kwargs)


def test_quiet_pause_intent_parks_and_a_second_drain_does_nothing(catalog):
    berth_id = _insert(catalog)
    cmux = FakeCmux()
    _live(cmux)
    _intent(
        catalog.path,
        "pause",
        berth_id,
        {"kind": "shell", "state": "live", "title": "cedar", "busy": False, "confirm": False},
    )
    first = _drain(catalog, cmux)
    assert first == [{"id": first[0]["id"], "op": "pause", "result": "done"}]
    assert catalog.require_berth(berth_id)["state"] == "parked"
    assert catalog.require_berth(berth_id)["cmux_workspace_id"] is None
    assert cmux.closed == ["ws-1"]
    assert _count(catalog, "intent") == 0

    again = _drain(catalog, cmux)
    assert again == []
    assert cmux.closed == ["ws-1"]
    assert catalog.require_berth(berth_id)["state"] == "parked"
    assert "intent_failed" not in _kinds(catalog, berth_id)


def test_play_intent_on_a_parked_row_ends_live(catalog):
    berth_id = _insert(catalog, state="parked", cmux_workspace_id=None, parked_at=utc_now())
    cmux = FakeCmux()
    _intent(
        catalog.path,
        "play",
        berth_id,
        {"kind": "shell", "state": "parked", "title": "cedar"},
    )
    outcome = _drain(catalog, cmux)
    assert outcome[0]["result"] == "done"
    assert catalog.require_berth(berth_id)["state"] == "live"
    assert catalog.require_berth(berth_id)["cmux_workspace_id"]
    assert len(cmux.created) == 1
    assert cmux.created[0]["title"] == "cedar"
    assert _count(catalog, "intent") == 0


BUSY_SCREEN = "✶ Working… (12s)\n" + "─" * 90 + " cedar ─\n❯ \n" + "─" * 100 + "\n  ⏵⏵ auto mode on · esc to interrupt · ← 1 agent\n"


def test_a_pause_of_a_busy_pane_is_refused_with_the_reason(catalog):
    berth_id = _insert(catalog, kind="grok", agent_name="grok", agent_session_id="sess-1")
    cmux = FakeCmux()
    _live(cmux)
    cmux.screens["ws-1"] = BUSY_SCREEN
    _intent(catalog.path, "pause", berth_id, {"kind": "grok", "state": "live", "title": "cedar", "busy": False, "confirm": False})
    outcome = _drain(catalog, cmux, hooks={}, pid_alive_fn=lambda pid: True)
    assert outcome[0]["result"] == "failed" and outcome[0]["code"] == "BUSY"
    assert cmux.closed == [] and catalog.require_berth(berth_id)["state"] == "live"
    assert _count(catalog, "intent") == 0  # not left to close by itself later
    failed = [json.loads(row["detail"]) for row in catalog.conn.execute(
        "SELECT detail FROM event WHERE kind = 'intent_failed' AND berth_id = ?", (berth_id,))]
    assert "a turn is running" in failed[-1]["message"]


def test_confirmed_pause_passes_force_and_closes(catalog):
    berth_id = _insert(catalog, kind="grok", agent_name="grok", agent_session_id="sess-1")
    cmux = FakeCmux()
    _live(cmux)
    _intent(
        catalog.path,
        "pause",
        berth_id,
        {"kind": "grok", "state": "live", "title": "cedar", "busy": True, "confirm": True},
    )
    outcome = _drain(catalog, cmux, hooks={"ws-1": _hook()}, pid_alive_fn=lambda pid: True)
    assert outcome[0]["result"] == "done"
    assert cmux.closed == ["ws-1"]
    assert catalog.require_berth(berth_id)["state"] == "parked"
    assert _count(catalog, "intent") == 0


def test_one_pass_does_not_run_the_same_intent_twice(catalog, monkeypatch):
    berth_id = _insert(catalog)
    cmux = FakeCmux()
    _live(cmux)
    _intent(
        catalog.path,
        "pause",
        berth_id,
        {"kind": "shell", "state": "live", "title": "cedar", "busy": False, "confirm": False},
    )
    fetched = pending_rows(catalog)
    assert len(fetched) == 1
    monkeypatch.setattr("berth.drain.pending_rows", lambda db: [*fetched, *fetched])
    calls: list[str] = []
    real = pause_berth

    def wrapped(*args, **kwargs):
        calls.append(args[2])
        return real(*args, **kwargs)

    monkeypatch.setattr("berth.drain.pause_berth", wrapped)
    _drain(catalog, cmux)
    assert calls == [berth_id]
    assert cmux.closed == ["ws-1"]


def test_other_failure_writes_intent_failed_and_leaves_the_row(catalog):
    berth_id = _insert(catalog, state="playing")
    cmux = FakeCmux()
    _live(cmux)
    _intent(
        catalog.path,
        "pause",
        berth_id,
        {"kind": "shell", "state": "live", "title": "cedar", "busy": False, "confirm": False},
    )
    outcome = _drain(catalog, cmux)
    assert outcome[0]["result"] == "failed"
    assert outcome[0]["code"] == "IN_PROGRESS"
    assert cmux.closed == []
    assert catalog.require_berth(berth_id)["state"] == "playing"
    assert _count(catalog, "intent") == 1
    assert _kinds(catalog, berth_id).count("intent_failed") == 1

    _drain(catalog, cmux)
    assert cmux.closed == []
    assert _count(catalog, "intent") == 1
    assert _kinds(catalog, berth_id).count("intent_failed") == 1


def test_focus_intent_selects_and_drops_the_row(catalog):
    berth_id = _insert(catalog)
    cmux = FakeCmux()
    _live(cmux)
    _intent(catalog.path, "focus", berth_id, {"kind": "shell", "state": "live", "title": "cedar"})
    outcome = _drain(catalog, cmux)
    assert outcome[0]["result"] == "done"
    assert cmux.selected == ["ws-1"]
    assert cmux.closed == []
    assert catalog.require_berth(berth_id)["state"] == "live"
    assert _count(catalog, "intent") == 0


def test_process_play_confirm_runs_the_command(catalog):
    berth_id = _insert(
        catalog,
        kind="process",
        state="parked",
        cmux_workspace_id=None,
        argv=["python", "-m", "http.server"],
        display="python -m http.server",
        parked_at=utc_now(),
    )
    cmux = FakeCmux()
    _intent(
        catalog.path,
        "play",
        berth_id,
        {"kind": "process", "state": "parked", "title": "cedar", "confirm": True},
    )
    outcome = _drain(catalog, cmux)
    assert outcome[0]["result"] == "done"
    assert catalog.require_berth(berth_id)["state"] == "live"
    assert cmux.created[0]["command"] == "python -m http.server"
    assert _count(catalog, "intent") == 0


def test_the_watcher_marks_busy_from_the_pane_and_p_asks_first(catalog):
    from berth.activity import refresh

    berth_id = _insert(catalog, kind="grok", agent_name="grok", agent_session_id="sess-1")
    cmux = FakeCmux()
    _live(cmux)
    cmux.screens["ws-1"] = BUSY_SCREEN
    refresh_figures(catalog, rss_bytes=7100000000, watcher="up", hooks={}, pid_alive_fn=lambda pid: True)
    status = catalog.conn.execute("SELECT child_rss_bytes, watcher FROM status WHERE id = 1").fetchone()
    assert (status["child_rss_bytes"], status["watcher"]) == (7100000000, "up")
    assert refresh(catalog, cmux, list(cmux.workspaces.values())) == 1
    runtime = catalog.conn.execute("SELECT busy, why FROM runtime WHERE berth_id = ?", (berth_id,)).fetchone()
    assert runtime["busy"] == 1 and runtime["why"] == "a turn is running"
    assert cmux.closed == [] and catalog.require_berth(berth_id)["state"] == "live"

    app = App(catalog.path, width=96, height=18, opened_at=OPENED, clock=frozen_clock)
    app.focus_berth(berth_id)
    assert app.current.busy is True
    app.handle("p")
    assert app.mode == "confirm-busy" and app.snap.intents == []  # it says why and waits for y
    app.handle("y")
    assert app.mode == "tree"
    assert app.snap.intents[0].op == "pause"
    assert app.snap.intents[0].detail["confirm"] is True
    assert app.snap.intents[0].detail["busy"] is True
    assert catalog.require_berth(berth_id)["state"] == "live"


def test_delete_intent_closes_a_live_row_once(catalog, tmp_path):
    session = "01a0dddd-eeee-7fff-8000-222222222222"
    root = tmp_path / "sessions" / session
    root.mkdir(parents=True)
    (root / "marker").write_text("stay")
    berth_id = _insert(
        catalog,
        id="live-intent",
        title="temporary",
        state="live",
        kind="grok",
        agent_name="grok",
        agent_session_id=session,
        cmux_workspace_id="ws-del",
        argv=["grok", "-r", session],
        display=f"grok -r {session}",
    )
    cmux = FakeCmux()
    _live(cmux, "ws-del", "temporary")
    _intent(
        catalog.path,
        "delete",
        berth_id,
        {"kind": "grok", "state": "live", "title": "temporary"},
    )
    outcome = _drain(catalog, cmux)
    assert outcome == [{"id": outcome[0]["id"], "op": "delete", "result": "done"}]
    assert catalog.get_berth(berth_id) is None
    assert cmux.closed == ["ws-del"]
    assert (root / "marker").read_text() == "stay"
    assert _count(catalog, "intent") == 0
    assert cmux.grouped == []

    again = _drain(catalog, cmux)
    assert again == []
    assert cmux.closed == ["ws-del"]


def test_delete_intent_refuses_kind_berth(catalog):
    berth_id = _insert(
        catalog,
        id="screen-row",
        title="Berth",
        kind="berth",
        state="live",
        cmux_workspace_id="ws-screen",
        argv=["berth"],
        display="berth",
    )
    cmux = FakeCmux()
    _live(cmux, "ws-screen", "Berth")
    _intent(
        catalog.path,
        "delete",
        berth_id,
        {"kind": "berth", "state": "live", "title": "Berth"},
    )
    outcome = _drain(catalog, cmux)
    assert outcome[0]["result"] == "failed"
    assert outcome[0]["code"] == "PROTECTED"
    row = catalog.require_berth(berth_id)
    assert row["kind"] == "berth"
    assert row["state"] == "live"
    assert row["cmux_workspace_id"] == "ws-screen"
    assert cmux.closed == []
    # PROTECTED can never succeed, so the intent is dropped, not retried.
    assert _count(catalog, "intent") == 0

    _drain(catalog, cmux)
    assert cmux.closed == []
    assert catalog.require_berth(berth_id)["kind"] == "berth"


def test_a_play_that_can_never_work_is_tried_once_not_every_tick(catalog):
    """2026-10-28: a play of a session whose transcript was gone ran twice a second for minutes."""
    berth_id = _insert(catalog, state="parked", kind="other-agent", agent_name="claude",
                       agent_session_id="never-written", cmux_workspace_id=None,
                       argv=["claude", "-r", "never-written"], display="claude -r never-written")
    cmux = FakeCmux()
    _intent(catalog.path, "play", berth_id, {"kind": "other-agent", "state": "parked", "title": "cedar"})
    for _ in range(3):
        _drain(catalog, cmux)
    kinds = _kinds(catalog, berth_id)
    assert kinds.count("intent_failed") == 1
    assert kinds.count("play_claim") == 1
    assert _count(catalog, "intent") == 0
    assert cmux.created == []


def test_delete_intent_on_a_parked_row_removes_the_catalog_row(catalog):
    berth_id = _insert(
        catalog,
        id="parked-intent",
        title="old shell",
        state="parked",
        kind="shell",
        cmux_workspace_id=None,
        parked_at=utc_now(),
    )
    cmux = FakeCmux()
    _intent(
        catalog.path,
        "delete",
        berth_id,
        {"kind": "shell", "state": "parked", "title": "old shell"},
    )
    outcome = _drain(catalog, cmux)
    assert outcome[0]["result"] == "done"
    assert catalog.get_berth(berth_id) is None
    assert cmux.closed == []
    assert _count(catalog, "intent") == 0
    assert _drain(catalog, cmux) == []


def test_dump_does_not_run_an_intent(desk, capsys):
    _intent(
        desk,
        "pause",
        "berth-sample",
        {"kind": "grok", "state": "live", "title": "sample-split", "busy": False, "confirm": False},
    )
    code = main(["--db", str(desk), "--dump", "--width", "96", "--height", "18"])
    assert code == 0
    assert "sample-split" in capsys.readouterr().out
    conn = sqlite3.connect(desk)
    state = conn.execute("SELECT state FROM berth WHERE id = 'berth-sample'").fetchone()[0]
    intents = conn.execute("SELECT count(*) FROM intent").fetchone()[0]
    failed = conn.execute("SELECT count(*) FROM event WHERE kind = 'intent_failed'").fetchone()[0]
    conn.close()
    assert state == "live"
    assert intents == 1
    assert failed == 0


def test_no_command_opens_the_screen_path(tmp_path, capsys):
    missing = tmp_path / "missing.sqlite"
    code = main(["--catalog", str(missing)])
    err = capsys.readouterr().err
    assert code == 1
    assert "no catalog" in err
    assert "separate" not in err


def test_screen_subcommand_needs_a_terminal(desk, capsys):
    code = main(["screen", "--db", str(desk)])
    assert code == 1
    assert "terminal" in capsys.readouterr().err


def test_a_play_intent_can_open_behind_the_current_workspace(catalog):
    first = _insert(catalog, id="first", state="parked", cmux_workspace_id=None, parked_at=utc_now())
    second = _insert(catalog, id="second", title="behind", state="parked", cmux_workspace_id=None, parked_at=utc_now())
    cmux = FakeCmux()
    _intent(catalog.path, "play", first, {"kind": "shell", "state": "parked", "title": "cedar", "focus": True})
    _intent(catalog.path, "play", second, {"kind": "shell", "state": "parked", "title": "behind", "focus": False})
    _drain(catalog, cmux)
    assert {item["title"]: item["focus"] for item in cmux.created} == {"cedar": True, "behind": False}
