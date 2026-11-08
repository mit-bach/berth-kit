"""Which sessions an agent sees: one folder, the levels below it, the levels above it.

A scope starts at one folder: the caller's own (the folder of the Berth row
whose cmux workspace the caller runs in), a named folder, or the root ``/``
(above every top-level folder; it holds no sessions of its own). From there:

* ``down``: how many levels of subfolders to add. 0 (the default) is the
  folder alone; ``"all"`` is everything under it.
* ``up``: how many ancestors to add. 0 (the default) is none; ``"all"`` goes
  to the top level. An ancestor adds its own sessions only, not its other
  subfolders...
* ``branches``: ...unless this is on. Then the scope is the whole tree under
  the highest ancestor, cut ``down`` levels below the starting folder's level,
  so your parent's other subfolders come in at your own level.

A ``query`` searches titles (every word, any case), Berth ids, agent session
ids, and workspace ids. With a query and no folder the scope is everything.

The result is grouped by folder, top-down, each group with its ``level``
(0 is the starting folder, -1 its parent, 1 its children) and what the scope
left out underneath or beside it, so an agent knows where to widen.

``berth/mcp.py`` (the Berth MCP's ``sessions`` tool) and ``berth sessions``
on the command line both call ``query``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from berth.catalog import Snapshot, load
from berth.errors import BerthError

ALL = "all"
DEFAULT_FIELDS = ("id", "title", "state", "session")
EXTRA_FIELDS = (
    "agent", "folder", "cwd", "workspace", "last_used", "created", "note", "park_reason", "fork_of", "recipe",
    "config_dir", "account", "memory",
)
STATES = ("live", "busy", "idle", "sleeping", "parked", "background", "playing", "pausing")
DEFAULT_LIMIT = 100
MAX_LIMIT = 500


def levels(value: Any, name: str) -> int | None:
    """0, 1, 2, ... or None for ``"all"`` (also -1)."""
    if value is None or value == "":
        return 0
    if isinstance(value, str):
        text = value.strip().casefold()
        if text in {"all", "*", "-1"}:
            return None
        if text.isdigit():
            return int(text)
        raise BerthError("BAD_ARGUMENT", f"{name} is a number of levels or \"all\"", {name: value})
    if isinstance(value, bool):
        raise BerthError("BAD_ARGUMENT", f"{name} is a number of levels or \"all\"", {name: value})
    if isinstance(value, int):
        if value < 0:
            return None
        return value
    raise BerthError("BAD_ARGUMENT", f"{name} is a number of levels or \"all\"", {name: value})


def state_of(berth) -> str:
    """live, idle, sleeping, parked (paused), background, playing, or pausing."""
    from berth.model import closed_state

    return closed_state(berth.state, berth.park_reason)


def agent_of(kind: str, agent_name: str) -> str:
    """claude, grok, codex, ... ; shell; process; berth (the screen)."""
    if kind == "grok":
        return "grok"
    if kind == "other-agent":
        return (agent_name or "agent").casefold()
    return kind or "shell"


def is_screen_berth(berth) -> bool:
    return berth.kind == "berth" or berth.title == "Berth"


@dataclass
class Extra:
    """Columns the screen's snapshot does not carry."""

    session: str | None
    workspace: str | None
    recipe: str
    created: str
    fork_of: str | None
    config_dir: str | None = None  # the Claude Code config folder it runs on (berth/configdir.py)


def _extras(db_path: Path) -> dict[str, Extra]:
    import sqlite3

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        found = {}
        for row in conn.execute(
            "SELECT id, agent_session_id, cmux_workspace_id, recipe_display, created_at, attachments FROM berth"
        ):
            try:
                attachments = json.loads(row["attachments"] or "{}")
            except ValueError:
                attachments = {}
            found[row["id"]] = Extra(
                session=row["agent_session_id"] or None,
                workspace=row["cmux_workspace_id"] or None,
                recipe=row["recipe_display"] or "",
                created=row["created_at"] or "",
                fork_of=attachments.get("fork_of") if isinstance(attachments, dict) else None,
                config_dir=attachments.get("config_dir") if isinstance(attachments, dict) else None,
            )
        return found
    finally:
        conn.close()


