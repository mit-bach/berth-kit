"""The screen's columns line up, and names are cut only when the frame is too narrow.

Two levels:

* The frame model, across every width from 40 to 240 columns: each line
  is split into terminal cells (a wide character takes two) and the column
  where each row's state starts is found from the text itself, not from
  the layout code under test.
* The real screen in a pseudo-terminal, read back cell by cell through a
  terminal emulator (pyte), so the painter's own placement is checked too.

The tree is built to be awkward: an uncolored Inbox next to colored
folders, three levels of nesting, a 60-character name, CJK and emoji names,
a fork mark, and every kind word (Process is the longest).
"""

from __future__ import annotations

import fcntl
import os
import pty
import select
import sqlite3
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest

from berth.fixture import build_contract, insert_berth, insert_folder
from berth.lineedit import cell_width
from berth.render import NOTE_MIN, TITLE_MIN, kind_word, state_text, title_cells
from tests.support import make_app

LONG = "Ecommerce Project Tutor for the quarterly close and its follow-ups"
STATES = ("live busy", "live", "parked", "playing", "pausing", "bg")


def _desk(path: Path, extra: list[tuple] = ()) -> Path:
    conn = build_contract(path)
    conn.execute("ALTER TABLE folder ADD COLUMN color TEXT")
    insert_folder(conn, "f-inbox", None, "Inbox", 0, note="not in a folder")
    insert_folder(conn, "f-hy", None, "HOMESTEAD", 1)
    insert_folder(conn, "f-edu", "f-hy", "HOMESTEAD education", 0)
    insert_folder(conn, "f-deep", "f-edu", "Tutors deep", 0)
    insert_folder(conn, "f-me", None, "PERSONAL", 2)
    for folder_id, color in (("f-hy", "#00dfa0"), ("f-edu", "#c53829"), ("f-deep", "#ff8b2e"), ("f-me", "#e0bdff")):
        conn.execute("UPDATE folder SET color = ? WHERE id = ?", (color, folder_id))
    rows = [
        ("b-shell", "f-inbox", "fresh shell", {"kind": "shell", "session": None, "state": "parked"}),
        ("b-prime", "f-hy", "Tutor Prime", {"kind": "other-agent", "agent_name": "claude", "state": "live", "workspace": "ws-1"}),
        ("b-fork", "f-hy", "Tutor Prime ⑂", {"kind": "other-agent", "agent_name": "claude"}),
        ("b-long", "f-edu", LONG, {"note": "a note long enough that it has to be cut on any normal screen width " * 2}),
        ("b-cjk", "f-edu", "日本語のセッション", {}),
        ("b-emoji", "f-deep", "🚀 launch checklist", {"kind": "other-agent", "agent_name": "codex"}),
        ("b-proc", "f-deep", "dev-server", {"kind": "process", "session": None, "state": "live", "workspace": "ws-2",
                                            "recipe_argv": '["python3","-m","http.server"]', "recipe_display": "python3 -m http.server"}),
        ("b-me", "f-me", "Obsidian development", {}),
        *extra,
    ]
    for berth_id, folder, title, fields in rows:
        insert_berth(conn, id=berth_id, folder_id=folder, title=title, **fields)
    conn.commit()
    conn.close()
    return path


def _cells(text: str) -> list[str]:
    """One entry per terminal cell; the second cell of a wide character is ''."""
    out: list[str] = []
    for char in text:
        out.append(char)
        if cell_width(char) == 2:
            out.append("")
    return out


def _find(cells: list[str], word: str, start: int) -> int:
    target = list(word)
    for index in range(start, len(cells) - len(target) + 1):
        if cells[index : index + len(target)] == target and (index == 0 or cells[index - 1] == " "):
            return index
    return -1


def _measure(rows, lines) -> dict:
    """Where each row's state (or a folder's summary) and kind start, found from the text."""
    by_id = {row.id: row for row in rows}
    found = {"state": {}, "kind": {}, "summary": {}, "title_whole": {}}
    for line in lines:
        row = by_id.get(line.row_id)
        if row is None:
            continue
        cells = _cells(line.text)
        name_at = 1 + 3 + 2 * row.depth + 2
        if row.kind == "folder":
            first = "not" if row.title == "Inbox" else f"{row.live_n}"
            found["summary"][row.id] = _find(cells, first, name_at + 1)
            continue
        state = state_text(row)
        state_at = _find(cells, state, name_at + 1)
        found["state"][row.id] = state_at
        found["kind"][row.id] = _find(cells, kind_word(row.berth_kind, row.agent_name), state_at + len(state))
        found["title_whole"][row.id] = "".join(cells[name_at : name_at + cell_width(row.title)]).startswith(row.title)
    return found


def _required(rows) -> int:
    kinds = max(cell_width(kind_word(r.berth_kind, r.agent_name)) for r in rows if r.kind == "berth")
    return 1 + 3 + max(title_cells(r) for r in rows) + 1 + 9 + 1 + 3 + 1 + max(5, kinds) + 1 + NOTE_MIN


@pytest.fixture
def awkward(tmp_path) -> Path:
    return _desk(tmp_path / "awkward.sqlite")


