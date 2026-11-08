"""Idle (2026-11-05, Dominik): between live and sleeping. Closed to free its memory, in use again soon, and shown
with the live sessions, in the screen (f: live + idle) and in every agent's sessions."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from berth.fixture import insert_berth
from tests.support import make_app
from tests.test_mcp import tree  # noqa: F401  (the MCP's made-up folders and sessions)
from tests.test_state import _insert


def _closed(desk, rows):
    from berth.db import Catalog

    Catalog(desk).open().close()
    conn = sqlite3.connect(desk)
    for berth_id, title, reason in rows:
        insert_berth(conn, id=berth_id, folder_id="folder-inbox", title=title, state="parked")
        conn.execute("UPDATE berth SET park_reason = ? WHERE id = ?", (reason, berth_id))
    conn.commit()
    conn.close()


def _filter(app, name):
    for _ in range(10):
        if app.filter == name:
            return app.text()
        app.handle("f")
    raise AssertionError(name)


def test_live_plus_idle_shows_the_live_and_the_idle_and_nothing_put_away(desk):
    _closed(desk, (("idle-1", "back-after-lunch", "idle"), ("slept-1", "slept-by-me", "sleep"),
                   ("paused-1", "paused-by-me", "pause")))
    app = make_app(desk, height=40)
    text = _filter(app, "live + idle")
    assert "filter live + idle" in text and "back-after-lunch" in text
    assert "slept-by-me" not in text and "paused-by-me" not in text
    live = [b.title for b in app.snap.berths.values() if b.state == "live"]
    assert live and all(title in text for title in live)
    text = _filter(app, "idle")
    shown = [row for row in app.visible if row.kind == "berth"]
    assert "back-after-lunch" in text and [row.id for row in shown] == ["idle-1"]
    text = _filter(app, "parked")
    assert "paused-by-me" in text and "back-after-lunch" not in text
    app.filter = "all"
    app.reload()
    row = next(line.text for line in app.lines() if "back-after-lunch" in line.text)
    assert " idle " in row and "1 idle" in app.lines()[0].text


def test_the_top_bar_has_no_account_usage(desk, monkeypatch):
    """Dominik, 2026-11-05: "get rid of the account usage stats from the top tool bar"."""
    monkeypatch.setattr("berth.accounts.usage_parts", lambda: ["uni 5h 42% 7d 63%"])
    header = make_app(desk, height=40).lines()[0].text
    assert "42%" not in header and "7d" not in header and " live" in header


def test_i_makes_a_live_session_idle_and_a_closed_one_idle_without_opening_it(desk):
    _closed(desk, (("paused-1", "paused-by-me", "pause"),))
    app = make_app(desk, height=40)
    live = next(b for b in app.snap.berths.values() if b.state == "live" and b.kind != "berth" and not b.busy)
    app.focus_berth(live.id)
    app.handle("i")
    assert any(item.op == "idle" and item.berth_id == live.id for item in app.snap.intents)
    assert "idle recorded" in app.message
    app.focus_berth("paused-1")
    app.handle("i")
    assert app.snap.berths["paused-1"].park_reason == "idle" and app.message.startswith("idle · paused-by-me")
    app.handle("i")
    assert app.message.startswith("already idle")
    app.handle("p")
    assert app.snap.berths["paused-1"].park_reason == "pause"  # p puts an idle one away, as it does a sleeping one


def test_the_screens_idle_request_closes_the_session_as_idle(catalog):
    from berth.catalog import insert_intent
    from berth.drain import drain_once
    from tests.fakes import FakeCmux

    from berth.model import Workspace

    _insert(catalog, id="tutor", title="Tutor", kind="grok", agent_name="grok", state="live", cmux_workspace_id="ws-t")
    cmux = FakeCmux()
    cmux.workspaces["ws-t"] = Workspace(id="ws-t", title="Tutor", cwd="/tmp")
    insert_intent(catalog.path, "idle", "tutor", {"confirm": True}, datetime.now(timezone.utc))
    drain_once(catalog, cmux, pid_alive_fn=lambda pid: False)
    row = catalog.require_berth("tutor")
    assert (row["state"], row["park_reason"]) == ("parked", "idle")
    [asked] = [event for event in catalog.events_for("tutor") if event["kind"] == "screen_request"]
    detail = json.loads(asked["detail"]) if isinstance(asked["detail"], str) else asked["detail"]
    assert detail["op"] == "idle"


def test_agents_see_idle_sessions_by_default_and_control_makes_one_idle_and_wakes_it(tree, monkeypatch):  # noqa: F811
    monkeypatch.setattr("berth.state.session_on_disk", lambda root, session: True)
    api, cmux = tree["api"], tree["cmux"]
    done = api("control", {"action": "idle", "sessions": "tutor"})
    assert done["done"][0]["state"] == "idle" and "ws-tutor" in cmux.closed
    every = {row["id"]: row["state"] for group in api("sessions", {"folder": "/"})["folders"]
             for row in group["sessions"]}
    assert every["tutor"] == "idle"  # no filter needed
    picked = {row["id"] for group in api("sessions", {"folder": "/", "state": ["live", "idle"]})["folders"]
              for row in group["sessions"]}
    assert "tutor" in picked and "nap" not in picked  # nap is sleeping
    folders = {row["folder"]: row for row in api("folders", {})["folders"]}
    assert any(row.get("idle") for row in folders.values())
    assert api("control", {"action": "idle", "sessions": "tutor"})["done"][0]["result"] == "already idle"
    woke = api("control", {"action": "wake", "sessions": "tutor"})
    assert woke["done"] and woke["done"][0]["result"] == "woken", woke


def test_idle_can_wait_until_the_session_is_not_busy(catalog):
    from berth.deferred import pending, run_due
    from berth.mcp import Api
    from tests.test_deferred import IDLE, _later, _live
    from tests.fakes import FakeCmux

    cmux = FakeCmux()
    _live(catalog, cmux, "worker")
    Api(catalog.path, cmux=cmux, caller_workspace=None)("control", {"action": "idle", "sessions": "worker",
                                                                     "when": "idle"})
    assert [item["action"] for item in pending(catalog)] == ["idle"]
    run_due(catalog, cmux, now=_later(0.1), observe_fn=lambda row: IDLE)
    row = catalog.require_berth("worker")
    assert (row["state"], row["park_reason"]) == ("parked", "idle")
