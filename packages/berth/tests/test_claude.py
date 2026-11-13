"""Claude sessions follow Claude Code's own records, and forks stay filed."""

from __future__ import annotations

import json
import os

import pytest

from berth import claude_sync, machine
from berth.errors import BerthError
from berth.model import Workspace
from berth.ops import fork_berth, mkdir_path
from berth.state import play_berth
from tests.fakes import FakeCmux
from tests.support import write_claude_conversation, write_claude_process
from tests.test_state import _insert

CWD = "/tmp/tutor"
PANE_PID = os.getpid()
JOB_PID = os.getppid()


def _cmux_with_pane(workspace_id="ws-1", title="Tutor", pids=(PANE_PID,)):
    cmux = FakeCmux()
    cmux.workspaces[workspace_id] = Workspace(id=workspace_id, title=title, cwd=CWD, ref="workspace:1", custom=True)
    lines = [
        "0\t0\t1\tworkspace\tworkspace:1\twindow:1\t" + title,
        "0\t0\t1\tpane\tpane:1\tworkspace:1\t",
        "0\t0\t1\tsurface\tsurface:1\tpane:1\t" + title,
    ]
    lines += [f"0\t0\t1\tprocess\t{pid}\tsurface:1\tclaude" for pid in pids]
    cmux.top_processes = lambda: "\n".join(lines)
    return cmux


def _claude_row(catalog, berth_id, session_id, *, state="live", folder=None, title="Tutor", workspace="ws-1"):
    return _insert(
        catalog, id=berth_id, title=title, state=state, kind="other-agent", agent_name="claude",
        agent_session_id=session_id, cwd=CWD, cmux_workspace_id=workspace if state == "live" else None,
        folder_id=folder or catalog.ensure_inbox(), argv=["claude", "-r", session_id or ""],
        display=f"claude -r {session_id}",
    )


def test_a_stale_hook_id_is_corrected_to_the_conversation_the_pane_is_on(catalog):
    tutors = mkdir_path(catalog, "Tutors")["id"]
    write_claude_conversation("real-old", CWD, root="family")
    _claude_row(catalog, "filed", "real-old", state="parked", folder=tutors, title="Old tutor")
    write_claude_conversation("current", CWD, root="family")
    _claude_row(catalog, "pane-row", "never-written")
    write_claude_process(PANE_PID, "current", CWD)
    cmux = _cmux_with_pane()
    actions = claude_sync.follow(catalog, cmux, list(cmux.workspaces.values()))
    row = catalog.require_berth("pane-row")
    assert [item["action"] for item in actions] == ["corrected"]
    assert row["agent_session_id"] == "current"
    assert json.loads(row["recipe_argv"])[-2:] == ["-r", "current"]
    assert row["folder_id"] == tutors  # filed with its family, not Inbox


def test_a_pane_that_moves_to_another_real_conversation_parks_the_old_one(catalog):
    tutors = mkdir_path(catalog, "Tutors")["id"]
    write_claude_conversation("first", CWD, root="family")
    write_claude_conversation("second", CWD, root="family")
    _claude_row(catalog, "pane-row", "first", folder=tutors)
    write_claude_process(PANE_PID, "second", CWD)
    cmux = _cmux_with_pane()
    claude_sync.follow(catalog, cmux, list(cmux.workspaces.values()))
    old = catalog.require_berth("pane-row")
    assert old["state"] == "parked" and old["park_reason"] == "replaced"
    new = catalog.conn.execute("SELECT * FROM berth WHERE agent_session_id = 'second'").fetchone()
    assert new["state"] == "live" and new["cmux_workspace_id"] == "ws-1" and new["folder_id"] == tutors


