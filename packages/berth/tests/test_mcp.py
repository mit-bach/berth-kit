"""The Berth MCP: the scope model, session references, every tool, and the stdio protocol."""

from __future__ import annotations

import io
import json
from pathlib import Path
from urllib.parse import quote

import pytest

from berth.errors import BerthError
from berth.mcp import TOOLS, Api, ToolError, handle, serve
from berth.ops import mkdir_path
from berth.scope import View, query
from tests.fakes import FakeCmux
from tests.test_state import _insert

CWD = "/tmp/work"


@pytest.fixture(autouse=True)
def grok_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr("berth.state.session_on_disk", lambda root, session: True)
    return tmp_path / "home"


def _row(catalog, berth_id, folder, title, *, state="parked", reason="pause", workspace=None, kind="grok", **extra):
    _insert(catalog, id=berth_id, title=title, state=state, kind=kind, agent_name="grok" if kind == "grok" else extra.pop("agent_name", None),
            agent_session_id=f"sess-{berth_id}" if kind != "shell" else None, cwd=CWD, folder_id=folder,
            cmux_workspace_id=workspace if state == "live" else None,
            argv=["grok", "-r", f"sess-{berth_id}"] if kind == "grok" else ["zsh"], display="grok", **extra)
    if state == "parked":
        with catalog.immediate():
            catalog.conn.execute("UPDATE berth SET park_reason = ? WHERE id = ?", (reason, berth_id))


@pytest.fixture
def tree(catalog):
    """HY {Learning {Deep}, Tutors {Prime}}, PERSONAL, Inbox. The caller is "me" in HY/Learning."""
    folders = {path: mkdir_path(catalog, path)["id"] for path in
               ("HY", "HY/Learning", "HY/Learning/Deep", "HY/Tutors", "HY/Tutors/Prime", "PERSONAL")}
    _row(catalog, "hy-orch", folders["HY"], "HY Orch", state="live", workspace="ws-orch")
    _row(catalog, "me", folders["HY/Learning"], "Learning Desk", state="live", workspace="ws-me")
    _row(catalog, "nap", folders["HY/Learning"], "Napping Notes", reason="sleep")
    _row(catalog, "deep", folders["HY/Learning/Deep"], "Deep Dive")
    _row(catalog, "tutor", folders["HY/Tutors"], "Tutor Queue", state="live", workspace="ws-tutor")
    _row(catalog, "prime", folders["HY/Tutors/Prime"], "Tutor Prime")
    _row(catalog, "mine", folders["PERSONAL"], "Personal Inbox Zero", reason="sleep")
    _row(catalog, "loose", catalog.ensure_inbox(), "Loose shell", kind="shell", state="live", workspace="ws-loose")
    _insert(catalog, id="screen", title="Berth", kind="berth", state="live", cmux_workspace_id="ws-screen",
            folder_id=folders["HY"])
    order = ["me", "nap", "hy-orch", "tutor", "deep", "prime", "mine", "loose"]  # most recently used first
    with catalog.immediate():
        for rank, berth_id in enumerate(order):
            catalog.update_berth(berth_id, last_used_at=f"2026-10-{28 - rank:02d}T12:00:00.000000Z")
    cmux = FakeCmux()
    from berth.model import Workspace

    for workspace in ("ws-orch", "ws-me", "ws-tutor", "ws-loose", "ws-screen"):
        cmux.workspaces[workspace] = Workspace(id=workspace, title=workspace, cwd=CWD)
    api = Api(catalog.path, cmux=cmux, caller_workspace="ws-me")
    api.adopt_wait = 0
    return {"catalog": catalog, "folders": folders, "cmux": cmux, "api": api}


def _shape(result) -> list[tuple[str, int, list[str]]]:
    return [(group["folder"], group["level"], [row["id"] for row in group["sessions"]]) for group in result["folders"]]


# the scope ------------------------------------------------------------


def test_with_no_arguments_an_agent_sees_its_own_folder_and_what_lies_deeper(tree):
    result = query(tree["catalog"].path, {}, caller_workspace="ws-me")
    assert result["scope"]["folder"] == "HY/Learning" and result["scope"]["from"] == "you"
    assert _shape(result) == [("HY/Learning", 0, ["me", "nap"])]
    assert result["folders"][0]["deeper"] == {"folders": 1, "sessions": 1}
    me = result["folders"][0]["sessions"][0]
    assert me == {"id": "me", "title": "Learning Desk", "state": "live", "session": "sess-me", "you": True}
    assert result["folders"][0]["sessions"][1]["state"] == "sleeping"


