"""The account view: sessions grouped by Claude account, each with its own copy of the folders (2026-11-05).

Dominik: a toggle, off unless he wants it; every session shaded in its account's color, the whole row; grouped so
he sees how many are on each; the same folders as the tree inside each account, a folder split across three
accounts three times, one only on one account once.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from berth import accounts
from berth.ops import mkdir_path
from tests.test_state import _insert


@pytest.fixture
def switcher(tmp_path):
    """The account switcher's files, as Settings Agent writes them (made-up)."""
    config = tmp_path / "accounts-config.json"
    data = tmp_path / "accounts-data"
    data.mkdir()
    folders = {name: tmp_path / f".claude-{name}" for name in ("homestead", "personal")}
    for folder in folders.values():
        folder.mkdir()
    config.write_text(json.dumps({"order": ["uni", "homestead", "personal"], "default_account": "uni",
                                  "folders": {name: str(path) for name, path in folders.items()}}))
    (data / "state.json").write_text(json.dumps({"active": "uni", "last_switch_at": 0}))
    (data / "usage.json").write_text(json.dumps({"homestead": {"five_hour": {"used_percentage": 100},
                                                               "seven_day": {"used_percentage": 17.2}}}))
    return {name: str(path) for name, path in folders.items()}


@pytest.fixture
def desk(catalog, switcher):
    """HOMESTEAD's sessions on three accounts, SLACK's on uni only, STUDIO's only Grok and a shell."""
    homestead = mkdir_path(catalog, "HOMESTEAD")["id"]
    slack = mkdir_path(catalog, "HOMESTEAD/SLACK")["id"]
    system = mkdir_path(catalog, "STUDIO")["id"]

    def claude(berth_id, folder, config_dir=None, state="live"):
        _insert(catalog, id=berth_id, title=berth_id.replace("-", " ").title(), kind="other-agent",
                agent_name="claude", agent_session_id=f"s-{berth_id}", state=state, folder_id=folder,
                cmux_workspace_id=f"ws-{berth_id}" if state == "live" else None,
                **({"attachments": {"config_dir": config_dir}} if config_dir else {}))

    claude("slack-orch", slack)  # ~/.claude: uni
    claude("recon-fix", homestead)  # uni
    claude("repo-steward", homestead, switcher["homestead"])
    claude("tutor", homestead, switcher["personal"], state="parked")
    _insert(catalog, id="grok", title="Grok Desk", kind="grok", agent_name="grok", state="live", folder_id=system,
            cmux_workspace_id="ws-grok")
    _insert(catalog, id="shell", title="A Shell", kind="shell", state="live", folder_id=system,
            cmux_workspace_id="ws-shell")
    return catalog


def _tree(catalog):
    from berth.catalog import load
    from berth.tree import build_account_tree

    return build_account_tree(load(catalog.path), datetime.now(timezone.utc), "", "name")


def test_each_account_holds_a_copy_of_only_the_folders_its_sessions_are_in(desk):
    rows, counts = _tree(desk)
    headings = [row for row in rows if row.heading == "account"]
    assert [row.title for row in headings] == ["uni", "homestead", "personal", "No account"]
    assert [row.live_n for row in headings] == [2, 1, 0, 2] and headings[2].parked_n == 1
    shown = {}
    for row in rows:
        if row.kind == "folder" and not row.heading:
            shown.setdefault(row.path, []).append(row.account_key)
    assert shown == {"HOMESTEAD": ["uni", "homestead", "personal"], "HOMESTEAD/SLACK": ["uni"], "STUDIO": [""]}
    # Each session once, under its account, one level deeper than in the tree; a copy's path is the real path.
    sessions = {row.id: row for row in rows if row.kind == "berth"}
    assert sorted(sessions) == ["grok", "recon-fix", "repo-steward", "shell", "slack-orch", "tutor"]
    assert sessions["slack-orch"].depth == 3 and sessions["slack-orch"].path == "HOMESTEAD/SLACK"
    assert sessions["repo-steward"].tint == accounts.color_of("homestead") == "#A78BFA"
    assert sessions["grok"].tint is None and headings[-1].tint is None
    assert counts.live == 5 and counts.parked == 1  # the header's counts are the whole catalog's


