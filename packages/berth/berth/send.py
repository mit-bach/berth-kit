"""Send a prompt to a live session, after looking at what it is doing (2026-10-30).

Typing into a pane without looking had three ways to go wrong: a prompt sent
mid-turn into Grok stops its turn; text typed into an open question or
permission picks an answer; and a paste into a box someone is typing in mixes
the two. So every prompt an agent sends another session goes through here
(the Berth MCP's ``send``, Berth's watcher for a send queued "when idle", and
the guard on cmux-mcp's ``input``).

``look`` reads the pane once (``berth/activity.py``) and ``situation`` says
what that means for a prompt:

- ``ready``: the prompt is on screen and no turn runs. Background shells or
  subagents may still run; the main agent takes a prompt at once then.
- ``working``: a turn is running.
- ``needs_you``: a question, permission, plan, or startup question is open.
  That is the operator's to answer; a prompt never goes into it.
- ``limited``: the usage limit stopped it.
- ``overlay``: a menu or the /btw panel covers the prompt.
- ``starting``: no prompt Berth recognizes yet.

Text in the prompt box is not judged from the screen: Claude's dim suggested
next prompt reads the same as typing, and cmux's screen API carries no
styling. cmux's own paste and send refuse when the box holds text someone is
typing, or a dialog is open, and that refusal is the check (``BUSY``, with
``refused`` ``typing`` or ``dialog`` in its details).

A slash command runs only when typed: Claude sends pasted text, even
"/compact", as a plain message. So a prompt starting with ``/`` is typed as
one line, never pasted, and gets no note in front of it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from berth.activity import NEEDS_YOU as DIALOG_KINDS, Activity, assess, main_turn, running_subagents
from berth.errors import BerthError

# Agents ``send`` knows the prompt of.
KINDS = ("claude", "grok")
# Put in front of a prompt sent while a Claude turn runs (``when now``). Claude takes a message sent mid-turn as
# soon as it can, which can be before its current work is done.
BUSY_NOTE = ("(This message came in while you were working. Finish what you are doing now first, "
             "then do this.)\n\n")
# cmux's refusals (paste, send), measured on cmux 0.65.2.
TYPING_REFUSED = "holds text someone is typing"
DIALOG_REFUSED = "waiting on a question or dialog"
TYPED_KEYS = ("\\n", "\\r", "\\t")  # escapes cmux send turns into keys (cmux send --help)
SLASH_MAX_BYTES = 800  # Claude folds a longer paste into a chip; a slash command must be one short typed line
SETTLE_SECONDS = 0.3  # between the paste and Enter
CHECK_SECONDS = 1.0  # before looking whether Enter took
INTERRUPT_SECONDS = 20.0  # how long an interrupt waits for the prompt to come back
POLL_SECONDS = 0.5

SITUATIONS = ("ready", "working", "needs_you", "limited", "overlay", "starting")
WHAT_TO_DO = {
    "working": "It is in the middle of a turn. Read the screen below to see what it is doing, then send again with "
               "when: idle (Berth sends it the moment the turn ends), now (Claude takes it as soon as it can; Berth "
               "asks it to finish its current work first; not Grok, which would stop), or interrupt (Esc stops the "
               "turn, then the prompt is sent).",
    "needs_you": "A question, permission, or plan is open. That is the operator's to answer, never yours: tell them "
                 "(or mark it urgent if your instructions say so). when idle sends it once it is answered.",
    "limited": "It hit its usage limit. when idle sends it once it can work again, within wait minutes.",
    "overlay": "A menu or the /btw panel covers its prompt. when idle sends it once that closes.",
    "starting": "Its prompt is not on screen (starting, or Berth cannot read it). when idle sends it once the "
                "prompt is ready.",
}


@dataclass
class Look:
    activity: Activity
    situation: str
    tail: str
    clipped: bool


def agent_of(row) -> str:
    """claude or grok, by the agent: the catalog files Claude as kind ``other-agent`` with agent ``claude``.

    2026-10-30: ``send`` checked ``kind`` against ``KINDS`` and refused all 63 Claude sessions; the tests
    passed because their rows said kind ``claude``, which the catalog never does.
    """
    if (row["kind"] or "") == "grok":
        return "grok"
    return (row["agent_name"] or row["kind"] or "").casefold()


def is_slash(body: str) -> bool:
    return body.lstrip().startswith("/")


def check(body: str) -> None:
    """Refuse a prompt that could never be sent: an empty one, or a slash command that is not one short line."""
    if not body.strip():
        raise BerthError("BAD_ARGUMENT", "the prompt is empty", {})
    text = body.strip()
    if is_slash(body) and ("\n" in text or len(text.encode("utf-8")) > SLASH_MAX_BYTES):
        raise BerthError("BAD_ARGUMENT", f"a slash command is one line, at most {SLASH_MAX_BYTES} bytes", {})
    if is_slash(body) and any(sequence in text for sequence in TYPED_KEYS):
        # cmux send types \n and \r as Enter and \t as Tab: a note that mentions one would run the command cut short.
        raise BerthError("BAD_ARGUMENT", "a slash command cannot contain the two characters \\n, \\r or \\t: "
                         "cmux types them as Enter or Tab. Write them out in words", {})


def situation(activity: Activity) -> str:
    dialog = activity.dialog
    if activity.asking or dialog in DIALOG_KINDS or dialog == "needs":
        return "needs_you"
    if dialog == "limit":
        return "limited"
    if activity.turn:
        return "working"
    if dialog in ("menu", "btw_answering", "btw_answered"):
        return "overlay"
    if not activity.prompt or activity.blank or activity.unclear:
        return "starting"
    return "ready"


def _workspace(row) -> str:
    workspace = (row["cmux_workspace_id"] or "").strip()
    if not workspace:
        # cmux falls back to the selected workspace for an empty target: never send without one.
        raise BerthError("NOT_LIVE", f"{row['title']} has no open workspace", {"id": row["id"]})
    return workspace


def look(cmux, row, *, lines: int = 15, programs: dict[str, str] | None = None) -> Look:
    """Read the pane once: what the session is doing, and the last ``lines`` of its screen."""
    workspace = _workspace(row)
    screen = cmux.read_screen(workspace, lines=120)
    activity = assess(screen, row["kind"] or "", (row["agent_name"] or "").casefold(),
                      program=(programs or {}).get(workspace.lower()), main_turn_fn=lambda: main_turn(row),
                      subagents_fn=lambda: running_subagents(row))
    rows = screen.rstrip("\n").splitlines()
    tail = "\n".join(rows[-lines:])
    clipped = len(rows) > lines
    if len(tail.encode("utf-8")) > 4000:
        tail, clipped = tail.encode("utf-8")[-4000:].decode("utf-8", "ignore"), True
    return Look(activity, situation(activity), tail, clipped)


def deliver(cmux, row, body: str, *, settle=time.sleep, reread=None) -> dict:
    """Put ``body`` in the prompt and press Enter. Refused by cmux when someone is typing there or a dialog is open.

    ``reread()`` returns a fresh ``Look``; when given, Berth checks once that Enter took and presses it again if
    the prompt still holds this text.
    """
    workspace = _workspace(row)
    check(body)
    slash = is_slash(body)
    # A trailing space closes Claude's slash menu, so Enter runs this command and not the menu's pick.
    text = body.strip() + " " if slash else body
    try:
        if slash:
            cmux.send_text(workspace, text)  # typed: Claude runs a slash command only when typed
        else:
            cmux.paste_text(workspace, text)
    except BerthError as exc:
        if TYPING_REFUSED in exc.message:
            raise BerthError("BUSY", "someone is typing in its prompt box (maybe the operator); nothing was sent",
                             {"id": row["id"], "refused": "typing"}) from exc
        if DIALOG_REFUSED in exc.message:
            raise BerthError("BUSY", "a question or dialog is open; nothing was sent",
                             {"id": row["id"], "refused": "dialog"}) from exc
        raise
    settle(SETTLE_SECONDS)
    cmux.send_key(_workspace(row), "enter")
    out = {"typed" if slash else "pasted": len(text.encode("utf-8"))}
    if reread is not None:
        settle(CHECK_SECONDS)
        after = reread()
        draft = after.activity.draft
        start = text.strip().splitlines()[0][:40] if text.strip() else ""
        if after.situation == "ready" and draft is not None and (
                (start and start in draft.text) or "[Pasted text" in draft.text):
            cmux.send_key(_workspace(row), "enter")  # Enter did not take: the text is still in the box
            out["enter_again"] = True
    return out


def interrupt(cmux, row, reread, *, settle=time.sleep, clock=time.monotonic) -> Look:
    """Esc to stop a running turn, then wait for the prompt. Only for a turn: Esc on a dialog cancels it."""
    first = reread()
    if first.situation != "working":
        return first
    cmux.send_key(_workspace(row), "escape")
    deadline = clock() + INTERRUPT_SECONDS
    current = first
    while clock() < deadline:
        settle(POLL_SECONDS)
        current = reread()
        if current.situation != "working":
            break
    return current


def refused(exc: BerthError) -> str | None:
    """``typing`` or ``dialog`` when cmux refused a prompt for that reason; else None."""
    return exc.details.get("refused") if exc.code == "BUSY" else None
