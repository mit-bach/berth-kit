import json
import sqlite3

from berth.fixture import insert_berth
from berth.render import banner_text
from tests.support import FROZEN, make_app


def test_j_and_k_move(desk):
    app = make_app(desk)
    first = app.cursor
    app.handle("k")
    assert app.cursor == first
    app.handle("j")
    assert app.cursor != first
    app.handle("k")
    assert app.cursor == first


def test_enter_on_parked_records_play_and_leaves_state(desk):
    app = make_app(desk)
    app.focus_berth("berth-mark")
    app.handle("enter")
    app.handle("enter")
    assert [item.op for item in app.snap.intents] == ["play"]
    assert app.snap.intents[0].berth_id == "berth-mark"
    assert app.snap.intents[0].at == "2026-10-02T21:30:00Z"
    assert "already recorded" in app.message
    assert app.snap.berths["berth-mark"].state == "parked"
    conn = sqlite3.connect(desk)
    row = conn.execute("SELECT op, berth_id, detail FROM intent").fetchone()
    conn.close()
    assert row[0] == "play"
    assert row[1] == "berth-mark"
    assert json.loads(row[2])["kind"] == "grok"


def test_enter_on_a_shell_says_it_opens_a_shell(desk):
    app = make_app(desk)
    app.focus_berth("berth-hw1")
    app.handle("enter")
    assert [item.op for item in app.snap.intents] == ["play"]
    assert app.snap.intents[0].detail["kind"] == "shell"
    assert "opens a shell, not a Grok session" in app.message
    assert app.snap.berths["berth-hw1"].state == "parked"
    app.handle("enter")
    assert "already recorded" in app.message
    assert "opens a shell, not a Grok session" in app.message


def test_enter_on_live_records_focus(desk):
    app = make_app(desk)
    app.focus_berth("berth-sample")
    app.handle("enter")
    assert app.snap.intents[0].op == "focus"
    assert app.snap.berths["berth-sample"].state == "live"
    assert FROZEN.isoformat()


def test_space_shows_excerpt_and_does_not_play(desk):
    app = make_app(desk)
    app.focus_berth("berth-mark")
    app.handle("space")
    assert app.mode == "excerpt"
    text = app.text()
    assert "does not play" in text
    assert "the mark is the blank line" in text
    assert "fixture-session-mark" in text
    assert "recorded 1.0.44" in text
    assert app.snap.intents == []
    assert app.snap.berths["berth-mark"].state == "parked"
    app.handle("enter")
    assert app.mode == "excerpt"
    assert app.snap.intents == []
    app.handle("space")
    assert app.mode == "tree"
    app.focus_berth("berth-fresh")
    app.handle("space")
    assert "no screen text stored" in app.text()
    assert app.snap.intents == []


def test_excerpt_scroll_does_not_record(desk):
    conn = sqlite3.connect(desk)
    excerpt = "\n".join(f"line-{index:02d}" for index in range(40))
    conn.execute("UPDATE berth SET excerpt = ? WHERE id = 'berth-mark'", (excerpt,))
    conn.commit()
    conn.close()
    app = make_app(desk)
    app.focus_berth("berth-mark")
    app.handle("space")
    assert "line-00" in app.text()
    app.handle("j")
    assert "line-00" not in app.text()
    assert "line-01" in app.text()
    assert app.snap.intents == []
    app.reload()
    assert app.mode == "excerpt"


def test_quiet_pause_records_intent(desk):
    app = make_app(desk)
    app.focus_berth("berth-sample")
    app.handle("p")
    app.focus_berth("berth-sample")  # p moved the cursor to the session above; come back for the repeat
    app.handle("p")
    assert app.mode == "tree"
    assert [item.op for item in app.snap.intents] == ["pause"]
    assert app.snap.intents[0].detail["busy"] is False
    assert app.snap.intents[0].detail["confirm"] is False
    assert app.snap.berths["berth-sample"].state == "live"
    assert "already recorded" in app.message