def test_a_claude_session_on_a_folder_claude_config_does_not_name_is_its_own_group(desk, tmp_path):
    from berth.tree import real_folder_id

    homestead = desk.conn.execute("SELECT id FROM folder WHERE name = 'HOMESTEAD'").fetchone()[0]
    _insert(desk, id="odd", title="Odd", kind="other-agent", agent_name="claude", agent_session_id="s-odd",
            state="live", folder_id=homestead, cmux_workspace_id="ws-odd",
            attachments={"config_dir": str(tmp_path / ".claude-elsewhere")})
    rows, _ = _tree(desk)
    headings = [row.title for row in rows if row.heading == "account"]
    assert headings == ["uni", "homestead", "personal", "Claude, account unknown", "No account"]
    copy = next(row for row in rows if row.kind == "folder" and row.account_key == "?" and not row.heading)
    assert real_folder_id(copy.id) == homestead and real_folder_id(rows[0].id) is None


def _app(catalog, **size):
    from berth.app import App

    return App(catalog.path, width=size.get("width", 140), height=size.get("height", 30))


def test_a_switches_this_screen_alone_and_folders_stay_the_trees(desk):
    view = desk.path.parent / "view.json"
    before = view.read_text() if view.exists() else None
    app = _app(desk)
    app.focus_berth("repo-steward")
    assert not any(row.heading for row in app.visible)
    app.handle("a")
    assert app.accounts_view
    assert (view.read_text() if view.exists() else None) == before  # nothing outside this screen changes
    assert _app(desk).accounts_view is False  # a new screen opens on the tree
    assert app.cursor == ("berth", "repo-steward")  # the cursor stays on its session
    assert "by account" in app.filter_label and "4 accounts" in app.message
    # Folders are changed in the tree: here a folder row is one account's copy of it.
    app.cursor = next((row.kind, row.id) for row in app.visible if row.kind == "folder" and not row.heading)
    for key in ("n", "r", "d", "p", "S", "v", "m"):
        app.message = ""
        app.handle(key)
        assert "a goes back to the tree" in app.message, key
    app.handle("a")
    assert not app.accounts_view


def test_an_account_folds_like_a_folder(desk):
    app = _app(desk)
    app.handle("a")
    app.reload()
    assert app.accounts_view  # a reload keeps this screen's view
    uni = next(row for row in app.visible if row.heading == "account" and row.title == "uni")
    app.cursor = ("folder", uni.id)
    before = len(app.visible)
    app.handle("h")
    assert uni.id in app.collapsed and len(app.visible) == before - 4  # HOMESTEAD, SLACK and two sessions
    folders = [line.text for line in app.lines() if line.role == "folder"]
    assert sum("HOMESTEAD" in text for text in folders) == 2  # homestead's and personal's copies are still open
    app.handle("l")
    app.focus_berth("slack-orch")
    assert app.current.id == "slack-orch"


