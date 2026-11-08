"""Read the catalog and write the rows the screen owns."""

from __future__ import annotations

from berth.sqlite_schema import schema_step

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from berth.model import INBOX_NAME
from berth.schema import INTENT_SQL, RUNTIME_SQL, STATUS_SQL


class CatalogError(Exception):
    """The sqlite file is missing, unreadable, or not a Berth catalog."""


@dataclass
class Folder:
    id: str
    parent_id: str | None
    name: str
    sort: int
    note: str
    archived: bool
    profile_id: str | None
    default_cwd: str | None
    color: str | None = None


@dataclass
class Berth:
    id: str
    folder_id: str
    title: str
    kind: str
    state: str
    protect: bool
    cwd: str
    note: str
    recipe_display: str
    excerpt: str
    excerpt_at: str
    agent_version: str
    created_at: datetime | None
    updated_at: str
    busy: bool
    agent_name: str = ""
    park_reason: str = ""
    last_used_at: str = ""
    workspace_id: str = ""
    pinned: bool = False
    busy_why: tuple = ()
    draft: str = ""  # "" none, "saved" when it can be saved exactly, else why it cannot
    urgent: bool = False  # the session said it needs you urgently (berth/attention.py)
    follow_up: bool = False  # it said its work is not finished, or something is left for you (2026-10-31)
    flag_why: str = ""  # the why and time of either flag
    flag_at: str = ""
    account: str = ""  # a Claude session's account, from Settings Agent's files (berth/accounts.py)
    account_stale: bool = False  # on ~/.claude, opened before the last account switch
    status: str = ""  # what the cmux sidebar shows: working, ready, needs_you, ... (live sessions)
    status_label: str = ""


@dataclass
class Status:
    child_rss_bytes: int | None = None
    watcher: str | None = None


@dataclass
class Intent:
    id: str
    at: str
    op: str
    berth_id: str
    detail: dict


@dataclass
class Snapshot:
    folders: dict[str, Folder] = field(default_factory=dict)
    berths: dict[str, Berth] = field(default_factory=dict)
    status: Status = field(default_factory=Status)
    intents: list[Intent] = field(default_factory=list)
    children: dict[str | None, list[str]] = field(default_factory=dict)
    berths_in: dict[str, list[str]] = field(default_factory=dict)

    def folder_path(self, folder_id: str) -> str:
        parts: list[str] = []
        seen: set[str] = set()
        current: str | None = folder_id
        while current and current not in seen:
            seen.add(current)
            folder = self.folders.get(current)
            if folder is None:
                break
            parts.append(folder.name)
            current = folder.parent_id
        return "/".join(reversed(parts))


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso(when: datetime) -> str:
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path, timeout=1.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=1000")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load(path: Path | str) -> Snapshot:
    file = Path(path)
    if not file.is_file():
        raise CatalogError(f"no catalog at {file}")
    try:
        with connect(file) as conn:
            ensure(conn)
            names = _tables(conn)
            if "folder" not in names or "berth" not in names:
                raise CatalogError("catalog has no berth schema")
            return _read(conn)
    except CatalogError:
        raise
    except sqlite3.DatabaseError as exc:
        raise CatalogError("catalog unreadable") from exc


