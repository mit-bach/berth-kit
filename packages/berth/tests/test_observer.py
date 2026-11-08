"""The quit and restart model in docs/BERTH-STATE-MACHINE.md."""

from __future__ import annotations

from berth import machine
from berth.adopt import adopt_workspace
from berth.invariant import check
from berth.model import Workspace
from berth.observer import Observer
from tests.fakes import FakeCmux
from tests.test_state import _insert


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _ws(identifier: str, title: str) -> Workspace:
    return Workspace(id=identifier, title=title, cwd="/tmp", custom=True)


def _fleet(catalog, cmux):
    _insert(catalog, id="screen", title="Berth", kind="shell", state="live", cmux_workspace_id="ws-screen")
    _insert(catalog, id="a", title="alpha", kind="other-agent", agent_name="claude",
            agent_session_id="s-a", state="live", cmux_workspace_id="ws-a")
    _insert(catalog, id="b", title="beta", kind="grok", agent_name="grok",
            agent_session_id="s-b", state="live", cmux_workspace_id="ws-b")
    for identifier, title in (("ws-screen", "Berth"), ("ws-a", "alpha"), ("ws-b", "beta")):
        cmux.workspaces[identifier] = _ws(identifier, title)


def _states(catalog):
    return {row["id"]: (row["state"], row["park_reason"]) for row in catalog.all_berths()}


def test_a_cmux_that_does_not_answer_but_still_runs_parks_nothing(catalog):
    cmux = FakeCmux()
    _fleet(catalog, cmux)
    observer = Observer()
    observer.tick(catalog, cmux, list(cmux.workspaces.values()), {})
    observer.tick(catalog, cmux, None, {})
    observer.tick(catalog, cmux, list(cmux.workspaces.values()), {})
    assert {state for state, _ in _states(catalog).values()} == {"live"}


def test_a_quit_parks_every_session_and_closes_the_idle_restored_panes(catalog):
    cmux = FakeCmux()
    _fleet(catalog, cmux)
    clock = Clock()
    observer = Observer(clock=clock)
    observer.tick(catalog, cmux, list(cmux.workspaces.values()), {})

    # cmux quits: down on two ticks.
    cmux.app = None  # cmux quits: its process is gone
    observer.tick(catalog, cmux, None, {})
    assert _states(catalog)["a"] == ("live", None)
    observer.tick(catalog, cmux, None, {})
    assert _states(catalog) == {
        "screen": ("parked", "cmux_quit"),
        "a": ("parked", "cmux_quit"),
        "b": ("parked", "cmux_quit"),
    }
    assert catalog.require_berth("a")["last_workspace_id"] == "ws-a"

    # cmux relaunches and restores every pane under the same UUIDs.
    cmux.app = (200, "Tue Jan  2 00:00:00 2026")  # a new cmux process
    clock.now += 60
    restored = list(cmux.workspaces.values())
    observer.tick(catalog, cmux, restored, {})
    assert sorted(cmux.closed) == ["ws-a", "ws-b"]
    states = _states(catalog)
    assert states["screen"] == ("live", None)
    assert states["a"] == ("parked", "cmux_quit")
    assert states["b"] == ("parked", "cmux_quit")
    assert cmux.sent == [("ws-screen", "berth\n")]
    assert check(catalog, list(cmux.workspaces.values())) == []

    # The operator plays one back. It gets a new workspace and leaves the filter.
    from berth.state import play_berth

    from tests.support import write_claude_conversation

    write_claude_conversation("s-a", "/tmp")
    played = play_berth(catalog, cmux, "a", sessions_root=None)
    assert played["state"] == "live"
    assert played["park_reason"] is None


def test_rows_closed_just_before_the_quit_are_relabeled(catalog):
    cmux = FakeCmux()
    _fleet(catalog, cmux)
    observer = Observer()
    observer.tick(catalog, cmux, list(cmux.workspaces.values()), {})
    # cmux tears panes down before the socket goes away.
    del cmux.workspaces["ws-a"]
    observer.tick(catalog, cmux, list(cmux.workspaces.values()), {})
    observer.tick(catalog, cmux, list(cmux.workspaces.values()), {})
    assert _states(catalog)["a"] == ("parked", "closed")
    cmux.app = None  # cmux quits: its process is gone
    observer.tick(catalog, cmux, None, {})
    observer.tick(catalog, cmux, None, {})
    assert _states(catalog)["a"] == ("parked", "cmux_quit")