def test_the_screen_draws_each_account_as_a_band_and_shades_its_rows(desk):
    from berth.render import ACCOUNT_HINT

    app = _app(desk)
    plain = app.lines()
    assert not any(line.tint for line in plain)
    app.handle("a")
    lines = app.lines()
    # Line 2 is the color key: each account's color square and name, then the keys (2026-11-05, Dominik: "I don't
    # know what color is assigned to what system").
    key = lines[1]
    assert key.text.startswith("by account   ██ uni   ██ homestead   ██ personal") and "a back to the tree" in key.text
    assert [name for _, _, name in key.spans] == ["account:#2DD4BF", "account:#A78BFA", "account:#FB923C"]
    assert ACCOUNT_HINT.split(" · ", 1)[1][:30] in key.text
    bands = [line for line in lines if line.role == "account"]
    assert [line.strong for line in bands] == [True] * 4
    homestead = next(line for line in bands if " homestead " in line.text)
    assert "1 live" in homestead.text and "5h 100% · 7d 17%" in homestead.text and len(homestead.text) == 140
    assert "default · ~/.claude" in bands[0].text and bands[-1].text.lstrip(" >").startswith("▾ No account")
    steward = next(line for line in lines if line.row_id == "repo-steward")
    assert steward.tint == "#A78BFA" and not steward.strong and "Claude hom" in steward.text
    # The square on the left is the account's color here, not the folder's; blank under "No account".
    assert [name for _, _, name in steward.spans if "account:" in str(name) or "swatch:" in str(name)] == \
        ["account:#A78BFA"]
    grok_line = next(line for line in lines if line.row_id == "grok")
    assert not any("account:" in str(name) or "swatch:" in str(name) for _, _, name in grok_line.spans)
    app.handle("a")
    assert not any(str(name).startswith("account:") for line in app.lines() for _, _, name in line.spans)  # tree
    app.handle("a")
    lines = app.lines()
    assert next(line for line in lines if line.row_id == "grok").tint is None
    # A click on a band folds that account, as a click on a folder does.
    from berth.render import target_at

    assert target_at(lines, lines.index(bands[0])) == ("folder", "account:uni")


def test_cmux_sidebar_never_follows_the_screens_account_view(desk):
    """2026-11-05, Dominik: the account view is the Berth screen's alone; cmux's sidebar stays as it was."""
    from berth.model import Workspace
    from berth.sidebar import sync
    from tests.fakes import FakeCmux

    def order():
        cmux = FakeCmux()
        names = ["ws-stranger", "ws-shell", "ws-recon-fix", "ws-slack-orch", "ws-repo-steward", "ws-grok"]
        for index, name in enumerate(names):
            cmux.workspaces[name] = Workspace(id=name, title=name, cwd="/tmp", index=index)
        sync(desk.path, cmux, list(cmux.workspaces.values()))
        return cmux.reorders[-1] if cmux.reorders else names

    with desk.immediate():
        desk.conn.execute("UPDATE berth SET pinned = 1 WHERE id = 'grok'")
    before = order()
    app = _app(desk)
    app.handle("a")
    assert app.accounts_view and order() == before and before[0] == "ws-grok"  # pinned first, as ever


def test_colors_come_from_claude_configs_file_or_the_palette(switcher, tmp_path):
    config = tmp_path / "accounts-config.json"
    data = json.loads(config.read_text())
    assert [accounts.color_of(name) for name in ("uni", "homestead", "personal")] == list(accounts.PALETTE[:3])
    data["colors"] = {"uni": "#a31f34", "homestead": "not a color"}
    config.write_text(json.dumps(data))
    assert accounts.color_of("uni") == "#A31F34" and accounts.color_of("homestead") == accounts.PALETTE[1]
    assert accounts.color_of("") is None and accounts.color_of(accounts.UNKNOWN) == accounts.UNKNOWN_COLOR
    assert accounts.rank_of("personal") == 2 < accounts.rank_of(accounts.UNKNOWN) < accounts.rank_of("")
    assert accounts.marks_of("uni") == ["default", "~/.claude"] and accounts.marks_of("homestead") == []


def test_a_tint_is_dark_on_a_row_and_stronger_on_a_heading():
    from berth.color import tint256

    assert tint256("#2DD4BF") == (23, 253)  # #005f5f under the terminal's light text
    background, foreground = tint256("#2DD4BF", strong=True)
    assert background not in (16, 23) and foreground in (16, 231)
    assert tint256(None) is None and tint256("#000000") == (236, 253)


