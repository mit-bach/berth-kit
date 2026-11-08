"""cmux's sidebar and notifications say one thing per session, Berth's (2026-10-29)."""

from __future__ import annotations

import io
import json

from berth.activity import assess
from berth.cmux_sidebar import BEAT_EVERY, REWRITE_EVERY, STATUS_KEY, Publisher, notification, notify_hook, status_of
from berth.errors import BerthError
from tests.fakes import FakeCmux
from tests.test_activity import claude_screen, grok_screen
from tests.test_state import _insert


def _state(screen, kind="other-agent", agent="claude", **options):
    return status_of(assess(screen, kind, agent, **options), kind)


def test_each_thing_a_pane_can_show_is_one_state_with_one_word():
    trust = "Quick safety check: Is this a project you trust?\n\n ❯ No, exit\n   Yes, I trust this folder\n\n Enter to confirm · Esc to cancel\n"
    cases = [
        (_state(claude_screen()), "ready", "Ready"),
        (_state(claude_screen("half a thought")), "ready", "Ready · draft"),
        (_state(claude_screen(strip="⏵⏵ auto mode on · esc to interrupt · ← 1 agent")), "working", "Working"),
        # A turn outranks the shells it started: it is still working.
        (_state(claude_screen(strip="⏵⏵ auto mode on · 1 shell · esc to interrupt · ← 1 agent")), "working", "Working"),
        (_state(claude_screen(strip="⏵⏵ bypass permissions on · 2 shells · ← 1 agent · ↓ to manage")),
         "background", "Waiting on 2 shells"),
        # Subagents come from their own transcripts (subagents_fn), never from the strip's "← N agents".
        (_state(claude_screen(strip="⏵⏵ auto mode on · 1 shell · ← 3 agents · ↓ to manage"), subagents_fn=lambda: 2),
         "background", "Waiting on 2 subagents and 1 shell"),
        (_state(claude_screen(strip="⏵⏵ auto mode on · ← 2 agents · ↓ to manage")), "ready", "Ready"),
        (_state(trust), "needs_you", "Trust directory?"),
        (_state(claude_screen(), program="blocked"), "needs_you", "Needs you"),
        (_state("Welcome to something new\nloading\n"), "unclear", "Unclear"),
        (_state("\n\n   \n"), "starting", "Starting"),
        (_state(grok_screen(), "grok", "grok"), "ready", "Ready"),
        (_state(grok_screen(above="⠋ Working… · send a message to interrupt"), "grok", "grok"), "working", "Working"),
        (_state("", "shell", foreground=["-zsh"]), "shell", "Shell"),
        (_state("", "shell", foreground=["-zsh", "python3"]), "running", "Running python3"),
        (_state("", "berth"), "screen", "Berth"),
    ]
    for status, state, label in cases:
        assert (status.state, status.label) == (state, label)
    # The ones you can talk to are green or amber; the rest are not.
    assert _state(claude_screen()).color == "#30D158" and _state(trust).color == "#FF9F0A"


def test_the_publisher_writes_a_status_line_and_the_sidebar_channel_only_when_they_change():
    cmux = FakeCmux()
    clock = [1000.0]
    publisher = Publisher(cmux, clock=lambda: clock[0])
    ready = _state(claude_screen())
    assert publisher.publish("WS-1", ready, "HOMESTEAD/Learning", "folders") is True
    assert cmux.statuses[("WS-1", STATUS_KEY)] == {"value": "Ready", "icon": "checkmark.circle.fill",
                                                    "color": "#30D158", "priority": 100}
    assert cmux.progress["WS-1"] == (0.0, "berth1|ready|Ready|HOMESTEAD/Learning|folders|1000|0")
    clock[0] += 5
    assert publisher.publish("ws-1", ready, "HOMESTEAD/Learning", "folders") is False  # nothing new
    working = _state(claude_screen(strip="⏵⏵ auto mode on · esc to interrupt"))
    assert publisher.publish("WS-1", working, "HOMESTEAD/Learning", "folders") is True
    assert cmux.statuses[("WS-1", STATUS_KEY)]["value"] == "Working"
    assert cmux.progress["WS-1"][1].startswith("berth1|working|Working|")