def test_a_busy_session_asks_first_and_y_closes_it_anyway(desk):
    """2026-10-29: c on a busy or unclear session was recorded unconfirmed and refused; now p, S, and c ask."""
    for key, op in (("p", "pause"), ("c", "cycle"), ("S", "sleep")):
        app = make_app(desk)
        app.focus_berth("berth-signer")
        app.handle(key)
        assert app.mode == "confirm-busy", key
        assert "busy · " in banner_text(app, app.current) and f"y {op}s it anyway" in banner_text(app, app.current)
        app.handle("n")
        assert app.mode == "tree" and not [item for item in app.snap.intents if item.op == op]
        app.focus_berth("berth-signer")
        app.handle(key)
        app.handle("y")
        recorded = [item for item in app.snap.intents if item.op == op]
        assert recorded and recorded[0].detail["confirm"] is True and recorded[0].detail["busy"] is True
        assert app.snap.berths["berth-signer"].state == "live"
        conn = sqlite3.connect(desk)
        conn.execute("DELETE FROM intent")
        conn.commit()
        conn.close()


def test_a_pin_does_not_refuse_pause(desk):
    app = make_app(desk)
    app.focus_berth("berth-sample")
    app.handle("P")
    app.handle("p")
    assert app.snap.berths["berth-sample"].pinned is True
    assert app.snap.intents[0].op == "pause"
    assert app.snap.berths["berth-sample"].state == "live"


def test_protected_mark_is_on_the_lexicon_row(desk):
    app = make_app(desk)
    app.focus_berth("berth-lexicon")
    app.handle("p")
    assert app.snap.intents == []
    assert "already parked" in app.message
    assert "pause refused" not in app.message
    assert app.snap.berths["berth-lexicon"].protect is True


def test_process_play_and_pause_wait(desk):
    conn = sqlite3.connect(desk)
    insert_berth(
        conn,
        id="berth-dev",
        folder_id="folder-inbox",
        title="dev-server",
        kind="process",
        state="parked",
        cwd="/desk/inbox/server",
        note="local server",
        recipe_argv='["python","-m","http.server"]',
        recipe_display="python -m http.server",
        agent_name=None,
        session=None,
        version=None,
    )
    conn.commit()
    conn.close()
    app = make_app(desk)
    app.focus_berth("berth-dev")
    app.handle("enter")
    assert app.mode == "confirm-play"
    assert "python -m http.server" in app.text()
    assert "y records play" in app.text()
    app.handle("n")
    assert app.mode == "tree"
    assert app.snap.intents == []
    app.handle("enter")
    app.handle("y")
    assert app.snap.intents[0].op == "play"
    assert app.snap.intents[0].detail["confirm"] is True
    assert app.snap.berths["berth-dev"].state == "parked"
    conn = sqlite3.connect(desk)
    conn.execute("UPDATE berth SET state = 'live' WHERE id = 'berth-dev'")
    conn.commit()
    conn.close()
    app.reload()
    app.focus_berth("berth-dev")
    app.handle("p")
    assert app.mode == "confirm"
    app.handle("y")
    pause = [item for item in app.snap.intents if item.op == "pause"]
    assert pause[0].detail["confirm"] is True
    assert pause[0].detail["busy"] is False
    assert pause[0].detail["kind"] == "process"
    assert app.snap.berths["berth-dev"].state == "live"


def test_capital_p_pins_and_unpins_in_sqlite_and_bang_does_nothing(desk):
    from berth.db import Catalog

    Catalog(desk).open().close()  # the fixture gains the pinned column on first open
    app = make_app(desk)
    app.focus_folder("folder-inbox")
    app.handle("P")
    assert "P pins a session" in app.message
    app.focus_berth("berth-sample")
    app.handle("!")
    assert app.snap.berths["berth-sample"].pinned is False and "!" not in _row(app, "sample-split")
    app.handle("P")
    assert app.snap.berths["berth-sample"].pinned is True and "pinned" in app.message
    assert "📌" in _row(app, "sample-split")
    app.handle("P")
    assert app.snap.berths["berth-sample"].pinned is False and "unpinned" in app.message
    conn = sqlite3.connect(desk)
    kinds = [row[0] for row in conn.execute(
        "SELECT kind FROM event WHERE berth_id = 'berth-sample' AND kind IN ('pin', 'unpin') ORDER BY rowid")]
    conn.close()
    assert kinds == ["pin", "unpin"]


def test_n_on_a_berth_makes_a_subfolder_of_its_folder(desk):
    app = make_app(desk)
    app.focus_berth("berth-sample")
    app.handle("n")
    assert app.mode == "mkdir"
    assert app.prompt == ""
    assert "new folder in Course/" in app.text()
    for char in "figures":
        app.handle(char)
    app.handle("enter")
    assert app.mode == "tree"
    assert app.current.title == "figures"
    conn = sqlite3.connect(desk)
    parent = conn.execute("SELECT parent_id FROM folder WHERE name = 'figures'").fetchone()[0]
    assert parent == "folder-course"
    assert "mkdir" in [row[0] for row in conn.execute("SELECT kind FROM event")]
    conn.close()