def ensure(conn: sqlite3.Connection) -> None:
    have = _tables(conn)
    if "status" not in have:
        schema_step(conn, STATUS_SQL)
    if "runtime" not in have:
        schema_step(conn, RUNTIME_SQL)
    if "intent" not in have:
        schema_step(conn, INTENT_SQL)
    if "runtime" in have and "why" not in _columns(conn, "runtime"):
        # Why a session is busy, and its draft (berth/activity.py, 2026-10-28).
        schema_step(conn, "ALTER TABLE runtime ADD COLUMN why TEXT NOT NULL DEFAULT ''")
        schema_step(conn, "ALTER TABLE runtime ADD COLUMN draft TEXT NOT NULL DEFAULT ''")
        schema_step(conn, "ALTER TABLE runtime ADD COLUMN seen_at TEXT")
    if "runtime" in have and "status" not in _columns(conn, "runtime"):
        # The one state the cmux sidebar shows, and its word (berth/cmux_sidebar.py, 2026-10-29).
        schema_step(conn, "ALTER TABLE runtime ADD COLUMN status TEXT NOT NULL DEFAULT ''")
        schema_step(conn, "ALTER TABLE runtime ADD COLUMN label TEXT NOT NULL DEFAULT ''")
    if "runtime" in have and "mode" not in _columns(conn, "runtime"):
        # Claude's permission mode, from its status strip (berth/standard.py, 2026-10-31).
        schema_step(conn, "ALTER TABLE runtime ADD COLUMN mode TEXT NOT NULL DEFAULT ''")
    if "folder" in have and "color" not in _columns(conn, "folder"):
        schema_step(conn, "ALTER TABLE folder ADD COLUMN color TEXT")


def insert_intent(path: Path | str, op: str, berth_id: str, detail: dict, when: datetime) -> None:
    # The screen that wrote it: the drain judges "the caller" by this screen, not by whichever screen drains it
    # (2026-10-30: with two screens open, the second refused a delete aimed at its own workspace).
    detail = {"by_workspace": os.environ.get("CMUX_WORKSPACE_ID") or None, **detail}
    payload = json.dumps(detail, sort_keys=True, separators=(",", ":"))
    with connect(Path(path)) as conn:
        ensure(conn)
        conn.execute(
            "INSERT INTO intent (id, at, op, berth_id, detail) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), iso(when), op, berth_id, payload),
        )


def toggle_protect(path: Path | str, berth_id: str, when: datetime) -> int:
    with connect(Path(path)) as conn:
        row = conn.execute("SELECT protect, title FROM berth WHERE id = ?", (berth_id,)).fetchone()
        if row is None:
            raise CatalogError("no berth")
        new = 0 if row["protect"] else 1
        conn.execute(
            "UPDATE berth SET protect = ?, updated_at = ? WHERE id = ?",
            (new, iso(when), berth_id),
        )
        _event(conn, berth_id, when, "protect" if new else "unprotect", {"protect": new})
        return new


def move_berth(path: Path | str, berth_id: str, folder_id: str, when: datetime) -> None:
    with connect(Path(path)) as conn:
        berth = conn.execute("SELECT folder_id FROM berth WHERE id = ?", (berth_id,)).fetchone()
        if berth is None:
            raise CatalogError("no berth")
        if conn.execute("SELECT 1 FROM folder WHERE id = ?", (folder_id,)).fetchone() is None:
            raise CatalogError("no folder")
        origin = _sql_path(conn, berth["folder_id"])
        dest = _sql_path(conn, folder_id)
        conn.execute(
            "UPDATE berth SET folder_id = ?, updated_at = ? WHERE id = ?",
            (folder_id, iso(when), berth_id),
        )
        _event(conn, berth_id, when, "move", {"from": origin, "to": dest})


def delete_berth(path: Path | str, berth_id: str, when: datetime) -> None:
    """Remove one catalog row. Session files on disk stay."""
    with connect(Path(path)) as conn:
        row = conn.execute("SELECT title, kind FROM berth WHERE id = ?", (berth_id,)).fetchone()
        if row is None:
            raise CatalogError("no berth")
        if row["kind"] == "berth":
            raise CatalogError("the Berth screen stays")
    from berth import machine
    from berth.db import Catalog

    db = Catalog(Path(path)).open()
    try:
        machine.delete(db, berth_id, reason="user", detail={"at": when.isoformat()})
    finally:
        db.close()


