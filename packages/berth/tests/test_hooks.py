"""Hook files. A live Claude record beats a dead Grok record on the same workspace."""

from __future__ import annotations

import json
import os

from berth.hooks import load_hooks


def _write(directory, name: str, records: list[dict]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    sessions = {item["sessionId"]: item for item in records}
    (directory / name).write_text(json.dumps({"sessions": sessions}), encoding="utf-8")


def _record(*, session_id: str, workspace_id: str, pid: int, updated_at: float, executable: str, launcher: str) -> dict:
    return {
        "sessionId": session_id,
        "workspaceId": workspace_id,
        "pid": pid,
        "updatedAt": updated_at,
        "cwd": "/Users/someone",
        "launchCommand": {
            "arguments": [executable] if executable else [],
            "executablePath": executable,
            "launcher": launcher,
        },
    }


def test_a_live_claude_record_beats_a_newer_dead_grok_record(tmp_path, monkeypatch):
    workspace_id = "A315276F-550B-4677-A12A-734233505A99"
    _write(
        tmp_path,
        "claude-hook-sessions.json",
        [
            _record(
                session_id="claude-sess",
                workspace_id=workspace_id,
                pid=50,
                updated_at=10,
                executable="/Users/someone/.local/bin/claude",
                launcher="claude",
            )
        ],
    )
    _write(
        tmp_path,
        "grok-hook-sessions.json",
        [
            _record(
                session_id="grok-sess",
                workspace_id=workspace_id,
                pid=40,
                updated_at=99,
                executable="",
                launcher="",
            )
        ],
    )

    def alive(pid: int | None) -> bool:
        return pid == 50

    monkeypatch.setattr("berth.place.pid_alive", alive)
    chosen = load_hooks(tmp_path)
    hook = chosen[workspace_id.lower()]
    assert hook.agent_name == "claude"
    assert hook.session_id == "claude-sess"


def test_a_newer_grok_record_wins_when_both_processes_are_dead(tmp_path, monkeypatch):
    workspace_id = "ws-dead"
    _write(
        tmp_path,
        "claude-hook-sessions.json",
        [
            _record(
                session_id="claude-sess",
                workspace_id=workspace_id,
                pid=1,
                updated_at=10,
                executable="/Users/someone/.local/bin/claude",
                launcher="claude",
            )
        ],
    )
    _write(
        tmp_path,
        "grok-hook-sessions.json",
        [
            _record(
                session_id="grok-sess",
                workspace_id=workspace_id,
                pid=2,
                updated_at=20,
                executable="/Users/someone/.grok/bin/grok",
                launcher="grok",
            )
        ],
    )
    monkeypatch.setattr("berth.place.pid_alive", lambda pid: False)
    hook = load_hooks(tmp_path)[workspace_id]
    assert hook.agent_name == "grok"
    assert hook.session_id == "grok-sess"


def test_a_newer_claude_record_wins_when_both_processes_are_dead(tmp_path, monkeypatch):
    workspace_id = "ws-quiet"
    _write(
        tmp_path,
        "claude-hook-sessions.json",
        [
            _record(
                session_id="claude-sess",
                workspace_id=workspace_id,
                pid=1,
                updated_at=30,
                executable="/Users/someone/.local/bin/claude",
                launcher="claude",
            )
        ],
    )
    _write(
        tmp_path,
        "grok-hook-sessions.json",
        [
            _record(
                session_id="grok-sess",
                workspace_id=workspace_id,
                pid=2,
                updated_at=10,
                executable="/Users/someone/.grok/bin/grok",
                launcher="grok",
            )
        ],
    )
    monkeypatch.setattr("berth.place.pid_alive", lambda pid: False)
    hook = load_hooks(tmp_path)[workspace_id]
    assert hook.agent_name == "claude"
    assert hook.session_id == "claude-sess"


def test_grok_wins_a_tie(tmp_path, monkeypatch):
    workspace_id = "ws-tie"
    _write(
        tmp_path,
        "claude-hook-sessions.json",
        [
            _record(
                session_id="claude-sess",
                workspace_id=workspace_id,
                pid=1,
                updated_at=10,
                executable="/Users/someone/.local/bin/claude",
                launcher="claude",
            )
        ],
    )
    _write(
        tmp_path,
        "grok-hook-sessions.json",
        [
            _record(
                session_id="grok-sess",
                workspace_id=workspace_id,
                pid=2,
                updated_at=10,
                executable="/Users/someone/.grok/bin/grok",
                launcher="grok",
            )
        ],
    )
    monkeypatch.setattr("berth.place.pid_alive", lambda pid: False)
    hook = load_hooks(tmp_path)[workspace_id]
    assert hook.agent_name == "grok"
    assert os.path.basename(hook.executable or "") == "grok"