def test_n_with_a_leading_slash_makes_a_root_and_refuses_a_duplicate(desk):
    app = make_app(desk)
    app.focus_folder("folder-course")
    app.handle("n")
    for char in "/figures":
        app.handle(char)
    assert "new folder in / " in app.text()
    app.handle("enter")
    conn = sqlite3.connect(desk)
    assert conn.execute("SELECT parent_id FROM folder WHERE name = 'figures'").fetchone()[0] is None
    conn.close()
    app.focus_folder("folder-course")
    app.handle("n")
    for char in "/Course":
        app.handle(char)
    app.handle("enter")
    assert app.mode == "mkdir"
    assert "folder exists" in app.message
    conn = sqlite3.connect(desk)
    assert conn.execute("SELECT count(*) FROM folder WHERE name = 'Course'").fetchone()[0] == 1
    conn.close()
    app.handle("esc")
    assert app.mode == "tree"


def test_n_makes_a_nested_path_inside_the_folder(desk):
    app = make_app(desk, height=40)
    app.focus_folder("folder-course")
    app.handle("n")
    for char in "Labs/Week 1":
        app.handle(char)
    app.handle("enter")
    assert app.mode == "tree"
    conn = sqlite3.connect(desk)
    labs = conn.execute("SELECT id, parent_id FROM folder WHERE name = 'Labs'").fetchone()
    week = conn.execute("SELECT parent_id FROM folder WHERE name = 'Week 1'").fetchone()
    conn.close()
    assert labs[1] == "folder-course"
    assert week[0] == labs[0]
    assert "folder · Course/Labs/Week 1" in app.text()


def test_n_inside_inbox_makes_a_root(desk):
    app = make_app(desk)
    app.focus_folder("folder-inbox")
    app.handle("n")
    for char in "Loose":
        app.handle(char)
    app.handle("enter")
    conn = sqlite3.connect(desk)
    assert conn.execute("SELECT parent_id FROM folder WHERE name = 'Loose'").fetchone()[0] is None
    conn.close()


def _new_folder(app, where, name):
    app.focus_folder(where)
    app.handle("n")
    for char in name:
        app.handle(char)
    app.handle("enter")
    return app.current.id


def _arrow_to(app, folder_id, key="down"):
    for _ in range(200):
        if app.current is not None and app.current.kind == "folder" and app.current.id == folder_id:
            return
        app.handle(key)
    raise AssertionError("never reached the folder with the arrow keys")


def _parent(desk, folder_id):
    conn = sqlite3.connect(desk)
    try:
        return conn.execute("SELECT parent_id FROM folder WHERE id = ?", (folder_id,)).fetchone()[0]
    finally:
        conn.close()


def test_m_picks_a_folder_up_and_m_on_another_folder_drops_it_inside(desk):
    app = make_app(desk, height=60)
    labs = _new_folder(app, "folder-course", "/Labs")
    app.focus_folder(labs)
    app.handle("m")
    assert app.marked_folders == {labs}
    assert "moving Labs" in app.message
    assert "v moving" in app.text()
    app.handle("g")
    _arrow_to(app, "folder-course")
    assert "m drops 1 row into Course" in app.text().splitlines()[1]
    app.handle("m")
    assert _parent(desk, labs) == "folder-course"
    assert app.marked_folders == set()
    assert "moved · 1 · Course" in app.message


def test_dropping_a_folder_into_itself_is_refused_and_inbox_means_top_level(desk):
    app = make_app(desk, height=60)
    labs = _new_folder(app, "folder-course", "Labs")
    assert _parent(desk, labs) == "folder-course"
    app.focus_folder("folder-course")
    app.handle("m")
    _arrow_to(app, labs)
    app.handle("m")
    assert "refused" in app.message and "inside itself" in app.message
    assert _parent(desk, "folder-course") is None
    app.focus_folder(labs)
    app.handle("m")
    app.handle("G")
    _arrow_to(app, "folder-inbox", key="up")
    assert "into the top level" in app.text().splitlines()[1]
    app.handle("m")
    assert _parent(desk, labs) is None


