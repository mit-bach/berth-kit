"""A shell that starts Grok becomes that session. The two names stay one name."""

from __future__ import annotations

import pytest

import json
import re

from berth.identity import refresh_identity
from berth.model import HookRecord, Workspace
from berth.ops import rename_berth
from tests.fakes import FakeCmux
from tests.test_state import _insert


_TOP = """
0.0	1	1	workspace	workspace:31	window:1	agy
0.0	1	1	pane	pane:38	workspace:31	
0.0	1	1	surface	surface:44	pane:38	agy
0.0	1	1	process	39004	surface:44	login
0.0	1	1	process	39005	39004	zsh
0.0	1	1	process	39619	39005	agy
0.0	1	1	workspace	workspace:30	window:1	Cursor Agent
0.0	1	1	pane	pane:35	workspace:30	
0.0	1	1	surface	surface:38	pane:35	Cursor Agent
0.0	1	1	process	89317	surface:38	login
0.0	1	1	process	89321	89317	zsh
0.0	1	1	process	98815	89321	node
0.0	1	1	workspace	workspace:27	window:1	Codex
0.0	1	1	pane	pane:32	workspace:27	
0.0	1	1	surface	surface:35	pane:32	pat-dom
0.0	1	1	process	2971	surface:35	codex
"""


class _Top:
    def __init__(self, text: str) -> None:
        self.text = text

    def socket_up(self) -> bool:
        return True

    def top_processes(self) -> str:
        return self.text



@pytest.fixture(autouse=True)
def idle_panes(monkeypatch):
    """A live pane with no screen of its own reads as an idle Claude prompt: /rename is typed only into an idle
    pane (2026-10-31). Tests that show a dialog or a running turn set their own screen."""
    from tests.test_activity import claude_screen

    idle = claude_screen()
    original = FakeCmux.read_screen

    def read_screen(self, workspace_id, *, lines=120, scrollback=True):
        if workspace_id not in self.screens and not self.fail_read:
            return idle
        return original(self, workspace_id, lines=lines, scrollback=scrollback)

    monkeypatch.setattr(FakeCmux, "read_screen", read_screen)

def test_foreground_codex_antigravity_and_cursor_leave_the_shell_kind(catalog):
    from berth.identity import promote_foreground_agents

    agy = _insert(
        catalog,
        id="agy-row",
        title="~/pat-dom",
        kind="shell",
        state="live",
        agent_name=None,
        cmux_workspace_id="WS-AGY",
        cwd="/Users/someone/pat-dom",
        argv=[],
        display="shell",
    )
    cursor = _insert(
        catalog,
        id="cursor-row",
        title="~/pat-dom",
        kind="shell",
        state="live",
        agent_name=None,
        cmux_workspace_id="WS-CURSOR",
        cwd="/Users/someone/pat-dom",
        argv=[],
        display="shell",
    )
    codex = _insert(
        catalog,
        id="codex-row",
        title="Codex",
        kind="shell",
        state="live",
        agent_name=None,
        cmux_workspace_id="WS-CODEX",
        cwd="/Users/someone/pat-dom",
        argv=[],
        display="shell",
    )
    workspaces = [
        Workspace(id="WS-AGY", title="agy", cwd="/Users/someone/pat-dom", ref="workspace:31"),
        Workspace(id="WS-CURSOR", title="Cursor Agent", cwd="/Users/someone/pat-dom", ref="workspace:30"),
        Workspace(id="WS-CODEX", title="Codex", cwd="/Users/someone/pat-dom", ref="workspace:27", custom=True),
    ]

    def command(pid: int) -> str:
        if pid == 98815:
            return "/Users/someone/.local/bin/agent --use-system-ca /opt/cursor-agent/index.js"
        return ""

    from berth.place import agents_in_top

    found = agents_in_top(_TOP, workspaces, command_fn=command)
    assert found["ws-agy"][0] == "antigravity"
    assert found["ws-cursor"][0] == "cursor"
    assert found["ws-codex"][0] == "codex"

    class Desk(_Top):
        def top_processes(self) -> str:
            return _TOP

    # command lookup goes through ps in promote; stub it
    import berth.identity as identity

    original = identity._process_command
    identity._process_command = command
    try:
        promote_foreground_agents(catalog, Desk(_TOP), workspaces)
    finally:
        identity._process_command = original

    agy_row = catalog.require_berth(agy)
    cursor_row = catalog.require_berth(cursor)
    codex_row = catalog.require_berth(codex)
    assert agy_row["kind"] == "other-agent" and agy_row["agent_name"] == "antigravity"
    assert agy_row["title"] == "agy"
    assert cursor_row["kind"] == "other-agent" and cursor_row["agent_name"] == "cursor"
    assert cursor_row["title"] == "Cursor Agent"
    assert codex_row["kind"] == "other-agent" and codex_row["agent_name"] == "codex"
    assert codex_row["title"] == "Codex"
    assert json.loads(agy_row["recipe_argv"]) == ["agy"]


def _reset() -> None:
    from berth import identity

    identity._sent.clear()
    identity._renamed.clear()
    identity._claude_cache.clear()


def _summary(tmp_path, session_id: str, title: str, manual: bool = False) -> None:
    folder = tmp_path / "sessions" / session_id
    folder.mkdir(parents=True)
    (folder / "summary.json").write_text(
        json.dumps({"generated_title": title, "title_is_manual": manual}),
        encoding="utf-8",
    )


def _hook(session_id: str = "sess-1", workspace_id: str = "ws-1") -> dict[str, HookRecord]:
    return {
        workspace_id.lower(): HookRecord(
            agent_name="grok",
            session_id=session_id,
            workspace_id=workspace_id,
            surface_id="surface-1",
            pid=42,
            cwd="/tmp/work",
            runtime_status="running",
            last_body=None,
            executable="/Users/olduser/.grok/bin/grok",
            arguments=["/Users/olduser/.grok/bin/grok"],
            launcher="grok",
        )
    }


def test_shell_that_starts_grok_takes_the_session_name(catalog, tmp_path, monkeypatch):
    _reset()
    monkeypatch.setattr("berth.identity.session_home", lambda cwd: tmp_path / "sessions")
    _summary(tmp_path, "sess-1", "jev 3", manual=True)
    _insert(
        catalog,
        id="shell-1",
        title="…/HW1",
        state="live",
        kind="shell",
        cwd="/tmp/work",
        note="no session id",
        cmux_workspace_id="ws-1",
        argv=[],
        display="shell",
    )
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(
        id="ws-1", title="jev 3 - grok", cwd="/tmp/work", custom=False
    )
    refresh_identity(catalog, cmux, _hook())
    row = catalog.require_berth("shell-1")
    assert row["kind"] == "grok"
    assert row["agent_session_id"] == "sess-1"
    assert row["title"] == "jev 3"
    assert row["note"] == ""
    assert cmux.workspaces["ws-1"].title == "jev 3"
    assert cmux.sent == []


