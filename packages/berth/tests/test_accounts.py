"""Which Claude account each session is on, read from Settings Agent's files (2026-11-03)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest

from berth import accounts
from berth.render import kind_word
from berth.scope import query
from tests.test_state import _insert


@pytest.fixture
def switcher(tmp_path):
    """The account switcher's files, as Settings Agent writes them (made-up numbers)."""
    config = tmp_path / "accounts-config.json"
    data = tmp_path / "accounts-data"
    data.mkdir()
    homestead = tmp_path / ".claude-homestead"
    homestead.mkdir()
    config.write_text(json.dumps({"order": ["uni", "homestead", "personal"],
                                  "folders": {"homestead": str(homestead), "personal": str(tmp_path / ".claude-personal")}}))
    switched = datetime(2026, 11, 3, 18, 0, tzinfo=timezone.utc).timestamp()
    (data / "state.json").write_text(json.dumps({"active": "uni", "last_switch_at": switched}))
    (data / "usage.json").write_text(json.dumps({
        "uni": {"five_hour": {"used_percentage": 42.4}, "seven_day": {"used_percentage": 63}},
        "personal": {"seven_day": {"used_percentage": 100.0}}}))
    return {"homestead": str(homestead)}


def _claude(catalog, berth_id, *, folder=None, played="2026-11-03T19:00:00Z"):
    _insert(catalog, id=berth_id, title=berth_id.title(), kind="other-agent", agent_name="claude",
            agent_session_id=f"s-{berth_id}", state="live", cmux_workspace_id=f"ws-{berth_id}",
            **({"attachments": {"config_dir": folder}} if folder else {}))
    with catalog.immediate():
        catalog.conn.execute("UPDATE berth SET played_at = ? WHERE id = ?", (played, berth_id))


def test_each_folder_maps_to_its_account_and_claude_follows_the_active_one(switcher):
    assert accounts.account_of(None) == "uni" and accounts.account_of(os.path.expanduser("~/.claude")) == "uni"
    assert accounts.account_of(switcher["homestead"]) == "homestead"
    assert accounts.account_of("/somewhere/else") is None
    assert [accounts.tag(name) for name in ("uni", "homestead", "personal")] == ["uni", "hom", "per"]


def test_a_claude_session_opened_before_the_switch_is_stale(switcher):
    assert accounts.stale(None, "2026-11-03T17:00:00Z") is True
    assert accounts.stale(None, "2026-11-03T19:00:00Z") is False
    assert accounts.stale(switcher["homestead"], "2026-11-03T17:00:00Z") is False  # its own folder never switches


def test_sessions_show_the_account_and_the_screen_tags_each_claude_row(catalog, switcher):
    _claude(catalog, "on-uni")
    _claude(catalog, "on-hom", folder=switcher["homestead"])
    _claude(catalog, "old", played="2026-11-03T17:00:00Z")
    _insert(catalog, id="grok", title="Grok", kind="grok", agent_name="grok", state="live", cmux_workspace_id="ws-g")
    rows = {row["id"]: row for group in query(catalog.path, {"folder": "/", "fields": ["account"]})["folders"]
            for row in group["sessions"]}
    assert (rows["on-uni"]["account"], rows["on-hom"]["account"]) == ("uni", "homestead")
    assert rows["old"]["account_stale"] is True and "account_stale" not in rows["on-uni"]
    assert "account" not in rows["grok"]
    assert kind_word("other-agent", "claude", "homestead") == "Claude hom"
    assert kind_word("other-agent", "claude", "uni", True) == "Claude uni*"
    assert kind_word("grok", "grok", "") == "Grok"


def test_the_header_shows_each_accounts_usage(switcher):
    assert accounts.usage_parts() == ["uni 5h 42% 7d 63%", "per 7d 100%"]


def test_without_the_switchers_files_nothing_changes():
    assert accounts.account_of(None) is None and accounts.usage_parts() == []


def test_a_new_or_closed_workspace_reorders_the_sidebar_on_the_next_look(catalog):
    """2026-11-03: a cycle swaps a session's workspace, and its folder showed twice for up to 30 seconds."""
    from berth.model import Workspace
    from berth.ops import mkdir_path
    from berth.sidebar import Pace, sync
    from tests.fakes import FakeCmux

    folder = mkdir_path(catalog, "Team")["id"]
    for name, workspace in (("a", "ws-a"), ("b", "ws-b2"), ("c", "ws-c")):
        _insert(catalog, id=name, title=name, state="live", cmux_workspace_id=workspace, folder_id=folder)
    cmux = FakeCmux()
    for index, name in enumerate(["ws-a", "ws-c"]):  # b's new workspace has not opened yet
        cmux.workspaces[name] = Workspace(id=name, title=name, cwd="/tmp", index=index)
    clock = [0.0]
    pace = Pace(every=30.0, clock=lambda: clock[0])
    sync(catalog.path, cmux, list(cmux.workspaces.values()), pace)
    before = len(cmux.reorders)
    cmux.workspaces["ws-b2"] = Workspace(id="ws-b2", title="b", cwd="/tmp", index=9)  # opens at the end
    clock[0] = 2.0  # one tick later, not thirty seconds
    assert sync(catalog.path, cmux, list(cmux.workspaces.values()), pace) is True
    assert len(cmux.reorders) == before + 1 and set(cmux.reorders[-1]) == {"ws-a", "ws-b2", "ws-c"}
    clock[0] = 4.0
    assert sync(catalog.path, cmux, list(cmux.workspaces.values()), pace) is False  # same set: back to the pace


