"""Run intent rows the screen inserted.

One pass reads the table once and runs each intent id once. ``play`` calls
``play_berth``. ``pause`` calls ``pause_berth``. When ``detail.confirm`` is
true, pause is passed ``force`` and play is passed ``run_command``, which is
how a process confirm starts the command again. ``focus`` calls ``focus_berth``.
``cycle`` pauses a live row and plays the same session again, in a new
workspace, without focus. ``sleep`` closes a live row as ``pause`` does but
parks it asleep (``park_reason`` "sleep"), or turns a paused row into a
sleeping one. ``delete`` removes the catalog row. A live row's workspace is closed first.
A parked row is removed with no close. ``kind=berth`` is refused and the row
stays. Session files under ``~/.grok/sessions`` are not deleted.

Success deletes that intent row. ``BUSY`` without confirm leaves the row and
does not close the workspace. A failure that can clear by itself (the row is
still playing or pausing, cmux is down) writes one ``intent_failed`` event and
leaves the row for a later pass. Any other failure writes ``intent_failed``
and deletes the row: it would fail the same way every pass. (2026-10-28: a
play of a session whose transcript was gone was retried twice a second for
minutes, and the screen only ever said "already recorded".) The screen reads
the ``intent_failed`` event and says why.
"""

from __future__ import annotations

import json
import sqlite3

from berth.catalog import ensure
from berth.db import Catalog
from berth.errors import BerthError
from berth.hooks import hook_for
from berth.model import HookRecord
from berth.ops import delete_berth, focus_berth
from berth.state import pause_berth, play_berth

# Failures worth another pass: the row is mid-change, or cmux is not answering.
RETRY = frozenset({"IN_PROGRESS", "CMUX_DOWN"})


def pending_rows(db: Catalog) -> list[sqlite3.Row]:
    ensure(db.conn)
    return list(
        db.conn.execute(
            "SELECT id, at, op, berth_id, detail FROM intent ORDER BY at, id"
        ).fetchall()
    )


def drain_once(
    db: Catalog,
    cmux,
    *,
    hooks: dict[str, HookRecord] | None = None,
    pid_alive_fn,
    caller_workspace_id: str | None = None,
    sessions_root=None,
) -> list[dict]:
    """Run each current intent id once. Returns one outcome per id."""
    found = pending_rows(db)
    seen: set[str] = set()
    outcomes: list[dict] = []
    for row in found:
        intent_id = str(row["id"])
        if intent_id in seen:
            continue
        seen.add(intent_id)
        outcomes.append(
            _run_one(
                db,
                cmux,
                row,
                hooks or {},
                pid_alive_fn,
                caller_workspace_id,
                sessions_root,
            )
        )
    return outcomes


def _run_one(
    db: Catalog,
    cmux,
    row: sqlite3.Row,
    hooks: dict[str, HookRecord],
    pid_alive_fn,
    caller_workspace_id: str | None,
    sessions_root,
) -> dict:
    intent_id = str(row["id"])
    op = str(row["op"])
    berth_id = str(row["berth_id"])
    detail = _detail(row["detail"])
    if detail is None:
        _fail(db, berth_id, intent_id, op, "intent_failed", "intent detail is not an object")
        return {"id": intent_id, "op": op, "result": "failed", "code": "intent_failed"}
    confirm = detail.get("confirm") is True
    if "by_workspace" in detail:
        caller_workspace_id = detail.get("by_workspace") or None
    # What the Berth screen asked for, and from which screen: a sleep or a park from a key press was recorded only
    # as the close itself, so Research Agent's sleep at 23:31:16Z on 2026-11-03 could not be traced (key press,
    # intent, then deleted). Now each request is an event before it runs.
    if op in {"pause", "sleep", "idle", "cycle", "delete", "fork", "focus", "play"} and db.get_berth(berth_id) is not None:
        with db.immediate():
            db.insert_event(berth_id, "screen_request", {"op": op, "by_workspace": detail.get("by_workspace"),
                                                         "confirm": confirm})
    try:
        if op == "play":
            play_berth(
                db,
                cmux,
                berth_id,
                focus=detail.get("focus") is not False,
                run_command=confirm,
                sessions_root=sessions_root,
            )
        elif op == "pause":
            record = db.get_berth(berth_id)
            workspace_id = record["cmux_workspace_id"] if record is not None else None
            pause_berth(
                db,
                cmux,
                berth_id,
                force=confirm,
                confirm=confirm,
                hook=hook_for(hooks, workspace_id),
                caller_workspace_id=caller_workspace_id,
                pid_alive_fn=pid_alive_fn,
            )
        elif op in {"sleep", "idle"}:
            record = db.get_berth(berth_id)
            workspace_id = record["cmux_workspace_id"] if record is not None else None
            pause_berth(
                db,
                cmux,
                berth_id,
                force=confirm,
                confirm=confirm,
                hook=hook_for(hooks, workspace_id),
                caller_workspace_id=caller_workspace_id,
                pid_alive_fn=pid_alive_fn,
                reason=op,
            )
        elif op == "focus":
            focus_berth(db, cmux, berth_id)
        elif op == "fork":
            from berth.ops import fork_berth

            fork_berth(db, cmux, berth_id, focus=True)
        elif op == "cycle":
            from berth.ops import cycle_berth

            record = db.get_berth(berth_id)
            workspace_id = record["cmux_workspace_id"] if record is not None else None
            cycle_berth(
                db,
                cmux,
                berth_id,
                confirm=confirm,
                focus=detail.get("focus") is True,
                hook=hook_for(hooks, workspace_id),
                caller_workspace_id=caller_workspace_id,
                pid_alive_fn=pid_alive_fn,
                sessions_root=sessions_root,
            )
        elif op == "delete":
            delete_berth(
                db,
                cmux,
                berth_id,
                caller_workspace_id=caller_workspace_id,
                force=detail.get("force") is True,
            )
        else:
            raise BerthError("NOT_FOUND", f"unknown intent {op}", {"op": op, "intent_id": intent_id})
    except BerthError as exc:
        # BUSY (the pane is busy, or holds a draft Berth cannot save) fails like any other
        # refusal, so the screen says why; it used to wait in the queue and close later.
        _fail(db, berth_id, intent_id, op, exc.code, exc.message)
        if exc.code not in RETRY:
            _delete(db, intent_id)
        return {"id": intent_id, "op": op, "result": "failed", "code": exc.code}
    _delete(db, intent_id)
    return {"id": intent_id, "op": op, "result": "done"}


def _detail(raw: str | None) -> dict | None:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _delete(db: Catalog, intent_id: str) -> None:
    db.conn.execute("DELETE FROM intent WHERE id = ?", (intent_id,))


def _fail(db: Catalog, berth_id: str, intent_id: str, op: str, code: str, message: str) -> None:
    if _already_failed(db, berth_id, intent_id):
        return
    db.insert_event(
        berth_id,
        "intent_failed",
        {"intent_id": intent_id, "op": op, "code": code, "message": message},
    )


def _already_failed(db: Catalog, berth_id: str, intent_id: str) -> bool:
    try:
        events = db.events_for(berth_id)
    except sqlite3.Error:
        return False
    for event in events:
        if event["kind"] != "intent_failed":
            continue
        try:
            detail = json.loads(event["detail"] or "{}")
        except json.JSONDecodeError:
            continue
        if isinstance(detail, dict) and detail.get("intent_id") == intent_id:
            return True
    return False