def rename_berth(path: Path | str, berth_id: str, title: str, when: datetime) -> None:
    """Catalog title only. The screen and ``berth rename`` use ``ops.rename_berth``."""
    title = " ".join(title.split())
    if not title:
        raise CatalogError("name the session")
    if len(title) > 200:
        raise CatalogError("name is too long")
    with connect(Path(path)) as conn:
        row = conn.execute("SELECT title FROM berth WHERE id = ?", (berth_id,)).fetchone()
        if row is None:
            raise CatalogError("no berth")
        conn.execute(
            "UPDATE berth SET title = ?, updated_at = ? WHERE id = ?",
            (title, iso(when), berth_id),
        )
        _event(conn, berth_id, when, "rename", {"from": row["title"], "to": title})


def mkdir_root(path: Path | str, name: str, when: datetime) -> str:
    name = name.strip()
    if not name:
        raise CatalogError("name the folder")
    if "/" in name or name in {".", ".."} or len(name) > 200:
        raise CatalogError("folders are one level")
    with connect(Path(path)) as conn:
        if _child(conn, None, name):
            raise CatalogError(f"folder exists · {name}")
        new_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO folder (
              id, parent_id, name, sort, profile_id, default_cwd, note, archived
            ) VALUES (?, NULL, ?, ?, NULL, NULL, '', 0)
            """,
            (new_id, name, _next_sort(conn, None)),
        )
        _event(conn, None, when, "mkdir", {"path": name, "name": name})
        return new_id


def remove_folder(path: Path | str, folder_id: str, when: datetime) -> dict:
    """Delete one folder. Berths move to the parent, or to Inbox for a root.

    Child folders move up one level. A child whose name already exists there
    is merged into that folder. No berth row is deleted. No workspace closes.
    """
    with connect(Path(path)) as conn:
        return _remove_folder(conn, folder_id, when)


def _remove_folder(conn: sqlite3.Connection, folder_id: str, when: datetime) -> dict:
    row = conn.execute(
        "SELECT id, parent_id, name FROM folder WHERE id = ?",
        (folder_id,),
    ).fetchone()
    if row is None:
        raise CatalogError("no folder")
    if row["parent_id"] is None and row["name"] == INBOX_NAME:
        raise CatalogError("Inbox stays")
    origin = _sql_path(conn, folder_id)
    if row["parent_id"] is None:
        berth_dest = _inbox_id(conn, keep=folder_id)
        folder_dest = None
    else:
        parent = conn.execute(
            "SELECT id FROM folder WHERE id = ?",
            (row["parent_id"],),
        ).fetchone()
        if parent is None:
            raise CatalogError("no folder")
        berth_dest = parent["id"]
        folder_dest = parent["id"]
    dest_path = _sql_path(conn, berth_dest)
    moved = _move_berths(conn, folder_id, berth_dest, origin, dest_path, when)
    lifted, merged = _lift_children(conn, folder_id, folder_dest, when)
    conn.execute("DELETE FROM folder WHERE id = ?", (folder_id,))
    _event(
        conn,
        None,
        when,
        "rmdir",
        {"path": origin, "to": dest_path, "moved": moved, "lifted": lifted, "merged": merged},
    )
    return {
        "deleted": origin,
        "id": folder_id,
        "to": dest_path,
        "to_id": berth_dest,
        "moved": moved,
        "lifted": lifted,
        "merged": merged,
    }


def _inbox_id(conn: sqlite3.Connection, *, keep: str) -> str:
    row = conn.execute(
        "SELECT id FROM folder WHERE parent_id IS NULL AND name = ? AND id != ?",
        (INBOX_NAME, keep),
    ).fetchone()
    if row is not None:
        return str(row["id"])
    new_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO folder (
          id, parent_id, name, sort, profile_id, default_cwd, note, archived
        ) VALUES (?, NULL, ?, 0, NULL, NULL, '', 0)
        """,
        (new_id, INBOX_NAME),
    )
    return new_id


