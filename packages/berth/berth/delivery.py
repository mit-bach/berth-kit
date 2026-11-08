"""Delivery: how a prompt from a program or an agent gets into one session's prompt box (2026-10-30).

Berth owns program-to-agent prompt delivery. Slack hooks
(``~/FORGE/systems/events/slack-hooks``) is its first user; the next
integrations use the same primitive. Dominik's words: integrations built from
the same primitives, "with invariants nothing can break".

A prompt is typed into the session's prompt box and submitted, so the agent
reads it as the operator's; it is never a SendMessage. Each session has a
mailbox: an ordered queue of prompts, each with a named ``sender``.

Every prompt gets a receipt (its ``id``), and its ``state`` moves only forward:

    queued -> typing -> delivered
    queued -> superseded | cancelled | expired | failed
    typing -> failed            (Berth stopped mid-typing: never typed twice)

The invariants, each enforced here and tested (``tests/test_delivery.py``):

1. **At most once.** A prompt is claimed (``queued -> typing``) in one atomic
   update before it is typed, so two deliverers cannot both type it. One left
   ``typing`` by a crash is ``failed``, never retyped.
2. **In order, one at a time.** Only the oldest queued prompt of a session is
   ever typed, and only when the session is ready: its prompt on screen, no
   turn running, no question, permission, plan or dialog, nobody typing
   (``berth/send.py``; cmux refuses a paste over a draft). A ``busy`` prompt
   (2026-11-05) is also typed while a Claude session works, with the empty box
   on screen, where Claude queues it; never into a question, a permission, a
   menu or a draft (``typable``). A new prompt never jumps ahead of queued ones.
3. **Senders never clobber each other.** A prompt is replaced only by the same
   sender with the same ``key`` (``superseded``), and cancelled only by its
   own sender.
4. **Every prompt ends once, with a reason,** recorded as an event on the row
   (``delivery_*``), and readable by id until it is purged (30 days).
5. **A closed session is woken first** when the prompt asks (``wake``), and a
   queued close (``berth/deferred.py``) goes before queued prompts.
6. **The sender is told, not polled.** A prompt may carry ``notify``, a command
   (argv, no shell) Berth runs once, detached, when it ends ``delivered``,
   ``expired``, ``failed`` or ``cancelled``, with ``BERTH_DELIVERY`` and
   ``BERTH_STATE`` in its environment. Senders run nothing on a timer
   (Dominik, 2026-10-30: "There should be no cycle where every 5 minutes we
   check for something").

Callers: the MCP's ``send`` (agents), ``berth send`` / ``berth receipt`` /
``berth unsend`` (programs, JSON out, no cmux environment needed), and the
watcher, which types queued prompts each tick (``run_due``).
"""

from __future__ import annotations

from berth.sqlite_schema import schema_step

import json
import uuid
from datetime import datetime, timedelta, timezone

from berth.errors import BerthError
from berth.timeutil import parse_time

STATES = ("queued", "typing", "delivered", "superseded", "cancelled", "expired", "failed")
OPEN = ("queued", "typing")
DEFAULT_WAIT_MINUTES = 1440
MAX_WAIT_MINUTES = 7 * 1440
TYPING_STALE_SECONDS = 120  # a prompt left "typing" this long: its deliverer stopped
KEEP_DAYS = 30

DDL = """
CREATE TABLE IF NOT EXISTS delivery (
  id TEXT PRIMARY KEY,
  berth_id TEXT NOT NULL,
  sender TEXT NOT NULL,
  key TEXT,
  prompt TEXT NOT NULL,
  meta TEXT,
  wake INTEGER NOT NULL DEFAULT 0,
  queued_at TEXT NOT NULL,
  until TEXT NOT NULL,
  state TEXT NOT NULL,
  settled_at TEXT,
  why TEXT,
  notify TEXT
);
CREATE INDEX IF NOT EXISTS delivery_open ON delivery(state, berth_id, queued_at);
"""


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


NOTIFY_ON = ("delivered", "expired", "failed", "cancelled")


