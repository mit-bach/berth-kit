"""The cmux sidebar lists workspaces in the order the Berth tree shows them.

The tree order is the screen's: last used, newest first, with folders and
sessions interleaved at every level (or by name, when the screen's ``s`` says
so; the choice is stored in ``view.json`` next to the catalog). The sidebar is
flat, so it gets the live sessions in the order a walk down the tree meets
them. Workspaces Berth does not hold keep their order after the rest.
Berth's pinned sessions come first, in Berth's order. The pin is Berth's
alone (2026-10-31): cmux's own pin is never set, so cmux's pinned-first rule
no longer decides any order (``berth/pins.py``).

The watcher looks every ``SIDEBAR_EVERY`` seconds, not every tick, and
reorders only when cmux's order differs, as one atomic ``reorder-workspaces``.
Reordering every two seconds moved rows under the pointer while you worked
(2026-10-28). Using a session moves it, and its folder, up in the screen at
once and in the sidebar at the next look. A new sort from the screen's ``s``
applies at once, and so does a pin or an unpin: pinned sessions come first
(``berth/pins.py``). So does switching the order between the tree's and
folder by folder (the screen's ``o``; ``SIDEBAR_MODES`` below).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from berth.model import Workspace

SIDEBAR_EVERY = 30.0


class Pace:
    """When the watcher may look at the sidebar order: every ``every`` seconds, or on a new sort."""

    def __init__(self, every: float = SIDEBAR_EVERY, clock: Callable[[], float] = time.monotonic) -> None:
        self.every = every
        self.clock = clock
        self.looked: float | None = None
        self.sort: str | None = None

    def due(self, key) -> bool:
        """``key`` is what applies at once when it changes: the sort, and which workspaces are pinned."""
        now = self.clock()
        if key == self.sort and self.looked is not None and now - self.looked < self.every:
            return False
        self.looked, self.sort = now, key
        return True

    def again(self) -> None:
        """Look again on the next tick."""
        self.looked = None


def view_path(directory: Path) -> Path:
    return directory / "view.json"


# How the sidebar is ordered (the screen's o, 2026-10-29):
#   tree     the Berth tree's order, walked top-down: a folder is as recent as its newest
#            session anywhere inside it, so a busy subfolder lifts its whole family.
#   folders  every folder on its own, at any depth: folders by their own newest session
#            (a subfolder's sessions do not count for its parent), then their sessions.
SIDEBAR_MODES = ("tree", "folders")


def _read_view(directory: Path) -> dict:
    try:
        data = json.loads(view_path(directory).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_view(directory: Path, **changes) -> None:
    path = view_path(directory)
    data = {**_read_view(directory), **changes}
    try:
        path.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")
        path.chmod(0o600)
    except OSError:
        pass


def read_sort(directory: Path) -> str:
    sort = _read_view(directory).get("sort")
    return sort if sort in ("recent", "name") else "recent"


def write_sort(directory: Path, sort: str) -> None:
    _write_view(directory, sort=sort)


def read_sidebar(directory: Path) -> str:
    mode = _read_view(directory).get("sidebar")
    return mode if mode in SIDEBAR_MODES else "tree"


def write_sidebar(directory: Path, mode: str) -> None:
    _write_view(directory, sidebar=mode if mode in SIDEBAR_MODES else "tree")


def tree_order(catalog_path: Path, sort: str) -> list[str]:
    """Workspace ids of live rows, in the order the Berth tree lists them."""
    from berth.catalog import load
    from berth.tree import build_tree

    snap = load(catalog_path)
    rows, _ = build_tree(snap, datetime.now(timezone.utc), "", sort)
    order: list[str] = []
    workspaces = _workspace_ids(catalog_path)
    for row in rows:
        if row.kind == "berth" and row.state == "live" and row.id in workspaces:
            order.append(workspaces[row.id])
    return order


def folder_order(catalog_path: Path, sort: str) -> list[str]:
    """Workspace ids of live rows, folder by folder, every folder on its own (the ``folders`` mode).

    A folder's place is its own newest session (``last_used_at``); what happens
    in its subfolders does not move it. By name, folders go by their full path.
    """
    from berth.catalog import load

    snap = load(catalog_path)
    workspaces = _workspace_ids(catalog_path)
    groups: dict[str, list] = {}
    for berth in snap.berths.values():
        if berth.state == "live" and berth.id in workspaces:
            groups.setdefault(berth.folder_id, []).append(berth)
    if sort == "name":
        for rows in groups.values():
            rows.sort(key=lambda berth: (berth.title.casefold(), berth.id))
        folders = sorted(groups, key=lambda folder_id: (snap.folder_path(folder_id).casefold(), folder_id))
    else:
        for rows in groups.values():
            rows.sort(key=lambda berth: (berth.title.casefold(), berth.id))
            rows.sort(key=lambda berth: berth.last_used_at or "", reverse=True)
        folders = sorted(groups, key=lambda folder_id: snap.folder_path(folder_id).casefold())
        folders.sort(key=lambda folder_id: groups[folder_id][0].last_used_at or "", reverse=True)
    return [workspaces[berth.id] for folder_id in folders for berth in groups[folder_id]]


def _workspace_ids(catalog_path: Path) -> dict[str, str]:
    import sqlite3

    conn = sqlite3.connect(f"file:{catalog_path}?mode=ro", uri=True)
    try:
        return {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT id, cmux_workspace_id FROM berth WHERE state = 'live' AND cmux_workspace_id IS NOT NULL"
            )
        }
    finally:
        conn.close()


def desired(order: list[str], workspaces: list[Workspace], pinned_ids: set[str] | None = None) -> list[str]:
    """The full sidebar order: pinned first, each part in Berth's order, strangers last.

    ``pinned_ids`` are the workspaces of Berth's pinned sessions; without it, cmux's own pin flag.
    """
    by_id = {item.id.lower(): item for item in workspaces}
    ranked = [by_id[key.lower()] for key in order if key.lower() in by_id]
    seen = {item.id.lower() for item in ranked}
    strangers = [item for item in workspaces if item.id.lower() not in seen]
    final = ranked + strangers

    def is_pinned(item) -> bool:
        return item.id.lower() in pinned_ids if pinned_ids is not None else bool(item.pinned)

    pinned = [item.id for item in final if is_pinned(item)]
    unpinned = [item.id for item in final if not is_pinned(item)]
    return pinned + unpinned


def berth_pins(catalog_path: Path) -> set[str]:
    """The workspaces of Berth's pinned live sessions (lower case)."""
    import sqlite3

    conn = sqlite3.connect(f"file:{catalog_path}?mode=ro", uri=True)
    try:
        return {row[0].lower() for row in conn.execute(
            "SELECT cmux_workspace_id FROM berth WHERE state = 'live' AND pinned = 1 AND cmux_workspace_id IS NOT NULL")}
    finally:
        conn.close()