def test_v_marks_folders_and_sessions_and_esc_cancels(desk):
    app = make_app(desk, height=60)
    labs = _new_folder(app, "folder-course", "/Labs")
    app.focus_folder(labs)
    app.handle("v")
    app.focus_berth("berth-sample")
    app.handle("v")
    assert "2 marked" in app.text().splitlines()[0]
    app.handle("esc")
    assert app.message == "move cancelled"
    assert not app.marked and not app.marked_folders
    app.focus_folder("folder-inbox")
    app.handle("m")
    assert app.message == "Inbox stays"


def test_r_renames_the_folder_under_the_cursor(desk):
    app = make_app(desk)
    app.focus_berth("berth-sample")
    app.handle("r")
    assert "r renames a folder" in app.message
    app.focus_folder("folder-course")
    app.handle("r")
    assert app.mode == "rename"
    assert app.prompt == "Course"
    app.handle("backspace")
    assert app.prompt == "Cours"
    for char in "e lab":
        app.handle(char)
    app.handle("enter")
    assert app.mode == "tree"
    assert app.current.title == "Course lab"
    conn = sqlite3.connect(desk)
    row = conn.execute("SELECT parent_id, name FROM folder WHERE id = 'folder-course'").fetchone()
    conn.close()
    assert row[0] is None
    assert row[1] == "Course lab"


def test_n_backspace_and_cancel(desk):
    app = make_app(desk)
    app.focus_berth("berth-sample")
    app.handle("n")
    for char in "ab":
        app.handle(char)
    app.handle("backspace")
    assert app.prompt == "a"
    app.handle("esc")
    assert app.mode == "tree"
    assert app.message == "cancelled"


def test_m_drops_marks_onto_the_folder_and_clears_them(desk):
    app = make_app(desk)
    app.focus_berth("berth-paper")
    app.handle("v")
    app.focus_berth("berth-margin-inbox")
    app.handle("v")
    assert app.marked == {"berth-paper", "berth-margin-inbox"}
    app.handle("m")
    assert "destination folder" in app.message
    assert app.snap.berths["berth-paper"].folder_id != "folder-course"
    app.focus_folder("folder-course")
    app.handle("m")
    assert app.snap.berths["berth-paper"].folder_id == "folder-course"
    assert app.snap.berths["berth-margin-inbox"].folder_id == "folder-course"
    assert app.marked == set()
    assert app.mode == "tree"
    assert "moved · 2 · Course" in app.message
    conn = sqlite3.connect(desk)
    kinds = [row[0] for row in conn.execute("SELECT kind FROM event WHERE berth_id = 'berth-paper'")]
    conn.close()
    assert "move" in kinds


def test_q_quits_only_from_the_tree(desk):
    app = make_app(desk)
    app.handle("/")
    app.handle("q")
    assert app.quit is False
    assert app.query == "q"
    app.handle("esc")
    app.handle("?")
    assert "move" in app.text()
    app.handle("q")
    assert app.quit is False
    assert app.mode == "tree"
    app.handle("q")
    assert app.quit is True


def test_help_lists_the_keys(desk):
    app = make_app(desk)
    app.handle("?")
    assert app.mode == "help"
    text = app.text()
    assert "records play" in text or "play" in text
    assert "pin or unpin" in text
    app.handle("esc")
    assert app.mode == "tree"
    assert "sample-split" in app.text()
    app.handle("?")
    app.handle("x")
    assert app.mode == "tree"
    app.handle("?")
    app.handle("space")
    assert app.mode == "tree"
    app.handle("?")
    app.handle("enter")
    assert app.mode == "tree"


def test_d_deletes_a_folder_and_files_its_berths_in_inbox(desk):
    app = make_app(desk)
    app.focus_folder("folder-course")
    app.handle("d")
    assert app.mode == "confirm-rmdir"
    assert "Course" in app.delete_banner
    assert "Inbox" in app.delete_banner
    app.handle("n")
    assert app.mode == "tree"
    assert app.message == "delete cancelled"
    app.focus_folder("folder-course")
    app.handle("d")
    app.handle("y")
    assert app.mode == "tree"
    assert "deleted folder · Course" in app.message
    assert "moved to Inbox" in app.message
    conn = sqlite3.connect(desk)
    assert conn.execute("SELECT count(*) FROM folder WHERE id = 'folder-course'").fetchone()[0] == 0
    filed = {
        row[0]
        for row in conn.execute(
            """
            SELECT folder_id FROM berth
            WHERE id IN ('berth-sample', 'berth-mark', 'berth-method', 'berth-margin-week')
            """
        )
    }
    live = conn.execute(
        "SELECT state, cmux_workspace_id FROM berth WHERE id = 'berth-sample'"
    ).fetchone()
    conn.close()
    assert filed == {"folder-inbox"}
    assert live == ("live", "fixture-workspace-sample")
    assert app.cursor == ("folder", "folder-inbox")


