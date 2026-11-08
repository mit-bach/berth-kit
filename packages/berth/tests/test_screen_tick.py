import sqlite3

from tests.support import make_app


def test_tick_shows_a_drained_pause(desk):
    app = make_app(desk)
    app.focus_berth("berth-sample")
    app.handle("p")
    assert app.snap.berths["berth-sample"].state == "live"
    assert [item.op for item in app.snap.intents] == ["pause"]
    assert "pause recorded" in app.lines()[-1].text

    conn = sqlite3.connect(desk)
    conn.execute(
        "UPDATE berth SET state = 'parked', cmux_workspace_id = NULL, parked_at = ? WHERE id = ?",
        ("2026-10-02T21:40:00Z", "berth-sample"),
    )
    conn.execute("DELETE FROM intent WHERE berth_id = ? AND op = 'pause'", ("berth-sample",))
    conn.commit()
    conn.close()

    app.reload()

    row = _line(app, "sample-split")
    assert "parked" in row
    assert "live" not in row
    assert app.snap.intents == []
    assert app.snap.berths["berth-sample"].state == "parked"
    assert "pause recorded" not in app.text()
    assert "already recorded" not in app.text()
    check = sqlite3.connect(desk)
    workspace = check.execute(
        "SELECT cmux_workspace_id FROM berth WHERE id = 'berth-sample'"
    ).fetchone()[0]
    check.close()
    assert workspace is None


def test_tick_drops_already_recorded_after_the_drain(desk):
    app = make_app(desk)
    app.focus_berth("berth-sample")
    app.handle("p")
    app.focus_berth("berth-sample")  # p moved the cursor to the session above; come back for the repeat
    app.handle("p")
    assert "already recorded" in app.lines()[-1].text

    conn = sqlite3.connect(desk)
    conn.execute(
        "UPDATE berth SET state = 'parked', cmux_workspace_id = NULL WHERE id = 'berth-sample'"
    )
    conn.execute("DELETE FROM intent")
    conn.commit()
    conn.close()

    app.reload()

    assert "parked" in _line(app, "sample-split")
    assert "already recorded" not in app.text()
    assert "pause recorded" not in app.text()
    assert app.snap.intents == []


def test_tick_shows_a_drained_play(desk):
    app = make_app(desk)
    app.focus_berth("berth-mark")
    app.handle("enter")
    assert app.snap.berths["berth-mark"].state == "parked"
    assert [item.op for item in app.snap.intents] == ["play"]
    assert "play recorded" in app.lines()[-1].text

    conn = sqlite3.connect(desk)
    conn.execute(
        """
        UPDATE berth
        SET state = 'live', cmux_workspace_id = ?, played_at = ?
        WHERE id = ?
        """,
        ("fixture-workspace-mark-next", "2026-10-02T21:41:00Z", "berth-mark"),
    )
    conn.execute("DELETE FROM intent WHERE berth_id = ? AND op = 'play'", ("berth-mark",))
    conn.commit()
    conn.close()

    app.reload()

    row = _line(app, "mark-finder")
    assert "live" in row
    assert "parked" not in row
    assert app.snap.berths["berth-mark"].state == "live"
    assert app.snap.intents == []
    assert "play recorded" not in app.text()
    assert "already recorded" not in app.text()
    check = sqlite3.connect(desk)
    workspace = check.execute(
        "SELECT cmux_workspace_id FROM berth WHERE id = 'berth-mark'"
    ).fetchone()[0]
    check.close()
    assert workspace == "fixture-workspace-mark-next"


def _line(app, token: str) -> str:
    return next(line for line in app.text().splitlines() if token in line)