def test_a_claude_run_by_the_panes_own_claude_is_not_the_pane(catalog):
    """2026-10-29: ``claude -p`` checks run from Berth Developer's Bash tool flipped its row to the scratch conversation."""
    write_claude_conversation("mine", CWD, root="family")
    write_claude_conversation("scratch", "/tmp/scratch", root="other")
    _claude_row(catalog, "pane-row", "mine")
    write_claude_process(PANE_PID, "mine", CWD)
    write_claude_process(JOB_PID, "scratch", "/tmp/scratch")
    from berth import claude

    path = claude.sessions_dir() / f"{JOB_PID}.json"
    record = json.loads(path.read_text())
    path.write_text(json.dumps({**record, "updatedAt": 2}))  # newer than the pane's own Claude
    cmux = _cmux_with_pane()
    lines = cmux.top_processes().splitlines()
    lines.append(f"0\t0\t1\tprocess\t{JOB_PID}\t{PANE_PID}\tclaude")  # a child of the pane's Claude
    cmux.top_processes = lambda: "\n".join(lines)
    running = claude.processes()
    assert claude.pane_conversation({PANE_PID, JOB_PID}, running).session_id == "scratch"  # the old rule
    assert claude_sync.follow(catalog, cmux, list(cmux.workspaces.values())) == []
    row = catalog.require_berth("pane-row")
    assert (row["state"], row["agent_session_id"]) == ("live", "mine")
    assert catalog.conn.execute("SELECT count(*) FROM berth WHERE agent_session_id = 'scratch'").fetchone()[0] == 0


def test_a_pane_that_resumes_a_parked_conversation_relinks_that_row(catalog):
    write_claude_conversation("first", CWD)
    write_claude_conversation("second", CWD)
    _claude_row(catalog, "pane-row", "first")
    _claude_row(catalog, "parked-row", "second", state="parked", title="Second")
    write_claude_process(PANE_PID, "second", CWD)
    cmux = _cmux_with_pane()
    claude_sync.follow(catalog, cmux, list(cmux.workspaces.values()))
    assert catalog.require_berth("pane-row")["state"] == "parked"
    relinked = catalog.require_berth("parked-row")
    assert relinked["state"] == "live" and relinked["cmux_workspace_id"] == "ws-1"
    assert len(catalog.all_berths()) == 2


def test_a_background_fork_gets_a_row_in_its_familys_folder_and_play_attaches(catalog):
    tutors = mkdir_path(catalog, "Tutors")["id"]
    write_claude_conversation("parent", CWD, root="family")
    _claude_row(catalog, "parent-row", "parent", state="parked", folder=tutors)
    write_claude_conversation("bg-fork", CWD, root="family")
    write_claude_process(JOB_PID, "bg-fork", CWD, kind="bg", name="Tutor ⑂", job_id="bgjob")
    cmux = _cmux_with_pane(pids=())
    actions = claude_sync.follow(catalog, cmux, list(cmux.workspaces.values()))
    assert [item["action"] for item in actions] == ["background"]
    row = catalog.conn.execute("SELECT * FROM berth WHERE agent_session_id = 'bg-fork'").fetchone()
    assert row["park_reason"] == "background" and row["folder_id"] == tutors and row["title"] == "Tutor ⑂"
    stopped = []
    import berth.claude as claude_mod

    original = claude_mod.stop_job
    claude_mod.stop_job = lambda job, **_: stopped.append(job.job_id) or True
    try:
        played = play_berth(catalog, cmux, row["id"], focus=False)
    finally:
        claude_mod.stop_job = original
    assert stopped == ["bgjob"]
    assert played["state"] == "live"
    assert "-r bg-fork -n " in cmux.created[-1]["command"]  # named on open: no name a person gave it
    # A second tick does not add it again.
    assert claude_sync.follow(catalog, cmux, list(cmux.workspaces.values())) == []


def test_play_refuses_a_claude_id_with_no_conversation(catalog):
    _claude_row(catalog, "ghost", "never-written", state="parked")
    cmux = FakeCmux()
    with pytest.raises(BerthError) as caught:
        play_berth(catalog, cmux, "ghost", focus=False)
    assert caught.value.code == "SESSION_MISSING"
    assert cmux.created == []
    assert catalog.require_berth("ghost")["state"] == "parked"