def test_d_on_inbox_leaves_the_folder(desk):
    app = make_app(desk)
    app.focus_folder("folder-inbox")
    app.handle("d")
    assert app.mode == "tree"
    assert app.message == "Inbox stays"
    conn = sqlite3.connect(desk)
    assert conn.execute("SELECT count(*) FROM folder WHERE id = 'folder-inbox'").fetchone()[0] == 1
    conn.close()


def test_marked_d_still_deletes_the_berth_when_the_cursor_is_on_a_folder(desk):
    app = make_app(desk)
    app.focus_berth("berth-mark")
    app.handle("v")
    app.focus_folder("folder-course")
    app.handle("d")
    assert app.mode == "tree"
    conn = sqlite3.connect(desk)
    assert conn.execute("SELECT count(*) FROM berth WHERE id = 'berth-mark'").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM folder WHERE id = 'folder-course'").fetchone()[0] == 1
    conn.close()


def test_folder_keys_do_not_pretend(desk):
    app = make_app(desk)
    app.focus_folder("folder-course")
    app.handle("enter")
    assert "berth" in app.message
    app.handle("space")
    assert "berth" in app.message
    assert app.snap.intents == []


def _row(app, token: str) -> str:
    return next(line for line in app.text().splitlines() if token in line)


def test_f_reaches_the_sleeping_and_the_parked_filters(desk):
    from berth.db import Catalog

    Catalog(desk).open().close()
    conn = sqlite3.connect(desk)
    for berth_id, title, reason in (("quit-1", "lost-in-quit", "cmux_quit"), ("paused-1", "paused-by-me", "pause")):
        insert_berth(conn, id=berth_id, folder_id="folder-inbox", title=title, state="parked")
        conn.execute("UPDATE berth SET park_reason = ? WHERE id = ?", (reason, berth_id))
    conn.commit()
    conn.close()
    app = make_app(desk, height=40)
    for _ in range(10):
        if app.filter == "sleeping":
            break
        app.handle("f")
    text = app.text()
    assert "filter sleeping" in text
    assert "lost-in-quit" in text  # a cmux quit closed it; it sleeps until it wakes
    assert "paused-by-me" not in text
    app.handle("f")
    text = app.text()
    assert "filter parked" in text and "paused-by-me" in text and "lost-in-quit" not in text


def test_f_reaches_live_plus_sleeping(desk):
    from berth.db import Catalog

    Catalog(desk).open().close()
    conn = sqlite3.connect(desk)
    for berth_id, title, reason in (("quit-1", "lost-in-quit", "cmux_quit"), ("slept-1", "slept-by-me", "sleep"),
                                    ("paused-1", "paused-by-me", "pause")):
        insert_berth(conn, id=berth_id, folder_id="folder-inbox", title=title, state="parked")
        conn.execute("UPDATE berth SET park_reason = ? WHERE id = ?", (reason, berth_id))
    conn.commit()
    conn.close()
    app = make_app(desk, height=40)
    for _ in range(10):
        if app.filter == "live + sleeping":
            break
        app.handle("f")
    text = app.text()
    assert "filter live + sleeping" in text
    assert "lost-in-quit" in text and "slept-by-me" in text and "paused-by-me" not in text
    live = [b.title for b in app.snap.berths.values() if b.state == "live"]
    assert live and all(title in text for title in live)


def test_the_hint_on_a_folder_names_both_folder_moves(desk):
    app = make_app(desk, width=140)
    app.focus_folder("folder-course")
    hint = app.text().splitlines()[1]
    assert "n new folder inside Course" in hint
    assert "m pick up Course to move it" in hint
    app.focus_folder("folder-inbox")
    assert "inside Inbox" not in app.text().splitlines()[1]


def _catalog_with(desk, rows):
    """Migrate the fixture, then add (id, folder, title, state, last_used) rows."""
    from berth.db import Catalog

    Catalog(desk).open().close()
    conn = sqlite3.connect(desk)
    for berth_id, folder, title, state, used in rows:
        insert_berth(conn, id=berth_id, folder_id=folder, title=title, state=state,
                     workspace=f"ws-{berth_id}" if state == "live" else None)
        conn.execute("UPDATE berth SET last_used_at = ? WHERE id = ?", (used, berth_id))
    conn.commit()
    return conn