def test_down_adds_levels_of_subfolders(tree):
    path = tree["catalog"].path
    assert _shape(query(path, {"down": 1}, caller_workspace="ws-me")) == [
        ("HY/Learning", 0, ["me", "nap"]), ("HY/Learning/Deep", 1, ["deep"])]
    everything = query(path, {"folder": "HY", "down": "all"}, caller_workspace="ws-me")
    assert [group["folder"] for group in everything["folders"]] == [
        "HY", "HY/Learning", "HY/Learning/Deep", "HY/Tutors", "HY/Tutors/Prime"]
    assert "screen" not in json.dumps(everything)  # the Berth screen is not a session


def test_up_adds_only_the_ancestors_own_sessions_and_says_what_is_beside(tree):
    result = query(tree["catalog"].path, {"up": 1}, caller_workspace="ws-me")
    assert _shape(result) == [("HY", -1, ["hy-orch"]), ("HY/Learning", 0, ["me", "nap"])]
    assert result["folders"][0]["beside"] == {"folders": 2, "sessions": 2}  # Tutors and Prime, not in the scope
    from_deep = query(tree["catalog"].path, {"folder": "Deep", "up": "all"}, caller_workspace="ws-me")
    assert _shape(from_deep) == [("HY", -2, ["hy-orch"]), ("HY/Learning", -1, ["me", "nap"]), ("HY/Learning/Deep", 0, ["deep"])]
    assert query(tree["catalog"].path, {"up": 9}, caller_workspace="ws-me")["scope"]["up_reached_top"] is True


def test_branches_bring_the_parents_other_subfolders_in_at_your_level(tree):
    path = tree["catalog"].path
    level = query(path, {"up": 1, "branches": True}, caller_workspace="ws-me")
    assert _shape(level) == [("HY", -1, ["hy-orch"]), ("HY/Learning", 0, ["me", "nap"]), ("HY/Tutors", 0, ["tutor"])]
    assert level["folders"][2]["deeper"] == {"folders": 1, "sessions": 1}
    deeper = query(path, {"up": 1, "branches": True, "down": 1}, caller_workspace="ws-me")
    assert [group["folder"] for group in deeper["folders"]] == [
        "HY", "HY/Learning", "HY/Learning/Deep", "HY/Tutors", "HY/Tutors/Prime"]


def test_a_query_searches_everywhere_and_filters_narrow_any_scope(tree):
    path = tree["catalog"].path
    found = query(path, {"query": "tutor"}, caller_workspace="ws-me")
    assert found["scope"]["from"] == "everything" and found["total"] == 2
    assert {row["id"] for group in found["folders"] for row in group["sessions"]} == {"tutor", "prime"}
    assert query(path, {"query": "sess-pri"}, caller_workspace="ws-me")["total"] == 1  # a session id prefix
    live = query(path, {"folder": "/", "state": ["live"], "agent": ["grok"]}, caller_workspace="ws-me")
    assert {row["id"] for group in live["folders"] for row in group["sessions"]} == {"hy-orch", "me", "tutor"}
    sleeping = query(path, {"folder": "/", "down": "all", "state": "sleeping"}, caller_workspace="ws-me")
    assert sorted(row["id"] for group in sleeping["folders"] for row in group["sessions"]) == ["mine", "nap"]


def test_outside_a_berth_session_the_default_is_everything(tree):
    result = query(tree["catalog"].path, {}, caller_workspace="")
    assert result["scope"]["from"] == "root" and result["total"] == 8 and "you" not in result["scope"]


def test_fields_add_metadata_and_pages_hold_the_order(tree):
    path = tree["catalog"].path
    rich = query(path, {"fields": ["agent", "cwd", "workspace", "folder"]}, caller_workspace="ws-me")
    me = rich["folders"][0]["sessions"][0]
    assert (me["agent"], me["cwd"], me["workspace"], me["folder"]) == ("grok", CWD, "ws-me", "HY/Learning")
    assert "workspace" not in rich["folders"][0]["sessions"][1]  # a closed session has none
    with pytest.raises(BerthError):
        query(path, {"fields": ["password"]}, caller_workspace="ws-me")
    first = query(path, {"folder": "/", "down": "all", "limit": 3}, caller_workspace="ws-me")
    rest = query(path, {"folder": "/", "down": "all", "limit": 10, "offset": first["next_offset"]}, caller_workspace="ws-me")
    ids = [row["id"] for page in (first, rest) for group in page["folders"] for row in group["sessions"]]
    assert first["returned"] == 3 and len(ids) == 8 and len(set(ids)) == 8 and "next_offset" not in rest