def test_the_sidebar_can_tell_a_quiet_watcher_from_a_stopped_one():
    cmux = FakeCmux()
    clock = [1000.0]
    publisher = Publisher(cmux, clock=lambda: clock[0])
    publisher.publish("WS-1", _state(claude_screen()), "A", "tree")
    publisher.publish("WS-2", _state(claude_screen()), "B", "tree")
    clock[0] += BEAT_EVERY - 1
    assert publisher.beat() is False
    clock[0] += 2
    assert publisher.beat() is True  # one row's time is rewritten
    assert cmux.progress["WS-1"][1].endswith(f"|{int(clock[0])}|0")
    publisher.tick({"ws-2"})  # WS-1 closed
    assert list(publisher.sent) == ["ws-2"]
    clock[0] += REWRITE_EVERY
    publisher.tick({"ws-2"})  # in case something else wrote over it
    assert publisher.sent == {}


def test_a_refused_write_is_tried_again_next_look():
    class Refusing(FakeCmux):
        def set_progress(self, workspace_id, value, label):
            raise BerthError("CMUX_DOWN", "cmux is not answering", {})

    publisher = Publisher(Refusing())
    assert publisher.publish("WS-1", _state(claude_screen()), "A") is False
    assert publisher.sent == {}


def test_the_watchers_look_publishes_each_live_session(catalog):
    from berth.activity import refresh
    from berth.model import Workspace

    cmux = FakeCmux()
    _insert(catalog, id="c1", title="Desk", kind="other-agent", agent_name="claude", agent_session_id="s1",
            state="live", cmux_workspace_id="ws-c1", argv=["claude"], display="claude")
    cmux.workspaces["ws-c1"] = Workspace(id="ws-c1", title="Desk", cwd="/tmp")
    cmux.screens["ws-c1"] = claude_screen(strip="⏵⏵ auto mode on · 2 shells · ← 1 agent")
    publisher = Publisher(cmux)
    refresh(catalog, cmux, list(cmux.workspaces.values()),
            publish=lambda row, activity: publisher.row(catalog, row, activity, "folders"))
    assert cmux.statuses[("ws-c1", STATUS_KEY)]["value"] == "Waiting on 2 shells"
    assert cmux.progress["ws-c1"][1].split("|")[1:5] == ["background", "Waiting on 2 shells", "Inbox", "folders"]


def _envelope(**note):
    return {"version": 1, "notification": {"workspaceId": "WS-1", **note},
            "effects": {"record": True, "desktop": True, "sound": True, "reorderWorkspace": True}}


def test_claude_and_grok_notifications_say_the_sessions_name_and_done_or_needs_you():
    titles = {"WS-1": "PR Review Tutor"}.get
    claude = {**_envelope(title="Claude Code", subtitle="Completed in 01-netsuite", body="Tests pass."),
              "agent": {"kind": "claude", "category": "turn-complete"}}
    out = notification(claude, titles)
    assert out["notification"]["title"] == "PR Review Tutor" and out["notification"]["subtitle"] == "Done"
    assert out["notification"]["body"] == "Tests pass." and out["effects"]["reorderWorkspace"] is False
    assert out["effects"]["desktop"] is True
    grok = {**_envelope(title="Grok", subtitle="Completed in STUDIO", body="Split written."),
            "agent": {"kind": "grok", "category": "turn-complete"}}
    assert notification(grok, titles)["notification"]["subtitle"] == "Done"
    asking = {**_envelope(title="Claude Code", body="Claude needs your permission to use Bash"),
              "agent": {"kind": "claude", "category": "needs-permission"}}
    assert notification(asking, titles)["notification"]["subtitle"] == "Needs you"
    unknown = {**_envelope(title="Claude Code", body="x"), "agent": {"kind": "claude"}}
    assert notification({**unknown, "notification": {**unknown["notification"], "workspaceId": "WS-9"}},
                        titles)["notification"]["title"] == "Claude Code"


def test_the_reminder_a_minute_after_a_turn_is_dropped_and_other_notifications_pass():
    for body in ("Claude is waiting for your input", "Waiting for your next prompt"):
        out = notification(_envelope(title="Claude Code", body=body), {}.get)
        assert not any(out["effects"][name] for name in ("record", "desktop", "sound", "markUnread"))
    reminder = {**_envelope(title="Grok", body="anything"), "agent": {"kind": "grok", "category": "idle-reminder"}}
    assert notification(reminder, {}.get)["effects"]["desktop"] is False
    own = _envelope(title="build", body="finished")
    assert notification(own, {"WS-1": "Desk"}.get) == own


def test_the_hook_never_fails_and_reads_titles_from_the_catalog(catalog):
    _insert(catalog, id="c1", title="Desk", kind="other-agent", agent_name="claude", agent_session_id="s1",
            state="live", cmux_workspace_id="WS-AB", argv=["claude"], display="claude")
    envelope = {**_envelope(title="Claude Code", body="ok"), "agent": {"kind": "claude", "category": "turn-complete"}}
    envelope["notification"]["workspaceId"] = "ws-ab"
    out = io.StringIO()
    assert notify_hook(io.StringIO(json.dumps(envelope)), out, catalog.path) == 0
    assert json.loads(out.getvalue())["notification"]["title"] == "Desk"
    out = io.StringIO()
    assert notify_hook(io.StringIO("{broken"), out, catalog.path) == 0 and out.getvalue() == "{broken"