def test_play_runs_in_the_transcripts_own_directory(catalog):
    write_claude_conversation("moved", CWD)
    _insert(catalog, id="r", title="t", state="parked", kind="other-agent", agent_name="claude",
            agent_session_id="moved", cwd="/Users/somewhere-else", cmux_workspace_id=None,
            argv=["claude", "-r", "moved"], display="claude -r moved")
    cmux = FakeCmux()
    play_berth(catalog, cmux, "r", focus=False)
    assert cmux.created[-1]["cwd"] == CWD


def test_fork_opens_next_to_the_original_under_an_id_berth_chose(catalog):
    tutors = mkdir_path(catalog, "Tutors")["id"]
    write_claude_conversation("parent", CWD, root="family")
    _claude_row(catalog, "parent-row", "parent", state="parked", folder=tutors, title="Tutor")
    cmux = _cmux_with_pane(pids=())
    record = fork_berth(catalog, cmux, "parent-row", focus=False)
    fork_id = record["agent_session_id"]
    assert record["title"] == "Tutor ⑂" and record["folder_id"] == tutors and record["state"] == "live"
    assert record["attachments"]["fork_of"] == "parent"
    assert fork_id and fork_id != "parent"
    command = cmux.created[-1]["command"]
    assert f"-r parent --fork-session --session-id {fork_id} -n" in command
    assert catalog.require_berth("parent-row")["state"] == "parked"
    # Claude starts under the id Berth chose, so the watcher has nothing to correct.
    new_ws = cmux.created[-1]["id"]
    cmux.workspaces = {new_ws: Workspace(id=new_ws, title="Tutor ⑂", cwd=CWD, ref="workspace:1")}
    cmux.top_processes = _cmux_with_pane(new_ws, "Tutor ⑂").top_processes
    write_claude_conversation(fork_id, CWD, root="family")
    write_claude_process(PANE_PID, fork_id, CWD)
    assert claude_sync.follow(catalog, cmux, list(cmux.workspaces.values())) == []
    assert catalog.require_berth(record["id"])["agent_session_id"] == fork_id


def test_a_parked_fork_resumes_itself_or_forks_its_parent_again(catalog):
    write_claude_conversation("parent", CWD, root="family")
    write_claude_conversation("kept", CWD, root="family")
    _claude_row(catalog, "kept-row", "kept", state="parked", title="Kept ⑂")
    _claude_row(catalog, "lost-row", "lost", state="parked", title="Lost ⑂")
    for berth_id in ("kept-row", "lost-row"):
        with catalog.immediate():
            catalog.update_berth(berth_id, attachments=json.dumps({"fork_of": "parent"}))
    cmux = FakeCmux()
    play_berth(catalog, cmux, "kept-row", focus=False)
    assert cmux.created[-1]["command"].endswith("-r kept -n 'Kept ⑂'")
    play_berth(catalog, cmux, "lost-row", focus=False)
    assert "-r parent --fork-session --session-id lost -n 'Lost ⑂'" in cmux.created[-1]["command"]


def test_a_fork_whose_id_has_an_empty_transcript_gets_a_new_id(catalog):
    from berth.identity import claude_projects_root

    write_claude_conversation("parent", CWD, root="family")
    folder = claude_projects_root() / CWD.replace("/", "-")
    (folder / "empty.jsonl").write_text('{"type": "summary"}\n', encoding="utf-8")
    _claude_row(catalog, "r", "empty", state="parked", title="Empty ⑂")
    with catalog.immediate():
        catalog.update_berth("r", attachments=json.dumps({"fork_of": "parent"}))
    cmux = FakeCmux()
    play_berth(catalog, cmux, "r", focus=False)
    fresh = catalog.require_berth("r")["agent_session_id"]
    assert fresh != "empty"
    assert f"--fork-session --session-id {fresh} -n" in cmux.created[-1]["command"]