class View:
    """One read of the catalog, with lookups for scopes and session references."""

    def __init__(self, db_path: Path | str, *, caller_workspace: str | None = None, archived: bool = False) -> None:
        self.db_path = Path(db_path)
        self.snap: Snapshot = load(self.db_path)
        self.extra = _extras(self.db_path)
        self.archived = archived
        self.caller = None
        workspace = (caller_workspace if caller_workspace is not None else os.environ.get("CMUX_WORKSPACE_ID", "")) or ""
        if workspace:
            for berth in self.snap.berths.values():
                if berth.state == "live" and (berth.workspace_id or "").lower() == workspace.lower():
                    self.caller = berth
                    break

    # folders -----------------------------------------------------------

    def visible_folder(self, folder_id: str) -> bool:
        folder = self.snap.folders.get(folder_id)
        while folder is not None:
            if folder.archived and not self.archived:
                return False
            folder = self.snap.folders.get(folder.parent_id) if folder.parent_id else None
        return True

    def children(self, folder_id: str | None) -> list[str]:
        return [item for item in self.snap.children.get(folder_id, []) if self.visible_folder(item)]

    def path(self, folder_id: str | None) -> str:
        return self.snap.folder_path(folder_id) if folder_id else "/"

    def depth(self, folder_id: str | None) -> int:
        """The root is 0; a top-level folder is 1."""
        count = 0
        current = folder_id
        while current:
            count += 1
            folder = self.snap.folders.get(current)
            current = folder.parent_id if folder else None
        return count

    def ancestors(self, folder_id: str | None) -> list[str]:
        """Parent first, then up to the top-level folder."""
        chain: list[str] = []
        folder = self.snap.folders.get(folder_id) if folder_id else None
        while folder is not None and folder.parent_id and folder.parent_id not in chain:
            chain.append(folder.parent_id)
            folder = self.snap.folders.get(folder.parent_id)
        return chain

    def folder(self, token: str | None) -> str | None:
        """A folder id from a path (any case), a unique last name, or an id. "/" is the root (None)."""
        text = (token or "").strip()
        if text in {"/", ""}:
            return None
        text = text.strip("/")
        if text in self.snap.folders and self.visible_folder(text):
            return text
        paths = {folder_id: self.path(folder_id) for folder_id in self.snap.folders if self.visible_folder(folder_id)}
        exact = [folder_id for folder_id, path in paths.items() if path.casefold() == text.casefold()]
        if len(exact) == 1:
            return exact[0]
        last = [folder_id for folder_id, path in paths.items() if path.split("/")[-1].casefold() == text.casefold()]
        if len(last) == 1:
            return last[0]
        if len(last) > 1:
            raise BerthError("AMBIGUOUS", f"{len(last)} folders are named {text}; give the full path",
                             {"folder": text, "matches": sorted(paths[item] for item in last)})
        near = sorted(path for path in paths.values() if text.casefold() in path.casefold())
        raise BerthError("NOT_FOUND", f"no folder {text}", {"folder": text, "candidates": near[:8]})

    def subtree(self, folder_id: str | None) -> list[str]:
        found: list[str] = []
        stack = list(reversed(self.children(folder_id)))
        while stack:
            current = stack.pop()
            found.append(current)
            stack.extend(reversed(self.children(current)))
        return found

    # sessions ----------------------------------------------------------

    def sessions_in(self, folder_id: str) -> list:
        rows = [self.snap.berths[item] for item in self.snap.berths_in.get(folder_id, []) if item in self.snap.berths]
        rows = [row for row in rows if not is_screen_berth(row)]
        rows.sort(key=lambda row: (row.title.casefold(), row.id))
        rows.sort(key=lambda row: row.last_used_at or "", reverse=True)
        return rows

    def session(self, token: str) -> Any:
        """One session from a Berth id or id prefix, a title, an agent session id, a workspace id, or "me"."""
        text = (token or "").strip()
        if not text:
            raise BerthError("BAD_ARGUMENT", "name a session", {})
        if text.casefold() in {"me", "self", "you"}:
            if self.caller is None:
                raise BerthError("NOT_FOUND", "this caller is not a Berth session (no cmux workspace)", {})
            return self.caller
        berths = list(self.snap.berths.values())
        low = text.casefold()
        if text in self.snap.berths:
            return self.snap.berths[text]
        for test in (
            lambda b: (self.extra.get(b.id) and (self.extra[b.id].session or "").casefold() == low),
            lambda b: (b.workspace_id or "").casefold() == low,
            lambda b: b.title.casefold() == low,
        ):
            hits = [b for b in berths if test(b)]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                return self._ambiguous(text, hits)
        if len(text) >= 4:
            hits = [b for b in berths if b.id.casefold().startswith(low)
                    or (self.extra.get(b.id) and (self.extra[b.id].session or "").casefold().startswith(low))]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                return self._ambiguous(text, hits)
        hits = [b for b in berths if matches(b, self.extra.get(b.id), text)]
        if len(hits) == 1:
            return hits[0]
        if hits:
            return self._ambiguous(text, hits)
        raise BerthError("NOT_FOUND", f"no session {text}", {"session": text})

    def _ambiguous(self, text: str, hits: list):
        raise BerthError(
            "AMBIGUOUS",
            f"{len(hits)} sessions match {text}; name one by id",
            {"session": text, "matches": [{"id": b.id, "title": b.title, "folder": self.path(b.folder_id),
                                           "state": state_of(b)} for b in hits[:10]]},
        )

    def _memory(self, berth) -> dict | None:
        from berth.memory import of_workspace

        if berth.state != "live" or not berth.workspace_id:
            return None
        return of_workspace(self.db_path.parent, berth.workspace_id)

    def row(self, berth, fields: tuple[str, ...]) -> dict[str, Any]:
        extra = self.extra.get(berth.id)
        values = {
            "id": berth.id,
            "title": berth.title,
            "state": state_of(berth),
            "session": extra.session if extra else None,
            "agent": agent_of(berth.kind, berth.agent_name),
            "folder": self.path(berth.folder_id),
            "cwd": berth.cwd or None,
            "workspace": (berth.workspace_id or None) if berth.state == "live" else None,
            "last_used": berth.last_used_at or None,
            "created": (extra.created if extra else "") or None,
            "note": berth.note or None,
            "park_reason": (berth.park_reason or None) if berth.state == "parked" else None,
            "fork_of": extra.fork_of if extra else None,
            "recipe": (extra.recipe if extra else berth.recipe_display) or None,
            # A Claude session's config folder; ~/.claude when none was set (berth/configdir.py).
            "config_dir": ((extra.config_dir if extra else None) or "~/.claude")
            if agent_of(berth.kind, berth.agent_name) == "claude" else None,
            # The Claude account it is on, from Settings Agent's files (berth/accounts.py).
            "account": getattr(berth, "account", "") or None,
            # A live session's memory, by part, from the watcher's latest measure (berth/memory.py).
            "memory": self._memory(berth) if "memory" in fields else None,
        }
        out = {name: values[name] for name in fields if values.get(name) is not None}
        if "account" in out and getattr(berth, "account_stale", False):
            out["account_stale"] = True  # on ~/.claude since before the last switch: cycle it for the new account
        if "folder" in out and "cwd" not in out and values.get("cwd"):
            # The Berth folder never comes without the working directory, so neither is read as the other.
            out["cwd"] = values["cwd"]
        if berth.state == "live" and getattr(berth, "status", ""):
            out["status"] = berth.status
            out["status_label"] = berth.status_label
        for name in ("urgent", "follow_up"):
            if getattr(berth, name, False):
                out[name] = {"why": berth.flag_why, "since": berth.flag_at}
        if berth.pinned:
            out["pinned"] = True
        if berth.busy and berth.state == "live":
            out["busy"] = True
            if getattr(berth, "busy_why", ()):
                out["busy_why"] = list(berth.busy_why)
        if berth.state == "live" and getattr(berth, "draft", ""):
            out["draft"] = "saved on close" if berth.draft == "saved" else f"cannot be saved exactly: {berth.draft}"
        if self.caller is not None and berth.id == self.caller.id:
            out["you"] = True
        return out