def test_a_workspace_rename_is_sent_into_the_grok_session(catalog, tmp_path, monkeypatch):
    _reset()
    monkeypatch.setattr("berth.identity.session_home", lambda cwd: tmp_path / "sessions")
    _summary(tmp_path, "sess-1", "old name", manual=True)
    _insert(
        catalog,
        id="grok-1",
        title="old name",
        state="live",
        kind="grok",
        cwd="/tmp/work",
        agent_name="grok",
        agent_session_id="sess-1",
        cmux_workspace_id="ws-1",
    )
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(
        id="ws-1", title="from the sidebar", cwd="/tmp/work", custom=True
    )
    refresh_identity(catalog, cmux, {})
    row = catalog.require_berth("grok-1")
    assert row["title"] == "from the sidebar"
    assert cmux.sent == [("ws-1", "/rename from the sidebar\n")]
    assert cmux.workspaces["ws-1"].title == "from the sidebar"
    refresh_identity(catalog, cmux, {})
    assert cmux.sent == [("ws-1", "/rename from the sidebar\n")]


def test_a_generated_grok_title_keeps_a_chosen_workspace_name(catalog, tmp_path, monkeypatch):
    _reset()
    monkeypatch.setattr("berth.identity.session_home", lambda cwd: tmp_path / "sessions")
    _summary(tmp_path, "sess-1", "Design Berth MCP", manual=False)
    _insert(
        catalog,
        id="grok-1",
        title="design cedar",
        state="live",
        kind="grok",
        cwd="/tmp/work",
        agent_name="grok",
        agent_session_id="sess-1",
        cmux_workspace_id="ws-1",
    )
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(
        id="ws-1", title="design cedar", cwd="/tmp/work", custom=True
    )
    refresh_identity(catalog, cmux, {})
    assert catalog.require_berth("grok-1")["title"] == "design cedar"
    assert cmux.workspaces["ws-1"].title == "design cedar"
    assert cmux.sent == []


def test_a_generated_grok_title_replaces_the_directory_title(catalog, tmp_path, monkeypatch):
    _reset()
    monkeypatch.setattr("berth.identity.session_home", lambda cwd: tmp_path / "sessions")
    _summary(tmp_path, "sess-1", "Design Berth MCP", manual=False)
    _insert(
        catalog,
        id="grok-1",
        title="~/work",
        state="live",
        kind="grok",
        cwd="/tmp/work",
        agent_name="grok",
        agent_session_id="sess-1",
        cmux_workspace_id="ws-1",
    )
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(id="ws-1", title="~/work", cwd="/tmp/work", custom=True)
    refresh_identity(catalog, cmux, {})
    assert catalog.require_berth("grok-1")["title"] == "Design Berth MCP"
    assert cmux.workspaces["ws-1"].title == "Design Berth MCP"
    assert cmux.sent == []


def test_a_moved_session_follows_the_pane_that_is_running_it(catalog, tmp_path, monkeypatch):
    _reset()
    monkeypatch.setattr("berth.identity.session_home", lambda cwd: tmp_path / "sessions")
    _summary(tmp_path, "old-sess", "prompts general", manual=True)
    _summary(tmp_path, "new-sess", "prompts prune", manual=True)
    _insert(
        catalog,
        id="old-row",
        title="prompts general",
        state="live",
        kind="grok",
        cwd="/tmp/work",
        agent_name="grok",
        agent_session_id="old-sess",
        cmux_workspace_id="ws-old",
    )
    _insert(
        catalog,
        id="shell-row",
        title="…/HW1",
        state="live",
        kind="shell",
        cwd="/tmp/work",
        note="no session id",
        agent_name=None,
        agent_session_id=None,
        cmux_workspace_id="ws-new",
        argv=[],
        display="shell",
    )
    cmux = FakeCmux()
    cmux.workspaces["ws-old"] = Workspace(
        id="ws-old", title="prompts general", cwd="/tmp/work", custom=True
    )
    cmux.workspaces["ws-new"] = Workspace(
        id="ws-new", title="prompts general - grok", cwd="/tmp/work", custom=False
    )
    hooks = {
        "ws-old": HookRecord(
            agent_name="grok",
            session_id="new-sess",
            workspace_id="ws-old",
            surface_id=None,
            pid=1,
            cwd="/tmp/work",
            runtime_status="needsInput",
            last_body=None,
            executable="/Users/olduser/.grok/bin/grok",
            arguments=["grok"],
            launcher="grok",
            updated_at=20,
        ),
        "ws-new": HookRecord(
            agent_name="grok",
            session_id="old-sess",
            workspace_id="ws-new",
            surface_id=None,
            pid=2,
            cwd="/tmp/work",
            runtime_status="running",
            last_body=None,
            executable="/Users/olduser/.grok/bin/grok",
            arguments=["grok"],
            launcher="grok",
            updated_at=30,
        ),
    }
    refresh_identity(catalog, cmux, hooks)
    assert catalog.require_berth("old-row")["agent_session_id"] == "new-sess"
    assert catalog.require_berth("old-row")["title"] == "prompts prune"
    assert catalog.require_berth("shell-row")["kind"] == "grok"
    assert catalog.require_berth("shell-row")["agent_session_id"] == "old-sess"
    assert catalog.require_berth("shell-row")["title"] == "prompts general"
    assert cmux.workspaces["ws-old"].title == "prompts prune"
    assert cmux.workspaces["ws-new"].title == "prompts general"
    assert cmux.sent == []


def test_a_phantom_hook_reattaches_the_parked_session(catalog, tmp_path, monkeypatch):
    _reset()
    monkeypatch.setattr("berth.identity.session_home", lambda cwd: tmp_path / "sessions")
    _summary(tmp_path, "old-sess", "prompts general", manual=True)
    parked_hook = HookRecord(
        agent_name="grok",
        session_id="old-sess",
        workspace_id="ws-closed",
        surface_id=None,
        pid=7,
        cwd="/tmp/work",
        runtime_status="idle",
        last_body=None,
        executable="/Users/olduser/.grok/bin/grok",
        arguments=["grok"],
        launcher="grok",
        updated_at=50,
    )
    phantom = HookRecord(
        agent_name="grok",
        session_id="missing-sess",
        workspace_id="ws-new",
        surface_id=None,
        pid=7,
        cwd="/tmp/work",
        runtime_status="running",
        last_body=None,
        executable="/Users/olduser/.grok/bin/grok",
        arguments=["grok"],
        launcher="grok",
        updated_at=10,
    )
    monkeypatch.setattr("berth.hooks.load_hook_records", lambda directory=None: [parked_hook, phantom])
    _insert(
        catalog,
        id="parked-row",
        title="prompts general",
        state="parked",
        kind="grok",
        cwd="/tmp/work",
        agent_name="grok",
        agent_session_id="old-sess",
        cmux_workspace_id=None,
    )
    _insert(
        catalog,
        id="shell-row",
        title="…/HW1",
        state="live",
        kind="shell",
        cwd="/tmp/work",
        note="no session id",
        agent_name=None,
        agent_session_id=None,
        cmux_workspace_id="ws-new",
        argv=[],
        display="shell",
        protect=0,
    )
    cmux = FakeCmux()
    cmux.workspaces["ws-new"] = Workspace(
        id="ws-new", title="prompts general - grok", cwd="/tmp/work", custom=False
    )
    refresh_identity(catalog, cmux, {"ws-new": phantom})
    assert catalog.get_berth("shell-row") is None
    kept = catalog.require_berth("parked-row")
    assert kept["state"] == "live"
    assert kept["agent_session_id"] == "old-sess"
    assert kept["cmux_workspace_id"] == "ws-new"
    assert kept["title"] == "prompts general"
    assert cmux.workspaces["ws-new"].title == "prompts general"
    assert cmux.sent == []