def migrate(conn) -> None:
    """The one queued send per session that ``deferred`` held until 2026-10-30 becomes a queued prompt here."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(delivery)")}
    if "notify" not in columns:
        schema_step(conn, "ALTER TABLE delivery ADD COLUMN notify TEXT")
    if "kind" not in columns:
        # Unused since 2026-11-05 (messages were taken out); kept because Berth MCP servers started before then
        # still read it, and every row is a prompt.
        schema_step(conn, "ALTER TABLE delivery ADD COLUMN kind TEXT NOT NULL DEFAULT 'prompt'")
    if "busy" not in columns:
        # 2026-11-05: typed while a Claude session works, too (``typable``), not only when it is ready.
        schema_step(conn, "ALTER TABLE delivery ADD COLUMN busy INTEGER NOT NULL DEFAULT 0")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(deferred)")}
    if "prompt" not in columns or "action" not in columns:
        return
    for row in conn.execute("SELECT * FROM deferred WHERE action = 'send'").fetchall():
        conn.execute(
            "INSERT OR IGNORE INTO delivery (id, berth_id, sender, key, prompt, meta, wake, queued_at, until, state) "
            "VALUES (?, ?, ?, NULL, ?, NULL, 0, ?, ?, 'queued')",
            (row["id"], row["berth_id"], row["requested_by"] or "an agent", row["prompt"] or "", row["queued_at"],
             row["until"]))
        conn.execute("DELETE FROM deferred WHERE id = ?", (row["id"],))


def _receipt(row) -> dict:
    item = dict(row)
    try:
        item["meta"] = json.loads(item["meta"]) if item.get("meta") else None
    except ValueError:
        item["meta"] = None
    try:
        item["notify"] = json.loads(item["notify"]) if item.get("notify") else None
    except ValueError:
        item["notify"] = None
    item["wake"] = bool(item.get("wake"))
    item["busy"] = bool(item.get("busy"))
    item.pop("kind", None)
    return item


def _find(db, delivery_id: str):
    """A delivery by its id, or by the id's first 4+ characters when only it starts so (2026-11-04)."""
    row = db.conn.execute("SELECT * FROM delivery WHERE id = ?", (delivery_id,)).fetchone()
    wanted = str(delivery_id or "").strip().lower()
    if row is None and len(wanted) >= 4:
        rows = db.conn.execute("SELECT * FROM delivery WHERE id LIKE ? ESCAPE '\\' LIMIT 2",
                               (wanted.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",)).fetchall()
        if len(rows) > 1:
            raise BerthError("AMBIGUOUS", f"more than one delivery starts {wanted}", {"id": delivery_id})
        row = rows[0] if rows else None
    if row is None:
        raise BerthError("NOT_FOUND", f"no delivery {delivery_id}", {"id": delivery_id})
    return row


def receipt(db, delivery_id: str) -> dict:
    row = _find(db, delivery_id)
    out = _receipt(row)
    if out["state"] == "queued":
        out["position"] = position(db, out)
    if out["state"] in OPEN:
        stalled = _stalled(db)
        if stalled:
            out["stalled"] = stalled  # the watcher, not the session, is why it waits (berth/health.py)
    return out


def _stalled(db) -> str | None:
    """Why the watcher cannot type waiting prompts right now, or None: not ticking, or its delivery job failing."""
    import time

    try:
        health = json.loads((db.path.parent / "watch-health.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    from berth.health import STALE_SECONDS

    if time.time() - float(health.get("at") or 0) > STALE_SECONDS:
        return "Berth's watcher is not ticking: launchctl kickstart -k gui/$(id -u)/com.bach.berth.watch"
    job = (health.get("jobs") or {}).get("delivery") or {}
    if job.get("error"):
        return f"Berth's delivery job is failing: {job['error']}"
    return None


def receipts(db, *, sender: str | None = None, berth_id: str | None = None, open_only: bool = False,
             limit: int = 200) -> list[dict]:
    query, args = "SELECT * FROM delivery WHERE 1 = 1", []
    if sender is not None:
        query += " AND sender = ?"
        args.append(sender)
    if berth_id is not None:
        query += " AND berth_id = ?"
        args.append(berth_id)
    if open_only:
        query += " AND state IN ('queued', 'typing')"
    query += " ORDER BY queued_at DESC LIMIT ?"
    args.append(int(limit))
    return [_receipt(row) for row in db.conn.execute(query, args).fetchall()]


def waiting(db, berth_id: str) -> list[dict]:
    """The session's open prompts, oldest first: the order they will be typed in."""
    rows = db.conn.execute("SELECT * FROM delivery WHERE berth_id = ? AND state IN ('queued', 'typing') "
                           "ORDER BY queued_at, id", (berth_id,)).fetchall()
    return [_receipt(row) for row in rows]


def position(db, item: dict) -> int:
    """1 when it is next."""
    ahead = db.conn.execute("SELECT count(*) FROM delivery WHERE berth_id = ? AND state IN ('queued', 'typing') "
                            "AND (queued_at < ? OR (queued_at = ? AND id < ?))",
                            (item["berth_id"], item["queued_at"], item["queued_at"], item["id"])).fetchone()[0]
    return int(ahead) + 1


def _event(db, berth_id: str, kind: str, item: dict, **extra) -> None:
    db.insert_event(berth_id, kind, {"delivery": item["id"], "sender": item["sender"], **extra})


def _settle(db, item: dict, state: str, why: str | None = None, *, frm: tuple[str, ...] = OPEN,
            now: datetime | None = None) -> bool:
    """Move one prompt to a final state, only from ``frm``. True if it moved (checked in the same statement)."""
    marks = ", ".join("?" * len(frm))
    with db.immediate():
        moved = db.conn.execute(f"UPDATE delivery SET state = ?, settled_at = ?, why = ? WHERE id = ? AND state IN ({marks})",
                                (state, _stamp(_now(now)), why, item["id"], *frm)).rowcount
        if moved:
            _event(db, item["berth_id"], f"delivery_{state}", item, why=why)
    if moved and state in NOTIFY_ON:
        _notify(db, item, state)
    return bool(moved)


def _notify(db, item: dict, state: str, *, spawn=None) -> None:
    """Run the sender's ``notify`` command once, detached. It never holds up or breaks delivery."""
    import os
    import subprocess

    raw = item.get("notify")
    if raw is None:
        row = db.conn.execute("SELECT notify FROM delivery WHERE id = ?", (item["id"],)).fetchone()
        raw = row[0] if row is not None else None
    try:
        argv = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        argv = None
    if not argv:
        return
    env = {**os.environ, "BERTH_DELIVERY": item["id"], "BERTH_STATE": state}
    try:
        (spawn or subprocess.Popen)([str(part) for part in argv], env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        db.insert_event(item["berth_id"], "delivery_notified", {"delivery": item["id"], "state": state})
    except (OSError, ValueError) as exc:
        db.insert_event(item["berth_id"], "delivery_notify_failed", {"delivery": item["id"], "error": str(exc)})


def enqueue(db, berth_id: str, prompt: str, *, sender: str, key: str | None = None, meta=None, wake: bool = False,
            wait_minutes: int | None = None, notify: list[str] | None = None, now: datetime | None = None,
            busy: bool = False) -> dict:
    """Put a prompt in the session's mailbox. With ``key``, it replaces this sender's own queued one with that key.

    ``busy`` (2026-11-05, Dominik: "you can send prompts when the agent is busy"): it is typed while the session
    works too, where Claude Code queues it, not only once it is ready (``typable``). Claude only, and never a slash
    command: anything else keeps waiting for a ready prompt, and its receipt says ``busy: false``.
    """
    from berth.send import agent_of, check, is_slash

    row = db.require_berth(berth_id)
    if row["state"] == "deleted":  # in the trash: gone for a sender (2026-11-04, slack-watch keys on NOT_FOUND)
        raise BerthError("NOT_FOUND", f"{row['title']} is in the trash", {"id": row["id"], "state": "deleted"})
    check(prompt)
    sender = " ".join(str(sender or "").split())
    if not sender:
        raise BerthError("BAD_ARGUMENT", "a prompt names its sender", {})
    minutes = DEFAULT_WAIT_MINUTES if wait_minutes is None else int(wait_minutes)
    if not 1 <= minutes <= MAX_WAIT_MINUTES:
        raise BerthError("BAD_ARGUMENT", f"wait is 1 to {MAX_WAIT_MINUTES} minutes", {"wait": wait_minutes})
    moment = _now(now)
    busy = bool(busy) and agent_of(row) == "claude" and not is_slash(prompt)
    item = {"id": str(uuid.uuid4()), "berth_id": row["id"], "sender": sender, "key": key or None, "prompt": prompt,
            "meta": json.dumps(meta, sort_keys=True) if meta is not None else None, "wake": 1 if wake else 0,
            "notify": json.dumps([str(part) for part in notify]) if notify else None,
            "queued_at": _stamp(moment), "until": _stamp(moment + timedelta(minutes=minutes)), "state": "queued",
            "busy": 1 if busy else 0}
    if key:
        for old in db.conn.execute("SELECT * FROM delivery WHERE berth_id = ? AND sender = ? AND key = ? "
                                   "AND state = 'queued'", (row["id"], sender, key)).fetchall():
            _settle(db, dict(old), "superseded", f"replaced by {item['id']}", frm=("queued",), now=moment)
    with db.immediate():
        db.conn.execute("INSERT INTO delivery (id, berth_id, sender, key, prompt, meta, wake, queued_at, until, state, notify, "
                        "busy) VALUES (:id, :berth_id, :sender, :key, :prompt, :meta, :wake, :queued_at, :until, :state, "
                        ":notify, :busy)", item)
        _event(db, row["id"], "delivery_queued", item, until=item["until"], **({"busy": True} if busy else {}))
    return receipt(db, item["id"])


def typable(item: dict, look) -> str | None:
    """The text to type for ``item`` now, or None to keep it waiting.

    Ready: the prompt. Working, for a ``busy`` prompt: the prompt with ``BUSY_NOTE`` before it (finish what you are
    doing first), as the MCP's ``send`` ``when: now`` does, and only while the box is empty. Never a question, a
    permission, a plan, a menu, a usage limit, or a box holding Dominik's draft (cmux refuses that paste as well).
    """
    from berth.send import BUSY_NOTE

    if look.situation == "ready":
        return item["prompt"]
    if look.situation == "working" and item.get("busy") and look.activity.draft is None:
        return BUSY_NOTE + item["prompt"]
    return None


def cancel(db, delivery_id: str, *, sender: str) -> dict:
    """Take back a queued prompt. Only its own sender can."""
    item = receipt(db, delivery_id)
    if item["sender"] != " ".join(str(sender or "").split()):
        raise BerthError("PROTECTED", f"that prompt is {item['sender']}'s; only its sender takes it back",
                         {"id": item["id"], "sender": item["sender"]})
    if not _settle(db, item, "cancelled", f"taken back by {item['sender']}", frm=("queued",)):
        raise BerthError("IN_PROGRESS", f"it is already {receipt(db, item['id'])['state']}", {"id": item["id"]})
    return receipt(db, item["id"])


def _claim(db, item: dict) -> bool:
    with db.immediate():
        return bool(db.conn.execute("UPDATE delivery SET state = 'typing', settled_at = ? WHERE id = ? AND state = 'queued'",
                                    (_stamp(_now(None)), item["id"])).rowcount)


def type_one(db, cmux, row, item: dict, *, reread=None, now: datetime | None = None, text: str | None = None) -> str:
    """Type this prompt now, at most once. Returns its state afterwards: delivered, queued (try again), or failed.

    ``text`` is what ``typable`` chose (the prompt, or the prompt after the busy note); default the prompt.
    """
    from berth.send import deliver, refused

    if not _claim(db, item):
        return receipt(db, item["id"])["state"]  # someone else has it, or it ended
    try:
        detail = deliver(cmux, row, text if text is not None else item["prompt"], reread=reread)
    except BerthError as exc:
        if refused(exc) or exc.code in {"CMUX_DOWN"}:
            # Nothing was typed (cmux refused before sending): it goes back to wait for its turn.
            with db.immediate():
                db.conn.execute("UPDATE delivery SET state = 'queued', settled_at = NULL WHERE id = ? AND state = 'typing'",
                                (item["id"],))
            return "queued"
        _settle(db, item, "failed", f"{exc.code}: {exc.message}", frm=("typing",), now=now)
        return "failed"
    _settle(db, item, "delivered", None, frm=("typing",), now=now)
    db.insert_event(row["id"], "delivery_typed", {"delivery": item["id"], **{k: v for k, v in detail.items() if k != "id"}})
    return "delivered"


def offer(db, cmux, berth_id: str, prompt: str, *, sender: str, key: str | None = None, meta=None, wake: bool = False,
          wait_minutes: int | None = None, notify: list[str] | None = None, look=None, busy: bool = False) -> dict:
    """Queue a prompt, and type it at once when the session is ready (or, ``busy``, working) and nothing is ahead
    of it. Returns its receipt."""
    item = enqueue(db, berth_id, prompt, sender=sender, key=key, meta=meta, wake=wake, wait_minutes=wait_minutes,
                   notify=notify, busy=busy)
    row = db.require_berth(berth_id)
    try:
        # Best effort from here: the prompt is in the mailbox, so a failure now must not read as "not sent"
        # (2026-10-30: cmux not found after the enqueue returned an error, and a retry would have typed it twice).
        if row["state"] == "live" and look is not None and position(db, item) == 1:
            text = typable(item, look())
            if text is not None:
                type_one(db, cmux, row, item, reread=look, text=text)
        elif row["state"] == "parked" and wake:
            _wake(db, cmux, row, item)
    except BerthError as exc:
        db.insert_event(row["id"], "delivery_deferred", {"delivery": item["id"], "code": exc.code, "message": exc.message})
    return receipt(db, item["id"])


def _wake(db, cmux, row, item: dict) -> None:
    from berth.state import play_berth

    try:
        play_berth(db, cmux, row["id"], focus=False)
        db.insert_event(row["id"], "delivery_woke", {"delivery": item["id"], "sender": item["sender"]})
    except BerthError as exc:
        db.insert_event(row["id"], "delivery_wake_failed", {"delivery": item["id"], "code": exc.code,
                                                             "message": exc.message})


def run_due(db, cmux, *, now: datetime | None = None, look_fn=None, closes_fn=None) -> list[dict]:
    """The watcher's tick: expire what waited too long, then type each session's next prompt if it is ready."""
    from berth.activity import observe, program_states
    from berth.send import look as send_look

    import time

    moment = _now(now)
    outcomes: list[dict] = []
    if time.monotonic() - _purged[0] > 3600:
        _purged[0] = time.monotonic()
        purge(db, now=moment)
    for raw in db.conn.execute("SELECT * FROM delivery WHERE state IN ('queued', 'typing')").fetchall():
        item = dict(raw)
        if item["state"] == "typing" and (moment - parse_time(item["settled_at"] or item["queued_at"])).total_seconds() \
                > TYPING_STALE_SECONDS:
            _settle(db, item, "failed", "Berth stopped while typing it; it may be in the prompt box. Not typed again.",
                    frm=("typing",), now=moment)
            outcomes.append({"id": item["id"], "result": "failed"})
        elif item["state"] == "queued" and _gone(db, item["berth_id"]):
            _settle(db, item, "failed", "the session was deleted", frm=("queued",), now=moment)
            outcomes.append({"id": item["id"], "result": "failed"})
        elif item["state"] == "queued" and moment >= parse_time(item["until"]):
            _settle(db, item, "expired", "the session was not ready in time", frm=("queued",), now=moment)
            outcomes.append({"id": item["id"], "result": "expired"})
    closing = closes_fn() if closes_fn is not None else _closes(db)
    heads: dict[str, dict] = {}
    for raw in db.conn.execute("SELECT * FROM delivery WHERE state IN ('queued', 'typing') "
                               "ORDER BY queued_at, id").fetchall():
        heads.setdefault(raw["berth_id"], dict(raw))
    for berth_id, item in heads.items():
        if item["state"] == "typing" or berth_id in closing:
            continue  # one at a time; a queued close goes first
        row = db.get_berth(berth_id)
        if row is None:
            _settle(db, item, "failed", "the session was deleted", frm=("queued",), now=moment)
            outcomes.append({"id": item["id"], "result": "failed"})
            continue
        if row["state"] != "live":
            if row["state"] == "parked" and any(other["wake"] for other in waiting(db, berth_id)):
                _wake(db, cmux, row, item)
            continue
        programs = program_states(db.path.parent)
        reread = look_fn(row) if look_fn is not None else (lambda: send_look(cmux, db.require_berth(berth_id), programs=programs))
        try:
            text = typable(item, reread())
        except BerthError:
            continue
        if text is not None:
            state = type_one(db, cmux, row, item, reread=reread, now=moment, text=text)
            outcomes.append({"id": item["id"], "result": state})
    return outcomes


def _gone(db, berth_id: str) -> bool:
    """The session was forgotten, or is in the trash: what waits for it fails now instead of expiring later."""
    row = db.get_berth(berth_id)
    return row is None or row["state"] == "deleted"


_purged = [0.0]


def _closes(db) -> set[str]:
    from berth.deferred import pending

    return {item["berth_id"] for item in pending(db)}


def purge(db, *, now: datetime | None = None) -> int:
    cutoff = _stamp(_now(now) - timedelta(days=KEEP_DAYS))
    with db.immediate():
        return db.conn.execute("DELETE FROM delivery WHERE state NOT IN ('queued', 'typing') AND settled_at < ?",
                               (cutoff,)).rowcount