def matches(berth, extra: Extra | None, query: str) -> bool:
    """Every word of the query is in the title, or the query is an id, session, or workspace (or their start)."""
    text = (query or "").strip().casefold()
    if not text:
        return True
    title = berth.title.casefold()
    if all(word in title for word in text.split()):
        return True
    keys = [berth.id, berth.workspace_id or ""]
    if extra is not None:
        keys.append(extra.session or "")
    return any(key and (key.casefold() == text or (len(text) >= 4 and key.casefold().startswith(text))) for key in keys)


def fields_from(value: Any) -> tuple[str, ...]:
    """The default fields plus the ones asked for; "all" is every field."""
    if value in (None, "", []):
        return DEFAULT_FIELDS
    asked = [value] if isinstance(value, str) else list(value)
    names: list[str] = list(DEFAULT_FIELDS)
    for item in asked:
        name = str(item).strip().casefold()
        if name == "all":
            return DEFAULT_FIELDS + EXTRA_FIELDS
        if name not in DEFAULT_FIELDS + EXTRA_FIELDS:
            raise BerthError("BAD_ARGUMENT", f"no field {item}", {"field": item, "fields": list(DEFAULT_FIELDS + EXTRA_FIELDS)})
        if name not in names:
            names.append(name)
    return tuple(names)