def _folder_named(desk, name, parent):
    conn = sqlite3.connect(desk)
    conn.execute(
        "INSERT INTO folder (id, parent_id, name, sort, note, archived) VALUES (?, ?, ?, 99, '', 0)",
        (f"folder-{name}", parent, name),
    )
    conn.commit()
    conn.close()
    return f"folder-{name}"


def test_p_on_a_folder_parks_everything_in_it_and_its_subfolders(desk):
    labs = _folder_named(desk, "labs", "folder-course")
    deep = _folder_named(desk, "deep", labs)
    conn = _catalog_with(desk, [
        ("in-labs", labs, "labs-run", "live", "2026-10-01T00:00:00.000000Z"),
        ("in-deep", deep, "deep-run", "live", "2026-10-02T00:00:00.000000Z"),
        ("parked-deep", deep, "old-run", "parked", "2026-09-01T00:00:00.000000Z"),
    ])
    conn.close()
    app = make_app(desk, height=60)
    app.focus_folder("folder-course")
    app.handle("p")
    assert app.mode == "confirm-park"
    live_in_course = {"in-labs", "in-deep"} | {
        b.id for b in app.snap.berths.values() if b.folder_id == "folder-course" and b.state == "live"
    }
    assert f"park · {len(live_in_course)} live sessions in Course and its 2 subfolders" in app.text()
    app.handle("n")
    assert app.snap.intents == []
    app.handle("p")
    app.handle("y")
    paused = {item.berth_id for item in app.snap.intents if item.op == "pause"}
    assert paused == live_in_course
    assert "parked-deep" not in paused
    assert "park recorded" in app.message


def test_p_on_a_folder_with_nothing_live_says_so(desk):
    empty = _folder_named(desk, "empty", None)
    app = make_app(desk, height=60)
    app.focus_folder(empty)
    app.handle("p")
    assert app.mode == "tree"
    assert "nothing live" in app.message


def test_recent_sort_interleaves_folders_and_sessions_and_s_toggles(desk):
    root_a = _folder_named(desk, "Alpha", None)
    root_b = _folder_named(desk, "Beta", None)
    sub_b = _folder_named(desk, "Beta-sub", root_b)
    conn = _catalog_with(desk, [
        ("a-old", root_a, "a-old", "parked", "2026-01-01T00:00:00.000000Z"),
        ("b-mid", root_b, "b-mid", "parked", "2026-05-01T00:00:00.000000Z"),
        ("b-sub-new", sub_b, "b-sub-new", "parked", "2027-01-01T00:00:00.000000Z"),
    ])
    conn.close()
    app = make_app(desk, height=80)
    titles = [row.title for row in app.visible]
    # Beta holds the newest session anywhere, so it is the first root.
    assert titles.index("Beta") < titles.index("Alpha")
    # Inside Beta the subfolder, whose session is newer, comes before b-mid.
    assert titles.index("Beta-sub") < titles.index("b-mid")
    assert "last used" in app.text().splitlines()[0]
    app.handle("s")
    assert app.sort == "name"
    assert "by name" in app.text().splitlines()[0]
    app.handle("s")
    assert app.sort == "recent"


def test_enter_on_marks_opens_claude_grok_and_shells_but_not_processes(desk):
    conn = sqlite3.connect(desk)
    insert_berth(conn, id="m-grok", folder_id="folder-inbox", title="m-grok", state="parked")
    insert_berth(conn, id="m-claude", folder_id="folder-inbox", title="m-claude", state="parked",
                 kind="other-agent", agent_name="claude")
    insert_berth(conn, id="m-shell", folder_id="folder-inbox", title="m-shell", state="parked",
                 kind="shell", session=None)
    insert_berth(conn, id="m-proc", folder_id="folder-inbox", title="m-proc", state="parked",
                 kind="process", session=None, recipe_argv='["python","-m","http.server"]')
    conn.commit()
    conn.close()
    app = make_app(desk, height=60)
    for berth_id in ("m-grok", "m-claude", "m-shell", "m-proc"):
        app.focus_berth(berth_id)
        app.handle("v")
    app.handle("enter")
    plays = [item for item in app.snap.intents if item.op == "play"]
    assert sorted(item.berth_id for item in plays) == ["m-claude", "m-grok", "m-shell"]
    focused = [json.loads(item.detail)["focus"] if isinstance(item.detail, str) else item.detail["focus"] for item in plays]
    assert sorted(focused) == [False, False, True]
    assert app.marked == set()
    assert "play recorded · 3 sessions" in app.message and "1 process left" in app.message


