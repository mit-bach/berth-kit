"""A session can say it needs you urgently (2026-10-29), or that there is more to do (2026-10-31).

Ready is not enough when many sessions are ready at once: the one that needs
you first got lost among them. A session raises its flag through the Berth MCP
(``update`` with ``urgent``), with a line saying why; the operator decides in
each agent's instructions when it should. The cmux sidebar then shows it red,
Urgent, above every other row, and cmux posts a notification.

The second level is ``follow_up`` (2026-10-31, Dominik): the turn ended, but
the work did not. A backlog is left, or the operator has something to do (run
a command, answer in a chat, decide). Every session that stops says Done, so
the ones that still need something were lost among those that do not. It is
magenta, Follow up, in the row's own place in the sidebar, with no
notification. Like urgent, it stays until a prompt reaches the session, and
the agent raises it again at the end of a later turn if there is still more.
A follow-up never replaces an urgent flag.

A follow-up is only for what Dominik alone can do (2026-10-31, after a day of
flags that asked him "your call", "yes or no", "say go"). Its ``why`` is
required and says what he does, as an action: "Sign in to aa.com so I can
claim the miles". A question, an approval, or a confirmation is refused
(``check_follow_up``): the agent decides those itself, unless its written
instructions reserve the decision for him. And one need has one flag: a why
that matches a flag already up (``same_need``) is refused with the line the
agent ends its reply with instead, "Follow-up: see <that session>".

Its whole footprint in an agent (2026-10-31, Dominik): one ``update`` call and
one closing line, "Follow-up: <action>" or "No follow-up.", written in the
Berth MCP's instructions and nowhere else. The checks above speak only when
an agent gets it wrong, so they cost nothing otherwise.

Dominik dismisses a follow-up with ⌘⇧X in cmux (``berth follow-up me
--dismiss``, run in a new tab of the workspace he is on, which closes at
once; 2026-10-31). Dismissed means he does not want it: the flag comes down,
and the same why raised again for that session is refused (``DISMISSED``),
so it stops coming back. A different need still can.

The flag is a small state machine of its own on the row, beside the row's
lifecycle (``berth/machine.py``), and only this module writes its columns
(``attention``, ``attention_why``, ``attention_at``):

    none       raise urgent     -> urgent     the session, or another session for it
    none       raise follow_up  -> follow_up
    follow_up  raise urgent     -> urgent     urgent outranks it
    urgent     raise follow_up  -> urgent     kept: a follow-up never replaces urgent
    any        raise the same   -> the same   a new why and time
    any        answer           -> none       a prompt reached it after the flag went up
    urgent     lower urgent     -> none       update with urgent false (follow_up false for a follow-up)
    any        delete           -> (gone)     with the row

Each move writes one event: ``attention_raised``, ``attention_answered``,
``attention_lowered``. Sleep, park, and cycle keep the flag: the session
still needs you when it is back.

Only Dominik answers a flag (2026-11-04): a prompt typed into the session's
box after the flag went up (``human_prompt``). Another agent's message does
not: until then cmux's last submitted message and Claude's prompt history
both counted it, so a session that flagged Dominik lost the flag as soon as a
steward wrote to it. Claude's transcript marks a message from another session
``origin.kind: "peer"``. What an agent types into the box looks typed, so
Berth matches the text against what agents typed there: its own mailbox
(``delivery``) and every ``agent_input`` event (Berth's ``send``, cmux-mcp's
``input``). A slash command is not an answer either. Otherwise the flag comes
down only when its session sets it false: ``update`` with ``follow_up``
(or ``urgent``) false.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from berth.errors import BerthError
from berth.timeutil import parse_time, utc_now

URGENT = "urgent"
FOLLOW_UP = "follow_up"
LEVELS = (URGENT, FOLLOW_UP)
WHY_MAX = 200

# A follow-up's why is an action for Dominik, not a question or a request for approval (2026-10-31).
_ASKING = re.compile(
    r"^(your call|yes or no|yes/no|y/n|approve|approval|confirm|ok to|okay to|say (go|yes|send|ok)|should i|shall i|"
    r"do you want|want me to|can i|may i|let me know|is it ok|permission to|green ?light|sign[- ]off)\b", re.I)
FOLLOW_UP_RULE = ("A follow-up is what only Dominik can do, as an action (\"Sign in to aa.com\"), never a question or "
                  "an approval: decide those yourself. A decision your instructions reserve for him: \"Decide <what>\".")
# Words that say nothing about which need it is: left out when two whys are compared.
_COMMON = frozenset("""the and for you your can could so that this with from into then his him her our its they them
before after sign run get give set add make need needs needed let use open click dominik please berth session agent
follow when what which who will would should may might also just now once there here been was were are has have had
not but all any one two""".split())


def _need_words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9][a-z0-9.\-]*[a-z0-9]", (text or "").casefold())
            if len(word) > 2 and word not in _COMMON}


def same_need(left: str, right: str) -> bool:
    """Two whys that name the same need: the same words, or most of the shorter one's (2026-10-31)."""
    if _norm_why(left) == _norm_why(right):
        return True
    a, b = _need_words(left), _need_words(right)
    if min(len(a), len(b)) < 2:
        return False
    return len(a & b) / min(len(a), len(b)) >= 0.6


