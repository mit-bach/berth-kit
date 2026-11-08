from berth.color import xterm256
from berth.db import Catalog
from berth.fixture import build_desk
from berth.render import render_berth, render_folder
from berth.tree import Row


class FakeCmux:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def set_workspace_color(self, workspace_id: str, hex_color: str | None) -> None:
        self.calls.append((workspace_id, hex_color))


def test_palette_red_and_blue_are_different_terminal_colors():
    red = xterm256("#c0392b")
    blue = xterm256("#2980b9")
    assert red is not None and blue is not None
    assert red != blue
    assert red > 16 and blue > 16


def test_a_folder_and_its_session_carry_a_swatch_not_a_hex_dot():
    folder = Row(kind="folder", id="f", depth=0, title="Course", color="#c0392b", live_n=1, parked_n=0, grok_n=1)
    line = render_folder(" ", folder, _layout(), False, False)
    assert "██" in line.text
    assert "#c0392b" not in line.text
    assert any(name == "swatch:#c0392b" for _, _, name in line.spans)
    session = Row(
        kind="berth",
        id="b",
        depth=1,
        title="sample",
        state="live",
        berth_kind="grok",
        color="#c0392b",
        path_label="Course",
    )
    drawn = render_berth(_app(), " ", session, _layout(), False)
    assert "██" in drawn.text
    assert any(name == "swatch:#c0392b" for _, _, name in drawn.spans)


def test_palette_sync_colors_the_folder_and_its_live_workspace(tmp_path):
    from berth.model import Workspace
    from berth.palette import sync

    path = build_desk(tmp_path / "desk.sqlite")
    db = Catalog(path).open()
    cmux = FakeCmux()
    workspace = Workspace(id="fixture-workspace-sample", title="sample", cwd="/desk", color=None)
    sync(db, cmux, [workspace])
    color = db.folder_by_id("folder-course")["color"]
    assert color and color.startswith("#")
    assert ("fixture-workspace-sample", color) in cmux.calls
    painted = Workspace(id="fixture-workspace-sample", title="sample", cwd="/desk", color=color.upper())
    cmux.calls.clear()
    result = sync(db, cmux, [painted])
    db.close()
    assert result == {"folders_changed": [], "workspaces_painted": []}
    assert cmux.calls == []


class _Layout:
    width = 80
    gutter = 12
    state_w = 8
    flag_w = 2
    kind_w = 6
    note_w = 10
    path_w = 10


def _layout():
    return _Layout()


class _App:
    marked: tuple = ()


def _app():
    return _App()
