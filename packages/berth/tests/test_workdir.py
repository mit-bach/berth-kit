"""A session's working directory, set on purpose: never confused with its Berth folder (2026-10-29)."""

from __future__ import annotations

import json

import pytest

from berth.errors import BerthError
from berth.mcp import TOOLS, Api, ToolError
from berth.model import Workspace
from berth.ops import mkdir_path
from berth.state import play_berth
from berth.workdir import GROK_REFUSAL, chosen, set_cwd
from tests.fakes import FakeCmux
from tests.support import write_claude_conversation
from tests.test_state import _insert

IDLE = "─" * 90 + " Desk ─\n❯ \n" + "─" * 100 + "\n  ⏵⏵ auto mode on (shift+tab to cycle)\n"


@pytest.fixture
def dirs(tmp_path):
    old, new = tmp_path / "skills" / "mac-app-config", tmp_path / "STUDIO" / "Mac" / "Config"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    return str(old), str(new)


def _claude(catalog, cmux, berth_id, cwd, *, state="parked", folder=None):
    write_claude_conversation(f"sess-{berth_id}", cwd)
    _insert(catalog, id=berth_id, title=berth_id.title(), kind="other-agent", agent_name="claude", state=state,
            agent_session_id=f"sess-{berth_id}", cwd=cwd, folder_id=folder or catalog.ensure_inbox(),
            cmux_workspace_id=f"ws-{berth_id}" if state == "live" else None,
            argv=["claude", "-r", f"sess-{berth_id}"], display="claude -r")
    if state == "live":
        cmux.workspaces[f"ws-{berth_id}"] = Workspace(id=f"ws-{berth_id}", title=berth_id, cwd=cwd)
        cmux.screens[f"ws-{berth_id}"] = IDLE


def _append_turn(session_id: str, cwd: str) -> None:
    """Claude records a turn: an entry with the directory it ran in, in the session's (old) project file."""
    from berth.identity import claude_transcript

    path = claude_transcript(session_id, cwd)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "assistant", "cwd": cwd, "sessionId": session_id}) + "\n")


def test_a_chosen_directory_survives_play_until_claude_records_a_turn_there(catalog, dirs):
    old, new = dirs
    cmux = FakeCmux()
    folder = mkdir_path(catalog, "STUDIO/Mac")["id"]
    _claude(catalog, cmux, "config", old, folder=folder)
    result = set_cwd(catalog, "config", new, by="test")
    assert result["cwd_changed"] and result["cwd_was"] == old
    row = catalog.require_berth("config")
    assert row["cwd"] == new and row["folder_id"] == folder  # the Berth folder did not move
    play_berth(catalog, cmux, "config", focus=False)
    assert cmux.created[-1]["cwd"] == new and "-r sess-config" in cmux.created[-1]["command"]
    assert catalog.require_berth("config")["cwd"] == new  # the transcript still says old, and lost
    assert json.loads(catalog.require_berth("config")["attachments"])["cwd_set"]["opened"] is True
    _append_turn("sess-config", new)  # the reopened session records a turn in the new directory
    from berth.state import pause_berth

    pause_berth(catalog, cmux, "config", pid_alive_fn=lambda pid: False)
    play_berth(catalog, cmux, "config", focus=False)
    row = catalog.require_berth("config")
    assert row["cwd"] == new and chosen(row) is None  # confirmed: the transcript speaks for it now
    assert "cwd_confirmed" in [event["kind"] for event in catalog.events_for("config")]


def test_without_a_chosen_directory_play_still_follows_the_transcript(catalog, dirs):
    old, new = dirs
    cmux = FakeCmux()
    _claude(catalog, cmux, "plain", old)
    _append_turn("sess-plain", new)
    play_berth(catalog, cmux, "plain", focus=False)
    assert catalog.require_berth("plain")["cwd"] == new


def test_a_directory_must_exist_and_be_absolute_and_grok_is_refused(catalog, dirs):
    old, new = dirs
    cmux = FakeCmux()
    _claude(catalog, cmux, "c", old)
    for bad in ("relative/path", f"{new}/missing", ""):
        with pytest.raises(BerthError) as caught:
            set_cwd(catalog, "c", bad)
        assert caught.value.code == "BAD_ARGUMENT"
    _insert(catalog, id="g", title="Grok", kind="grok", agent_name="grok", agent_session_id="s-g", state="parked",
            cmux_workspace_id=None, cwd=old)
    with pytest.raises(BerthError) as grok:
        set_cwd(catalog, "g", new)
    assert grok.value.code == "KIND_UNSAFE" and "keeps working in the directory it started in" in grok.value.message
    _insert(catalog, id="sh", title="Shell", kind="shell", state="parked", cmux_workspace_id=None, cwd=old)
    set_cwd(catalog, "sh", new)
    play_berth(catalog, cmux, "sh", focus=False)
    assert cmux.created[-1]["cwd"] == new and chosen(catalog.require_berth("sh")) is None  # a shell needs no more


