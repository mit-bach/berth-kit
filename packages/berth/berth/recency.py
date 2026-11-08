"""When you last used each session. The tree sorts by it.

A session's ``last_used_at`` only moves forward. Every watcher tick takes the
newest of what is known about it:

* ``played_at``: Berth played or focused it.
* cmux ``latest_submitted_at``: you sent something in that pane.
* the hook record's ``updatedAt``: the agent's last hook event.
* the agent's own files: the newest message in the Claude transcript, or
  Grok's ``last_active_at``. For Claude that includes a slash command you ran
  and a message you queued while it worked, its forks' transcripts (what you
  said to a fork, and the fork's work), and Claude's prompt history
  (``~/.claude/history.jsonl``: everything typed into its box).

2026-10-29: a prompt sent to Research Agent as ``/subtask`` (Claude runs it
in a fork) left it in place. The transcript recorded it only as a local
command, cmux saw no submitted message, and no hook fired, so none of the
signals above moved.

Live rows are checked every tick. A parked row's files are checked once per
watcher run, since nothing moves them while the session is parked.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from berth.db import Catalog
from berth.hooks import HookRecord, hook_for
from berth.model import Workspace
from berth.timeutil import parse_time


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parsed(value) -> datetime | None:
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(float(value), timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            return parse_time(value)
        except ValueError:
            return None
    return None


def claude_history_path():
    from pathlib import Path

    return Path.home() / ".claude" / "history.jsonl"


_history_caches: dict[str, list] = {}  # per history.jsonl: mtime_ns, size, {session id: newest prompt}


def prompted_at(session_id: str) -> datetime | None:
    """When something was last typed into this Claude session's box, from Claude's prompt history.

    Every Claude config folder keeps its own ``history.jsonl`` (one per account, berth/configdir.py).
    """
    from berth.configdir import extra_roots, unique

    paths = unique([claude_history_path()] + [root / "history.jsonl" for root in extra_roots()])
    found = [stamp for stamp in (_prompted_in(path, session_id) for path in paths) if stamp is not None]
    return max(found, default=None)


def _prompted_in(path, session_id: str) -> datetime | None:
    cache = _history_caches.setdefault(str(path), [None, None, {}])
    try:
        st = path.stat()
    except OSError:
        return None
    if cache[0] != st.st_mtime_ns or cache[1] != st.st_size:
        newest: dict[str, datetime] = {}
        with path.open("rb") as handle:
            handle.seek(max(0, st.st_size - _TAIL_BYTES))
            for raw in handle.read().splitlines():
                try:
                    item = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not isinstance(item, dict) or not isinstance(item.get("sessionId"), str):
                    continue
                stamp = item.get("timestamp")
                stamp = _parsed(stamp / 1000 if isinstance(stamp, (int, float)) else stamp)
                if stamp is not None and (item["sessionId"] not in newest or stamp > newest[item["sessionId"]]):
                    newest[item["sessionId"]] = stamp
        cache[:] = [st.st_mtime_ns, st.st_size, newest]
    return cache[2].get(session_id)


def forks_activity(transcript) -> datetime | None:
    """The newest message in the session's fork and subagent transcripts, next to its own."""
    newest = None
    try:
        files = list((transcript.with_suffix("") / "subagents").glob("agent-*.jsonl"))
    except OSError:
        return None
    for path in files:
        try:
            stamp = last_message_at(path)
        except OSError:
            continue
        if stamp is not None and (newest is None or stamp > newest):
            newest = stamp
    return newest


def agent_activity(row) -> datetime | None:
    """The agent's own record of its last activity, when it keeps one on disk."""
    session_id = row["agent_session_id"]
    if not session_id:
        return None
    from berth import identity

    try:
        if row["kind"] == "grok":
            path = identity.grok_summary_path(session_id, row["cwd"] or "")
            if path is None:
                return None
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            stamp = _parsed(data.get("last_active_at")) if isinstance(data, dict) else None
            return stamp or _parsed(path.stat().st_mtime)
        if (row["agent_name"] or "").casefold() == "claude":
            path = identity.claude_transcript(session_id, row["cwd"] or "")
            found = [prompted_at(session_id)]
            if path is not None:
                found += [last_message_at(path), forks_activity(path)]
            known = [item for item in found if item is not None]
            return max(known) if known else None
    except OSError:
        return None
    return None


