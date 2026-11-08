"""Where you came from when you switch to the Berth screen.

cmux's ``workspace.selected`` event names the workspace you left
(``previous_workspace_id``). The watcher writes the last selection to
``selected.json`` beside the catalog. When that selection is the screen's
own workspace, the screen puts its cursor on the session you came from, so
⌘⇧B from a session lands on that session's row (2026-10-28). cmux does not
say how you switched, so a click on Berth in the sidebar does the same.

A selection counts once, and only while it is fresh: the watcher replays
retained events after a restart, and an old switch must not move the cursor.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from berth.timeutil import parse_time, utc_now

FRESH_SECONDS = 10.0


def selected_path(directory: Path) -> Path:
    return Path(directory) / "selected.json"


def record(directory: Path, event: dict) -> dict | None:
    """Keep the last workspace selection. Called by the watcher for each ``workspace.selected``."""
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    workspace = payload.get("workspace_id") or event.get("workspace_id")
    if not isinstance(workspace, str) or not workspace:
        return None
    data = {
        "id": str(event.get("id") or event.get("seq") or ""),
        "at": event.get("occurred_at") or utc_now(),
        "workspace_id": workspace,
        "previous_workspace_id": payload.get("previous_workspace_id") or None,
    }
    path = selected_path(directory)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".selected.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return None
    return data


def arrived_from(directory: Path, own_workspace: str, now: datetime, seen: str | None) -> tuple[str, str | None] | None:
    """``(selection id, workspace you came from)`` when this screen was just switched to, else None.

    The id is returned even when the previous workspace is unknown, so the
    caller can mark the selection as handled.
    """
    if not own_workspace:
        return None
    try:
        data = json.loads(selected_path(directory).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("id") or data.get("id") == seen:
        return None
    if str(data.get("workspace_id") or "").lower() != own_workspace.lower():
        return None
    try:
        age = (now - parse_time(str(data.get("at") or ""))).total_seconds()
    except (TypeError, ValueError):
        return None
    if age > FRESH_SECONDS:
        return None
    previous = data.get("previous_workspace_id")
    return str(data["id"]), previous if isinstance(previous, str) and previous else None
