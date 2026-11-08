"""Read agent hook files. Session ids come from these files, not from a scan of session transcripts.

Grok ids are read from ``~/.cmuxterm/grok-hook-sessions.json``. Other agents
are read from the sibling ``*-hook-sessions.json`` files. Claude is
``claude-hook-sessions.json``. The last body is used only to decide busy
versus quiet. It is not written into the catalog.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from berth.model import HookRecord

GROK_HOOK = "grok-hook-sessions.json"


def hook_dir() -> Path:
    override = os.environ.get("BERTH_HOOK_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cmuxterm"


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _arguments(launch: object) -> tuple[list[str], str | None, str | None]:
    if not isinstance(launch, dict):
        return [], None, None
    raw = launch.get("arguments") or []
    arguments = [item for item in raw if isinstance(item, str)]
    executable = launch.get("executablePath") or launch.get("executable_path")
    launcher = launch.get("launcher")
    if not isinstance(executable, str):
        executable = None
    if not isinstance(launcher, str):
        launcher = None
    return arguments, executable, launcher


def _record_from(agent_name: str, payload: dict) -> HookRecord | None:
    workspace_id = payload.get("workspaceId") or payload.get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id:
        return None
    session_id = payload.get("sessionId") or payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        session_id = None
    surface_id = payload.get("surfaceId") or payload.get("surface_id")
    if not isinstance(surface_id, str):
        surface_id = None
    arguments, executable, launcher = _arguments(payload.get("launchCommand") or payload.get("launch_command"))
    cwd = payload.get("cwd")
    if not isinstance(cwd, str):
        working = None
        launch = payload.get("launchCommand")
        if isinstance(launch, dict):
            working = launch.get("workingDirectory") or launch.get("working_directory")
        cwd = working if isinstance(working, str) else None
    updated = payload.get("updatedAt") or payload.get("updated_at")
    updated_at = float(updated) if isinstance(updated, (int, float)) else None
    last_body = payload.get("lastBody") if isinstance(payload.get("lastBody"), str) else None
    status = payload.get("runtimeStatus") or payload.get("runtime_status")
    if not isinstance(status, str):
        lifecycle = payload.get("agentLifecycle") or payload.get("agent_lifecycle")
        status = lifecycle if isinstance(lifecycle, str) else None
    event = payload.get("hookEventName") or payload.get("hook_event_name")
    if not isinstance(event, str):
        event = None
    pending = payload.get("hadPendingBackgroundWorkAtStop")
    return HookRecord(
        agent_name=agent_name,
        session_id=session_id,
        workspace_id=workspace_id,
        surface_id=surface_id,
        pid=_as_int(payload.get("pid")),
        cwd=cwd,
        runtime_status=status,
        last_body=last_body,
        executable=executable,
        arguments=arguments,
        launcher=launcher,
        updated_at=updated_at,
        hook_event=event,
        pending_background=pending is True,
    )


def _sessions(document: object) -> list[dict]:
    if isinstance(document, dict):
        sessions = document.get("sessions")
        if isinstance(sessions, dict):
            return [value for value in sessions.values() if isinstance(value, dict)]
        if isinstance(sessions, list):
            return [value for value in sessions if isinstance(value, dict)]
    if isinstance(document, list):
        return [value for value in document if isinstance(value, dict)]
    return []


def load_hook_file(path: Path, agent_name: str) -> list[HookRecord]:
    if not path.is_file():
        return []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    records = []
    for payload in _sessions(document):
        record = _record_from(agent_name, payload)
        if record is not None:
            records.append(record)
    return records


def agent_name_from_filename(name: str) -> str:
    suffix = "-hook-sessions.json"
    if name.endswith(suffix):
        return name[: -len(suffix)]
    return name


def _prefer(current: HookRecord, candidate: HookRecord) -> HookRecord:
    """Pick the hook that names the process actually in the workspace.

    A live PID beats a dead one, so a finished Grok record does not hide the
    Claude process that replaced it. When both are live or both are dead, the
    newer stamp wins. Grok wins only a tie.
    """
    if candidate.agent_name == current.agent_name:
        if (candidate.updated_at or 0) >= (current.updated_at or 0):
            return candidate
        return current
    from berth.place import pid_alive

    candidate_alive = pid_alive(candidate.pid)
    current_alive = pid_alive(current.pid)
    if candidate_alive != current_alive:
        return candidate if candidate_alive else current
    candidate_stamp = candidate.updated_at or 0
    current_stamp = current.updated_at or 0
    if candidate_stamp != current_stamp:
        return candidate if candidate_stamp > current_stamp else current
    if candidate.agent_name == "grok":
        return candidate
    return current


def load_hooks(directory: Path | None = None) -> dict[str, HookRecord]:
    """Map a lowercased workspace UUID to the hook record for the live agent.

    Two files can name one workspace. The record whose PID is alive wins.
    A dead Grok session left on that workspace does not beat a live Claude
    session. Grok wins only when the stamps and the PIDs tie.
    """
    root = directory or hook_dir()
    if not root.is_dir():
        return {}
    chosen: dict[str, HookRecord] = {}
    paths = sorted(root.glob("*-hook-sessions.json"))
    paths.sort(key=lambda path: (path.name == GROK_HOOK, path.name))
    for path in paths:
        agent = agent_name_from_filename(path.name)
        for record in load_hook_file(path, agent):
            key = record.workspace_id.lower()
            previous = chosen.get(key)
            if previous is None:
                chosen[key] = record
                continue
            chosen[key] = _prefer(previous, record)
    return chosen


def load_hook_records(directory: Path | None = None) -> list[HookRecord]:
    """Every hook record, including more than one for the same workspace."""
    root = directory or hook_dir()
    if not root.is_dir():
        return []
    records: list[HookRecord] = []
    for path in sorted(root.glob("*-hook-sessions.json")):
        records.extend(load_hook_file(path, agent_name_from_filename(path.name)))
    return records


def hook_for(hooks: dict[str, HookRecord], workspace_id: str | None) -> HookRecord | None:
    if not workspace_id:
        return None
    return hooks.get(workspace_id.lower())
