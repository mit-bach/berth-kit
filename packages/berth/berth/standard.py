"""Every Claude session in auto mode at xhigh effort (2026-10-31).

Dominik asked for a system that checks every Claude session runs in auto mode,
never bypass, at extra-high effort. Settings Agent owns the standard and the
settings behind it (~/STUDIO/Harness/Coding-Agents/Settings-Agent/work/
2026-10-session-standard/PLAN.md); Berth watches and corrects, because its
watcher already reads every pane.

* **Mode**, live: the pane's status strip ("auto mode on", "bypass permissions
  on"), read each tick by ``berth/activity.py`` and kept in ``runtime.mode``.
* **Effort**: the ``effort`` on Claude's last reply in its transcript. An
  ``/effort xhigh`` Berth typed counts until the next reply says otherwise.
* **Settings**: ``effortLevel`` and each ``modelSettings.<model>.effortLevel``
  in ~/.claude/settings.json. One ``/effort`` pick lowers a model for every
  session, so a setting below xhigh is reported to Settings Agent, which resets it.

``berth check`` (and the watcher's drift) lists each session off the standard
(``off_standard``) and each setting below it (``settings_effort``).

Correction is on unless ``berth standard --correct off`` turned it off, and it
loses no work: Berth acts only on a session that is ready, holds no draft, is
not the workspace selected in cmux, and has written nothing to its transcript
for a minute. Bypass: the session is set to reopen in auto (``berth mode``)
and cycled through the idle queue (``berth/deferred.py``), at most once an hour.
Effort below xhigh: ``/effort xhigh`` goes into its mailbox
(``berth/delivery.py``), at most once an hour. Another mode (plan, accept edits)
is reported only: someone chose it.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

MODE = "auto"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
FLOOR = "xhigh"
SENDER = "berth-standard"
AGAIN_AFTER = 3600.0  # seconds before Berth corrects the same session the same way again
QUIET_FOR = 60.0  # seconds a transcript must be untouched before Berth acts on its session
TICK_EVERY = 10.0
CONFIG_TITLE = "Settings Agent"


def settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def switch_path(directory: Path) -> Path:
    return directory / "standard.json"


def below(effort: str | None) -> bool:
    return effort in EFFORTS and EFFORTS.index(effort) < EFFORTS.index(FLOOR)


def _read_switch(directory: Path) -> dict:
    try:
        data = json.loads(switch_path(directory).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def correcting(directory: Path) -> bool:
    return _read_switch(directory).get("correct", True) is not False


def set_correcting(directory: Path, on: bool) -> dict:
    data = _read_switch(directory)
    data["correct"] = bool(on)
    path = switch_path(directory)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return data


_effort_cache: dict[str, tuple[int, int, str | None, str | None]] = {}


def transcript_effort(path: Path | None) -> tuple[str | None, str | None]:
    """The effort on Claude's last reply in a transcript, and when. Reads only the file's tail."""
    if path is None:
        return None, None
    try:
        st = path.stat()
    except OSError:
        return None, None
    key = str(path)
    cached = _effort_cache.get(key)
    if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
        return cached[2], cached[3]
    found: tuple[str | None, str | None] = (None, None)
    for size in (65536, 1 << 20):
        try:
            with path.open("rb") as handle:
                handle.seek(max(0, st.st_size - size))
                lines = handle.read().decode("utf-8", errors="replace").splitlines()
        except OSError:
            break
        for line in reversed(lines):
            if '"effort"' not in line or '"assistant"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("type") == "assistant" and entry.get("effort"):
                found = (str(entry["effort"]), entry.get("timestamp"))
                break
        if found[0] is not None or size >= st.st_size:
            break
    _effort_cache[key] = (st.st_mtime_ns, st.st_size, found[0], found[1])
    return found


def settings_findings(path: Path | None = None) -> list[str]:
    """Each effort setting below the floor, as "<key> is <value>"."""
    try:
        data = json.loads((path or settings_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    found = []
    if below(data.get("effortLevel")):
        found.append(f"effortLevel is {data['effortLevel']}")
    for model, values in sorted((data.get("modelSettings") or {}).items()):
        if isinstance(values, dict) and below(values.get("effortLevel")):
            found.append(f"modelSettings.{model}.effortLevel is {values['effortLevel']}")
    return found


def _attachments(raw) -> dict:
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _when(stamp) -> float | None:
    if isinstance(stamp, (int, float)):
        return float(stamp)
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def sessions(db) -> list[dict]:
    """Every live Claude session with its mode and effort, and what is off the standard (``off``)."""
    from berth.identity import claude_transcript

    rows = db.conn.execute(
        "SELECT b.id, b.title, b.agent_session_id, b.cwd, b.attachments, b.cmux_workspace_id, "
        "r.mode AS mode, r.status AS status, r.draft AS draft FROM berth b LEFT JOIN runtime r ON r.berth_id = b.id "
        "WHERE b.state = 'live' AND b.kind != 'grok' AND lower(coalesce(b.agent_name, '')) = 'claude'").fetchall()
    out = []
    for row in rows:
        path = claude_transcript(row["agent_session_id"], row["cwd"] or "") if row["agent_session_id"] else None
        effort, at = transcript_effort(path)
        data = _attachments(row["attachments"])
        sent = _when(data.get("effort_sent_at"))
        if below(effort) and sent is not None and sent > (_when(at) or 0):
            effort = f"{FLOOR} (sent)"  # Berth typed /effort xhigh after that reply
        mode = row["mode"] or None
        off = []
        if mode and mode != MODE:
            off.append(f"mode {mode}")
        if below(effort):
            off.append(f"effort {effort}")
        try:
            quiet = time.time() - path.stat().st_mtime if path is not None else None
        except OSError:
            quiet = None
        out.append({"id": row["id"], "title": row["title"], "mode": mode, "effort": effort, "off": off,
                    "status": row["status"] or "", "draft": row["draft"] or "", "workspace": row["cmux_workspace_id"],
                    "quiet": quiet, "data": data})
    return out


def report(db, directory: Path) -> dict:
    """What ``berth standard`` prints: each session off the standard, the settings, and whether Berth corrects."""
    rows = sessions(db)
    return {"standard": {"mode": MODE, "effort": FLOOR}, "correcting": correcting(directory),
            "claude_sessions": len(rows),
            "off": [{key: item[key] for key in ("id", "title", "mode", "effort", "off")} for item in rows if item["off"]],
            "settings": settings_findings()}


_last_tick = [0.0]


def tick(db, cmux, directory: Path, *, now: float | None = None, selected: str | None = None, force: bool = False) -> list[dict]:
    """The watcher's job: correct what is off, at idle. Runs at most every ``TICK_EVERY`` seconds."""
    moment = time.time() if now is None else now
    if not force and moment - _last_tick[0] < TICK_EVERY:
        return []
    _last_tick[0] = moment
    if not correcting(directory):
        return []
    if selected is None:
        try:
            selected = next((item.id for item in cmux.list_workspaces() if item.selected), "") or ""
        except Exception:
            return []  # cmux cannot say what Dominik is looking at: act on nothing
    done = []
    for item in sessions(db):
        if not item["off"]:
            continue
        idle = (item["status"] == "ready" and not item["draft"] and item["quiet"] is not None
                and item["quiet"] >= QUIET_FOR and (item["workspace"] or "").lower() != selected.lower())
        if not idle:
            continue
        if item["mode"] == "bypassPermissions":
            done += _cycle_into_auto(db, item, moment)
        elif below(item["effort"]):
            done += _send_effort(db, item, moment)
    done += _report_settings(db, directory, moment)
    return done