def test_c_records_a_cycle_on_a_live_session_and_not_on_a_parked_one(desk):
    app = make_app(desk)
    app.focus_berth("berth-sample")
    app.handle("c")
    app.handle("c")
    assert [item.op for item in app.snap.intents] == ["cycle"]
    assert app.snap.intents[0].detail["confirm"] is False
    assert "already recorded" in app.message
    assert app.snap.berths["berth-sample"].state == "live"
    app.focus_berth("berth-mark")
    app.handle("c")
    assert "enter opens it" in app.message
    assert [item.op for item in app.snap.intents] == ["cycle"]


def test_c_with_marks_cycles_every_marked_live_session(desk):
    app = make_app(desk)
    for berth_id in ("berth-sample", "berth-signer", "berth-mark"):
        app.focus_berth(berth_id)
        app.handle("v")
    app.handle("c")
    assert app.mode == "confirm-cycle-many" and "1 busy (" in app.delete_banner  # berth-signer is busy
    app.handle("y")
    assert sorted(item.berth_id for item in app.snap.intents if item.op == "cycle") == ["berth-sample", "berth-signer"]
    assert all(item.detail["confirm"] is True for item in app.snap.intents if item.op == "cycle")
    assert app.marked == set()
    assert "cycle recorded · 2 sessions" in app.message


def test_c_on_a_process_asks_first(desk):
    conn = sqlite3.connect(desk)
    insert_berth(
        conn, id="berth-dev", folder_id="folder-inbox", title="dev-server", kind="process", state="live",
        cwd="/desk/inbox/server", note="", recipe_argv='["python","-m","http.server"]',
        recipe_display="python -m http.server", agent_name=None, session=None, version=None,
    )
    conn.commit()
    conn.close()
    app = make_app(desk)
    app.focus_berth("berth-dev")
    app.handle("c")
    assert app.mode == "confirm-cycle"
    assert "y records cycle" in app.text()
    app.handle("n")
    assert app.snap.intents == []
    app.handle("c")
    app.handle("y")
    assert app.snap.intents[0].op == "cycle" and app.snap.intents[0].detail["confirm"] is True


def test_naming_a_folder_types_every_letter_and_the_cursor_follows_spaces(desk):
    app = make_app(desk)
    app.focus_folder("folder-course")
    before = app.current
    app.handle("n")
    for key in ["j", "k", "space", "q", "space"]:
        app.handle(key)
    assert app.prompt == "jk q "
    assert app.current == before
    line = app.lines()[1]
    assert line.role == "prompt" and line.text.endswith("jk q ")
    assert line.cursor == len(line.text)


def test_the_name_box_edits_in_place_and_takes_a_paste(desk):
    app = make_app(desk, height=40)
    app.focus_folder("folder-course")
    app.handle("n")
    app.handle("paste:Week\n2")
    for key in ["left", "left", "backspace", "space", "space", "end", "shift-left"]:
        app.handle(key)
    assert app.prompt == "Wee   2"
    line = app.lines()[1]
    assert [span[2] for span in line.spans] == ["select"]
    app.handle("1")
    app.handle("enter")
    assert app.current.title == "Wee   1"


def test_search_walks_the_list_with_up_and_down_and_edits_with_left_and_right(desk):
    app = make_app(desk)
    app.handle("/")
    for key in "cours":
        app.handle(key)
    app.handle("left")
    app.handle("x")
    assert app.query == "courxs"
    start = app.cursor
    app.handle("down")
    assert app.query == "courxs"
    assert app.cursor != start or len(app.visible) <= 1
    app.handle("esc")
    assert app.query == "" and app.mode == "tree"


def test_a_mouse_drag_in_the_name_box_selects_and_backspace_deletes_it(desk):
    from berth.render import box_origin

    app = make_app(desk)
    app.focus_folder("folder-course")
    app.handle("n")
    app.handle("paste:hello world")
    app.lines()
    origin = box_origin(app)
    app.handle(f"mouse:press:{origin + 6}:1")
    app.handle(f"mouse:drag:{origin + 11}:1")
    app.handle(f"mouse:release:{origin + 11}:1")
    assert app.editor.selected() == "world"
    app.handle("backspace")
    assert app.prompt == "hello "
    app.handle(f"mouse:press:{origin + 1}:5")
    assert app.editor.cursor == len("hello ")