def test_folders_and_sessions_resolve_by_path_name_id_title_or_me(tree, catalog):
    mkdir_path(catalog, "PERSONAL/Learning")
    view = View(catalog.path, caller_workspace="ws-me")
    assert view.path(view.folder("hy/tutors/PRIME")) == "HY/Tutors/Prime"
    assert view.path(view.folder("Prime")) == "HY/Tutors/Prime"
    with pytest.raises(BerthError) as twice:
        view.folder("Learning")
    assert twice.value.code == "AMBIGUOUS" and twice.value.details["matches"] == ["HY/Learning", "PERSONAL/Learning"]
    with pytest.raises(BerthError) as missing:
        view.folder("Tutorz")
    assert missing.value.code == "NOT_FOUND"
    assert view.session("me").id == "me" and view.session("tutor prime").id == "prime"
    assert view.session("sess-deep").id == "deep" and view.session("ws-tutor").id == "tutor"
    with pytest.raises(BerthError) as both:
        view.session("tu")  # in two titles (an id or session id needs 4+ characters)
    assert both.value.code == "AMBIGUOUS" and len(both.value.details["matches"]) == 2


# the tools ------------------------------------------------------------


def test_status_names_the_caller_and_counts(tree):
    status = tree["api"]("status", {})
    assert status["you"]["title"] == "Learning Desk" and status["you"]["folder"] == "HY/Learning"
    assert status["counts"] == {"live": 4, "sleeping": 2, "parked": 2} and status["cmux"] == "up"


def test_folders_lists_the_tree_with_levels_counts_and_you(tree):
    listed = tree["api"]("folders", {"folder": "HY", "depth": 1})
    assert [(row["folder"], row["level"]) for row in listed["folders"]] == [("HY", 0), ("HY/Learning", 1), ("HY/Tutors", 1)]
    learning = listed["folders"][1]
    assert learning == {"folder": "HY/Learning", "level": 1, "live": 1, "sleeping": 1, "you": True}


def test_session_gives_one_in_full_with_its_screen_and_history(tree, catalog):
    with catalog.immediate():
        catalog.conn.execute("UPDATE berth SET excerpt = ?, excerpt_at = '2026-10-28T10:00:00Z' WHERE id = 'deep'",
                             ("\n".join(f"line {n}" for n in range(60)),))
        catalog.insert_event("deep", "move", {"path": "HY/Learning/Deep"})
    full = tree["api"]("session", {"session": "Deep Dive", "excerpt": True, "history": 5})
    assert full["folder"] == "HY/Learning/Deep" and full["agent"] == "grok" and full["state"] == "parked"
    assert full["excerpt"]["clipped"] is True and full["excerpt"]["text"].endswith("line 59")
    assert full["history"][-1]["event"] == "move"


def test_a_scope_action_is_a_dry_run_until_confirmed_and_skips_the_caller(tree):
    api = tree["api"]
    plan = api("control", {"action": "sleep", "scope": {"folder": "HY", "down": "all"}})
    assert plan["confirmed"] is False
    assert {row["id"] for row in plan["would"]} == {"hy-orch", "tutor"}
    assert plan["skipped"] == [{"id": "me", "title": "Learning Desk", "why": "you"}]
    assert tree["cmux"].closed == []
    done = api("control", {"action": "sleep", "scope": {"folder": "HY", "down": "all"}, "confirm": True})
    assert {row["id"] for row in done["done"]} == {"hy-orch", "tutor"}
    assert {row["state"] for row in done["done"]} == {"sleeping"}
    assert sorted(tree["cmux"].closed) == ["ws-orch", "ws-tutor"]


def test_named_sessions_wake_park_cycle_and_focus(tree):
    api, cmux = tree["api"], tree["cmux"]
    woken = api("control", {"action": "wake", "sessions": ["nap", "deep"]})
    assert [row["result"] for row in woken["done"]] == ["woken", "woken"] and len(cmux.created) == 2
    assert all(call["focus"] is False for call in cmux.created)
    parked = api("control", {"action": "park", "sessions": "tutor queue"})
    assert parked["done"][0]["state"] == "parked"
    again = api("control", {"action": "park", "sessions": "tutor queue"})
    assert again["done"][0]["result"] == "already parked"
    cycled = api("control", {"action": "cycle", "sessions": "HY Orch"})
    assert cycled["done"][0]["result"] == "cycled" and cycled["done"][0]["state"] == "live"
    shown = api("control", {"action": "focus", "sessions": "ws-loose"})
    assert shown["done"][0]["result"] == "focused"
    mine = api("control", {"action": "park", "sessions": "me"})
    assert mine["done"] == [] and mine["queued"][0]["when"] == "idle"  # your own pane: Berth's watcher does it


