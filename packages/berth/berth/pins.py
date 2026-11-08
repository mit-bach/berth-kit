"""Pinned sessions: the one mark a session carries (2026-10-28), Berth's alone (2026-10-31).

A pinned session stays where it is in the Berth tree, marked 📌, and comes
first in the cmux sidebar: Berth's sidebar order puts it there
(``berth/sidebar.py``), and Berth's sidebar draws it under "Pinned"
(``cmux-sidebar/berth.js``, from the pin each row's status carries).

The pin lives only in the catalog. Until 2026-10-31 it was also cmux's own
workspace pin, kept in step both ways, and cmux keeps pinned workspaces
ahead of unpinned ones and orders only inside each group, so its rule, not
Berth's, decided parts of the order. Dominik's decision: Berth stops using
cmux's pins. Berth never pins a workspace in cmux now. A pin made in cmux
(the sidebar's Pin and Unpin, or cmux's own menu) is a request: Berth turns
its own pin over for that session, then clears cmux's pin. Pin in Berth with
P, ``berth pin``, or the MCP's ``update``.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from berth.errors import BerthError
from berth.timeutil import utc_now

PIN_MARK = "📌"


def _attachments(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def set_pinned(db, berth_id: str, pinned: bool) -> dict:
    """Pin or unpin one session in the catalog. The watcher carries it to cmux."""
    from berth.db import public_berth

    row = db.require_berth(berth_id)
    if bool(row["pinned"]) != bool(pinned):
        with db.immediate():
            db.update_berth(berth_id, pinned=1 if pinned else 0, updated_at=utc_now())
            db.insert_event(berth_id, "pin" if pinned else "unpin", {"via": "berth"})
    return public_berth(db.require_berth(berth_id))


def toggle_pinned(path: Path | str, berth_id: str, when: datetime | None = None) -> bool:
    """The screen's P: flip the pin. Returns the new pin."""
    from berth.db import Catalog

    db = Catalog(Path(path)).open()
    try:
        row = db.get_berth(berth_id)
        if row is None:
            raise BerthError("NOT_FOUND", "no session", {"berth_id": berth_id})
        pinned = not bool(row["pinned"])
        set_pinned(db, berth_id, pinned)
        return pinned
    finally:
        db.close()


def sync(db, cmux, workspaces) -> list[str]:
    """Clear cmux's pins, reading each new one as a request to turn Berth's pin over. Returns the rows it changed.

    ``pin_seen`` in the row's attachments is the cmux pin Berth last saw for which workspace, so a cmux pin
    that was already there (the pins kept in step before 2026-10-31) is only cleared, never read as a request.
    """
    if workspaces is None:
        return []
    by_id = {item.id.lower(): item for item in workspaces}
    changed: list[str] = []
    rows = db.conn.execute("SELECT * FROM berth WHERE state = 'live' AND cmux_workspace_id IS NOT NULL").fetchall()
    for row in rows:
        workspace = by_id.get(row["cmux_workspace_id"].lower())
        if workspace is None or not workspace.pinned:
            continue
        data = _attachments(row["attachments"])
        seen = data.get("pin_seen") if isinstance(data.get("pin_seen"), dict) else {}
        already = (seen.get("workspace") or "").lower() == workspace.id.lower() and bool(seen.get("pinned"))
        try:
            cmux.pin_workspace(workspace.id, False)
        except BerthError:
            continue
        fields: dict = {}
        if not already:
            # A pin made in cmux: turn Berth's pin over (Pin on an unpinned row pins it; on a pinned one, unpins).
            fields["pinned"] = 0 if row["pinned"] else 1
        data["pin_seen"] = {"workspace": workspace.id, "pinned": False}
        fields["attachments"] = json.dumps(data, sort_keys=True)
        with db.immediate():
            db.update_berth(row["id"], **fields)
            if "pinned" in fields:
                db.insert_event(row["id"], "pin" if fields["pinned"] else "unpin", {"via": "cmux"})
        changed.append(row["id"])
    return changed