def test_a_falsely_parked_workspace_that_is_still_open_is_relinked(catalog):
    cmux = FakeCmux()
    _insert(catalog, id="a", title="alpha", state="live", cmux_workspace_id="ws-a")
    machine.transition(catalog, "a", "vanished", reason="closed")
    cmux.workspaces["ws-a"] = _ws("ws-a", "alpha")
    Observer().tick(catalog, cmux, list(cmux.workspaces.values()), {})
    row = catalog.require_berth("a")
    assert row["state"] == "live"
    assert row["cmux_workspace_id"] == "ws-a"
    assert cmux.closed == []


def test_an_unknown_workspace_is_adopted_on_the_second_tick(catalog):
    cmux = FakeCmux()
    cmux.workspaces["ws-new"] = _ws("ws-new", "fresh")
    observer = Observer()
    observer.tick(catalog, cmux, list(cmux.workspaces.values()), {})
    assert catalog.all_berths() == []
    observer.tick(catalog, cmux, list(cmux.workspaces.values()), {})
    rows = catalog.all_berths()
    assert len(rows) == 1 and rows[0]["state"] == "live" and rows[0]["cmux_workspace_id"] == "ws-new"


def test_adopt_does_not_turn_a_restored_pane_into_a_new_row(catalog):
    cmux = FakeCmux()
    _insert(catalog, id="a", title="alpha", state="live", cmux_workspace_id="ws-a")
    machine.transition(catalog, "a", "vanished", reason="cmux_quit")
    record = adopt_workspace(catalog, _ws("ws-a", "alpha"), hook=None, surfaces=[], groups=[], cmux=cmux)
    assert record["id"] == "a"
    assert len(catalog.all_berths()) == 1
    assert catalog.require_berth("a")["state"] == "parked"


def test_the_screen_is_started_when_cmux_did_not_restore_it(catalog):
    cmux = FakeCmux()
    _fleet(catalog, cmux)
    clock = Clock()
    observer = Observer(clock=clock)
    observer.tick(catalog, cmux, list(cmux.workspaces.values()), {})
    cmux.app = None  # cmux quits: its process is gone
    observer.tick(catalog, cmux, None, {})
    observer.tick(catalog, cmux, None, {})
    cmux.app = (200, "Tue Jan  2 00:00:00 2026")  # a new cmux process
    cmux.workspaces.clear()
    observer.tick(catalog, cmux, [], {})
    assert _states(catalog)["screen"] == ("parked", "cmux_quit")
    clock.now += 30
    observer.tick(catalog, cmux, [], {})
    screen = catalog.require_berth("screen")
    assert screen["state"] == "live"
    assert screen["cmux_workspace_id"] in cmux.workspaces
    assert _states(catalog)["a"] == ("parked", "cmux_quit")


def test_a_restart_faster_than_two_ticks_is_still_a_quit(catalog, tmp_path):
    cmux = FakeCmux()
    _fleet(catalog, cmux)
    sock = tmp_path / "cmux.sock"
    sock.write_text("")
    cmux.socket = sock
    observer = Observer(clock=Clock())
    observer.tick(catalog, cmux, list(cmux.workspaces.values()), {})
    assert {state for state, _ in _states(catalog).values()} == {"live"}
    # cmux quits and relaunches between two ticks: a new process, panes restored under the same ids.
    cmux.app = (200, "Tue Jan  2 00:00:00 2026")  # a new cmux process
    observer.tick(catalog, cmux, list(cmux.workspaces.values()), {})
    states = _states(catalog)
    assert states["a"] == ("parked", "cmux_quit") and states["b"] == ("parked", "cmux_quit")
    assert states["screen"] == ("live", None)
    assert sorted(cmux.closed) == ["ws-a", "ws-b"]


