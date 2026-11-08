"""Switching to the Berth screen puts its cursor on the session you came from."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from berth.arrival import FRESH_SECONDS, arrived_from, record, selected_path
from tests.support import make_app


def _event(workspace, previous, *, event_id="e1", at=None):
    stamp = (at or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return {"id": event_id, "name": "workspace.selected", "occurred_at": stamp, "workspace_id": workspace,
            "payload": {"workspace_id": workspace, "previous_workspace_id": previous, "selected": True}}


def test_a_switch_counts_once_while_fresh_and_only_for_its_own_workspace(tmp_path):
    now = datetime.now(timezone.utc)
    record(tmp_path, _event("WS-SCREEN", "ws-claude"))
    assert oct(selected_path(tmp_path).stat().st_mode & 0o777) == "0o600"
    assert arrived_from(tmp_path, "ws-screen", now, None) == ("e1", "ws-claude")  # cmux ids differ in case
    assert arrived_from(tmp_path, "ws-screen", now, "e1") is None  # handled already
    assert arrived_from(tmp_path, "ws-other", now, None) is None  # a switch to some other workspace
    assert arrived_from(tmp_path, "", now, None) is None  # a screen outside cmux
    assert arrived_from(tmp_path, "ws-screen", now + timedelta(seconds=FRESH_SECONDS + 1), None) is None  # a replay


def _desk_with_workspaces(desk):
    conn = sqlite3.connect(desk)
    conn.execute("UPDATE berth SET state = 'live', cmux_workspace_id = 'WS-MARK' WHERE id = 'berth-mark'")
    conn.commit()
    conn.close()


def test_the_screen_lands_on_the_session_you_came_from_once(desk, monkeypatch):
    _desk_with_workspaces(desk)
    monkeypatch.setenv("CMUX_WORKSPACE_ID", "ws-screen")
    app = make_app(desk, height=40)
    folder = app.snap.berths["berth-mark"].folder_id
    app.focus_folder(folder)
    app.handle("h")  # fold the folder that holds it
    first = app.visible[0]
    app.cursor = (first.kind, first.id)
    record(desk.parent, _event("ws-screen", "ws-mark"))
    app.reload()
    assert app.cursor == ("berth", "berth-mark") and app.current is not None  # unfolded on the way
    app.handle("k")
    moved = app.cursor
    app.reload()
    assert app.cursor == moved  # the same switch does not pull the cursor back on the next tick


def test_a_text_box_or_a_hiding_filter_keeps_the_cursor(desk, monkeypatch):
    _desk_with_workspaces(desk)
    monkeypatch.setenv("CMUX_WORKSPACE_ID", "ws-screen")
    app = make_app(desk, height=40)
    for _ in range(10):
        if app.filter == "parked":
            break
        app.handle("f")
    before = app.cursor
    record(desk.parent, _event("ws-screen", "ws-mark", event_id="e2"))
    app.reload()
    assert app.cursor == before  # a live session is not in the parked filter
    app.handle("f")  # back to all
    elsewhere = next(row for row in app.visible if row.id != "berth-mark")
    app.cursor = (elsewhere.kind, elsewhere.id)
    app.handle("/")
    record(desk.parent, _event("ws-screen", "ws-mark", event_id="e3"))
    app.reload()
    assert app.mode == "search" and app.arrival_seen == "e3"
    app.handle("esc")
    app.reload()
    assert app.cursor == (elsewhere.kind, elsewhere.id)  # handled while typing, not replayed afterwards


def test_the_watcher_keeps_the_last_selection(catalog):
    from berth.watch import WATCH_NAMES, handle_line
    from tests.fakes import FakeCmux

    assert "workspace.selected" in WATCH_NAMES
    line = json.dumps({**_event("ws-screen", "ws-mark", event_id="e9"), "type": "event"})
    assert handle_line(catalog, FakeCmux(), line) is None
    saved = json.loads(selected_path(catalog.path.parent).read_text())
    assert (saved["id"], saved["workspace_id"], saved["previous_workspace_id"]) == ("e9", "ws-screen", "ws-mark")
