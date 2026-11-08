"""Berth tools. Agents see catalog rows, not cmux groups."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

# Berth's home since 2026-10-28; a bundle that ships cmux-mcp next to berth uses the sibling.
_FORGE_BERTH = Path.home() / "FORGE" / "systems" / "agents" / "berth"
_REPO_BERTH = Path(__file__).resolve().parents[2] / "berth"
_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_WS_REF = re.compile(r"^workspace:\d+$")


# The session that calls (2026-11-06, Berth's shared server). A stdio server is one session's, so the session's
# CMUX_WORKSPACE_ID is in its environment. One shared server (berth mcp --http) serves every session, so it sets
# CALLER for each request from the request's X-Berth-Workspace header; an empty one means "not in cmux".
import contextvars as _contextvars

_UNSET = object()
CALLER: "_contextvars.ContextVar" = _contextvars.ContextVar("cmux_mcp_caller", default=_UNSET)


def caller_workspace() -> str | None:
    value = CALLER.get()
    if value is _UNSET:
        return os.environ.get("CMUX_WORKSPACE_ID")
    return value or None


def resolve_berth_root(
    env: Mapping[str, str] | None = None,
    repo: Path | None = None,
) -> Path | None:
    """Directory that contains the berth package.

    BERTH_ROOT wins when that tree exists. A missing override falls back to
    Berth's FORGE home, then to a sibling checkout, so a tool call does not
    fail just because the default path is absent.
    """
    source = os.environ if env is None else env
    fallbacks = [_FORGE_BERTH, _REPO_BERTH] if repo is None else [repo]
    override = source.get("BERTH_ROOT")
    if override:
        candidate = Path(override).expanduser()
        if (candidate / "berth" / "__init__.py").is_file():
            return candidate
    for fallback in fallbacks:
        if (fallback / "berth" / "__init__.py").is_file():
            return fallback
    return None


BERTH_ROOT = resolve_berth_root()
if BERTH_ROOT is not None and str(BERTH_ROOT) not in sys.path:
    sys.path.insert(0, str(BERTH_ROOT))

from berth.adopt import adopt_appearing  # noqa: E402
from berth.color import paint_berth  # noqa: E402
from berth.cmux import Cmux  # noqa: E402
from berth.db import Catalog, default_catalog_path  # noqa: E402
from berth.errors import BerthError  # noqa: E402
from berth.hooks import hook_for, load_hooks  # noqa: E402
from berth.cmux import child_rss_bytes  # noqa: E402
from berth.ops import folder_path, format_bytes, mkdir_path, move_berth, rename_berth, rmdir_folder  # noqa: E402
from berth.place import cwd_matches, is_berth_command, normalize_cwd, pid_alive, split_command  # noqa: E402
from berth.model import is_idle, is_sleeping  # noqa: E402
from berth.observer import SCREEN_TITLE, is_screen  # noqa: E402
from berth.state import pause_berth, play_berth  # noqa: E402
from cmux_mcp.ops import FILE_MAX_BYTES, SHORT_TEXT_MAX, CmuxOps  # noqa: E402


class ToolError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}

    def payload(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": str(self), "details": self.details}}


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _string(description: str) -> dict[str, str]:
    return {"type": "string", "description": description}


def _bool(description: str) -> dict[str, str]:
    return {"type": "boolean", "description": description}


def _int(description: str) -> dict[str, str]:
    return {"type": "integer", "description": description}


_SESSION_KINDS = "grok, other-agent (Claude, Codex, ...: see agent), shell, process, or berth (the Berth screen)."

TOOLS: list[dict[str, Any]] = [
    {"name": "status", "description": "Berth at a glance: cmux up or down, open workspaces, live, sleeping, parked, playing, and pausing rows, workspaces with no row, the watcher, and drift (the violations the watcher saw last; call check for a fresh look).", "inputSchema": _schema({}, [])},
    {"name": "check", "description": "Does Berth match cmux right now? ok, plus each violation: open_unlinked, double_link, live_missing, name_mismatch, parked_linked, stuck, session_twice, dead_session, background_job. Read-only; the same as berth check.", "inputSchema": _schema({}, [])},
    {"name": "folders", "description": "Every Berth folder with its full path (Parent/Child: folders nest) and its live and parked counts. These are not cmux groups.", "inputSchema": _schema({"archived": _bool("Include archived folders.")}, [])},
    {"name": "list", "description": "One page of Berth rows, most recently used first (the Berth screen's order). Each row: id, title, folder, kind, agent, state, park_reason, last_used, protect. An idle row is closed to free memory and in use again soon; a sleeping row is closed but meant to come back: wake opens either. A parked row is paused: put away. No excerpts.", "inputSchema": _schema({"folder": _string("Folder path or id."), "subfolders": _bool("With folder: include every folder inside it."), "state": _string("live, idle, sleeping, parked (paused), playing, or pausing. Several with commas: live,idle."), "kind": _string(_SESSION_KINDS), "agent": _string("claude, grok, codex, cursor, or antigravity."), "park_reason": _string("pause, closed, cmux_quit, fork, replaced, merged, stale, play_failed, background, or new."), "sort": _string("recent (default, newest use first) or name."), "limit": _int("1 to 40. Default 40."), "after": _string("Berth id to start after."), "archived": _bool("Include archived folders.")}, [])},
    {"name": "get", "description": "One row in full: the list fields plus cwd, note, session, workspace, fork_of, and recipe.", "inputSchema": _schema({"id": _string("Berth id, workspace UUID, or workspace:N of a live row.")}, ["id"])},
    {"name": "mkdir", "description": "Make a Berth folder. A slash path makes every missing level: 'HOMESTEAD/Tutors' makes Tutors inside HOMESTEAD, and HOMESTEAD too if it is missing. A path that exists is returned unchanged. Inbox holds no folders. Not a cmux group.", "inputSchema": _schema({"path": _string("Slash path from the top level, e.g. Parent/Child.")}, ["path"])},
    {"name": "rmdir", "description": "Delete a Berth folder. Its sessions move to the parent, or to Inbox when the folder is at the top level. Child folders move up one level. Does not close a workspace and does not delete a session.", "inputSchema": _schema({"path": _string("Folder path or id.")}, ["path"])},
    {"name": "move_folder", "description": "Put a folder, with its sessions and subfolders, inside another folder, or at the top level when into is omitted. The same as m on a folder in the Berth screen. Nothing closes. Refused: moving Inbox, a folder into itself, a name already used there.", "inputSchema": _schema({"path": _string("Folder path or id to move."), "into": _string("Destination folder path or id. Omit or '' for the top level.")}, ["path"])},
    {"name": "rename_folder", "description": "Rename one folder in place; its sessions and subfolders stay in it. The same as r on a folder in the Berth screen. Inbox keeps its name.", "inputSchema": _schema({"path": _string("Folder path or id."), "name": _string("New name, one level, no slash.")}, ["path", "name"])},
    {"name": "move", "description": "File one session into a folder. Its workspace keeps running.", "inputSchema": _schema({"id": _string("Berth id."), "folder": _string("Folder path or id.")}, ["id", "folder"])},
    {"name": "rename", "description": "One name everywhere for one session: the Berth row, the cmux sidebar, and the agent's own session name. Berth types /rename into a live Claude, Grok, Codex, Cursor, or Antigravity session itself, and writes a parked session's name into its files. Do not also type /rename.", "inputSchema": _schema({"id": _string("Berth id."), "title": _string("The one name every side will use.")}, ["id", "title"])},
    {"name": "create", "description": "Open a new workspace running command (or a shell) and file it in a Berth folder. For a session that already has a row use play; for a copy of a session use fork.", "inputSchema": _schema({"title": _string("Name."), "cwd": _string("Existing directory."), "command": _string("Terminal command."), "folder": _string("Berth folder path or id. Omit only when Inbox is acceptable."), "focus": _bool("Focus the new workspace.")}, ["title", "cwd"])},
    {"name": "play", "description": "Open a parked session in a new workspace and resume it, as Enter does in the Berth screen: Claude with claude -r, Grok with grok -r and its flags; a fork that never wrote its conversation forks again under its own id. A bg row's background copy is stopped and resumed in a pane. A live row is left as is, or focused when focus is true. A process row reruns its command only with confirm.", "inputSchema": _schema({"id": _string("Berth id."), "focus": _bool("Select the workspace."), "confirm": _bool("Required for a process row.")}, ["id"])},
    {"name": "fork", "description": "Fork a Claude or Grok session, exactly as F does in the Berth screen: the agent copies the conversation under a new session id that Berth chooses, and it opens in a new live workspace in the same Berth folder, titled with ⑂ and focused. The result's session is the fork's own id at once. The original is untouched and keeps running. Use this for every fork; never type /fork into a pane.", "inputSchema": _schema({"id": _string("Berth id of a Claude or Grok session."), "title": _string("Name for the fork. Default: the source title plus ⑂."), "focus": _bool("Select the new workspace. Default true, as F does.")}, ["id"])},
    {"name": "pause", "description": "Park one session, as p does in the Berth screen: save its recipe and the last screen lines, then close its workspace. The session itself is kept; play reopens it. A process row needs confirm because closing stops it.", "inputSchema": _schema({"id": _string("Berth id."), "confirm": _bool("Required for a process row.")}, ["id"])},
    {"name": "cycle", "description": "Turn one live session off and on again, as c does in the Berth screen: Berth pauses it (recipe and last screen saved, workspace closed), then plays the same session in a new workspace in the same folder, without focus. The conversation continues; a turn in progress stops. A process reruns its command only with confirm.", "inputSchema": _schema({"id": _string("Berth id of a live session."), "confirm": _bool("Required for a process row."), "focus": _bool("Select the new workspace. Default false.")}, ["id"])},
    {"name": "sleep", "description": "Put sessions to sleep, as S and Z do in the Berth screen: each live one is closed with its recipe and last screen saved, like pause, but marked sleeping so wake (W) brings it back with the rest; a paused one just becomes sleeping. One id, every live session in a folder and its subfolders, or all live sessions (all, for before an update or a cmux restart). The Berth screen and the caller's own workspace are skipped. folder and all need confirm; without it the result lists what would sleep.", "inputSchema": _schema({"id": _string("Berth id of one session."), "folder": _string("Folder path or id: every live session in it and its subfolders."), "all": _bool("Every live session but the Berth screen."), "confirm": _bool("Required with folder or all.")}, [])},
    {"name": "wake", "description": "Open sleeping sessions again, as W (all) and Enter (one) do in the Berth screen: each resumes its conversation in a new workspace in its folder, without taking focus. A cmux restart wakes what it closed by itself.", "inputSchema": _schema({"id": _string("Berth id of one sleeping session."), "all": _bool("Every sleeping session.")}, [])},
    {"name": "park_folder", "description": "Park every live session in a folder and in all of its subfolders, as p on a folder does in the Berth screen. Without confirm nothing closes: the result lists what would be parked. The Berth screen and the caller's own workspace are skipped.", "inputSchema": _schema({"path": _string("Folder path or id."), "confirm": _bool("Close them. Without it, only the plan is returned.")}, ["path"])},
    {"name": "restore", "description": "Reopen the sessions the last cmux quit or restart parked (park_reason cmux_quit), each in its own workspace without focus, as berth restore does. Process rows stay parked.", "inputSchema": _schema({}, [])},
    {"name": "delete", "description": "Move one session's row to Berth's trash, as d does in the Berth screen. A live row's workspace is closed first. The trash keeps it 30 days; untrash brings it back to Inbox. The agent's session files stay on disk. The Berth screen and the caller's own workspace are refused. Needs confirm.", "inputSchema": _schema({"id": _string("Berth id."), "confirm": _bool("Required.")}, ["id", "confirm"])},
    {"name": "trash", "description": "What is in Berth's trash: sessions deleted in the last 30 days, newest first, with the folder each was in and the days it has left. The same list as ctrl-t in the Berth screen.", "inputSchema": _schema({}, [])},
    {"name": "untrash", "description": "Bring one session back from the trash, as Enter does in the screen's trash: it returns parked, in Inbox, with its own id, session, and history. play then reopens it.", "inputSchema": _schema({"id": _string("Trash id, id prefix, or title (from trash).")}, ["id"])},
    {"name": "focus", "description": "Select a live workspace. Does not drive the Berth screen.", "inputSchema": _schema({"id": _string("Berth id.")}, ["id"])},
    {"name": "screen", "description": "Last lines of one live pane. Not the Berth screen.", "inputSchema": _schema({"id": _string("Berth id."), "lines": _int("1 to 40. Default 20.")}, ["id"])},
    {"name": "input", "description": "Write a live pane's prompt or terminal. Exactly one of text, path, or key. The file body is not returned. A prompt (path, or text with submit) into a Claude or Grok session that is not ready (a turn running, a question or permission open, or starting) is refused with what it is doing, unless force; keys and short answers are not checked.", "inputSchema": _schema({"id": _string("Berth id."), "target": _string("prompt or terminal."), "text": _string("One short line, at most 200 bytes."), "path": _string("Absolute file for the prompt box. Does not press Enter."), "key": _string("One key: enter, escape, down, up, tab, ctrl-c."), "submit": _bool("Press Enter after text."), "force": _bool("Type a prompt (path, or text with submit) although the session is not ready: in a turn, a dialog open, or starting. Berth's send is the usual way to prompt a session: it looks first and can wait, queue, or interrupt.")}, ["id", "target"])},
    {"name": "excerpt", "description": "Stored pause text for one row, clipped.", "inputSchema": _schema({"id": _string("Berth id.")}, ["id"])},
    {"name": "peers", "description": "Other machines on the Tailscale network. This Mac is not in the list.", "inputSchema": _schema({}, [])},
    {"name": "offer", "description": "Folders and parked rows this Mac can send. Live workspaces stay here. Session files are named and are not copied.", "inputSchema": _schema({}, [])},
    {"name": "preview", "description": "Compare a remote Berth offer with this catalog. Nothing is written. Pass a peer name, an https URL, or a snapshot file.", "inputSchema": _schema({"from": _string("Peer DNS name, https URL, or snapshot file."), "folder": _string("Limit the view to this folder path.")}, ["from"])},
    {"name": "merge", "description": "Add chosen folders from a remote offer as parked rows. Does not close a workspace, delete a local row, or copy session files. Name folders, or set all.", "inputSchema": _schema({"from": _string("Peer DNS name, https URL, or snapshot file."), "folders": {"type": "array", "items": {"type": "string"}, "description": "Folder paths to add, including their children."}, "all": _bool("Add every folder in the offer.")}, ["from"])},
]


_RESUME_FLAG = re.compile(r"(?:^|\s)(?:--resume|-r)(?:\s+|=)(\S+)")


def _shell_script(parts: list[str]) -> str | None:
    for index, token in enumerate(parts[:-1]):
        if token in {"-lc", "-c"}:
            return parts[index + 1]
    return None


def _resume_ids(command: str) -> list[str]:
    found: list[str] = []
    for match in _RESUME_FLAG.finditer(command):
        token = match.group(1).strip("\"'")
        if token and token not in found:
            found.append(token)
    return found


def _recency(row) -> str:
    """When the row was last used, as Berth's screen sorts it. ISO text sorts as time."""
    keys = set(row.keys())
    if "last_used_at" in keys and row["last_used_at"]:
        return str(row["last_used_at"])
    stamps = [str(row[name]) for name in ("played_at", "parked_at", "created_at") if name in keys and row[name]]
    return max(stamps, default="")