def _note(db, berth_id: str, fields: dict, event: str, detail: dict) -> None:
    row = db.require_berth(berth_id)
    data = _attachments(row["attachments"])
    data.update(fields)
    with db.immediate():
        db.update_berth(berth_id, attachments=json.dumps(data, sort_keys=True))
        db.insert_event(berth_id, event, detail)


def _cycle_into_auto(db, item: dict, moment: float) -> list[dict]:
    from berth import deferred
    from berth.state import set_reopen_mode

    last = _when(item["data"].get("standard_cycled_at"))
    if last is not None and moment - last < AGAIN_AFTER:
        return []
    if deferred.pending(db, item["id"]):
        return []
    if item["data"].get("reopen_mode") != MODE:
        set_reopen_mode(db, item["id"], MODE)
    deferred.queue(db, item["id"], "cycle", idle=True, wait_minutes=30, requested_by=SENDER)
    _note(db, item["id"], {"standard_cycled_at": moment}, "standard", {"fix": "cycle into auto", "was": item["mode"]})
    return [{"id": item["id"], "title": item["title"], "fix": "cycle into auto"}]


def _send_effort(db, item: dict, moment: float) -> list[dict]:
    from berth import delivery

    last = _when(item["data"].get("effort_sent_at"))
    if last is not None and moment - last < AGAIN_AFTER:
        return []
    delivery.enqueue(db, item["id"], f"/effort {FLOOR}", sender=SENDER, key="effort", wait_minutes=60)
    _note(db, item["id"], {"effort_sent_at": moment}, "standard", {"fix": f"/effort {FLOOR}", "was": item["effort"]})
    return [{"id": item["id"], "title": item["title"], "fix": f"/effort {FLOOR}"}]


def _report_settings(db, directory: Path, moment: float) -> list[dict]:
    """A settings.json effort below xhigh goes to Settings Agent once, as one line in its mailbox."""
    from berth import delivery

    found = settings_findings()
    data = _read_switch(directory)
    if not found or found == data.get("reported"):
        return []
    row = db.conn.execute("SELECT id FROM berth WHERE state = 'live' AND title = ?", (CONFIG_TITLE,)).fetchone()
    if row is None:
        return []
    text = ("From Berth (the session standard): ~/.claude/settings.json has effort below xhigh: "
            + "; ".join(found) + ". Reset it to xhigh.")
    delivery.enqueue(db, row["id"], text, sender=SENDER, key="settings", wait_minutes=24 * 60)
    data["reported"] = found
    switch_path(directory).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return [{"id": row["id"], "title": CONFIG_TITLE, "fix": "reported settings"}]
