from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

from berth import machine
from berth.db import Catalog
from berth.errors import BerthError
from berth.invariant import Monitor, check
from berth.model import Workspace
from tests.test_state import _insert

PACKAGE = Path(__file__).resolve().parents[1] / "berth"


def _events(db, berth_id):
    return [(row["kind"], json.loads(row["detail"])) for row in db.events_for(berth_id)]


def test_parking_keeps_the_last_workspace_and_the_reason(catalog):
    _insert(catalog, id="b1", state="live", cmux_workspace_id="ws-1")
    row = machine.transition(catalog, "b1", "vanished", reason="cmux_quit")
    assert row["state"] == "parked"
    assert row["cmux_workspace_id"] is None
    assert row["last_workspace_id"] == "ws-1"
    assert row["park_reason"] == "cmux_quit"
    kind, detail = _events(catalog, "b1")[-1]
    assert kind == "transition"
    assert detail["from"] == "live" and detail["to"] == "parked" and detail["reason"] == "cmux_quit"


def test_relink_clears_the_reason(catalog):
    _insert(catalog, id="b1", state="live", cmux_workspace_id="ws-1")
    machine.transition(catalog, "b1", "vanished", reason="closed")
    row = machine.transition(catalog, "b1", "relink", workspace_id="ws-1")
    assert row["state"] == "live"
    assert row["cmux_workspace_id"] == "ws-1"
    assert row["park_reason"] is None
    assert row["parked_at"] is None


def test_a_move_not_in_the_table_changes_nothing(catalog):
    _insert(catalog, id="b1", state="parked", cmux_workspace_id=None)
    with pytest.raises(BerthError) as caught:
        machine.transition(catalog, "b1", "pause_flush")
    assert caught.value.code == "BAD_TRANSITION"
    assert catalog.require_berth("b1")["state"] == "parked"
    assert _events(catalog, "b1") == []


def test_live_needs_a_workspace(catalog):
    _insert(catalog, id="b1", state="parked", cmux_workspace_id=None)
    with pytest.raises(BerthError):
        machine.transition(catalog, "b1", "relink")
    assert catalog.require_berth("b1")["state"] == "parked"


def test_two_claims_cannot_both_win(catalog):
    _insert(catalog, id="b1", state="parked", cmux_workspace_id=None)
    machine.transition(catalog, "b1", "play_claim", workspace_id=None)
    with pytest.raises(BerthError):
        machine.transition(catalog, "b1", "play_claim", workspace_id=None)


def test_a_failed_step_inside_a_transaction_rolls_everything_back(catalog):
    _insert(catalog, id="b1", state="live", cmux_workspace_id="ws-1")
    with pytest.raises(RuntimeError):
        with catalog.immediate():
            machine.transition(catalog, "b1", "vanished", reason="replaced")
            raise RuntimeError("adopt failed")
    row = catalog.require_berth("b1")
    assert row["state"] == "live"
    assert row["cmux_workspace_id"] == "ws-1"


def test_delete_keeps_the_history(catalog):
    _insert(catalog, id="b1", state="live", cmux_workspace_id="ws-1")
    machine.transition(catalog, "b1", "vanished")
    machine.delete(catalog, "b1", reason="user")
    assert catalog.get_berth("b1") is None
    kinds = [kind for kind, _ in _events(catalog, "b1")]
    assert kinds == ["transition", "delete"]


def test_update_berth_refuses_state_columns(catalog):
    _insert(catalog, id="b1", state="live", cmux_workspace_id="ws-1")
    for column in ("state", "cmux_workspace_id", "last_workspace_id", "park_reason"):
        with pytest.raises(RuntimeError):
            with catalog.immediate():
                catalog.update_berth("b1", **{column: None})
    with catalog.immediate():
        catalog.update_berth("b1", title="still allowed")
    assert catalog.require_berth("b1")["title"] == "still allowed"


_RAW = re.compile(
    r"(UPDATE\s+berth\s+SET[^\"']*\b(state|cmux_workspace_id|last_workspace_id|park_reason)\s*="
    r"|DELETE\s+FROM\s+berth\b|INSERT\s+INTO\s+berth\b|\.write_berth\(|\.insert_berth\()",
    re.IGNORECASE,
)


def test_only_the_machine_writes_state_in_the_package():
    allowed = {"machine.py", "db.py", "fixture.py"}
    offenders = []
    for path in sorted(PACKAGE.glob("*.py")):
        if path.name in allowed:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _RAW.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert offenders == []


