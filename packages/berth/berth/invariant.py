"""The catalog and cmux agree, or this says where they do not.

docs/BERTH-STATE-MACHINE.md lists the rules. ``check`` is pure: it reads the
catalog and one ``workspace list`` and returns the violations. ``Monitor`` is
the watcher's view: a violation must be seen on two ticks in a row before it
is reported, so a rename in flight is not drift, and only a change in the
reported set is written as an event.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from berth.db import Catalog
from berth.model import Workspace
from berth.timeutil import STALE_PLAY_SECONDS, age_seconds, utc_now

LINKED = ("live", "playing", "pausing")


@dataclass(frozen=True)
class Violation:
    rule: str
    message: str
    berth_id: str | None = None
    workspace_id: str | None = None

    @property
    def key(self) -> tuple:
        return (self.rule, self.berth_id or "", (self.workspace_id or "").lower())


def check(db: Catalog, workspaces: list[Workspace], *, now: str | None = None) -> list[Violation]:
    stamp = now or utc_now()
    rows = list(db.conn.execute("SELECT * FROM berth"))
    open_ws = {item.id.lower(): item for item in workspaces}
    found: list[Violation] = []

    by_ws: dict[str, list] = {}
    for row in rows:
        if row["state"] in LINKED and row["cmux_workspace_id"]:
            by_ws.setdefault(row["cmux_workspace_id"].lower(), []).append(row)

    for key, workspace in open_ws.items():
        holders = by_ws.get(key, [])
        if not holders:
            found.append(
                Violation("open_unlinked", f"cmux workspace {workspace.title!r} has no live row", None, workspace.id)
            )
        elif len(holders) > 1:
            names = ", ".join(row["title"] for row in holders)
            found.append(
                Violation("double_link", f"{workspace.title!r} is linked by {len(holders)} rows: {names}", None, workspace.id)
            )

    sessions: dict[str, list] = {}
    for row in rows:
        state = row["state"]
        workspace_id = row["cmux_workspace_id"]
        if state == "live":
            workspace = open_ws.get((workspace_id or "").lower())
            if workspace is None:
                found.append(
                    Violation("live_missing", f"{row['title']!r} is live but its workspace is gone", row["id"], workspace_id)
                )
            elif _norm(workspace.title) != _norm(row["title"]):
                found.append(
                    Violation(
                        "name_mismatch",
                        f"Berth {row['title']!r} vs cmux {workspace.title!r}",
                        row["id"],
                        workspace_id,
                    )
                )
        if state == "parked" and workspace_id:
            found.append(
                Violation("parked_linked", f"{row['title']!r} is parked but still names a workspace", row["id"], workspace_id)
            )
        if state in ("playing", "pausing"):
            try:
                age = age_seconds(row["updated_at"], _parse(stamp))
            except ValueError:
                age = 0
            if age >= STALE_PLAY_SECONDS:
                found.append(Violation("stuck", f"{row['title']!r} has been {state} for {int(age)}s", row["id"], workspace_id))
        if state in LINKED and row["agent_session_id"]:
            sessions.setdefault(row["agent_session_id"].lower(), []).append(row)

    found += _two_screens(rows, open_ws)
    found += _session_names(rows, open_ws)
    found += _standard(db)
    found += _dead_claude(rows)
    found += _background_jobs(rows)

    for session_id, holders in sessions.items():
        if len(holders) > 1:
            names = ", ".join(row["title"] for row in holders)
            found.append(Violation("session_twice", f"session {session_id[:8]} is live on {names}", holders[0]["id"]))

    return found


def _two_screens(rows, open_ws: dict) -> list[Violation]:
    """At most one Berth screen, in the one workspace named Berth (berth/screens.py, 2026-10-30)."""
    from berth.screens import HOME_TITLE, live_screens

    found = []
    screens = live_screens(rows)
    if len(screens) > 1:
        extra = next((row for row in screens if row["title"] != HOME_TITLE), screens[-1])
        names = ", ".join(repr(row["title"]) for row in screens)
        found.append(Violation(
            "two_screens",
            f"{len(screens)} Berth screens are open ({names}); only one may be. Delete the extra one: d then ! on its "
            f"row in the screen, or the MCP's delete with force", extra["id"], extra["cmux_workspace_id"]))
    homes = [workspace for workspace in open_ws.values() if workspace.title == HOME_TITLE]
    if len(homes) > 1:
        found.append(Violation(
            "two_screens", f"{len(homes)} cmux workspaces are named {HOME_TITLE!r}; ⌘⇧B reaches only one. Delete or "
                           f"rename the extra one", None, homes[-1].id))
    return found


def _session_names(rows, open_ws: dict) -> list[Violation]:
    """The third copy of a name: the agent's own session name (2026-10-31).

    ``check`` compares Berth's title with cmux's above; this compares it with what the agent calls the session.
    Claude's name is its last custom-title, the name ListAgents and SendMessage use. Its generated ai-title is
    not a name (2026-10-30, "tooling-1e"), so a Claude conversation with only an ai-title has no name. Other
    agents count only a name a person gave. The watcher repairs a difference by typing ``/rename`` into the pane
    once it is idle (berth/identity.py); until it has, this reports it, and says when the rename is waiting.
    """
    from berth.identity import RENAME_ON_OPEN, accepts_session_rename, claude_transcript, current_session_title

    found = []
    for row in rows:
        workspace_id = row["cmux_workspace_id"] or ""
        if row["state"] != "live" or not row["agent_session_id"] or workspace_id.lower() not in open_ws:
            continue
        if not accepts_session_rename(row["kind"], row["agent_name"]):
            continue
        claude = row["kind"] != "grok" and (row["agent_name"] or "").casefold() == "claude"
        cwd = row["cwd"] or open_ws[workspace_id.lower()].cwd or ""
        try:
            if claude and claude_transcript(row["agent_session_id"], cwd) is None:
                continue  # no conversation on disk yet: nothing to compare
            name, manual = current_session_title(row["kind"], row["agent_name"], row["agent_session_id"], cwd)
        except Exception:
            continue
        if not manual:
            if not claude:
                continue
            name = None
        if name is not None and _norm(name) == _norm(row["title"]):
            continue
        try:
            data = json.loads(row["attachments"] or "{}")
        except json.JSONDecodeError:
            data = {}
        data = data if isinstance(data, dict) else {}
        waiting = bool(data.get(RENAME_ON_OPEN)) or (
            _norm(data.get("name_lock")) == _norm(row["title"]) and _norm(data.get("name_sent")) != _norm(row["title"]))
        agent = "Claude" if claude else ("Grok" if row["kind"] == "grok" else (row["agent_name"] or "the agent"))
        said = f"{agent} calls it {name!r}" if name else f"{agent} has no name for it"
        tail = "; /rename waits for the pane to be idle" if waiting else ""
        found.append(Violation("session_name", f"Berth {row['title']!r} vs {said}{tail}", row["id"], workspace_id))
    return found


def _standard(db) -> list[Violation]:
    """Every Claude session in auto mode at xhigh effort, and no effort setting below it (berth/standard.py)."""
    from berth import standard

    found = []
    try:
        rows = standard.sessions(db)
    except Exception:
        return []
    fixing = standard.correcting(db.path.parent)
    for item in rows:
        if item["off"]:
            fixable = item["mode"] == "bypassPermissions" or standard.below(item["effort"])
            tail = "; Berth fixes it when the session is idle" if fixing and fixable else ""
            found.append(Violation("off_standard", f"{item['title']}: {', '.join(item['off'])}{tail}",
                                   item["id"], item["workspace"]))
    for finding in standard.settings_findings():
        found.append(Violation("settings_effort", f"~/.claude/settings.json {finding} (below xhigh)"))
    return found


def _background_jobs(rows) -> list[Violation]:
    """Berth keeps no Claude background agents: each conversation lives in a pane."""
    from berth import claude

    if not claude.sessions_dir().is_dir():
        return []
    titles = {row["agent_session_id"]: (row["id"], row["title"]) for row in rows if row["agent_session_id"]}
    found = []
    for job in claude.background_jobs(claude.processes()):
        berth_id, title = titles.get(job.session_id, (None, job.name))
        found.append(
            Violation(
                "background_job",
                f"{title!r} is running as a Claude background agent; enter on it in Berth moves it to a pane",
                berth_id,
            )
        )
    return found


def _dead_claude(rows) -> list[Violation]:
    """A parked Claude row must name a conversation Claude can resume or attach to."""
    from berth import claude

    if not claude.sessions_dir().is_dir():
        return []
    running = claude.processes()
    found = []
    for row in rows:
        if row["state"] != "parked" or (row["agent_name"] or "").casefold() != "claude":
            continue
        try:
            attachments = json.loads(row["attachments"] or "{}")
        except json.JSONDecodeError:
            attachments = {}
        parent = attachments.get("fork_of")
        if parent and claude.resumable(parent, row["cwd"] or ""):
            continue  # play forks the parent again under the row's id
        if claude.running_job(row["agent_session_id"], running) or claude.resumable(row["agent_session_id"], row["cwd"] or ""):
            continue
        found.append(
            Violation(
                "dead_session",
                f"{row['title']!r}: Claude has no conversation {str(row['agent_session_id'])[:8]} to resume",
                row["id"],
            )
        )
    return found


def _parse(stamp: str):
    from berth.timeutil import parse_time

    return parse_time(stamp)


def _norm(value: str | None) -> str:
    return " ".join((value or "").split())


def report(violations: list[Violation], *, cmux_up: bool = True) -> dict:
    return {
        "at": utc_now(),
        "cmux": "up" if cmux_up else "down",
        "ok": cmux_up and not violations,
        "violations": [asdict(item) for item in violations],
    }


def run(db, cmux) -> dict:
    """Read-only. The violations against a fresh workspace list (``berth check``, MCP ``check``)."""
    from berth.errors import BerthError

    if not cmux.socket_up():
        return report([], cmux_up=False)
    try:
        workspaces = cmux.list_workspaces()
    except BerthError:
        return report([], cmux_up=False)
    document = report(check(db, workspaces))
    document["workspaces"] = len(workspaces)
    document["live"] = db.conn.execute("SELECT COUNT(*) FROM berth WHERE state = 'live'").fetchone()[0]
    return document


def state_path(directory: Path) -> Path:
    return directory / "invariant.json"


def write_state(directory: Path, document: dict) -> None:
    path = state_path(directory)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)


def read_state(directory: Path) -> dict | None:
    try:
        return json.loads(state_path(directory).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


class Monitor:
    """Two ticks before a violation is reported. One event per change."""

    def __init__(self) -> None:
        self._seen: set[tuple] = set()
        self._reported: set[tuple] = set()
        self._cmux_up: bool | None = None

    def observe(self, db: Catalog, workspaces: list[Workspace] | None, directory: Path) -> dict:
        if workspaces is None:
            if self._cmux_up is not False:
                with db.immediate():
                    db.insert_event(None, "invariant", {"cmux": "down"})
                self._cmux_up = False
                self._seen = set()
                self._reported = set()
            document = report([], cmux_up=False)
            write_state(directory, document)
            return document
        self._cmux_up = True
        current = check(db, workspaces)
        keys = {item.key for item in current}
        confirmed = [item for item in current if item.key in self._seen]
        self._seen = keys
        confirmed_keys = {item.key for item in confirmed}
        if confirmed_keys != self._reported:
            with db.immediate():
                db.insert_event(
                    None,
                    "invariant",
                    {
                        "ok": not confirmed,
                        "violations": [asdict(item) for item in confirmed],
                    },
                )
            self._reported = confirmed_keys
        document = report(confirmed)
        write_state(directory, document)
        return document
