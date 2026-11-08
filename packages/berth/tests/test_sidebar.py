from __future__ import annotations

from berth.model import Workspace
from berth.ops import mkdir_path
from berth.sidebar import desired, read_sort, sync, write_sort
from tests.fakes import FakeCmux
from tests.test_state import _insert


def _ws(identifier, index, pinned=False):
    return Workspace(id=identifier, title=identifier, cwd="/tmp", index=index, pinned=pinned)


def test_desired_puts_pinned_first_and_strangers_last():
    workspaces = [_ws("x", 0), _ws("a", 1), _ws("p", 2, pinned=True), _ws("b", 3)]
    assert desired(["b", "a", "p"], workspaces) == ["p", "b", "a", "x"]


def test_the_sidebar_follows_the_tree_and_does_not_reorder_when_aligned(catalog):
    old = mkdir_path(catalog, "Old")["id"]
    new = mkdir_path(catalog, "New")["id"]
    _insert(catalog, id="o1", title="o1", state="live", cmux_workspace_id="ws-o1", folder_id=old)
    _insert(catalog, id="n1", title="n1", state="live", cmux_workspace_id="ws-n1", folder_id=new)
    _insert(catalog, id="n2", title="n2", state="live", cmux_workspace_id="ws-n2", folder_id=new)
    with catalog.immediate():
        for berth_id, stamp in (("o1", "2026-01-01"), ("n1", "2026-03-01"), ("n2", "2026-02-01")):
            catalog.update_berth(berth_id, last_used_at=f"{stamp}T00:00:00.000000Z")
    cmux = FakeCmux()
    for index, identifier in enumerate(["ws-o1", "ws-n2", "ws-n1"]):
        cmux.workspaces[identifier] = _ws(identifier, index)
    assert sync(catalog.path, cmux, list(cmux.workspaces.values())) is True
    assert cmux.reorders[-1] == ["ws-n1", "ws-n2", "ws-o1"]
    assert sync(catalog.path, cmux, list(cmux.workspaces.values())) is False
    write_sort(catalog.path.parent, "name")
    assert read_sort(catalog.path.parent) == "name"
    sync(catalog.path, cmux, list(cmux.workspaces.values()))
    assert cmux.reorders[-1] == ["ws-o1", "ws-n1", "ws-n2"]  # name order: folders in stored order, then titles


def test_a_workspace_that_closes_mid_reorder_is_retried_next_tick(catalog):
    from berth.errors import BerthError

    _insert(catalog, id="a", title="a", state="live", cmux_workspace_id="ws-a")
    _insert(catalog, id="b", title="b", state="live", cmux_workspace_id="ws-b")
    with catalog.immediate():
        catalog.update_berth("a", last_used_at="2026-01-01T00:00:00.000000Z")
        catalog.update_berth("b", last_used_at="2026-02-01T00:00:00.000000Z")
    cmux = FakeCmux()
    cmux.workspaces = {"ws-a": _ws("ws-a", 0), "ws-b": _ws("ws-b", 1)}

    def gone(ids):
        raise BerthError("CMUX_REJECTED", "Error: not_found: Workspace not found", {})

    cmux.reorder_workspaces = gone
    assert sync(catalog.path, cmux, list(cmux.workspaces.values())) is False


def test_the_sidebar_moves_at_most_every_thirty_seconds_but_a_new_sort_moves_it_at_once(catalog):
    """2026-10-28: reordering every two seconds moved rows under the pointer."""
    from berth.sidebar import SIDEBAR_EVERY, Pace

    for berth_id, stamp in (("a", "2026-01-01"), ("b", "2026-02-01")):
        _insert(catalog, id=berth_id, title=berth_id, state="live", cmux_workspace_id=f"ws-{berth_id}")
        with catalog.immediate():
            catalog.update_berth(berth_id, last_used_at=f"{stamp}T00:00:00.000000Z")
    cmux = FakeCmux()
    cmux.workspaces = {"ws-a": _ws("ws-a", 0), "ws-b": _ws("ws-b", 1)}
    now = [1000.0]
    pace = Pace(clock=lambda: now[0])

    def tick() -> bool:
        moved = sync(catalog.path, cmux, list(cmux.workspaces.values()), pace)
        if moved:  # cmux now lists them in that order
            for index, identifier in enumerate(cmux.reorders[-1]):
                cmux.workspaces[identifier] = _ws(identifier, index)
        return moved

    assert tick() is True and cmux.reorders[-1] == ["ws-b", "ws-a"]
    with catalog.immediate():
        catalog.update_berth("a", last_used_at="2026-03-01T00:00:00.000000Z")  # a is used: it should go up
    for _ in range(14):
        now[0] += 2
        assert tick() is False  # every two-second tick inside the window leaves the sidebar alone
    now[0] = 1000.0 + SIDEBAR_EVERY
    assert tick() is True and cmux.reorders[-1] == ["ws-a", "ws-b"]
    now[0] += 2
    write_sort(catalog.path.parent, "name")
    with catalog.immediate():
        catalog.update_berth("b", title="0 first by name")
    assert tick() is True and cmux.reorders[-1] == ["ws-b", "ws-a"]  # s in the screen applies at once



