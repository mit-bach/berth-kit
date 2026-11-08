"""Deleted sessions wait in the trash for 30 days and come back into Inbox."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from berth import machine, trash
from berth.errors import BerthError
from berth.model import Workspace
from berth.ops import delete_berth, mkdir_path
from tests.fakes import FakeCmux
from tests.support import make_app, write_claude_conversation
from tests.test_state import _insert


def _claude(catalog, berth_id, session, *, folder=None, title="Tutor", state="parked", note=""):
    _insert(catalog, id=berth_id, title=title, state=state, kind="other-agent", agent_name="claude",
            agent_session_id=session, cwd="/tmp/tutor", folder_id=folder or catalog.ensure_inbox(),
            cmux_workspace_id="ws-1" if state == "live" else None, argv=["claude", "-r", session],
            display=f"claude -r {session}", note=note)


def test_a_delete_goes_to_the_trash_and_a_restore_brings_it_back_into_inbox(catalog):
    tutors = mkdir_path(catalog, "HOMESTEAD/Tutors")["id"]
    _claude(catalog, "row", "sess-1", folder=tutors, title="Tutor Prime", state="live", note="keep me")
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(id="ws-1", title="Tutor Prime", cwd="/tmp")
    delete_berth(catalog, cmux, "row", caller_workspace_id=None)
    assert catalog.get_berth("row") is None and cmux.closed == ["ws-1"]
    [item] = trash.items(catalog)
    assert (item["title"], item["folder"], item["reason"], item["session"]) == ("Tutor Prime", "HOMESTEAD/Tutors", "user", "sess-1")
    assert item["days_left"] == trash.TRASH_DAYS

    restored = trash.restore(catalog, "row")
    row = catalog.require_berth("row")
    assert row["folder_id"] == catalog.ensure_inbox()
    assert (row["state"], row["park_reason"], row["cmux_workspace_id"], row["last_workspace_id"]) == ("parked", "restored", None, None)
    assert (row["agent_session_id"], row["note"], row["title"]) == ("sess-1", "keep me", "Tutor Prime")
    assert restored["id"] == "row" and trash.items(catalog) == []
    kinds = [event["kind"] for event in catalog.events_for("row")]
    assert "delete" in kinds and kinds[-1] == "restore"  # the history before the delete is still there


def test_berths_own_removals_go_to_the_trash_too(catalog):
    _claude(catalog, "dup", "sess-2")
    machine.delete(catalog, "dup", reason="merged", detail={"into": "other"})
    [item] = trash.items(catalog)
    assert item["reason"] == "merged" and item["detail"] == {"into": "other"}


def test_the_trash_forgets_after_thirty_days(catalog):
    for berth_id in ("old", "new"):
        _claude(catalog, berth_id, f"s-{berth_id}", title=berth_id)
    machine.delete(catalog, "old", reason="user")
    machine.delete(catalog, "new", reason="user")
    stamp = (datetime.now(timezone.utc) - timedelta(days=31)).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    with catalog.immediate():
        catalog.conn.execute("UPDATE trash SET deleted_at = ? WHERE id = 'old'", (stamp,))
    assert [item["id"] for item in trash.items(catalog)] == ["new"]
    later = datetime.now(timezone.utc) + timedelta(days=29, hours=12)
    assert trash.items(catalog, now=later)[0]["days_left"] == 1


def test_forget_find_and_a_reused_id(catalog):
    for berth_id, title in (("a1", "Same"), ("a2", "Same"), ("b", "Other")):
        _claude(catalog, berth_id, f"s-{berth_id}", title=title)
        machine.delete(catalog, berth_id, reason="user")
    with pytest.raises(BerthError) as caught:
        trash.find(catalog, "Same")
    assert len(caught.value.details["matches"]) == 2
    assert trash.find(catalog, "Oth")["id"] == "b"
    trash.forget(catalog, "b")
    assert {item["id"] for item in trash.items(catalog)} == {"a1", "a2"}
    with pytest.raises(BerthError):
        trash.restore(catalog, "b")
    _claude(catalog, "a1", "s-new", title="took the id")  # a new row now holds that id
    restored = trash.restore(catalog, "a1")
    assert restored["id"] != "a1" and restored["title"] == "Same"


def test_deletions_from_before_the_trash_are_rebuilt_from_their_events(catalog):
    write_claude_conversation("sess-old", "/tmp/old-cwd")
    tutors = mkdir_path(catalog, "Tutors")["id"]
    with catalog.immediate():
        catalog.insert_event("gone", "adopt", {"folder_id": tutors, "kind": "other-agent", "title": "Harness v4"})
        catalog.insert_event("gone", "delete", {"reason": "user", "kind": "other-agent", "title": "Harness v4",
                                                "session_id": "sess-old"})
        catalog.insert_event("junk", "delete", {"reason": "user", "kind": "shell", "title": "_check junk"})
    added = trash.backfill(catalog, skip=lambda title: title.startswith("_check"))
    assert added == ["gone"]
    [item] = trash.items(catalog)
    assert (item["title"], item["folder"], item["recovered"], item["session"]) == ("Harness v4", "Tutors", True, "sess-old")
    row = trash.restore(catalog, "gone")
    assert (row["cwd"], row["agent_name"], row["recipe_argv"][-2:]) == ("/tmp/old-cwd", "claude", ["-r", "sess-old"])
    assert trash.backfill(catalog, skip=lambda title: title.startswith("_check")) == []  # restored: not rebuilt


def test_the_screen_deletes_to_the_trash_and_ctrl_t_brings_it_back(desk):
    app = make_app(desk)
    app.focus_berth("berth-mark")
    title = app.current.title
    app.handle("d")
    assert "in the trash" in app.message and "ctrl-t" in app.message
    assert "berth-mark" not in app.snap.berths
    app.handle("ctrl-t")
    assert app.mode == "trash"
    text = app.text()
    assert "Trash · 1 session" in text and title in text and "30d left" in text
    app.handle("enter")
    assert "restored" in app.message and "Inbox" in app.message
    assert app.snap.berths["berth-mark"].state == "parked"
    assert app.snap.berths["berth-mark"].folder_id == "folder-inbox"
    assert "the trash is empty" in app.text()
    app.handle("esc")
    assert app.mode == "tree"


def test_the_screen_deletes_from_the_trash_for_good_only_after_y(desk):
    app = make_app(desk)
    app.focus_berth("berth-mark")
    app.handle("d")
    app.handle("ctrl-t")
    app.handle("D")
    assert app.mode == "confirm-forget" and "cannot come back" in app.text()
    app.handle("n")
    assert app.mode == "trash" and len(app.trash_items) == 1
    app.handle("D")
    app.handle("y")
    assert app.trash_items == [] and "deleted for good" in app.message
    conn = sqlite3.connect(desk)
    assert conn.execute("SELECT count(*) FROM trash").fetchone()[0] == 0
    conn.close()


def test_the_cli_lists_restores_and_forgets(desk, capsys):
    from berth.cli import main

    assert main(["--db", str(desk), "delete", "berth-mark"]) == 0
    capsys.readouterr()
    assert main(["--db", str(desk), "--json", "trash"]) == 0
    listed = json.loads(capsys.readouterr().out)["trash"]
    assert [item["id"] for item in listed] == ["berth-mark"]
    assert main(["--db", str(desk), "trash", "restore", "berth-mark"]) == 0
    conn = sqlite3.connect(desk)
    assert conn.execute("SELECT state FROM berth WHERE id = 'berth-mark'").fetchone()[0] == "parked"
    conn.close()


def test_the_trash_view_lines_up_with_wide_names(desk):
    from berth.lineedit import cell_width

    conn = sqlite3.connect(desk)
    for berth_id, title in (("berth-mark", "日本語のセッション"), ("berth-method", "🚀 launch checklist"),
                            ("berth-margin-week", "plain")):
        conn.execute("UPDATE berth SET title = ?, state = 'parked', cmux_workspace_id = NULL WHERE id = ?",
                     (title, berth_id))
    conn.commit()
    conn.close()
    app = make_app(desk, width=110, height=30)
    for title in ("日本語のセッション", "🚀 launch checklist", "plain"):
        row = next(r for r in app.rows if r.kind == "berth" and r.title == title)
        app.focus_berth(row.id)
        app.handle("d")
    app.handle("ctrl-t")
    columns = set()
    for line in app.lines()[2:]:
        if " deleted " in line.text:
            before = line.text.split(" deleted ")[0]
            columns.add(cell_width(before))
    assert len(columns) == 1, columns


def _trash_two(desk):
    from berth.db import Catalog

    db = Catalog(desk).open()
    try:
        for berth_id in ("berth-mark", "berth-method", "berth-margin-week"):
            row = db.require_berth(berth_id)
            if row["state"] != "parked":
                with db.immediate():
                    db.conn.execute("UPDATE berth SET state = 'parked', cmux_workspace_id = NULL WHERE id = ?", (berth_id,))
            machine.delete(db, berth_id, reason="user")
    finally:
        db.close()


def test_the_terminal_batches_move_the_trash_cursor_not_the_tree(desk):
    """2026-10-28: the input loop turned j, k, and the arrows into moves of the hidden tree."""
    from berth.curses_ui import feed

    _trash_two(desk)
    app = make_app(desk, height=30)
    tree_cursor = app.cursor
    feed(app, ["ctrl-t"])
    assert app.mode == "trash" and app.trash_cursor == 0
    feed(app, ["j"])
    assert app.trash_cursor == 1
    feed(app, ["down", "down"])
    assert app.trash_cursor == 2
    feed(app, ["k", "up"])
    assert app.trash_cursor == 0
    assert app.cursor == tree_cursor  # the tree did not move underneath
    feed(app, ["esc", "?"])
    assert app.mode == "help"
    feed(app, ["j", "j"])
    assert app.help_top == 2


def test_the_real_screen_walks_the_trash_with_j_and_the_arrow_key(desk):
    import fcntl
    import os
    import pty
    import select
    import struct
    import subprocess
    import sys
    import termios
    import time
    from pathlib import Path

    _trash_two(desk)
    from berth.db import Catalog

    db = Catalog(desk).open()
    try:
        order = [item["id"] for item in trash.items(db)]  # newest first
    finally:
        db.close()
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 110, 0, 0))
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]) + os.pathsep + env.get("PYTHONPATH", "")
    env["TERM"] = "xterm-256color"
    env["BERTH_NO_TICK"] = "1"

    def _take_tty() -> None:
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    proc = subprocess.Popen([sys.executable, "-m", "berth", "--db", str(desk)], stdin=slave, stdout=slave,
                            stderr=slave, env=env, close_fds=True, preexec_fn=_take_tty)
    os.close(slave)

    def drain(seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            ready, _, _ = select.select([master], [], [], 0.05)
            if ready:
                try:
                    os.read(master, 65536)
                except OSError:
                    break

    try:
        drain(1.5)
        for chunk in (b"\x14", b"j", b"\x1b[B", b"\r", b"\x1b", b"q"):  # ctrl-t, j, down, enter, esc, q
            os.write(master, chunk)
            drain(0.3)
        proc.wait(timeout=3)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)
        os.close(master)
    db = Catalog(desk).open()
    try:
        left = [item["id"] for item in trash.items(db)]
        assert db.get_berth(order[2]) is not None  # the third line, two moves down, came back
        assert left == [order[0], order[1]]
    finally:
        db.close()
