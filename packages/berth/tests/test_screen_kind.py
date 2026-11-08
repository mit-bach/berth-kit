"""The Berth screen is its own kind, Berth, painted in a color no session kind uses."""

from __future__ import annotations

import sqlite3

from berth.adopt import build_record
from berth.model import Workspace
from berth.render import kind_word
from berth.state import _play_argv
from tests.support import make_app
from tests.test_state import _insert


def test_a_workspace_titled_berth_is_adopted_as_the_screen(catalog):
    record = build_record(workspace=Workspace(id="ws-s", title="Berth", cwd="/tmp"), hook=None, surfaces=[],
                          groups=[], folders=[], inbox_id=catalog.ensure_inbox())
    assert (record["kind"], record["recipe_display"]) == ("berth", "berth")


def test_the_screen_filed_as_a_shell_becomes_kind_berth_and_plays_berth(catalog):
    _insert(catalog, id="old-screen", title="Berth", kind="shell", state="live", cmux_workspace_id="ws-s")
    _insert(catalog, id="other", title="notes", kind="shell", state="live", cmux_workspace_id="ws-n")
    catalog.close()
    catalog.open()
    row = catalog.require_berth("old-screen")
    assert row["kind"] == "berth" and kind_word(row["kind"]) == "Berth"
    assert catalog.require_berth("other")["kind"] == "shell"
    assert _play_argv(catalog, row, False, None)[0] == ["berth"]


def test_the_kind_column_paints_berth_magenta_shells_cyan_and_agents_green(desk):
    from berth.curses_ui import PAIRS

    conn = sqlite3.connect(desk)
    conn.execute("UPDATE berth SET title = 'Berth', kind = 'berth' WHERE id = 'berth-mark'")
    conn.commit()
    conn.close()
    app = make_app(desk, width=140, height=40)
    painted = {}
    for line in app.lines():
        row = next((r for r in app.rows if r.id == line.row_id and r.kind == "berth"), None)
        if row is None:
            continue
        word = kind_word(row.berth_kind, row.agent_name)
        for start, end, name in line.spans:
            if line.text[start:end] == word:
                painted[row.berth_kind] = name
    assert painted["berth"] == "screen" and PAIRS["screen"] not in {PAIRS["live"], PAIRS["play"]}
    assert painted.get("shell", "play") == "play" and painted.get("grok", "live") == "live"