def test_update_moves_renames_notes_and_pins(tree, catalog):
    api = tree["api"]
    moved = api("update", {"sessions": ["deep", "prime"], "folder": "PERSONAL", "note": "  for   later ", "pin": True})
    assert [row["folder"] for row in moved["sessions"]] == ["PERSONAL", "PERSONAL"]
    assert all(row["pinned"] for row in moved["sessions"])
    row = catalog.require_berth("deep")
    assert (row["note"], row["pinned"]) == ("for later", 1)
    pinned = query(catalog.path, {"folder": "/", "pinned": True}, caller_workspace="ws-me")
    assert {r["id"] for g in pinned["folders"] for r in g["sessions"]} == {"deep", "prime"}
    assert all(r["pinned"] is True for g in pinned["folders"] for r in g["sessions"])
    renamed = api("update", {"sessions": "deep", "title": "Deeper Dive"})
    assert renamed["sessions"][0]["title"] == "Deeper Dive"
    assert "rename_on_open" in catalog.require_berth("deep")["attachments"]  # closed: renamed when it opens
    with pytest.raises(ToolError):
        api("update", {"sessions": ["deep", "prime"], "title": "Twins"})
    with pytest.raises(ToolError):
        api("update", {"sessions": "screen", "note": "x"})


def test_folder_creates_renames_moves_and_deletes(tree, catalog):
    api = tree["api"]
    assert api("folder", {"action": "create", "path": "HY/Learning/New/Leaf"})["path"] == "HY/Learning/New/Leaf"
    api("folder", {"action": "rename", "path": "Leaf", "name": "Petal"})
    api("folder", {"action": "move", "path": "Petal", "into": "PERSONAL"})
    view = View(catalog.path, caller_workspace="ws-me")
    assert view.path(view.folder("Petal")) == "PERSONAL/Petal"
    api("folder", {"action": "archive", "path": "PERSONAL/Petal"})
    assert "Petal" not in json.dumps(api("folders", {}))
    api("folder", {"action": "unarchive", "path": "PERSONAL/Petal"})
    assert "PERSONAL/Petal" in json.dumps(api("folders", {}))
    api("folder", {"action": "delete", "path": "Deep"})
    assert catalog.require_berth("deep")["folder_id"] == tree["folders"]["HY/Learning"]  # its session moved up


def test_delete_lists_first_refuses_you_and_trash_brings_it_back(tree, catalog):
    api = tree["api"]
    plan = api("delete", {"sessions": ["deep", "prime"]})
    assert plan["confirmed"] is False and len(plan["would_delete"]) == 2 and catalog.get_berth("deep") is not None
    with pytest.raises(ToolError) as you:
        api("delete", {"sessions": "me", "confirm": True})
    assert you.value.code == "PROTECTED"
    gone = api("delete", {"sessions": ["deep", "prime"], "confirm": True})
    assert {row["id"] for row in gone["deleted"]} == {"deep", "prime"}
    listed = api("trash", {})
    assert {item["id"] for item in listed["items"]} == {"deep", "prime"}
    back = api("trash", {"action": "restore", "item": "Deep Dive"})
    assert back["restored"]["folder"] == "Inbox" and back["was_in"] == "HY/Learning/Deep"
    with pytest.raises(ToolError):
        api("trash", {"action": "forget", "item": "prime"})
    assert api("trash", {"action": "forget", "item": "prime", "confirm": True})["forgotten"] is True


def test_update_on_a_scope_lists_first_then_pins_them_all(tree, catalog):
    api = tree["api"]
    plan = api("update", {"scope": {"folder": "HY", "down": "all", "state": ["live"]}, "pin": True})
    assert plan["confirmed"] is False and {r["id"] for r in plan["would_change"]} == {"hy-orch", "me", "tutor"}
    assert not catalog.require_berth("tutor")["pinned"]
    done = api("update", {"scope": {"folder": "HY", "down": "all", "state": ["live"]}, "pin": True, "confirm": True})
    assert {r["id"] for r in done["sessions"]} == {"hy-orch", "me", "tutor"} and catalog.require_berth("tutor")["pinned"]
    with pytest.raises(ToolError):
        api("update", {"scope": {"folder": "HY"}, "title": "One name for many"})


