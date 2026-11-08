"""Sleeping: closed to come back soon. S, Z, and W in the screen; sleep and wake in the engine."""

from __future__ import annotations

import sqlite3

from berth.drain import drain_once
from berth.model import Workspace, is_sleeping
from berth.ops import sleep_berth, sleeping_ids, wake_all
from berth.state import pause_berth
from tests.fakes import FakeCmux
from tests.support import make_app
from tests.test_state import _insert


def _live(catalog, cmux, berth_id, title="desk", **fields):
    _insert(catalog, id=berth_id, title=title, state="live", cmux_workspace_id=f"ws-{berth_id}",
            kind="grok", agent_name="grok", agent_session_id=f"s-{berth_id}", argv=["grok", "-r", f"s-{berth_id}"],
            display=f"grok -r s-{berth_id}", **fields)
    cmux.workspaces[f"ws-{berth_id}"] = Workspace(id=f"ws-{berth_id}", title=title, cwd="/tmp")


def _no_files(monkeypatch):
    monkeypatch.setattr("berth.state.session_on_disk", lambda root, session: True)


def test_sleep_closes_like_pause_and_wake_brings_every_sleeper_back(catalog, monkeypatch):
    _no_files(monkeypatch)
    cmux = FakeCmux()
    for berth_id in ("a", "b", "c"):
        _live(catalog, cmux, berth_id, title=berth_id)
    sleep_berth(catalog, cmux, "a", pid_alive_fn=lambda pid: False)
    sleep_berth(catalog, cmux, "b", pid_alive_fn=lambda pid: False)
    pause_berth(catalog, cmux, "c", pid_alive_fn=lambda pid: False)
    rows = {key: catalog.require_berth(key) for key in "abc"}
    assert [is_sleeping(r["state"], r["park_reason"]) for r in rows.values()] == [True, True, False]
    assert sorted(cmux.closed) == ["ws-a", "ws-b", "ws-c"]
    assert sorted(sleeping_ids(catalog)) == ["a", "b"]
    results = wake_all(catalog, cmux)
    assert sorted(item["id"] for item in results if item["result"] == "woken") == ["a", "b"]
    assert all(call["focus"] is False for call in cmux.created)
    assert catalog.require_berth("a")["state"] == "live" and catalog.require_berth("c")["state"] == "parked"


def test_a_closed_session_switches_between_sleeping_and_paused_without_opening(catalog):
    cmux = FakeCmux()
    _insert(catalog, id="p", state="parked", cmux_workspace_id=None)
    sleep_berth(catalog, cmux, "p", pid_alive_fn=lambda pid: False)
    assert catalog.require_berth("p")["park_reason"] == "sleep"
    pause_berth(catalog, cmux, "p", pid_alive_fn=lambda pid: False)
    assert catalog.require_berth("p")["park_reason"] == "pause"
    assert cmux.created == [] and cmux.closed == []


def test_a_cmux_quit_counts_as_sleeping_and_the_screen_is_never_put_to_sleep(catalog):
    from berth.errors import BerthError

    _insert(catalog, id="q", state="parked", cmux_workspace_id=None)
    with catalog.immediate():
        catalog.conn.execute("UPDATE berth SET park_reason = 'cmux_quit' WHERE id = 'q'")
    assert sleeping_ids(catalog) == ["q"]
    _insert(catalog, id="screen", title="Berth", state="live", cmux_workspace_id="ws-screen")
    try:
        sleep_berth(catalog, FakeCmux(), "screen", pid_alive_fn=lambda pid: False)
        raise AssertionError("the screen went to sleep")
    except BerthError as exc:
        assert exc.code == "PROTECTED"


def test_the_screen_s_records_a_sleep_and_the_drain_closes_it_asleep(desk):
    app = make_app(desk)
    app.focus_berth("berth-sample")
    app.handle("S")
    assert app.snap.intents[0].op == "sleep" and "W wakes it" in app.message
    cmux = FakeCmux()
    cmux.workspaces["ws-sample"] = Workspace(id="ws-sample", title="sample-split", cwd="/tmp")
    conn = sqlite3.connect(desk)
    conn.execute("UPDATE berth SET cmux_workspace_id = 'ws-sample' WHERE id = 'berth-sample'")
    conn.commit()
    conn.close()
    from berth.db import Catalog

    db = Catalog(desk).open()
    try:
        assert drain_once(db, cmux, pid_alive_fn=lambda pid: False)[0]["result"] == "done"
        row = db.require_berth("berth-sample")
        assert (row["state"], row["park_reason"]) == ("parked", "sleep")
    finally:
        db.close()
    app.reload()
    line = next(line.text for line in app.lines() if "sample-split" in line.text)
    assert " sleeping " in line
    assert "1 sleeping" in app.lines()[0].text


def test_z_asks_then_puts_every_live_session_but_the_screen_to_sleep(desk):
    app = make_app(desk, height=40)
    live = [b for b in app.snap.berths.values() if b.state == "live" and b.kind != "berth" and b.title != "Berth"]
    app.handle("Z")
    assert app.mode == "confirm-sleep" and f"{len(live)} live" in app.text()
    app.handle("n")
    assert app.snap.intents == []
    app.handle("Z")
    app.handle("y")
    slept = sorted(item.berth_id for item in app.snap.intents if item.op == "sleep")
    assert slept == sorted(b.id for b in live)
    assert all(app.snap.berths[i].title != "Berth" for i in slept)


def test_w_wakes_every_sleeper_without_focus_and_s_p_switch_a_closed_row(desk):
    from berth.db import Catalog

    Catalog(desk).open().close()  # the fixture gains park_reason on first open
    conn = sqlite3.connect(desk)
    conn.execute("UPDATE berth SET park_reason = 'sleep' WHERE id = 'berth-mark'")
    conn.execute("UPDATE berth SET park_reason = 'cmux_quit' WHERE id = 'berth-method'")
    conn.commit()
    conn.close()
    app = make_app(desk, height=40)
    app.handle("W")
    woken = {item.berth_id: item.detail for item in app.snap.intents if item.op == "play"}
    assert set(woken) == {"berth-mark", "berth-method"} and all(d["focus"] is False for d in woken.values())
    assert "waking 2" in app.message
    app.focus_berth("berth-mark")
    app.handle("p")
    assert app.snap.berths["berth-mark"].park_reason == "pause" and "paused" in app.message
    app.handle("S")
    assert app.snap.berths["berth-mark"].park_reason == "sleep" and "sleeping" in app.message


def test_the_mcp_and_the_cli_sleep_and_wake(desk, capsys, monkeypatch):
    from berth.cli import main

    assert main(["--db", str(desk), "sleep"]) != 0  # needs a name or --all
    capsys.readouterr()
    conn = sqlite3.connect(desk)
    conn.execute("UPDATE berth SET park_reason = 'pause' WHERE id = 'berth-mark'")
    conn.commit()
    conn.close()
    assert main(["--db", str(desk), "sleep", "berth-mark"]) == 0
    conn = sqlite3.connect(desk)
    assert conn.execute("SELECT park_reason FROM berth WHERE id = 'berth-mark'").fetchone()[0] == "sleep"
    conn.close()
