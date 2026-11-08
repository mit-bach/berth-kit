"""Compaction done by Berth, checked by Claude's own record (2026-10-30).

STUDIO Orch pasted "/compact <note>" into a fork; Claude sent it as a plain
message, and the fork built the thing its note described. These tests hold
the two routes that work, and the checks that stop the ones that don't.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

import berth.compact
import berth.send
from berth.compact import boundaries, headless, in_pane, plan, run_job, start
from berth.errors import BerthError
from berth.mcp import Api, ToolError
from berth.model import Workspace
from tests.fakes import FakeCmux
from tests.support import write_claude_conversation
from tests.test_activity import claude_screen
from tests.test_state import _insert

READY = claude_screen()
CWD = "/tmp/tutor"
NOTE = "TASK: next you will write one line about the codename, HERON.\n\nKEEP OUT: the decoy, because it misleads."


@pytest.fixture(autouse=True)
def no_pauses(monkeypatch):
    for name in ("SETTLE_SECONDS", "CHECK_SECONDS", "POLL_SECONDS"):
        monkeypatch.setattr(berth.send, name, 0)
    monkeypatch.setattr(berth.compact, "POLL", 0)


def _boundary(session_id: str, cwd: str = CWD, pre: int = 5000, post: int = 900) -> None:
    from berth.identity import claude_transcript

    path = claude_transcript(session_id, cwd)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "system", "subtype": "compact_boundary", "timestamp": stamp,
                                 "compactMetadata": {"trigger": "manual", "preTokens": pre, "postTokens": post}}) + "\n")


def _claude(catalog, cmux, berth_id="worker", *, state="live", screen=READY):
    workspace = f"ws-{berth_id}"
    write_claude_conversation(f"s-{berth_id}", CWD)
    _insert(catalog, id=berth_id, title=berth_id.title(), kind="other-agent", agent_name="claude",
            agent_session_id=f"s-{berth_id}", state=state, cwd=CWD,
            cmux_workspace_id=workspace if state == "live" else None,
            argv=["claude", "-r", f"s-{berth_id}"], display="claude -r")
    if state == "live":
        cmux.workspaces[workspace] = Workspace(id=workspace, title=berth_id.title(), cwd=CWD)
        cmux.screens[workspace] = screen
    return workspace


class CompactingCmux(FakeCmux):
    """Claude in a pane: the typed /compact and the pasted note show in the box; Enter records a boundary."""

    def __init__(self, session_id: str, *, takes: bool = True):
        super().__init__()
        self.session_id, self.takes, self.box = session_id, takes, ""

    def send_text(self, workspace_id, text):
        super().send_text(workspace_id, text)
        self.box += text

    def paste_text(self, workspace_id, text, *, force=False):
        self.forced = force
        super().paste_text(workspace_id, text, force=force)
        self.box += "[Pasted text #1 +5 lines]"

    def read_screen(self, workspace_id, *, lines=120, scrollback=True):
        if self.box:
            return "─" * 80 + "\n❯ " + self.box + "\n" + "─" * 80 + "\n  ⏵⏵ auto mode on\n"
        return super().read_screen(workspace_id, lines=lines, scrollback=scrollback)

    def send_key(self, workspace_id, key):
        super().send_key(workspace_id, key)
        if key == "enter" and self.box.startswith("/compact") and self.takes:
            self.box = ""
            _boundary(self.session_id)
        elif key == "ctrl-u":
            self.box = ""


def test_in_the_pane_compact_is_typed_the_note_pasted_behind_it_and_claudes_record_is_the_proof(catalog):
    cmux = CompactingCmux("s-worker")
    workspace = _claude(catalog, cmux)
    result = in_pane(cmux, catalog.require_berth("worker"), NOTE, settle=lambda s: None)
    assert cmux.sent == [(workspace, "/compact ")]  # typed: Claude runs only a typed slash command
    assert cmux.pasted == [(workspace, NOTE)] and cmux.forced is True  # behind our own typed text
    assert cmux.keys == [(workspace, "enter")]
    assert result["mode"] == "pane" and result["pre_tokens"] == 5000 and result["post_tokens"] == 900


def test_someone_typing_stops_it_before_anything_is_placed(catalog):
    cmux = CompactingCmux("s-worker")
    _claude(catalog, cmux)

    def refuse(workspace_id, text):
        raise BerthError("CMUX_REJECTED", "Error: paste: the agent prompt in X holds text someone is typing", {})

    cmux.send_text = refuse
    with pytest.raises(BerthError) as busy:
        in_pane(cmux, catalog.require_berth("worker"), NOTE, settle=lambda s: None)
    assert busy.value.code == "BUSY" and busy.value.details["refused"] == "typing"
    assert getattr(cmux, "pasted", []) == [] and cmux.keys == []


def test_a_box_that_does_not_read_compact_is_cleared_and_never_entered(catalog):
    cmux = CompactingCmux("s-worker")
    _claude(catalog, cmux)
    original = cmux.send_text
    cmux.send_text = lambda workspace_id, text: (original(workspace_id, text), setattr(cmux, "box", "something else"))
    with pytest.raises(BerthError) as failed:
        in_pane(cmux, catalog.require_berth("worker"), NOTE, settle=lambda s: None)
    assert failed.value.code == "COMPACT_FAILED"
    assert [key for _, key in cmux.keys] == ["ctrl-u"]  # cleared; Enter never pressed


def test_no_record_means_no_compaction_however_the_screen_looks(catalog):
    cmux = CompactingCmux("s-worker", takes=False)
    _claude(catalog, cmux)
    ticks = iter(range(10_000))
    with pytest.raises(BerthError) as failed:
        in_pane(cmux, catalog.require_berth("worker"), NOTE, settle=lambda s: None, clock=lambda: next(ticks),
                timeout=30)
    assert failed.value.code == "COMPACT_FAILED"


def test_headless_runs_claude_p_with_the_command_and_checks_the_record(catalog):
    write_claude_conversation("s-closed", CWD)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        _boundary("s-closed")
        return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    result = headless("s-closed", CWD, NOTE, model="opus", run=run)
    argv, kwargs = calls[0]
    # No hooks in the run: the reorientation hook started a whole turn inside it (2026-11-06, UNI Orch's fork).
    assert argv[1:] == ["-p", "--settings", '{"disableAllHooks": true}', "--model", "opus", "-r", "s-closed",
                        "/compact " + NOTE]
    assert kwargs["env"]["BERTH_COMPACTING"] == "1"
    assert kwargs["cwd"] == CWD and kwargs["stdin"] is not None  # nothing of ours is read as the prompt
    assert not any(key.startswith(("CMUX", "CLAUDE")) for key in kwargs["env"])
    assert result["mode"] == "headless" and result["post_tokens"] == 900
    with pytest.raises(BerthError) as failed:
        headless("s-closed", CWD, NOTE, run=lambda argv, **kw: type("R", (), {"returncode": 0, "stdout": "",
                                                                                  "stderr": ""})())
    assert failed.value.code == "COMPACT_FAILED"



def test_the_reorientation_hook_is_silent_inside_a_berth_compaction(catalog, capsys, monkeypatch):
    from berth.cli import main

    monkeypatch.setenv("BERTH_COMPACTING", "1")
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO('{"session_id": "s-any"}'))
    assert main(["--catalog", str(catalog.path), "orchestrator", "me", "--hook"]) == 0
    assert capsys.readouterr().out == ""


def test_plan_refuses_what_cannot_be_compacted(catalog):
    cmux = FakeCmux()
    _claude(catalog, cmux)
    _insert(catalog, id="grok", title="Grok", kind="grok", agent_name="grok", agent_session_id="g", state="parked",
            cmux_workspace_id=None)
    for row, note, code in (("grok", NOTE, "KIND_UNSAFE"), ("worker", "", "BAD_ARGUMENT"),
                            ("worker", "/compact keep it", "BAD_ARGUMENT")):
        with pytest.raises(BerthError) as refused:
            plan(catalog, row, note=note)
        assert refused.value.code == code
    assert plan(catalog, "worker", note=NOTE)["mode"] == "pane"
    assert plan(catalog, "worker", note=NOTE, caller="ws-worker")["own"] is True


def test_fork_and_compact_compacts_before_the_pane_opens_then_sends_the_job(catalog, monkeypatch):
    monkeypatch.setattr("berth.place.CMUX_CLAUDE_WRAPPER", "/nonexistent")
    cmux = FakeCmux()
    _claude(catalog, cmux, "source")
    job = plan(catalog, "source", note=NOTE, prompt="Build the launcher.", fork=True, title="Google SA", cmux=cmux)
    fork = catalog.require_berth(job["berth_id"])
    assert fork["state"] == "parked" and cmux.created == []  # no pane yet

    def run(argv, **kwargs):
        assert argv[argv.index("-r") + 1] == "s-source" and "--fork-session" in argv
        fork_id = argv[argv.index("--session-id") + 1]
        write_claude_conversation(fork_id, CWD)
        _boundary(fork_id)
        return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    original_create = cmux.create_workspace

    def create(**kwargs):
        made = original_create(**kwargs)
        cmux.screens[made.id] = READY
        return made

    cmux.create_workspace = create
    path = berth.compact.jobs_dir(catalog.path) / "job.json"
    path.write_text(json.dumps({"job": "j1", **job}))
    result = run_job(catalog, path, cmux=cmux, programs_fn=dict, settle=lambda s: None, run=run)
    assert result["state"] == "done" and result["mode"] == "headless"
    fork = catalog.require_berth(job["berth_id"])
    assert fork["state"] == "live" and boundaries(fork["agent_session_id"], CWD)
    assert getattr(cmux, "pasted", [])[-1][1] == "Build the launcher."
    assert boundaries("s-source", CWD) == []  # the original is untouched
    kinds = [event["kind"] for event in catalog.events_for(job["berth_id"])]
    assert "compact_done" in kinds and "prompt_sent" in kinds
    assert json.loads(fork["attachments"])["compaction"]["state"] == "done"


def test_the_mcp_starts_it_and_returns_at_once(catalog, monkeypatch):
    cmux = FakeCmux()
    _claude(catalog, cmux)
    spawned = []
    monkeypatch.setattr(berth.compact.subprocess, "Popen",
                        lambda argv, **kw: spawned.append(argv) or type("P", (), {"pid": 4242})())
    api = Api(catalog.path, cmux=cmux, caller_workspace="ws-orch")
    out = api("send", {"session": "worker", "compact": NOTE, "text": "Now the next task."})
    assert out["compaction"]["state"] == "queued" and "typing /compact" in out["compaction"]["doing"]
    assert spawned and "compact-run" in spawned[0]
    shown = api("session", {"session": "worker"})["compaction"]
    assert shown["state"] == "queued" and shown["mode"] == "pane"
    with pytest.raises(ToolError) as twice:
        monkeypatch.setattr(berth.compact, "_pid_alive", lambda pid: True)
        api("send", {"session": "worker", "compact": NOTE})
    assert twice.value.code == "IN_PROGRESS"
    with pytest.raises(ToolError):
        api("send", {"session": "worker", "compact": NOTE, "when": "now"})


def test_the_command_line_compacts_in_the_foreground(catalog, monkeypatch, capsys):
    from berth.cli import main

    write_claude_conversation("s-closed", CWD)
    _insert(catalog, id="closed", title="Closed", kind="other-agent", agent_name="claude",
            agent_session_id="s-closed", state="parked", cwd=CWD, cmux_workspace_id=None,
            argv=["claude", "-r", "s-closed"], display="claude -r")

    def run(argv, **kwargs):
        _boundary("s-closed")
        return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(berth.compact.subprocess, "run", run)
    assert main(["--catalog", str(catalog.path), "--json", "compact", "closed", "--note", NOTE]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["state"] == "done" and out["mode"] == "headless"
    assert catalog.require_berth("closed")["state"] == "parked"  # compacted while it stayed closed


def test_claudes_refusal_ends_the_wait_with_its_own_words(catalog):
    """2026-10-30: a /compact with nothing new since the last one got "Not enough messages to compact." and no boundary."""
    from berth.identity import claude_transcript

    cmux = CompactingCmux("s-worker", takes=False)
    _claude(catalog, cmux)
    original = cmux.send_key

    def refuse(workspace_id, key):
        original(workspace_id, key)
        if key == "enter":
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            with claude_transcript("s-worker", CWD).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"type": "system", "subtype": "local_command", "timestamp": stamp,
                                         "content": "<local-command-stdout>Not enough messages to compact."
                                                    "</local-command-stdout>"}) + "\n")

    cmux.send_key = refuse
    ticks = iter(range(10_000))
    with pytest.raises(BerthError) as failed:
        in_pane(cmux, catalog.require_berth("worker"), NOTE, settle=lambda s: None, clock=lambda: next(ticks),
                timeout=1_000_000)
    assert failed.value.code == "COMPACT_FAILED" and "Not enough messages" in failed.value.message


def test_compacting_your_own_pane_from_its_shell_never_waits_on_itself(catalog, monkeypatch, capsys):
    """The command runs in that pane, so the pane is not ready until it returns: it must go to a detached worker."""
    from berth.cli import main

    cmux = FakeCmux()
    _claude(catalog, cmux, "me")
    spawned = []
    monkeypatch.setattr(berth.compact.subprocess, "Popen",
                        lambda argv, **kw: spawned.append(argv) or type("P", (), {"pid": 4242})())
    monkeypatch.setenv("CMUX_WORKSPACE_ID", "ws-me")
    assert main(["--catalog", str(catalog.path), "--json", "compact", "me", "--note", NOTE]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["state"] == "queued" and spawned and "berth log me" in out["follow"]


def test_fork_and_compact_into_another_directory_is_one_step(catalog, tmp_path):
    """HOMESTEAD re-homes a fork into its unit: the fork row takes the new directory before it is compacted there."""
    cmux = FakeCmux()
    _claude(catalog, cmux, "source")
    unit = tmp_path / "Unit"
    unit.mkdir()
    job = plan(catalog, "source", note=NOTE, fork=True, title="Unit agent", cmux=cmux, cwd=str(unit))
    fork = catalog.require_berth(job["berth_id"])
    assert fork["cwd"] == str(unit) and fork["state"] == "parked"
    assert catalog.require_berth("source")["cwd"] == CWD
    with pytest.raises(BerthError):
        plan(catalog, "source", note=NOTE, cwd=str(unit))  # cwd without fork: that is update cwd
    with pytest.raises(BerthError):
        plan(catalog, "source", note=NOTE, fork=True, cwd=str(tmp_path / "missing"))