def _move_berths(
    conn: sqlite3.Connection,
    source: str,
    dest: str,
    origin: str,
    dest_path: str,
    when: datetime,
) -> int:
    rows = conn.execute("SELECT id FROM berth WHERE folder_id = ?", (source,)).fetchall()
    if not rows:
        return 0
    conn.execute(
        "UPDATE berth SET folder_id = ?, updated_at = ? WHERE folder_id = ?",
        (dest, iso(when), source),
    )
    for item in rows:
        _event(conn, item["id"], when, "move", {"from": origin, "to": dest_path, "via": "rmdir"})
    return len(rows)


def _lift_children(
    conn: sqlite3.Connection,
    folder_id: str,
    dest_parent: str | None,
    when: datetime,
) -> tuple[list[str], list[str]]:
    children = conn.execute(
        "SELECT id, name FROM folder WHERE parent_id = ? ORDER BY sort, name",
        (folder_id,),
    ).fetchall()
    lifted: list[str] = []
    merged: list[str] = []
    for child in children:
        existing = _child(conn, dest_parent, child["name"])
        if existing and existing != child["id"]:
            _merge_into(conn, child["id"], existing, when)
            merged.append(child["name"])
            continue
        conn.execute(
            "UPDATE folder SET parent_id = ?, sort = ? WHERE id = ?",
            (dest_parent, _next_sort(conn, dest_parent), child["id"]),
        )
        lifted.append(child["name"])
    return lifted, merged


def _merge_into(conn: sqlite3.Connection, source_id: str, dest_id: str, when: datetime) -> None:
    if source_id == dest_id or _is_under(conn, dest_id, source_id):
        raise CatalogError("folder cycle")
    origin = _sql_path(conn, source_id)
    dest_path = _sql_path(conn, dest_id)
    _move_berths(conn, source_id, dest_id, origin, dest_path, when)
    _lift_children(conn, source_id, dest_id, when)
    conn.execute("DELETE FROM folder WHERE id = ?", (source_id,))


def _is_under(conn: sqlite3.Connection, node: str, ancestor: str) -> bool:
    seen: set[str] = set()
    current: str | None = node
    while current and current not in seen:
        if current == ancestor:
            return True
        seen.add(current)
        row = conn.execute("SELECT parent_id FROM folder WHERE id = ?", (current,)).fetchone()
        if row is None:
            return False
        current = row["parent_id"]
    return False


def rename_folder(path: Path | str, folder_id: str, name: str, when: datetime) -> None:
    name = name.strip()
    if not name or "/" in name or name in {".", ".."} or len(name) > 200:
        raise CatalogError("folders are one level")
    with connect(Path(path)) as conn:
        row = conn.execute("SELECT parent_id, name FROM folder WHERE id = ?", (folder_id,)).fetchone()
        if row is None:
            raise CatalogError("no folder")
        existing = _child(conn, row["parent_id"], name)
        if existing and existing != folder_id:
            raise CatalogError(f"folder exists · {name}")
        if row["name"] == name:
            return
        conn.execute("UPDATE folder SET name = ? WHERE id = ?", (name, folder_id))
        _event(conn, None, when, "rename", {"from": row["name"], "to": name})


def mkdir_path(path: Path | str, text: str, when: datetime) -> str:
    """Folders along a path from the root, including parents."""
    return mkdir_under(path, None, text, when)