def test_fork_refuses_anything_but_a_real_conversation(catalog):
    _insert(catalog, id="shell", title="sh", state="parked", kind="shell", cmux_workspace_id=None)
    _claude_row(catalog, "ghost", "never-written", state="parked")
    _insert(catalog, id="blank-grok", title="g", state="parked", kind="grok", agent_name="grok",
            agent_session_id=None, cmux_workspace_id=None, argv=["grok"], display="grok")
    for berth_id, code in (("shell", "KIND_UNSAFE"), ("ghost", "SESSION_MISSING"), ("blank-grok", "SESSION_MISSING")):
        with pytest.raises(BerthError) as caught:
            fork_berth(catalog, FakeCmux(), berth_id)
        assert caught.value.code == code
    assert machine is not None


def test_check_reports_a_parked_claude_row_with_no_conversation(catalog):
    from berth import claude
    from berth.invariant import check

    claude.sessions_dir().mkdir(parents=True, exist_ok=True)
    _claude_row(catalog, "ghost", "never-written", state="parked")
    write_claude_conversation("real", CWD)
    _claude_row(catalog, "fine", "real", state="parked", title="Fine")
    rules = [(item.rule, item.berth_id) for item in check(catalog, [])]
    assert rules == [("dead_session", "ghost")]


def test_an_in_app_fork_gets_a_clean_name_and_a_running_note(catalog):
    write_claude_conversation("bg-fork", CWD, root="family")
    write_claude_process(JOB_PID, "bg-fork", CWD, kind="bg",
                         name="Tutor Prime ⑂ You are a new fork of Tutor Prime. Your name is", job_id="bgjob")
    cmux = _cmux_with_pane(pids=())
    claude_sync.follow(catalog, cmux, list(cmux.workspaces.values()))
    row = catalog.conn.execute("SELECT * FROM berth WHERE agent_session_id = 'bg-fork'").fetchone()
    assert row["title"] == "Tutor Prime ⑂"
    assert row["note"] == claude_sync.RUNNING_NOTE
    from berth import claude
    from berth.invariant import check

    assert [item.rule for item in check(catalog, [])] == ["background_job"]

    (claude.sessions_dir() / f"{JOB_PID}.json").unlink()
    claude_sync.follow(catalog, cmux, list(cmux.workspaces.values()))
    assert catalog.require_berth(row["id"])["note"] == claude_sync.STOPPED_NOTE


def test_a_parked_row_whose_conversation_is_gone_says_so_and_a_users_note_stays(catalog):
    from berth import claude
    from berth.claude_sync import GONE_NOTE, _gone_notes

    claude.sessions_dir().mkdir(parents=True, exist_ok=True)
    _claude_row(catalog, "ghost", "deleted-transcript", state="parked")
    _claude_row(catalog, "mine", "also-gone", state="parked", title="Mine")
    with catalog.immediate():
        catalog.update_berth("mine", note="keep this")
    _gone_notes(catalog)
    assert catalog.require_berth("ghost")["note"] == GONE_NOTE
    assert catalog.require_berth("mine")["note"] == "keep this"
    write_claude_conversation("deleted-transcript", CWD)
    _gone_notes(catalog)
    assert catalog.require_berth("ghost")["note"] == ""


def test_a_claude_session_reopens_under_its_berth_title_unless_a_person_already_named_it(catalog):
    """2026-10-30: a session whose only title was Claude's ai-title came back from a move as "tooling-1e"."""
    import json as _json

    from berth.identity import claude_transcript

    write_claude_conversation("named-by-ai", CWD)
    with claude_transcript("named-by-ai", CWD).open("a", encoding="utf-8") as handle:
        handle.write(_json.dumps({"type": "ai-title", "aiTitle": "Diagram MCP setup", "sessionId": "named-by-ai"}) + "\n")
    _claude_row(catalog, "ai", "named-by-ai", state="parked", title="Diagram MCP setup")
    cmux = FakeCmux()
    play_berth(catalog, cmux, "ai", focus=False)
    assert cmux.created[-1]["command"].endswith("-r named-by-ai -n 'Diagram MCP setup'")
    write_claude_conversation("named-by-person", CWD, title="Tutor")
    _claude_row(catalog, "person", "named-by-person", state="parked", title="Tutor", workspace="ws-2")
    play_berth(catalog, cmux, "person", focus=False)
    assert cmux.created[-1]["command"].endswith("-r named-by-person")  # its name is already its title


