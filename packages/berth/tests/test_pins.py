"""Pinned sessions: one mark, kept in step with cmux's workspace pin; first in the sidebar."""

from __future__ import annotations

import json
import sqlite3

from berth.model import Workspace
from berth.ops import mkdir_path
from berth.pins import PIN_MARK, set_pinned, sync
from berth.sidebar import Pace
from berth.sidebar import sync as sidebar_sync
from tests.fakes import FakeCmux
from tests.test_state import _insert


def _live(catalog, cmux, berth_id, *, folder=None, used="2026-10-01", pinned_in_cmux=False):
    _insert(catalog, id=berth_id, title=berth_id, state="live", cmux_workspace_id=f"ws-{berth_id}",
            folder_id=folder or catalog.ensure_inbox())
    with catalog.immediate():
        catalog.update_berth(berth_id, last_used_at=f"{used}T00:00:00.000000Z")
    cmux.workspaces[f"ws-{berth_id}"] = Workspace(id=f"ws-{berth_id}", title=berth_id, cwd="/tmp",
                                                   index=len(cmux.workspaces), pinned=pinned_in_cmux)


def _pinned(catalog, berth_id) -> bool:
    return bool(catalog.require_berth(berth_id)["pinned"])


def test_a_pin_is_berths_alone_and_a_pin_made_in_cmux_turns_it_over(catalog):
    """2026-10-31, Dominik: Berth stops using cmux's pins. Berth never pins in cmux; a cmux pin is a request."""
    cmux = FakeCmux()
    _live(catalog, cmux, "a")
    _live(catalog, cmux, "b")
    set_pinned(catalog, "a", True)  # P in the screen, berth pin, the MCP's update
    sync(catalog, cmux, list(cmux.workspaces.values()))
    assert _pinned(catalog, "a") and cmux.workspaces["ws-a"].pinned is False and getattr(cmux, "pins", []) == []
    cmux.pin_workspace("ws-b", True)  # Pin in the sidebar's menu
    sync(catalog, cmux, list(cmux.workspaces.values()))
    assert _pinned(catalog, "b") and cmux.workspaces["ws-b"].pinned is False  # Berth's now; cmux's cleared
    cmux.pin_workspace("ws-a", True)  # Unpin in the sidebar's menu: also a cmux pin, a request to turn it over
    sync(catalog, cmux, list(cmux.workspaces.values()))
    assert not _pinned(catalog, "a") and cmux.workspaces["ws-a"].pinned is False
    sync(catalog, cmux, list(cmux.workspaces.values()))
    assert _pinned(catalog, "b") and not _pinned(catalog, "a")  # settled
    kinds = [row["kind"] for row in catalog.conn.execute("SELECT kind FROM event WHERE kind IN ('pin', 'unpin') ORDER BY rowid")]
    assert kinds == ["pin", "pin", "unpin"]


def test_pins_kept_in_step_before_the_change_are_cleared_in_cmux_not_turned_over(catalog):
    cmux = FakeCmux()
    _live(catalog, cmux, "a", pinned_in_cmux=True)
    with catalog.immediate():
        catalog.update_berth("a", pinned=1, attachments=json.dumps({"pin_seen": {"workspace": "ws-a", "pinned": True}}))
    sync(catalog, cmux, list(cmux.workspaces.values()))
    assert _pinned(catalog, "a") and cmux.workspaces["ws-a"].pinned is False

def test_a_pinned_session_keeps_its_pin_through_a_close_and_comes_back_first(catalog):
    cmux = FakeCmux()
    _live(catalog, cmux, "a")
    _live(catalog, cmux, "b", used="2026-10-30")
    set_pinned(catalog, "a", True)
    from berth import machine

    machine.transition(catalog, "a", "pause_flush", workspace_id="ws-a")
    machine.transition(catalog, "a", "pause_closed", workspace_id=None, reason="pause")
    assert _pinned(catalog, "a")
    machine.transition(catalog, "a", "play_claim", workspace_id=None)
    machine.transition(catalog, "a", "play_created", workspace_id="ws-a2")
    del cmux.workspaces["ws-a"]
    cmux.workspaces["ws-a2"] = Workspace(id="ws-a2", title="a", cwd="/tmp", index=9)
    sync(catalog, cmux, list(cmux.workspaces.values()))
    assert _pinned(catalog, "a") and cmux.workspaces["ws-a2"].pinned is False
    sidebar_sync(catalog.path, cmux, list(cmux.workspaces.values()), Pace(clock=lambda: 1.0))
    assert cmux.reorders[-1][0] == "ws-a2"  # first in the sidebar by Berth's pin, not cmux's

