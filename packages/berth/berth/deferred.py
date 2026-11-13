"""Actions Berth's watcher runs later: when a session goes idle, or right after the call that asked.

A session cannot close its own pane: the process that asks (its MCP server,
or a ``berth`` command in its shell) dies with the pane before the reopen
half of a cycle runs. So "cycle me" is queued here and run by the watcher,
which launchd keeps outside every pane (2026-10-29: Settings Agent asked to be
cycled to load a new MCP server, and Berth could only refuse).

The same queue serves "do it when it is idle" for any session: the watcher
reads the pane each tick (``berth/activity.py``) and acts as soon as the
session is not busy and holds no draft Berth cannot save, so no turn,
background work, or typed text is lost. By ``until`` it gives up, or, when
the request was ``force``, acts anyway and cuts the work off.

Prompts do not wait here: they wait in the session's mailbox
(``berth/delivery.py``), in order, each with its sender and a receipt. Until
2026-10-30 a queued prompt took this one slot, and any new request replaced it.
A queued close goes before the session's queued prompts.

One queued action per session; a new request replaces it, and ``cancel``
takes it back (the MCP's ``control`` with ``cancel``, 2026-10-29: before,
nothing could). Every outcome is an event on the row: ``deferred_done``,
``deferred_failed``, ``deferred_gave_up``, ``deferred_dropped``,
``deferred_cancelled``.
"""

from __future__ import annotations

from berth.sqlite_schema import schema_step

import uuid
from datetime import datetime, timedelta, timezone

from berth.errors import BerthError
from berth.timeutil import parse_time

ACTIONS = ("cycle", "sleep", "idle", "park")
DEFAULT_WAIT_MINUTES = 60
MAX_WAIT_MINUTES = 24 * 60
# A session that asked to be closed "now" finishes the tool call first.
SELF_GRACE_SECONDS = 3
# Cycles started per watcher tick (2 s). Each one starts a Claude and its MCP servers; 34 queued at once (an account
# switch, 2026-10-31) would start 34 together. The rest wait their turn, in the order they were queued.
CYCLES_PER_TICK = 2

DDL = """
CREATE TABLE IF NOT EXISTS deferred (
  id TEXT PRIMARY KEY,
  berth_id TEXT NOT NULL UNIQUE,
  action TEXT NOT NULL,
  queued_at TEXT NOT NULL,
  not_before TEXT NOT NULL,
  until TEXT NOT NULL,
  force INTEGER NOT NULL DEFAULT 0,
  idle INTEGER NOT NULL DEFAULT 1,
  requested_by TEXT NOT NULL DEFAULT '',
  prompt TEXT
);
"""


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _ensure(db) -> None:
    """Catalogs made before 2026-10-30 lack the prompt column."""
    columns = {row[1] for row in db.conn.execute("PRAGMA table_info(deferred)")}
    if "prompt" not in columns:
        schema_step(db.conn, "ALTER TABLE deferred ADD COLUMN prompt TEXT")