def check_follow_up(why: str) -> str:
    """The why of a follow-up, cleaned, or BAD_ARGUMENT when it is empty, a question, or an approval request."""
    text = " ".join((why or "").split())[:WHY_MAX]
    if not text:
        raise BerthError("BAD_ARGUMENT", "a follow-up needs why: " + FOLLOW_UP_RULE, {})
    if text.rstrip().endswith("?") or _ASKING.match(text):
        raise BerthError("BAD_ARGUMENT", f"not a follow-up: {text!r}. " + FOLLOW_UP_RULE, {"why": text})
    return text


def open_flags(db, *, besides: str | None = None) -> list[dict]:
    """Every raised flag, so an agent sees whether its need is flagged already (one need, one flag)."""
    rows = db.conn.execute(
        "SELECT b.id, b.title, b.attention, b.attention_why, b.attention_at, f.name AS folder FROM berth b "
        "LEFT JOIN folder f ON f.id = b.folder_id WHERE b.attention IS NOT NULL ORDER BY b.attention_at").fetchall()
    return [{"id": row["id"], "title": row["title"], "flag": row["attention"], "why": row["attention_why"],
             "folder": row["folder"] or "", "since": row["attention_at"]} for row in rows if row["id"] != besides]


def raise_flag(db, berth_id: str, why: str, *, by: str = "", cmux=None, level: str = URGENT) -> dict:
    """Mark one session urgent, or as having more to do. With ``cmux``, a live urgent one also gets a notification."""
    if level not in LEVELS:
        raise BerthError("BAD_ARGUMENT", f"the flag is one of {', '.join(LEVELS)}", {"level": level})
    row = db.require_berth(berth_id)
    if row["kind"] == "berth":
        raise BerthError("PROTECTED", "the Berth screen is not a session", {"berth_id": berth_id})
    if level == FOLLOW_UP and row["attention"] == URGENT:
        return dict(row)  # urgent outranks it and stays until answered or lowered
    text = check_follow_up(why) if level == FOLLOW_UP else " ".join((why or "").split())[:WHY_MAX]
    if level == FOLLOW_UP and _norm_why(text) in _attachments(row).get(DISMISSED_KEY, []):
        raise BerthError("DISMISSED", "Dominik dismissed this follow-up: he does not need it. Do not raise it again; "
                                      "end your reply with: No follow-up.", {"why": text})
    if level == FOLLOW_UP:
        for other in open_flags(db, besides=row["id"]):
            if same_need(text, other["why"]):
                raise BerthError("DUPLICATE", f"{other['title']} has flagged this already (\"{other['why']}\"). Do not "
                                              f"raise it; end your reply with: Follow-up: see {other['title']}.",
                                 {"session": other["id"], "title": other["title"]})
    with db.immediate():
        db.update_berth(row["id"], attention=level, attention_why=text, attention_at=utc_now())
        db.insert_event(row["id"], "attention_raised", {"why": text, "by": by, "level": level})
    if level == URGENT and cmux is not None and row["state"] == "live" and row["cmux_workspace_id"]:
        try:
            cmux.notify(row["cmux_workspace_id"], title=row["title"], subtitle="Urgent", body=text or "needs you")
        except BerthError:
            pass  # the sidebar still says it
    return dict(db.require_berth(row["id"]))