def _set(value: Any, name: str, allowed: tuple[str, ...] | None = None) -> set[str]:
    if value in (None, "", []):
        return set()
    items = value.split(",") if isinstance(value, str) else list(value)
    found = {str(item).strip().casefold() for item in items if str(item).strip()}
    if allowed is not None:
        wrong = sorted(found - set(allowed))
        if wrong:
            raise BerthError("BAD_ARGUMENT", f"{name} is one or more of {', '.join(allowed)}", {name: wrong})
    return found


def scope_folders(view: View, args: dict) -> tuple[list[dict], dict]:
    """The folders a scope covers, top-down, each with its level and role, and the scope's summary."""
    named = args.get("folder")
    query = (args.get("query") or "").strip()
    if named not in (None, ""):
        start = view.folder(str(named))
        origin = "named"
    elif view.caller is not None and not query:
        start = view.caller.folder_id
        origin = "you"
    else:
        start = None
        origin = "everything" if query else "root"
    down = levels(args.get("down"), "down")
    up = levels(args.get("up"), "up")
    if start is None and "down" not in args:
        down = None  # the root holds no sessions itself: from "/" (or with no folder of your own) take everything
    branches = bool(args.get("branches"))
    chain = view.ancestors(start) if start else []
    reach = len(chain) if up is None else min(up, len(chain))
    included: list[dict] = []
    if branches and reach:
        top = chain[reach - 1]
        base = view.depth(start)
        for folder_id in [top, *view.subtree(top)]:
            level = view.depth(folder_id) - base
            if down is not None and level > down:
                continue
            if folder_id == start:
                role = "start"
            elif folder_id in chain:
                role = "above"
            elif start in view.ancestors(folder_id):
                role = "below"
            else:
                role = "beside"
            if role == "above":
                level = -(chain.index(folder_id) + 1)
            included.append({"id": folder_id, "level": level, "role": role})
    else:
        for index in reversed(range(reach)):
            included.append({"id": chain[index], "level": -(index + 1), "role": "above"})
        if start is not None:
            included.append({"id": start, "level": 0, "role": "start"})
        base = view.depth(start)
        for folder_id in view.subtree(start):
            level = view.depth(folder_id) - base
            if down is not None and level > down:
                continue
            included.append({"id": folder_id, "level": level, "role": "below"})
    summary = {
        "folder": view.path(start),
        "from": origin,
        "down": ALL if down is None else down,
        "up": ALL if up is None else up,
    }
    if branches:
        summary["branches"] = True
    if up is not None and up > len(chain):
        summary["up_reached_top"] = True
    if view.caller is not None:
        summary["you"] = {"id": view.caller.id, "title": view.caller.title, "folder": view.path(view.caller.folder_id)}
    return included, summary


def _left_out(view: View, folder_id: str, shown: set[str]) -> dict[str, int] | None:
    """Folders under this one that the scope did not include, and the sessions in them."""
    folders = sessions = 0
    for child in view.children(folder_id):
        if child in shown:
            continue
        for item in [child, *view.subtree(child)]:
            folders += 1
            sessions += len(view.sessions_in(item))
    if not folders:
        return None
    return {"folders": folders, "sessions": sessions}