def test_the_mcp_update_sets_cwd_says_when_it_applies_and_cycles_on_request(catalog, dirs, monkeypatch):
    monkeypatch.setattr("berth.place.CMUX_CLAUDE_WRAPPER", "/nonexistent")
    old, new = dirs
    cmux = FakeCmux()
    _claude(catalog, cmux, "orch", old, state="live")
    _claude(catalog, cmux, "worker", old, state="live")
    _claude(catalog, cmux, "busy", old, state="live")
    cmux.screens["ws-busy"] = IDLE.replace("(shift+tab to cycle)", "· esc to interrupt")
    api = Api(catalog.path, cmux=cmux, caller_workspace="ws-orch")
    later = api("update", {"sessions": "worker", "cwd": new})
    [row] = later["sessions"]
    assert (row["folder"], row["cwd"]) == ("Inbox", new) and "next open" in row["cwd_note"]
    assert api("session", {"session": "worker"})["cwd"] == new
    now = api("update", {"sessions": "worker", "cwd": new, "cycle": True})
    assert now["sessions"][0]["cycle"].startswith("cycled") and cmux.created[-1]["cwd"] == new
    refused = api("update", {"sessions": "busy", "cwd": new, "cycle": True})
    assert refused["sessions"][0]["cycle"].startswith("not cycled, busy") and refused["sessions"][0]["cwd"] == new
    mine = api("update", {"sessions": "me", "cwd": new, "cycle": True})
    assert mine["sessions"][0]["cycle"].startswith("queued")  # your own pane: Berth's watcher, after this turn
    with pytest.raises(ToolError):
        api("update", {"sessions": "worker", "cycle": True})  # cycle goes with cwd


def test_a_scope_lists_first_and_skips_grok(catalog, dirs):
    old, new = dirs
    cmux = FakeCmux()
    _claude(catalog, cmux, "a", old)
    _insert(catalog, id="g", title="Grok", kind="grok", agent_name="grok", agent_session_id="s-g", state="parked",
            cmux_workspace_id=None, cwd=old)
    api = Api(catalog.path, cmux=cmux, caller_workspace="")
    plan = api("update", {"scope": {"folder": "/"}, "cwd": new})
    assert plan["confirmed"] is False and plan["new_cwd"] == new
    assert [(row["id"], row["folder"], row["cwd"]) for row in plan["would_change"]] == [("a", "Inbox", old)]
    assert plan["skipped"][0]["why"] == GROK_REFUSAL
    assert catalog.require_berth("a")["cwd"] == old
    done = api("update", {"scope": {"folder": "/"}, "cwd": new, "confirm": True})
    assert done["sessions"][0]["cwd"] == new and catalog.require_berth("g")["cwd"] == old
    with pytest.raises(ToolError) as named:
        api("update", {"sessions": "g", "cwd": new})
    assert named.value.code == "KIND_UNSAFE"


def test_a_claude_fork_into_another_directory_and_grok_is_refused(catalog, dirs, monkeypatch):
    monkeypatch.setattr("berth.place.CMUX_CLAUDE_WRAPPER", "/nonexistent")
    old, new = dirs
    cmux = FakeCmux()
    _claude(catalog, cmux, "source", old, state="live")
    api = Api(catalog.path, cmux=cmux, caller_workspace="")
    fork = api("create", {"from": "source", "cwd": new})
    assert fork["cwd"] == new and fork["folder"] == "Inbox" and cmux.created[-1]["cwd"] == new
    assert "--fork-session" in cmux.created[-1]["command"]
    same = api("create", {"from": "source"})
    assert same["cwd"] == old  # without cwd, as before
    _insert(catalog, id="g", title="Grok", kind="grok", agent_name="grok", agent_session_id="s-g", state="live",
            cmux_workspace_id="ws-g", cwd=old)
    with pytest.raises(ToolError) as grok:
        api("create", {"from": "g", "cwd": new})
    assert grok.value.code == "KIND_UNSAFE"


def test_the_command_line_shows_and_sets_a_working_directory(catalog, dirs, capsys):
    from berth.cli import main

    old, new = dirs
    _claude(catalog, FakeCmux(), "config", old)
    assert main(["--catalog", str(catalog.path), "--json", "cwd", "config"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert (shown["cwd"], shown["folder"]) == (old, "Inbox")
    assert main(["--catalog", str(catalog.path), "--json", "cwd", "config", new]) == 0
    moved = json.loads(capsys.readouterr().out)
    assert (moved["cwd"], moved["cwd_was"], moved["folder"]) == (new, old, "Inbox") and "woken" in moved["cwd_note"]


def test_a_berth_folder_and_a_working_directory_are_never_called_by_each_others_names(catalog, dirs):
    old, new = dirs
    _claude(catalog, FakeCmux(), "x", old)
    api = Api(catalog.path, cmux=FakeCmux(), caller_workspace="")
    rows = api("sessions", {"folder": "/", "fields": ["folder"]})["folders"][0]["sessions"]
    assert rows[0]["folder"] == "Inbox" and rows[0]["cwd"] == old  # a folder never comes without its directory
    for tool in TOOLS:
        for name, spec in tool["inputSchema"]["properties"].items():
            text = spec.get("description", "")
            if name == "cwd":
                assert "directory" in text and "Berth folder" not in text.replace("not the Berth folder", "").replace(
                    "change the Berth folder", ""), (tool["name"], text)
            if name == "folder" and tool["name"] in ("create", "update"):
                assert "Berth folder" in text, (tool["name"], text)
