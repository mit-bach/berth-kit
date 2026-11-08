"""Delivery: prompts from programs and agents, in order, each with a receipt (berth/delivery.py, 2026-10-30).

One test per invariant in the module's docstring, then the command line programs use.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from berth import delivery
from berth.activity import Activity
from berth.errors import BerthError
from berth.model import Workspace
from berth.send import Look
from tests.fakes import FakeCmux
from tests.test_activity import claude_screen
from tests.test_state import _insert

READY = Look(Activity(prompt=True), "ready", "", False)


@pytest.fixture(autouse=True)
def no_pauses(monkeypatch):
    import berth.send

    for name in ("SETTLE_SECONDS", "CHECK_SECONDS", "POLL_SECONDS"):
        monkeypatch.setattr(berth.send, name, 0)
BUSY = Look(Activity(busy=True, turn=True, prompt=True), "working", "", False)


def _live(catalog, cmux, berth_id="orch"):
    workspace = f"ws-{berth_id}"
    _insert(catalog, id=berth_id, title=berth_id.title(), kind="other-agent", agent_name="claude",
            agent_session_id=f"s-{berth_id}", state="live", cmux_workspace_id=workspace, argv=["claude"], display="claude")
    cmux.workspaces[workspace] = Workspace(id=workspace, title=berth_id.title(), cwd="/tmp")
    cmux.screens[workspace] = claude_screen()
    return workspace


def _tick(catalog, cmux, look=READY, **kw):
    return delivery.run_due(catalog, cmux, look_fn=lambda row: (lambda: look), closes_fn=set, **kw)


def _pasted(cmux):
    return [text for _, text in getattr(cmux, "pasted", [])]


def test_prompts_are_typed_in_order_one_per_tick_each_once(catalog):
    cmux = FakeCmux()
    _live(catalog, cmux)
    first = delivery.enqueue(catalog, "orch", "one", sender="slack-hooks")
    second = delivery.enqueue(catalog, "orch", "two", sender="Lead Orch")
    assert (first["position"], second["position"]) == (1, 2)
    assert _tick(catalog, cmux, BUSY) == [] and _pasted(cmux) == []  # busy: nothing typed
    assert [o["result"] for o in _tick(catalog, cmux)] == ["delivered"] and _pasted(cmux) == ["one"]
    assert [o["result"] for o in _tick(catalog, cmux)] == ["delivered"] and _pasted(cmux) == ["one", "two"]
    assert _tick(catalog, cmux) == [] and _pasted(cmux) == ["one", "two"]  # never twice
    assert [delivery.receipt(catalog, item["id"])["state"] for item in (first, second)] == ["delivered", "delivered"]


def test_a_claimed_prompt_is_never_typed_twice_and_a_stopped_typing_fails(catalog):
    cmux = FakeCmux()
    _live(catalog, cmux)
    item = delivery.enqueue(catalog, "orch", "one", sender="slack-hooks")
    row = catalog.require_berth("orch")
    assert delivery.type_one(catalog, cmux, row, item, reread=lambda: READY) == "delivered"
    assert delivery.type_one(catalog, cmux, row, item, reread=lambda: READY) == "delivered"  # a second deliverer
    assert _pasted(cmux) == ["one"]
    stuck = delivery.enqueue(catalog, "orch", "two", sender="slack-hooks")
    assert delivery._claim(catalog, stuck)  # a deliverer took it, then stopped
    later = datetime.now(timezone.utc) + timedelta(minutes=5)
    [outcome] = delivery.run_due(catalog, cmux, now=later, look_fn=lambda row: (lambda: READY), closes_fn=set)
    assert outcome["result"] == "failed" and "Not typed again" in delivery.receipt(catalog, stuck["id"])["why"]
    assert _pasted(cmux) == ["one"]


def test_senders_never_clobber_each_other(catalog):
    cmux = FakeCmux()
    _live(catalog, cmux)
    mine = delivery.enqueue(catalog, "orch", "posts 1", sender="slack-hooks", key="amplify")
    theirs = delivery.enqueue(catalog, "orch", "PR 42", sender="Lead Orch", key="amplify")
    newer = delivery.enqueue(catalog, "orch", "posts 1 and 2", sender="slack-hooks", key="amplify")
    assert delivery.receipt(catalog, mine["id"])["state"] == "superseded"  # only its own sender's, same key
    assert delivery.receipt(catalog, theirs["id"])["state"] == "queued"
    with pytest.raises(BerthError) as refused:
        delivery.cancel(catalog, theirs["id"], sender="slack-hooks")
    assert refused.value.code == "PROTECTED"
    assert [item["prompt"] for item in delivery.waiting(catalog, "orch")] == ["PR 42", "posts 1 and 2"]


def test_a_refused_paste_waits_for_its_turn_and_nothing_is_lost(catalog):
    """cmux refuses a paste over someone's draft or into a dialog: nothing was typed, so it waits again."""
    cmux = FakeCmux()
    _live(catalog, cmux)
    item = delivery.enqueue(catalog, "orch", "one", sender="slack-hooks")
    original = cmux.paste_text

    def refuse(workspace_id, text, force=False):
        raise BerthError("CMUX_REJECTED", "paste: the agent prompt in X holds text someone is typing", {})

    cmux.paste_text = refuse
    assert [o["result"] for o in _tick(catalog, cmux)] == ["queued"]
    assert delivery.receipt(catalog, item["id"])["state"] == "queued"
    cmux.paste_text = original
    assert [o["result"] for o in _tick(catalog, cmux)] == ["delivered"]