def sync(catalog_path: Path, cmux, workspaces: list[Workspace] | None, pace: Pace | None = None) -> bool:
    """Reorder the sidebar when it differs from the tree. True when it moved."""
    if not workspaces:
        return False
    sort = read_sort(catalog_path.parent)
    mode = read_sidebar(catalog_path.parent)
    pins = berth_pins(catalog_path)
    # A workspace that opened or closed is looked at on the next tick, not after up to SIDEBAR_EVERY seconds: a
    # cycle replaces a session's workspace, and cmux puts the new one out of its folder's run, so the folder
    # showed twice until the next look (2026-11-03, 16 account moves in one burst).
    present = tuple(sorted(item.id.lower() for item in workspaces))
    if pace is not None and not pace.due((sort, mode, tuple(sorted(pins)), present)):
        return False
    order = folder_order(catalog_path, sort) if mode == "folders" else tree_order(catalog_path, sort)
    want = desired(order, workspaces, pins)
    have = [item.id for item in sorted(workspaces, key=lambda item: item.index if item.index is not None else 0)]
    if [item.lower() for item in want] == [item.lower() for item in have]:
        return False
    from berth.errors import BerthError

    try:
        cmux.reorder_workspaces(want)
    except BerthError as exc:
        if "not_found" in exc.message or "not found" in exc.message.lower():
            # A workspace closed between the list and the reorder. Next tick retries.
            if pace is not None:
                pace.again()
            return False
        raise
    return True