def test_pinned_sessions_come_first_in_berth_order_and_a_pin_reorders_at_once(catalog):
    old = mkdir_path(catalog, "Old")["id"]
    new = mkdir_path(catalog, "New")["id"]
    cmux = FakeCmux()
    _live(catalog, cmux, "o1", folder=old, used="2026-01-01")
    _live(catalog, cmux, "o2", folder=old, used="2026-01-02")
    _live(catalog, cmux, "n1", folder=new, used="2026-03-01")
    now = [100.0]
    pace = Pace(clock=lambda: now[0])

    def settle():
        sync(catalog, cmux, list(cmux.workspaces.values()))
        moved = sidebar_sync(catalog.path, cmux, list(cmux.workspaces.values()), pace)
        if moved:
            for index, workspace_id in enumerate(cmux.reorders[-1]):
                cmux.workspaces[workspace_id].index = index
        return moved

    assert settle() and cmux.reorders[-1] == ["ws-n1", "ws-o2", "ws-o1"]
    now[0] += 2
    set_pinned(catalog, "o1", True)
    set_pinned(catalog, "o2", True)
    assert settle()  # a pin applies at once, inside the 30-second pace
    assert cmux.reorders[-1] == ["ws-o2", "ws-o1", "ws-n1"]  # pinned first, in Berth's order (last used)
    colors_before = list(cmux.colors)
    now[0] += 2
    assert not settle() and cmux.colors == colors_before  # nothing else moves, colors untouched


def test_the_screen_shows_one_mark_and_the_columns_still_line_up(tmp_path):
    from tests.support import make_app
    from tests.test_columns import _desk, _measure

    path = _desk(tmp_path / "pins.sqlite")
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE berth ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
    conn.execute("UPDATE berth SET pinned = 1, protect = 1 WHERE id IN ('b-prime', 'b-cjk')")
    conn.commit()
    conn.close()
    app = make_app(path, width=200, height=40)
    app.marked = {"b-prime"}  # pinned and marked: both fit the flag column
    lines = app.lines()
    text = "\n".join(line.text for line in lines)
    assert text.count(PIN_MARK) == 2 and "!" not in text and "*" not in text
    found = _measure(app.rows, lines)
    assert len(set(found["state"].values())) == 1 and len(set(found["kind"].values())) == 1


def test_the_mcp_and_scope_carry_the_pin(catalog):
    from berth.scope import query

    cmux = FakeCmux()
    _live(catalog, cmux, "a")
    set_pinned(catalog, "a", True)
    rows = query(catalog.path, {"folder": "/"})["folders"][0]["sessions"]
    assert rows == [{"id": "a", "title": "a", "state": "live", "pinned": True}]
    assert json.loads(catalog.require_berth("a")["attachments"] or "{}") == {}


def test_closing_a_pinned_workspace_unpins_it_first_and_the_pin_stays_in_berth(catalog, monkeypatch):
    """2026-10-28: cmux refuses to close a pinned workspace, so pause, sleep, cycle, and delete failed."""
    from berth.cmux import Cmux
    from berth.errors import BerthError

    calls = []
    pinned = {"ws-x": True}

    def run(self, args, **kwargs):
        calls.append(args)
        if args[:2] == ["workspace", "close"] and pinned.get(args[2]):
            raise BerthError("CMUX_REJECTED", "Error: protected: Pinned workspaces can't be closed while pinned. "
                                              "Unpin the workspace first.", {})
        if args[0] == "workspace-action" and args[-1] == "unpin":
            pinned[args[2]] = False
        return ""

    monkeypatch.setattr(Cmux, "_run", run)
    Cmux.__new__(Cmux).close_workspace("ws-x")
    assert calls == [["workspace", "close", "ws-x"], ["workspace-action", "--workspace", "ws-x", "--action", "unpin"],
                     ["workspace", "close", "ws-x"]]