def test_a_prompt_that_waits_too_long_expires_with_its_reason(catalog):
    cmux = FakeCmux()
    _live(catalog, cmux)
    item = delivery.enqueue(catalog, "orch", "one", sender="slack-hooks", wait_minutes=1)
    later = datetime.now(timezone.utc) + timedelta(minutes=2)
    [outcome] = delivery.run_due(catalog, cmux, now=later, look_fn=lambda row: (lambda: BUSY), closes_fn=set)
    assert outcome["result"] == "expired"
    seen = delivery.receipt(catalog, item["id"])
    assert seen["state"] == "expired" and seen["why"] and seen["settled_at"]


def test_a_closed_session_is_woken_for_a_prompt_that_asks(catalog, monkeypatch):
    cmux = FakeCmux()
    _insert(catalog, id="asleep", title="Asleep", kind="other-agent", agent_name="claude", agent_session_id="s-a",
            state="parked", cmux_workspace_id=None, argv=["claude"], display="claude")
    woke = []
    monkeypatch.setattr("berth.state.play_berth", lambda db, cmux, berth_id, focus=False: woke.append(berth_id))
    delivery.enqueue(catalog, "asleep", "no wake", sender="x")
    _tick(catalog, cmux)
    assert woke == []
    delivery.enqueue(catalog, "asleep", "wake it", sender="slack-hooks", wake=True)
    _tick(catalog, cmux)
    assert woke == ["asleep"]
    assert any(event["kind"] == "delivery_woke" for event in catalog.events_for("asleep"))


def test_every_ending_is_an_event_with_the_sender(catalog):
    cmux = FakeCmux()
    _live(catalog, cmux)
    item = delivery.enqueue(catalog, "orch", "one", sender="slack-hooks", meta={"rows": [7, 9]})
    delivery.cancel(catalog, item["id"], sender="slack-hooks")
    kinds = [(event["kind"], json.loads(event["detail"])["sender"]) for event in catalog.events_for("orch")
             if event["kind"].startswith("delivery_")]
    assert kinds[-2:] in ([("delivery_queued", "slack-hooks"), ("delivery_cancelled", "slack-hooks")],
                          [("delivery_cancelled", "slack-hooks"), ("delivery_queued", "slack-hooks")])
    assert delivery.receipt(catalog, item["id"])["meta"] == {"rows": [7, 9]}


def test_a_send_held_in_the_old_single_slot_moves_to_the_mailbox(tmp_path):
    import sqlite3

    from berth.db import Catalog

    path = tmp_path / "old.sqlite"
    db = Catalog(path).open()
    _insert(db, id="orch", title="Orch", kind="other-agent", agent_name="claude", state="live",
            cmux_workspace_id="ws-orch", argv=["claude"], display="claude")
    db.conn.execute("INSERT INTO deferred (id, berth_id, action, queued_at, not_before, until, force, idle, requested_by, prompt) "
                    "VALUES ('d1', 'orch', 'send', '2026-10-30T10:00:00.000000Z', '2026-10-30T10:00:00.000000Z', "
                    "'2030-01-01T00:00:00.000000Z', 0, 1, 'slack-hooks', 'held prompt')")
    db.conn.commit()
    db.close()
    db = Catalog(path).open()
    [held] = delivery.waiting(db, "orch")
    assert (held["prompt"], held["sender"], held["state"]) == ("held prompt", "slack-hooks", "queued")
    assert db.conn.execute("SELECT count(*) FROM deferred").fetchone()[0] == 0
    db.close()
    del sqlite3


