import sqlite3
from pathlib import Path

from berth.fixture import build_contract, insert_berth, insert_folder
from berth.render import format_rss, format_watch, layout_for
from tests.support import clear, make_app, search


def test_layout_widths_sum_to_the_frame():
    for width in (40, 72, 96, 140, 220):
        lay = layout_for(width)
        # cursor, swatch, name, then a space before each later column
        total = 1 + 3 + lay.gutter + 1 + lay.state_w + 1 + lay.flag_w + 1 + lay.kind_w + 1 + lay.note_w
        total += (1 + lay.path_w) if lay.path_w else 0
        assert total == width, width
        assert lay.flag_w == 3 and lay.state_w == 9


def test_rss_and_watch_words():
    assert format_rss(None) == "children unknown"
    assert format_rss(0) == "children 0 B"
    assert format_rss(7_100_000_000) == "children 7.1 GB"
    assert format_rss(500_000_000) == "children 500 MB"
    assert format_watch(None) == "watch unknown"
    assert format_watch("up") == "watch up"
    assert format_watch("DOWN") == "watch down"
    assert format_watch("maybe") == "watch unknown"


def test_header_when_the_figure_is_absent(tmp_path):
    file = tmp_path / "empty.sqlite"
    conn = build_contract(file)
    conn.close()
    app = make_app(file)
    text = app.text()
    assert "0 live" in text
    assert "0 parked" in text
    assert "children" not in text and "Mac " not in text  # no memory measure yet: nothing shown, never a guess
    assert "watch unknown" in text
    assert "catalog is empty" in text
    conn = sqlite3.connect(file)
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"status", "runtime", "intent"} <= names
    assert conn.execute("SELECT count(*) FROM status").fetchone()[0] == 0
    conn.close()


def test_desk_frame_is_one_tree(desk):
    app = make_app(desk)
    assert len(app.snap.berths) == 12
    assert len(app.snap.folders) == 3
    assert len(app.lines()) == 18
    for line in app.lines():
        assert len(line.text) <= 96
    header = app.lines()[0].text
    assert len(header) == 96
    assert header.startswith("Berth")
    assert header.endswith("watch up")
    assert "4 live" in header
    assert "8 parked" in header
    assert " new" not in header  # the * (new) mark and its count are gone (2026-10-28)
    assert "1 busy" in header
    assert "9 Grok" in header
    assert "2 Shell" in header
    assert "children 7.1 GB" in header
    text = app.text()
    assert text.count("*") == 0 and text.count("!") == 0  # one mark now: 📌 pinned
    assert text.count("📌") == 0
    assert text.count("▾") == 3
    assert "▸" not in text
    cursors = [line.text for line in app.lines() if line.text.startswith(">")]
    assert len(cursors) == 1
    assert cursors[0].startswith(">▾")
    assert "Course" in cursors[0]
    assert "q quits" in app.lines()[1].text
    sample = _line(app, "sample-split")
    assert "live" in sample
    assert "Grok" in sample
    assert "split the sample on the mark" in sample
    assert sample.rstrip().endswith("Course")
    assert "!" not in sample
    assert "*" not in sample
    signer = _line(app, "signer-lab")
    assert "live busy" in signer
    assert "mid-tool on the signer" in signer
    assert "!" not in signer
    lexicon = _line(app, "lexicon-run")
    assert "parked" in lexicon
    assert "!" not in lexicon  # protect is no longer shown
    assert "leave this session" in lexicon
    assert "Agent Systems" in lexicon
    fresh = _line(app, "fresh-shell")
    assert "*" in fresh
    assert "live" in fresh
    assert "Shell" in fresh
    assert "adopted from the sidebar" in fresh
    assert fresh.rstrip().endswith("Inbox")
    shell = _line(app, "6.S978 HW1")
    assert "Shell" in shell
    assert "parked" in shell
    inbox_folder = _line(app, "Inbox")
    assert "not in a folder" in inbox_folder
    week = _line(app, "margin-notes", "Course")
    inbox = _line(app, "margin-notes", "Inbox")
    assert week != inbox
    assert text.index("sample-split") < text.index("mark-finder")
    assert text.index("signer-lab") < text.index("idle-map")
    assert text.index("fresh-shell") < text.index("paper-queue")
    course = next(line for line in text.splitlines() if line.startswith(">▾") and "Course" in line)
    assert "1 live" in course and "3 parked" in course


def test_same_system_when_a_folder_is_closed(desk):
    app = make_app(desk)
    app.focus_folder("folder-course")
    app.handle("h")
    assert "sample-split" not in app.text()
    closed = next(line for line in app.text().splitlines() if line.startswith(">") and "▸ Course" in line)
    assert "1 live" in closed
    assert "3 parked" in closed
    app.handle("l")
    assert "sample-split" in app.text()
    app.focus_berth("berth-sample")
    app.handle("h")
    assert app.cursor == ("folder", "folder-course")
    assert "sample-split" not in app.text()
    app.focus_folder("folder-inbox")
    app.handle("h")
    assert "fresh-shell" not in app.text()
    inbox = _line(app, "Inbox")
    assert " new" not in inbox and " new" not in app.lines()[0].text