def test_a_live_grok_row_yields_the_workspace_to_claude(catalog):
    _reset()
    _insert(
        catalog,
        id="grok-row",
        title="…/desk",
        state="live",
        kind="grok",
        cwd="/tmp/desk",
        agent_name="grok",
        agent_session_id="grok-sess",
        cmux_workspace_id="ws-claude",
        protect=1,
        argv=["grok", "-r", "grok-sess"],
        display="grok -r grok-sess",
    )
    binary = "/Users/someone/.local/bin/claude"
    hook = HookRecord(
        agent_name="claude",
        session_id="claude-sess",
        workspace_id="ws-claude",
        surface_id="surface-c",
        pid=86250,
        cwd="/Users/someone",
        runtime_status="running",
        last_body=None,
        executable=binary,
        arguments=[binary],
        launcher="claude",
        updated_at=100,
    )
    cmux = FakeCmux()
    cmux.workspaces["ws-claude"] = Workspace(
        id="ws-claude", title="login-successful", cwd="/Users/someone", custom=True
    )
    refresh_identity(catalog, cmux, {"ws-claude": hook})
    parked = catalog.require_berth("grok-row")
    assert parked["state"] == "parked"
    assert parked["kind"] == "grok"
    assert parked["agent_session_id"] == "grok-sess"
    assert parked["cmux_workspace_id"] is None
    assert cmux.closed == []
    assert cmux.sent == []
    rows = list(catalog.all_berths())
    assert len(rows) == 2
    live = [row for row in rows if row["state"] == "live"]
    assert len(live) == 1
    assert live[0]["kind"] == "other-agent"
    assert live[0]["agent_name"] == "claude"
    assert live[0]["agent_session_id"] == "claude-sess"
    assert live[0]["title"] == "login-successful"
    assert live[0]["cmux_workspace_id"] == "ws-claude"
    assert live[0]["protect"] == 1
    assert json.loads(live[0]["recipe_argv"]) == [binary, "-r", "claude-sess"]
    refresh_identity(catalog, cmux, {"ws-claude": hook})
    assert len(list(catalog.all_berths())) == 2
    assert catalog.require_berth("grok-row")["state"] == "parked"


def test_a_shell_that_starts_claude_becomes_claude(catalog):
    _reset()
    binary = "/Users/someone/.local/bin/claude"
    _insert(
        catalog,
        id="shell-row",
        title="login-successful",
        state="live",
        kind="shell",
        cwd="/Users/someone",
        note="no session id",
        agent_name=None,
        agent_session_id=None,
        cmux_workspace_id="ws-claude",
        argv=[],
        display="shell",
        protect=0,
    )
    hook = HookRecord(
        agent_name="claude",
        session_id="claude-sess",
        workspace_id="ws-claude",
        surface_id="surface-c",
        pid=86250,
        cwd="/Users/someone",
        runtime_status="running",
        last_body=None,
        executable=binary,
        arguments=[binary],
        launcher="claude",
    )
    cmux = FakeCmux()
    cmux.workspaces["ws-claude"] = Workspace(
        id="ws-claude", title="login-successful", cwd="/Users/someone", custom=True
    )
    refresh_identity(catalog, cmux, {"ws-claude": hook})
    row = catalog.require_berth("shell-row")
    assert row["kind"] == "other-agent"
    assert row["agent_name"] == "claude"
    assert row["agent_session_id"] == "claude-sess"
    assert row["state"] == "live"
    assert json.loads(row["recipe_argv"]) == [binary, "-r", "claude-sess"]
    assert len(list(catalog.all_berths())) == 1
    assert cmux.closed == []
    assert cmux.sent == []


def test_a_locked_name_waits_for_the_session_file(catalog, tmp_path, monkeypatch):
    _reset()
    monkeypatch.setattr("berth.identity.session_home", lambda cwd: tmp_path / "sessions")
    _summary(tmp_path, "sess-1", "generated", manual=False)
    _insert(
        catalog,
        id="grok-1",
        title="chosen",
        state="live",
        kind="grok",
        cwd="/tmp/work",
        agent_name="grok",
        agent_session_id="sess-1",
        cmux_workspace_id="ws-1",
        attachments={"name_lock": "chosen"},
    )
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(id="ws-1", title="chosen", cwd="/tmp/work", custom=True)
    refresh_identity(catalog, cmux, {})
    assert catalog.require_berth("grok-1")["title"] == "chosen"
    assert cmux.workspaces["ws-1"].title == "chosen"
    assert cmux.sent == [("ws-1", "/rename chosen\n")]


def _claude_log(tmp_path, session_id: str, cwd: str, title: str | None):
    folder = tmp_path / "projects" / re.sub(r"[^A-Za-z0-9]", "-", cwd)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{session_id}.jsonl"
    if title is None:
        path.write_text('{"type": "user"}\n', encoding="utf-8")
    else:
        line = json.dumps({"type": "custom-title", "customTitle": title, "sessionId": session_id})
        path.write_text(line + "\n", encoding="utf-8")
    return path