def test_an_old_catalog_gets_the_link_columns_from_its_events(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE berth (
          id TEXT PRIMARY KEY, folder_id TEXT NOT NULL, title TEXT NOT NULL, kind TEXT NOT NULL,
          state TEXT NOT NULL, protect INTEGER NOT NULL DEFAULT 0, cwd TEXT NOT NULL,
          note TEXT NOT NULL DEFAULT '', recipe_argv TEXT NOT NULL, recipe_display TEXT NOT NULL,
          agent_name TEXT, agent_session_id TEXT, agent_version TEXT, cmux_workspace_id TEXT,
          cmux_surface_id TEXT, cmux_pid INTEGER, attachments TEXT NOT NULL DEFAULT '{}',
          excerpt TEXT NOT NULL DEFAULT '', excerpt_at TEXT, created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL, parked_at TEXT, played_at TEXT
        );
        CREATE TABLE event (id TEXT PRIMARY KEY, berth_id TEXT, at TEXT NOT NULL, kind TEXT NOT NULL,
          detail TEXT NOT NULL DEFAULT '{}');
        INSERT INTO berth VALUES ('p1','f','gone','shell','parked',0,'/','','[]','shell',
          NULL,NULL,NULL,NULL,NULL,NULL,'{}','',NULL,'t','t','t',NULL);
        INSERT INTO berth VALUES ('l1','f','open','shell','live',0,'/','','[]','shell',
          NULL,NULL,NULL,'WS-L',NULL,NULL,'{}','',NULL,'t','t',NULL,NULL);
        INSERT INTO event VALUES ('e1','p1','2026-01-01T00:00:00Z','workspace_vanished',
          '{"via": "closed", "workspace_id": "WS-P"}');
        """
    )
    conn.commit()
    conn.close()
    db = Catalog(path).open()
    parked = db.require_berth("p1")
    assert parked["last_workspace_id"] == "WS-P"
    assert parked["park_reason"] == "closed"
    assert db.require_berth("l1")["last_workspace_id"] == "WS-L"
    db.close()


def _ws(identifier: str, title: str) -> Workspace:
    return Workspace(id=identifier, title=title, cwd="/tmp", custom=True)


def test_check_names_every_rule(catalog):
    _insert(catalog, id="ok", title="fine", state="live", cmux_workspace_id="ws-ok")
    _insert(catalog, id="gone", title="gone", state="live", cmux_workspace_id="ws-gone")
    _insert(catalog, id="named", title="old", state="live", cmux_workspace_id="ws-named")
    _insert(
        catalog, id="twice-a", title="a", state="live", cmux_workspace_id="ws-a", agent_session_id="s-1"
    )
    _insert(
        catalog, id="twice-b", title="b", state="live", cmux_workspace_id="ws-b", agent_session_id="s-1"
    )
    workspaces = [
        _ws("ws-ok", "fine"),
        _ws("ws-named", "new"),
        _ws("ws-a", "a"),
        _ws("ws-b", "b"),
        _ws("ws-orphan", "orphan"),
    ]
    rules = sorted(item.rule for item in check(catalog, workspaces))
    assert rules == ["live_missing", "name_mismatch", "open_unlinked", "session_twice"]


def test_a_clean_catalog_has_no_violations(catalog):
    _insert(catalog, id="ok", title="fine", state="live", cmux_workspace_id="ws-ok")
    _insert(catalog, id="p", title="parked", state="parked", cmux_workspace_id=None)
    assert check(catalog, [_ws("WS-OK", "fine")]) == []


def test_monitor_waits_two_ticks_and_logs_changes_once(catalog, tmp_path):
    _insert(catalog, id="named", title="old", state="live", cmux_workspace_id="ws-1")
    monitor = Monitor()
    first = monitor.observe(catalog, [_ws("ws-1", "new")], tmp_path)
    assert first["ok"] is True
    second = monitor.observe(catalog, [_ws("ws-1", "new")], tmp_path)
    assert [item["rule"] for item in second["violations"]] == ["name_mismatch"]
    monitor.observe(catalog, [_ws("ws-1", "new")], tmp_path)
    logged = [row for row in catalog.conn.execute("SELECT * FROM event WHERE kind = 'invariant'")]
    assert len(logged) == 1
    down = monitor.observe(catalog, None, tmp_path)
    assert down["cmux"] == "down"
    stored = json.loads((tmp_path / "invariant.json").read_text())
    assert stored["cmux"] == "down"


# The third copy of a name (2026-10-31): Berth's title, cmux's title, and the agent's own session name.

def _named_claude(catalog, tmp_path, monkeypatch, *, berth="Chosen", custom=None, generated=None, transcript=True):
    from tests.test_identity import _reset, _use_claude_root

    _reset()
    _use_claude_root(monkeypatch, tmp_path)
    folder = tmp_path / "projects" / "-tmp-work"
    folder.mkdir(parents=True, exist_ok=True)
    if transcript:
        lines = ['{"type": "user"}']
        if generated:
            lines.append(json.dumps({"type": "ai-title", "aiTitle": generated}))
        if custom:
            lines.append(json.dumps({"type": "custom-title", "customTitle": custom}))
        (folder / "c-1.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _insert(catalog, id="c", title=berth, state="live", kind="other-agent", agent_name="claude",
            agent_session_id="c-1", cwd="/tmp/work", cmux_workspace_id="ws-c")


def _rules(catalog, sidebar="Chosen"):
    return [(item.rule, item.message) for item in check(catalog, [_ws("ws-c", sidebar)])]


def test_all_three_copies_agree_and_nothing_is_reported(catalog, tmp_path, monkeypatch):
    _named_claude(catalog, tmp_path, monkeypatch, custom="Chosen")
    assert _rules(catalog) == []


def test_berth_and_cmux_agree_but_claude_calls_it_something_else(catalog, tmp_path, monkeypatch):
    _named_claude(catalog, tmp_path, monkeypatch, custom="Other")
    assert _rules(catalog) == [("session_name", "Berth 'Chosen' vs Claude calls it 'Other'")]


def test_berth_and_claude_agree_but_cmux_does_not(catalog, tmp_path, monkeypatch):
    _named_claude(catalog, tmp_path, monkeypatch, custom="Chosen")
    assert [rule for rule, _ in _rules(catalog, sidebar="Stale")] == ["name_mismatch"]


def test_cmux_and_claude_agree_but_berth_does_not(catalog, tmp_path, monkeypatch):
    _named_claude(catalog, tmp_path, monkeypatch, berth="Old", custom="Chosen")
    assert sorted(rule for rule, _ in _rules(catalog)) == ["name_mismatch", "session_name"]


def test_claudes_generated_title_is_not_a_name(catalog, tmp_path, monkeypatch):
    """2026-10-30, "tooling-1e": Berth and cmux agreed, Claude had only an ai-title, and nothing said so."""
    _named_claude(catalog, tmp_path, monkeypatch, generated="Chosen")
    assert _rules(catalog) == [("session_name", "Berth 'Chosen' vs Claude has no name for it")]


def test_a_conversation_not_on_disk_yet_is_not_reported(catalog, tmp_path, monkeypatch):
    _named_claude(catalog, tmp_path, monkeypatch, transcript=False)
    assert _rules(catalog) == []


def test_a_grok_session_name_is_the_third_copy_too(catalog, tmp_path, monkeypatch):
    from tests.test_identity import _reset, _summary

    _reset()
    monkeypatch.setattr("berth.identity.session_home", lambda cwd: tmp_path / "sessions")
    _summary(tmp_path, "g-1", "Something else", manual=True)
    _insert(catalog, id="g", title="Chosen", state="live", kind="grok", agent_session_id="g-1", cwd="/tmp/g",
            cmux_workspace_id="ws-c")
    assert _rules(catalog) == [("session_name", "Berth 'Chosen' vs Grok calls it 'Something else'")]


def test_a_mismatch_is_reported_while_the_pane_is_busy_and_cleared_once_rename_lands(catalog, tmp_path, monkeypatch):
    """Berth renames; the pane is mid-turn, so /rename waits, the check says so, and once idle all three agree."""
    from berth.identity import refresh_identity
    from berth.ops import rename_berth
    from tests.fakes import FakeCmux
    from tests.test_activity import claude_screen

    _named_claude(catalog, tmp_path, monkeypatch, berth="Old", custom="Old")
    cmux = FakeCmux()
    cmux.workspaces["ws-c"] = Workspace(id="ws-c", title="Old", cwd="/tmp/work", custom=True)
    cmux.screens["ws-c"] = claude_screen(strip="⏵⏵ auto mode on (shift+tab to cycle) · esc to interrupt")
    rename_berth(catalog, cmux, "c", "New")
    assert cmux.sent == []
    rules = [(item.rule, item.message) for item in check(catalog, list(cmux.workspaces.values()))]
    assert rules == [("session_name", "Berth 'New' vs Claude calls it 'Old'; /rename waits for the pane to be idle")]
    cmux.screens["ws-c"] = claude_screen()
    refresh_identity(catalog, cmux, {})
    assert cmux.sent == [("ws-c", "/rename New\n")]
    # Claude writes the name it was given.
    path = tmp_path / "projects" / "-tmp-work" / "c-1.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "custom-title", "customTitle": "New"}) + "\n")
    refresh_identity(catalog, cmux, {})
    assert check(catalog, list(cmux.workspaces.values())) == []
    assert cmux.sent == [("ws-c", "/rename New\n")]  # typed once