def test_the_command_line_sends_reads_receipts_and_takes_back(catalog, monkeypatch, capsys):
    """What programs call, from a LaunchAgent with no cmux environment: JSON in, JSON out."""
    from berth.cli import main

    cmux = FakeCmux()
    workspace = _live(catalog, cmux)
    cmux.screens[workspace] = claude_screen(strip="⏵⏵ auto mode on · esc to interrupt")  # busy: it waits
    monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
    monkeypatch.setattr("berth.cli.Cmux", lambda: cmux)
    base = ["--catalog", str(catalog.path), "--json"]
    assert main(base + ["send", "orch", "--text", "posts: https://s/1", "--sender", "slack-hooks", "--key", "amplify",
                        "--meta", '{"rows": [1]}', "--wake"]) == 0
    sent = json.loads(capsys.readouterr().out)
    assert sent["state"] == "queued" and sent["position"] == 1 and sent["meta"] == {"rows": [1]}
    assert main(base + ["receipt", sent["id"]]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "queued"
    assert main(base + ["receipts", "--sender", "slack-hooks", "--open"]) == 0
    assert [item["id"] for item in json.loads(capsys.readouterr().out)["receipts"]] == [sent["id"]]
    assert main(base + ["unsend", sent["id"], "--sender", "someone-else"]) != 0
    capsys.readouterr()
    assert main(base + ["unsend", sent["id"], "--sender", "slack-hooks"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "cancelled"
    cmux.screens[workspace] = claude_screen()  # ready, nothing ahead: typed at once, receipt delivered
    assert main(base + ["send", "orch", "--text", "posts: https://s/2", "--sender", "slack-hooks"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "delivered"
    assert _pasted(cmux) == ["posts: https://s/2"]


def test_berth_finds_cmux_without_it_on_the_path(monkeypatch, tmp_path):
    """A LaunchAgent can have a bare PATH; Berth falls back to where cmux installs its CLI."""
    import berth.cmux

    fake = tmp_path / "cmux"
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setattr(berth.cmux.shutil, "which", lambda name: None)
    monkeypatch.setattr(berth.cmux, "INSTALLED", ("/nonexistent/cmux", str(fake)))
    assert berth.cmux.Cmux().binary == str(fake)


def test_once_a_prompt_is_in_the_mailbox_the_sender_gets_its_receipt_whatever_fails_next(catalog):
    """2026-10-30: cmux failed after the enqueue, the sender got an error, and a retry would have typed it twice."""
    cmux = FakeCmux()
    _live(catalog, cmux)

    def broken():
        raise BerthError("CMUX_DOWN", "cmux binary not found (cmux)", {})

    seen = delivery.offer(catalog, cmux, "orch", "one", sender="slack-hooks", look=broken)
    assert seen["state"] == "queued" and seen["position"] == 1
    assert len(delivery.waiting(catalog, "orch")) == 1


def test_the_sender_is_told_once_when_its_receipt_settles_never_polled(catalog, monkeypatch):
    """Dominik, 2026-10-30: "There should be no cycle where every 5 minutes we check for something"."""
    ran = []
    monkeypatch.setattr("subprocess.Popen", lambda argv, env=None, **kw: ran.append((argv, env["BERTH_DELIVERY"],
                                                                                         env["BERTH_STATE"])))
    cmux = FakeCmux()
    _live(catalog, cmux)
    nudge = ["/Users/x/.local/bin/slack-hooks", "nudge"]
    first = delivery.enqueue(catalog, "orch", "posts 1", sender="slack-hooks", key="amplify", notify=nudge)
    second = delivery.enqueue(catalog, "orch", "posts 1 and 2", sender="slack-hooks", key="amplify", notify=nudge)
    assert ran == []  # superseding its own prompt tells it nothing: it knows
    _tick(catalog, cmux)
    assert ran == [(nudge, second["id"], "delivered")]
    _tick(catalog, cmux)
    assert len(ran) == 1  # once
    late = delivery.enqueue(catalog, "orch", "later", sender="slack-hooks", wait_minutes=1, notify=nudge)
    delivery.run_due(catalog, cmux, now=datetime.now(timezone.utc) + timedelta(minutes=2),
                     look_fn=lambda row: (lambda: BUSY), closes_fn=set)
    assert ran[-1] == (nudge, late["id"], "expired")
    assert delivery.receipt(catalog, first["id"])["notify"] == nudge


def test_a_waiting_receipt_says_when_the_watcher_is_why(catalog):
    import time as _time

    cmux = FakeCmux()
    _live(catalog, cmux)
    item = delivery.enqueue(catalog, "orch", "one", sender="slack-hooks")
    assert "stalled" not in delivery.receipt(catalog, item["id"])  # no health written: nothing to say
    health = catalog.path.parent / "watch-health.json"
    health.write_text(json.dumps({"at": _time.time(), "jobs": {"delivery": {"error": "RuntimeError: boom"}}}))
    assert "delivery job is failing" in delivery.receipt(catalog, item["id"])["stalled"]
    health.write_text(json.dumps({"at": _time.time() - 600, "jobs": {}}))
    assert "not ticking" in delivery.receipt(catalog, item["id"])["stalled"]


def test_a_busy_prompt_is_typed_while_claude_works_with_the_note_never_into_a_question_or_a_draft(catalog):
    """2026-11-05, Dominik: "you can send prompts when the agent is busy" (Slack watch decision 6)."""
    from berth.activity import Draft
    from berth.send import BUSY_NOTE

    cmux = FakeCmux()
    _live(catalog, cmux)
    asking = Look(Activity(asking=True, prompt=True), "needs_you", "", False)
    drafted = Look(Activity(busy=True, turn=True, prompt=True, draft=Draft("half a thought")), "working", "", False)
    plain = delivery.enqueue(catalog, "orch", "only when ready", sender="slack-hooks")
    assert plain["busy"] is False and _tick(catalog, cmux, BUSY) == []  # the default waits, as before
    delivery.cancel(catalog, plain["id"], sender="slack-hooks")
    item = delivery.enqueue(catalog, "orch", "a reply landed", sender="slack-watch", busy=True)
    assert item["busy"] is True
    for look in (asking, drafted):
        assert _tick(catalog, cmux, look) == [] and _pasted(cmux) == []  # never into a question or over a draft
    assert [o["result"] for o in _tick(catalog, cmux, BUSY)] == ["delivered"]
    assert _pasted(cmux) == [BUSY_NOTE + "a reply landed"]  # Claude queues it; it finishes its work first
    assert delivery.receipt(catalog, item["id"])["state"] == "delivered"


def test_busy_is_for_claude_prompts_grok_and_slash_commands_wait_for_ready(catalog):
    cmux = FakeCmux()
    _live(catalog, cmux)
    _insert(catalog, id="grok", title="Grok", kind="grok", agent_name="grok", agent_session_id="s-g", state="live",
            cmux_workspace_id="ws-grok")
    assert delivery.enqueue(catalog, "grok", "hi", sender="x", busy=True)["busy"] is False  # mid-turn stops Grok
    assert delivery.enqueue(catalog, "orch", "/compact ", sender="x", busy=True)["busy"] is False


def test_offer_types_a_busy_prompt_at_once_while_claude_works(catalog):
    from berth.send import BUSY_NOTE

    cmux = FakeCmux()
    _live(catalog, cmux)
    out = delivery.offer(catalog, cmux, "orch", "a reply landed", sender="slack-watch", look=lambda: BUSY, busy=True)
    assert out["state"] == "delivered" and _pasted(cmux) == [BUSY_NOTE + "a reply landed"]
    held = delivery.offer(catalog, cmux, "orch", "later", sender="slack-hooks", look=lambda: BUSY)
    assert held["state"] == "queued"


def test_a_receipt_takes_the_ids_first_characters(catalog):
    """2026-11-04, Settings Agent: `berth receipt 1285b419` answered NOT_FOUND for one it had delivered."""
    cmux = FakeCmux()
    _live(catalog, cmux)
    sent = delivery.enqueue(catalog, "orch", "a note", sender="test")
    assert delivery.receipt(catalog, sent["id"][:8])["id"] == sent["id"]
    assert delivery.receipt(catalog, sent["id"][:8].upper())["id"] == sent["id"]
    with pytest.raises(BerthError) as missing:
        delivery.receipt(catalog, "abc")  # too short to be a prefix
    assert missing.value.code == "NOT_FOUND"
    assert delivery.cancel(catalog, sent["id"][:6], sender="test")["state"] == "cancelled"


def test_a_session_in_the_trash_is_gone_for_a_sender(catalog):
    """2026-11-04, slack-watch keys "the agent is gone" on NOT_FOUND: a trashed session answers it, and what
    already waited for it fails at the next tick instead of expiring a day later."""
    cmux = FakeCmux()
    _live(catalog, cmux)
    waiting = delivery.enqueue(catalog, "orch", "a job", sender="slack-watch")
    with catalog.immediate():
        catalog.conn.execute("UPDATE berth SET state = 'deleted' WHERE id = 'orch'")
    _tick(catalog, cmux, BUSY)
    settled = delivery.receipt(catalog, waiting["id"])
    assert (settled["state"], settled["why"]) == ("failed", "the session was deleted")
    with pytest.raises(BerthError) as gone:
        delivery.enqueue(catalog, "orch", "another", sender="slack-watch")
    assert gone.value.code == "NOT_FOUND" and "trash" in gone.value.message
