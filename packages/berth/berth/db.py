"""Sqlite catalog. The table columns are the contract."""

from __future__ import annotations

from berth.sqlite_schema import schema_step

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from berth.errors import BerthError
from berth.model import INBOX_NAME
from berth.timeutil import utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS folder (
  id TEXT PRIMARY KEY,
  parent_id TEXT,
  name TEXT NOT NULL,
  sort INTEGER NOT NULL,
  profile_id TEXT,
  default_cwd TEXT,
  note TEXT NOT NULL DEFAULT '',
  archived INTEGER NOT NULL DEFAULT 0,
  color TEXT
);

CREATE TABLE IF NOT EXISTS berth (
  id TEXT PRIMARY KEY,
  folder_id TEXT NOT NULL,
  title TEXT NOT NULL,
  kind TEXT NOT NULL,
  state TEXT NOT NULL,
  protect INTEGER NOT NULL DEFAULT 0,
  cwd TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  recipe_argv TEXT NOT NULL,
  recipe_display TEXT NOT NULL,
  agent_name TEXT,
  agent_session_id TEXT,
  agent_version TEXT,
  cmux_workspace_id TEXT,
  cmux_surface_id TEXT,
  cmux_pid INTEGER,
  attachments TEXT NOT NULL DEFAULT '{}',
  excerpt TEXT NOT NULL DEFAULT '',
  excerpt_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  parked_at TEXT,
  played_at TEXT,
  last_workspace_id TEXT,
  park_reason TEXT,
  last_used_at TEXT,
  pinned INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS event (
  id TEXT PRIMARY KEY,
  berth_id TEXT,
  at TEXT NOT NULL,
  kind TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '{}'
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS berth_by_workspace ON berth(cmux_workspace_id);
CREATE INDEX IF NOT EXISTS berth_by_folder ON berth(folder_id);
CREATE INDEX IF NOT EXISTS berth_by_state ON berth(state);
CREATE INDEX IF NOT EXISTS berth_by_session ON berth(agent_session_id);
CREATE INDEX IF NOT EXISTS event_by_berth ON event(berth_id);
"""

BERTH_COLUMNS = (
    "id",
    "folder_id",
    "title",
    "kind",
    "state",
    "protect",
    "cwd",
    "note",
    "recipe_argv",
    "recipe_display",
    "agent_name",
    "agent_session_id",
    "agent_version",
    "cmux_workspace_id",
    "cmux_surface_id",
    "cmux_pid",
    "attachments",
    "excerpt",
    "excerpt_at",
    "created_at",
    "updated_at",
    "parked_at",
    "played_at",
    "last_workspace_id",
    "park_reason",
    "last_used_at",
    "pinned",
    "attention",
    "attention_why",
    "attention_at",
)

LINK_COLUMNS = ("last_workspace_id", "park_reason")
ATTENTION_COLUMNS = ("attention", "attention_why", "attention_at")
# Insert fills these with null when a caller leaves them out.
OPTIONAL_COLUMNS = (*LINK_COLUMNS, "last_used_at", "pinned", *ATTENTION_COLUMNS)
# What an insert that leaves an optional column out gets (null otherwise).
OPTIONAL_DEFAULTS = {"pinned": 0, "attention_why": ""}

# Only berth.machine writes these. See docs/BERTH-STATE-MACHINE.md.
GUARDED_COLUMNS = frozenset({"state", "cmux_workspace_id", "last_workspace_id", "park_reason"})

FOLDER_COLUMNS = (
    "id",
    "parent_id",
    "name",
    "sort",
    "profile_id",
    "default_cwd",
    "note",
    "archived",
    "color",
    "orchestrator",  # berth/orchestrator.py (2026-11-04)
)


def _ensure_folder_color(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(folder)")}
    if "color" not in columns:
        schema_step(conn, "ALTER TABLE folder ADD COLUMN color TEXT")


def _ensure_links(conn: sqlite3.Connection) -> None:
    """Add the link columns and backfill parked rows from their last park event."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(berth)")}
    added = False
    for name in LINK_COLUMNS:
        if name not in columns:
            schema_step(conn, f"ALTER TABLE berth ADD COLUMN {name} TEXT")
            added = True
    if not added:
        return
    conn.execute("UPDATE berth SET last_workspace_id = cmux_workspace_id WHERE cmux_workspace_id IS NOT NULL")
    parked = conn.execute("SELECT id FROM berth WHERE state = 'parked'").fetchall()
    for (berth_id,) in parked:
        found = conn.execute(
            """
            SELECT kind, detail FROM event
            WHERE berth_id = ? AND kind IN ('workspace_vanished', 'pause', 'play_stale')
            ORDER BY at DESC LIMIT 1
            """,
            (berth_id,),
        ).fetchone()
        if found is None:
            continue
        try:
            detail = json.loads(found[1] or "{}")
        except json.JSONDecodeError:
            detail = {}
        reason = detail.get("via") or ("pause" if found[0] == "pause" else found[0])
        conn.execute(
            "UPDATE berth SET last_workspace_id = COALESCE(last_workspace_id, ?), park_reason = ? WHERE id = ?",
            (detail.get("workspace_id"), reason, berth_id),
        )