def _restorer(catalog, cmux, calls):
    from berth.restore import restore_rows

    def run(ids):
        calls.append(list(ids))
        played = []
        restore_rows(catalog, cmux, ids, play=lambda db, cx, berth_id, focus: played.append((berth_id, focus))
                     or machine.transition(db, berth_id, "play_claim", workspace_id=None)
                     and machine.transition(db, berth_id, "play_created", workspace_id=f"new-{berth_id}"))
        calls.append(played)
    return run


def test_a_restart_reopens_every_session_that_was_open_and_only_those(catalog, tmp_path):
    cmux = FakeCmux()
    _fleet(catalog, cmux)
    # Parked by an earlier restart and left parked on purpose: it stays parked.
    _insert(catalog, id="old", title="old", state="live", cmux_workspace_id="ws-old")
    machine.transition(catalog, "old", "vanished", reason="cmux_quit")
    clock = Clock()
    calls = []
    observer = Observer(clock=clock, restore=_restorer(catalog, cmux, calls), state_path=tmp_path / "observer.json")
    observer.tick(catalog, cmux, list(cmux.workspaces.values()), {})
    cmux.app = None  # cmux quits: its process is gone
    observer.tick(catalog, cmux, None, {})
    observer.tick(catalog, cmux, None, {})
    assert _states(catalog)["a"] == ("parked", "cmux_quit")
    cmux.app = (200, "Tue Jan  2 00:00:00 2026")  # a new cmux process
    clock.now += 5
    observer.tick(catalog, cmux, list(cmux.workspaces.values()), {})
    assert sorted(cmux.closed) == ["ws-a", "ws-b"]  # the idle panes cmux restored
    assert calls == []  # not yet: give cmux a moment to finish restoring
    clock.now += 10
    observer.tick(catalog, cmux, list(cmux.workspaces.values()), {})
    assert sorted(calls[0]) == ["a", "b"]
    assert sorted(calls[1]) == [("a", False), ("b", False)]  # nothing steals focus
    states = _states(catalog)
    assert states["a"][0] == "live" and states["b"][0] == "live" and states["screen"] == ("live", None)
    assert states["old"] == ("parked", "cmux_quit")


def test_a_watcher_restart_with_the_same_cmux_reopens_nothing(catalog, tmp_path):
    cmux = FakeCmux()
    sock = tmp_path / "cmux.sock"
    sock.write_text("")
    cmux.socket = sock
    _insert(catalog, id="old", title="old", state="live", cmux_workspace_id="ws-old")
    machine.transition(catalog, "old", "vanished", reason="cmux_quit")
    calls = []
    state = tmp_path / "observer.json"
    Observer(restore=calls.append, state_path=state).tick(catalog, cmux, [], {})
    second = Observer(clock=Clock(), restore=calls.append, state_path=state)
    for _ in range(3):
        second.clock.now += 10
        second.tick(catalog, cmux, [], {})
    assert calls == []
    assert _states(catalog)["old"] == ("parked", "cmux_quit")


def test_a_restart_the_watcher_missed_is_found_at_its_next_start(catalog, tmp_path):
    cmux = FakeCmux()
    _fleet(catalog, cmux)
    sock = tmp_path / "cmux.sock"
    sock.write_text("")
    cmux.socket = sock
    state = tmp_path / "observer.json"
    Observer(state_path=state).tick(catalog, cmux, list(cmux.workspaces.values()), {})
    # The watcher is down while cmux restarts (or the Mac reboots).
    cmux.app = (200, "Tue Jan  2 00:00:00 2026")  # a new cmux process
    calls = []
    clock = Clock()
    observer = Observer(clock=clock, restore=calls.append, state_path=state)
    observer.tick(catalog, cmux, list(cmux.workspaces.values()), {})
    assert sorted(cmux.closed) == ["ws-a", "ws-b"]
    clock.now += 10
    observer.tick(catalog, cmux, list(cmux.workspaces.values()), {})
    assert sorted(calls[0]) == ["a", "b"]


