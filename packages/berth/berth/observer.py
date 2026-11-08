"""The one place that turns what cmux shows into row transitions.

The watcher calls ``Observer.tick`` every two seconds with one fresh
``workspace list`` (or ``None`` when cmux cannot answer). Nothing else parks
a row because a workspace looks gone. docs/BERTH-STATE-MACHINE.md is the
contract; the short version:

* A linked row whose workspace is missing on two ticks in a row is parked
  with reason ``closed``. A close event is only a hint.
* A quit is the cmux app process being gone; a restart is a different cmux
  app process (pid and start time), from ``Cmux.app_process``. Nothing else
  counts. cmux recreates its socket file on every wake from sleep, and a
  sleeping or busy cmux stops answering for a while without quitting: on
  2026-10-22 treating either as a restart closed every pane on every wake.
  A cmux that does not answer while its process is still the same is left
  alone; nothing is parked.
* On a quit or restart every linked row is parked with reason ``cmux_quit``.
  Rows parked ``closed`` in the half minute before that were closed by the
  quit, and are relabeled ``cmux_quit``.
* A restart the watcher did not see (it was down too, or the Mac rebooted) is
  found at the next start: the cmux app identity is kept in ``observer.json``.
* When cmux is back, a workspace whose UUID was open at the quit is a pane
  cmux restored on its own, with its agent idle. It is closed, except the
  Berth screen, which is relinked and started. A pane that is running
  anything but a shell is never closed: its row is relinked to it instead.
  Berth closes a running pane only when a person pauses or deletes it. A workspace that comes back
  for any other parked row is relinked.
* A few seconds later every session that was open at the quit is reopened,
  one at a time, through the same play as Enter (``restore``), without taking
  focus. Rows left parked by an earlier restart are not reopened.
* An open workspace no row knows about is adopted once it has been seen on
  two ticks, so its hook file has time to land.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Callable

from berth import machine
from berth.db import Catalog
from berth.errors import BerthError
from berth.hooks import HookRecord, hook_for
from berth.model import Workspace
from berth.timeutil import STALE_PLAY_SECONDS, age_seconds, parse_time, utc_now

# A pause closes its pane within seconds. A row still "pausing" this long while its pane is open was cut off mid-way
# (2026-11-05: the watcher restarted between a cycle's flush and its close, and UNI Notes Sync stayed "pausing"
# on a live pane, which every wake and cycle then refused): the close did not happen, so it is live again.
STALE_PAUSE_SECONDS = 90

CONFIRM_TICKS = 2
QUIT_WINDOW_SECONDS = 30
SCREEN_START_SECONDS = 20
RESTORE_DELAY_SECONDS = 6
GHOST_SECONDS = 180
SCREEN_TITLE = "Berth"


def is_screen(row) -> bool:
    return row["kind"] == "berth" or row["title"] == SCREEN_TITLE


class Observer:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        restore: Callable[[list[str]], None] | None = None,
        state_path: Path | None = None,
    ) -> None:
        self.clock = clock
        self.restore = restore
        self.state_path = state_path
        self.missing: dict[str, int] = {}
        self.unknown: dict[str, int] = {}
        self.down_ticks = 0
        self.up_since: float | None = None
        self.came_back = False
        self.app: tuple | None = _saved_app(state_path)
        # Panes open at the last quit: workspace id -> berth id. cmux restores them.
        self.ghosts: dict[str, str] = {}
        self.ghosts_until: float | None = None
        # Sessions open at the last quit, reopened once cmux is back.
        self.quit_rows: list[str] = []
        self.restore_at: float | None = None

    def tick(
        self,
        db: Catalog,
        cmux,
        workspaces: list[Workspace] | None,
        hooks: dict[str, HookRecord],
    ) -> list[dict]:
        app = _app_identity(cmux)
        if workspaces is None:
            if app is _UNKNOWN:
                return []
            if app is not None and (self.app is None or app == self.app):
                # The same cmux is still running and did not answer (asleep,
                # busy, a timeout). That is not a quit. Park nothing.
                if self.app is None:
                    self.app = app
                    _save_app(self.state_path, app)
                return []
            # The cmux process is gone, or a different one is starting.
            return self._down(db)
        actions: list[dict] = []
        handled = self.down_ticks >= CONFIRM_TICKS
        if app not in (None, _UNKNOWN) and self.app is not None and app != self.app and not handled:
            # A different cmux process: a restart, however fast, or one that
            # happened while this watcher was not running.
            actions += self._quit(db)
            self.down_ticks = CONFIRM_TICKS
        if app not in (None, _UNKNOWN) and app != self.app:
            self.app = app
            _save_app(self.state_path, app)
        if self.down_ticks >= CONFIRM_TICKS or self.up_since is None:
            restarted = self.down_ticks >= CONFIRM_TICKS
            self.came_back = restarted or _has_quit_rows(db)
            self.up_since = self.clock()
            if restarted:
                self.ghosts_until = self.up_since + GHOST_SECONDS
                if self.quit_rows:
                    self.restore_at = self.up_since + RESTORE_DELAY_SECONDS
        self.down_ticks = 0
        open_ws = {item.id.lower(): item for item in workspaces}
        actions += self._missing(db, open_ws)
        actions += self._pausing(db, open_ws)
        actions += self._playing(db, open_ws)
        actions += self._unlinked(db, cmux, workspaces, open_ws, hooks)
        actions += self._screen(db, cmux)
        actions += self._restore(db)
        return actions

    # a quit, and what comes back ------------------------------------------

    def _quit(self, db: Catalog) -> list[dict]:
        """Park everything that was open, and remember it for the restore."""
        actions = park_for_quit(db)
        ghosts = {}
        rows = []
        for item in actions:
            row = db.get_berth(item["berth_id"])
            if row is None or is_screen(row):
                continue
            if item.get("workspace_id"):
                ghosts[item["workspace_id"].lower()] = row["id"]
            if item["action"] in ("parked", "relabeled"):
                rows.append(row["id"])
        self.ghosts = ghosts
        self.ghosts_until = None
        self.quit_rows = rows
        return actions

    def _restore(self, db: Catalog) -> list[dict]:
        if self.restore_at is None or self.clock() < self.restore_at:
            return []
        self.restore_at = None
        ids = []
        for berth_id in self.quit_rows:
            row = db.get_berth(berth_id)
            if row is not None and row["state"] == "parked" and row["park_reason"] == "cmux_quit":
                ids.append(berth_id)
        self.quit_rows = []
        if not ids or self.restore is None:
            return []
        self.restore(ids)
        return [{"berth_id": berth_id, "action": "restore_queued"} for berth_id in ids]

    # cmux is gone ---------------------------------------------------------

    def _down(self, db: Catalog) -> list[dict]:
        self.down_ticks += 1
        self.missing.clear()
        self.unknown.clear()
        self.up_since = None
        if self.down_ticks != CONFIRM_TICKS:
            return []
        return self._quit(db)

    # rows whose workspace is gone ----------------------------------------

    def _missing(self, db: Catalog, open_ws: dict[str, Workspace]) -> list[dict]:
        actions = []
        rows = db.conn.execute(
            "SELECT * FROM berth WHERE state IN ('live', 'pausing') AND cmux_workspace_id IS NOT NULL"
        ).fetchall()
        seen = set()
        for row in rows:
            seen.add(row["id"])
            if row["cmux_workspace_id"].lower() in open_ws:
                self.missing.pop(row["id"], None)
                continue
            count = self.missing.get(row["id"], 0) + 1
            self.missing[row["id"]] = count
            if count < CONFIRM_TICKS:
                continue
            self.missing.pop(row["id"], None)
            reason = "pause" if row["state"] == "pausing" else "closed"
            try:
                machine.transition(
                    db,
                    row["id"],
                    "vanished",
                    reason=reason,
                    log_kind="workspace_vanished",
                    detail={"workspace_id": row["cmux_workspace_id"], "via": "observer"},
                )
            except BerthError:
                continue
            actions.append({"berth_id": row["id"], "action": "parked", "reason": reason})
        for berth_id in list(self.missing):
            if berth_id not in seen:
                self.missing.pop(berth_id, None)
        return actions

    def _pausing(self, db: Catalog, open_ws: dict[str, Workspace]) -> list[dict]:
        """A pause cut off before it closed the pane: the row goes back to live (``STALE_PAUSE_SECONDS``)."""
        actions = []
        moment = parse_time(utc_now())
        for row in db.conn.execute("SELECT * FROM berth WHERE state = 'pausing'").fetchall():
            workspace_id = row["cmux_workspace_id"]
            if not workspace_id or workspace_id.lower() not in open_ws:
                continue  # its pane is gone: _missing parks it
            if age_seconds(row["updated_at"], moment) < STALE_PAUSE_SECONDS:
                continue
            try:
                machine.transition(db, row["id"], "relink", log_kind="reconcile",
                                   detail={"action": "close_did_not_happen", "via": "observer"})
            except BerthError:
                continue
            actions.append({"berth_id": row["id"], "action": "live", "why": "its pause never closed the pane"})
        return actions

    def _playing(self, db: Catalog, open_ws: dict[str, Workspace]) -> list[dict]:
        actions = []
        moment = parse_time(utc_now())
        for row in db.conn.execute("SELECT * FROM berth WHERE state = 'playing'").fetchall():
            workspace_id = row["cmux_workspace_id"]
            if workspace_id and workspace_id.lower() in open_ws:
                machine.transition(
                    db, row["id"], "relink", log_kind="reconcile", detail={"action": "play_landed", "via": "observer"}
                )
                actions.append({"berth_id": row["id"], "action": "live"})
            elif age_seconds(row["updated_at"], moment) >= STALE_PLAY_SECONDS:
                machine.transition(db, row["id"], "play_stale", reason="stale", log_kind="play_stale")
                actions.append({"berth_id": row["id"], "action": "play_stale"})
        return actions

    # workspaces no row links ----------------------------------------------

    def _unlinked(self, db, cmux, workspaces, open_ws, hooks) -> list[dict]:
        linked = {
            row[0].lower()
            for row in db.conn.execute(
                "SELECT cmux_workspace_id FROM berth WHERE cmux_workspace_id IS NOT NULL AND state != 'parked'"
            )
        }
        actions = []
        for key, workspace in open_ws.items():
            if key in linked:
                self.unknown.pop(key, None)
                continue
            if key in self.ghosts and (self.ghosts_until is None or self.clock() <= self.ghosts_until):
                # A pane cmux restored for a session that was open at the quit.
                # The session comes back through restore, not in this idle pane.
                self.unknown.pop(key, None)
                actions.append(self._close_ghost(db, cmux, self.ghosts.pop(key), workspace))
                continue
            parked = last_link(db, workspace.id)
            count = self.unknown.get(key, 0) + 1
            if parked is not None and (parked["park_reason"] != "pause" or count >= CONFIRM_TICKS):
                self.unknown.pop(key, None)
                actions.append(self._returned(db, cmux, parked, workspace))
                continue
            # A paused row's pane is relinked only when seen open on two lists
            # in a row: a list taken just before the pause closed it is stale.
            self.unknown[key] = count
            if parked is not None or count < CONFIRM_TICKS:
                continue
            self.unknown.pop(key, None)
            from berth.adopt import _safe_groups, _safe_surfaces, adopt_workspace

            record = adopt_workspace(
                db,
                workspace,
                hook=hook_for(hooks, workspace.id),
                surfaces=_safe_surfaces(cmux, workspace.id),
                groups=_safe_groups(cmux),
                cmux=cmux,
            )
            actions.append({"berth_id": record["id"], "action": "adopted", "workspace_id": workspace.id})
        for key in list(self.unknown):
            if key not in open_ws:
                self.unknown.pop(key, None)
        return actions

    def _close_ghost(self, db: Catalog, cmux, berth_id: str, workspace: Workspace) -> dict:
        if _running(cmux, workspace):
            return self._keep_running(db, berth_id, workspace)
        try:
            cmux.close_workspace(workspace.id)
        except BerthError as exc:
            with db.immediate():
                db.insert_event(berth_id, "restore", {"action": "close_failed", "workspace_id": workspace.id,
                                                      "message": exc.message})
            return {"berth_id": berth_id, "action": "close_failed", "workspace_id": workspace.id}
        with db.immediate():
            db.insert_event(berth_id, "restore", {"action": "closed_restored_pane", "workspace_id": workspace.id})
        return {"berth_id": berth_id, "action": "closed_restored", "workspace_id": workspace.id}

    def _keep_running(self, db: Catalog, berth_id: str, workspace: Workspace) -> dict:
        """A pane with a live agent is never closed. Its row goes back on it."""
        row = db.get_berth(berth_id)
        if row is not None and row["state"] == "parked":
            machine.transition(
                db, berth_id, "relink", workspace_id=workspace.id, log_kind="restore",
                detail={"action": "kept_running_pane", "was": row["park_reason"]},
            )
        else:
            with db.immediate():
                db.insert_event(berth_id, "restore", {"action": "kept_running_pane", "workspace_id": workspace.id})
        if berth_id in self.quit_rows:
            self.quit_rows.remove(berth_id)
        return {"berth_id": berth_id, "action": "kept_running", "workspace_id": workspace.id}

    def _returned(self, db: Catalog, cmux, row, workspace: Workspace) -> dict:
        """A workspace came back under a parked row's last UUID."""
        if row["park_reason"] == "cmux_quit" and not is_screen(row) and _running(cmux, workspace):
            return self._keep_running(db, row["id"], workspace)
        if row["park_reason"] == "cmux_quit" and not is_screen(row):
            try:
                cmux.close_workspace(workspace.id)
            except BerthError as exc:
                machine.transition(
                    db,
                    row["id"],
                    "relink",
                    workspace_id=workspace.id,
                    log_kind="restore",
                    detail={"action": "close_failed", "message": exc.message},
                )
                return {"berth_id": row["id"], "action": "relinked", "workspace_id": workspace.id}
            with db.immediate():
                db.insert_event(row["id"], "restore", {"action": "closed_restored_pane", "workspace_id": workspace.id})
            return {"berth_id": row["id"], "action": "closed_restored", "workspace_id": workspace.id}
        was_quit = row["park_reason"] == "cmux_quit"
        machine.transition(
            db,
            row["id"],
            "relink",
            workspace_id=workspace.id,
            log_kind="restore",
            detail={"action": "reappeared", "was": row["park_reason"]},
        )
        if was_quit and is_screen(row):
            _start_screen(cmux, workspace.id)
        return {"berth_id": row["id"], "action": "relinked", "workspace_id": workspace.id}

    # the screen comes back ------------------------------------------------

    def _screen(self, db: Catalog, cmux) -> list[dict]:
        """cmux did not restore the screen's pane. Open a new one."""
        if not self.came_back or self.up_since is None:
            return []
        if self.clock() - self.up_since < SCREEN_START_SECONDS:
            return []
        self.came_back = False
        live = db.conn.execute("SELECT * FROM berth WHERE state != 'parked'").fetchall()
        if any(is_screen(row) for row in live):
            return []
        parked = [
            row
            for row in db.conn.execute("SELECT * FROM berth WHERE state = 'parked' AND park_reason = 'cmux_quit'")
            if is_screen(row)
        ]
        if not parked:
            return []
        row = parked[0]
        machine.transition(db, row["id"], "play_claim", workspace_id=None, log_kind="restore")
        try:
            created = cmux.create_workspace(
                title=row["title"], cwd=row["cwd"] or "/", command="berth", focus=True
            )
        except BerthError:
            machine.transition(db, row["id"], "play_failed", reason="cmux_quit", log_kind="restore")
            return []
        current = db.require_berth(row["id"])
        if current["state"] == "playing":
            machine.transition(
                db,
                row["id"],
                "play_created",
                workspace_id=created.id,
                log_kind="restore",
                detail={"action": "screen_started"},
            )
        return [{"berth_id": row["id"], "action": "screen_started", "workspace_id": created.id}]


