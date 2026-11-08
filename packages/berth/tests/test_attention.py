"""A session can say it needs you urgently, and the flag comes down when you answer (2026-10-29)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from berth.activity import Activity
from berth.attention import settle
from berth.cmux_sidebar import status_of
from berth.mcp import Api, ToolError
from berth.model import Workspace
from tests.fakes import FakeCmux
from tests.test_deferred import _live


@pytest.fixture(autouse=True)
def no_claude_history(monkeypatch, tmp_path):
    monkeypatch.setattr("berth.recency.claude_history_path", lambda: tmp_path / "no-history.jsonl")


def _stamp(minutes: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _events(catalog, berth_id):
    return [event["kind"] for event in catalog.events_for(berth_id)]


def test_a_session_raises_its_flag_and_everyone_sees_it(catalog):
    cmux = FakeCmux()
    for berth_id in ("orch", "worker", "other"):
        _live(catalog, cmux, berth_id)
    api = Api(catalog.path, cmux=cmux, caller_workspace="ws-worker")
    done = api("update", {"sessions": "me", "urgent": True, "why": "the migration is ready to run; say go"})
    assert done["sessions"][0]["urgent"]["why"] == "the migration is ready to run; say go"
    assert cmux.notified == [("ws-worker", "Worker", "Urgent", "the migration is ready to run; say go")]
    assert "attention_raised" in _events(catalog, "worker")
    seen = Api(catalog.path, cmux=cmux, caller_workspace="ws-orch")("sessions", {"folder": "/", "urgent": True})
    rows = [row for group in seen["folders"] for row in group["sessions"]]
    assert [row["id"] for row in rows] == ["worker"] and rows[0]["urgent"]["why"].startswith("the migration")
    # Red, first in the sidebar, once its turn is over; Working while it runs.
    assert status_of(Activity(), "grok", urgent=True).label == "Urgent"
    assert status_of(Activity(turn=True), "grok", urgent=True).label == "Working"


def test_the_flag_comes_down_when_a_prompt_reaches_the_session_after_it_went_up(catalog):
    cmux = FakeCmux()
    _live(catalog, cmux, "worker")
    Api(catalog.path, cmux=cmux, caller_workspace="ws-worker")("update", {"sessions": "me", "urgent": True})
    before = Workspace(id="ws-worker", title="worker", cwd="/tmp", submitted_at=_stamp(-5), submitted_message="go ahead")
    assert settle(catalog, [before]) == [] and catalog.require_berth("worker")["attention"] == "urgent"
    after = Workspace(id="ws-worker", title="worker", cwd="/tmp", submitted_at=_stamp(1), submitted_message="go ahead")
    assert settle(catalog, [after]) == ["worker"]
    row = catalog.require_berth("worker")
    assert row["attention"] is None and row["attention_why"] == "" and "attention_answered" in _events(catalog, "worker")


def _claude_worker(catalog, cmux):
    from tests.test_state import _insert

    _insert(catalog, id="worker", title="Worker", kind="other-agent", agent_name="claude", agent_session_id="s-worker",
            state="live", cmux_workspace_id="ws-worker", cwd="/tmp/work")
    cmux.workspaces["ws-worker"] = Workspace(id="ws-worker", title="Worker", cwd="/tmp/work")


def _say(entries):
    """Append user entries to the worker's transcript, as Claude Code 2.1.3xx writes them."""
    import json

    from berth.identity import claude_projects_root

    folder = claude_projects_root() / "-tmp-work"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "s-worker.jsonl").open("a", encoding="utf-8") as handle:
        for minutes, text, origin in entries:
            entry = {"type": "user", "timestamp": _stamp(minutes), "message": {"role": "user", "content": text},
                     "origin": {"kind": origin}, "promptSource": "typed" if origin == "human" else "system"}
            if origin == "peer":
                entry["isMeta"] = True
            handle.write(json.dumps(entry) + "\n")


def test_only_dominik_answers_a_flag_not_another_agent(catalog):
    """2026-11-04: a steward's message cleared a session's follow-up, so Dominik never saw it."""
    from berth import delivery
    from berth.attention import note_agent_input

    cmux = FakeCmux()
    _claude_worker(catalog, cmux)
    Api(catalog.path, cmux=cmux, caller_workspace="ws-worker")(
        "update", {"sessions": "me", "follow_up": True, "why": "Sign in to the console"})
    workspaces = list(cmux.workspaces.values())
    _say([(1, 'Another Claude session sent a message: <cross-session-message from="x">done</cross-session-message>',
           "peer"),
          (1, "/effort xhigh", "human")])
    item = delivery.enqueue(catalog, "worker", "There's a new post in Slack to amplify", sender="slack-hooks")
    with catalog.immediate():
        catalog.conn.execute("UPDATE delivery SET state = 'delivered', settled_at = ? WHERE id = ?", (_stamp(1), item["id"]))
    note_agent_input(catalog, "worker", "Your next job is in jobs/next.md", by="STUDIO Orch")
    _say([(2, '<pasted_content id="1"> There\'s a new post in Slack to amplify </pasted_content>', "human"),
          (2, "Your next job is in jobs/next.md", "human")])
    assert settle(catalog, workspaces) == [] and catalog.require_berth("worker")["attention"] == "follow_up"
    _say([(3, "done, I signed in", "human")])  # Dominik, in the box
    assert settle(catalog, workspaces) == ["worker"] and catalog.require_berth("worker")["attention"] is None


