"""CLI shape and the watcher event name. No live socket."""

from __future__ import annotations

import json

from berth.cli import main
from berth.install import LABEL, agent_plist, render
from berth.model import Surface, Workspace
from berth.watch import WATCH_NAMES, WORKSPACE_CLOSED, WORKSPACE_CREATED, handle_line
from tests.fakes import FakeCmux
from tests.test_state import _insert


def test_pin_and_unpin_from_the_command_line(catalog, capsys):
    _insert(catalog, id="berth-1", title="keep")
    assert main(["--catalog", str(catalog.path), "pin", "--json", "keep"]) == 0
    assert json.loads(capsys.readouterr().out)["pinned"] is True
    assert catalog.require_berth("berth-1")["pinned"] == 1
    assert main(["--catalog", str(catalog.path), "unpin", "berth-1"]) == 0
    assert catalog.require_berth("berth-1")["pinned"] == 0
    capsys.readouterr()
    code = main(["--catalog", str(catalog.path), "pause", "--json", "berth-1"])
    assert code == 1 and json.loads(capsys.readouterr().out)["error"]["code"] == "CMUX_DOWN"


def test_status_json_without_socket(catalog, capsys):
    code = main(["--catalog", str(catalog.path), "status", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["live"] == 0
    assert payload["parked"] == 0
    assert payload["watcher"] in {"up", "down"}
    assert payload["cmux"] == "down"
    assert "child_rss_bytes" not in payload and payload["memory"] is None  # no measure yet (berth/memory.py)


def test_mkdir_and_tree(catalog, capsys):
    code = main(["--catalog", str(catalog.path), "mkdir", "UNI/6.S978"])
    assert code == 0
    capsys.readouterr()
    code = main(["--catalog", str(catalog.path), "--json", "tree"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    names = [folder["name"] for folder in payload["folders"]]
    assert "Inbox" in names
    assert "UNI" in names


def test_ambiguous_query_exits_without_acting(catalog, capsys):
    _insert(catalog, id="a", title="alpha one", cmux_workspace_id="a")
    _insert(catalog, id="b", title="alpha two", cmux_workspace_id="b")
    code = main(["--catalog", str(catalog.path), "--json", "note", "alpha", "changed"])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["matches"]) == 2
    assert catalog.require_berth("a")["note"] == ""


def test_workspace_created_event_adopts_only_that_name(catalog):
    cmux = FakeCmux()
    cmux.workspaces["new-1"] = Workspace(id="new-1", title="appeared", cwd="/tmp/not-a-rule")
    cmux.panels["new-1"] = [Surface(initial_command="sleep 3600", cwd="/tmp/not-a-rule")]
    ignored = handle_line(catalog, cmux, json.dumps({"type": "event", "name": "workspace.closed", "workspace_id": "new-1"}))
    assert ignored is None
    assert catalog.all_berths() == []
    adopted = handle_line(
        catalog,
        cmux,
        json.dumps(
            {
                "type": "event",
                "name": WORKSPACE_CREATED,
                "workspace_id": "new-1",
                "payload": {"workspace_id": "new-1", "title": "appeared", "cwd": "/tmp/not-a-rule"},
            }
        ),
    )
    assert adopted is not None
    assert adopted["title"] == "appeared"
    assert adopted["kind"] == "process"
    again = handle_line(
        catalog,
        cmux,
        json.dumps({"type": "event", "name": WORKSPACE_CREATED, "workspace_id": "new-1"}),
    )
    assert again["id"] == adopted["id"]
    assert len(catalog.all_berths()) == 1


def test_watch_subscribes_to_created_and_closed():
    assert WORKSPACE_CREATED in WATCH_NAMES
    assert WORKSPACE_CLOSED in WATCH_NAMES


def test_workspace_closed_is_a_hint_and_the_observer_parks(catalog):
    from berth.observer import Observer

    session = "01a0aaaa-bbbb-7ccc-8ddd-eeeeeeeeeeee"
    berth_id = _insert(
        catalog,
        state="live",
        kind="grok",
        agent_name="grok",
        agent_session_id=session,
        argv=["grok", "-r", session, "--always-approve"],
        display=f"grok -r {session} --always-approve",
        cmux_workspace_id="ws-closed",
        title="closed-grok",
    )
    cmux = FakeCmux()
    cmux.workspaces["ws-closed"] = Workspace(id="ws-closed", title="closed-grok", cwd="/tmp")
    event = json.dumps({"name": WORKSPACE_CLOSED, "workspace_id": "ws-closed"})
    assert handle_line(catalog, cmux, event) is None
    assert catalog.require_berth(berth_id)["state"] == "live"
    observer = Observer()
    observer.tick(catalog, cmux, list(cmux.workspaces.values()), {})
    assert catalog.require_berth(berth_id)["state"] == "live"
    del cmux.workspaces["ws-closed"]
    observer.tick(catalog, cmux, [], {})
    assert catalog.require_berth(berth_id)["state"] == "live"
    observer.tick(catalog, cmux, [], {})
    row = catalog.require_berth(berth_id)
    assert row["state"] == "parked"
    assert row["park_reason"] == "closed"
    assert row["cmux_workspace_id"] is None
    assert row["last_workspace_id"] == "ws-closed"
    assert row["agent_session_id"] == session
    assert json.loads(row["recipe_argv"]) == ["grok", "-r", session, "--always-approve"]
    assert cmux.closed == []


def test_plist_label_and_watch_argument():
    plist = agent_plist("/usr/local/bin/berth")
    assert plist["Label"] == LABEL == "com.bach.berth.watch"
    assert plist["ProgramArguments"] == ["/usr/local/bin/berth", "watch"]
    assert plist["KeepAlive"] is True
    assert plist["RunAtLoad"] is True
    raw = render(plist)
    assert b"com.bach.berth.watch" in raw
    assert b"<string>watch</string>" in raw


def test_one_failing_event_does_not_stop_the_watcher(catalog, monkeypatch, tmp_path):
    import io
    import subprocess as sp

    from berth import watch
    from berth.errors import BerthError

    lines = [json.dumps({"name": "workspace.created", "workspace_id": "ws-x"}) + "\n",
             json.dumps({"name": "workspace.created", "workspace_id": "ws-y"}) + "\n"]
    seen = []

    def handle(db, cmux, line):
        seen.append(line)
        if len(seen) == 1:
            raise BerthError("CMUX_REJECTED", "Error: Command timed out", {})
        return None

    class Proc:
        stdout = io.StringIO("".join(lines))

        def poll(self):
            return 0

    monkeypatch.setattr(watch, "handle_line", handle)
    monkeypatch.setattr(watch, "seed_cursor", lambda *a: (_ for _ in ()).throw(BerthError("CMUX_DOWN", "x", {})))
    monkeypatch.setattr(watch.subprocess, "Popen", lambda *a, **k: Proc())
    monkeypatch.setattr(watch, "_refresh", lambda *a: None)
    monkeypatch.setattr(watch, "_beat", lambda *a: None)
    cmux = FakeCmux()
    cmux.binary = "cmux"
    cmux._env = lambda: {}
    watch.serve(catalog, cmux)
    assert len(seen) == 2


def test_follow_up_and_urgent_from_the_command_line_with_me(catalog, capsys, monkeypatch):
    """2026-10-31: a session whose MCP server predates follow_up raises it with the CLI."""
    monkeypatch.setattr("berth.cli.Cmux", FakeCmux)
    _insert(catalog, id="berth-1", title="worker", state="live", cmux_workspace_id="WS-1")
    monkeypatch.setenv("CMUX_WORKSPACE_ID", "ws-1")
    assert main(["--catalog", str(catalog.path), "follow-up", "--json", "me", "Sign", "in", "to", "gcloud"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["flag"], out["why"]) == ("follow_up", "Sign in to gcloud") and "other_flags" not in out
    assert main(["--catalog", str(catalog.path), "follow-up", "--json", "me", "Should", "I", "go?"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "BAD_ARGUMENT"
    assert main(["--catalog", str(catalog.path), "urgent", "--json", "worker", "say", "go"]) == 0
    assert json.loads(capsys.readouterr().out)["flag"] == "urgent"
    assert main(["--catalog", str(catalog.path), "follow-up", "--json", "me", "Run", "make", "db"]) == 0
    assert json.loads(capsys.readouterr().out)["note"].startswith("it is urgent")
    assert main(["--catalog", str(catalog.path), "urgent", "--json", "--off", "me"]) == 0
    assert json.loads(capsys.readouterr().out)["lowered"] is True
    assert catalog.require_berth("berth-1")["attention"] is None
    monkeypatch.delenv("CMUX_WORKSPACE_ID")
    assert main(["--catalog", str(catalog.path), "follow-up", "--json", "me", "Sign", "in"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "NOT_FOUND"


def test_dismissing_a_follow_up_lowers_it_and_the_same_one_cannot_come_back(catalog, capsys, monkeypatch):
    """2026-10-31: ⌘⇧X in cmux runs `berth follow-up me --dismiss` in the workspace Dominik is on."""
    monkeypatch.setattr("berth.cli.Cmux", FakeCmux)
    _insert(catalog, id="berth-1", title="worker", state="live", cmux_workspace_id="WS-1")
    monkeypatch.setenv("CMUX_WORKSPACE_ID", "ws-1")
    run = lambda *words: main(["--catalog", str(catalog.path), "follow-up", "--json", *words])
    assert run("me", "Sign in to the console") == 0
    capsys.readouterr()
    assert run("me", "--dismiss") == 0
    assert json.loads(capsys.readouterr().out)["lowered"] is True
    assert catalog.require_berth("berth-1")["attention"] is None
    assert "attention_dismissed" in [event["kind"] for event in catalog.events_for("berth-1")]
    assert run("me", "sign in to the  console") == 1  # the same need, any case or spacing: refused
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "DISMISSED"
    assert run("me", "Run make db so I can finish") == 0  # a different need still comes through
    capsys.readouterr()
    assert run("me", "--dismiss") == 0
    capsys.readouterr()
    assert run("me", "--dismiss") == 0  # nothing up: a no-op
    assert json.loads(capsys.readouterr().out)["lowered"] is False


def test_selected_is_the_workspace_selected_in_cmux(catalog, capsys, monkeypatch):
    """berth-keys runs `berth follow-up selected --dismiss` for ⌘⇧X: the session Dominik is looking at."""
    fake = FakeCmux()
    fake.workspaces["WS-A"] = Workspace(id="WS-A", title="a", cwd="/tmp", selected=False)
    fake.workspaces["WS-B"] = Workspace(id="WS-B", title="b", cwd="/tmp", selected=True)
    monkeypatch.setattr("berth.cli.Cmux", lambda: fake)
    monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
    _insert(catalog, id="a", title="a", state="live", cmux_workspace_id="WS-A")
    _insert(catalog, id="b", title="b", state="live", cmux_workspace_id="WS-B")
    for berth_id, why in (("a", "Sign in to gcloud"), ("b", "Sign in to aa.com")):
        assert main(["--catalog", str(catalog.path), "follow-up", "--json", berth_id, why]) == 0
    capsys.readouterr()
    assert main(["--catalog", str(catalog.path), "follow-up", "--json", "selected", "--dismiss"]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == "b"
    assert catalog.require_berth("b")["attention"] is None and catalog.require_berth("a")["attention"] == "follow_up"


def test_whoami_finds_the_session_by_claudes_id_then_its_workspace_and_refuses_outside(catalog):
    """2026-11-06, Slack watch: "me" from a process, without reading CMUX_WORKSPACE_ID itself."""
    import pytest

    from berth.cli import whoami
    from berth.errors import BerthError
    from tests.test_state import _insert

    _insert(catalog, id="orch", title="Mail Orch", kind="other-agent", agent_name="claude", agent_session_id="s-orch",
            state="live", cmux_workspace_id="ws-orch")
    me = whoami(catalog, {"CLAUDE_CODE_SESSION_ID": "s-orch", "CMUX_WORKSPACE_ID": "ws-other"})
    assert (me["id"], me["title"], me["via"], me["state"]) == ("orch", "Mail Orch", "session", "live")
    assert whoami(catalog, {"CMUX_WORKSPACE_ID": "WS-ORCH"})["via"] == "workspace"
    for environ in ({}, {"CLAUDE_CODE_SESSION_ID": "s-unknown"}, {"CMUX_WORKSPACE_ID": "ws-gone"}):
        with pytest.raises(BerthError) as refused:
            whoami(catalog, environ)
        assert refused.value.code == "NOT_FOUND"