def test_the_painter_shades_the_whole_line_and_keeps_its_colors_over_it():
    from berth import curses_ui
    from berth.render import Line

    class Curses:
        COLORS, COLOR_PAIRS, A_BOLD, A_REVERSE = 256, 32767, 1 << 20, 1 << 18
        error = Exception
        pairs = {}

        def init_pair(self, pair_id, foreground, background):
            self.pairs[pair_id] = (foreground, background)

        def color_pair(self, pair_id):
            return pair_id << 8

    class Screen:
        def __init__(self):
            self.calls = []

        def addnstr(self, y, x, text, n, attr):
            self.calls.append((x, text, attr))

    curses, screen = Curses(), Screen()
    line = Line("> ██ Repo Agent  live", role="berth", spans=((2, 4, "swatch:#30A0F0"), (19, 23, "live")),
                tint="#A78BFA")
    assert curses_ui._paint_tinted(screen, curses, 3, line, line.text, 60) is True
    (x, text, attr), *spans = screen.calls
    assert x == 0 and len(text) == 60 and text.startswith("> ██ Repo")  # the whole bar, not only the words
    assert curses.pairs[attr >> 8] == (253, 60)  # light text on the account's dark violet
    colors = {curses.pairs[(call[2] & ~curses.A_BOLD) >> 8] for call in spans}
    assert (2, 60) in colors and any(background == 60 for _, background in colors)  # green "live" on the tint
    assert curses_ui._paint_tinted(screen, curses, 3, Line("x", tint=None), "x", 10) is False


def test_a_terminal_that_sets_its_palette_gets_each_accounts_exact_tint(monkeypatch):
    """cmux's Ghostty can set palette colors: rows get a soft wash of the account's color, not the cube's heavy one,
    and a selected heading brightens to the account's own color instead of reversing to a dark bar."""
    from berth import curses_ui
    from berth.color import tint_rgb
    from berth.render import Line

    monkeypatch.setenv("COLORTERM", "truecolor")
    monkeypatch.setattr(curses_ui, "_EXACT_SLOTS", list(range(251, 236, -1)))
    monkeypatch.setattr(curses_ui, "_EXACT", {})

    class Curses:
        COLORS, COLOR_PAIRS, A_BOLD, A_REVERSE = 256, 32767, 1 << 20, 1 << 18
        error = Exception

        def __init__(self):
            self.pairs, self.colors = {}, {}

        def can_change_color(self):
            return True

        def init_color(self, slot, red, green, blue):
            self.colors[slot] = (red, green, blue)

        def init_pair(self, pair_id, foreground, background):
            self.pairs[pair_id] = (foreground, background)

        def color_pair(self, pair_id):
            return pair_id << 8

    class Screen:
        def __init__(self):
            self.calls = []

        def addnstr(self, y, x, text, n, attr):
            self.calls.append((x, text, attr))

    curses, screen = Curses(), Screen()
    row = Line("   Repo Agent  live", role="berth", spans=((16, 20, "live"),), tint="#A78BFA")
    assert curses_ui._paint_tinted(screen, curses, 3, row, row.text, 40) is True
    foreground, background = curses.pairs[screen.calls[0][2] >> 8]
    assert background in range(237, 252) and foreground == 253
    (red, green, blue), dark_text = tint_rgb("#A78BFA")
    assert curses.colors[background] == tuple(round(value * 1000 / 255) for value in (red, green, blue))
    assert max(red, green, blue) < 90 and not dark_text  # a soft wash under light text
    band = Line(" ▾ homestead  17 live", role="account", tint="#A78BFA", strong=True, selected=True)
    screen.calls.clear()
    assert curses_ui._paint_tinted(screen, curses, 4, band, band.text, 40) is True
    x, text, attr = screen.calls[0]
    assert not attr & curses.A_REVERSE and curses.pairs[(attr & ~curses.A_BOLD) >> 8][0] == 16  # dark text, no reverse
    assert curses.colors[curses.pairs[(attr & ~curses.A_BOLD) >> 8][1]] == (655, 545, 980)  # the account's own color
    monkeypatch.setenv("COLORTERM", "")
    monkeypatch.setattr(curses_ui, "_EXACT", {})
    assert curses_ui._tint(curses, "#A78BFA", False) == (60, 253)  # elsewhere: the 256-color cube, as before
