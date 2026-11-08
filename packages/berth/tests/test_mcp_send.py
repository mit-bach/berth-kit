"""send: a prompt reaches a session only after Berth looked at what it is doing (2026-10-30)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import berth.send
from berth.activity import Activity
from berth.deferred import pending, queue, run_due
from berth.errors import BerthError
from berth.mcp import Api, ToolError
from berth.model import Workspace
from berth.send import BUSY_NOTE, TYPING_REFUSED
from tests.fakes import FakeCmux
from tests.test_activity import claude_screen, grok_screen
from tests.test_state import _insert

READY = claude_screen()
WORKING = claude_screen(strip="⏵⏵ auto mode on · esc to interrupt")
WAITING = claude_screen(strip="⏵⏵ auto mode on · 2 shells · ← 1 agent")
PERMISSION = ("⏺ Working on it.\n\n Bash command\n\n   rm -rf build\n\n Do you want to proceed?\n ❯ 1. Yes\n"
              "   2. Yes, and don't ask again for rm commands in this project\n"
              "   3. No, and tell Claude what to do differently (esc)\n")


@pytest.fixture(autouse=True)
def no_pauses(monkeypatch):
    for name in ("SETTLE_SECONDS", "CHECK_SECONDS", "POLL_SECONDS"):
        monkeypatch.setattr(berth.send, name, 0)
    monkeypatch.setattr(berth.send, "INTERRUPT_SECONDS", 0.2)


def _session(catalog, cmux, berth_id="worker", *, kind="claude", screen=READY, state="live"):
    workspace = f"ws-{berth_id}"
    # As the catalog files them: Claude is kind other-agent with agent claude (2026-10-30: kind "claude" here hid
    # that send refused every real Claude session).
    _insert(catalog, id=berth_id, title=berth_id.title(), kind="other-agent" if kind == "claude" else kind,
            agent_name=kind if kind != "shell" else "",
            agent_session_id=f"s-{berth_id}", state=state, cmux_workspace_id=workspace if state == "live" else None,
            argv=[kind, "-r", f"s-{berth_id}"], display=kind)
    if state == "live":
        cmux.workspaces[workspace] = Workspace(id=workspace, title=berth_id.title(), cwd="/tmp")
        cmux.screens[workspace] = screen
    return workspace


def _api(catalog, cmux, caller="ws-orch"):
    return Api(catalog.path, cmux=cmux, caller_workspace=caller)


def _pasted(cmux):
    return getattr(cmux, "pasted", [])


def test_a_ready_session_gets_the_prompt_pasted_and_entered(catalog):
    cmux = FakeCmux()
    workspace = _session(catalog, cmux)
    result = _api(catalog, cmux)("send", {"session": "worker", "text": "Run the tests and report."})
    assert result["sent"] is True and result["status"] == "ready"
    assert _pasted(cmux) == [(workspace, "Run the tests and report.")]
    assert cmux.keys == [(workspace, "enter")]


def test_waiting_on_shells_or_subagents_is_ready_because_the_main_agent_takes_a_prompt(catalog):
    cmux = FakeCmux()
    _session(catalog, cmux, screen=WAITING)
    result = _api(catalog, cmux)("send", {"session": "worker", "text": "next"})
    assert result["sent"] is True and result["status"] == "ready" and result["why"]


def test_a_working_session_is_only_looked_at_and_the_caller_sees_what_it_is_doing(catalog):
    cmux = FakeCmux()
    _session(catalog, cmux, screen=WORKING)
    result = _api(catalog, cmux)("send", {"session": "worker", "text": "next job"})
    assert result["sent"] is False and result["status"] == "working"
    assert "esc to interrupt" in result["screen"]["text"] and "when: idle" in result["what_to_do"]
    assert _pasted(cmux) == [] and cmux.keys == []


def test_now_asks_a_working_claude_to_finish_first_unless_as_is(catalog):
    cmux = FakeCmux()
    workspace = _session(catalog, cmux, screen=WORKING)
    api = _api(catalog, cmux)
    result = api("send", {"session": "worker", "text": "Also check the docs.", "when": "now"})
    assert result["sent"] is True and _pasted(cmux)[-1] == (workspace, BUSY_NOTE + "Also check the docs.")
    api("send", {"session": "worker", "text": "Stop and summarize.", "when": "now", "as_is": True})
    assert _pasted(cmux)[-1] == (workspace, "Stop and summarize.")


def test_now_is_refused_for_grok_whose_turn_a_message_stops(catalog):
    cmux = FakeCmux()
    _session(catalog, cmux, kind="grok", screen=grok_screen(below="send a message to interrupt"))
    with pytest.raises(ToolError) as refused:
        _api(catalog, cmux)("send", {"session": "worker", "text": "next", "when": "now"})
    assert refused.value.code == "BUSY" and _pasted(cmux) == []


def test_an_open_permission_is_the_operators_and_nothing_is_typed_or_escaped(catalog):
    cmux = FakeCmux()
    _session(catalog, cmux, screen=PERMISSION)
    api = _api(catalog, cmux)
    for when in (None, "now", "interrupt"):
        args = {"session": "worker", "text": "next"} | ({"when": when} if when else {})
        result = api("send", args)
        assert result["sent"] is False and result["status"] == "needs_you", when
    assert _pasted(cmux) == [] and cmux.keys == []


def test_interrupt_presses_escape_on_a_running_turn_then_sends(catalog):
    class Stops(FakeCmux):
        def send_key(self, workspace_id, key):
            super().send_key(workspace_id, key)
            if key == "escape":
                self.screens[workspace_id] = READY

    cmux = Stops()
    workspace = _session(catalog, cmux, screen=WORKING)
    result = _api(catalog, cmux)("send", {"session": "worker", "text": "Change of plan.", "when": "interrupt"})
    assert result["sent"] is True and result["interrupted"] is True
    assert cmux.keys == [(workspace, "escape"), (workspace, "enter")]
    assert _pasted(cmux) == [(workspace, "Change of plan.")]  # no note: its turn was stopped


def test_someone_typing_in_the_box_is_never_typed_over(catalog):
    class Typing(FakeCmux):
        def paste_text(self, workspace_id, text):
            raise BerthError("CMUX_REJECTED", f"paste: the agent prompt in {workspace_id} {TYPING_REFUSED}, so nothing "
                                              "was sent.", {})

    cmux = Typing()
    _session(catalog, cmux)
    result = _api(catalog, cmux)("send", {"session": "worker", "text": "next"})
    assert result["sent"] is False and "typing" in result["refused"] and cmux.keys == []


def test_a_slash_command_is_typed_on_one_line_and_never_gets_the_note(catalog):
    cmux = FakeCmux()
    workspace = _session(catalog, cmux)
    api = _api(catalog, cmux)
    assert api("send", {"session": "worker", "text": "/compact keep the open questions\n"})["sent"] is True
    assert cmux.sent == [(workspace, "/compact keep the open questions ")] and _pasted(cmux) == []
    cmux.screens[workspace] = WORKING
    with pytest.raises(ToolError) as refused:
        api("send", {"session": "worker", "text": "/compact", "when": "now"})
    assert refused.value.code == "BUSY"
    with pytest.raises(ToolError):
        api("send", {"session": "worker", "text": "/compact one\ntwo", "when": "idle"})


def test_idle_queues_the_prompt_and_the_watcher_sends_it_once_the_session_is_ready(catalog, tmp_path):
    from berth import delivery
    from berth.send import Look

    cmux = FakeCmux()
    workspace = _session(catalog, cmux, screen=WORKING)
    prompt = tmp_path / "job.md"
    prompt.write_text("Write the report to /tmp/report.md.\n", encoding="utf-8")
    api = _api(catalog, cmux)
    result = api("send", {"session": "worker", "prompt_file": str(prompt), "when": "idle", "wait": 30})
    assert result["sent"] is False and result["queued"]["when"] == "idle" and result["queued"]["position"] == 1
    [waiting] = api("session", {"session": "worker"})["prompts"]
    assert waiting["delivery"] == result["queued"]["delivery"] and waiting["position"] == 1
    busy = Look(Activity(busy=True, turn=True, prompt=True), "working", "", False)
    assert delivery.run_due(catalog, cmux, look_fn=lambda row: (lambda: busy), closes_fn=set) == []
    assert _pasted(cmux) == []
    ready = Look(Activity(prompt=True), "ready", "", False)
    [outcome] = delivery.run_due(catalog, cmux, look_fn=lambda row: (lambda: ready), closes_fn=set)
    assert outcome["result"] == "delivered"
    assert _pasted(cmux) == [(workspace, "Write the report to /tmp/report.md.\n")] and cmux.keys == [(workspace, "enter")]
    assert delivery.receipt(catalog, result["queued"]["delivery"])["state"] == "delivered"

def test_a_queued_prompt_expires_at_its_deadline_and_waits_behind_a_queued_close(catalog):
    from berth import delivery
    from berth.send import Look

    cmux = FakeCmux()
    _session(catalog, cmux, screen=WORKING)
    item = delivery.enqueue(catalog, "worker", "next", sender="Orch", wait_minutes=1)
    late = datetime.now(timezone.utc) + timedelta(minutes=2)
    busy = Look(Activity(busy=True, turn=True, prompt=True), "working", "", False)
    [outcome] = delivery.run_due(catalog, cmux, now=late, look_fn=lambda row: (lambda: busy), closes_fn=set)
    assert outcome["result"] == "expired" and _pasted(cmux) == []
    assert delivery.receipt(catalog, item["id"])["why"] == "the session was not ready in time"
    queue(catalog, "worker", "cycle", idle=True)
    second = delivery.enqueue(catalog, "worker", "after the cycle", sender="Orch")  # a close and a prompt coexist
    ready = Look(Activity(prompt=True), "ready", "", False)
    assert delivery.run_due(catalog, cmux, look_fn=lambda row: (lambda: ready)) == []  # the close goes first
    assert delivery.receipt(catalog, second["id"])["state"] == "queued"

def test_send_refuses_yourself_a_closed_session_and_a_shell(catalog):
    cmux = FakeCmux()
    _session(catalog, cmux, "orch")
    _session(catalog, cmux, "asleep", state="parked")
    _session(catalog, cmux, "term", kind="shell")
    api = _api(catalog, cmux, caller="ws-orch")
    for name, code in (("orch", "PROTECTED"), ("asleep", "NOT_LIVE"), ("term", "KIND_UNSAFE")):
        with pytest.raises(ToolError) as refused:
            api("send", {"session": name, "text": "hi"})
        assert refused.value.code == code, name
    assert _pasted(cmux) == [] and cmux.keys == []


def test_a_slash_command_that_names_an_escape_is_refused_before_anything_is_typed():
    """cmux send types a backslash-n as Enter: "/compact keep the \\n notes" would run cut short (2026-10-30)."""
    from berth.errors import BerthError
    from berth.send import check

    for body in (r"/compact keep the \n handling notes", r"/compact tabs (\t) stay", r"/compact drop \r"):
        with pytest.raises(BerthError) as refused:
            check(body)
        assert refused.value.code == "BAD_ARGUMENT"
    check(r"a pasted prompt may say \n freely")  # only typed slash commands are at risk


def test_two_senders_queue_in_order_and_neither_replaces_the_other(catalog):
    """Until 2026-10-30 a new queued send replaced the old one, whoever sent it. Now each waits its turn."""
    cmux = FakeCmux()
    _session(catalog, cmux, screen=WORKING)
    outside = _api(catalog, cmux, caller="")  # a LaunchAgent: no CMUX_WORKSPACE_ID
    first = outside("send", {"session": "worker", "text": "Slack event 1", "when": "idle", "wait": 1440})
    _session(catalog, cmux, "orch")
    second = _api(catalog, cmux, caller="ws-orch")("send", {"session": "worker", "text": "PR 42 is ready", "when": "idle"})
    prompts = outside("session", {"session": "worker"})["prompts"]
    assert [(item["sender"], item["position"]) for item in prompts] == [("an agent outside Berth", 1), ("Orch", 2)]
    assert first["queued"]["position"] == 1 and second["queued"]["position"] == 2
    taken = outside("control", {"action": "cancel", "sessions": "worker"})  # takes back only its own
    assert taken["cancelled"][0]["prompts"] == [first["queued"]["delivery"]]
    assert [item["sender"] for item in outside("session", {"session": "worker"})["prompts"]] == ["Orch"]