@dataclass
class Filters:
    states: set[str]
    agents: set[str]
    text: str
    pinned: bool | None = None
    busy: bool | None = None
    urgent: bool | None = None
    follow_up: bool | None = None

    @classmethod
    def of(cls, args: dict) -> "Filters":
        for name in ("pinned", "busy", "urgent", "follow_up"):
            if args.get(name) is not None and not isinstance(args.get(name), bool):
                raise BerthError("BAD_ARGUMENT", f"{name} is true or false", {name: args.get(name)})
        return cls(_set(args.get("state"), "state", STATES), _set(args.get("agent"), "agent"),
                   (args.get("query") or "").strip(), args.get("pinned"), args.get("busy"), args.get("urgent"),
                   args.get("follow_up"))

    def __bool__(self) -> bool:
        return bool(self.states or self.agents or self.text or self.pinned is not None or self.busy is not None
                    or self.urgent is not None or self.follow_up is not None)

    def keep(self, view: View, berth) -> bool:
        if self.pinned is not None and bool(berth.pinned) != self.pinned:
            return False
        if self.urgent is not None and bool(getattr(berth, "urgent", False)) != self.urgent:
            return False
        if self.follow_up is not None and bool(getattr(berth, "follow_up", False)) != self.follow_up:
            return False
        if self.busy is not None and bool(berth.busy and berth.state == "live") != self.busy:
            return False
        state = state_of(berth)
        if self.states and state not in self.states and not ("busy" in self.states and state == "live" and berth.busy):
            return False
        if self.agents and agent_of(berth.kind, berth.agent_name) not in self.agents:
            return False
        return matches(berth, view.extra.get(berth.id), self.text)


def select(view: View, args: dict) -> tuple[list, dict]:
    """The sessions a scope and its filters pick, in order, and the scope summary (for control and delete)."""
    included, summary = scope_folders(view, args)
    filters = Filters.of(args)
    chosen = [berth for item in included for berth in view.sessions_in(item["id"]) if filters.keep(view, berth)]
    return chosen, summary


def query(db_path: Path | str, args: dict, *, caller_workspace: str | None = None) -> dict[str, Any]:
    """The ``sessions`` tool: the sessions in a scope, grouped by folder."""
    view = View(db_path, caller_workspace=caller_workspace, archived=bool(args.get("archived")))
    included, summary = scope_folders(view, args)
    fields = fields_from(args.get("fields"))
    limit = args.get("limit") or DEFAULT_LIMIT
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
        raise BerthError("BAD_ARGUMENT", f"limit is 1 to {MAX_LIMIT}", {"limit": limit})
    offset = args.get("offset") or 0
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise BerthError("BAD_ARGUMENT", "offset is 0 or more", {"offset": offset})
    filters = Filters.of(args)
    shown = {item["id"] for item in included}
    groups: list[dict] = []
    total = 0
    position = 0
    returned = 0
    for item in included:
        rows = []
        for berth in view.sessions_in(item["id"]):
            if not filters.keep(view, berth):
                continue
            total += 1
            if position >= offset and returned < limit:
                rows.append(view.row(berth, fields))
                returned += 1
            position += 1
        if filters and not rows and item["role"] != "start":
            continue
        group: dict[str, Any] = {"folder": view.path(item["id"]), "level": item["level"], "sessions": rows}
        if item["role"] == "above" and not args.get("branches"):
            beside = _left_out(view, item["id"], shown)
            if beside:
                group["beside"] = beside  # its other subfolders, not in this scope
        else:
            deeper = _left_out(view, item["id"], shown)
            if deeper:
                group["deeper"] = deeper  # subfolders below the scope's depth
        groups.append(group)
    if not included:
        for child in view.children(None):
            groups.append({"folder": view.path(child), "level": 1, "sessions": [],
                           "deeper": {"folders": 1 + len(view.subtree(child)),
                                      "sessions": sum(len(view.sessions_in(f)) for f in [child, *view.subtree(child)])}})
    body: dict[str, Any] = {"scope": summary, "total": total, "returned": returned, "folders": groups}
    if offset + returned < total:
        body["next_offset"] = offset + returned
    if filters.text:
        summary["query"] = filters.text
    return body