def lower_flag(db, berth_id: str, *, event: str = "attention_lowered", detail: dict | None = None,
               level: str | None = None) -> bool:
    """Clear the flag (only a flag of ``level``, when given). Returns whether one came down."""
    row = db.require_berth(berth_id)
    if not row["attention"] or (level is not None and row["attention"] != level):
        return False
    with db.immediate():
        db.update_berth(row["id"], attention=None, attention_why="", attention_at=None)
        db.insert_event(row["id"], event, {"why": row["attention_why"], "since": row["attention_at"],
                                           "level": row["attention"], **(detail or {})})
    return True


DISMISSED_KEY = "follow_up_dismissed"  # the whys Dominik dismissed for a session, newest last
DISMISSED_KEEP = 20


def _norm_why(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def _attachments(row) -> dict:
    import json

    try:
        data = json.loads(row["attachments"] or "{}")
    except (ValueError, TypeError):
        data = {}
    return data if isinstance(data, dict) else {}


def dismiss_flag(db, berth_id: str, *, by: str = "") -> bool:
    """Dominik does not want this follow-up: lower it and refuse the same why from now on. Returns whether one came down."""
    import json

    row = db.require_berth(berth_id)
    if row["attention"] != FOLLOW_UP:
        return False
    data = _attachments(row)
    seen = [item for item in data.get(DISMISSED_KEY, []) if isinstance(item, str)]
    why = _norm_why(row["attention_why"])
    if why and why not in seen:
        seen.append(why)
    data[DISMISSED_KEY] = seen[-DISMISSED_KEEP:]
    with db.immediate():
        db.update_berth(row["id"], attachments=json.dumps(data, sort_keys=True))
    return lower_flag(db, berth_id, event="attention_dismissed", detail={"by": by}, level=FOLLOW_UP)


def _when(value) -> datetime | None:
    if not value:
        return None
    try:
        return parse_time(value)
    except ValueError:
        return None


_PASTED = re.compile(r"^\s*<pasted_content[^>]*>\s*|\s*</pasted_content[^>]*>\s*$")
_NOT_PROMPTS = ("<cross-session-message", "<task-notification", "<system-reminder", "<command-name>",
                "<local-command", "<bash-input>", "<bash-stdout>", "caveat: the messages below")


def _plain(text: str) -> str:
    """A prompt's words, without the paste wrapper Claude puts around pasted text, spaces collapsed."""
    return " ".join(_PASTED.sub("", text or "").split())


def agent_typed(db, berth_id: str, since: datetime) -> list[str]:
    """What agents typed into this session's box since ``since``: Berth's mailbox and every agent_input event."""
    stamp = since.strftime("%Y-%m-%dT%H:%M:%S")
    texts = [row[0] for row in db.conn.execute(
        "SELECT prompt FROM delivery WHERE berth_id = ? AND state = 'delivered' AND settled_at >= ?", (berth_id, stamp))
        ] if _has_table(db, "delivery") else []
    for row in db.conn.execute("SELECT detail FROM event WHERE berth_id = ? AND kind = 'agent_input' AND at >= ?",
                               (berth_id, stamp)):
        try:
            detail = json.loads(row[0] or "{}")
        except ValueError:
            continue
        if isinstance(detail, dict) and isinstance(detail.get("text"), str):
            texts.append(detail["text"])
    return [_plain(text) for text in texts if text]


def _has_table(db, name: str) -> bool:
    return db.conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _typed_by_agent(text: str, typed: list[str]) -> bool:
    words = _plain(text)
    head = words[:120]
    return any(head and (other.startswith(head) or words.startswith(other[:120])) for other in typed if other)


def _is_prompt(text: str) -> bool:
    words = _plain(text)
    return bool(words) and not words.startswith("/") and not any(words.casefold().startswith(mark) for mark in _NOT_PROMPTS)


_typed_cache: dict[str, tuple[int, list[tuple[datetime, str]]]] = {}  # transcript -> (bytes read, typed prompts)


def _claude_typed(path: Path) -> list[tuple[datetime, str]]:
    """Every prompt typed into a Claude session's box, from its transcript (read on from where it was last)."""
    try:
        size = path.stat().st_size
    except OSError:
        return []
    offset, found = _typed_cache.get(str(path), (0, []))
    if size < offset:
        offset, found = 0, []
    if size > offset:
        with path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read(size - offset)
        complete = chunk.rfind(b"\n") + 1
        for raw in chunk[:complete].splitlines():
            if b'"type":"user"' not in raw and b'"type": "user"' not in raw:
                continue
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(entry, dict) or entry.get("type") != "user" or entry.get("isMeta"):
                continue
            origin = entry.get("origin")
            if isinstance(origin, dict) and origin.get("kind") not in (None, "human"):
                continue  # another session's message, a task notice: not typed in the box
            content = (entry.get("message") or {}).get("content")
            text = content if isinstance(content, str) else " ".join(
                part.get("text", "") for part in content or [] if isinstance(part, dict) and part.get("type") == "text")
            moment = _when(entry.get("timestamp"))
            if moment is not None and _is_prompt(text):
                found.append((moment, text))
        offset += complete
        _typed_cache[str(path)] = (offset, found[-200:])
    return found


def human_prompt(db, row, raised: datetime, workspace=None) -> datetime | None:
    """When Dominik first typed a prompt into this session's box after ``raised``; None when he has not.

    Claude: its transcript's typed prompts, less what agents typed there. Other agents: cmux's last submitted
    message, less another agent's message and what agents typed.
    """
    typed = agent_typed(db, row["id"], raised - timedelta(minutes=2))
    if (row["agent_name"] or "").casefold() == "claude" and row["kind"] != "grok" and row["agent_session_id"]:
        from berth.identity import claude_transcript

        path = claude_transcript(row["agent_session_id"], row["cwd"] or "")
        if path is None:
            return None
        for moment, text in _claude_typed(path):
            if moment > raised and not _typed_by_agent(text, typed):
                return moment
        return None
    if workspace is None:
        return None
    moment = _when(workspace.submitted_at)
    message = workspace.submitted_message or ""
    if moment is None or moment <= raised or not _is_prompt(message) or _typed_by_agent(message, typed):
        return None
    return moment


def settle(db, workspaces) -> list[str]:
    """The watcher's tick: clear each flag Dominik has answered since it went up. Returns the ids cleared."""
    rows = db.conn.execute("SELECT * FROM berth WHERE attention IS NOT NULL").fetchall()
    if not rows:
        return []
    by_ws = {item.id.lower(): item for item in workspaces or []}
    cleared = []
    for row in rows:
        raised = _when(row["attention_at"])
        if raised is None:
            continue
        answered = human_prompt(db, row, raised, by_ws.get((row["cmux_workspace_id"] or "").lower()))
        if answered is not None:
            lower_flag(db, row["id"], event="attention_answered", detail={"answered_at": answered.isoformat()})
            cleared.append(row["id"])
    return cleared


def note_agent_input(db, berth_id: str, text: str, *, by: str = "") -> None:
    """Record text an agent typed into a session's box, so it never counts as Dominik's answer to a flag."""
    words = _plain(text)[:400]
    if not words:
        return
    with db.immediate():
        db.insert_event(berth_id, "agent_input", {"text": words, "by": by})