def queue(db, berth_id: str, action: str, *, idle: bool = True, force: bool = False,
          wait_minutes: int | None = None, delay_seconds: float = 0, requested_by: str = "",
          prompt: str | None = None, now: datetime | None = None) -> dict:
    """Queue (or replace) the action for one session. Returns what was queued."""
    _ensure(db)
    if action not in ACTIONS:
        raise BerthError("BAD_ARGUMENT", f"Berth's watcher runs {', '.join(ACTIONS)}", {"action": action})
    minutes = DEFAULT_WAIT_MINUTES if wait_minutes is None else int(wait_minutes)
    if not 1 <= minutes <= MAX_WAIT_MINUTES:
        raise BerthError("BAD_ARGUMENT", f"wait is 1 to {MAX_WAIT_MINUTES} minutes", {"wait": wait_minutes})
    moment = now or datetime.now(timezone.utc)
    row = db.require_berth(berth_id)
    item = {
        "id": str(uuid.uuid4()),
        "berth_id": row["id"],
        "action": action,
        "queued_at": _stamp(moment),
        "not_before": _stamp(moment + timedelta(seconds=delay_seconds)),
        "until": _stamp(moment + timedelta(minutes=minutes)),
        "force": 1 if force else 0,
        "idle": 1 if idle else 0,
        "requested_by": requested_by,
        "prompt": None,
    }
    with db.immediate():
        for old in db.conn.execute("SELECT * FROM deferred WHERE berth_id = ?", (row["id"],)).fetchall():
            # The one it replaces leaves a trace (2026-11-08: a cycle whose deadline passed while the Mac slept was
            # replaced by a new request before the watcher woke to log "gave up", and its history just stopped).
            db.insert_event(row["id"], "deferred_replaced", {"action": old["action"], "until": old["until"],
                                                             "requested_by": old["requested_by"]})
        db.conn.execute("DELETE FROM deferred WHERE berth_id = ?", (row["id"],))
        db.conn.execute(
            "INSERT INTO deferred (id, berth_id, action, queued_at, not_before, until, force, idle, requested_by, prompt) "
            "VALUES (:id, :berth_id, :action, :queued_at, :not_before, :until, :force, :idle, :requested_by, :prompt)", item)
        db.insert_event(row["id"], "deferred_queued", {key: item[key] for key in ("action", "until", "force", "idle", "requested_by")})
    return item


def pending(db, berth_id: str | None = None) -> list[dict]:
    _ensure(db)
    query = "SELECT * FROM deferred" + (" WHERE berth_id = ?" if berth_id else "") + " ORDER BY queued_at"
    return [dict(row) for row in db.conn.execute(query, (berth_id,) if berth_id else ())]


def cancel(db, berth_id: str, *, requested_by: str = "") -> dict | None:
    """Take back the action queued for one session. Returns it, or None when nothing was queued."""
    items = pending(db, berth_id)
    if not items:
        return None
    _finish(db, items[0], "deferred_cancelled", {"cancelled_by": requested_by})
    return items[0]


def _finish(db, item: dict, event: str, detail: dict) -> None:
    with db.immediate():
        db.conn.execute("DELETE FROM deferred WHERE id = ?", (item["id"],))
        db.insert_event(item["berth_id"], event, {"action": item["action"], "requested_by": item["requested_by"], **detail})


def _started(db, item: dict) -> bool:
    """Whether the watcher already began this queued action (its ``deferred_started`` event)."""
    for event in db.conn.execute("SELECT detail FROM event WHERE berth_id = ? AND kind = 'deferred_started' "
                                 "ORDER BY at DESC LIMIT 5", (item["berth_id"],)).fetchall():
        if f'"{item["id"]}"' in (event["detail"] or ""):
            return True
    return False