def test_another_agents_message_in_cmux_does_not_answer_a_grok_flag(catalog):
    cmux = FakeCmux()
    _live(catalog, cmux, "worker")
    Api(catalog.path, cmux=cmux, caller_workspace="ws-worker")("update", {"sessions": "me", "urgent": True})
    peer = Workspace(id="ws-worker", title="worker", cwd="/tmp", submitted_at=_stamp(1),
                     submitted_message='<cross-session-message from="x">hi</cross-session-message>')
    assert settle(catalog, [peer]) == []
    typed = Workspace(id="ws-worker", title="worker", cwd="/tmp", submitted_at=_stamp(2), submitted_message="continue")
    assert settle(catalog, [typed]) == ["worker"]


def test_urgent_false_lowers_it_and_the_rules_hold(catalog):
    cmux = FakeCmux()
    _live(catalog, cmux, "worker")
    api = Api(catalog.path, cmux=cmux, caller_workspace="ws-worker")
    api("update", {"sessions": "me", "urgent": True, "why": "x"})
    api("update", {"sessions": "me", "urgent": False})
    assert catalog.require_berth("worker")["attention"] is None and "attention_lowered" in _events(catalog, "worker")
    for bad in ({"sessions": "me", "why": "x"}, {"sessions": "me", "urgent": "yes"},
                {"scope": {"folder": "/"}, "urgent": True, "confirm": True}):
        with pytest.raises(ToolError):
            api("update", bad)


# Follow up (2026-10-31, Dominik): the turn ended, the work did not.

def test_a_session_says_its_work_is_not_done_and_it_shows_until_the_next_prompt(catalog):
    cmux = FakeCmux()
    for berth_id in ("orch", "worker"):
        _live(catalog, cmux, berth_id)
    api = Api(catalog.path, cmux=cmux, caller_workspace="ws-worker")
    done = api("update", {"sessions": "me", "follow_up": True, "why": "Run make db so I can finish the last 3 migrations"})
    assert done["sessions"][0]["follow_up"]["why"] == "Run make db so I can finish the last 3 migrations"
    assert "urgent" not in done["sessions"][0]
    assert getattr(cmux, "notified", []) == []  # no notification: it waits in the sidebar
    row = catalog.require_berth("worker")
    assert row["attention"] == "follow_up"
    seen = Api(catalog.path, cmux=cmux, caller_workspace="ws-orch")("sessions", {"folder": "/", "follow_up": True})
    rows = [item for group in seen["folders"] for item in group["sessions"]]
    assert [item["id"] for item in rows] == ["worker"] and rows[0]["follow_up"]["why"].startswith("Run make db")
    urgent = Api(catalog.path, cmux=cmux, caller_workspace="ws-orch")("sessions", {"folder": "/", "urgent": True})
    assert [item for group in urgent["folders"] for item in group["sessions"]] == []  # a follow-up is not urgent
    # Magenta Follow up instead of Ready once its turn is over; Working while a turn runs; a question outranks it.
    shown = status_of(Activity(prompt=True), "other-agent", flag="follow_up")
    assert (shown.state, shown.label, shown.color) == ("follow_up", "Follow up", "#FF2DD1")
    assert status_of(Activity(turn=True), "other-agent", flag="follow_up").label == "Working"
    assert status_of(Activity(asking=True), "other-agent", flag="follow_up").state == "needs_you"
    assert status_of(Activity(shells=1), "other-agent", flag="follow_up").state == "follow_up"
    # A prompt from before it went up does not clear it; the next one does.
    before = Workspace(id="ws-worker", title="worker", cwd="/tmp", submitted_at=_stamp(-5), submitted_message="go ahead")
    assert settle(catalog, [before]) == []
    after = Workspace(id="ws-worker", title="worker", cwd="/tmp", submitted_at=_stamp(1), submitted_message="go ahead")
    assert settle(catalog, [after]) == ["worker"] and catalog.require_berth("worker")["attention"] is None


