"""One Berth screen, in the workspace named "Berth" (2026-10-30).

Dominik ran ``berth`` in a workspace titled "~" while the main screen ran. Two
screens drained one intent queue, judged "the caller" by their own panes, and
the second could not be deleted. His words: "it should be impossible to
create two Berth sessions" and "it's not a state machine if it's able to be
broken and actually exist".

The rules, each enforced here:

* **At most one screen runs.** It holds an exclusive lock on
  ``screen.lock`` for its whole life (the OS lets go when the process dies).
  A second ``berth`` is refused, says where Berth is open, and switches there.
* **The screen lives in the workspace named "Berth".** ⌘⇧B goes to that
  workspace by name. ``berth`` typed anywhere else goes home: when a
  workspace named "Berth" is open, Berth switches to it and starts the screen
  there if its pane is at a shell prompt. When none is open, the workspace it
  was typed in becomes home: renamed "Berth" in cmux and in the catalog, so
  the names never differ (2026-10-30: a row stayed "~" while cmux said "berth").
* **Two screens anyway** (a race, an old screen still running) is a violation:
  ``berth check`` and the watcher report ``two_screens`` (``berth/invariant.py``),
  the screen's header says so, and the extra screen's sidebar row turns red.

``screen_of`` was the first fix of the same day (a screen claimed its row and
gave it back at quit). Rows still carrying it are given back once that screen
is gone (``release_dead``).
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path

from berth.timeutil import utc_now

HOME_TITLE = "Berth"
LOCK_NAME = "screen.lock"
_SHELLS = frozenset({"login", "zsh", "-zsh", "bash", "-bash", "sh", "fish", "dash", "tcsh", "csh", "ksh", "nu"})


def own_workspace() -> str | None:
    return os.environ.get("CMUX_WORKSPACE_ID") or None


# --- one screen ----------------------------------------------------------------

def lock(data_dir: Path, workspace_id: str | None) -> tuple[int | None, dict | None]:
    """Take the screen lock. Returns (fd, None) to hold for the screen's life, or (None, who holds it)."""
    path = Path(data_dir) / LOCK_NAME
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None, _read(path) or {"pid": None, "workspace_id": None}
    os.ftruncate(fd, 0)
    os.write(fd, json.dumps({"pid": os.getpid(), "workspace_id": workspace_id, "started": utc_now()}).encode())
    return fd, None


def holder(data_dir: Path) -> dict | None:
    """The running screen (pid, workspace_id), or None when no screen holds the lock."""
    path = Path(data_dir) / LOCK_NAME
    if not path.exists():
        return None
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return _read(path) or {"pid": None, "workspace_id": None}
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return None
    finally:
        os.close(fd)


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# --- its home ----------------------------------------------------------------

def place(cmux, workspace_id: str | None) -> tuple[str, object | None]:
    """Where this screen belongs: ("here", None), ("home", the open workspace named Berth), or ("promote", None)."""
    if not workspace_id:
        return "here", None  # not in cmux: nothing to name
    try:
        workspaces = cmux.list_workspaces()
    except Exception:
        return "here", None
    mine = next((item for item in workspaces if item.id.lower() == workspace_id.lower()), None)
    if mine is not None and mine.title == HOME_TITLE:
        return "here", None
    home = next((item for item in workspaces if item.title == HOME_TITLE and item.id.lower() != workspace_id.lower()), None)
    if home is not None:
        return "home", home
    return "promote", None


def promote(db, cmux, workspace_id: str) -> str | None:
    """Make this workspace Berth's home: named "Berth" in cmux and in the catalog, kind ``berth``."""
    cmux.rename_workspace(workspace_id, HOME_TITLE)
    row = db.conn.execute(
        "SELECT * FROM berth WHERE lower(cmux_workspace_id) = lower(?) AND state = 'live'", (workspace_id,)
    ).fetchone()
    if row is None:
        return None  # the watcher files it as the Berth screen, by its new name
    data = _attachments(row)
    data.pop("screen_of", None)
    with db.immediate():
        db.update_berth(row["id"], title=HOME_TITLE, kind="berth", recipe_argv=json.dumps(["berth"]),
                        recipe_display="berth", attachments=json.dumps(data, sort_keys=True), updated_at=utc_now())
        db.insert_event(row["id"], "screen_home", {"was_title": row["title"], "was_kind": row["kind"]})
    return row["id"]


def go_home(cmux, home, *, foreground=None) -> bool:
    """Switch to Berth's home, and start the screen there if its pane sits at a shell prompt. True if started."""
    from berth.activity import foreground as foreground_of

    look = foreground or foreground_of
    try:
        cmux.select_workspace(home.id)
        ttys = cmux.terminals().get(home.id.lower(), [])
    except Exception:
        return False
    names = [name.rsplit("/", 1)[-1] for tty in ttys[:1] for name in look(tty)]
    if not names or any(name not in _SHELLS for name in names):
        return False  # something runs there (maybe a screen starting); never type into it
    try:
        cmux.send_text(home.id, "berth")
        cmux.send_key(home.id, "enter")
    except Exception:
        return False
    return True


# --- the first fix's claims ----------------------------------------------------

def _attachments(row) -> dict:
    try:
        data = json.loads(row["attachments"] or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def release(db, berth_id: str, *, pid: int | None = None) -> bool:
    """Give a claimed row back what it was. With ``pid``, only if that screen still holds it."""
    row = db.get_berth(berth_id)
    if row is None:
        return False
    data = _attachments(row)
    owned = data.get("screen_of")
    if not isinstance(owned, dict) or (pid is not None and owned.get("pid") != pid):
        return False
    data.pop("screen_of", None)
    with db.immediate():
        db.update_berth(berth_id, kind=owned.get("kind") or "shell", recipe_argv=owned.get("recipe_argv") or "[]",
                        recipe_display=owned.get("recipe_display") or "", attachments=json.dumps(data, sort_keys=True),
                        updated_at=utc_now())
        db.insert_event(berth_id, "screen_released", {"kind": owned.get("kind"), "pid": owned.get("pid")})
    return True


def release_dead(db, pid_alive_fn) -> list[str]:
    """The watcher's half: give back every claimed row whose screen is gone."""
    released = []
    for row in db.conn.execute("SELECT * FROM berth WHERE attachments LIKE '%screen_of%'").fetchall():
        owned = _attachments(row).get("screen_of")
        if isinstance(owned, dict) and not pid_alive_fn(int(owned.get("pid") or 0)):
            if release(db, row["id"]):
                released.append(row["id"])
    return released


def claimed(row) -> bool:
    return isinstance(_attachments(row).get("screen_of"), dict)


def live_screens(rows) -> list:
    """The rows that are open Berth screens. More than one is the ``two_screens`` violation."""
    return [row for row in rows if row["kind"] == "berth" and row["state"] in ("live", "playing", "pausing")]