def test_every_width_keeps_columns_aligned_and_names_whole_when_they_fit(awkward):
    app = make_app(awkward, width=200, height=40)
    need = _required(app.rows)
    for width in range(40, 241):
        app.set_size(width, 40)
        lines = app.lines()
        for line in lines:
            assert len(_cells(line.text)) <= width, (width, line.text)
        found = _measure(app.rows, lines)
        states = set(found["state"].values())
        kinds = set(found["kind"].values())
        summaries = set(found["summary"].values())
        assert -1 not in states | kinds | summaries, (width, found)
        assert len(states) == 1, (width, found["state"])  # every Live/Parked starts in one column
        assert len(kinds) == 1, (width, found["kind"])
        assert summaries == states, (width, summaries, states)  # folder counts line up with it, Inbox too
        if width >= need:
            assert all(found["title_whole"].values()), (width, found["title_whole"])
            longest = max(title_cells(row) for row in app.rows)
            assert states == {1 + 3 + longest + 1}, width  # the column sits just past the longest name
        else:
            assert min(states) >= 1 + 3 + TITLE_MIN + 1


def test_the_state_column_moves_right_by_exactly_the_longer_name(tmp_path):
    base = make_app(_desk(tmp_path / "a.sqlite"), width=240, height=40)
    longer = LONG + " and one more clause"
    grown = make_app(_desk(tmp_path / "b.sqlite", extra=[("b-x", "f-deep", longer, {})]), width=240, height=40)
    before = _measure(base.rows, base.lines())["state"]
    after = _measure(grown.rows, grown.lines())["state"]
    depth_before = max(title_cells(row) for row in base.rows)
    depth_after = max(title_cells(row) for row in grown.rows)
    assert set(after.values()) == {next(iter(before.values())) + (depth_after - depth_before)}


def test_folding_a_folder_does_not_move_the_columns(awkward):
    app = make_app(awkward, width=200, height=40)
    open_state = set(_measure(app.rows, app.lines())["state"].values())
    app.focus_folder("f-edu")
    app.handle("h")
    folded = set(_measure(app.rows, app.lines())["state"].values())
    assert folded == open_state


# The real screen, read back from a terminal emulator ------------------


def _emulator(width: int, height: int):
    """pyte, plus REP (CSI n b), which curses uses for runs of one character and cmux implements."""
    import pyte

    class Screen(pyte.Screen):
        last = " "

        def draw(self, data: str) -> None:
            if data:
                self.last = data[-1]
            super().draw(data)

        def repeat_last(self, count: int = 1) -> None:
            super().draw(self.last * max(1, count))

    class Stream(pyte.ByteStream):
        csi = {**pyte.ByteStream.csi, "b": "repeat_last"}

    screen = Screen(width, height)
    return screen, Stream(screen)


def _row_for(text: str, rows) -> object | None:
    """The row whose name this painted line shows: the longest name (or its clipped start) in it."""
    best = None
    for row in rows:
        name = row.title
        shown = name if name in text else next((name[:n] for n in range(len(name), 3, -1) if name[:n] + "…" in text), None)
        if shown and (best is None or len(shown) > best[0]):
            best = (len(shown), row)
    return best[1] if best else None


def _screen(db: Path, width: int, height: int = 30) -> list[str]:

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", height, width, 0, 0))
    env = os.environ.copy()
    root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    env["TERM"] = "xterm-256color"
    env["BERTH_NO_TICK"] = "1"

    def _take_tty() -> None:
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    proc = subprocess.Popen([sys.executable, "-m", "berth", "--db", str(db)], stdin=slave, stdout=slave,
                            stderr=slave, env=env, close_fds=True, preexec_fn=_take_tty)
    os.close(slave)
    screen, stream = _emulator(width, height)
    try:
        deadline = time.time() + 5
        quiet_since = None
        while time.time() < deadline:
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    stream.feed(os.read(master, 65536))
                except OSError:
                    break
                quiet_since = None
            elif screen.display[0].startswith("Berth"):
                quiet_since = quiet_since or time.time()
                if time.time() - quiet_since > 0.4:
                    break
        snapshot = [[screen.buffer[y][x].data for x in range(width)] for y in range(height)]
        os.write(master, b"q")
        deadline = time.time() + 3
        while proc.poll() is None and time.time() < deadline:
            # Keep reading: a full pty would block the screen's last frame and its exit.
            ready, _, _ = select.select([master], [], [], 0.05)
            if ready:
                try:
                    os.read(master, 65536)
                except OSError:
                    break
        proc.wait(timeout=1)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)
        os.close(master)
    return snapshot


@pytest.mark.parametrize("width", [72, 120, 200])
def test_the_painted_screen_lines_up_cell_for_cell(awkward, width):
    app = make_app(awkward, width=width, height=30)
    rows = {row.title: row for row in app.rows}
    painted = _screen(awkward, width)
    columns = set()
    name_area = 1 + 3 + max(title_cells(row) for row in app.rows) + 1
    for cells in painted[2:]:
        text = "".join(cells[:name_area])  # the folder-path column at the right can name a folder too
        row = _row_for(text, rows.values())
        if row is None:
            continue
        if row.kind == "folder":
            first = "not" if row.title == "Inbox" else f"{row.live_n}"
            columns.add(("summary", _find(cells, first, 1 + 3 + 2 * row.depth + 2 + 1)))
            continue
        state = state_text(row)
        at = _find(cells, state, 1 + 3 + 2 * row.depth + 2 + 1)
        columns.add(("state", at))
        if width >= _required(app.rows):
            name_at = 1 + 3 + 2 * row.depth + 2
            assert "".join(cells[name_at : name_at + cell_width(row.title)]).startswith(row.title), (width, row.title)
    states = {at for kind, at in columns if kind == "state"}
    summaries = {at for kind, at in columns if kind == "summary"}
    assert len(states) == 1 and -1 not in states, (width, columns)
    assert summaries == states, (width, columns)
