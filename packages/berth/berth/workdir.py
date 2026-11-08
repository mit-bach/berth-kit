"""A session's working directory (its ``cwd``), set on purpose (2026-10-29).

Two different things, never called by each other's names:

* a **Berth folder** is the catalog grouping (``update {folder}``, the
  ``folder`` tool). It is Berth's own and never touches the disk;
* a **working directory** (``cwd``) is the directory on disk a session runs
  in: where Claude reads its ``CLAUDE.md`` chain, auto-memory, project
  settings, and folder trust.

They are independent: moving a session to another Berth folder leaves its
directory alone, and changing its directory leaves its Berth folder alone.

Before this, nothing could set a row's directory: every Claude play copied
the transcript's last recorded ``cwd`` onto the row, and Claude records a new
``cwd`` only under the directory a session started in. Now ``set_cwd`` writes
the row's ``cwd`` and keeps the choice in ``attachments.cwd_set``:

* play opens the session there (``-r`` on the same conversation), and does not
  overwrite the choice from the transcript (``chosen``) until Claude has
  recorded a turn there (``confirm_from_transcript`` then forgets it);
* a live session keeps running where it is until its next open (wake or
  cycle); ``opened`` marks that it has opened there.

Tested by script on 2026-10-29 (``tests/test_workdir.py`` holds the cases):

* Claude: ``claude -r <id>`` from another directory resumes the same
  conversation; its transcript stays in the old project directory and its
  new entries record the new ``cwd``. ``claude -r <id> --fork-session`` from
  another directory writes the fork's transcript under that directory's
  project, every entry recording it.
* Grok: ``grok -r <id>`` resumes from anywhere, but keeps working in the
  directory the session started in (``pwd`` says so), with or without
  ``--cwd``; a fork lands in the parent's directory too. Moving the closed
  session's folder from ``~/.grok/sessions/<old>/<id>`` to
  ``~/.grok/sessions/<new>/<id>`` made it work in the new directory, but that
  rewrites Grok's own store, so Berth refuses instead (``GROK_REFUSAL``).
"""

from __future__ import annotations

import json
import os

from berth.errors import BerthError
from berth.timeutil import utc_now

GROK_REFUSAL = (
    "A Grok session keeps working in the directory it started in: grok -r resumes it from anywhere but runs in "
    "its own directory (tested 2026-10-29). To work somewhere else, start a new Grok session in that directory "
    "and hand the work over. Relocating Grok's session files under ~/.grok/sessions would make a move possible; "
    "Berth does not do that."
)


def check_directory(path: object) -> str:
    """An absolute path to a directory that exists, normalized; else BAD_ARGUMENT."""
    from berth.place import normalize_cwd

    text = os.path.expanduser(str(path or "").strip())
    if not text or not os.path.isabs(text):
        raise BerthError("BAD_ARGUMENT", "cwd is an absolute path to a directory", {"cwd": path})
    if not os.path.isdir(text):
        raise BerthError("BAD_ARGUMENT", f"no directory {text}", {"cwd": text})
    return normalize_cwd(os.path.realpath(text) if os.path.islink(text) else text)


def unmovable(row) -> str | None:
    """Why this session's working directory cannot be changed, or None when it can."""
    from berth.observer import is_screen

    if is_screen(row):
        return "the Berth screen is not a session"
    kind = row["kind"] or ""
    if kind == "grok":
        return GROK_REFUSAL
    if kind == "other-agent" and (row["agent_name"] or "").casefold() != "claude":
        return f"changing the working directory of a {row['agent_name']} session is not tested; only Claude, shells, and processes"
    return None


def _attachments(row) -> dict:
    try:
        data = json.loads(row["attachments"] or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def chosen(row) -> str | None:
    """The working directory set on purpose and not yet confirmed by the agent, if any."""
    item = _attachments(row).get("cwd_set")
    if isinstance(item, dict) and isinstance(item.get("cwd"), str) and item["cwd"] == row["cwd"]:
        return item["cwd"]
    return None


def set_cwd(db, berth_id: str, path: object, *, by: str = "") -> dict:
    """Set a session's working directory. Returns what changed. Its Berth folder is not touched."""
    from berth.db import public_berth

    row = db.require_berth(berth_id)
    reason = unmovable(row)
    if reason:
        raise BerthError("KIND_UNSAFE", f"{row['title']}: {reason}", {"berth_id": berth_id, "kind": row["kind"]})
    cwd = check_directory(path)
    before = row["cwd"] or ""
    if cwd == before and chosen(row) is None:
        return {**public_berth(row), "cwd_changed": False}
    data = _attachments(row)
    data["cwd_set"] = {"cwd": cwd, "was": before, "at": utc_now(), "opened": False, "by": by}
    with db.immediate():
        db.update_berth(berth_id, cwd=cwd, attachments=json.dumps(data, sort_keys=True), updated_at=utc_now())
        db.insert_event(berth_id, "cwd_set", {"from": before, "to": cwd, "by": by})
    return {**public_berth(db.require_berth(berth_id)), "cwd_changed": True, "cwd_was": before}


def mark_opened(db, berth_id: str) -> None:
    """Play opened the session in its chosen directory. A shell or process needs nothing more."""
    row = db.get_berth(berth_id)
    if row is None or chosen(row) is None:
        return
    data = _attachments(row)
    if row["kind"] in ("shell", "process"):
        data.pop("cwd_set", None)
    else:
        data["cwd_set"]["opened"] = True
    with db.immediate():
        db.update_berth(berth_id, attachments=json.dumps(data, sort_keys=True))


def confirm_from_transcript(db, row, transcript_cwd: str | None) -> bool:
    """Claude recorded a turn in the chosen directory: forget the choice; the transcript speaks for it now."""
    target = chosen(row)
    if not target or not transcript_cwd or transcript_cwd.rstrip("/") != target.rstrip("/"):
        return False
    data = _attachments(row)
    data.pop("cwd_set", None)
    with db.immediate():
        db.update_berth(row["id"], attachments=json.dumps(data, sort_keys=True))
        db.insert_event(row["id"], "cwd_confirmed", {"cwd": target})
    return True


def pending_note(row) -> str | None:
    """What a result says about a directory not in use yet."""
    target = chosen(row)
    if target is None:
        return None
    opened = bool((_attachments(row).get("cwd_set") or {}).get("opened"))
    if row["state"] == "live" and not opened:
        return "set; it takes effect at the session's next open (wake, or cycle it now)"
    if row["state"] != "live":
        return "set; the session opens there when it is woken"
    return "open there; Claude records it with the next turn"