def test_a_session_whose_fork_is_running_is_waiting_not_ready_with_a_draft():
    """2026-10-29: Berth Developer, its own turn over and a fork running, showed Ready · draft; the draft was Claude's grey hint."""
    screen = claude_screen("Message @now-that-we…", strip="⏵⏵ auto mode on (shift+tab to cycle) · ← 1 agent · ↓ to manage")
    screen += "  \n  ◯ main\n  ⏺ now-that-we  Screenshotting the sidebar      17m 21s · ↓ 224.2k tokens\n"
    activity = assess(screen, "other-agent", "claude")
    assert activity.draft is None and activity.agents == 1
    assert status_of(activity, "other-agent").label == "Waiting on 1 subagent"
    idle = claude_screen(strip="⏵⏵ auto mode on (shift+tab to cycle) · ← 1 agent · ↓ to manage") \
        + "  \n  ◯ main\n  ⏺ is-there-any  Handing off OrbStack migration routing          idle\n"
    assert status_of(assess(idle, "other-agent", "claude"), "other-agent").label == "Ready"
    # Research Agent, a minute later: the box back on main, the running fork marked ◯.
    other = claude_screen(strip="⏵⏵ bypass permissions on (shift+tab to cycle) · ← 1 agent · ↓ to manage") \
        + "\n  ⏺ main\n  ◯ at-the-moment  Reading the treasury thread          1m 6s · ↓ 324.1k tokens\n"
    assert status_of(assess(other, "other-agent", "claude"), "other-agent").label == "Waiting on 1 subagent"


# What covers the prompt box, as Claude Code 2.1.312 draws it (its own strings, 2026-10-29).
BTW_ANSWERED = """  Here's exactly where things stand right now:
      Yes — you DO have the access we needed.

    ⇧←/→ to browse · c to copy · f to fork · x to clear history · Esc to close
"""


def _covered(body: str, program=None):
    return status_of(assess("⏺ Working on it.\n\n" + body, "other-agent", "claude", program=program), "other-agent")


def test_a_btw_side_question_is_its_own_state_while_it_is_answered_and_once_it_is():
    """2026-10-29: an open /btw answer showed Unclear, and nothing told answering from answered."""
    answering = _covered("  /btw is the token stored?\n\n    ⠋ Answering…\n\n  Esc to close\n")
    assert (answering.state, answering.label) == ("btw_answering", "Answering /btw")
    answered = _covered("  /btw is the token stored?\n\n" + BTW_ANSWERED)
    assert (answered.state, answered.label, answered.color) == ("btw_answered", "/btw answered", "#64D2FF")
    # A running turn outranks it; once the turn is over, the /btw shows (2026-10-29).
    assert _covered("  /btw is it done?\n\n" + BTW_ANSWERED, program="working").label == "Working"
    assert _covered("  /btw is it done?\n\n" + BTW_ANSWERED, program="done").label == "/btw answered"


def test_each_way_claude_stops_and_asks_has_its_own_word_and_a_menu_you_opened_is_not_one():
    question = ("  ☐ Scope\n\n  Which systems first?\n\n  ❯ 1. Palm\n    2. FISPAN\n    3. Type something.\n"
                "    4. Chat about this\n\n  Enter to select · Tab/Arrow keys to navigate · Esc to cancel\n")
    permission = (" Bash command\n\n   rm -rf build\n\n Do you want to proceed?\n ❯ 1. Yes\n"
                  "   2. Yes, and don't ask again for rm commands in this project\n"
                  "   3. No, and tell Claude what to do differently (esc)\n")
    plan = (" Claude has written up a plan and is ready to execute. Would you like to proceed?\n\n"
            " ❯ 1. Yes, and auto-accept edits\n   2. Yes, and manually approve edits\n   3. No, keep planning\n")
    mcp = " New MCP server found in this project: zoom\n\n ❯ 1. Use this MCP server\n   2. Continue without using this MCP server\n"
    menu = " Select model\n\n ❯ 1. Default (recommended)\n   2. Opus\n\n Enter to select · Esc to cancel\n"
    limit = " Usage limit reached · continuing automatically at 3pm · Esc to cancel\n"
    cases = [(question, "needs_you", "Question"), (permission, "needs_you", "Permission"),
             (plan, "needs_you", "Plan to approve"), (mcp, "needs_you", "New MCP server"),
             (menu, "menu", "Menu open"), (limit, "limited", "Usage limit")]
    for body, state, label in cases:
        status = _covered(body)
        assert (status.state, status.label) == (state, label), body
    # A list cmux says Claude is blocked on is Claude's, not yours.
    assert (_covered(menu, program="blocked").state, _covered(menu, program="blocked").label) == ("needs_you", "Needs you")