def test_a_sleep_from_the_screen_says_so(catalog):
    from berth.catalog import insert_intent
    from berth.drain import drain_once
    from tests.fakes import FakeCmux

    _insert(catalog, id="tutor", title="Tutor", kind="grok", agent_name="grok", state="live", cmux_workspace_id="ws-t")
    insert_intent(catalog.path, "sleep", "tutor", {"confirm": True}, datetime.now(timezone.utc))
    drain_once(catalog, FakeCmux(), pid_alive_fn=lambda pid: False)
    [asked] = [event for event in catalog.events_for("tutor") if event["kind"] == "screen_request"]
    detail = json.loads(asked["detail"]) if isinstance(asked["detail"], str) else asked["detail"]
    assert detail["op"] == "sleep"


def test_a_new_claude_starts_on_the_default_accounts_folder(catalog, switcher, tmp_path, monkeypatch):
    """2026-11-04, Dominik: new sessions default to homestead. Settings Agent's default_account says which."""
    from berth import configdir
    from berth.mcp import Api
    from tests.fakes import FakeCmux

    (tmp_path / ".claude-homestead" / "settings.json").write_text("{}")
    config = tmp_path / "accounts-config.json"
    data = json.loads(config.read_text())
    data["default_account"] = "homestead"
    config.write_text(json.dumps(data))
    assert accounts.default_folder() == switcher["homestead"]
    commands = []

    def adopt(db, cmux, hooks, workspace_id, wait_s=0):
        _claude(db, "fresh")
        return {"id": "fresh"}

    monkeypatch.setattr("berth.adopt.adopt_appearing", adopt)
    cmux = FakeCmux()
    real_create = cmux.create_workspace

    def create(**kwargs):
        commands.append(kwargs["command"])
        return real_create(**kwargs)

    monkeypatch.setattr(cmux, "create_workspace", create)
    api = Api(catalog.path, cmux=cmux, caller_workspace=None)
    api("create", {"title": "Fresh", "cwd": str(tmp_path), "folder": "Inbox"})
    assert f"export CLAUDE_CONFIG_DIR={switcher['homestead']}; exec " in commands[-1]
    assert configdir.of(catalog.require_berth("fresh")) == switcher["homestead"]  # recorded: a later default never moves it
    data["default_account"] = "uni"  # the account ~/.claude is on
    config.write_text(json.dumps(data))
    assert accounts.default_folder() is None


def test_an_explicit_folder_or_default_wins_over_the_default_account(switcher, tmp_path):
    from berth.mcp import spawn_command

    assert "CLAUDE_CONFIG_DIR" not in spawn_command("claude", "A job", model="opus", config_dir=None)


def test_create_never_errors_once_the_session_exists_even_before_claudes_hook(catalog, switcher, tmp_path, monkeypatch):
    """2026-11-10, UNI Orch: Claude's session hook came after the 3 s adopt wait, the row was still a shell, and the
    folder step answered KIND_UNSAFE for a session that existed and had its prompt."""
    from berth import configdir
    from berth.mcp import Api
    from tests.fakes import FakeCmux

    (tmp_path / ".claude-homestead" / "settings.json").write_text("{}")
    config = tmp_path / "accounts-config.json"
    data = json.loads(config.read_text())
    data["default_account"] = "homestead"
    config.write_text(json.dumps(data))

    def adopt(db, cmux, hooks, workspace_id, wait_s=0):
        _insert(db, id="early", title="Early", kind="shell", agent_name=None, state="live", cmux_workspace_id=workspace_id)
        return {"id": "early"}

    monkeypatch.setattr("berth.adopt.adopt_appearing", adopt)
    out = Api(catalog.path, cmux=FakeCmux(), caller_workspace=None)("create", {"title": "Early", "cwd": str(tmp_path),
                                                                              "folder": "Inbox"})
    assert out["id"] == "early" and "warnings" not in out and out["config_dir"] == switcher["homestead"]
    assert configdir.of(catalog.require_berth("early")) == switcher["homestead"]