def _attachments(row) -> dict:
    try:
        data = json.loads(row["attachments"] or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class Api:
    def __init__(self, ops: CmuxOps, catalog: Path | None = None, cmux: Cmux | None = None) -> None:
        self.ops = ops
        self.catalog = catalog or default_catalog_path()
        self.cmux = cmux or Cmux()

    def __call__(self, name: str, arguments: dict[str, Any]) -> Any:
        handler = {
            "status": self.status,
            "check": self.check,
            "folders": self.folders,
            "list": self.list_rows,
            "get": self.get,
            "mkdir": self.mkdir,
            "rmdir": self.rmdir,
            "move_folder": self.move_folder,
            "rename_folder": self.rename_folder,
            "move": self.move,
            "rename": self.rename,
            "create": self.create,
            "play": self.play,
            "fork": self.fork,
            "pause": self.pause,
            "cycle": self.cycle,
            "sleep": self.sleep,
            "wake": self.wake,
            "park_folder": self.park_folder,
            "restore": self.restore,
            "delete": self.delete,
            "trash": self.trash,
            "untrash": self.untrash,
            "focus": self.focus,
            "screen": self.screen,
            "input": self.input_call,
            "excerpt": self.excerpt,
            "peers": self.peers,
            "offer": self.offer,
            "preview": self.preview,
            "merge": self.merge,
        }.get(name)
        if handler is None:
            raise ToolError("BAD_ARGUMENT", f"unknown tool {name}", {"tool": name})
        try:
            return handler(arguments)
        except ToolError:
            raise
        except BerthError as exc:
            details = dict(exc.details)
            details.pop("excerpt", None)
            raise ToolError(exc.code, str(exc), details) from exc
        except TypeError as exc:
            raise ToolError("INTERNAL", str(exc), {}) from exc

    def _db(self) -> Catalog:
        return Catalog(self.catalog).open()

    def _open_ids(self) -> set[str] | None:
        if not self.cmux.socket_up():
            return None
        try:
            return {item.id.lower() for item in self.cmux.list_workspaces()}
        except BerthError:
            return None

    def _prepare(self, db: Catalog) -> set[str] | None:
        # Read-only. The Berth watcher is the only thing that parks a row whose
        # workspace is gone (Berth's docs/BERTH-STATE-MACHINE.md).
        del db
        return self._open_ids()

    def _refs(self) -> dict[str, str]:
        if not self.cmux.socket_up():
            return {}
        try:
            return {item.id.lower(): item.ref for item in self.cmux.list_workspaces() if item.ref}
        except BerthError:
            return {}

    def _row(self, db: Catalog, berth_id: str):
        token = (berth_id or "").strip()
        if not token:
            raise ToolError("NOT_FOUND", "no berth", {"id": token})
        direct = db.get_berth(token)
        if direct is not None:
            return direct
        workspace_id = self._workspace_id_for_token(token)
        if workspace_id:
            matches = db.conn.execute(
                """
                SELECT * FROM berth
                WHERE cmux_workspace_id = ? COLLATE NOCASE AND state = 'live'
                """,
                (workspace_id,),
            ).fetchall()
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise ToolError(
                    "NOT_FOUND",
                    "more than one live berth for that workspace",
                    {"id": token, "workspace": workspace_id},
                )
        raise ToolError("NOT_FOUND", f"no berth {token}", {"id": token})

    def _workspace_id_for_token(self, token: str) -> str | None:
        """Cmux workspace id for a UUID or one workspace:N ref. Does not guess."""
        if _UUID.match(token):
            return token
        if not _WS_REF.match(token) or not self.cmux.socket_up():
            return None
        try:
            items = self.cmux.list_workspaces()
        except BerthError:
            return None
        found: list[str] = []
        for item in items:
            ref = getattr(item, "ref", None)
            item_id = str(getattr(item, "id", None) or "")
            if ref == token or item_id.lower() == token.lower():
                if item_id:
                    found.append(item_id)
        if len(found) == 1:
            return found[0]
        return None

    def _file_into(self, db: Catalog, berth_id: str, folder_id: str) -> None:
        """ops.move_berth takes a catalog and a folder path. Not a timestamp."""
        move_berth(db, berth_id, folder_path(db, folder_id))

    def _view(self, db: Catalog, row, present: set[str] | None, refs: dict[str, str]) -> dict[str, Any]:
        folder = folder_path(db, row["folder_id"])
        color_row = db.conn.execute("SELECT color FROM folder WHERE id = ?", (row["folder_id"],)).fetchone()
        color = color_row["color"] if color_row else None
        workspace_id = (row["cmux_workspace_id"] or "").lower()
        if is_idle(row["state"], row["park_reason"]):
            state = "idle"  # closed to free memory, in use again soon (Berth, 2026-11-05)
        else:
            state = "sleeping" if is_sleeping(row["state"], row["park_reason"]) else row["state"]
        ref = None
        if present is None and state == "live":
            state = "unseen"
        elif state == "live" and workspace_id and workspace_id not in (present or set()):
            state = "parked"
        elif workspace_id and present is not None and workspace_id in present:
            ref = refs.get(workspace_id)
        note = row["note"] or ""
        clipped_note = len(note) > 240
        command = None
        command_clipped = False
        if row["kind"] == "process":
            command = (row["recipe_display"] or "")[:120]
            command_clipped = len(row["recipe_display"] or "") > 120
        recipe = row["recipe_display"] or ""
        return {
            "id": row["id"],
            "title": row["title"],
            "folder": folder,
            "kind": row["kind"],
            "agent": row["agent_name"] if row["kind"] in {"grok", "other-agent"} else None,
            "state": state,
            "park_reason": row["park_reason"] if row["state"] == "parked" else None,
            "sleeping": is_sleeping(row["state"], row["park_reason"]),
            "last_used": _recency(row) or None,
            "protect": bool(row["protect"]),
            "cwd": row["cwd"] or "",
            "note": note[:240],
            "note_clipped": clipped_note,
            "color": color,
            "session": row["agent_session_id"] if row["kind"] in {"grok", "other-agent"} else None,
            "fork_of": _attachments(row).get("fork_of"),
            "workspace": ref,
            "recipe": recipe[:200],
            "command": command,
            "command_clipped": command_clipped,
        }

    def _require_live(self, view: dict[str, Any]) -> None:
        if view["kind"] == "berth" or view["title"] == SCREEN_TITLE:
            raise ToolError("KIND_BERTH", "the Berth screen is not a session", {"id": view["id"]})
        if view["state"] != "live" or not view["workspace"]:
            raise ToolError("NOT_LIVE", "that row is not an open workspace", {"id": view["id"], "state": view["state"]})

    def status(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        db = self._db()
        try:
            present = self._prepare(db)
            from berth.watch import watcher_state

            watcher = watcher_state(db.path.parent)
            parked = db.conn.execute("SELECT COUNT(*) FROM berth WHERE state = 'parked'").fetchone()[0]
            playing = db.conn.execute("SELECT COUNT(*) FROM berth WHERE state = 'playing'").fetchone()[0]
            pausing = db.conn.execute("SELECT COUNT(*) FROM berth WHERE state = 'pausing'").fetchone()[0]
            if present is None:
                return {
                    "cmux": "down",
                    "open": None,
                    "live": None,
                    "parked": parked,
                    "playing": playing,
                    "pausing": pausing,
                    "unfiled": None,
                    "watcher": watcher,
                    "children": None,
                }
            children = None
            try:
                children = format_bytes(child_rss_bytes(self.cmux.memory()))
            except BerthError:
                children = None
            live = db.conn.execute("SELECT COUNT(*) FROM berth WHERE state = 'live'").fetchone()[0]
            known = {
                (row["cmux_workspace_id"] or "").lower()
                for row in db.conn.execute("SELECT cmux_workspace_id FROM berth WHERE state = 'live'")
            }
            return {
                "cmux": "up",
                "open": len(present),
                "live": live,
                "parked": parked,
                "sleeping": db.conn.execute(
                    "SELECT COUNT(*) FROM berth WHERE state = 'parked' AND park_reason IN ('sleep', 'cmux_quit')"
                ).fetchone()[0],
                "playing": playing,
                "pausing": pausing,
                "unfiled": len(present - known),
                "watcher": watcher,
                "children": children,
                "drift": self._drift(db),
            }
        finally:
            db.close()

    def _drift(self, db: Catalog) -> dict[str, Any] | None:
        """What the watcher's invariant check saw last (invariant.json)."""
        try:
            document = json.loads((db.path.parent / "invariant.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        violations = document.get("violations") or []
        return {
            "ok": bool(document.get("ok")),
            "at": document.get("at"),
            "violations": [f"{item.get('rule')}: {item.get('message')}" for item in violations][:10],
        }

    def check(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        from berth.invariant import run

        db = self._db()
        try:
            return run(db, self.cmux)
        finally:
            db.close()

    def folders(self, arguments: dict[str, Any]) -> dict[str, Any]:
        archived = bool(arguments.get("archived"))
        db = self._db()
        try:
            self._prepare(db)
            rows = []
            for folder in db.all_folders():
                if folder["archived"] and not archived:
                    continue
                counts = db.conn.execute(
                    "SELECT state, COUNT(*) n FROM berth WHERE folder_id = ? GROUP BY state",
                    (folder["id"],),
                ).fetchall()
                by = {item["state"]: item["n"] for item in counts}
                rows.append(
                    {
                        "id": folder["id"],
                        "path": folder_path(db, folder["id"]),
                        "color": folder["color"],
                        "note": folder["note"] or "",
                        "default_cwd": folder["default_cwd"],
                        "live": by.get("live", 0),
                        "parked": by.get("parked", 0),
                    }
                )
            rows.sort(key=lambda item: item["path"])
            total = len(rows)
            return {"folders": rows[:200], "total": total, "truncated": total > 200}
        finally:
            db.close()

    def list_rows(self, arguments: dict[str, Any]) -> dict[str, Any]:
        limit = int(arguments.get("limit") or 40)
        if limit < 1 or limit > 40:
            raise ToolError("LIMIT", "limit is 1 to 40", {"limit": limit})
        state = arguments.get("state")
        if state == "unseen":
            raise ToolError("BAD_ARGUMENT", "unseen is not a stored state", {})
        db = self._db()
        try:
            present = self._prepare(db)
            folder_filter = arguments.get("folder")
            folder_id = None
            folder_ids: set[str] = set()
            if folder_filter:
                folder_id = self._resolve_folder(db, str(folder_filter))
                folder_ids = {folder_id}
                if arguments.get("subfolders"):
                    folder_ids |= self._descendants(db, folder_id)
            kind = arguments.get("kind")
            agent = str(arguments.get("agent") or "").casefold()
            reason = arguments.get("park_reason")
            sort = str(arguments.get("sort") or "recent")
            if sort not in {"recent", "name"}:
                raise ToolError("BAD_ARGUMENT", "sort is recent or name", {"sort": sort})
            archived = bool(arguments.get("archived"))
            built = []
            for row in db.conn.execute("SELECT * FROM berth"):
                folder = db.conn.execute("SELECT archived FROM folder WHERE id = ?", (row["folder_id"],)).fetchone()
                if folder and folder["archived"] and not archived:
                    continue
                if folder_ids and row["folder_id"] not in folder_ids:
                    continue
                if kind and row["kind"] != kind:
                    continue
                if agent and (row["agent_name"] or "").casefold() != agent:
                    continue
                if reason and row["park_reason"] != reason:
                    continue
                view = self._view(db, row, present, {})
                if state and view["state"] not in {part.strip() for part in str(state).split(",")}:
                    continue
                built.append(
                    {
                        "id": view["id"],
                        "title": view["title"],
                        "folder": view["folder"],
                        "kind": view["kind"],
                        "agent": view["agent"],
                        "state": view["state"],
                        "park_reason": view["park_reason"],
                        "last_used": view["last_used"],
                        "protect": view["protect"],
                    }
                )
            if sort == "name":
                built.sort(key=lambda item: (item["folder"], item["title"], item["id"]))
            else:
                built.sort(key=lambda item: (item["title"], item["id"]))
                built.sort(key=lambda item: item["last_used"] or "", reverse=True)
            total = len(built)
            after = arguments.get("after")
            page_source = built
            if after:
                index = next((i for i, item in enumerate(built) if item["id"] == after), None)
                if index is None:
                    raise ToolError("NOT_FOUND", "after is not in this list", {"after": after})
                page_source = built[index + 1 :]
            page = page_source[:limit]
            body: dict[str, Any] = {
                "state": state,
                "kind": kind,
                "sort": sort,
                "total": total,
                "returned": len(page),
                "rows": page,
            }
            if present is None:
                body["cmux"] = "down"
            if folder_id:
                body["folder"] = folder_path(db, folder_id)
                color = db.conn.execute("SELECT color FROM folder WHERE id = ?", (folder_id,)).fetchone()
                body["color"] = color["color"] if color else None
            if len(page_source) > limit and page:
                body["next"] = page[-1]["id"]
            return body
        finally:
            db.close()

    def get(self, arguments: dict[str, Any]) -> dict[str, Any]:
        db = self._db()
        try:
            present = self._prepare(db)
            row = self._row(db, str(arguments.get("id") or ""))
            return self._view(db, row, present, self._refs())
        finally:
            db.close()

    def mkdir(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = str(arguments.get("path") or "").strip()
        if not path:
            raise ToolError("BAD_ARGUMENT", "path is empty", {})
        db = self._db()
        try:
            before = {folder_path(db, row["id"]) for row in db.all_folders()}
            try:
                made = mkdir_path(db, path)
            except BerthError as exc:
                raise ToolError(exc.code, str(exc), exc.details) from exc
            after = {folder_path(db, row["id"]) for row in db.all_folders()}
            return {"id": made["id"], "path": made["path"], "created": sorted(after - before)}
        finally:
            db.close()

    def rmdir(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = str(arguments.get("path") or "").strip()
        if not path:
            raise ToolError("BAD_ARGUMENT", "path is empty", {})
        db = self._db()
        try:
            try:
                return rmdir_folder(db, path)
            except BerthError as exc:
                raise ToolError(exc.code, str(exc), exc.details) from exc
        finally:
            db.close()

    def move_folder(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from berth.ops import move_folder_path

        path = str(arguments.get("path") or "").strip()
        if not path:
            raise ToolError("BAD_ARGUMENT", "path is empty", {})
        db = self._db()
        try:
            return move_folder_path(db, path, str(arguments.get("into") or ""))
        finally:
            db.close()

    def rename_folder(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from berth.ops import rename_folder_path

        path = str(arguments.get("path") or "").strip()
        name = str(arguments.get("name") or "").strip()
        if not path or not name:
            raise ToolError("BAD_ARGUMENT", "path and name are required", {})
        db = self._db()
        try:
            return rename_folder_path(db, path, name)
        finally:
            db.close()

    def cycle(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from berth.ops import cycle_berth

        db = self._db()
        try:
            row = self._row(db, str(arguments.get("id") or ""))
            if is_screen(row):
                raise ToolError("KIND_BERTH", "the Berth screen is not cycled", {"id": row["id"]})
            self._require_up()
            if row["state"] != "live":
                raise ToolError("NOT_LIVE", "cycle turns a live session off and on; play opens a parked one", {"id": row["id"], "state": row["state"]})
            before = row["cmux_workspace_id"]
            try:
                cycle_berth(
                    db,
                    self.cmux,
                    row["id"],
                    confirm=arguments.get("confirm") is True,
                    focus=arguments.get("focus") is True,
                    hook=hook_for(load_hooks(), row["cmux_workspace_id"]),
                    caller_workspace_id=caller_workspace(),
                    pid_alive_fn=pid_alive,
                )
            except BerthError as exc:
                details = dict(exc.details)
                details.pop("excerpt", None)
                raise ToolError(exc.code, str(exc), details) from exc
            view = self._view(db, db.require_berth(row["id"]), self._open_ids(), self._refs())
            view["cycled_from"] = before
            return view
        finally:
            db.close()

    def sleep(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from berth.ops import require_folder, sleep_berth

        db = self._db()
        try:
            caller = caller_workspace()
            if arguments.get("id"):
                row = self._row(db, str(arguments["id"]))
                if row["state"] == "live":
                    self._require_up()
                done = sleep_berth(db, self.cmux, row["id"], pid_alive_fn=pid_alive, caller_workspace_id=caller,
                                   hook=hook_for(load_hooks(), row["cmux_workspace_id"]), confirm=True)
                return self._view(db, db.require_berth(done["id"]), self._open_ids(), self._refs())
            if not arguments.get("folder") and arguments.get("all") is not True:
                raise ToolError("BAD_ARGUMENT", "name an id, a folder, or all", {})
            folder_ids = None
            if arguments.get("folder"):
                folder_ids = {require_folder(db, str(arguments["folder"]))[0]}
                folder_ids |= self._descendants(db, next(iter(folder_ids)))
            chosen = []
            for row in db.conn.execute("SELECT * FROM berth WHERE state = 'live'"):
                if is_screen(row) or (folder_ids is not None and row["folder_id"] not in folder_ids):
                    continue
                if caller and (row["cmux_workspace_id"] or "").lower() == caller.lower():
                    continue
                chosen.append(row)
            plan = [{"id": row["id"], "title": row["title"]} for row in chosen]
            if arguments.get("confirm") is not True:
                return {"confirmed": False, "would_sleep": plan}
            self._require_up()
            slept, left = [], []
            hooks = load_hooks()
            for row in chosen:
                try:
                    sleep_berth(db, self.cmux, row["id"], pid_alive_fn=pid_alive, caller_workspace_id=caller,
                                hook=hook_for(hooks, row["cmux_workspace_id"]), confirm=True)
                    slept.append({"id": row["id"], "title": row["title"]})
                except BerthError as exc:
                    left.append({"id": row["id"], "title": row["title"], "code": exc.code})
            return {"confirmed": True, "slept": slept, "left": left}
        finally:
            db.close()

    def wake(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from berth.ops import wake_all

        self._require_up()
        db = self._db()
        try:
            if arguments.get("id"):
                row = self._row(db, str(arguments["id"]))
                if not is_sleeping(row["state"], row["park_reason"]):
                    raise ToolError("NOT_FOUND", f"{row['title']} is not sleeping", {"id": row["id"], "state": row["state"]})
                results = wake_all(db, self.cmux, ids=[row["id"]])
            elif arguments.get("all") is True:
                results = wake_all(db, self.cmux)
            else:
                raise ToolError("BAD_ARGUMENT", "name an id, or all", {})
            return {"woken": sum(1 for item in results if item["result"] == "woken"), "results": results}
        finally:
            db.close()

    def park_folder(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from berth.ops import pause_folder, require_folder

        path = str(arguments.get("path") or "").strip()
        if not path:
            raise ToolError("BAD_ARGUMENT", "path is empty", {})
        confirm = arguments.get("confirm") is True
        db = self._db()
        try:
            _folder_id, label = require_folder(db, path)
            if confirm:
                self._require_up()
            try:
                result = pause_folder(
                    db,
                    self.cmux,
                    label,
                    force=False,
                    confirm=confirm,
                    caller_workspace_id=caller_workspace(),
                    hooks=load_hooks(),
                )
            except BerthError as exc:
                if confirm or exc.code != "KIND_UNSAFE":
                    raise
                return {
                    "folder": label,
                    "confirmed": False,
                    "would_park": exc.details.get("berths", []),
                    "skipped": exc.details.get("skipped", []),
                }
            result["confirmed"] = True
            return result
        finally:
            db.close()

    def restore(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        from berth.restore import last_quit, restore_rows

        self._require_up()
        db = self._db()
        try:
            results = restore_rows(db, self.cmux, last_quit(db))
            for item in results:
                row = db.get_berth(item["berth_id"])
                item["title"] = row["title"] if row is not None else None
            return {"restored": sum(1 for item in results if item["result"] == "restored"), "results": results}
        finally:
            db.close()

    def delete(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from berth.ops import delete_berth

        if arguments.get("confirm") is not True:
            raise ToolError("BAD_ARGUMENT", "delete needs confirm true; it cannot be undone", {})
        db = self._db()
        try:
            row = self._row(db, str(arguments.get("id") or ""))
            if row["state"] != "parked":
                self._require_up()
            folder = folder_path(db, row["folder_id"])
            removed = delete_berth(db, self.cmux, row["id"], caller_workspace_id=caller_workspace())
            removed["folder"] = folder
            return removed
        finally:
            db.close()

    def trash(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        from berth import trash

        db = self._db()
        try:
            found = trash.items(db)
            return {"total": len(found), "days_kept": trash.TRASH_DAYS, "items": found[:100]}
        finally:
            db.close()

    def untrash(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from berth import trash

        db = self._db()
        try:
            item = trash.find(db, str(arguments.get("id") or ""))
            restored = trash.restore(db, item["id"])
            view = self._view(db, db.require_berth(restored["id"]), self._open_ids(), self._refs())
            view["restored_from"] = item["folder"] or "Inbox"
            return view
        finally:
            db.close()

    def _descendants(self, db: Catalog, folder_id: str) -> set[str]:
        found: set[str] = set()
        stack = [folder_id]
        while stack:
            for child in db.child_folders(stack.pop()):
                if child["id"] not in found:
                    found.add(child["id"])
                    stack.append(child["id"])
        return found

    def move(self, arguments: dict[str, Any]) -> dict[str, Any]:
        db = self._db()
        try:
            present = self._prepare(db)
            row = self._row(db, str(arguments.get("id") or ""))
            folder_id = self._resolve_folder(db, str(arguments.get("folder") or ""))
            self._file_into(db, row["id"], folder_id)
            try:
                paint_berth(db, self.cmux, row["id"])
            except Exception:
                pass
            fresh = db.require_berth(row["id"])
            return self._view(db, fresh, present, self._refs())
        finally:
            db.close()

    def rename(self, arguments: dict[str, Any]) -> dict[str, Any]:
        title = str(arguments.get("title") or "").strip()
        if not title:
            raise ToolError("BAD_ARGUMENT", "title is empty", {})
        db = self._db()
        try:
            row = self._row(db, str(arguments.get("id") or ""))
            if is_screen(row):
                raise ToolError("KIND_BERTH", "the Berth screen stays", {"id": row["id"]})
            if row["state"] == "live":
                self._require_up()
            present = self._prepare(db)
            rename_berth(db, self.cmux, row["id"], title)
            fresh = db.require_berth(row["id"])
            return self._view(db, fresh, present, self._refs())
        finally:
            db.close()

    def create(self, arguments: dict[str, Any]) -> dict[str, Any]:
        title = str(arguments.get("title") or "").strip()
        cwd = str(arguments.get("cwd") or "")
        if not title:
            raise ToolError("BAD_ARGUMENT", "title is empty", {})
        if not cwd or not Path(cwd).is_dir():
            raise ToolError("BAD_ARGUMENT", "cwd is not a directory", {"cwd": cwd})
        command = arguments.get("command")
        if command is not None:
            parts = split_command(str(command))
            if is_berth_command(parts) or any(Path(part).name == "berth" for part in parts):
                raise ToolError("KIND_BERTH", "do not spawn the Berth screen from here", {})
            inner = _shell_script(parts)
            if inner is not None:
                inner_parts = split_command(inner)
                if is_berth_command(inner_parts) or any(Path(part).name == "berth" for part in inner_parts):
                    raise ToolError("KIND_BERTH", "do not spawn the Berth screen from here", {})
        db = self._db()
        try:
            placed = self._place(db, arguments.get("folder"), cwd)
            if command is not None:
                self._refuse_resume(db, str(command))
            self._require_up()
            created = self.cmux.create_workspace(title=title, cwd=cwd, command=str(command) if command else None, focus=bool(arguments.get("focus")))
            record = adopt_appearing(db, self.cmux, load_hooks, created.id)
            if record is None:
                raise ToolError("CMUX_REJECTED", "the new workspace was not filed", {"workspace": created.ref})
            if record["folder_id"] != placed["id"]:
                self._file_into(db, record["id"], placed["id"])
            try:
                paint_berth(db, self.cmux, record["id"])
            except Exception:
                pass
            present = self._open_ids() or set()
            view = self._view(db, db.require_berth(record["id"]), present, self._refs())
            view["placed"] = placed["placed"]
            if placed["placed"] == "inferred":
                view["by"] = "default_cwd"
            if placed["placed"] == "inbox":
                view["reason"] = "no default_cwd matched"
            return view
        finally:
            db.close()

    def play(self, arguments: dict[str, Any]) -> dict[str, Any]:
        db = self._db()
        try:
            row = self._row(db, str(arguments.get("id") or ""))
            if is_screen(row):
                raise ToolError("KIND_BERTH", "the Berth screen is not a session", {"id": row["id"]})
            self._require_up()
            present = self._prepare(db)
            if row["state"] in {"playing", "pausing"}:
                raise ToolError("IN_PROGRESS", "that row is already changing", {"id": row["id"]})
            focus = bool(arguments.get("focus"))
            if row["state"] == "live" and present and (row["cmux_workspace_id"] or "").lower() in present:
                if focus:
                    self.cmux.select_workspace(row["cmux_workspace_id"])
                    played = "focus"
                else:
                    played = "live"
            else:
                play_berth(db, self.cmux, row["id"], focus=focus, run_command=bool(arguments.get("confirm")))
                played = "resume"
            view = self._view(db, db.require_berth(row["id"]), self._open_ids(), self._refs())
            view["played"] = played
            return view
        except BerthError as exc:
            details = dict(exc.details)
            details.pop("excerpt", None)
            raise ToolError(exc.code, str(exc), details) from exc
        finally:
            db.close()

    def fork(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from berth.ops import fork_berth

        db = self._db()
        try:
            row = self._row(db, str(arguments.get("id") or ""))
            self._require_up()
            title = arguments.get("title")
            record = fork_berth(
                db, self.cmux, row["id"], focus=arguments.get("focus") is not False,
                title=str(title) if isinstance(title, str) and title.strip() else None,
            )
            view = self._view(db, db.require_berth(record["id"]), self._open_ids(), self._refs())
            view["forked_from"] = row["id"]
            return view
        except BerthError as exc:
            raise ToolError(exc.code, str(exc), dict(exc.details)) from exc
        finally:
            db.close()

    def pause(self, arguments: dict[str, Any]) -> dict[str, Any]:
        db = self._db()
        try:
            row = self._row(db, str(arguments.get("id") or ""))
            if is_screen(row):
                raise ToolError("KIND_BERTH", "the Berth screen stays", {"id": row["id"]})
            self._require_up()
            self._prepare(db)
            try:
                pause_berth(
                    db,
                    self.cmux,
                    row["id"],
                    force=bool(arguments.get("confirm")),
                    confirm=bool(arguments.get("confirm")),
                    hook=hook_for(load_hooks(), row["cmux_workspace_id"]),
                    caller_workspace_id=caller_workspace(),
                    pid_alive_fn=pid_alive,
                )
            except BerthError as exc:
                details = dict(exc.details)
                details.pop("excerpt", None)
                raise ToolError(exc.code, str(exc), details) from exc
            return self._view(db, db.require_berth(row["id"]), self._open_ids(), self._refs())
        finally:
            db.close()

    def focus(self, arguments: dict[str, Any]) -> dict[str, Any]:
        db = self._db()
        try:
            row = self._row(db, str(arguments.get("id") or ""))
            if is_screen(row):
                raise ToolError("KIND_BERTH", "the Berth screen is not a session", {"id": row["id"]})
            self._require_up()
            present = self._prepare(db)
            view = self._view(db, row, present, self._refs())
            self._require_live(view)
            self.cmux.select_workspace(row["cmux_workspace_id"])
            view["focused"] = True
            return view
        finally:
            db.close()

    def screen(self, arguments: dict[str, Any]) -> dict[str, Any]:
        lines = int(arguments.get("lines") or 20)
        if lines < 1 or lines > 40:
            raise ToolError("LIMIT", "lines is 1 to 40", {"lines": lines})
        db = self._db()
        try:
            row = self._row(db, str(arguments.get("id") or ""))
            if is_screen(row):
                raise ToolError("KIND_BERTH", "the Berth screen is not a session", {"id": row["id"]})
            self._require_up()
            present = self._prepare(db)
            view = self._view(db, row, present, self._refs())
            self._require_live(view)
            payload = self.ops.screen(view["workspace"], None, lines)
            text = str(payload.get("text") or "")
            tail = "\n".join(text.splitlines()[-lines:])
            clipped = len(tail.encode("utf-8")) > 4000 or len(text.splitlines()) > lines
            if len(tail.encode("utf-8")) > 4000:
                tail = tail.encode("utf-8")[:4000].decode("utf-8", "ignore")
            return {"id": view["id"], "lines": lines, "clipped": clipped, "text": tail}
        finally:
            db.close()

    def input_call(self, arguments: dict[str, Any]) -> dict[str, Any]:
        present_keys = [name for name in ("text", "path", "key") if arguments.get(name) not in (None, "")]
        if len(present_keys) != 1:
            raise ToolError("BAD_ARGUMENT", "pass exactly one of text, path, or key", {})
        if arguments.get("path") and arguments.get("submit"):
            raise ToolError("BAD_ARGUMENT", "submit with path is refused", {})
        text = arguments.get("text")
        if isinstance(text, str) and ("\n" in text or len(text.encode("utf-8")) > SHORT_TEXT_MAX):
            raise ToolError("BAD_ARGUMENT", "text is one line up to 200 bytes", {})
        path = arguments.get("path")
        if path is not None:
            file = Path(str(path))
            if not file.is_file() or file.stat().st_size > FILE_MAX_BYTES:
                raise ToolError("BAD_ARGUMENT", "path is not a readable file under 512 KiB", {"path": str(path)})
            head = file.read_text(encoding="utf-8", errors="replace").lstrip()[:40]
            if arguments.get("target") == "prompt" and head.startswith("/"):
                # Claude never runs a pasted slash command: it arrives as a plain message. On 2026-10-30 a fork
                # read its pasted "/compact <note>" as its job and built the thing the note described.
                raise ToolError("BAD_ARGUMENT", "a pasted slash command is sent as a plain message, never run",
                                {"hint": "to compact, use the berth MCP: send {session, compact_file, prompt_file}, "
                                         "or create {from, compact_file, prompt_file} for fork and compact. A short "
                                         "one-line slash command goes as text (typed) instead"})
        db = self._db()
        try:
            row = self._row(db, str(arguments.get("id") or ""))
            if is_screen(row):
                raise ToolError("KIND_BERTH", "the Berth screen is not a session", {"id": row["id"]})
            self._require_up()
            present = self._prepare(db)
            view = self._view(db, row, present, self._refs())
            self._require_live(view)
            self._ready_for_prompt(row, arguments)
            result = self.ops.input_bytes(
                workspace=view["workspace"],
                target=str(arguments.get("target") or ""),
                text=text,
                path=None if path is None else str(path),
                key=arguments.get("key"),
                submit=bool(arguments.get("submit") or False),
                surface=None,
            )
            result["id"] = view["id"]
            if str(arguments.get("target") or "") == "prompt" and (text or path):
                # An agent typed this, not Dominik: it never answers the session's follow-up (berth/attention.py).
                from berth.attention import note_agent_input

                typed = text if text else Path(str(path)).read_text(encoding="utf-8", errors="replace")
                try:
                    note_agent_input(db, row["id"], typed, by="cmux-mcp input")
                except Exception:
                    pass  # the input went in; the record is best effort
            return result
        finally:
            db.close()

    def _ready_for_prompt(self, row, arguments: dict[str, Any]) -> None:
        """Look at the session before a prompt goes in (berth/send.py, 2026-10-30). Keys are never checked: they answer dialogs."""
        prompt = str(arguments.get("target") or "") == "prompt" and (
            arguments.get("path") not in (None, "") or (arguments.get("text") and arguments.get("submit")))
        if not prompt or arguments.get("force") is True:
            return
        from berth.activity import program_states
        from berth.send import KINDS, WHAT_TO_DO, agent_of, look

        # By the agent: Claude rows are kind other-agent, so a kind check never looked at a Claude pane (2026-11-04).
        if agent_of(row) not in KINDS:
            return
        seen = look(self.cmux, row, programs=program_states(Path(self.catalog).parent))
        if seen.situation == "ready":
            return
        raise ToolError("BUSY", f"{row['title']} is {seen.situation}, so nothing was typed. Berth's send looks first and "
                                "can wait (when idle), queue (now), or interrupt; force types anyway.",
                        {"id": row["id"], "status": seen.situation, "why": list(seen.activity.reasons),
                         "what_to_do": WHAT_TO_DO[seen.situation], "screen": seen.tail})

    def excerpt(self, arguments: dict[str, Any]) -> dict[str, Any]:
        db = self._db()
        try:
            row = self._row(db, str(arguments.get("id") or ""))
            if is_screen(row):
                raise ToolError("KIND_BERTH", "the Berth screen has no excerpt", {"id": row["id"]})
            text = row["excerpt"] or ""
            lines = text.splitlines()
            clipped = len(lines) > 40 or len(text.encode("utf-8")) > 4000
            tail = "\n".join(lines[-40:])
            if len(tail.encode("utf-8")) > 4000:
                tail = tail.encode("utf-8")[:4000].decode("utf-8", "ignore")
            return {"id": row["id"], "excerpt_at": row["excerpt_at"], "clipped": clipped, "text": tail}
        finally:
            db.close()

    def peers(self, arguments: dict[str, Any]) -> dict[str, Any]:
        del arguments
        from berth.offer import peers

        return {"peers": peers()}

    def offer(self, arguments: dict[str, Any]) -> dict[str, Any]:
        del arguments
        from berth.offer import plan, snapshot

        db = self._db()
        try:
            document = snapshot(db)
            return plan(db, document, take_all=True)
        finally:
            db.close()

    def preview(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from berth.offer import fetch_snapshot, plan

        source = str(arguments.get("from") or "")
        folder = arguments.get("folder")
        folders = [str(folder)] if folder else None
        db = self._db()
        try:
            return plan(db, fetch_snapshot(source), folders=folders, take_all=not folders)
        finally:
            db.close()

    def merge(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from berth.offer import fetch_snapshot, merge

        source = str(arguments.get("from") or "")
        raw = arguments.get("folders") or []
        folders = [str(item) for item in raw] if isinstance(raw, list) else []
        take_all = bool(arguments.get("all"))
        db = self._db()
        try:
            return merge(db, fetch_snapshot(source), folders=folders or None, take_all=take_all)
        finally:
            db.close()

    def _require_up(self) -> None:
        if not self.cmux.socket_up():
            raise ToolError("CMUX_DOWN", "cmux is down", {})

    def _refuse_resume(self, db: Catalog, command: str) -> None:
        matches = []
        for session_id in _resume_ids(command):
            for row in db.conn.execute(
                "SELECT * FROM berth WHERE agent_session_id = ?",
                (session_id,),
            ):
                matches.append(
                    {
                        "id": row["id"],
                        "title": row["title"],
                        "folder": folder_path(db, row["folder_id"]),
                        "state": row["state"],
                    }
                )
        if matches:
            raise ToolError(
                "SESSION_IN_USE",
                "that session already has a row",
                {"matches": matches[:8]},
            )

    def _resolve_folder(self, db: Catalog, requested: str) -> str:
        requested = requested.strip()
        if not requested:
            raise ToolError("BAD_ARGUMENT", "folder is empty", {})
        for row in db.all_folders():
            if row["id"] == requested and not row["archived"]:
                return row["id"]
        found = None
        try:
            from berth.ops import find_folder_path

            found = find_folder_path(db, requested)
        except BerthError as exc:
            raise ToolError(exc.code, str(exc), exc.details) from exc
        if found:
            return found
        last = requested.split("/")[-1]
        candidates = []
        for row in db.all_folders():
            path = folder_path(db, row["id"])
            if path.split("/")[-1] == last:
                candidates.append(path)
        raise ToolError("NOT_FOUND", f"no folder {requested}", {"folder": requested, "candidates": candidates[:8]})

    def _place(self, db: Catalog, folder: Any, cwd: str) -> dict[str, str]:
        if folder not in (None, ""):
            return {"id": self._resolve_folder(db, str(folder)), "placed": "named"}
        best_id = None
        best_len = -1
        for row in db.all_folders():
            if row["archived"] or not row["default_cwd"]:
                continue
            if cwd_matches(row["default_cwd"], cwd) and len(normalize_cwd(row["default_cwd"])) > best_len:
                best_id = row["id"]
                best_len = len(normalize_cwd(row["default_cwd"]))
        if best_id:
            return {"id": best_id, "placed": "inferred"}
        return {"id": db.ensure_inbox(), "placed": "inbox"}


def call(ops: CmuxOps, name: str, arguments: dict[str, Any]) -> Any:
    return Api(ops)(name, arguments)