def mkdir_under(path: Path | str, parent_id: str | None, text: str, when: datetime) -> str:
    """Folders along ``text`` (``a/b/c``) under ``parent_id``, or the root.

    Missing parents are made. The last name must be new. Inbox is "not in a
    folder", so a folder asked for inside Inbox is made at the root.
    """
    parts = [part.strip() for part in text.split("/")]
    parts = [part for part in parts if part]
    if not parts:
        raise CatalogError("name the folder")
    for part in parts:
        if part in {".", ".."} or len(part) > 200:
            raise CatalogError("bad folder name")
    with connect(Path(path)) as conn:
        parent: str | None = parent_id
        if parent is not None:
            row = conn.execute("SELECT parent_id, name FROM folder WHERE id = ?", (parent,)).fetchone()
            if row is None:
                raise CatalogError("no folder")
            if row["parent_id"] is None and row["name"] == INBOX_NAME:
                parent = None
        if parent is None and parts[0] == INBOX_NAME and len(parts) > 1:
            raise CatalogError("Inbox has no folders")
        built: list[str] = [_sql_path(conn, parent)] if parent else []
        for index, name in enumerate(parts):
            existing = _child(conn, parent, name)
            last = index == len(parts) - 1
            if existing and last:
                raise CatalogError("folder exists · " + "/".join([*built, name]))
            if existing:
                parent = existing
                built.append(name)
                continue
            new_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO folder (
                  id, parent_id, name, sort, profile_id, default_cwd, note, archived
                ) VALUES (?, ?, ?, ?, NULL, NULL, '', 0)
                """,
                (new_id, parent, name, _next_sort(conn, parent)),
            )
            built.append(name)
            _event(conn, None, when, "mkdir", {"path": "/".join(built), "name": name})
            parent = new_id
        return parent or ""


def move_folder(path: Path | str, folder_id: str, parent_id: str | None, when: datetime) -> str:
    """Put a folder, with everything in it, under ``parent_id`` or at the root.

    Inbox does not move and holds no folders. A folder cannot go inside
    itself. A name already used at the destination is refused, not merged.
    """
    with connect(Path(path)) as conn:
        row = conn.execute("SELECT id, parent_id, name FROM folder WHERE id = ?", (folder_id,)).fetchone()
        if row is None:
            raise CatalogError("no folder")
        if row["parent_id"] is None and row["name"] == INBOX_NAME:
            raise CatalogError("Inbox stays")
        if parent_id is not None:
            dest = conn.execute("SELECT parent_id, name FROM folder WHERE id = ?", (parent_id,)).fetchone()
            if dest is None:
                raise CatalogError("no folder")
            if dest["parent_id"] is None and dest["name"] == INBOX_NAME:
                parent_id = None
        if parent_id is not None and (parent_id == folder_id or _is_under(conn, parent_id, folder_id)):
            raise CatalogError("a folder cannot go inside itself")
        if row["parent_id"] == parent_id:
            return _sql_path(conn, folder_id)
        clash = _child(conn, parent_id, row["name"])
        if clash and clash != folder_id:
            raise CatalogError(f"folder exists · {row['name']}")
        origin = _sql_path(conn, folder_id)
        conn.execute(
            "UPDATE folder SET parent_id = ?, sort = ? WHERE id = ?",
            (parent_id, _next_sort(conn, parent_id), folder_id),
        )
        dest_path = _sql_path(conn, folder_id)
        _event(conn, None, when, "move_folder", {"from": origin, "to": dest_path})
        return dest_path


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if table not in _tables(conn):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _read(conn: sqlite3.Connection) -> Snapshot:
    folders: dict[str, Folder] = {}
    for row in conn.execute(
        "SELECT id, parent_id, name, sort, note, archived, profile_id, default_cwd, color FROM folder"
    ):
        folders[row["id"]] = Folder(
            id=row["id"],
            parent_id=row["parent_id"],
            name=row["name"],
            sort=int(row["sort"]),
            note=row["note"] or "",
            archived=bool(row["archived"]),
            profile_id=row["profile_id"],
            default_cwd=row["default_cwd"],
            color=row["color"],
        )
    runtime = _runtime(conn)
    statuses = _statuses(conn)
    busy = {berth_id for berth_id, (on, _why, _draft) in runtime.items() if on}
    berths: dict[str, Berth] = {}
    for row in conn.execute("SELECT * FROM berth"):
        berths[row["id"]] = _berth(row, busy)
        seen = runtime.get(row["id"])
        if seen is not None:
            berths[row["id"]].busy_why = tuple(line for line in seen[1].split("\n") if line)
            berths[row["id"]].draft = seen[2]
        shown = statuses.get(row["id"])
        if shown is not None:
            berths[row["id"]].status, berths[row["id"]].status_label = shown
    children: dict[str | None, list[str]] = {}
    for folder in folders.values():
        parent = folder.parent_id
        if parent not in folders:
            parent = None
        children.setdefault(parent, []).append(folder.id)
    for key in children:
        children[key].sort(key=lambda item: (folders[item].sort, folders[item].name.casefold(), item))
    berths_in: dict[str, list[str]] = {}
    for berth in berths.values():
        if berth.folder_id in folders:
            berths_in.setdefault(berth.folder_id, []).append(berth.id)
    return Snapshot(
        folders=folders,
        berths=berths,
        status=_status(conn),
        intents=_intents(conn),
        children=children,
        berths_in=berths_in,
    )


def _berth(row: sqlite3.Row, busy: set[str]) -> Berth:
    keys = set(row.keys())
    display = (row["recipe_display"] or "").strip()
    if not display:
        display = _argv_display(row["recipe_argv"] if "recipe_argv" in keys else "[]")
    note = " ".join((row["note"] or "").split())
    return Berth(
        id=row["id"],
        folder_id=row["folder_id"],
        title=row["title"],
        kind=row["kind"],
        state=row["state"],
        protect=bool(row["protect"]),
        cwd=row["cwd"] or "",
        note=note,
        recipe_display=display,
        excerpt=row["excerpt"] or "",
        excerpt_at=row["excerpt_at"] or "",
        agent_version=(row["agent_version"] or "") if "agent_version" in keys else "",
        created_at=parse_time(row["created_at"]),
        updated_at=row["updated_at"] or "",
        busy=row["id"] in busy,
        agent_name=(row["agent_name"] or "") if "agent_name" in keys else "",
        park_reason=(row["park_reason"] or "") if "park_reason" in keys else "",
        last_used_at=_last_used(row, keys),
        workspace_id=(row["cmux_workspace_id"] or "") if "cmux_workspace_id" in keys else "",
        pinned=bool(row["pinned"]) if "pinned" in keys else False,
        urgent=(row["attention"] == "urgent") if "attention" in keys else False,
        follow_up=(row["attention"] == "follow_up") if "attention" in keys else False,
        flag_why=(row["attention_why"] or "") if "attention_why" in keys else "",
        flag_at=(row["attention_at"] or "") if "attention_at" in keys else "",
        **_account(row, keys),
    )


def _account(row, keys) -> dict:
    """Which Claude account the session is on (berth/accounts.py); nothing for any other agent."""
    if row["kind"] == "grok" or ((row["agent_name"] or "") if "agent_name" in keys else "").casefold() != "claude":
        return {}
    from berth import accounts, configdir

    try:
        folder = configdir.of(row)
        name = accounts.account_of(folder)
        played = row["played_at"] if "played_at" in keys else None
        return {"account": name or "", "account_stale": accounts.stale(folder, played) if name else False}
    except Exception:
        return {}


def _last_used(row: sqlite3.Row, keys: set[str]) -> str:
    """Normalized so string order is time order. Falls back for old catalogs."""
    raw = row["last_used_at"] if "last_used_at" in keys else None
    candidates = [raw] if raw else [row[name] for name in ("played_at", "parked_at", "created_at") if name in keys]
    best = ""
    for value in candidates:
        if not value:
            continue
        parsed = parse_time(value)
        if parsed is None:
            continue
        best = max(best, parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
    return best


def _argv_display(raw: str) -> str:
    try:
        argv = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return ""
    if not isinstance(argv, list):
        return ""
    return " ".join(str(part) for part in argv)


def _busy_ids(conn: sqlite3.Connection) -> set[str]:
    return {berth_id for berth_id, (busy, _why, _draft) in _runtime(conn).items() if busy}


def _runtime(conn: sqlite3.Connection) -> dict[str, tuple[bool, str, str]]:
    """What the watcher last saw in each live pane: busy, why (one reason a line), and the draft."""
    columns = _columns(conn, "runtime")
    if "berth_id" not in columns or "busy" not in columns:
        return {}
    extra = "why, draft" if "why" in columns else "'' AS why, '' AS draft"
    return {row["berth_id"]: (bool(row["busy"]), row["why"] or "", row["draft"] or "")
            for row in conn.execute(f"SELECT berth_id, busy, {extra} FROM runtime")}


def _statuses(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    """The state and word the cmux sidebar shows for each live session, from the watcher's last look."""
    if "status" not in _columns(conn, "runtime"):
        return {}
    return {row["berth_id"]: (row["status"] or "", row["label"] or "")
            for row in conn.execute("SELECT berth_id, status, label FROM runtime")}