def test_renaming_to_claudes_own_ai_title_still_names_the_session(catalog, monkeypatch):
    import json as _json

    from berth.identity import claude_transcript
    from berth.ops import rename_berth

    write_claude_conversation("live-ai", CWD)
    with claude_transcript("live-ai", CWD).open("a", encoding="utf-8") as handle:
        handle.write(_json.dumps({"type": "ai-title", "aiTitle": "Diagram MCP setup", "sessionId": "live-ai"}) + "\n")
    _claude_row(catalog, "live", "live-ai", title="tooling-1e")
    sent = []
    monkeypatch.setattr("berth.identity.send_session_rename",
                        lambda cmux, ws, sid, title, force=False, **_: sent.append(title) or True)
    rename_berth(catalog, _cmux_with_pane(), "live", "Diagram MCP setup")
    assert sent == ["Diagram MCP setup"]  # Claude's ai-title is not a name: /rename is typed


def test_an_sdk_claude_carrying_the_panes_environment_is_never_the_pane(catalog, monkeypatch):
    """2026-11-08: Lee Course Builder's scripts ran Agent SDK Claudes (entrypoint sdk-cli) in its scratchpad, outside
    its own Claude's tree; each, as the newest, parked the real conversation as "replaced": 21 rows in ten hours."""
    write_claude_conversation("mine", CWD, root="family")
    write_claude_conversation("sdk-run", "/tmp/scratchpad/run", root="other")
    _claude_row(catalog, "pane-row", "mine")
    write_claude_process(PANE_PID, "mine", CWD)
    write_claude_process(JOB_PID, "sdk-run", "/tmp/scratchpad/run")
    from berth import claude

    path = claude.sessions_dir() / f"{JOB_PID}.json"
    record = json.loads(path.read_text())
    path.write_text(json.dumps({**record, "updatedAt": 2, "entrypoint": "sdk-cli"}))  # newer, and not nested
    running = claude.processes()
    assert running[JOB_PID].entrypoint == "sdk-cli"
    assert claude.pane_conversation({PANE_PID, JOB_PID}, running).session_id == "mine"
    cmux = _cmux_with_pane()
    lines = cmux.top_processes().splitlines()
    lines.append(f"0\t0\t1\tprocess\t{JOB_PID}\t1\tclaude")  # its parent is launchd: the old nesting rule missed it
    cmux.top_processes = lambda: "\n".join(lines)
    assert claude_sync.follow(catalog, cmux, list(cmux.workspaces.values())) == []
    assert catalog.require_berth("pane-row")["agent_session_id"] == "mine"


def test_an_adopted_claude_row_records_the_folder_its_claude_runs_on(monkeypatch):
    """2026-11-08, the Accounts Agent: rows adopted from a HOMESTEAD pane said ~/.claude while it ran on homestead."""
    from berth import configdir, proc

    monkeypatch.setattr(proc, "environ_value", lambda pid, name: "/Users/owner/.claude-homestead" if pid == 42 else None)
    assert configdir.of_process(42) == "/Users/owner/.claude-homestead"
    assert configdir.of_process(43) is None and configdir.of_process(None) is None
    monkeypatch.setattr(proc, "environ_value", lambda pid, name: str(configdir.default_root()))
    assert configdir.of_process(42) is None  # ~/.claude is the default: nothing to record
    row = claude_sync._row(None, title="t", folder="f", cwd="/x", session_id="s", argv=[], state="live",
                           config_dir="/Users/owner/.claude-homestead")
    assert json.loads(row["attachments"]) == {"config_dir": "/Users/owner/.claude-homestead"}
