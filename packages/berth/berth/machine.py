"""The only writer of row state and workspace links.

``state``, ``cmux_workspace_id``, ``last_workspace_id``, and ``park_reason``
change here and nowhere else. Rows are inserted and deleted here. Each
change reads the current state inside the same ``BEGIN IMMEDIATE``
transaction, checks it against ``TABLE``, writes the row, and writes one
event, so two writers cannot both win and every change leaves a trace.

The table is docs/BERTH-STATE-MACHINE.md. A from-state that is not in the
table raises ``BAD_TRANSITION`` and changes nothing.
"""

from __future__ import annotations

import sqlite3

from berth.db import GUARDED_COLUMNS, Catalog
from berth.errors import BerthError
from berth.timeutil import utc_now

PARKED = "parked"
PLAYING = "playing"
LIVE = "live"
PAUSING = "pausing"
STATES = (PARKED, PLAYING, LIVE, PAUSING)
LINKED = (PLAYING, LIVE, PAUSING)

TABLE: dict[tuple[str, str], str] = {
    (PARKED, "relink"): LIVE,
    (PARKED, "play_claim"): PLAYING,
    (PLAYING, "play_bind"): PLAYING,
    (PLAYING, "play_created"): LIVE,
    (PLAYING, "play_failed"): PARKED,
    (PLAYING, "play_stale"): PARKED,
    (PLAYING, "relink"): LIVE,
    (LIVE, "pause_flush"): PAUSING,
    (PAUSING, "pause_closed"): PARKED,
    (PAUSING, "pause_failed"): LIVE,
    (PAUSING, "relink"): LIVE,
    (LIVE, "vanished"): PARKED,
    (PLAYING, "vanished"): PARKED,
    (PAUSING, "vanished"): PARKED,
    (LIVE, "retarget"): LIVE,
    (PARKED, "relabel"): PARKED,
}

_KEEP = object()


def allowed(state: str, event: str) -> bool:
    return (state, event) in TABLE


def transition(
    db: Catalog,
    berth_id: str,
    event: str,
    *,
    workspace_id: object = _KEEP,
    reason: str | None = None,
    fields: dict | None = None,
    log_kind: str = "transition",
    detail: dict | None = None,
    now: str | None = None,
) -> sqlite3.Row:
    """Move one row along the table and return the row as written.

    ``workspace_id`` names the workspace for a move into ``live`` or a
    ``play_bind``. Parking keeps the old id in ``last_workspace_id``.
    ``reason`` is the ``park_reason`` for a move into ``parked``; it defaults
    to the event name. ``fields`` are other columns written in the same
    transaction. ``log_kind`` and ``detail`` are the event row; the machine
    adds ``from``, ``to``, and ``event`` to the detail.
    """
    extra = dict(fields or {})
    bad = GUARDED_COLUMNS & set(extra)
    if bad:
        raise RuntimeError(f"transition fields may not set {sorted(bad)}")
    stamp = now or utc_now()
    with db.immediate():
        row = db.require_berth(berth_id)
        before = row["state"]
        after = TABLE.get((before, event))
        if after is None:
            raise BerthError(
                "BAD_TRANSITION",
                f"{row['title']} is {before}; {event} is not allowed",
                {"berth_id": berth_id, "state": before, "event": event},
            )
        current = row["cmux_workspace_id"]
        target = current if workspace_id is _KEEP else workspace_id
        write: dict = {"state": after, "updated_at": stamp}
        if event == "relabel":
            # A parked row learns a better reason. Its link and times stay.
            write["park_reason"] = reason or row["park_reason"]
        elif after == PARKED:
            write.update(
                cmux_workspace_id=None,
                cmux_pid=None,
                last_workspace_id=current or row["last_workspace_id"],
                park_reason=reason or event,
                parked_at=stamp,
            )
        elif after == LIVE:
            if not target:
                raise BerthError(
                    "BAD_TRANSITION",
                    f"{row['title']} cannot be live without a workspace",
                    {"berth_id": berth_id, "event": event},
                )
            write.update(
                cmux_workspace_id=target,
                last_workspace_id=target,
                park_reason=None,
                parked_at=None,
            )
        elif after == PLAYING:
            write.update(cmux_workspace_id=target, park_reason=None)
            if target:
                write["last_workspace_id"] = target
        else:
            write["cmux_workspace_id"] = target
        write.update(extra)
        db.write_berth(berth_id, **write)
        info = dict(detail or {})
        info.update({"from": before, "to": after, "event": event})
        if after == PARKED:
            info.setdefault("workspace_id", current)
            info["reason"] = write["park_reason"]
        elif target:
            info.setdefault("workspace_id", target)
        db.insert_event(berth_id, log_kind, info)
        return db.require_berth(berth_id)


def insert(
    db: Catalog,
    fields: dict,
    *,
    event: str,
    log_kind: str,
    detail: dict | None = None,
) -> None:
    """A new row. ``adopt`` rows are live on a workspace. ``import`` rows are parked."""
    state = fields.get("state")
    if (event, state) not in {("adopt", LIVE), ("import", PARKED)}:
        raise BerthError(
            "BAD_TRANSITION",
            f"a new row cannot start {state} by {event}",
            {"event": event, "state": state},
        )
    if state == LIVE and not fields.get("cmux_workspace_id"):
        raise BerthError("BAD_TRANSITION", "a live row needs a workspace", {"event": event})
    row = dict(fields)
    if state == LIVE:
        row["last_workspace_id"] = row["cmux_workspace_id"]
        row["park_reason"] = None
    else:
        row["cmux_workspace_id"] = None
        row.setdefault("park_reason", event)
    with db.immediate():
        db.insert_berth(row)
        info = dict(detail or {})
        info.update({"from": None, "to": state, "event": event})
        db.insert_event(row["id"], log_kind, info)


def delete(db: Catalog, berth_id: str, *, reason: str, detail: dict | None = None) -> None:
    """Remove a row. A copy goes to the trash first (``berth.trash``), for 30 days.

    Its events stay, so the history is not lost with it either.
    """
    from berth import trash

    with db.immediate():
        row = db.get_berth(berth_id)
        if row is None:
            return
        trash.put(db, row, reason=reason, detail=detail)
        tables = {item[0] for item in db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for table in ("intent", "runtime"):
            if table in tables:
                db.conn.execute(f"DELETE FROM {table} WHERE berth_id = ?", (berth_id,))
        info = dict(detail or {})
        info.update(
            {
                "from": row["state"],
                "to": None,
                "event": "delete",
                "reason": reason,
                "title": row["title"],
                "kind": row["kind"],
                "session_id": row["agent_session_id"],
                "workspace_id": row["cmux_workspace_id"] or row["last_workspace_id"],
            }
        )
        db.insert_event(berth_id, "delete", info)
        db.conn.execute("DELETE FROM berth WHERE id = ?", (berth_id,))