def _status(conn: sqlite3.Connection) -> Status:
    columns = _columns(conn, "status")
    if not columns:
        return Status()
    row = conn.execute("SELECT * FROM status WHERE id = 1").fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM status LIMIT 1").fetchone()
    if row is None:
        return Status()
    keys = set(row.keys())
    rss = row["child_rss_bytes"] if "child_rss_bytes" in keys else None
    watcher = row["watcher"] if "watcher" in keys else None
    return Status(
        child_rss_bytes=None if rss is None else int(rss),
        watcher=None if watcher is None else str(watcher),
    )


def _intents(conn: sqlite3.Connection) -> list[Intent]:
    columns = _columns(conn, "intent")
    needed = {"id", "at", "op", "berth_id", "detail"}
    if not needed <= columns:
        return []
    found: list[Intent] = []
    for row in conn.execute("SELECT id, at, op, berth_id, detail FROM intent ORDER BY at, id"):
        try:
            detail = json.loads(row["detail"] or "{}")
        except json.JSONDecodeError:
            detail = {}
        if not isinstance(detail, dict):
            detail = {}
        found.append(
            Intent(
                id=row["id"],
                at=row["at"],
                op=row["op"],
                berth_id=row["berth_id"],
                detail=detail,
            )
        )
    return found


def _event(conn: sqlite3.Connection, berth_id: str | None, when: datetime, kind: str, detail: dict) -> None:
    if "event" not in _tables(conn):
        return
    conn.execute(
        "INSERT INTO event (id, berth_id, at, kind, detail) VALUES (?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4()),
            berth_id,
            iso(when),
            kind,
            json.dumps(detail, sort_keys=True, separators=(",", ":")),
        ),
    )