_UNKNOWN = object()


def _app_identity(cmux):
    """``(pid, start)`` of the cmux app, None when it is not running, or _UNKNOWN."""
    reader = getattr(cmux, "app_process", None)
    if reader is None:
        return _UNKNOWN
    try:
        found = reader()
    except OSError:
        return _UNKNOWN
    return tuple(found) if found is not None else None


def _running(cmux, workspace: Workspace) -> bool:
    check = getattr(cmux, "pane_has_agent", None)
    if check is None:
        return False
    try:
        return bool(check(workspace))
    except Exception:
        return True


def _saved_app(path: Path | None) -> tuple | None:
    if path is None:
        return None
    try:
        import json

        value = json.loads(path.read_text(encoding="utf-8")).get("cmux_app")
    except (OSError, ValueError, AttributeError):
        return None
    return tuple(value) if isinstance(value, list) else None


def _save_app(path: Path | None, app: tuple) -> None:
    if path is None:
        return
    import json

    try:
        path.write_text(json.dumps({"cmux_app": list(app)}) + "\n", encoding="utf-8")
        path.chmod(0o600)
    except OSError:
        pass


def last_link(db: Catalog, workspace_id: str):
    """The parked row that last held this workspace UUID, newest first.

    A row parked ``replaced`` gave its pane to another session, and a
    ``merged`` row was folded into another row. Neither takes the pane back.
    """
    return db.conn.execute(
        """
        SELECT * FROM berth
        WHERE state = 'parked' AND last_workspace_id = ? COLLATE NOCASE
          AND COALESCE(park_reason, '') NOT IN ('replaced', 'merged')
        ORDER BY parked_at DESC, id
        LIMIT 1
        """,
        (workspace_id,),
    ).fetchone()


