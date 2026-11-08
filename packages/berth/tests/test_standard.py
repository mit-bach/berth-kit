"""Every Claude session in auto mode at xhigh effort (berth/standard.py, 2026-10-31)."""

from __future__ import annotations

import json
import os
import re
import time

from berth import deferred, delivery, standard
from berth.catalog import ensure
from berth.invariant import check
from berth.model import Workspace
from tests.fakes import FakeCmux
from tests.test_state import _insert

OLD = time.time() - 600


def _transcript(tmp_path, session: str, cwd: str, efforts: list[str], quiet: bool = True):
    folder = tmp_path / "claude-projects" / re.sub(r"[^A-Za-z0-9]", "-", cwd)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{session}.jsonl"
    lines = [json.dumps({"type": "user", "permissionMode": "auto"})]
    for index, effort in enumerate(efforts):
        lines.append(json.dumps({"type": "assistant", "effort": effort, "perTurnEffort": effort,
                                 "timestamp": f"2026-10-31T20:0{index}:00.000Z", "message": {"content": []}}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if quiet:
        os.utime(path, (OLD, OLD))
    return path


def _claude(catalog, tmp_path, berth_id, *, mode="auto", efforts=("xhigh",), status="ready", draft="", quiet=True,
            title=None):
    _insert(catalog, id=berth_id, title=title or berth_id.title(), state="live", kind="other-agent",
            agent_name="claude", agent_session_id=f"s-{berth_id}", cwd="/tmp/work", cmux_workspace_id=f"ws-{berth_id}")
    _transcript(tmp_path, f"s-{berth_id}", "/tmp/work", list(efforts), quiet=quiet)
    with catalog.immediate():
        ensure(catalog.conn)
        catalog.conn.execute("INSERT OR REPLACE INTO runtime (berth_id, busy, status, draft, mode, seen_at) "
                             "VALUES (?, 0, ?, ?, ?, '2026-10-31T20:00:00Z')", (berth_id, status, draft, mode))


def _off(catalog):
    return {item["id"]: item["off"] for item in standard.sessions(catalog) if item["off"]}


def test_mode_comes_from_the_strip_and_effort_from_the_last_reply(catalog, tmp_path):
    _claude(catalog, tmp_path, "good")
    _claude(catalog, tmp_path, "bypass", mode="bypassPermissions")
    _claude(catalog, tmp_path, "high", efforts=("xhigh", "high"))
    _claude(catalog, tmp_path, "planning", mode="plan")
    _claude(catalog, tmp_path, "most", efforts=("max",))
    assert _off(catalog) == {"bypass": ["mode bypassPermissions"], "high": ["effort high"], "planning": ["mode plan"]}


def test_the_last_reply_is_found_past_a_long_tail(tmp_path):
    path = _transcript(tmp_path, "s-long", "/tmp/w", ["high"])
    with path.open("a", encoding="utf-8") as handle:
        handle.write((json.dumps({"type": "user", "message": "x" * 1000}) + "\n") * 100)  # 100 KB after the reply
    assert standard.transcript_effort(path) == ("high", "2026-10-31T20:00:00.000Z")


def test_settings_below_xhigh_are_found(tmp_path):
    path = tmp_path / "claude-settings.json"
    path.write_text(json.dumps({"effortLevel": "xhigh", "modelSettings": {
        "claude-opus-4-8": {"effortLevel": "high"}, "claude-opus-5-5": {"effortLevel": "max"}}}))
    assert standard.settings_findings() == ["modelSettings.claude-opus-4-8.effortLevel is high"]


def test_a_bypass_session_is_set_to_reopen_in_auto_and_cycled_when_idle_once_an_hour(catalog, tmp_path):
    _claude(catalog, tmp_path, "bypass", mode="bypassPermissions")
    cmux = FakeCmux()
    done = standard.tick(catalog, cmux, tmp_path, now=time.time(), selected="ws-other", force=True)
    assert done == [{"id": "bypass", "title": "Bypass", "fix": "cycle into auto"}]
    row = catalog.require_berth("bypass")
    assert json.loads(row["attachments"])["reopen_mode"] == "auto"
    assert [item["action"] for item in deferred.pending(catalog, "bypass")] == ["cycle"]
    deferred.cancel(catalog, "bypass")
    assert standard.tick(catalog, cmux, tmp_path, now=time.time() + 60, selected="ws-other", force=True) == []
    assert standard.tick(catalog, cmux, tmp_path, now=time.time() + 3700, selected="ws-other", force=True)


def test_nothing_is_touched_while_busy_typed_in_looked_at_or_just_written(catalog, tmp_path):
    _claude(catalog, tmp_path, "working", mode="bypassPermissions", status="working")
    _claude(catalog, tmp_path, "typing", mode="bypassPermissions", draft="saved")
    _claude(catalog, tmp_path, "watched", mode="bypassPermissions")
    _claude(catalog, tmp_path, "fresh", mode="bypassPermissions", quiet=False)
    assert standard.tick(catalog, FakeCmux(), tmp_path, now=time.time(), selected="WS-WATCHED", force=True) == []
    assert deferred.pending(catalog) == []


def test_low_effort_gets_effort_xhigh_in_its_mailbox_once_and_counts_as_sent(catalog, tmp_path):
    _claude(catalog, tmp_path, "high", efforts=("high",))
    now = time.time()
    done = standard.tick(catalog, FakeCmux(), tmp_path, now=now, selected="ws-other", force=True)
    assert done == [{"id": "high", "title": "High", "fix": "/effort xhigh"}]
    assert [item["prompt"] for item in delivery.waiting(catalog, "high")] == ["/effort xhigh"]
    assert _off(catalog) == {}  # sent after its last reply: xhigh (sent)
    assert standard.tick(catalog, FakeCmux(), tmp_path, now=now + 60, selected="ws-other", force=True) == []


def test_turned_off_it_only_reports(catalog, tmp_path):
    _claude(catalog, tmp_path, "bypass", mode="bypassPermissions")
    standard.set_correcting(tmp_path, False)
    assert standard.tick(catalog, FakeCmux(), tmp_path, now=time.time(), selected="", force=True) == []
    assert standard.report(catalog, tmp_path)["correcting"] is False
    assert [item["id"] for item in standard.report(catalog, tmp_path)["off"]] == ["bypass"]


def test_a_low_setting_goes_to_claude_config_once(catalog, tmp_path):
    (tmp_path / "claude-settings.json").write_text(json.dumps({"effortLevel": "high"}))
    _claude(catalog, tmp_path, "config", title="Settings Agent")
    done = standard.tick(catalog, FakeCmux(), tmp_path, now=time.time(), selected="", force=True)
    assert done == [{"id": "config", "title": "Settings Agent", "fix": "reported settings"}]
    [prompt] = delivery.waiting(catalog, "config")
    assert "effortLevel is high" in prompt["prompt"] and prompt["sender"] == "berth-standard"
    assert standard.tick(catalog, FakeCmux(), tmp_path, now=time.time(), selected="", force=True) == []


def test_berth_check_lists_sessions_and_settings_off_the_standard(catalog, tmp_path):
    (tmp_path / "claude-settings.json").write_text(json.dumps({"modelSettings": {"m": {"effortLevel": "medium"}}}))
    _claude(catalog, tmp_path, "bypass", mode="bypassPermissions")
    found = {item.rule: item.message for item in check(catalog, [Workspace(id="ws-bypass", title="Bypass", cwd="/tmp")])}
    assert found["off_standard"] == "Bypass: mode bypassPermissions; Berth fixes it when the session is idle"
    assert found["settings_effort"] == "~/.claude/settings.json modelSettings.m.effortLevel is medium (below xhigh)"