_TAIL_BYTES = 512 * 1024
_message_cache: dict[str, tuple[int, int, datetime | None]] = {}


def _a_message(item) -> bool:
    kind = item.get("type")
    if kind in ("user", "assistant"):
        return True
    if kind == "system" and item.get("subtype") == "local_command":
        return True  # a slash command you ran: /subtask (a prompt run in a fork), /compact, /model
    attachment = item.get("attachment")
    # A message you sent while it was working, queued for its next step.
    return kind == "attachment" and isinstance(attachment, dict) and attachment.get("type") == "queued_command"


def last_message_at(path) -> datetime | None:
    """The newest message in a Claude transcript: user or assistant, a slash command, a queued message.

    Not the file's modification time: Claude rewrites transcripts in batches,
    which would make old sessions look just used. Only the tail is read, and
    the answer is cached until the file changes.
    """
    st = path.stat()
    key = str(path)
    cached = _message_cache.get(key)
    if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
        return cached[2]
    newest: datetime | None = None
    with path.open("rb") as handle:
        handle.seek(max(0, st.st_size - _TAIL_BYTES))
        for raw in handle.read().splitlines():
            if b'"timestamp"' not in raw:
                continue
            try:
                item = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(item, dict) or not _a_message(item):
                continue
            stamp = _parsed(item.get("timestamp"))
            if stamp is not None and (newest is None or stamp > newest):
                newest = stamp
    _message_cache[key] = (st.st_mtime_ns, st.st_size, newest)
    return newest


class Recency:
    def __init__(self) -> None:
        self.checked_parked: set[str] = set()

    def refresh(
        self,
        db: Catalog,
        workspaces: list[Workspace] | None,
        hooks: dict[str, HookRecord],
        *,
        rebuild: bool = False,
    ) -> list[str]:
        """Move ``last_used_at`` forward. ``rebuild`` recomputes it from the
        signals alone, so a value an earlier, wrong signal pushed too far comes back."""
        by_ws = {item.id.lower(): item for item in workspaces or []}
        changed: list[str] = []
        rows = db.conn.execute("SELECT * FROM berth").fetchall()
        updates: list[tuple[str, str, str]] = []
        for row in rows:
            live = row["state"] != "parked"
            if not live and row["id"] in self.checked_parked and not rebuild:
                continue
            candidates = [None if rebuild else _parsed(row["last_used_at"]), _parsed(row["played_at"])]
            if live:
                workspace = by_ws.get((row["cmux_workspace_id"] or "").lower())
                if workspace is not None:
                    candidates.append(_parsed(workspace.submitted_at))
                hook = hook_for(hooks, row["cmux_workspace_id"])
                if hook is not None and hook.session_id == row["agent_session_id"]:
                    candidates.append(_parsed(hook.updated_at))
            else:
                self.checked_parked.add(row["id"])
                candidates.append(_parsed(row["parked_at"]))
            candidates.append(agent_activity(row))
            known = [item for item in candidates if item is not None]
            if not known:
                continue
            newest = iso(max(known))
            current = _parsed(row["last_used_at"])
            if current is not None and (iso(current) == newest or (not rebuild and iso(current) > newest)):
                continue
            updates.append((row["id"], newest, row["updated_at"]))
        if updates:
            with db.immediate():
                for berth_id, stamp, updated in updates:
                    # Not an edit of the row: updated_at stays, so stale-play timing is untouched.
                    db.update_berth(berth_id, last_used_at=stamp, updated_at=updated)
                    changed.append(berth_id)
        return changed