def test_folder_by_folder_keeps_a_folder_in_place_when_a_subfolder_is_busy(catalog):
    """2026-10-29: with the tree's order, any session in a HOMESTEAD subfolder lifted all of HOMESTEAD above BERTH."""
    from berth.sidebar import Pace, folder_order, read_sidebar, tree_order, write_sidebar

    hy = mkdir_path(catalog, "HY")["id"]
    sub = mkdir_path(catalog, "HY/Sub")["id"]
    berth_folder = mkdir_path(catalog, "BERTH")["id"]
    for berth_id, folder, stamp in (("h1", hy, "2026-01-01"), ("s1", sub, "2026-03-01"), ("b1", berth_folder, "2026-02-01")):
        _insert(catalog, id=berth_id, title=berth_id, state="live", cmux_workspace_id=f"ws-{berth_id}", folder_id=folder)
        with catalog.immediate():
            catalog.update_berth(berth_id, last_used_at=f"{stamp}T00:00:00.000000Z")
    assert tree_order(catalog.path, "recent") == ["ws-s1", "ws-h1", "ws-b1"]  # Sub lifts HY over BERTH
    assert folder_order(catalog.path, "recent") == ["ws-s1", "ws-b1", "ws-h1"]  # each folder on its own
    with catalog.immediate():
        catalog.update_berth("s1", last_used_at="2026-04-01T00:00:00.000000Z")
    assert folder_order(catalog.path, "recent").index("ws-b1") < folder_order(catalog.path, "recent").index("ws-h1")
    assert folder_order(catalog.path, "name") == ["ws-b1", "ws-h1", "ws-s1"]  # BERTH, HY, HY/Sub

    cmux = FakeCmux()
    for index, identifier in enumerate(["ws-h1", "ws-b1", "ws-s1"]):
        cmux.workspaces[identifier] = _ws(identifier, index)
    now = [0.0]
    pace = Pace(clock=lambda: now[0])
    assert sync(catalog.path, cmux, list(cmux.workspaces.values()), pace)
    assert cmux.reorders[-1] == ["ws-s1", "ws-h1", "ws-b1"]  # the default follows the tree
    for index, identifier in enumerate(cmux.reorders[-1]):
        cmux.workspaces[identifier] = _ws(identifier, index)
    write_sort(catalog.path.parent, "recent")
    write_sidebar(catalog.path.parent, "folders")
    assert read_sidebar(catalog.path.parent) == "folders" and read_sort(catalog.path.parent) == "recent"
    now[0] += 2  # inside the 30-second pace: the switch still applies at once
    assert sync(catalog.path, cmux, list(cmux.workspaces.values()), pace)
    assert cmux.reorders[-1] == ["ws-s1", "ws-b1", "ws-h1"]
    write_sort(catalog.path.parent, "name")
    assert read_sidebar(catalog.path.parent) == "folders"  # one setting does not wipe the other


def test_o_in_the_screen_switches_the_sidebar_order_and_says_so(desk):
    from berth.sidebar import read_sidebar
    from tests.support import make_app

    app = make_app(desk)
    assert app.sidebar == "tree" and "sidebar" not in app.lines()[0].text
    app.handle("o")
    assert read_sidebar(desk.parent) == "folders" and "folder by folder" in app.message
    assert "sidebar by folder" in app.lines()[0].text
    app.handle("o")
    assert read_sidebar(desk.parent) == "tree"
