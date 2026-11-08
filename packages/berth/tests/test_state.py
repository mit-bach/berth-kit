"""State machine without cmux. The socket is not opened."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from berth.adopt import adopt_known, adopt_workspace, build_record, merge_duplicate_sessions
from berth.db import BERTH_COLUMNS, FOLDER_COLUMNS, public_berth
from berth.errors import BerthError, QueryAmbiguous
from berth.model import Folder, Group, HookRecord, Surface, Workspace
from berth.ops import (
    archive_folder,
    delete_berth,
    mkdir_path,
    rename_berth,
    resolve_query,
    rmdir_folder,
)
from berth.place import (
    GROK_BINARY,
    agent_is_busy,
    choose_folder_id,
    grok_exec_text,
    grok_resume_argv,
    other_agent_exec_text,
    other_agent_resume_argv,
    session_on_disk,
)
from berth.render import kind_word
from berth.state import pause_berth, play_berth, reconcile
from berth.timeutil import utc_now
from tests.fakes import FakeCmux


def _stamp(minutes_ago: int = 0) -> str:
    moment = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _insert(db, **overrides) -> str:
    now = utc_now()
    fields = {
        "id": overrides.get("id", "berth-1"),
        "folder_id": overrides.get("folder_id", db.ensure_inbox()),
        "title": overrides.get("title", "cedar"),
        "kind": overrides.get("kind", "shell"),
        "state": overrides.get("state", "live"),
        "protect": overrides.get("protect", 0),
        "cwd": overrides.get("cwd", "/tmp"),
        "note": overrides.get("note", ""),
        "recipe_argv": json.dumps(overrides.get("argv", ["sleep", "3600"])),
        "recipe_display": overrides.get("display", "sleep 3600"),
        "agent_name": overrides.get("agent_name"),
        "agent_session_id": overrides.get("agent_session_id"),
        "agent_version": overrides.get("agent_version"),
        "cmux_workspace_id": overrides.get("cmux_workspace_id", "ws-1"),
        "cmux_surface_id": overrides.get("cmux_surface_id"),
        "cmux_pid": overrides.get("cmux_pid"),
        "attachments": json.dumps(overrides.get("attachments", {})),
        "excerpt": overrides.get("excerpt", ""),
        "excerpt_at": overrides.get("excerpt_at"),
        "created_at": overrides.get("created_at", now),
        "updated_at": overrides.get("updated_at", now),
        "parked_at": overrides.get("parked_at"),
        "played_at": overrides.get("played_at"),
    }
    with db.immediate():
        db.insert_berth(fields)
    return fields["id"]


def _hook(**overrides) -> HookRecord:
    values = dict(
        agent_name="grok",
        session_id="sess-1",
        workspace_id="ws-1",
        surface_id="surface-1",
        pid=4242,
        cwd="/tmp",
        runtime_status="running",
        last_body="tool call in progress",
        executable="/usr/local/bin/grok",
        arguments=["grok", "--always-approve", "--reasoning-effort", "xhigh"],
        launcher="grok",
        updated_at=None,
    )
    values.update(overrides)
    return HookRecord(**values)


RULE = "─" * 100
IDLE_SCREEN = f"line one\nline two\n{RULE[:-10]} cedar ─\n❯ \n{RULE}\n  ⏵⏵ auto mode on (shift+tab to cycle) · ← 1 agent\n"


def _live(cmux, workspace_id="ws-1", title="cedar", cwd="/tmp") -> None:
    cmux.workspaces[workspace_id] = Workspace(id=workspace_id, title=title, cwd=cwd, ref="workspace:1")
    # An idle agent: its output, then an empty prompt box and the status strip.
    cmux.screens[workspace_id] = IDLE_SCREEN


def test_contract_columns(catalog):
    def names(table: str) -> tuple:
        rows = catalog.conn.execute(f"PRAGMA table_info({table})").fetchall()
        return tuple(row[1] for row in rows)

    assert names("berth") == BERTH_COLUMNS
    assert names("folder") == FOLDER_COLUMNS
    assert names("event") == ("id", "berth_id", "at", "kind", "detail")


def test_catalog_modes(catalog):
    directory = catalog.path.parent
    assert directory.stat().st_mode & 0o777 == 0o700
    assert catalog.path.stat().st_mode & 0o777 == 0o600
    inbox = catalog.folder_by_id(catalog.ensure_inbox())
    assert inbox["name"] == "Inbox"


def test_busy_pause_closes_without_a_confirm(catalog):
    berth_id = _insert(catalog, kind="grok", agent_name="grok", agent_session_id="sess-1")
    cmux = FakeCmux()
    _live(cmux)
    result = pause_berth(
        catalog,
        cmux,
        berth_id,
        hook=_hook(),
        pid_alive_fn=lambda pid: True,
    )
    assert result["state"] == "parked"
    assert "line two" in result["excerpt"]
    assert cmux.closed == ["ws-1"]


def test_busy_pause_force_closes(catalog):
    berth_id = _insert(catalog, kind="grok", agent_name="grok", agent_session_id="sess-1")
    cmux = FakeCmux()
    _live(cmux)
    result = pause_berth(
        catalog,
        cmux,
        berth_id,
        force=True,
        hook=_hook(),
        pid_alive_fn=lambda pid: True,
    )
    assert result["state"] == "parked"
    assert cmux.closed == ["ws-1"]
    assert result["cmux_workspace_id"] is None
    assert result["recipe_display"].endswith(" -r sess-1")  # flags first, then the id


def test_waiting_agent_is_quiet(catalog):
    berth_id = _insert(catalog, kind="grok", agent_name="grok", agent_session_id="sess-1")
    cmux = FakeCmux()
    _live(cmux)
    result = pause_berth(
        catalog,
        cmux,
        berth_id,
        hook=_hook(runtime_status="needsInput", last_body="Waiting for your next prompt"),
        pid_alive_fn=lambda pid: True,
    )
    assert result["state"] == "parked"
    assert cmux.closed == ["ws-1"]


def test_dead_pid_is_quiet(catalog):
    berth_id = _insert(catalog, kind="grok", agent_name="grok", agent_session_id="sess-1")
    cmux = FakeCmux()
    _live(cmux)
    result = pause_berth(
        catalog,
        cmux,
        berth_id,
        hook=_hook(runtime_status="running", last_body=""),
        pid_alive_fn=lambda pid: False,
    )
    assert result["state"] == "parked"


def test_protect_mark_does_not_block_pause(catalog):
    berth_id = _insert(catalog, protect=1)
    cmux = FakeCmux()
    _live(cmux)
    result = pause_berth(catalog, cmux, berth_id, pid_alive_fn=lambda pid: False)
    assert result["state"] == "parked"
    assert result["protect"] is True
    assert cmux.closed == ["ws-1"]


def test_caller_workspace_is_protected(catalog):
    berth_id = _insert(catalog, protect=0, cmux_workspace_id="ws-1")
    cmux = FakeCmux()
    _live(cmux)
    with pytest.raises(BerthError) as caught:
        pause_berth(
            catalog,
            cmux,
            berth_id,
            caller_workspace_id="WS-1",
            pid_alive_fn=lambda pid: False,
        )
    assert caught.value.code == "PROTECTED"
    assert cmux.closed == []


def test_parked_pause_does_not_close(catalog):
    berth_id = _insert(catalog, state="parked", cmux_workspace_id=None, argv=["sleep", "30"])
    cmux = FakeCmux()
    result = pause_berth(catalog, cmux, berth_id, pid_alive_fn=lambda pid: False)
    assert result["state"] == "parked"
    assert cmux.closed == []
    assert result["recipe_argv"] == ["sleep", "30"]


def test_flush_before_close(catalog):
    berth_id = _insert(catalog, kind="shell", argv=[], display="shell")
    cmux = FakeCmux()
    _live(cmux)
    seen = {}

    def observe(_workspace_id: str) -> None:
        other = type(catalog)(catalog.path).open()
        try:
            seen["state"] = other.require_berth(berth_id)["state"]
            seen["excerpt"] = other.require_berth(berth_id)["excerpt"]
        finally:
            other.close()

    cmux.on_close = observe
    pause_berth(catalog, cmux, berth_id, pid_alive_fn=lambda pid: False)
    assert seen["state"] == "pausing"
    assert "line two" in seen["excerpt"]
    assert catalog.require_berth(berth_id)["state"] == "parked"


def test_unread_screen_still_pauses(catalog):
    berth_id = _insert(catalog, kind="shell", argv=[], display="shell")
    cmux = FakeCmux()
    _live(cmux)
    cmux.fail_read = True
    result = pause_berth(catalog, cmux, berth_id, pid_alive_fn=lambda pid: False)
    assert result["state"] == "parked"
    assert result["excerpt"] == "screen not ready"
    assert result["cmux_workspace_id"] is None
    assert cmux.closed == ["ws-1"]
    assert cmux.selected == []
    assert catalog.require_berth(berth_id)["state"] == "parked"


def test_close_failure_returns_to_live(catalog):
    berth_id = _insert(catalog, kind="shell", argv=[], display="shell")
    cmux = FakeCmux()
    _live(cmux)
    cmux.fail_close = True
    with pytest.raises(BerthError) as caught:
        pause_berth(catalog, cmux, berth_id, pid_alive_fn=lambda pid: False)
    assert caught.value.code == "CMUX_REJECTED"
    row = catalog.require_berth(berth_id)
    assert row["state"] == "live"
    assert row["excerpt"].startswith("line one\nline two")


def test_process_pause_needs_confirm(catalog):
    berth_id = _insert(catalog, kind="process")
    cmux = FakeCmux()
    _live(cmux)
    with pytest.raises(BerthError) as caught:
        pause_berth(catalog, cmux, berth_id, pid_alive_fn=lambda pid: False)
    assert caught.value.code == "KIND_UNSAFE"
    assert cmux.closed == []
    result = pause_berth(
        catalog,
        cmux,
        berth_id,
        confirm=True,
        pid_alive_fn=lambda pid: False,
    )
    assert result["state"] == "parked"
    assert result["excerpt"]


def test_double_play_in_progress(catalog):
    berth_id = _insert(
        catalog,
        state="playing",
        cmux_workspace_id=None,
        updated_at=utc_now(),
        kind="shell",
        argv=[],
        display="shell",
    )
    cmux = FakeCmux()
    with pytest.raises(BerthError) as caught:
        play_berth(catalog, cmux, berth_id)
    assert caught.value.code == "IN_PROGRESS"
    assert cmux.created == []


def test_play_of_live_focuses_existing_row(catalog):
    berth_id = _insert(catalog, state="live", kind="shell", argv=[], display="shell")
    cmux = FakeCmux()
    _live(cmux)
    result = play_berth(catalog, cmux, berth_id)
    assert result["state"] == "live"
    assert result["cmux_workspace_id"] == "ws-1"
    assert cmux.selected == ["ws-1"]
    assert cmux.created == []


def test_stale_playing_plays_again(catalog):
    berth_id = _insert(
        catalog,
        state="playing",
        cmux_workspace_id=None,
        updated_at=_stamp(minutes_ago=3),
        kind="shell",
        argv=[],
        display="shell",
        cwd="/tmp",
    )
    cmux = FakeCmux()
    result = play_berth(catalog, cmux, berth_id, now=utc_now())
    assert result["state"] == "live"
    assert result["cmux_workspace_id"] == "created-1"
    assert cmux.created[0]["cwd"] == "/tmp"
    assert cmux.created[0]["command"] is None


def test_process_play_needs_run_command(catalog):
    berth_id = _insert(catalog, state="parked", cmux_workspace_id=None, kind="process")
    cmux = FakeCmux()
    with pytest.raises(BerthError) as caught:
        play_berth(catalog, cmux, berth_id)
    assert caught.value.code == "KIND_UNSAFE"
    assert cmux.created == []
    assert catalog.require_berth(berth_id)["state"] == "parked"
    result = play_berth(catalog, cmux, berth_id, run_command=True, focus=False)
    assert result["state"] == "live"
    assert cmux.created[0]["command"] == "sleep 3600"
    assert cmux.created[0]["focus"] is False


def test_session_missing_leaves_parked(catalog, tmp_path):
    berth_id = _insert(
        catalog,
        state="parked",
        cmux_workspace_id=None,
        kind="grok",
        agent_name="grok",
        agent_session_id="not-a-real-session",
        argv=["grok", "-r", "not-a-real-session"],
        display="grok -r not-a-real-session",
    )
    cmux = FakeCmux()
    with pytest.raises(BerthError) as caught:
        play_berth(catalog, cmux, berth_id, sessions_root=tmp_path / "sessions")
    assert caught.value.code == "SESSION_MISSING"
    assert cmux.created == []
    assert catalog.require_berth(berth_id)["state"] == "parked"


def test_session_on_disk_and_in_use(catalog, tmp_path):
    session_id = "01a0aaaa-bbbb-7ccc-8ddd-eeeeeeeeeeee"
    root = tmp_path / "sessions" / "encoded-cwd" / session_id
    root.mkdir(parents=True)
    assert session_on_disk(tmp_path / "sessions", session_id)
    assert not session_on_disk(tmp_path / "sessions", "missing-id")
    owner = _insert(
        catalog,
        id="owner",
        state="live",
        kind="grok",
        agent_name="grok",
        agent_session_id=session_id,
        cmux_workspace_id="ws-owner",
        argv=["grok", "-r", session_id],
    )
    other = _insert(
        catalog,
        id="other",
        state="parked",
        cmux_workspace_id=None,
        kind="grok",
        agent_name="grok",
        agent_session_id=session_id,
        argv=["grok", "-r", session_id],
        title="other",
    )
    cmux = FakeCmux()
    _live(cmux, "ws-owner", title="owner")
    with pytest.raises(BerthError) as caught:
        play_berth(catalog, cmux, other, sessions_root=tmp_path / "sessions")
    assert caught.value.code == "SESSION_IN_USE"
    assert caught.value.details["other_berth_id"] == owner
    assert catalog.require_berth(other)["state"] == "parked"


def test_blank_grok_new_plays(catalog):
    inbox = catalog.ensure_inbox()
    berth_id = _insert(
        catalog,
        state="parked",
        cmux_workspace_id=None,
        kind="grok",
        agent_name="grok",
        agent_session_id=None,
        argv=["grok"],
        display="grok",
        attachments={"blank": True},
        folder_id=inbox,
        cwd="/tmp",
    )
    cmux = FakeCmux()
    result = play_berth(catalog, cmux, berth_id, focus=False)
    assert result["state"] == "live"
    assert cmux.created[0]["command"] == grok_exec_text(["grok"])
    assert cmux.grouped == []


def test_grok_play_execs_the_binary_and_keeps_flags(catalog, tmp_path):
    session_id = "01a0bbbb-cccc-7ddd-8eee-ffffffffffff"
    (tmp_path / "sessions" / "cwd" / session_id).mkdir(parents=True)
    folder = mkdir_path(catalog, "Signals")
    berth_id = _insert(
        catalog,
        state="parked",
        cmux_workspace_id=None,
        kind="grok",
        agent_name="grok",
        agent_session_id=session_id,
        argv=["grok", "-r", session_id, "--always-approve", "--no-subagents", "--reasoning-effort", "xhigh"],
        display=f"grok -r {session_id} --always-approve --no-subagents --reasoning-effort xhigh",
        folder_id=folder["id"],
        cwd="/tmp/signals",
    )
    cmux = FakeCmux()
    result = play_berth(catalog, cmux, berth_id, sessions_root=tmp_path / "sessions", focus=False)
    command = cmux.created[0]["command"]
    assert command.startswith("exec ")
    assert f"-r {session_id}" in command
    assert "--always-approve" in command
    assert "--no-subagents" in command
    assert "--reasoning-effort xhigh" in command
    if GROK_BINARY.is_file():
        assert str(GROK_BINARY) in command
    assert result["state"] == "live"
    assert result["agent_session_id"] == session_id
    assert cmux.grouped == []
    assert cmux.selected == []


def test_login_echo_is_not_a_resume(catalog):
    from berth.state import _proves_resume

    session_id = "01a0bbbb-cccc-7ddd-8eee-ffffffffffff"
    echo = f"exec /Users/olduser/.grok/bin/grok -r {session_id}\nLast login: Sat Oct  3 10:48:15 on ttys003\n"
    assert _proves_resume(echo, session_id) is False
    assert _proves_resume(f"Grok  session {session_id}\nhello from the conversation\n", session_id) is True
    assert catalog is not None


def test_shell_play_does_not_exec_grok(catalog):
    berth_id = _insert(
        catalog,
        state="parked",
        cmux_workspace_id=None,
        kind="shell",
        argv=[],
        display="shell",
        cwd="/tmp",
    )
    cmux = FakeCmux()
    play_berth(catalog, cmux, berth_id, focus=False)
    assert cmux.created[0]["command"] is None
    assert cmux.grouped == []


def test_create_failure_returns_to_parked(catalog):
    berth_id = _insert(catalog, state="parked", cmux_workspace_id=None, kind="shell", argv=[], display="shell")
    cmux = FakeCmux()
    cmux.fail_create = True
    with pytest.raises(BerthError) as caught:
        play_berth(catalog, cmux, berth_id)
    assert caught.value.code == "CMUX_REJECTED"
    assert catalog.require_berth(berth_id)["state"] == "parked"


def test_reconcile_pausing_missing_becomes_parked(catalog):
    berth_id = _insert(
        catalog,
        state="pausing",
        argv=["sleep", "3600"],
        display="sleep 3600",
        excerpt="kept",
        cmux_workspace_id="gone",
    )
    report = reconcile(catalog, present={}, hooks={})
    row = catalog.require_berth(berth_id)
    assert row["state"] == "parked"
    assert json.loads(row["recipe_argv"]) == ["sleep", "3600"]
    assert row["excerpt"] == "kept"
    assert report["corrections"][0]["action"] == "parked"


def test_reconcile_pausing_present_becomes_live(catalog):
    berth_id = _insert(catalog, state="pausing", cmux_workspace_id="ws-1", argv=["sleep", "1"])
    present = {"ws-1": Workspace(id="ws-1", title="cedar", cwd="/tmp")}
    reconcile(catalog, present=present, hooks={})
    assert catalog.require_berth(berth_id)["state"] == "live"
    assert json.loads(catalog.require_berth(berth_id)["recipe_argv"]) == ["sleep", "1"]


def test_vanished_live_becomes_parked_and_keeps_recipe(catalog):
    berth_id = _insert(
        catalog,
        state="live",
        kind="grok",
        agent_name="grok",
        agent_session_id="sess-1",
        argv=["grok", "-r", "sess-1", "--always-approve"],
        display="grok -r sess-1 --always-approve",
        excerpt="the screen",
        cmux_workspace_id="missing",
    )
    reconcile(catalog, present={}, hooks={})
    row = public_berth(catalog.require_berth(berth_id))
    assert row["state"] == "parked"
    assert row["recipe_argv"] == ["grok", "-r", "sess-1", "--always-approve"]
    assert row["excerpt"] == "the screen"
    assert row["cmux_workspace_id"] is None
    assert row["agent_session_id"] == "sess-1"
    kinds = [event["kind"] for event in catalog.events_for(berth_id)]
    assert "workspace_vanished" in kinds


def test_reconcile_stale_play_and_young_play(catalog):
    stale = _insert(
        catalog,
        id="stale",
        state="playing",
        cmux_workspace_id=None,
        updated_at=_stamp(minutes_ago=5),
        argv=["sleep", "9"],
    )
    young = _insert(
        catalog,
        id="young",
        title="young",
        state="playing",
        cmux_workspace_id=None,
        updated_at=utc_now(),
        argv=[],
    )
    landed = _insert(
        catalog,
        id="landed",
        title="landed",
        state="playing",
        cmux_workspace_id="ws-landed",
        updated_at=_stamp(minutes_ago=5),
    )
    present = {"ws-landed": Workspace(id="ws-landed", title="landed", cwd="/tmp")}
    reconcile(catalog, present=present, hooks={}, now=utc_now())
    assert catalog.require_berth(stale)["state"] == "parked"
    assert json.loads(catalog.require_berth(stale)["recipe_argv"]) == ["sleep", "9"]
    assert catalog.require_berth(young)["state"] == "playing"
    assert catalog.require_berth(landed)["state"] == "live"


def test_reconcile_parked_workspace_returns_live(catalog):
    berth_id = _insert(catalog, state="parked", cmux_workspace_id="ws-1", argv=["sleep", "2"])
    present = {"ws-1": Workspace(id="ws-1", title="cedar", cwd="/tmp")}
    reconcile(catalog, present=present, hooks={})
    row = catalog.require_berth(berth_id)
    assert row["state"] == "live"
    assert json.loads(row["recipe_argv"]) == ["sleep", "2"]


def test_adopt_matches_workspace_uuid_once(catalog):
    workspace = Workspace(id="WS-NEW", title="scratch", cwd="/tmp/nowhere", ref="workspace:9")
    first = adopt_workspace(catalog, workspace, hook=None, surfaces=[], groups=[])
    second = adopt_workspace(catalog, workspace, hook=None, surfaces=[], groups=[])
    assert first["id"] == second["id"]
    assert first["state"] == "live"
    assert first["kind"] == "shell"
    rows = catalog.all_berths()
    assert len(rows) == 1
    folder = catalog.folder_by_id(first["folder_id"])
    assert folder["name"] == "Inbox"


def test_adopt_uses_cwd_rule_and_group_name(catalog):
    made = mkdir_path(catalog, "Work")
    with catalog.immediate():
        catalog.conn.execute(
            "UPDATE folder SET default_cwd = ? WHERE id = ?",
            ("/tmp/project", made["id"]),
        )
    folders = [
        Folder(
            id=row["id"],
            parent_id=row["parent_id"],
            name=row["name"],
            sort=row["sort"],
            profile_id=row["profile_id"],
            default_cwd=row["default_cwd"],
            note=row["note"],
            archived=row["archived"],
        )
        for row in catalog.all_folders()
    ]
    inbox = catalog.ensure_inbox()
    by_cwd = choose_folder_id(folders, cwd="/tmp/project/pkg", group_name=None)
    assert by_cwd == made["id"]
    assert choose_folder_id(folders, cwd="/var/empty", group_name=None) is None
    workspace = Workspace(id="g1", title="job", cwd="/var/empty", ref="workspace:4")
    groups = [Group(name="Work", ref="workspace_group:1", members=["workspace:4"])]
    record = build_record(
        workspace=workspace,
        hook=None,
        surfaces=[],
        groups=groups,
        folders=folders,
        inbox_id=inbox,
    )
    assert record["folder_id"] == made["id"]


def test_adopt_reattaches_a_parked_session_and_does_not_clone(catalog):
    session_id = "01a0aaaa-bbbb-7ccc-8ddd-eeeeeeeeeeee"
    parked = _insert(
        catalog,
        id="older",
        title="backend linden",
        state="parked",
        kind="grok",
        agent_name="grok",
        agent_session_id=session_id,
        cmux_workspace_id=None,
        argv=["grok", "-r", session_id],
        display=f"grok -r {session_id}",
        created_at="2026-10-02T22:43:36Z",
        parked_at="2026-10-02T22:43:36Z",
    )
    workspace = Workspace(id="ws-new", title="backend linden", cwd="/tmp", ref="workspace:101")
    hook = _hook(session_id=session_id, workspace_id="ws-new")
    record = adopt_workspace(catalog, workspace, hook=hook, surfaces=[], groups=[])
    assert record["id"] == parked
    assert record["state"] == "live"
    assert record["cmux_workspace_id"] == "ws-new"
    assert record["agent_session_id"] == session_id
    assert len(catalog.all_berths()) == 1
    again = adopt_workspace(catalog, workspace, hook=hook, surfaces=[], groups=[])
    assert again["id"] == parked
    assert len(catalog.all_berths()) == 1


def test_merge_keeps_the_older_row_and_the_session_id(catalog):
    session_id = "01a0cccc-dddd-7eee-8fff-000000000001"
    older = _insert(
        catalog,
        id="older",
        title="backend linden",
        state="parked",
        kind="grok",
        agent_name="grok",
        agent_session_id=session_id,
        cmux_workspace_id=None,
        argv=["grok", "-r", session_id],
        created_at="2026-10-02T22:43:36Z",
    )
    _insert(
        catalog,
        id="newer",
        title="backend linden",
        state="live",
        kind="grok",
        agent_name="grok",
        agent_session_id=session_id,
        cmux_workspace_id="4E6207C8-D0FA-4C34-9CEC-AEC08B52BDF1",
        argv=["grok", "-r", session_id],
        created_at="2026-10-03T14:39:41Z",
    )
    reports = merge_duplicate_sessions(catalog)
    assert len(reports) == 1
    assert reports[0]["keeper_id"] == older
    assert reports[0]["dropped"] == ["newer"]
    kept = catalog.require_berth(older)
    assert kept["state"] == "live"
    assert kept["cmux_workspace_id"] == "4E6207C8-D0FA-4C34-9CEC-AEC08B52BDF1"
    assert kept["agent_session_id"] == session_id
    assert catalog.get_berth("newer") is None
    assert len(catalog.all_berths()) == 1


def test_adopt_creates_a_root_folder_for_the_group(catalog):
    workspace = Workspace(id="g1", title="job", cwd="/var/empty", ref="workspace:4")
    groups = [Group(name="Signals", ref="workspace_group:1", members=["workspace:4"])]
    record = adopt_workspace(catalog, workspace, hook=None, surfaces=[], groups=groups)
    folder = catalog.folder_by_id(record["folder_id"])
    assert folder["name"] == "Signals"
    assert folder["parent_id"] is None
    again = adopt_workspace(catalog, workspace, hook=None, surfaces=[], groups=groups)
    assert again["id"] == record["id"]
    roots = [row["name"] for row in catalog.child_folders(None)]
    assert roots.count("Signals") == 1


def test_refile_moves_live_inbox_members_and_leaves_the_rest(catalog):
    inbox = catalog.ensure_inbox()
    kept = mkdir_path(catalog, "Kept")["id"]
    _insert(catalog, id="live-member", title="member", cmux_workspace_id="ws-a", folder_id=inbox)
    _insert(
        catalog,
        id="parked-member",
        title="old",
        state="parked",
        cmux_workspace_id="ws-b",
        folder_id=inbox,
        parked_at=utc_now(),
    )
    _insert(catalog, id="kept-member", title="kept", cmux_workspace_id="ws-c", folder_id=kept)
    _insert(catalog, id="loose", title="loose", cmux_workspace_id="ws-d", folder_id=inbox)
    cmux = FakeCmux()
    cmux.workspaces["ws-a"] = Workspace(id="ws-a", title="member", cwd="/tmp", ref="workspace:a")
    cmux.workspaces["ws-b"] = Workspace(id="ws-b", title="old", cwd="/tmp", ref="workspace:b")
    cmux.workspaces["ws-c"] = Workspace(id="ws-c", title="kept", cwd="/tmp", ref="workspace:c")
    cmux.workspaces["ws-d"] = Workspace(id="ws-d", title="loose", cwd="/tmp", ref="workspace:d")
    cmux.groups = [
        Group(
            name="Signals",
            ref="workspace_group:1",
            members=["workspace:a", "workspace:b", "workspace:c"],
        )
    ]
    adopt_known(catalog, cmux, {})
    assert catalog.require_berth("live-member")["folder_id"] != inbox
    assert catalog.folder_by_id(catalog.require_berth("live-member")["folder_id"])["name"] == "Signals"
    assert catalog.require_berth("parked-member")["folder_id"] == inbox
    assert catalog.require_berth("parked-member")["state"] == "parked"
    assert catalog.require_berth("kept-member")["folder_id"] == kept
    assert catalog.require_berth("loose")["folder_id"] == inbox
    assert cmux.closed == []


def test_adopt_grok_stores_hook_session_not_body(catalog):
    workspace = Workspace(id="ws-1", title="cedar", cwd="/work", ref="workspace:2")
    hook = _hook(last_body="SECRET tool payload that must not be stored")
    record = adopt_workspace(
        catalog,
        workspace,
        hook=hook,
        surfaces=[Surface(initial_command="grok -r sess-1")],
        groups=[],
    )
    assert record["kind"] == "grok"
    assert record["agent_session_id"] == "sess-1"
    assert record["agent_name"] == "grok"
    blob = json.dumps(record)
    assert "SECRET" not in blob
    assert record["recipe_argv"][0] == "grok" and record["recipe_argv"][-2:] == ["-r", "sess-1"]
    assert "--always-approve" in record["recipe_argv"]


def test_adopt_process_from_surface_command(catalog):
    workspace = Workspace(id="proof", title="berth-proof", cwd="/tmp", selected=False)
    surface = Surface(initial_command="echo berth-proof; exec sleep 3600", cwd="/tmp")
    record = adopt_workspace(catalog, workspace, hook=None, surfaces=[surface], groups=[])
    assert record["kind"] == "process"
    assert record["protect"] is False
    assert "sleep 3600" in record["recipe_display"]
    assert catalog.folder_by_id(record["folder_id"])["name"] == "Inbox"


def test_adopt_attaches_playing_claim(catalog):
    berth_id = _insert(
        catalog,
        state="playing",
        title="berth-proof",
        cmux_workspace_id=None,
        kind="process",
        cwd="/tmp",
    )
    workspace = Workspace(id="new-ws", title="berth-proof", cwd="/tmp")
    record = adopt_workspace(catalog, workspace, hook=None, surfaces=[], groups=[])
    assert record["id"] == berth_id
    assert record["cmux_workspace_id"] == "new-ws"
    assert record["state"] == "playing"
    assert len(catalog.all_berths()) == 1


def test_selected_workspace_is_protected_on_adopt(catalog):
    workspace = Workspace(id="focus", title="looking", cwd="/tmp", selected=True)
    record = adopt_workspace(catalog, workspace, hook=None, surfaces=[], groups=[])
    assert record["protect"] is True


def test_ambiguous_query_does_not_act(catalog):
    _insert(catalog, id="a", title="reservoir alpha", cmux_workspace_id="a")
    _insert(catalog, id="b", title="reservoir beta", cmux_workspace_id="b")
    with pytest.raises(QueryAmbiguous) as caught:
        resolve_query(catalog, "reservoir")
    assert len(caught.value.matches) == 2
    assert catalog.require_berth("a")["state"] == "live"


def test_folder_delete_moves_berths_and_lifts_children(catalog):
    mkdir_path(catalog, "Empty/Child")
    removed = rmdir_folder(catalog, "Empty")
    assert removed["deleted"] == "Empty"
    assert removed["to"] == "Inbox"
    assert removed["moved"] == 0
    assert removed["lifted"] == ["Child"]
    assert catalog.folder_by_id(removed["id"]) is None
    child = catalog.child_folders(None)
    assert any(row["name"] == "Child" and row["parent_id"] is None for row in child)
    assert all(row["name"] != "Empty" for row in child)

    made = mkdir_path(catalog, "LiveBox")
    _insert(
        catalog,
        id="inside",
        folder_id=made["id"],
        state="live",
        cmux_workspace_id="ws-live",
        title="inside",
    )
    removed = rmdir_folder(catalog, "LiveBox")
    assert removed["moved"] == 1
    assert removed["to"] == "Inbox"
    row = catalog.require_berth("inside")
    assert row["folder_id"] == catalog.ensure_inbox()
    assert row["state"] == "live"
    assert row["cmux_workspace_id"] == "ws-live"
    assert catalog.folder_by_id(made["id"]) is None

    with pytest.raises(BerthError) as caught:
        rmdir_folder(catalog, "Inbox")
    assert caught.value.code == "FOLDER_NONEMPTY"
    assert caught.value.message == "Inbox stays"


def test_folder_delete_merges_a_child_that_already_exists(catalog):
    keep_twin = mkdir_path(catalog, "Keep/Twin")
    nested = mkdir_path(catalog, "Keep/Box/Twin")
    _insert(catalog, id="in-box", folder_id=nested["id"], state="live", cmux_workspace_id="ws-box", title="nested")
    box_id = catalog.folder_by_id(nested["id"])["parent_id"]
    _insert(catalog, id="direct", folder_id=box_id, state="parked", cmux_workspace_id=None, title="direct")
    removed = rmdir_folder(catalog, "Keep/Box")
    assert removed["to"] == "Keep"
    assert removed["moved"] == 1
    assert removed["merged"] == ["Twin"]
    assert catalog.require_berth("direct")["folder_id"] != box_id
    assert catalog.folder_by_id(catalog.require_berth("direct")["folder_id"])["name"] == "Keep"
    assert catalog.require_berth("in-box")["folder_id"] == keep_twin["id"]
    assert catalog.require_berth("in-box")["state"] == "live"
    assert catalog.require_berth("in-box")["cmux_workspace_id"] == "ws-box"
    assert catalog.folder_by_id(box_id) is None
    assert catalog.folder_by_id(nested["id"]) is None


def test_folder_archive_refuses_a_live_berth(catalog):
    made = mkdir_path(catalog, "LiveBox")
    _insert(catalog, id="inside", folder_id=made["id"], state="live", cmux_workspace_id="in", title="inside")
    with pytest.raises(BerthError) as caught:
        archive_folder(catalog, "LiveBox")
    assert caught.value.code == "FOLDER_LIVE"


def test_closed_list_parks_the_ghost_and_keeps_the_open_row(catalog):
    session = "01a0cccc-dddd-7eee-8fff-000000000001"
    ghost = _insert(
        catalog,
        id="ghost",
        title="School OS",
        kind="shell",
        state="live",
        cmux_workspace_id="babf9f5e-0000-4000-8000-000000000001",
        argv=[],
        display="shell",
    )
    kept = _insert(
        catalog,
        id="kept",
        title="backend linden",
        kind="grok",
        agent_name="grok",
        agent_session_id=session,
        state="live",
        cmux_workspace_id="ws-open",
        argv=["grok", "-r", session],
        display=f"grok -r {session}",
    )
    from berth.observer import Observer

    observer = Observer()
    open_now = [Workspace(id="ws-open", title="backend linden", cwd="/tmp")]
    observer.tick(catalog, FakeCmux(), open_now, {})
    assert catalog.require_berth(ghost)["state"] == "live"
    actions = observer.tick(catalog, FakeCmux(), open_now, {})
    assert [item["berth_id"] for item in actions if item["action"] == "parked"] == ["ghost"]
    ghost_row = catalog.require_berth(ghost)
    assert ghost_row["state"] == "parked"
    assert ghost_row["cmux_workspace_id"] is None
    kept_row = catalog.require_berth(kept)
    assert kept_row["state"] == "live"
    assert kept_row["agent_session_id"] == session
    assert kept_row["cmux_workspace_id"] == "ws-open"


def test_delete_parked_row_and_refuses_the_screen(catalog, tmp_path):
    session = "01a0dddd-eeee-7fff-8000-111111111111"
    root = tmp_path / "sessions" / "cwd" / session
    root.mkdir(parents=True)
    (root / "marker").write_text("stay")
    parked = _insert(
        catalog,
        id="parked-del",
        title="old grok",
        state="parked",
        kind="grok",
        agent_name="grok",
        agent_session_id=session,
        cmux_workspace_id=None,
        argv=["grok", "-r", session],
        display=f"grok -r {session}",
    )
    cmux = FakeCmux()
    removed = delete_berth(catalog, cmux, parked, caller_workspace_id=None)
    assert removed["state"] == "deleted"
    assert catalog.get_berth(parked) is None
    assert (root / "marker").read_text() == "stay"
    assert cmux.closed == []

    screen = _insert(
        catalog,
        id="screen",
        title="Berth",
        kind="berth",
        state="live",
        cmux_workspace_id="FA20C8CB-86F3-47AF-9514-B8B4CA8436EC",
        argv=["berth"],
        display="berth",
    )
    with pytest.raises(BerthError) as caught:
        delete_berth(catalog, cmux, screen, caller_workspace_id=None)
    assert caught.value.code == "PROTECTED"
    assert catalog.get_berth(screen) is not None
    assert cmux.closed == []

    caller = _insert(
        catalog,
        id="caller",
        title="this pane",
        state="live",
        cmux_workspace_id="caller-ws",
    )
    with pytest.raises(BerthError) as caught:
        delete_berth(catalog, cmux, caller, caller_workspace_id="caller-ws")
    assert caught.value.code == "PROTECTED"
    assert catalog.get_berth(caller) is not None
    assert cmux.closed == []


def test_delete_live_closes_then_removes_the_row(catalog):
    berth_id = _insert(
        catalog,
        id="live-del",
        title="temporary",
        state="live",
        kind="shell",
        cmux_workspace_id="ws-del",
        argv=[],
        display="shell",
    )
    cmux = FakeCmux()
    cmux.workspaces["ws-del"] = Workspace(id="ws-del", title="temporary", cwd="/tmp")
    delete_berth(catalog, cmux, berth_id, caller_workspace_id="other-ws")
    assert cmux.closed == ["ws-del"]
    assert catalog.get_berth(berth_id) is None


def test_a_new_inbox_session_takes_the_inbox_color(catalog):
    inbox = catalog.ensure_inbox()
    catalog.conn.execute("UPDATE folder SET color = ? WHERE id = ?", ("#336699", inbox))
    cmux = FakeCmux()
    workspace = Workspace(id="ws-fresh", title="fresh", cwd="/tmp", ref="workspace:9")
    cmux.workspaces[workspace.id] = workspace
    record = adopt_workspace(
        catalog,
        workspace,
        hook=None,
        surfaces=[],
        groups=[],
        cmux=cmux,
    )
    assert record["folder_id"] == inbox
    assert record["state"] == "live"
    assert cmux.colors == [("ws-fresh", "#336699")]
    again = adopt_workspace(
        catalog,
        workspace,
        hook=None,
        surfaces=[],
        groups=[],
        cmux=cmux,
    )
    assert again["id"] == record["id"]
    assert cmux.colors == [("ws-fresh", "#336699")]


def test_a_new_session_stays_uncolored_when_the_folder_has_no_color(catalog):
    cmux = FakeCmux()
    workspace = Workspace(id="ws-plain", title="plain", cwd="/tmp", ref="workspace:10")
    adopt_workspace(catalog, workspace, hook=None, surfaces=[], groups=[], cmux=cmux)
    assert cmux.colors == []


def test_rename_live_berth_renames_the_workspace(catalog):
    berth_id = _insert(catalog, title="old title", state="live", cmux_workspace_id="ws-1")
    cmux = FakeCmux()
    _live(cmux, title="old title")
    result = rename_berth(catalog, cmux, berth_id, "new title")
    assert result["title"] == "new title"
    assert cmux.workspaces["ws-1"].title == "new title"
    assert catalog.require_berth(berth_id)["agent_session_id"] is None
    assert cmux.sent == []


def test_adopt_berth_binary_is_not_a_shell(catalog):
    workspace = Workspace(id="screen-new", title="Berth", cwd="/tmp")
    surface = Surface(initial_command="/Users/olduser/.local/bin/berth", cwd="/tmp")
    record = adopt_workspace(catalog, workspace, hook=None, surfaces=[surface], groups=[])
    assert record["kind"] == "berth"
    assert kind_word(record["kind"]) == "Berth"
    assert record["kind"] != "shell"
    assert record["kind"] != "grok"


def test_claude_stop_with_a_body_is_quiet():
    hook = _hook(
        agent_name="claude",
        runtime_status="idle",
        hook_event="Stop",
        last_body="Both documents are written.",
        executable="/Users/someone/.local/bin/claude",
        launcher="claude",
    )
    assert agent_is_busy("other-agent", hook, alive=True) is False


def test_claude_session_start_is_quiet():
    hook = _hook(
        agent_name="claude",
        runtime_status="running",
        hook_event="SessionStart",
        last_body=None,
        executable="/Users/someone/.local/bin/claude",
        launcher="claude",
    )
    assert agent_is_busy("other-agent", hook, alive=True) is False


def test_claude_pretooluse_is_busy():
    hook = _hook(
        agent_name="claude",
        runtime_status="running",
        hook_event="PreToolUse",
        last_body=None,
        executable="/Users/someone/.local/bin/claude",
        launcher="claude",
    )
    assert agent_is_busy("other-agent", hook, alive=True) is True


def test_grok_resume_argv_uses_session_field():
    hook = _hook(arguments=["opaque-token-not-the-session-id", "--always-approve", "--no-subagents"])
    argv = grok_resume_argv(hook)
    assert argv[0] == "grok" and argv[-2:] == ["-r", "sess-1"]
    assert "--always-approve" in argv
    assert "--no-subagents" in argv
    assert "opaque-token-not-the-session-id" not in argv


def test_claude_resume_uses_the_absolute_binary():
    binary = "/Users/someone/.local/bin/claude"
    hook = _hook(
        agent_name="claude",
        session_id="06efcf44-3127-4eb7-bf2d-ab4ca07eac0f",
        executable=binary,
        launcher="claude",
        arguments=[binary],
    )
    argv = other_agent_resume_argv(hook)
    assert argv == [binary, "-r", "06efcf44-3127-4eb7-bf2d-ab4ca07eac0f"]
    assert kind_word("other-agent", "claude") == "Claude"


def test_codex_antigravity_and_cursor_resume_with_their_own_commands():
    codex = _hook(
        agent_name="codex",
        session_id="thread-1",
        executable="/Users/someone/.local/bin/codex",
        launcher="codex",
        arguments=["/Users/someone/.local/bin/codex"],
    )
    agy = _hook(
        agent_name="antigravity",
        session_id="conv-1",
        executable="/Users/someone/.local/bin/agy",
        launcher="agy",
        arguments=["agy", "--model", "gemini"],
    )
    cursor = _hook(
        agent_name="cursor",
        session_id="chat-1",
        executable="/Users/someone/.local/bin/agent",
        launcher="agent",
        arguments=["/Users/someone/.local/bin/agent"],
    )
    assert other_agent_resume_argv(codex) == ["/Users/someone/.local/bin/codex", "resume", "thread-1"]
    assert other_agent_resume_argv(agy) == ["/Users/someone/.local/bin/agy", "--conversation", "conv-1"]
    assert other_agent_resume_argv(cursor) == ["/Users/someone/.local/bin/agent", "--resume", "chat-1"]


def test_play_codex_uses_resume_not_a_short_flag(catalog):
    binary = "/Users/someone/.local/bin/codex"
    session_id = "thread-9"
    berth_id = _insert(
        catalog,
        id="codex-play",
        title="Codex",
        state="parked",
        kind="other-agent",
        agent_name="codex",
        agent_session_id=session_id,
        cmux_workspace_id=None,
        cwd="/tmp",
        argv=[binary, "-r", session_id, "--model", "gpt"],
        display=f"{binary} -r {session_id}",
    )
    cmux = FakeCmux()
    play_berth(catalog, cmux, berth_id, focus=False)
    assert cmux.created[0]["command"] == f"exec {binary} resume {session_id}"
    assert cmux.closed == []


def test_claude_resume_drops_flags_whose_values_do_not_survive():
    binary = "/Users/someone/.local/bin/claude"
    session_id = "fbaae4e1-ee32-4e16-8c31-e820b30c1954"
    hook = _hook(
        agent_name="claude",
        session_id=session_id,
        executable=binary,
        launcher="claude",
        arguments=[
            binary,
            "--dangerously-skip-permissions",
            "--effort",
            "high",
            "--model",
            "claude-opus-5-5",
            "--name",
            "desk",
        ],
    )
    assert other_agent_resume_argv(hook) == [binary, "-r", session_id]


def test_play_claude_execs_the_resume(catalog):
    binary = "/Users/someone/.local/bin/claude"
    session_id = "06efcf44-3127-4eb7-bf2d-ab4ca07eac0f"
    berth_id = _insert(
        catalog,
        id="claude-row",
        title="login-successful",
        state="parked",
        kind="other-agent",
        agent_name="claude",
        agent_session_id=session_id,
        cmux_workspace_id=None,
        cwd="/Users/someone",
        argv=[binary, "-r", session_id],
        display=f"{binary} -r {session_id}",
    )
    from tests.support import write_claude_conversation

    write_claude_conversation(session_id, "/Users/someone")
    cmux = FakeCmux()
    play_berth(catalog, cmux, berth_id, focus=False)
    assert cmux.created[0]["command"] == other_agent_exec_text([binary, "-r", session_id, "-n", "login-successful"])
    assert catalog.require_berth(berth_id)["state"] == "live"
    assert cmux.closed == []


def test_play_claude_strips_a_stored_command_down_to_the_session(catalog):
    binary = "/Users/someone/.local/bin/claude"
    session_id = "fbaae4e1-ee32-4e16-8c31-e820b30c1954"
    stored = [
        binary,
        "-r",
        session_id,
        "--dangerously-skip-permissions",
        "--effort",
        "--model",
        "claude-opus-5-5",
        "--name",
    ]
    berth_id = _insert(
        catalog,
        id="claude-bloated",
        title="loop-claude",
        state="parked",
        kind="other-agent",
        agent_name="claude",
        agent_session_id=session_id,
        cmux_workspace_id=None,
        cwd="/Users/someone",
        argv=stored,
        display=" ".join(stored),
    )
    from tests.support import write_claude_conversation

    write_claude_conversation(session_id, "/Users/someone")
    cmux = FakeCmux()
    play_berth(catalog, cmux, berth_id, focus=False)
    assert cmux.created[0]["command"] == other_agent_exec_text([binary, "-r", session_id, "-n", "loop-claude"])
    assert cmux.created[0]["command"].endswith(f"-r {session_id} -n loop-claude")
    assert "--effort" not in cmux.created[0]["command"]
    assert "--name" not in cmux.created[0]["command"]
    row = catalog.require_berth(berth_id)
    assert json.loads(row["recipe_argv"]) == [binary, "-r", session_id]
    assert row["state"] == "live"
    assert cmux.closed == []


def test_adopt_reattaches_a_parked_claude_session(catalog):
    binary = "/Users/someone/.local/bin/claude"
    session_id = "06efcf44-3127-4eb7-bf2d-ab4ca07eac0f"
    parked = _insert(
        catalog,
        id="older-claude",
        title="login-successful",
        state="parked",
        kind="other-agent",
        agent_name="claude",
        agent_session_id=session_id,
        cmux_workspace_id=None,
        argv=[binary, "-r", session_id],
        display=f"{binary} -r {session_id}",
        created_at="2026-10-05T15:00:00Z",
        parked_at="2026-10-05T15:10:00Z",
    )
    workspace = Workspace(id="ws-new", title="login-successful", cwd="/Users/someone", ref="workspace:8")
    hook = _hook(
        agent_name="claude",
        session_id=session_id,
        workspace_id="ws-new",
        executable=binary,
        launcher="claude",
        arguments=[binary],
        cwd="/Users/someone",
    )
    record = adopt_workspace(catalog, workspace, hook=hook, surfaces=[], groups=[])
    assert record["id"] == parked
    assert record["state"] == "live"
    assert record["kind"] == "other-agent"
    assert record["agent_name"] == "claude"
    assert record["cmux_workspace_id"] == "ws-new"
    assert record["recipe_argv"] == [binary, "-r", session_id]
    assert len(catalog.all_berths()) == 1


def test_claude_starts_through_the_cmux_wrapper_so_agent_messages_arrive(monkeypatch, tmp_path):
    from berth import place

    wrapper = tmp_path / "cmux-claude-wrapper"
    wrapper.write_text("#!/bin/sh\n")
    monkeypatch.setattr(place, "CMUX_CLAUDE_WRAPPER", str(wrapper))
    monkeypatch.delenv("BERTH_NO_CMUX_WRAPPER", raising=False)
    assert place.other_agent_exec_text(["/Users/x/.local/bin/claude", "-r", "abc"]) == f"exec {wrapper} -r abc"
    assert place.other_agent_exec_text(["/opt/codex", "resume", "abc"]) == "exec /opt/codex resume abc"
    monkeypatch.setenv("BERTH_NO_CMUX_WRAPPER", "1")
    assert place.other_agent_exec_text(["/Users/x/.local/bin/claude", "-r", "abc"]) == "exec /Users/x/.local/bin/claude -r abc"
    monkeypatch.delenv("BERTH_NO_CMUX_WRAPPER")
    monkeypatch.setattr(place, "CMUX_CLAUDE_WRAPPER", str(tmp_path / "missing"))
    assert place.other_agent_exec_text(["/Users/x/.local/bin/claude", "-r", "abc"]) == "exec /Users/x/.local/bin/claude -r abc"