def _ensure_pinned(conn: sqlite3.Connection) -> None:
    """Pinned sessions (2026-10-28): the one mark, kept in step with cmux's workspace pin (berth/pins.py)."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(berth)")}
    if "pinned" not in columns:
        schema_step(conn, "ALTER TABLE berth ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")


def _ensure_attention(conn: sqlite3.Connection) -> None:
    """A session can say it needs you urgently (berth/attention.py, 2026-10-29)."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(berth)")}
    if "attention" not in columns:
        schema_step(conn, "ALTER TABLE berth ADD COLUMN attention TEXT")
        schema_step(conn, "ALTER TABLE berth ADD COLUMN attention_why TEXT NOT NULL DEFAULT ''")
        schema_step(conn, "ALTER TABLE berth ADD COLUMN attention_at TEXT")


def _ensure_screen_kind(conn: sqlite3.Connection) -> None:
    """The Berth screen was filed as a shell until 2026-10-28. It is kind ``berth`` now."""
    conn.execute(
        """
        UPDATE berth SET kind = 'berth', recipe_argv = '["berth"]', recipe_display = 'berth'
        WHERE title = 'Berth' AND kind IN ('shell', 'process')
        """
    )


def _ensure_last_used(conn: sqlite3.Connection) -> None:
    """When you last used a session. The watcher moves it forward from cmux and the agent."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(berth)")}
    if "last_used_at" in columns:
        return
    schema_step(conn, "ALTER TABLE berth ADD COLUMN last_used_at TEXT")
    conn.execute(
        """
        UPDATE berth SET last_used_at = MAX(
          COALESCE(played_at, ''), COALESCE(parked_at, ''), COALESCE(created_at, '')
        )
        """
    )


def default_catalog_path() -> Path:
    override = os.environ.get("BERTH_CATALOG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "berth" / "berth.sqlite"


def data_dir(catalog: Path | None = None) -> Path:
    return (catalog or default_catalog_path()).parent


class Catalog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None

    def open(self) -> Catalog:
        directory = self.path.parent
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)
        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        conn.executescript(INDEXES)
        from berth.trash import DDL as TRASH_DDL

        conn.executescript(TRASH_DDL)
        from berth.deferred import DDL as DEFERRED_DDL

        conn.executescript(DEFERRED_DDL)
        # Prompts waiting for a session, from programs and agents (berth/delivery.py, 2026-10-30).
        from berth.delivery import DDL as DELIVERY_DDL, migrate as migrate_deliveries

        conn.executescript(DELIVERY_DDL)
        try:
            migrate_deliveries(conn)
        except sqlite3.Error:
            pass
        _ensure_folder_color(conn)
        from berth.orchestrator import ensure as _ensure_orchestrator

        _ensure_orchestrator(conn)  # folder.orchestrator (berth/orchestrator.py, 2026-11-04)
        _ensure_links(conn)
        _ensure_last_used(conn)
        _ensure_screen_kind(conn)
        _ensure_pinned(conn)
        _ensure_attention(conn)
        conn.executescript("CREATE INDEX IF NOT EXISTS berth_by_last_workspace ON berth(last_workspace_id);")
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        if current < 2:
            conn.execute("PRAGMA user_version = 2")
        self._conn = conn
        self._chmod_files()
        self.ensure_inbox()
        return self

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("catalog is not open")
        return self._conn

    def _chmod_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self.path) + suffix)
            if candidate.exists():
                os.chmod(candidate, 0o600)

    @contextmanager
    def immediate(self) -> Iterator[sqlite3.Connection]:
        """One write transaction. A nested call joins the outer one."""
        conn = self.conn
        if conn.in_transaction:
            yield conn
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
            self._chmod_files()

    def ensure_inbox(self) -> str:
        row = self.conn.execute(
            "SELECT id FROM folder WHERE parent_id IS NULL AND name = ?",
            (INBOX_NAME,),
        ).fetchone()
        if row is not None:
            return str(row["id"])
        folder_id = str(uuid.uuid4())
        with self.immediate():
            again = self.conn.execute(
                "SELECT id FROM folder WHERE parent_id IS NULL AND name = ?",
                (INBOX_NAME,),
            ).fetchone()
            if again is not None:
                return str(again["id"])
            self.conn.execute(
                """
                INSERT INTO folder (id, parent_id, name, sort, profile_id, default_cwd, note, archived)
                VALUES (?, NULL, ?, 0, NULL, NULL, '', 0)
                """,
                (folder_id, INBOX_NAME),
            )
        return folder_id

    def get_berth(self, berth_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM berth WHERE id = ?", (berth_id,)).fetchone()

    def require_berth(self, berth_id: str) -> sqlite3.Row:
        row = self.get_berth(berth_id)
        if row is None:
            raise BerthError("NOT_FOUND", f"no berth {berth_id}", {"berth_id": berth_id})
        return row

    def berth_by_workspace(self, workspace_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM berth WHERE cmux_workspace_id = ? COLLATE NOCASE",
            (workspace_id,),
        ).fetchone()

    def all_berths(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM berth ORDER BY title, created_at").fetchall())

    def all_folders(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute("SELECT * FROM folder ORDER BY parent_id, sort, name").fetchall()
        )

    def insert_berth(self, fields: dict) -> None:
        fields = {**{name: OPTIONAL_DEFAULTS.get(name) for name in OPTIONAL_COLUMNS}, **fields}
        missing = [name for name in BERTH_COLUMNS if name not in fields]
        if missing:
            raise RuntimeError(f"berth insert missing {missing}")
        columns = ", ".join(BERTH_COLUMNS)
        marks = ", ".join("?" for _ in BERTH_COLUMNS)
        values = [fields[name] for name in BERTH_COLUMNS]
        self.conn.execute(f"INSERT INTO berth ({columns}) VALUES ({marks})", values)

    def update_berth(self, berth_id: str, **fields: object) -> None:
        """Columns that are not state. ``berth.machine`` owns the guarded ones."""
        guarded = GUARDED_COLUMNS & set(fields)
        if guarded:
            raise RuntimeError(f"state columns go through berth.machine: {sorted(guarded)}")
        self.write_berth(berth_id, **fields)

    def write_berth(self, berth_id: str, **fields: object) -> None:
        """Any column. Only ``berth.machine`` calls this."""
        if not fields:
            return
        unknown = set(fields) - set(BERTH_COLUMNS)
        if unknown:
            raise RuntimeError(f"unknown berth columns {sorted(unknown)}")
        if "updated_at" not in fields:
            fields["updated_at"] = utc_now()
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = list(fields.values()) + [berth_id]
        self.conn.execute(f"UPDATE berth SET {assignments} WHERE id = ?", values)

    def insert_event(self, berth_id: str | None, kind: str, detail: dict | None = None) -> str:
        event_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO event (id, berth_id, at, kind, detail) VALUES (?, ?, ?, ?, ?)",
            (event_id, berth_id, utc_now(), kind, json.dumps(detail or {}, sort_keys=True)),
        )
        return event_id

    def events_for(self, berth_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM event WHERE berth_id = ? ORDER BY at, id",
                (berth_id,),
            ).fetchall()
        )

    def folder_by_id(self, folder_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM folder WHERE id = ?", (folder_id,)).fetchone()

    def child_folders(self, parent_id: str | None) -> list[sqlite3.Row]:
        if parent_id is None:
            return list(
                self.conn.execute(
                    "SELECT * FROM folder WHERE parent_id IS NULL ORDER BY sort, name"
                ).fetchall()
            )
        return list(
            self.conn.execute(
                "SELECT * FROM folder WHERE parent_id = ? ORDER BY sort, name",
                (parent_id,),
            ).fetchall()
        )

    def berths_in(self, folder_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM berth WHERE folder_id = ? ORDER BY title, created_at",
                (folder_id,),
            ).fetchall()
        )

    def live_session_owner(self, session_id: str, except_berth: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT * FROM berth
            WHERE agent_session_id = ? AND id != ? AND state IN ('live', 'playing')
            LIMIT 1
            """,
            (session_id, except_berth),
        ).fetchone()


def public_berth(row: sqlite3.Row) -> dict:
    data = {name: row[name] for name in row.keys()}
    data["recipe_argv"] = json.loads(data["recipe_argv"] or "[]")
    data["attachments"] = json.loads(data["attachments"] or "{}")
    data["protect"] = bool(data["protect"])
    if "pinned" in data:
        data["pinned"] = bool(data["pinned"])
    return data


def public_event(row: sqlite3.Row) -> dict:
    detail = row["detail"] or "{}"
    return {
        "id": row["id"],
        "berth_id": row["berth_id"],
        "at": row["at"],
        "kind": row["kind"],
        "detail": json.loads(detail),
    }
