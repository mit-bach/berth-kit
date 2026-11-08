"""Deleted sessions wait here for 30 days, and can come back.

Every removal goes through ``machine.delete``: ``d`` in the screen, ``berth
delete``, the MCP ``delete`` tool, and the rows Berth folds into another
(``merged``). Each one copies the whole row here first, with when and why.
``restore`` puts it back as a parked row in Inbox, under its own id, so its
session, recipe, note, and event history are what they were. Rows older than
``TRASH_DAYS`` are purged whenever the trash is written or read.

The agent's own conversation files were never touched by a delete; a
restored row resumes the same conversation if Claude or Grok still has it.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from berth.errors import BerthError
from berth.timeutil import parse_time, utc_now

TRASH_DAYS = 30

DDL = """
CREATE TABLE IF NOT EXISTS trash (
  id TEXT PRIMARY KEY,
  deleted_at TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  folder_path TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '{}',
  row TEXT NOT NULL,
  recovered INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS trash_by_time ON trash(deleted_at);
"""

# Never carried back: where it ran, and why it was parked.
_CLEARED = ("cmux_workspace_id", "cmux_surface_id", "cmux_pid", "last_workspace_id")


def _folder_path(db, folder_id: str | None) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    while folder_id and folder_id not in seen:
        seen.add(folder_id)
        found = db.conn.execute("SELECT name, parent_id FROM folder WHERE id = ?", (folder_id,)).fetchone()
        if found is None:
            break
        parts.append(found["name"])
        folder_id = found["parent_id"]
    return "/".join(reversed(parts))


def put(db, row, *, reason: str, detail: dict | None = None, now: str | None = None) -> None:
    """Keep a copy of the row. Called inside ``machine.delete``'s transaction."""
    record = {name: row[name] for name in row.keys()}
    db.conn.execute(
        """
        INSERT OR REPLACE INTO trash (id, deleted_at, reason, folder_path, detail, row, recovered)
        VALUES (?, ?, ?, ?, ?, ?, 0)
        """,
        (
            record["id"],
            now or utc_now(),
            reason,
            _folder_path(db, record.get("folder_id")),
            json.dumps(detail or {}, sort_keys=True),
            json.dumps(record, sort_keys=True),
        ),
    )


def purge(db, *, now: datetime | None = None) -> int:
    """Forget what was deleted more than TRASH_DAYS ago."""
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=TRASH_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    with db.immediate():
        gone = db.conn.execute("DELETE FROM trash WHERE deleted_at < ?", (cutoff,)).rowcount
    return gone or 0


def items(db, *, now: datetime | None = None) -> list[dict]:
    """What is in the trash, newest first, with how many days each has left."""
    purge(db, now=now)
    moment = now or datetime.now(timezone.utc)
    found = []
    for item in db.conn.execute("SELECT * FROM trash ORDER BY deleted_at DESC, id"):
        row = json.loads(item["row"])
        deleted = parse_time(item["deleted_at"])
        left = TRASH_DAYS - (moment - deleted).total_seconds() / 86400
        session = row.get("agent_session_id")
        holder = None
        if session:
            # Restoring makes a second row for a conversation another row already holds.
            other = db.conn.execute("SELECT title FROM berth WHERE agent_session_id = ? LIMIT 1", (session,)).fetchone()
            holder = other["title"] if other else None
        found.append(
            {
                "id": item["id"],
                "title": row.get("title") or "",
                "kind": row.get("kind") or "",
                "agent": row.get("agent_name"),
                "session": row.get("agent_session_id"),
                "folder": item["folder_path"],
                "reason": item["reason"],
                "detail": json.loads(item["detail"] or "{}"),
                "deleted_at": item["deleted_at"],
                "days_left": max(0, int(left + 0.999)),
                "recovered": bool(item["recovered"]),
                "held_by": holder,
            }
        )
    return found


def find(db, token: str) -> dict:
    """One trash item by id, id prefix, or title. Several items with one title need the id."""
    token = (token or "").strip()
    listed = items(db)
    by_id = [item for item in listed if item["id"] == token] or [
        item for item in listed if token and item["id"].startswith(token)
    ]
    if len(by_id) == 1:
        return by_id[0]
    titled = [item for item in listed if item["title"] == token] or [
        item for item in listed if token and token.casefold() in item["title"].casefold()
    ]
    if len(titled) == 1:
        return titled[0]
    if titled:
        raise BerthError(
            "NOT_FOUND",
            f"{len(titled)} items in the trash match {token}; name one by id",
            {"matches": [{"id": item["id"], "title": item["title"], "deleted_at": item["deleted_at"]} for item in titled[:8]]},
        )
    raise BerthError("NOT_FOUND", f"nothing in the trash matches {token}", {"query": token})


def restore(db, berth_id: str, *, now: str | None = None) -> dict:
    """Bring a trashed row back, parked, in Inbox. Its id and session stay the same."""
    from berth import machine
    from berth.db import public_berth

    stamp = now or utc_now()
    with db.immediate():
        item = db.conn.execute("SELECT * FROM trash WHERE id = ?", (berth_id,)).fetchone()
        if item is None:
            raise BerthError("NOT_FOUND", f"{berth_id} is not in the trash", {"berth_id": berth_id})
        saved = json.loads(item["row"])
        columns = {info[1] for info in db.conn.execute("PRAGMA table_info(berth)")}
        fields = {name: value for name, value in saved.items() if name in columns}
        for name in _CLEARED:
            if name in columns:
                fields[name] = None
        new_id = fields["id"] if db.get_berth(fields["id"]) is None else str(uuid.uuid4())
        fields.update(
            {
                "id": new_id,
                "folder_id": db.ensure_inbox(),
                "state": "parked",
                "park_reason": "restored",
                "parked_at": stamp,
                "updated_at": stamp,
                "protect": fields.get("protect") or 0,
            }
        )
        fields.setdefault("created_at", stamp)
        machine.insert(
            db,
            fields,
            event="import",
            log_kind="restore",
            detail={"from_trash": item["deleted_at"], "reason": item["reason"], "folder": item["folder_path"],
                    "was": berth_id},
        )
        db.conn.execute("DELETE FROM trash WHERE id = ?", (berth_id,))
    return public_berth(db.require_berth(new_id))


def forget(db, berth_id: str) -> dict:
    """Delete one trash item for good. Its events stay."""
    with db.immediate():
        item = db.conn.execute("SELECT id, row FROM trash WHERE id = ?", (berth_id,)).fetchone()
        if item is None:
            raise BerthError("NOT_FOUND", f"{berth_id} is not in the trash", {"berth_id": berth_id})
        db.conn.execute("DELETE FROM trash WHERE id = ?", (berth_id,))
        db.insert_event(berth_id, "trash_forgotten", {"title": json.loads(item["row"]).get("title")})
    return {"id": berth_id, "forgotten": True}


def backfill(db, *, days: int = TRASH_DAYS, skip=lambda title: False, now: datetime | None = None) -> list[str]:
    """Rebuild trash items from the delete events of rows removed before the trash existed.

    A delete event kept the title, kind, and session id, not the whole row.
    The rest is rebuilt from what Berth and the agents still have: the row's
    adopt event (its folder), and the session's own files (its directory and
    agent). Marked ``recovered`` so the screen can say the note and excerpt
    were not kept.
    """
    from berth import claude
    from berth.identity import grok_summary_path

    moment = now or datetime.now(timezone.utc)
    since = (moment - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    held = {row[0] for row in db.conn.execute("SELECT id FROM trash")} | {
        row[0] for row in db.conn.execute("SELECT id FROM berth")
    }
    added: list[str] = []
    events = db.conn.execute(
        "SELECT berth_id, at, detail FROM event WHERE kind = 'delete' AND at >= ? ORDER BY at", (since,)
    ).fetchall()
    for event in events:
        berth_id = event["berth_id"]
        detail = json.loads(event["detail"] or "{}")
        title = detail.get("title") or ""
        if not berth_id or berth_id in held or skip(title):
            continue
        kind = detail.get("kind") or "shell"
        session = detail.get("session_id")
        first = db.conn.execute(
            "SELECT at, detail FROM event WHERE berth_id = ? AND kind IN ('adopt', 'import', 'fork') ORDER BY at LIMIT 1",
            (berth_id,),
        ).fetchone()
        origin = json.loads(first["detail"]) if first else {}
        moves = db.conn.execute(
            "SELECT detail FROM event WHERE berth_id = ? AND kind = 'move' ORDER BY at DESC LIMIT 1", (berth_id,)
        ).fetchone()
        folder_id = json.loads(moves["detail"]).get("folder_id") if moves else origin.get("folder_id")
        cwd, agent, argv = "", None, []
        if kind == "grok" and session:
            agent, argv = "grok", ["grok", "-r", session]
            summary = grok_summary_path(session, "")
            if summary is not None:
                from urllib.parse import unquote

                cwd = unquote(summary.parent.parent.name)
        elif kind == "other-agent" and session:
            info = claude.transcript(session, "")
            agent = "claude"
            argv = [claude.claude_binary(), "-r", session]
            cwd = (info.cwd if info else "") or ""
        record = {
            "id": berth_id,
            "folder_id": folder_id,
            "title": title,
            "kind": kind,
            "state": "parked",
            "protect": 0,
            "cwd": cwd,
            "note": "",
            "recipe_argv": json.dumps(argv),
            "recipe_display": " ".join(argv) or "shell",
            "agent_name": agent,
            "agent_session_id": session,
            "agent_version": None,
            "attachments": "{}",
            "excerpt": "",
            "excerpt_at": None,
            "created_at": first["at"] if first else event["at"],
            "updated_at": event["at"],
            "parked_at": event["at"],
            "played_at": None,
            "last_used_at": event["at"],
        }
        with db.immediate():
            db.conn.execute(
                """
                INSERT INTO trash (id, deleted_at, reason, folder_path, detail, row, recovered)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (berth_id, event["at"], detail.get("reason") or "user", _folder_path(db, folder_id),
                 json.dumps({"rebuilt_from": "delete event"}), json.dumps(record, sort_keys=True)),
            )
        held.add(berth_id)
        added.append(berth_id)
    return added