def test_a_turn_with_subagents_is_working_and_subagents_after_the_turn_are_waiting():
    with_agents = claude_screen(strip="⏵⏵ auto mode on · esc to interrupt · ← 3 agents · ↓ to manage")
    two = lambda: 2  # noqa: E731  (two of its subagents' transcripts say they run)
    assert status_of(assess(with_agents, "other-agent", "claude", subagents_fn=two), "other-agent").label == "Working"
    after = claude_screen(strip="⏵⏵ auto mode on · ← 3 agents · ↓ to manage")
    assert status_of(assess(after, "other-agent", "claude", subagents_fn=two), "other-agent").label == \
        "Waiting on 2 subagents"


def test_only_a_fork_working_is_waiting_and_main_working_too_is_working():
    """2026-10-29: Berth Developer showed Working while only its fork worked. After /subtask (or f on a /btw answer)
    the prompt box talks to the fork, so the strip's "esc to interrupt" is the fork's, and cmux's program
    status says working for any agent in the process. Main's own transcript decides."""
    screen = claude_screen("Message @now-that-we…", strip="⏵⏵ auto mode on (shift+tab to cycle) · esc to interrupt · "
                           "← 1 agent · ↓ to manage")
    screen += "   \n  ◯ main\n  ⏺ now-that-we  Restarting Berth screen process      3h 9m 13s · ↓ 410.1k tokens\n"
    alone = assess(screen, "other-agent", "claude", program="working")
    assert (status_of(alone, "other-agent").label, alone.agents) == ("Waiting on 1 subagent", 1)
    idle_main = assess(screen, "other-agent", "claude", program="working", main_turn_fn=lambda: False)
    assert status_of(idle_main, "other-agent").label == "Waiting on 1 subagent"
    both = assess(screen, "other-agent", "claude", program="working", main_turn_fn=lambda: True)
    assert status_of(both, "other-agent").label == "Working" and both.reasons[0] == "a turn is running"


def test_mains_turn_is_read_from_its_own_transcript(tmp_path, monkeypatch):
    import json

    from berth import identity
    from berth.activity import main_turn

    monkeypatch.setattr(identity, "claude_projects_root", lambda: tmp_path)
    (tmp_path / "-tmp").mkdir()
    path = tmp_path / "-tmp" / "s-1.jsonl"
    row = {"agent_name": "claude", "agent_session_id": "s-1", "cwd": "/tmp"}
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def write(*items):
        path.write_text("".join(json.dumps({"timestamp": now, **item}) + "\n" for item in items))

    prompt = {"type": "user", "message": {"role": "user", "content": "go"}}
    tool = {"type": "assistant", "message": {"stop_reason": "tool_use", "content": [{"type": "tool_use"}]}}
    done = {"type": "assistant", "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}]}}
    write(prompt, tool)
    assert main_turn(row) is True
    write(prompt, tool, done, {"type": "system", "subtype": "turn_duration", "durationMs": 5})
    assert main_turn(row) is False
    # A fork's lines (old Claude wrote them in the main file) do not start main's turn.
    write(prompt, done, {**tool, "isSidechain": True})
    assert main_turn(row) is False
    write(prompt, tool, {"type": "user", "message": {"content": [{"type": "text", "text": "[Request interrupted by user]"}]}})
    assert main_turn(row) is False



def test_install_takes_the_old_tab_running_dismiss_action_out_of_cmux_json():
    """2026-10-31: the first ⌘⇧X was a cmux command action that ran in a new tab; berth-keys replaced it."""
    from berth.cmux_sidebar import without_dismiss_action

    original = ('{\n  // my comment\n  "actions" : {\n    "berth" : { "type" : "workspace" }\n  },\n  "app": {}\n}\n')
    installed = original.replace('"actions" : {\n', '"actions" : {\n'
                                 '    // berth: dismiss follow-up (berth sidebar install adds it, uninstall removes it)\n'
                                 '    "berth-dismiss-follow-up" : {\n      "type": "command",\n      "command": "x"\n    },\n'
                                 '    // /berth: dismiss follow-up\n')
    assert installed != original and without_dismiss_action(installed) == original
    assert without_dismiss_action(original) == original