def test_urgent_outranks_follow_up_and_each_false_lowers_only_its_own(catalog):
    cmux = FakeCmux()
    _live(catalog, cmux, "worker")
    api = Api(catalog.path, cmux=cmux, caller_workspace="ws-worker")
    api("update", {"sessions": "me", "urgent": True, "why": "prod is down"})
    kept = api("update", {"sessions": "me", "follow_up": True, "why": "Sign in to the console"})["sessions"][0]
    assert kept["urgent"]["why"] == "prod is down" and "follow_up" not in kept and kept["note"].startswith("it is urgent")
    api("update", {"sessions": "me", "follow_up": False})
    assert catalog.require_berth("worker")["attention"] == "urgent"  # follow_up false leaves urgent alone
    api("update", {"sessions": "me", "urgent": False})
    api("update", {"sessions": "me", "follow_up": True, "why": "Sign in to the console"})
    api("update", {"sessions": "me", "urgent": False})
    assert catalog.require_berth("worker")["attention"] == "follow_up"  # and urgent false leaves a follow-up
    api("update", {"sessions": "me", "urgent": True, "why": "now it is urgent"})
    assert catalog.require_berth("worker")["attention"] == "urgent"
    api("update", {"sessions": "me", "urgent": False})
    api("update", {"sessions": "me", "follow_up": True, "why": "Sign in to the console"})
    api("update", {"sessions": "me", "follow_up": False})
    assert catalog.require_berth("worker")["attention"] is None
    for bad in ({"sessions": "me", "follow_up": "yes"}, {"sessions": "me", "urgent": True, "follow_up": True},
                {"scope": {"folder": "/"}, "follow_up": True, "confirm": True}, {"sessions": "me", "why": "x"}):
        with pytest.raises(ToolError):
            api("update", bad)
    levels = [event["detail"] for event in catalog.events_for("worker") if event["kind"] == "attention_raised"]
    assert levels  # each raise is an event with its level


def test_the_berth_screen_marks_a_follow_up(catalog):
    cmux = FakeCmux()
    _live(catalog, cmux, "worker")
    Api(catalog.path, cmux=cmux, caller_workspace="ws-worker")("update", {"sessions": "me", "follow_up": True,
                                                                     "why": "Sign in to the console"})
    from datetime import datetime, timezone

    from berth.catalog import load
    from berth.tree import berth_as_row

    row = berth_as_row(load(catalog.path), "worker", datetime.now(timezone.utc))
    assert row.follow_up is True and row.urgent is False


def test_a_follow_up_is_an_action_only_the_operator_can_take_not_a_question_or_an_approval(catalog):
    """2026-10-31, Dominik: the flag made agents ask more, and ask for approvals. Those are refused; nothing changes."""
    cmux = FakeCmux()
    _live(catalog, cmux, "worker")
    api = Api(catalog.path, cmux=cmux, caller_workspace="ws-worker")
    for asking in ("Your call: rename the Trip Planner to Travel Agent where it is, or keep it",
                   "Yes or no: add the two steward rules to ROOTS.md",
                   "Say send and Mail Orch sends Alex the Cursor kit v2.1.0",
                   "Who gets Berth kits to teammates: Kit Agent or me?", "Should I add it?", "confirm the split", ""):
        with pytest.raises(ToolError) as refused:
            api("update", {"sessions": "me", "follow_up": True, "why": asking, "note": "changed"})
        assert "only Dominik can do" in refused.value.message
    row = catalog.require_berth("worker")
    assert row["attention"] is None and (row["note"] or "") == ""  # refused before anything changed
    done = api("update", {"sessions": "me", "follow_up": True,
                          "why": "Decide whether to add the LaunchAgent (reserved for you by FORGE/CLAUDE.md)"})
    assert done["sessions"][0]["follow_up"]["why"].startswith("Decide whether")


def test_one_need_has_one_flag_and_the_second_asker_is_told_the_line_to_end_with(catalog):
    """2026-10-31: Kit Agent and Kit Builder both flagged the Cursor kit for Alex. Now Berth refuses the second."""
    cmux = FakeCmux()
    for berth_id in ("handoff", "builder"):
        _live(catalog, cmux, berth_id)
    first = Api(catalog.path, cmux=cmux, caller_workspace="ws-handoff")(
        "update", {"sessions": "me", "follow_up": True, "why": "Send Alex the Cursor kit v2.1.0 from Slack"})
    assert set(first["sessions"][0]) >= {"follow_up"} and "other_flags" not in first["sessions"][0]  # nothing extra
    with pytest.raises(ToolError) as refused:
        Api(catalog.path, cmux=cmux, caller_workspace="ws-builder")(
            "update", {"sessions": "me", "follow_up": True,
                       "why": "Have Mail Orch send Alex the Cursor kit v2.1.0"})
    assert refused.value.code == "DUPLICATE" and "end your reply with: Follow-up: see Handoff." in refused.value.message
    assert catalog.require_berth("builder")["attention"] is None
    Api(catalog.path, cmux=cmux, caller_workspace="ws-builder")(  # a different need still goes up
        "update", {"sessions": "me", "follow_up": True, "why": "Sign in to gcloud for the service account"})
    assert catalog.require_berth("builder")["attention"] == "follow_up"