def _has_quit_rows(db: Catalog) -> bool:
    return db.conn.execute("SELECT 1 FROM berth WHERE park_reason = 'cmux_quit' LIMIT 1").fetchone() is not None


def park_for_quit(db: Catalog, *, now: str | None = None) -> list[dict]:
    """cmux is gone. Every linked row parks as ``cmux_quit``, in one transaction."""
    stamp = now or utc_now()
    moment = parse_time(stamp)
    actions = []
    with db.immediate():
        for row in db.conn.execute(
            "SELECT * FROM berth WHERE state IN ('live', 'playing', 'pausing')"
        ).fetchall():
            machine.transition(
                db,
                row["id"],
                "vanished",
                reason="cmux_quit",
                log_kind="workspace_vanished",
                detail={"via": "cmux_quit"},
                now=stamp,
            )
            actions.append({"berth_id": row["id"], "action": "parked", "reason": "cmux_quit",
                            "workspace_id": row["cmux_workspace_id"]})
        for row in db.conn.execute(
            "SELECT * FROM berth WHERE state = 'parked' AND park_reason = 'closed' AND parked_at IS NOT NULL"
        ).fetchall():
            try:
                age = age_seconds(row["parked_at"], moment)
            except ValueError:
                continue
            if age <= QUIT_WINDOW_SECONDS:
                machine.transition(
                    db, row["id"], "relabel", reason="cmux_quit", log_kind="workspace_vanished",
                    detail={"via": "cmux_quit", "was": "closed"}, now=stamp,
                )
                actions.append({"berth_id": row["id"], "action": "relabeled", "reason": "cmux_quit",
                                "workspace_id": row["last_workspace_id"]})
    return actions


def _start_screen(cmux, workspace_id: str) -> None:
    """A restored screen pane is a plain shell. Start ``berth`` in it once."""
    if _screen_running(cmux, workspace_id):
        return
    try:
        cmux.send_text(workspace_id, "berth\n")
    except BerthError:
        pass


def _screen_running(cmux, workspace_id: str) -> bool:
    reader = getattr(cmux, "top_processes", None)
    if reader is None:
        return False
    try:
        surfaces = {item.ref for item in cmux.surfaces(workspace_id) if item.ref}
        table = reader()
    except BerthError:
        return False
    for line in table.splitlines():
        parts = line.split("\t")
        if len(parts) < 6 or parts[3] != "process" or parts[5] not in surfaces:
            continue
        command = _command(parts[4])
        if "/berth" in command and " watch" not in command:
            return True
    return False


def _command(pid: str) -> str:
    from berth import proc

    return proc.command(pid)  # berth/proc.py
