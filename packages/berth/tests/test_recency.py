from __future__ import annotations

from berth.model import HookRecord, Workspace
from berth.recency import Recency
from tests.test_state import _insert


def test_last_used_takes_the_newest_signal_and_only_moves_forward(catalog):
    _insert(catalog, id="b1", state="live", cmux_workspace_id="ws-1", agent_session_id="s-1",
            played_at="2026-01-01T00:00:00.000000Z", updated_at="2026-01-01T00:00:00.000000Z")
    recency = Recency()
    ws = Workspace(id="ws-1", title="t", cwd="/tmp", submitted_at="2026-03-01T10:00:00.000Z")
    recency.refresh(catalog, [ws], {})
    row = catalog.require_berth("b1")
    assert row["last_used_at"] == "2026-03-01T10:00:00.000000Z"
    assert row["updated_at"] == "2026-01-01T00:00:00.000000Z"
    hook = HookRecord(agent_name="claude", session_id="s-1", workspace_id="ws-1", surface_id=None,
                      pid=None, cwd="/tmp", runtime_status=None, last_body=None, executable=None,
                      updated_at=1798761600.0)  # 2027-01-01
    recency.refresh(catalog, [ws], {"ws-1": hook})
    assert catalog.require_berth("b1")["last_used_at"].startswith("2027-01-01")
    older = Workspace(id="ws-1", title="t", cwd="/tmp", submitted_at="2025-01-01T00:00:00.000Z")
    recency.refresh(catalog, [older], {})
    assert catalog.require_berth("b1")["last_used_at"].startswith("2027-01-01")


def test_a_hook_for_another_session_does_not_count(catalog):
    _insert(catalog, id="b1", state="live", cmux_workspace_id="ws-1", agent_session_id="s-1",
            played_at="2026-01-01T00:00:00.000000Z")
    hook = HookRecord(agent_name="claude", session_id="other", workspace_id="ws-1", surface_id=None,
                      pid=None, cwd="/tmp", runtime_status=None, last_body=None, executable=None, updated_at=1798761600.0)
    Recency().refresh(catalog, [Workspace(id="ws-1", title="t", cwd="/tmp")], {"ws-1": hook})
    assert catalog.require_berth("b1")["last_used_at"] == "2026-01-01T00:00:00.000000Z"


def test_claude_recency_is_the_last_message_not_the_file_time(tmp_path):
    import json
    import os

    from berth.recency import last_message_at

    path = tmp_path / "s.jsonl"
    lines = [
        {"type": "user", "timestamp": "2026-10-01T10:00:00.000Z"},
        {"type": "assistant", "timestamp": "2026-10-01T10:05:00.000Z"},
        {"type": "ai-title", "aiTitle": "x"},
    ]
    path.write_text("\n".join(json.dumps(item) for item in lines) + "\n")
    os.utime(path, (2_000_000_000, 2_000_000_000))
    assert last_message_at(path).isoformat().startswith("2026-10-01T10:05")


def test_a_prompt_run_as_a_subtask_or_said_to_a_fork_counts_as_using_the_session(tmp_path, monkeypatch):
    """2026-10-29: a prompt sent to Research Agent with /subtask did not move it up in the sidebar."""
    import json

    from berth import identity
    from berth import recency

    monkeypatch.setattr(identity, "claude_projects_root", lambda: tmp_path / "projects")
    monkeypatch.setattr(recency, "claude_history_path", lambda: tmp_path / "history.jsonl")
    project = tmp_path / "projects" / "-tmp"
    project.mkdir(parents=True)
    transcript = project / "s-1.jsonl"

    def write(path, items):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(item) + "\n" for item in items))

    row = {"agent_session_id": "s-1", "cwd": "/tmp", "kind": "other-agent", "agent_name": "claude"}
    # What Claude 2.1.312 wrote for the prompt: two system lines, no user message.
    write(transcript, [
        {"type": "assistant", "timestamp": "2026-10-29T13:13:26.735Z"},
        {"type": "system", "subtype": "informational", "content": "Started in the background: At the moment…",
         "timestamp": "2026-10-29T16:18:16.672Z"},
        {"type": "system", "subtype": "local_command", "content": "<command-name>/subtask</command-name>",
         "timestamp": "2026-10-29T16:18:16.676Z"},
    ])
    assert recency.agent_activity(row).isoformat().startswith("2026-10-29T16:18:16.676")
    # The fork's own transcript: a message said to it while it worked, then its work.
    write(project / "s-1" / "subagents" / "agent-at-the-moment-1.jsonl", [
        {"type": "attachment", "attachment": {"type": "queued_command", "prompt": "and fispan?"},
         "timestamp": "2026-10-29T16:19:00.000Z"},
    ])
    assert recency.agent_activity(row).isoformat().startswith("2026-10-29T16:19:00")
    # Claude's prompt history knows everything typed into the box, whatever the transcript says.
    write(tmp_path / "history.jsonl", [{"display": "/model", "timestamp": 1793290800000, "sessionId": "s-1"},
                                       {"display": "x", "timestamp": 1893290800000, "sessionId": "other"}])
    assert recency.agent_activity(row).isoformat().startswith("2026-10-29T16:20:00")