def run_due(db, cmux, *, now: datetime | None = None, observe_fn=None, pid_alive_fn=None) -> list[dict]:
    """Run what is due. Called by the watcher every tick. Returns one outcome per action it settled."""
    from berth.activity import observe, program_states
    from berth.hooks import hook_for, load_hooks
    from berth.ops import cycle_berth, idle_berth, sleep_berth
    from berth.place import pid_alive
    from berth.state import pause_berth

    moment = now or datetime.now(timezone.utc)
    alive = pid_alive_fn or pid_alive
    look = observe_fn or (lambda row: observe(cmux, row, programs=program_states(db.path.parent)))
    outcomes = []
    cycles = 0
    for item in pending(db):
        if moment < parse_time(item["not_before"]):
            continue
        if item["action"] == "cycle" and cycles >= CYCLES_PER_TICK:
            continue  # its turn comes next tick
        row = db.get_berth(item["berth_id"])
        if row is not None and row["state"] in ("pausing", "playing") and moment < parse_time(item["until"]):
            # Mid-change: wait, rather than drop it. A cycle cut off by a watcher restart left its row "pausing"
            # (2026-11-05); the watcher puts such a row back to live (observer.STALE_PAUSE_SECONDS), and this runs then.
            continue
        if (row is not None and row["state"] == "parked" and item["action"] == "cycle" and _started(db, item)
                and moment < parse_time(item["until"])):
            # This cycle closed the pane, and the watcher stopped before the reopen: finish it (2026-11-05).
            cycles += 1
            try:
                cycle_berth(db, cmux, row["id"], confirm=bool(item["force"]), focus=False, hook=None,
                            caller_workspace_id=None, pid_alive_fn=alive)
            except BerthError as exc:
                if exc.code in {"IN_PROGRESS", "CMUX_DOWN"}:
                    continue
                _finish(db, item, "deferred_failed", {"code": exc.code, "message": exc.message})
                outcomes.append({"id": row["id"], "result": "failed", "code": exc.code})
                continue
            _finish(db, item, "deferred_done", {"forced": False, "resumed": True})
            outcomes.append({"id": row["id"], "result": "done", "resumed": True})
            continue
        if row is None or row["state"] != "live":
            state = row["state"] if row is not None else "deleted"
            _finish(db, item, "deferred_dropped", {"why": f"the session is {state}, not live"})
            outcomes.append({"id": item["berth_id"], "result": "dropped"})
            continue
        force = bool(item["force"])
        why: list[str] = []
        if item["idle"]:
            activity = look(row)
            why = list(activity.reasons)
            keys = row.keys()
            flag = row["attention"] if "attention" in keys else None
            if flag in ("urgent", "follow_up"):
                # Waiting for Dominik is not idle (2026-11-08, UNI Orch's terms via the Accounts Agent: never move a
                # session while a question waits for him). An open question in the pane already counted; a raised
                # flag, with the turn over and the pane at rest, did not.
                reason = (row["attention_why"] if "attention_why" in keys else "") or ""
                why.append(f"waiting for Dominik ({flag.replace('_', '-')}{': ' + reason if reason else ''})")
            if activity.draft is not None and not activity.draft.exact:
                why.append(f"a draft Berth cannot save exactly ({activity.draft.why})")
        if why:
            if moment < parse_time(item["until"]):
                continue  # still busy: look again next tick
            if not force:
                _finish(db, item, "deferred_gave_up", {"why": why})
                outcomes.append({"id": row["id"], "result": "gave up", "why": why})
                continue
        hook = hook_for(load_hooks(), row["cmux_workspace_id"])
        if item["action"] == "cycle":
            cycles += 1
        with db.immediate():  # so a watcher that stops mid-way knows this action closed the pane (``_started``)
            db.insert_event(row["id"], "deferred_started", {"action": item["action"], "item": item["id"]})
        try:
            if item["action"] == "cycle":
                cycle_berth(db, cmux, row["id"], confirm=force, focus=False, hook=hook,
                            caller_workspace_id=None, pid_alive_fn=alive)
            elif item["action"] == "sleep":
                sleep_berth(db, cmux, row["id"], pid_alive_fn=alive, hook=hook, caller_workspace_id=None, confirm=force)
            elif item["action"] == "idle":
                idle_berth(db, cmux, row["id"], pid_alive_fn=alive, hook=hook, caller_workspace_id=None, confirm=force)
            else:
                pause_berth(db, cmux, row["id"], force=force, confirm=force, hook=hook, caller_workspace_id=None,
                            pid_alive_fn=alive)
        except BerthError as exc:
            if exc.code in {"IN_PROGRESS", "CMUX_DOWN"} and moment < parse_time(item["until"]):
                continue
            if exc.code == "BUSY" and not force and moment < parse_time(item["until"]):
                continue  # it got busy between the look and the close
            _finish(db, item, "deferred_failed", {"code": exc.code, "message": exc.message})
            outcomes.append({"id": row["id"], "result": "failed", "code": exc.code})
            continue
        _finish(db, item, "deferred_done", {"forced": bool(why)})
        outcomes.append({"id": row["id"], "result": "done", "forced": bool(why)})
    return outcomes