def _use_claude_root(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("berth.identity.claude_projects_root", lambda: tmp_path / "projects")


def _claude_row(catalog, *, berth_id: str = "claude-1", title: str = "old name", state: str = "live") -> str:
    return _insert(
        catalog,
        id=berth_id,
        title=title,
        state=state,
        kind="other-agent",
        cwd="/tmp/work",
        agent_name="claude",
        agent_session_id="claude-1",
        cmux_workspace_id="ws-1" if state == "live" else None,
        argv=["claude", "-r", "claude-1"],
        display="claude -r claude-1",
    )


def test_retitle_live_grok_renames_the_workspace_and_the_session(catalog, tmp_path, monkeypatch):
    _reset()
    monkeypatch.setattr("berth.identity.session_home", lambda cwd: tmp_path / "sessions")
    _summary(tmp_path, "sess-1", "old name", manual=True)
    _insert(
        catalog,
        id="grok-1",
        title="old name",
        state="live",
        kind="grok",
        cwd="/tmp/work",
        agent_name="grok",
        agent_session_id="sess-1",
        cmux_workspace_id="ws-1",
    )
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(id="ws-1", title="old name", cwd="/tmp/work", custom=True)
    rename_berth(catalog, cmux, "grok-1", "chosen name")
    assert catalog.require_berth("grok-1")["title"] == "chosen name"
    assert cmux.workspaces["ws-1"].title == "chosen name"
    assert cmux.sent == [("ws-1", "/rename chosen name\n")]
    refresh_identity(catalog, cmux, {})
    refresh_identity(catalog, cmux, {})
    assert cmux.sent == [("ws-1", "/rename chosen name\n")]
    assert catalog.require_berth("grok-1")["title"] == "chosen name"
    assert cmux.workspaces["ws-1"].title == "chosen name"


def test_retitle_live_claude_sends_rename_once(catalog, tmp_path, monkeypatch):
    _reset()
    _use_claude_root(monkeypatch, tmp_path)
    transcript = _claude_log(tmp_path, "claude-1", "/tmp/work", "old name")
    before = transcript.read_text(encoding="utf-8")
    _claude_row(catalog)
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(id="ws-1", title="old name", cwd="/tmp/work", custom=True)
    rename_berth(catalog, cmux, "claude-1", "chosen name")
    assert catalog.require_berth("claude-1")["title"] == "chosen name"
    assert cmux.workspaces["ws-1"].title == "chosen name"
    assert cmux.sent == [("ws-1", "/rename chosen name\n")]
    assert transcript.read_text(encoding="utf-8") == before
    refresh_identity(catalog, cmux, {})
    refresh_identity(catalog, cmux, {})
    assert cmux.sent == [("ws-1", "/rename chosen name\n")]
    assert catalog.require_berth("claude-1")["title"] == "chosen name"


def test_a_workspace_rename_on_claude_updates_the_catalog(catalog, tmp_path, monkeypatch):
    _reset()
    _use_claude_root(monkeypatch, tmp_path)
    _claude_log(tmp_path, "claude-1", "/tmp/work", "old name")
    _claude_row(catalog)
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(
        id="ws-1", title="from the sidebar", cwd="/tmp/work", custom=True
    )
    refresh_identity(catalog, cmux, {})
    assert catalog.require_berth("claude-1")["title"] == "from the sidebar"
    assert cmux.workspaces["ws-1"].title == "from the sidebar"
    assert cmux.sent == [("ws-1", "/rename from the sidebar\n")]
    refresh_identity(catalog, cmux, {})
    assert cmux.sent == [("ws-1", "/rename from the sidebar\n")]
    assert catalog.require_berth("claude-1")["title"] == "from the sidebar"


def test_a_claude_session_rename_updates_the_catalog_and_workspace(catalog, tmp_path, monkeypatch):
    _reset()
    _use_claude_root(monkeypatch, tmp_path)
    _claude_log(tmp_path, "claude-1", "/tmp/work", "from the session")
    _claude_row(catalog, title="old name")
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(id="ws-1", title="old name", cwd="/tmp/work", custom=True)
    refresh_identity(catalog, cmux, {})
    assert catalog.require_berth("claude-1")["title"] == "from the session"
    assert cmux.workspaces["ws-1"].title == "from the session"
    assert cmux.sent == []
    refresh_identity(catalog, cmux, {})
    assert cmux.sent == []
    assert cmux.workspaces["ws-1"].title == "from the session"


def test_retitle_shell_does_not_send_rename(catalog):
    _reset()
    _insert(
        catalog,
        id="shell-1",
        title="old title",
        state="live",
        kind="shell",
        cwd="/tmp/work",
        cmux_workspace_id="ws-1",
        argv=[],
        display="shell",
    )
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(id="ws-1", title="old title", cwd="/tmp/work", custom=True)
    rename_berth(catalog, cmux, "shell-1", "new title")
    assert catalog.require_berth("shell-1")["title"] == "new title"
    assert cmux.workspaces["ws-1"].title == "new title"
    assert cmux.sent == []
    refresh_identity(catalog, cmux, {})
    assert cmux.sent == []
    assert catalog.require_berth("shell-1")["title"] == "new title"


def test_parked_claude_rename_keeps_the_transcript(catalog, tmp_path, monkeypatch):
    _reset()
    _use_claude_root(monkeypatch, tmp_path)
    transcript = _claude_log(tmp_path, "claude-1", "/tmp/work", "old name")
    before = transcript.read_text(encoding="utf-8")
    _claude_row(catalog, state="parked", title="old name")
    cmux = FakeCmux()
    rename_berth(catalog, cmux, "claude-1", "chosen name")
    assert catalog.require_berth("claude-1")["title"] == "chosen name"
    assert cmux.sent == []
    assert transcript.read_text(encoding="utf-8") == before
    sidecar = transcript.parent / "claude-1" / "custom-title.json"
    assert json.loads(sidecar.read_text(encoding="utf-8"))["customTitle"] == "chosen name"


def _use_agent_roots(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("berth.identity.cursor_chats_root", lambda: tmp_path / "chats")
    monkeypatch.setattr("berth.identity.codex_state_path", lambda: tmp_path / "state.sqlite")
    monkeypatch.setattr("berth.identity.codex_lock_dir", lambda: tmp_path / "locks")
    monkeypatch.setattr("berth.identity.agy_summary_path", lambda: tmp_path / "summaries.db")
    monkeypatch.setattr("berth.identity.agy_presence_dir", lambda: tmp_path / "presence")


def _cursor_meta(tmp_path, session_id: str, cwd: str, title: str | None, updated: int = 1) -> Path:
    folder = tmp_path / "chats" / "project" / session_id
    folder.mkdir(parents=True, exist_ok=True)
    data = {
        "schemaVersion": 1,
        "cwd": cwd,
        "createdAtMs": 1,
        "updatedAtMs": updated,
        "hasConversation": True,
    }
    if title is not None:
        data["title"] = title
    path = folder / "meta.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _codex_db(tmp_path, session_id: str, cwd: str, title: str, name: str | None = None, updated: int = 1) -> None:
    import sqlite3

    path = tmp_path / "state.sqlite"
    fresh = not path.exists()
    conn = sqlite3.connect(path)
    if fresh:
        conn.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                cwd TEXT,
                title TEXT,
                name TEXT,
                updated_at INTEGER,
                archived INTEGER
            )
            """
        )
    conn.execute(
        """
        INSERT INTO threads (id, cwd, title, name, updated_at, archived)
        VALUES (?, ?, ?, ?, ?, 0)
        """,
        (session_id, cwd, title, name, updated),
    )
    conn.commit()
    conn.close()


def _agy_db(tmp_path, session_id: str, cwd: str, title: str, modified: str = "2026-01-01") -> None:
    import sqlite3

    path = tmp_path / "summaries.db"
    fresh = not path.exists()
    conn = sqlite3.connect(path)
    if fresh:
        conn.execute(
            """
            CREATE TABLE conversation_summaries (
                conversation_id TEXT PRIMARY KEY,
                title TEXT,
                workspace_uris TEXT,
                last_modified_time TEXT
            )
            """
        )
    conn.execute(
        """
        INSERT INTO conversation_summaries
            (conversation_id, title, workspace_uris, last_modified_time)
        VALUES (?, ?, ?, ?)
        """,
        (session_id, title, json.dumps([f"file://{cwd}"]), modified),
    )
    conn.commit()
    conn.close()


def _agent_row(
    catalog,
    *,
    berth_id: str,
    agent: str,
    title: str = "old name",
    state: str = "live",
    session: str | None = "sess-1",
    cwd: str = "/tmp/work",
    workspace: str = "ws-1",
) -> str:
    program = {"cursor": "/bin/agent", "codex": "codex", "antigravity": "agy"}[agent]
    argv = [program]
    if session:
        if agent == "codex":
            argv += ["resume", session]
        elif agent == "antigravity":
            argv += ["--conversation", session]
        else:
            argv += ["--resume", session]
    return _insert(
        catalog,
        id=berth_id,
        title=title,
        state=state,
        kind="other-agent",
        cwd=cwd,
        agent_name=agent,
        agent_session_id=session,
        cmux_workspace_id=workspace if state == "live" else None,
        argv=argv,
        display=" ".join(argv),
    )


def test_retitle_live_cursor_sends_rename_once(catalog, tmp_path, monkeypatch):
    _reset()
    _use_agent_roots(monkeypatch, tmp_path)
    meta = _cursor_meta(tmp_path, "cursor-1", "/tmp/work", "old name")
    before = meta.read_text(encoding="utf-8")
    _agent_row(catalog, berth_id="cursor-row", agent="cursor", session="cursor-1")
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(id="ws-1", title="old name", cwd="/tmp/work", custom=True)
    rename_berth(catalog, cmux, "cursor-row", "chosen name")
    assert catalog.require_berth("cursor-row")["title"] == "chosen name"
    assert cmux.workspaces["ws-1"].title == "chosen name"
    assert cmux.sent == [("ws-1", "/rename chosen name\n")]
    assert meta.read_text(encoding="utf-8") == before
    refresh_identity(catalog, cmux, {})
    refresh_identity(catalog, cmux, {})
    assert cmux.sent == [("ws-1", "/rename chosen name\n")]
    assert catalog.require_berth("cursor-row")["title"] == "chosen name"


def test_a_workspace_rename_on_cursor_updates_the_agent(catalog, tmp_path, monkeypatch):
    _reset()
    _use_agent_roots(monkeypatch, tmp_path)
    _cursor_meta(tmp_path, "cursor-1", "/tmp/work", "old name")
    _agent_row(catalog, berth_id="cursor-row", agent="cursor", session="cursor-1")
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(
        id="ws-1", title="Cursor Prime", cwd="/tmp/work", custom=True
    )
    refresh_identity(catalog, cmux, {})
    assert catalog.require_berth("cursor-row")["title"] == "Cursor Prime"
    assert cmux.sent == [("ws-1", "/rename Cursor Prime\n")]
    refresh_identity(catalog, cmux, {})
    assert cmux.sent == [("ws-1", "/rename Cursor Prime\n")]
    assert cmux.workspaces["ws-1"].title == "Cursor Prime"


def test_a_cursor_session_rename_updates_the_catalog_and_workspace(catalog, tmp_path, monkeypatch):
    _reset()
    _use_agent_roots(monkeypatch, tmp_path)
    _cursor_meta(tmp_path, "cursor-1", "/tmp/work", "Cursor Beta")
    _agent_row(catalog, berth_id="cursor-row", agent="cursor", session="cursor-1", title="Cursor Prime")
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(id="ws-1", title="Cursor Prime", cwd="/tmp/work", custom=True)
    refresh_identity(catalog, cmux, {})
    assert catalog.require_berth("cursor-row")["title"] == "Cursor Beta"
    assert cmux.workspaces["ws-1"].title == "Cursor Beta"
    assert cmux.sent == []
    refresh_identity(catalog, cmux, {})
    assert cmux.sent == []


def test_retitle_live_codex_sends_rename_once(catalog, tmp_path, monkeypatch):
    _reset()
    _use_agent_roots(monkeypatch, tmp_path)
    _codex_db(tmp_path, "codex-1", "/tmp/work", "generated", name="old name")
    _agent_row(catalog, berth_id="codex-row", agent="codex", session="codex-1")
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(id="ws-1", title="old name", cwd="/tmp/work", custom=True)
    rename_berth(catalog, cmux, "codex-row", "chosen name")
    assert catalog.require_berth("codex-row")["title"] == "chosen name"
    assert cmux.workspaces["ws-1"].title == "chosen name"
    assert cmux.sent == [("ws-1", "/rename chosen name\n")]
    refresh_identity(catalog, cmux, {})
    refresh_identity(catalog, cmux, {})
    assert cmux.sent == [("ws-1", "/rename chosen name\n")]


def test_a_codex_rename_updates_the_catalog_and_workspace(catalog, tmp_path, monkeypatch):
    _reset()
    _use_agent_roots(monkeypatch, tmp_path)
    _codex_db(tmp_path, "codex-1", "/tmp/work", "generated", name="from the session")
    _agent_row(catalog, berth_id="codex-row", agent="codex", session="codex-1")
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(id="ws-1", title="old name", cwd="/tmp/work", custom=True)
    refresh_identity(catalog, cmux, {})
    assert catalog.require_berth("codex-row")["title"] == "from the session"
    assert cmux.workspaces["ws-1"].title == "from the session"
    assert cmux.sent == []


def test_a_generated_codex_title_does_not_break_a_lock(catalog, tmp_path, monkeypatch):
    _reset()
    _use_agent_roots(monkeypatch, tmp_path)
    _codex_db(tmp_path, "codex-1", "/tmp/work", "generated", name=None)
    _agent_row(
        catalog,
        berth_id="codex-row",
        agent="codex",
        session="codex-1",
        title="chosen",
    )
    row = catalog.require_berth("codex-row")
    with catalog.immediate():
        catalog.update_berth(row["id"], attachments=json.dumps({"name_lock": "chosen"}))
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(id="ws-1", title="chosen", cwd="/tmp/work", custom=True)
    refresh_identity(catalog, cmux, {})
    assert catalog.require_berth("codex-row")["title"] == "chosen"
    assert cmux.workspaces["ws-1"].title == "chosen"
    assert cmux.sent == [("ws-1", "/rename chosen\n")]


def test_retitle_live_antigravity_sends_rename_once(catalog, tmp_path, monkeypatch):
    _reset()
    _use_agent_roots(monkeypatch, tmp_path)
    _agy_db(tmp_path, "agy-1", "/tmp/work", "old name")
    _agent_row(catalog, berth_id="agy-row", agent="antigravity", session="agy-1")
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(id="ws-1", title="old name", cwd="/tmp/work", custom=True)
    rename_berth(catalog, cmux, "agy-row", "chosen name")
    assert catalog.require_berth("agy-row")["title"] == "chosen name"
    assert cmux.sent == [("ws-1", "/rename chosen name\n")]
    refresh_identity(catalog, cmux, {})
    refresh_identity(catalog, cmux, {})
    assert cmux.sent == [("ws-1", "/rename chosen name\n")]
    assert cmux.workspaces["ws-1"].title == "chosen name"


def test_an_antigravity_rename_updates_the_catalog_and_workspace(catalog, tmp_path, monkeypatch):
    _reset()
    _use_agent_roots(monkeypatch, tmp_path)
    _agy_db(tmp_path, "agy-1", "/tmp/work", "from the session")
    _agent_row(catalog, berth_id="agy-row", agent="antigravity", session="agy-1")
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(id="ws-1", title="old name", cwd="/tmp/work", custom=True)
    refresh_identity(catalog, cmux, {})
    assert catalog.require_berth("agy-row")["title"] == "from the session"
    assert cmux.workspaces["ws-1"].title == "from the session"
    assert cmux.sent == []


def test_a_cursor_rename_leaves_the_codex_row_alone(catalog, tmp_path, monkeypatch):
    _reset()
    _use_agent_roots(monkeypatch, tmp_path)
    _cursor_meta(tmp_path, "cursor-1", "/tmp/work", "from cursor")
    _codex_db(tmp_path, "codex-1", "/tmp/work", "generated", name="Codex")
    _agent_row(catalog, berth_id="cursor-row", agent="cursor", session="cursor-1", title="Cursor Prime")
    _agent_row(
        catalog,
        berth_id="codex-row",
        agent="codex",
        session="codex-1",
        title="Codex",
        workspace="ws-codex",
    )
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(id="ws-1", title="Cursor Prime", cwd="/tmp/work", custom=True)
    cmux.workspaces["ws-codex"] = Workspace(id="ws-codex", title="Codex", cwd="/tmp/work", custom=True)
    refresh_identity(catalog, cmux, {})
    assert catalog.require_berth("cursor-row")["title"] == "from cursor"
    assert cmux.workspaces["ws-1"].title == "from cursor"
    assert catalog.require_berth("codex-row")["title"] == "Codex"
    assert cmux.workspaces["ws-codex"].title == "Codex"
    assert cmux.sent == []


def test_parked_cursor_codex_and_antigravity_store_the_name(catalog, tmp_path, monkeypatch):
    _reset()
    _use_agent_roots(monkeypatch, tmp_path)
    meta = _cursor_meta(tmp_path, "cursor-1", "/tmp/work", "old name")
    _codex_db(tmp_path, "codex-1", "/tmp/work", "generated", name="old name")
    _agy_db(tmp_path, "agy-1", "/tmp/work", "old name")
    _agent_row(catalog, berth_id="cursor-row", agent="cursor", session="cursor-1", state="parked")
    _agent_row(catalog, berth_id="codex-row", agent="codex", session="codex-1", state="parked")
    _agent_row(catalog, berth_id="agy-row", agent="antigravity", session="agy-1", state="parked")
    cmux = FakeCmux()
    rename_berth(catalog, cmux, "cursor-row", "Cursor Prime")
    rename_berth(catalog, cmux, "codex-row", "Codex Prime")
    rename_berth(catalog, cmux, "agy-row", "agy prime")
    assert json.loads(meta.read_text(encoding="utf-8"))["title"] == "Cursor Prime"
    import sqlite3

    codex = sqlite3.connect(tmp_path / "state.sqlite")
    assert codex.execute("SELECT name, title FROM threads").fetchone() == ("Codex Prime", "generated")
    codex.close()
    agy = sqlite3.connect(tmp_path / "summaries.db")
    assert agy.execute("SELECT title FROM conversation_summaries").fetchone()[0] == "agy prime"
    agy.close()
    assert cmux.sent == []


def test_discover_uses_the_newest_cursor_chat_and_keeps_grok(catalog, tmp_path, monkeypatch):
    _reset()
    _use_agent_roots(monkeypatch, tmp_path)
    _cursor_meta(tmp_path, "cursor-old", "/tmp/work", "older", updated=1)
    _cursor_meta(tmp_path, "cursor-new", "/tmp/work", "newer", updated=5)
    _agent_row(catalog, berth_id="cursor-row", agent="cursor", session=None, title="~/pat-dom")
    _insert(
        catalog,
        id="grok-1",
        title="kept",
        state="live",
        kind="grok",
        cwd="/tmp/work",
        agent_name="grok",
        agent_session_id="grok-sess",
        cmux_workspace_id="ws-grok",
    )
    from berth.identity import discover_agent_sessions

    changed = discover_agent_sessions(catalog)
    assert changed == ["cursor-row"]
    row = catalog.require_berth("cursor-row")
    assert row["agent_session_id"] == "cursor-new"
    assert json.loads(row["recipe_argv"]) == ["/bin/agent", "--resume", "cursor-new"]
    assert catalog.require_berth("grok-1")["agent_session_id"] == "grok-sess"
    assert catalog.require_berth("grok-1")["title"] == "kept"


def test_discover_does_not_reuse_a_session_id(catalog, tmp_path, monkeypatch):
    _reset()
    _use_agent_roots(monkeypatch, tmp_path)
    _cursor_meta(tmp_path, "cursor-old", "/tmp/work", "older", updated=1)
    _cursor_meta(tmp_path, "cursor-new", "/tmp/work", "newer", updated=5)
    _agent_row(
        catalog,
        berth_id="taken",
        agent="cursor",
        session="cursor-new",
        workspace="ws-taken",
    )
    _agent_row(catalog, berth_id="cursor-row", agent="cursor", session=None)
    from berth.identity import discover_agent_sessions

    discover_agent_sessions(catalog)
    assert catalog.require_berth("cursor-row")["agent_session_id"] == "cursor-old"
    assert catalog.require_berth("taken")["agent_session_id"] == "cursor-new"


def test_discover_codex_uses_the_open_lock_when_the_thread_is_not_stored(catalog, tmp_path, monkeypatch):
    _reset()
    _use_agent_roots(monkeypatch, tmp_path)
    import sqlite3

    conn = sqlite3.connect(tmp_path / "state.sqlite")
    conn.execute(
        """
        CREATE TABLE threads (
            id TEXT PRIMARY KEY,
            cwd TEXT,
            title TEXT,
            name TEXT,
            updated_at INTEGER,
            archived INTEGER
        )
        """
    )
    conn.commit()
    conn.close()
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    (lock_dir / "codex-live.lock").write_text("", encoding="utf-8")
    (lock_dir / ".coordination.lock").write_text("", encoding="utf-8")
    _agent_row(catalog, berth_id="codex-row", agent="codex", session=None)
    from berth.identity import discover_agent_sessions

    discover_agent_sessions(catalog)
    row = catalog.require_berth("codex-row")
    assert row["agent_session_id"] == "codex-live"
    assert json.loads(row["recipe_argv"]) == ["codex", "resume", "codex-live"]


def _claude_lines(tmp_path, session_id: str, cwd: str, *records: tuple[str, str]):
    """Append ``("ai", title)`` or ``("custom", title)`` lines to the transcript."""
    folder = tmp_path / "projects" / re.sub(r"[^A-Za-z0-9]", "-", cwd)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{session_id}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        for kind, title in records:
            if kind == "ai":
                item = {"type": "ai-title", "aiTitle": title, "sessionId": session_id}
            else:
                item = {"type": "custom-title", "customTitle": title, "sessionId": session_id}
            handle.write(json.dumps(item) + "\n")
    return path


def test_a_generated_claude_title_replaces_the_directory_title(catalog, tmp_path, monkeypatch):
    _reset()
    _use_claude_root(monkeypatch, tmp_path)
    _claude_lines(tmp_path, "claude-1", "/tmp/work", ("ai", "Fix the sync bug"))
    _claude_row(catalog, title="~")
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(
        id="ws-1", title="Fix the sync bug", cwd="/tmp/work", custom=False
    )
    refresh_identity(catalog, cmux, {})
    assert catalog.require_berth("claude-1")["title"] == "Fix the sync bug"
    assert cmux.workspaces["ws-1"].custom is False
    assert cmux.sent == []


def test_a_generated_claude_title_renames_a_sidebar_cmux_named(catalog, tmp_path, monkeypatch):
    _reset()
    _use_claude_root(monkeypatch, tmp_path)
    _claude_lines(tmp_path, "claude-1", "/tmp/work", ("ai", "Offer letter review"))
    _claude_row(catalog, title="~/work")
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(
        id="ws-1", title="offer.md", cwd="/tmp/work", custom=False
    )
    refresh_identity(catalog, cmux, {})
    assert catalog.require_berth("claude-1")["title"] == "Offer letter review"
    assert cmux.workspaces["ws-1"].title == "Offer letter review"
    assert cmux.sent == []
    _reset()
    _claude_lines(tmp_path, "claude-1", "/tmp/work", ("ai", "Offer letter and tax review"))
    refresh_identity(catalog, cmux, {})
    assert catalog.require_berth("claude-1")["title"] == "Offer letter and tax review"
    assert cmux.workspaces["ws-1"].title == "Offer letter and tax review"
    assert cmux.sent == []


def test_a_generated_claude_title_keeps_a_chosen_workspace_name(catalog, tmp_path, monkeypatch):
    _reset()
    _use_claude_root(monkeypatch, tmp_path)
    _claude_lines(tmp_path, "claude-1", "/tmp/work", ("ai", "Something generated"))
    _claude_row(catalog, title="Tutor")
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(id="ws-1", title="Tutor", cwd="/tmp/work", custom=True)
    refresh_identity(catalog, cmux, {})
    assert catalog.require_berth("claude-1")["title"] == "Tutor"
    assert cmux.workspaces["ws-1"].title == "Tutor"
    assert cmux.sent == []


def test_a_claude_rename_wins_over_a_later_generated_title(catalog, tmp_path, monkeypatch):
    _reset()
    _use_claude_root(monkeypatch, tmp_path)
    _claude_lines(
        tmp_path,
        "claude-1",
        "/tmp/work",
        ("ai", "first guess"),
        ("custom", "chosen"),
        ("ai", "second guess"),
    )
    _claude_row(catalog, title="~")
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(id="ws-1", title="~", cwd="/tmp/work", custom=False)
    refresh_identity(catalog, cmux, {})
    assert catalog.require_berth("claude-1")["title"] == "chosen"
    assert cmux.workspaces["ws-1"].title == "chosen"
    assert cmux.sent == []


def test_grok_title_is_found_when_the_pane_moved(catalog, tmp_path, monkeypatch):
    _reset()
    root = tmp_path / "grok-sessions"
    monkeypatch.setattr("berth.identity.grok_sessions_root", lambda: root)
    folder = root / "%2Fstarted%2Fhere" / "sess-1"
    folder.mkdir(parents=True)
    (folder / "summary.json").write_text(
        json.dumps({"generated_title": "Fix Slack notifications"}), encoding="utf-8"
    )
    _insert(
        catalog,
        id="grok-1",
        title="~/moved",
        state="live",
        kind="grok",
        cwd="/tmp/moved",
        agent_name="grok",
        agent_session_id="sess-1",
        cmux_workspace_id="ws-1",
    )
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(
        id="ws-1", title="Fix Slack notifications - grok", cwd="/tmp/moved", custom=False
    )
    refresh_identity(catalog, cmux, {})
    assert catalog.require_berth("grok-1")["title"] == "Fix Slack notifications"
    assert cmux.workspaces["ws-1"].title == "Fix Slack notifications"
    assert cmux.sent == []


def test_a_session_renamed_while_closed_is_renamed_once_it_opens_and_its_prompt_is_ready(catalog):
    """2026-10-28: a sleeping Claude renamed in Berth opened under its old name."""
    import json as _json

    from berth.identity import RENAME_ON_OPEN, refresh_identity, rename_on_open
    from berth.ops import rename_berth
    from tests.fakes import FakeCmux
    from tests.support import write_claude_conversation
    from tests.test_state import _insert

    write_claude_conversation("sess-r", "/tmp/r", title="Old name")
    _insert(catalog, id="r", title="Old name", state="parked", kind="other-agent", agent_name="claude",
            agent_session_id="sess-r", cwd="/tmp/r", cmux_workspace_id=None, argv=["claude", "-r", "sess-r"])
    cmux = FakeCmux()
    rename_berth(catalog, cmux, "r", "New name")
    assert _json.loads(catalog.require_berth("r")["attachments"])[RENAME_ON_OPEN] == "New name"
    assert cmux.sent == []  # nothing to type into while it is closed

    # It opens. Claude is still on its folder-trust dialog: nothing is typed, not even by sync_names.
    with catalog.immediate():
        catalog.conn.execute("UPDATE berth SET state = 'live', cmux_workspace_id = 'ws-r' WHERE id = 'r'")
    cmux.workspaces["ws-r"] = Workspace(id="ws-r", title="New name", cwd="/tmp/r", ref="workspace:1", custom=True)
    cmux.screens["ws-r"] = "Do you trust this folder?\n ❯ No, exit\n   Yes, I trust this folder\n Enter to confirm"
    refresh_identity(catalog, cmux, {})
    assert cmux.sent == []

    # The prompt is up: /rename goes in once.
    from tests.test_activity import claude_screen

    cmux.screens["ws-r"] = claude_screen()
    assert rename_on_open(catalog, cmux, list(cmux.workspaces.values())) == ["r"]
    assert cmux.sent == [("ws-r", "/rename New name\n")]
    assert rename_on_open(catalog, cmux, list(cmux.workspaces.values())) == []
    data = _json.loads(catalog.require_berth("r")["attachments"])
    assert RENAME_ON_OPEN not in data and data["name_sent"] == "New name"


def test_renaming_an_open_session_drops_a_pending_rename(catalog):
    import json as _json

    from berth.identity import RENAME_ON_OPEN, with_rename_on_open
    from berth.ops import rename_berth
    from tests.fakes import FakeCmux
    from tests.test_state import _insert

    _insert(catalog, id="s", title="A", state="live", kind="other-agent", agent_name="claude",
            agent_session_id="sess-s", cmux_workspace_id="ws-s",
            attachments=_json.loads(with_rename_on_open("{}", "B")))
    cmux = FakeCmux()
    cmux.workspaces["ws-s"] = Workspace(id="ws-s", title="A", cwd="/tmp", ref="workspace:2")
    rename_berth(catalog, cmux, "s", "C")
    assert RENAME_ON_OPEN not in _json.loads(catalog.require_berth("s")["attachments"])


def test_a_shell_renamed_while_closed_waits_for_nothing(catalog):
    import json as _json

    from berth.identity import RENAME_ON_OPEN
    from berth.ops import rename_berth
    from tests.fakes import FakeCmux
    from tests.test_state import _insert

    _insert(catalog, id="sh", title="term", state="parked", kind="shell", cmux_workspace_id=None)
    rename_berth(catalog, FakeCmux(), "sh", "notes")
    assert RENAME_ON_OPEN not in _json.loads(catalog.require_berth("sh")["attachments"] or "{}")


def test_a_rename_waits_for_an_idle_pane_and_is_typed_once_it_is(catalog, tmp_path, monkeypatch):
    """2026-10-31, Dominik: Berth owns names; /rename goes only into an idle pane, never into a turn or a draft."""
    from tests.test_activity import claude_screen

    _reset()
    _use_claude_root(monkeypatch, tmp_path)
    _claude_log(tmp_path, "claude-1", "/tmp/work", "old name")
    _claude_row(catalog)
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(id="ws-1", title="old name", cwd="/tmp/work", custom=True)
    cmux.screens["ws-1"] = claude_screen(strip="⏵⏵ auto mode on (shift+tab to cycle) · esc to interrupt")  # a turn
    rename_berth(catalog, cmux, "claude-1", "chosen name")
    assert cmux.workspaces["ws-1"].title == "chosen name" and catalog.require_berth("claude-1")["title"] == "chosen name"
    assert cmux.sent == []
    assert "name_sent" not in json.loads(catalog.require_berth("claude-1")["attachments"])
    refresh_identity(catalog, cmux, {})
    assert cmux.sent == []  # still busy
    cmux.screens["ws-1"] = claude_screen(draft="half a thought")  # idle, but someone is typing
    refresh_identity(catalog, cmux, {})
    assert cmux.sent == []
    cmux.screens["ws-1"] = claude_screen()  # idle and empty
    refresh_identity(catalog, cmux, {})
    assert cmux.sent == [("ws-1", "/rename chosen name\n")]
    assert json.loads(catalog.require_berth("claude-1")["attachments"])["name_sent"] == "chosen name"
    refresh_identity(catalog, cmux, {})
    assert cmux.sent == [("ws-1", "/rename chosen name\n")]  # once


def test_a_rename_cmux_refuses_to_type_is_not_counted_as_sent(catalog, tmp_path, monkeypatch):
    from berth.errors import BerthError

    _reset()
    _use_claude_root(monkeypatch, tmp_path)
    _claude_log(tmp_path, "claude-1", "/tmp/work", "old name")
    _claude_row(catalog)
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(id="ws-1", title="old name", cwd="/tmp/work", custom=True)
    refused = []

    def send_text(workspace_id, text):
        refused.append(text)
        raise BerthError("CMUX_REJECTED", "Error: the prompt box has a draft", {})

    monkeypatch.setattr(cmux, "send_text", send_text)
    rename_berth(catalog, cmux, "claude-1", "chosen name")  # no error for the operator: the name is Berth's now
    assert catalog.require_berth("claude-1")["title"] == "chosen name" and refused
    assert "name_sent" not in json.loads(catalog.require_berth("claude-1")["attachments"])
    monkeypatch.setattr(cmux, "send_text", FakeCmux.send_text.__get__(cmux))  # cmux types again
    refresh_identity(catalog, cmux, {})
    assert cmux.sent == [("ws-1", "/rename chosen name\n")]  # the next tick types it


def _stuck(catalog, tmp_path, monkeypatch, screen):
    """A rename Berth typed long ago that never took: the lock says sent, Claude still has the old name."""
    from berth import identity

    _reset()
    identity._stuck_checked.clear()
    _use_claude_root(monkeypatch, tmp_path)
    _claude_log(tmp_path, "claude-1", "/tmp/work", "Trip Planner")
    _claude_row(catalog, title="Travel Agent")
    with catalog.immediate():
        catalog.update_berth("claude-1", attachments=json.dumps(
            {"name_lock": "Travel Agent", "name_seen": "Trip Planner", "name_sent": "Travel Agent",
             "name_sent_at": 0}))
    cmux = FakeCmux()
    cmux.workspaces["ws-1"] = Workspace(id="ws-1", title="Travel Agent", cwd="/tmp/work", custom=True)
    cmux.screens["ws-1"] = screen
    return cmux


def test_our_own_rename_left_in_the_box_is_submitted(catalog, tmp_path, monkeypatch):
    """2026-10-31: /rename typed while Travel Agent started; its Enter was lost and the line sat in the box."""
    from tests.test_activity import claude_screen

    cmux = _stuck(catalog, tmp_path, monkeypatch, claude_screen(draft="/rename Travel Agent"))
    refresh_identity(catalog, cmux, {})
    assert getattr(cmux, "keys", []) == [("ws-1", "enter")] and cmux.sent == []
    assert "rename" in [event["kind"] for event in catalog.events_for("claude-1")]
    refresh_identity(catalog, cmux, {})
    assert cmux.keys == [("ws-1", "enter")]  # once: it waits for Claude to take it


def test_a_rename_that_never_arrived_is_typed_again_once_the_pane_is_idle(catalog, tmp_path, monkeypatch):
    from tests.test_activity import claude_screen

    cmux = _stuck(catalog, tmp_path, monkeypatch, claude_screen())
    refresh_identity(catalog, cmux, {})
    assert cmux.sent == [("ws-1", "/rename Travel Agent\n")]


def test_someone_elses_text_in_the_box_is_never_touched(catalog, tmp_path, monkeypatch):
    from tests.test_activity import claude_screen

    cmux = _stuck(catalog, tmp_path, monkeypatch, claude_screen(draft="half a thought of Dominik's"))
    refresh_identity(catalog, cmux, {})
    assert getattr(cmux, "keys", []) == [] and cmux.sent == []