def test_restore_rows_skips_processes_and_keeps_failures_parked(catalog):
    from berth.errors import BerthError
    from berth.restore import last_quit, restore_rows

    for berth_id, kind in (("ok", "shell"), ("proc", "process"), ("bad", "shell")):
        _insert(catalog, id=berth_id, title=berth_id, kind=kind, state="live", cmux_workspace_id=f"ws-{berth_id}")
        machine.transition(catalog, berth_id, "vanished", reason="cmux_quit")

    def play(db, cmux, berth_id, focus):
        if berth_id == "bad":
            raise BerthError("SESSION_MISSING", "gone", {})
        machine.transition(db, berth_id, "play_claim", workspace_id=None)
        machine.transition(db, berth_id, "play_created", workspace_id="new")

    results = {item["berth_id"]: item["result"] for item in restore_rows(catalog, FakeCmux(), last_quit(catalog), play=play)}
    assert results == {"ok": "restored", "proc": "skipped", "bad": "failed"}
    assert _states(catalog)["bad"] == ("parked", "cmux_quit")
    assert [row["kind"] for row in catalog.events_for("bad")][-1] == "restore_failed"


def test_closing_the_lid_parks_nothing_and_closes_nothing(catalog, tmp_path):
    """2026-10-22: every wake from sleep closed every pane. Never again."""
    cmux = FakeCmux()
    _fleet(catalog, cmux)
    sock = tmp_path / "cmux.sock"
    sock.write_text("")
    cmux.socket = sock
    calls = []
    clock = Clock()
    observer = Observer(clock=clock, restore=calls.append, state_path=tmp_path / "observer.json")
    observer.tick(catalog, cmux, list(cmux.workspaces.values()), {})
    # Asleep: cmux does not answer for a long time, but it is the same process.
    for _ in range(30):
        clock.now += 2
        observer.tick(catalog, cmux, None, {})
    # Awake: cmux recreated its socket file without restarting.
    sock.unlink()
    sock.write_text("")
    for _ in range(10):
        clock.now += 2
        observer.tick(catalog, cmux, list(cmux.workspaces.values()), {})
    assert {state for state, _ in _states(catalog).values()} == {"live"}
    assert cmux.closed == []
    assert calls == []


def test_a_real_restart_never_closes_a_pane_that_runs_an_agent(catalog):
    cmux = FakeCmux()
    _fleet(catalog, cmux)
    cmux.running = frozenset({"ws-a"})  # Claude is still running in ws-a
    calls = []
    clock = Clock()
    observer = Observer(clock=clock, restore=calls.append)
    observer.tick(catalog, cmux, list(cmux.workspaces.values()), {})
    cmux.app = (300, "Wed Jan  3 00:00:00 2026")
    observer.tick(catalog, cmux, list(cmux.workspaces.values()), {})
    assert cmux.closed == ["ws-b"]  # only the idle restored pane
    row = catalog.require_berth("a")
    assert row["state"] == "live" and row["cmux_workspace_id"] == "ws-a"
    clock.now += 10
    observer.tick(catalog, cmux, list(cmux.workspaces.values()), {})
    assert calls == [["b"]]  # the kept pane is not reopened a second time


def test_a_first_start_that_cannot_reach_cmux_parks_nothing(catalog, tmp_path):
    cmux = FakeCmux()
    _fleet(catalog, cmux)
    observer = Observer(state_path=tmp_path / "observer.json")
    for _ in range(5):
        observer.tick(catalog, cmux, None, {})
    assert {state for state, _ in _states(catalog).values()} == {"live"}


def test_a_list_taken_just_before_a_pause_does_not_relink_the_paused_row(catalog):
    """2026-10-23: park_folder closed a pane between the watcher's list and its tick."""
    from berth import machine

    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(id="ws-1", title="a", cwd="/tmp", ref="workspace:1")
    _insert(catalog, id="a", title="a", state="live", cmux_workspace_id="ws-1")
    stale = list(cmux.workspaces.values())
    machine.transition(catalog, "a", "pause_flush", log_kind="pause")
    machine.transition(catalog, "a", "pause_closed", reason="pause", log_kind="pause")
    del cmux.workspaces["ws-1"]
    observer = Observer()
    observer.tick(catalog, cmux, stale, {})
    assert (catalog.require_berth("a")["state"], catalog.require_berth("a")["park_reason"]) == ("parked", "pause")
    observer.tick(catalog, cmux, list(cmux.workspaces.values()), {})
    assert catalog.require_berth("a")["park_reason"] == "pause"