def _child(conn: sqlite3.Connection, parent: str | None, name: str) -> str | None:
    if parent is None:
        rows = conn.execute("SELECT id, name FROM folder WHERE parent_id IS NULL").fetchall()
    else:
        rows = conn.execute("SELECT id, name FROM folder WHERE parent_id = ?", (parent,)).fetchall()
    folded = name.casefold()
    for row in rows:
        if row["name"].casefold() == folded:
            return row["id"]
    return None


def _next_sort(conn: sqlite3.Connection, parent: str | None) -> int:
    if parent is None:
        row = conn.execute("SELECT COALESCE(MAX(sort), -1) + 1 AS n FROM folder WHERE parent_id IS NULL").fetchone()
    else:
        row = conn.execute(
            "SELECT COALESCE(MAX(sort), -1) + 1 AS n FROM folder WHERE parent_id = ?",
            (parent,),
        ).fetchone()
    return int(row["n"])


def _sql_path(conn: sqlite3.Connection, folder_id: str | None) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    current = folder_id
    while current and current not in seen:
        seen.add(current)
        row = conn.execute("SELECT parent_id, name FROM folder WHERE id = ?", (current,)).fetchone()
        if row is None:
            break
        parts.append(row["name"])
        current = row["parent_id"]
    return "/".join(reversed(parts))
