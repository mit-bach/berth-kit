"""One Claude Code config folder per Claude account; Berth starts each session on its own (2026-11-03)."""

from __future__ import annotations

import json
import os

import pytest

from berth import configdir
from berth.errors import BerthError
from berth.mcp import Api, ToolError, spawn_command
from berth.model import Workspace
from berth.state import play_berth
from tests.fakes import FakeCmux
from tests.support import write_claude_conversation
from tests.test_state import _insert


@pytest.fixture
def personal(tmp_path):
    """A second config folder, as the Accounts Agent makes it: Claude's files, its own login."""
    folder = tmp_path / ".claude-personal"
    folder.mkdir()
    (folder / "settings.json").write_text("{}")
    return str(folder)


def _parked_claude(catalog, berth_id="c1", **attachments):
    write_claude_conversation(f"s-{berth_id}", "/tmp")
    extra = {"attachments": attachments} if attachments else {}
    _insert(catalog, id=berth_id, title=berth_id.title(), kind="other-agent", agent_name="claude",
            agent_session_id=f"s-{berth_id}", state="parked", cmux_workspace_id=None, cwd="/tmp",
            argv=["claude", "-r", f"s-{berth_id}"], display="claude -r", **extra)


def test_a_folder_is_checked_and_the_default_is_none(tmp_path, personal):
    assert configdir.check("default") is None and configdir.check("") is None
    assert configdir.check(personal) == personal
    for bad in ("relative/path", str(tmp_path / "missing")):
        with pytest.raises(BerthError):
            configdir.check(bad)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(BerthError) as refused:
        configdir.check(str(empty))
    assert "not a Claude Code config folder" in refused.value.message


def test_a_session_opens_and_cycles_on_its_folder(catalog, personal, monkeypatch):
    monkeypatch.setattr("berth.place.CMUX_CLAUDE_WRAPPER", "/nonexistent")
    _parked_claude(catalog)
    configdir.set_config_dir(catalog, "c1", personal, by="Accounts Agent")
    cmux = FakeCmux()
    play_berth(catalog, cmux, "c1", focus=False)
    command = cmux.created[-1]["command"]
    assert command.startswith(f"export CLAUDE_CONFIG_DIR={personal}; exec ") and "-r s-c1" in command
    assert "config_dir" in [event["kind"] for event in catalog.events_for("c1")]
    configdir.set_config_dir(catalog, "c1", "default")
    assert configdir.of(catalog.require_berth("c1")) is None


def test_a_session_on_the_default_folder_starts_as_before(catalog, monkeypatch):
    monkeypatch.setattr("berth.place.CMUX_CLAUDE_WRAPPER", "/nonexistent")
    _parked_claude(catalog)
    cmux = FakeCmux()
    play_berth(catalog, cmux, "c1", focus=False)
    assert "CLAUDE_CONFIG_DIR" not in cmux.created[-1]["command"]


def test_only_claude_sessions_take_a_folder(catalog, personal):
    _insert(catalog, id="g", title="G", kind="grok", agent_name="grok", agent_session_id="s-g", state="parked")
    with pytest.raises(BerthError) as refused:
        configdir.set_config_dir(catalog, "g", personal)
    assert refused.value.code == "KIND_UNSAFE"


def test_a_new_claude_starts_on_the_folder_it_was_given(personal):
    command = spawn_command("claude", "A job", model="opus", config_dir=personal)
    assert f"export CLAUDE_CONFIG_DIR={personal}; exec " in command and "--name 'A job'" in command
    assert "CLAUDE_CONFIG_DIR" not in spawn_command("claude", "A job", model="opus")


