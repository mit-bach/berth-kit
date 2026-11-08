"""A second Berth screen, and deletes that can always be overridden (2026-10-30).

Dominik ran ``berth`` in a workspace titled "~". It was filed as a shell
("Running python"), and three deletes of it failed: whichever screen drained
the intent judged "the caller" by its own workspace, and nothing could
override the protection. His words: "I should never be in a case where it's
impossible to delete something from Berth, even if it's a safety check."
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from berth.catalog import insert_intent
from berth.drain import drain_once
from berth.errors import BerthError
from berth.mcp import Api, ToolError
from berth.model import Workspace
from berth.ops import delete_berth
from berth.render import banner_text
from berth.screens import claimed, release_dead
from tests.fakes import FakeCmux
from tests.support import FROZEN, make_app
from tests.test_state import _insert


def _shell(catalog, cmux, berth_id, workspace):
    _insert(catalog, id=berth_id, title="~", kind="shell", state="live", cmux_workspace_id=workspace,
            argv=["zsh"], display="zsh")
    cmux.workspaces[workspace] = Workspace(id=workspace, title="~", cwd="/Users/you")


def test_only_one_screen_can_hold_the_lock(tmp_path):
    from berth.screens import holder, lock

    first, none = lock(tmp_path, "ws-main")
    assert first is not None and none is None
    assert holder(tmp_path)["workspace_id"] == "ws-main"
    second, who = lock(tmp_path, "ws-tilde")  # a second berth, anywhere
    assert second is None and who["workspace_id"] == "ws-main"
    import os

    os.close(first)  # the first screen quits (or dies: the OS lets go either way)
    assert holder(tmp_path) is None
    again, _ = lock(tmp_path, "ws-tilde")
    assert again is not None
    os.close(again)


def test_a_screen_goes_home_or_makes_its_workspace_home(catalog):
    from berth.screens import go_home, place, promote

    cmux = FakeCmux()
    _shell(catalog, cmux, "tilde", "ws-tilde")
    assert place(cmux, None) == ("here", None)
    assert place(cmux, "ws-tilde") == ("promote", None)  # no workspace named Berth: this one becomes home
    promote(catalog, cmux, "ws-tilde")
    row = catalog.require_berth("tilde")
    assert (row["title"], row["kind"], cmux.workspaces["ws-tilde"].title) == ("Berth", "berth", "Berth")  # one name
    assert place(cmux, "ws-tilde") == ("here", None)
    _shell(catalog, cmux, "other", "ws-other")
    where, home = place(cmux, "ws-other")  # berth typed elsewhere: Berth's home exists
    assert where == "home" and home.id == "ws-tilde"
    cmux.terminals = lambda: {"ws-tilde": ["ttys009"]}
    assert go_home(cmux, home, foreground=lambda tty: ["python3.12"]) is False  # something runs there: never typed
    assert cmux.selected == ["ws-tilde"] and cmux.sent == []
    assert go_home(cmux, home, foreground=lambda tty: ["-zsh"]) is True
    assert cmux.sent == [("ws-tilde", "berth")] and cmux.keys[-1] == ("ws-tilde", "enter")


def test_a_claim_from_the_first_fix_is_given_back_once_its_screen_is_gone(catalog):
    cmux = FakeCmux()
    _shell(catalog, cmux, "tilde", "ws-tilde")
    with catalog.immediate():
        catalog.update_berth("tilde", kind="berth", attachments=json.dumps(
            {"screen_of": {"kind": "shell", "recipe_argv": '["zsh"]', "recipe_display": "zsh", "pid": 333}}))
    assert release_dead(catalog, lambda pid: True) == []
    assert release_dead(catalog, lambda pid: False) == ["tilde"]
    row = catalog.require_berth("tilde")
    assert row["kind"] == "shell" and not claimed(row)


def test_two_screens_is_a_violation_named_everywhere(catalog, desk):
    from berth.cmux_sidebar import status_of
    from berth.invariant import check

    cmux = FakeCmux()
    _shell(catalog, cmux, "tilde", "ws-tilde")
    _insert(catalog, id="main", title="Berth", kind="berth", state="live", cmux_workspace_id="ws-main", argv=["berth"])
    cmux.workspaces["ws-main"] = Workspace(id="ws-main", title="Berth", cwd="/")
    assert not [v for v in check(catalog, list(cmux.workspaces.values())) if v.rule == "two_screens"]
    with catalog.immediate():
        catalog.update_berth("tilde", kind="berth")
    [found] = [v for v in check(catalog, list(cmux.workspaces.values())) if v.rule == "two_screens"]
    assert found.berth_id == "tilde" and "d then !" in found.message
    assert status_of(None, "berth", twice=True).label.startswith("Two Berth screens")
    conn = sqlite3.connect(desk)
    conn.execute("UPDATE berth SET kind = 'berth' WHERE id = 'berth-fresh'")
    conn.commit()
    conn.close()
    app = make_app(desk, width=140)
    assert "⚠ 2 Berth screens" in app.text().splitlines()[0]


def test_the_mcp_never_creates_a_screen_and_its_refusals_name_the_override(catalog):
    cmux = FakeCmux()
    _shell(catalog, cmux, "me", "ws-me")
    api = Api(catalog.path, cmux=cmux, caller_workspace="ws-me")
    with pytest.raises(ToolError) as screen:
        api("create", {"agent": "shell", "title": "Berth 2", "command": "berth"})
    assert screen.value.code == "PROTECTED" and "focus" in screen.value.details["hint"]
    with pytest.raises(ToolError) as refused:
        api("delete", {"sessions": "me", "confirm": True})
    assert refused.value.details["override"] == {"force": True, "confirm": True} and "force" in refused.value.message


def test_an_intent_is_judged_by_the_screen_that_wrote_it(catalog, monkeypatch):
    """The main screen deletes the "~" row; the "~" screen drains it. That is not "the caller deleting itself"."""
    cmux = FakeCmux()
    _shell(catalog, cmux, "tilde", "ws-tilde")
    monkeypatch.setenv("CMUX_WORKSPACE_ID", "ws-main")
    insert_intent(catalog.path, "delete", "tilde", {"title": "~"}, FROZEN)
    outcome = drain_once(catalog, cmux, pid_alive_fn=lambda pid: False, caller_workspace_id="ws-tilde")
    assert outcome[0]["result"] == "done" and catalog.get_berth("tilde") is None
    assert cmux.closed == ["ws-tilde"]


def test_protected_deletes_need_force_and_force_always_works(catalog):
    cmux = FakeCmux()
    _shell(catalog, cmux, "tilde", "ws-tilde")
    with catalog.immediate():
        catalog.update_berth("tilde", kind="berth", attachments=json.dumps(
            {"screen_of": {"kind": "shell", "recipe_argv": '["zsh"]', "recipe_display": "zsh", "pid": 111}}))
    with pytest.raises(BerthError) as screen:
        delete_berth(catalog, cmux, "tilde", caller_workspace_id=None)
    assert screen.value.code == "PROTECTED" and "force" in screen.value.message
    delete_berth(catalog, cmux, "tilde", caller_workspace_id=None, force=True)
    assert catalog.get_berth("tilde") is None and cmux.closed == ["ws-tilde"]
    from berth import trash

    restored = trash.restore(catalog, "tilde")
    assert catalog.require_berth("tilde")["kind"] == "shell", restored  # the trash kept what it really was


def test_deleting_your_own_pane_trashes_the_row_before_closing_the_pane(catalog):
    """Closing it ends the process doing the delete, so the close comes last."""
    order = []
    cmux = FakeCmux()
    _shell(catalog, cmux, "me", "ws-me")
    original = cmux.close_workspace

    def close(workspace_id):
        order.append(("close", catalog.get_berth("me") is None))
        original(workspace_id)

    cmux.close_workspace = close
    with pytest.raises(BerthError):
        delete_berth(catalog, cmux, "me", caller_workspace_id="ws-me")
    delete_berth(catalog, cmux, "me", caller_workspace_id="ws-me", force=True)
    assert order == [("close", True)]  # the row was already in the trash when the pane closed


def test_the_mcp_lists_protected_first_and_deletes_them_with_force_and_confirm(catalog):
    cmux = FakeCmux()
    _shell(catalog, cmux, "me", "ws-me")
    _shell(catalog, cmux, "other", "ws-other")
    api = Api(catalog.path, cmux=cmux, caller_workspace="ws-me")
    with pytest.raises(ToolError):
        api("delete", {"sessions": "me", "confirm": True})
    plan = api("delete", {"sessions": ["me", "other"], "force": True})
    assert plan["confirmed"] is False and plan["protected"][0]["why"] == "you" and "warning" in plan
    done = api("delete", {"sessions": ["me", "other"], "force": True, "confirm": True})
    assert [item["id"] for item in done["deleted"]] == ["other", "me"]  # your own pane last
    assert catalog.get_berth("me") is None and catalog.get_berth("other") is None


def test_the_command_line_deletes_its_own_pane_with_force(catalog, monkeypatch, capsys):
    from berth.cli import main

    cmux = FakeCmux()
    _shell(catalog, cmux, "me", "ws-me")
    monkeypatch.setenv("CMUX_WORKSPACE_ID", "ws-me")
    monkeypatch.setattr("berth.cli.Cmux", lambda: cmux)
    assert main(["--catalog", str(catalog.path), "--json", "delete", "me"]) != 0
    capsys.readouterr()
    assert main(["--catalog", str(catalog.path), "--json", "delete", "me", "--force"]) == 0
    assert catalog.get_berth("me") is None


def test_the_screen_warns_about_a_protected_row_and_bang_overrides(desk, monkeypatch):
    monkeypatch.setenv("CMUX_WORKSPACE_ID", "fixture-workspace-fresh")  # this screen runs in the "fresh" workspace
    app = make_app(desk)
    app.focus_berth("berth-fresh")
    app.handle("d")
    assert app.mode == "confirm-delete"
    shown = banner_text(app, app.current)
    assert "protected: this screen's own workspace" in shown and "! deletes it anyway" in shown
    app.handle("y")  # y leaves a protected row alone
    assert not [item for item in app.snap.intents if item.op == "delete"]
    app.focus_berth("berth-fresh")
    app.handle("d")
    app.handle("!")
    [intent] = [item for item in app.snap.intents if item.op == "delete"]
    assert intent.detail["force"] is True and intent.detail["by_workspace"] == "fixture-workspace-fresh"
    app.focus_berth("berth-screen")
    app.handle("d")
    assert "protected: a Berth screen" in banner_text(app, app.current)
    app.handle("n")
    assert app.mode == "tree"