def test_create_forks_with_from(tree, grok_home):
    folder = grok_home / ".grok" / "sessions" / quote(CWD, safe="") / "sess-tutor"
    folder.mkdir(parents=True)
    (folder / "summary.json").write_text(json.dumps({"generated_title": "Tutor Queue"}), encoding="utf-8")
    forked = tree["api"]("create", {"from": "tutor"})
    assert forked["title"] == "Tutor Queue ⑂" and forked["folder"] == "HY/Tutors" and forked["state"] == "live"
    assert forked["fork_of"]["id"] == "tutor" and tree["cmux"].created[-1]["focus"] is False
    with pytest.raises(ToolError):
        tree["api"]("create", {"from": "tutor", "prompt": "do more"})


def test_create_starts_a_claude_in_your_folder_with_its_first_prompt(tree, catalog, tmp_path):
    api, cmux = tree["api"], tree["cmux"]
    made = api("create", {"title": "Bob's helper", "cwd": str(tmp_path), "prompt": "Line one.\nIt's \"quoted\" $(not run)", "model": "sonnet"})
    assert made["folder"] == "HY/Learning" and made["agent"] == "claude" and made["state"] == "live"
    command = cmux.created[-1]["command"]
    assert command.startswith("unset FORCE_COLOR NO_COLOR ANTHROPIC_MODEL ANTHROPIC_SMALL_FAST_MODEL; exec ")
    assert "--model sonnet" in command and "--name 'Bob'\"'\"'s helper'" in command
    prompt_file = command.split("$(cat ")[1].rstrip(')"').strip("'")
    assert Path(prompt_file).read_text() == "Line one.\nIt's \"quoted\" $(not run)"
    assert oct(Path(prompt_file).stat().st_mode & 0o777) == "0o600"
    grok = api("create", {"agent": "grok", "title": "Grok helper", "cwd": str(tmp_path), "folder": "PERSONAL"})
    assert grok["folder"] == "PERSONAL" and "exec grok --always-approve" in cmux.created[-1]["command"]
    with pytest.raises(ToolError):
        api("create", {"agent": "grok", "title": "x", "cwd": str(tmp_path), "prompt": "hi"})
    shell = api("create", {"agent": "shell", "title": "Logs", "cwd": str(tmp_path), "command": "tail -f /var/log/system.log"})
    assert cmux.created[-1]["command"] == "tail -f /var/log/system.log" and shell["title"] == "Logs"
    with pytest.raises(ToolError) as berth_screen:
        api("create", {"agent": "shell", "title": "Another screen", "cwd": str(tmp_path), "command": "berth"})
    assert berth_screen.value.code == "PROTECTED"
    with pytest.raises(ToolError):
        api("create", {"title": "", "cwd": str(tmp_path)})


# the protocol ---------------------------------------------------------


def test_eleven_tools_with_closed_schemas():
    assert [tool["name"] for tool in TOOLS] == [
        "status", "folders", "sessions", "session", "control", "send", "create", "update", "folder", "delete", "trash"]
    for tool in TOOLS:
        schema = tool["inputSchema"]
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        assert set(schema["required"]) <= set(schema["properties"])
        assert len(tool["description"]) < 900


def test_the_server_answers_initialize_lists_tools_and_returns_errors_as_tool_results(tree):
    api = tree["api"]
    init = handle(api, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}}})
    assert init["result"]["serverInfo"]["name"] == "berth" and "scope" in init["result"]["instructions"]
    assert handle(api, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    listed = handle(api, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert len(listed["result"]["tools"]) == 11
    call = handle(api, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "sessions", "arguments": {}}})
    assert json.loads(call["result"]["content"][0]["text"])["scope"]["folder"] == "HY/Learning"
    bad = handle(api, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                       "params": {"name": "session", "arguments": {"session": "nobody"}}})
    assert bad["result"]["isError"] is True and json.loads(bad["result"]["content"][0]["text"])["error"]["code"] == "NOT_FOUND"
    assert handle(api, {"jsonrpc": "2.0", "id": 5, "method": "resources/list"})["error"]["code"] == -32601
    stdin = io.StringIO("\n".join(json.dumps(m) for m in (
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "status", "arguments": {}}},
    )) + "\n")
    stdout = io.StringIO()
    serve(api, stdin=stdin, stdout=stdout)
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [reply["id"] for reply in replies] == [1, 2] and "isError" not in replies[1]["result"]


def test_a_new_claude_starts_in_the_mode_claudes_settings_give():
    """2026-10-31: create hard-coded --dangerously-skip-permissions, so agent-made sessions ran in bypass while
    ~/.claude/settings.json said auto. Settings Agent owns that default; Berth passes no permission flag."""
    from berth.mcp import spawn_command

    command = spawn_command("claude", "A job", model="opus")
    assert "--dangerously-skip-permissions" not in command and "--permission-mode" not in command
    assert "--effort xhigh" in command and "--name 'A job'" in command