def test_update_moves_a_live_session_once_idle_and_sessions_show_the_folder(catalog, personal):
    _insert(catalog, id="live", title="Live", kind="other-agent", agent_name="claude", agent_session_id="s-live",
            state="live", cmux_workspace_id="ws-live", cwd="/tmp")
    _insert(catalog, id="other", title="Other", kind="other-agent", agent_name="claude", agent_session_id="s-o",
            state="parked", cmux_workspace_id=None, cwd="/tmp")
    cmux = FakeCmux()
    cmux.workspaces["ws-live"] = Workspace(id="ws-live", title="Live", cwd="/tmp")
    api = Api(catalog.path, cmux=cmux, caller_workspace="ws-steward")
    done = api("update", {"sessions": "live", "config_dir": personal, "cycle": True})["sessions"][0]
    assert done["cycle"].startswith("queued") and configdir.of(catalog.require_berth("live")) == personal
    from berth.deferred import pending

    assert [(item["action"], item["idle"]) for item in pending(catalog, "live")] == [("cycle", 1)]
    rows = {row["id"]: row for group in api("sessions", {"folder": "/", "fields": ["config_dir"]})["folders"]
            for row in group["sessions"]}
    assert rows["live"]["config_dir"] == personal and rows["other"]["config_dir"] == "~/.claude"
    assert api("session", {"session": "live"})["config_dir"] == personal
    with pytest.raises(ToolError):
        api("update", {"sessions": "live", "config_dir": "/nowhere/at/all"})


def test_a_fork_runs_on_its_sources_folder(catalog, personal, monkeypatch):
    from berth.ops import fork_berth

    monkeypatch.setattr("berth.place.CMUX_CLAUDE_WRAPPER", "/nonexistent")
    _parked_claude(catalog, "src", config_dir=personal)
    monkeypatch.setattr("berth.claude.transcript", lambda session, cwd: type("T", (), {"has_messages": True, "cwd": "/tmp"})())
    record = fork_berth(catalog, FakeCmux(), "src", open_pane=False)
    assert configdir.of(catalog.require_berth(record["id"])) == personal


def test_berth_reads_claude_from_every_config_folder(catalog, tmp_path, monkeypatch, personal):
    """A session on the other account writes its process record, transcript, and prompts in that folder."""
    from pathlib import Path

    from berth import claude, recency
    from berth.identity import claude_transcript

    root = Path(personal)
    monkeypatch.setattr("berth.configdir.extra_roots", lambda: [root])
    (root / "projects" / "-tmp").mkdir(parents=True)
    (root / "projects" / "-tmp" / "s-p.jsonl").write_text('{"type": "user"}\n')
    assert claude_transcript("s-p", "/tmp") == root / "projects" / "-tmp" / "s-p.jsonl"
    (root / "sessions").mkdir()
    (root / "sessions" / f"{os.getpid()}.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "s-p"}))
    assert claude.processes()[os.getpid()].session_id == "s-p"
    (root / "history.jsonl").write_text(json.dumps({"sessionId": "s-p", "timestamp": "2026-11-03T12:00:00Z"}) + "\n")
    assert recency.prompted_at("s-p") is not None


def test_a_folder_linked_to_claude_is_read_once(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    assert configdir.unique([real, link, tmp_path]) == [real, tmp_path]


def test_a_headless_compaction_runs_on_the_sessions_folder(personal):
    from berth.compact import headless

    seen = {}

    def run(argv, **kwargs):
        seen.update(kwargs)
        raise __import__("subprocess").TimeoutExpired(argv, 1)

    with pytest.raises(BerthError):  # the stand-in times out; headless turns that into an error
        headless("s-x", "/tmp", "keep the plan", run=run, config_dir=personal)
    assert seen["env"]["CLAUDE_CONFIG_DIR"] == personal


def test_the_command_line_takes_an_id_prefix_and_queues_the_move(catalog, personal, capsys, monkeypatch):
    from berth.cli import main

    monkeypatch.setattr("berth.cli.Cmux", FakeCmux)
    _insert(catalog, id="791a1ca9-d002-4956-ac17-ac677281d915", title="Agent", kind="other-agent",
            agent_name="claude", agent_session_id="s-st", state="live", cmux_workspace_id="ws-st", cwd="/tmp")
    assert main(["--catalog", str(catalog.path), "config-dir", "--json", "791a1ca9", personal, "--cycle"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["config_dir"] == personal and out["cycle"].startswith("queued")
    assert main(["--catalog", str(catalog.path), "config-dir", "--json", "791a", "default"]) == 0
    assert json.loads(capsys.readouterr().out)["config_dir"] == "~/.claude"