def test_inbox_keeps_its_name(desk):
    app = make_app(desk)
    app.focus_folder("folder-inbox")
    app.handle("r")
    assert app.mode == "tree" and "Inbox keeps its name" in app.message


def test_the_screen_says_why_a_play_failed(desk):
    import json as _json
    from datetime import datetime, timezone

    app = make_app(desk)
    app.focus_berth("berth-mark")
    app.handle("enter")
    intent = app.snap.intents[0]
    # The engine fails the play and drops the intent.
    conn = sqlite3.connect(desk)
    conn.execute("DELETE FROM intent WHERE id = ?", (intent.id,))
    conn.execute(
        "INSERT INTO event (id, berth_id, at, kind, detail) VALUES ('ev-x', ?, ?, 'intent_failed', ?)",
        (intent.berth_id, datetime.now(timezone.utc).isoformat(),
         _json.dumps({"intent_id": intent.id, "op": "play", "code": "SESSION_MISSING",
                      "message": "no conversation 3f9d024c to resume"})),
    )
    conn.commit()
    conn.close()
    app.reload()
    assert app.message.startswith("play failed · ")
    assert "no conversation 3f9d024c to resume" in app.message
    app.reload()
    assert "no conversation" in app.message


def _session_above(app, berth_id):
    index = next(i for i, row in enumerate(app.visible) if row.kind == "berth" and row.id == berth_id)
    return next((row for row in reversed(app.visible[:index]) if row.kind == "berth"), None)


def test_parking_moves_the_cursor_to_the_session_above(desk):
    app = make_app(desk, height=40)
    live = [row for row in app.visible if row.kind == "berth" and row.state == "live" and _session_above(app, row.id)]
    target = live[-1]
    above = _session_above(app, target.id)
    app.focus_berth(target.id)
    app.handle("p")
    if app.mode == "confirm-busy":
        app.handle("y")
    assert app.snap.intents[0].op == "pause"
    assert app.cursor == ("berth", above.id)


def test_in_the_live_filter_the_cursor_stays_put_when_the_parked_row_leaves(desk):
    app = make_app(desk, height=40)
    while app.filter != "live":
        app.handle("f")
    live = [row for row in app.visible if row.kind == "berth" and _session_above(app, row.id)]
    target = live[-1]
    above = _session_above(app, target.id)
    app.focus_berth(target.id)
    app.handle("p")
    # The engine parks it; the row drops out of the live filter.
    conn = sqlite3.connect(desk)
    conn.execute("UPDATE berth SET state = 'parked', cmux_workspace_id = NULL WHERE id = ?", (target.id,))
    conn.execute("DELETE FROM intent")
    conn.commit()
    conn.close()
    app.reload()
    assert all(row.id != target.id for row in app.visible)
    assert app.cursor == ("berth", above.id)


def test_a_row_that_vanishes_leaves_the_cursor_on_the_session_above_not_the_top(desk):
    app = make_app(desk, height=40)
    sessions = [row for row in app.visible if row.kind == "berth"]
    target = sessions[-1]
    above = _session_above(app, target.id)
    app.focus_berth(target.id)
    conn = sqlite3.connect(desk)
    conn.execute("DELETE FROM berth WHERE id = ?", (target.id,))
    conn.commit()
    conn.close()
    app.reload()
    assert app.cursor == ("berth", above.id) and app.cursor != (app.visible[0].kind, app.visible[0].id)


def test_the_first_session_falls_back_to_its_folder_line(desk):
    app = make_app(desk, height=40)
    first = next(row for row in app.visible if row.kind == "berth" and row.state == "live")
    assert _session_above(app, first.id) is None  # fresh-shell, right under the Inbox line
    app.focus_berth(first.id)
    app.handle("p")
    assert app.cursor[0] == "folder"


def test_f_cycles_the_state_filters_only_and_the_header_says_berth(desk):
    """2026-10-28: the Grok filter and the "Grok sessions" title were from when every row was Grok."""
    from berth.app import FILTERS

    app = make_app(desk, height=40)
    assert app.lines()[0].text.startswith("Berth ")
    seen = []
    for _ in range(len(FILTERS)):
        app.handle("f")
        seen.append(app.filter)
    assert seen == ["live", "live + idle", "idle", "live + sleeping", "sleeping", "parked", "all"]
    assert "grok" not in FILTERS and "Grok" not in app.lines()[0].text.split("  ")[0]