def test_new_row_appears_on_reload(desk):
    app = make_app(desk)
    conn = sqlite3.connect(desk)
    insert_berth(
        conn,
        id="berth-late",
        folder_id="folder-inbox",
        title="late-shell",
        state="live",
        cwd="/desk/inbox",
        note="filed while the screen was open",
        session="fixture-session-late",
        created_at="2026-10-02T21:10:00Z",
        played_at="2026-10-02T21:10:00Z",
        parked_at=None,
        workspace="fixture-workspace-late",
    )
    conn.commit()
    conn.close()
    app.focus_berth("berth-mark")
    app.reload()
    assert app.cursor == ("berth", "berth-mark")
    assert "late-shell" in app.text()
    app.focus_folder("folder-inbox")
    app.handle("h")
    assert "late-shell" not in app.text()


def test_search_filters_and_keeps_the_header(desk):
    app = make_app(desk)
    search(app, "blank line")
    assert "mark-finder" in app.text()
    assert "sample-split" not in app.text()
    assert "4 live" in app.lines()[0].text
    assert "8 parked" in app.lines()[0].text
    clear(app)
    search(app, "methods")
    assert "method-draft" in app.text()
    assert "mark-finder" not in app.text()
    clear(app)
    search(app, "signal-bench")
    assert "method-draft" in app.text()
    assert "sample-split" not in app.text()
    clear(app)
    search(app, "SIGNER")
    assert "signer-lab" in app.text()
    assert "fresh-shell" not in app.text()
    clear(app)
    search(app, "Inbox")
    assert "fresh-shell" in app.text()
    assert "paper-queue" in app.text()
    assert "sample-split" not in app.text()
    clear(app)
    search(app, "no-such-berth-xyz")
    assert "no match" in app.text()
    assert "4 live" in app.lines()[0].text


def test_archive_hides_parked_and_keeps_live(desk):
    conn = sqlite3.connect(desk)
    insert_folder(conn, "folder-old", None, "Old", 9, archived=1)
    insert_berth(
        conn,
        id="berth-ghost",
        folder_id="folder-old",
        title="ghost-row",
        state="parked",
        note="closed work",
        session="fixture-session-ghost",
    )
    insert_berth(
        conn,
        id="berth-still",
        folder_id="folder-old",
        title="still-up",
        state="live",
        cwd="/desk/old",
        note="still running",
        session="fixture-session-still",
        workspace="fixture-workspace-still",
        parked_at=None,
        played_at="2026-10-01T15:00:00Z",
    )
    conn.commit()
    conn.close()
    app = make_app(desk, height=24)
    assert "5 live" in app.lines()[0].text
    assert "8 parked" in app.lines()[0].text
    assert "still-up" in app.text()
    assert "ghost-row" not in app.text()
    assert "archived" in _line(app, "Old")
    search(app, "ghost-row")
    body = "\n".join(line.text for line in app.lines()[2:-1])
    assert "ghost-row" not in body
    clear(app)
    search(app, "still-up")
    assert "still-up" in app.text()


def test_long_note_is_cut(desk):
    conn = sqlite3.connect(desk)
    conn.execute("UPDATE berth SET note = ? WHERE id = 'berth-sample'", ("n" * 80,))
    conn.commit()
    conn.close()
    app = make_app(desk)
    assert "…" in _line(app, "sample-split")


def test_narrow_frame_still_draws(desk):
    app = make_app(desk)
    app.set_size(48, 10)
    assert len(app.lines()) == 10
    assert "live" in app.text()
    assert "parked" in app.text()
    app.handle("j")
    app.handle("/")
    app.handle("a")
    app.set_size(20, 4)
    assert len(app.lines()) == 4
    app.text()


def test_playing_is_counted_and_blocks_keys(desk):
    conn = sqlite3.connect(desk)
    conn.execute("UPDATE berth SET state = 'playing' WHERE id = 'berth-mark'")
    conn.commit()
    conn.close()
    app = make_app(desk)
    assert "1 playing" in app.lines()[0].text
    assert "7 parked" in app.lines()[0].text
    app.focus_berth("berth-mark")
    app.handle("enter")
    assert app.snap.intents == []
    assert "in progress" in app.message
    app.handle("p")
    assert app.snap.intents == []
    assert "in progress" in app.message


def test_look_matches_the_rendered_frames(desk):
    look = (Path(__file__).resolve().parents[1] / "docs" / "LOOK.md").read_text(encoding="utf-8")
    app = make_app(desk)
    for line in app.lines():
        if line.text.strip():
            assert line.text.rstrip() in look
    app.focus_berth("berth-mark")
    app.handle("space")
    for line in app.lines():
        if line.text.strip():
            assert line.text.rstrip() in look
    assert "does not play" in look
    assert "y records pause" in look


def _line(app, token: str, also: str | None = None) -> str:
    matches = []
    for line in app.text().splitlines():
        if token in line and (also is None or also in line):
            matches.append(line)
    assert matches, token
    return matches[0]
