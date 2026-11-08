"""Compaction, done by Berth and checked on disk (2026-10-30).

An agent asked to compact a session placed its own ``/compact`` text, and it
kept failing the same ways (STUDIO Orch on 2026-10-30, Grok Studio Orch Bypass
on 2026-10-29, both replicated on a throwaway pane the same day):

* **A pasted ``/compact <note>`` is not a command.** Claude shows a multi-line
  or long paste as ``[Pasted text #N +L lines]`` and sends it as an ordinary
  message. The session answers it, or, a fork, reads the note as its job: one
  built a whole Google launcher in FORGE from its compaction note.
* **Typing ``/compact `` and pasting the note behind it** is refused by cmux
  ("holds text someone is typing"), and cmux-mcp cannot pass ``--force``.
* **One typed line** works, but a note is several paragraphs.

Two routes work, every time, and Berth uses each where it fits:

* **In the pane** (a live session): type ``/compact `` (cmux's own typing guard
  refuses if someone is typing there, so nothing is lost), paste the note
  behind it with ``--force`` (our own text is all that is in the box), check
  the box reads ``/compact …``, press Enter. Claude expands the chip into the
  command's arguments: a 4,140-byte, 40-line note arrived whole (2026-10-30).
* **Headless** (a closed session, or a fork before its pane exists):
  ``claude -p -r <id> "/compact <note>"``; for a fork, ``-r <source>
  --fork-session --session-id <fork>`` in the same call, which leaves the
  source untouched. No prompt box, no typing.

Either way the proof is Claude's own record: one more ``compact_boundary`` in
the session's transcript, after the start. Then, if asked, Berth waits for the
session to be ready and sends the next prompt (``berth/send.py``).

A compaction can take minutes (147 s for 742k tokens), so the MCP starts it in
a detached worker (``berth compact-run <job>``) and returns at once; progress
is in the row's ``attachments.compaction`` and its events (``compact_*``).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

from berth.errors import BerthError
from berth.timeutil import utc_now

COMMAND = "/compact "
NOTE_MAX_BYTES = 32_000
PROMPT_MAX_BYTES = 256_000
HEADLESS_TIMEOUT = 1200  # seconds for claude -p to compact
# A headless run only compacts. With hooks on, the reorientation hook (SessionStart, matcher compact) handed the model
# "re-read your brief" right after the compaction, and it took a whole turn inside this run: it messaged its
# orchestrator and worked on its job until the timeout killed it (2026-11-06, UNI Orch's fork of Master Cleanup).
# So no hook runs here; the session's own pane, when it opens, works as ever.
NO_HOOKS = json.dumps({"disableAllHooks": True})
COMPACTING_ENV = "BERTH_COMPACTING"  # set in the run, so Berth's own hooks stay silent even if hooks did run
PANE_TIMEOUT = 1200.0  # seconds for an in-pane compaction to show its boundary
READY_TIMEOUT = 180.0  # seconds for a pane to come up, or settle after compacting
POLL = 1.0
STATES = ("queued", "waiting", "compacting", "opening", "sending", "done", "failed")


# --- what Claude wrote -----------------------------------------------------

def transcript_path(session_id: str, cwd: str = "") -> Path | None:
    from berth.identity import claude_transcript

    return claude_transcript(session_id, cwd) if session_id else None


def boundaries(session_id: str, cwd: str = "") -> list[dict]:
    """Every compaction Claude recorded in this conversation: its time and metadata."""
    path = transcript_path(session_id, cwd)
    if path is None or not path.exists():
        return []
    found = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if "compact_boundary" not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("subtype") == "compact_boundary":
                found.append({"at": entry.get("timestamp") or "", "meta": entry.get("compactMetadata") or {}})
    return found


def _done_since(session_id: str, cwd: str, before: int, started: str) -> dict | None:
    marks = boundaries(session_id, cwd)
    fresh = [mark for mark in marks[before:] if mark["at"] >= started[:19]]
    if not fresh:
        return None
    meta = fresh[-1]["meta"]
    return {"pre_tokens": meta.get("preTokens"), "post_tokens": meta.get("postTokens"),
            "duration_ms": meta.get("durationMs"), "at": fresh[-1]["at"]}


def refusal_since(session_id: str, cwd: str, started: str) -> str | None:
    """What Claude answered a /compact with instead of compacting, if it did ("Not enough messages to compact.").

    A compaction Claude declines leaves no boundary, only its reply as a ``local_command`` entry; without
    this, Berth would wait out the whole timeout (2026-10-30, the probe compacted twice in a row).
    """
    path = transcript_path(session_id, cwd)
    if path is None or not path.exists():
        return None
    said = None
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if '"local_command"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("subtype") == "local_command" and (entry.get("timestamp") or "") >= started[:19]:
                text = str(entry.get("content") or "")
                said = text.replace("<local-command-stdout>", "").replace("</local-command-stdout>", "").strip()
    return said or None


# The frame around every compaction note Berth sends (2026-11-04). Agent Behavior's text, from its eval of 54
# compactions: Dominik's own notes give the task and let the summarizer decide (2.8 of 3); agents' notes were keep
# and drop lists (1.2 of 3), written to Berth's own parameter text. The words are Agent Behavior's; changes go to it.
FRAME = ("Write this compaction's summary for the task below. You decide what it keeps and what it lets go:\n"
         "keep what an agent needs to do this task at its best, starting fresh from your summary, and leave\n"
         "out the rest. Where something lives on disk, point at it instead of restating it. Never copy client\n"
         "figures or credential values into the summary; say where they live.")
FRAME_VERSION = "v1"
# A line that heads a keep or drop section: after an optional #, ** or list marker, the words alone or before a colon.
_LIST_HEADING = re.compile(r"^\s*(?:#+\s*|[-*+]\s+|\d+[.)]\s+)?(?:\*\*|__)?\s*"
                           r"(?!keep\s+out\b)(keep|drop|let\s+go|what\s+to\s+keep|what\s+to\s+drop|what\s+to\s+let\s+go)"
                           r"\b[^\n:]{0,30}?(?:\*\*|__)?\s*(?::|$)", re.IGNORECASE | re.MULTILINE)
LIST_REFUSAL = ("A compaction note gives the task and the seat; the summarizer decides what to keep. Rewrite the keep and "
                "drop sections as TASK (what the agent does next) and, if it becomes another agent, SEAT (name, home, "
                "who it reports to, whose work it leaves alone). Keep something out only on purpose: KEEP OUT, with why.")


def check_note(note: object) -> str:
    text = str(note or "").strip()
    if not text:
        raise BerthError("BAD_ARGUMENT", "the compaction note is empty", {})
    if text.startswith("/"):
        raise BerthError("BAD_ARGUMENT", "give the note only: Berth types /compact itself", {})
    if len(text.encode("utf-8")) > NOTE_MAX_BYTES:
        raise BerthError("BAD_ARGUMENT", f"the compaction note is over {NOTE_MAX_BYTES} bytes", {})
    heading = _LIST_HEADING.search(text)
    if heading is not None:
        raise BerthError("BAD_ARGUMENT", LIST_REFUSAL, {"line": heading.group(0).strip()})
    return text


def framed(note: str) -> tuple[str, bool]:
    """The note Claude receives: the frame, a blank line, the note. Not twice."""
    if note.startswith(FRAME.split("\n", 1)[0].split(":")[0]):
        return note, False
    return FRAME + "\n\n" + note, True


def _iso(moment: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(moment or time.time()))


from berth.configdir import of as config_dir_of  # noqa: E402  (the session's account folder)

# --- headless --------------------------------------------------------------

def _clean_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("CMUX", "CLAUDE")) and key not in ("ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL")}
    return env


def headless(session_id: str, cwd: str, note: str, *, fork_id: str | None = None, name: str | None = None,
             model: str | None = None, run=subprocess.run, timeout: int = HEADLESS_TIMEOUT,
             config_dir: str | None = None) -> dict:
    """``claude -p -r <id> "/compact <note>"``; with ``fork_id``, fork and compact in one call. Checked on disk."""
    from berth.claude import claude_binary

    target = fork_id or session_id
    before = len(boundaries(target, cwd))
    started = _iso()
    argv = [claude_binary(), "-p", "--settings", NO_HOOKS]
    if model:
        argv += ["--model", model]
    argv += ["-r", session_id]
    if fork_id:
        argv += ["--fork-session", "--session-id", fork_id]
        if name:
            argv += ["-n", name]
    argv.append(COMMAND + note)
    try:
        env = _clean_env()
        env[COMPACTING_ENV] = "1"
        if config_dir:
            env["CLAUDE_CONFIG_DIR"] = config_dir  # the session's account (berth/configdir.py)
        result = run(argv, cwd=cwd or None, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                     text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise BerthError("COMPACT_FAILED", f"claude -p did not finish compacting in {timeout} s", {}) from exc
    done = _done_since(target, cwd, before, started)
    if done is None:
        tail = ((result.stderr or "") + (result.stdout or "")).strip()[-600:]
        said = refusal_since(target, cwd, started) or tail
        raise BerthError("COMPACT_FAILED", f"Claude did not compact: {said}" if said else "Claude recorded no compaction",
                         {"exit": result.returncode, "output": tail})
    return {"mode": "headless", **done}


# --- in the pane -----------------------------------------------------------

def _box(cmux, workspace: str) -> str:
    """The prompt box line, as the screen shows it (``❯`` and what follows), or ''."""
    try:
        screen = cmux.read_screen(workspace, lines=40, scrollback=False)
    except BerthError:
        return ""
    lines = [line.replace("\xa0", " ") for line in screen.splitlines() if line.lstrip().startswith("❯")]
    return lines[-1].strip() if lines else ""


def _clear(cmux, workspace: str) -> None:
    try:
        cmux.send_key(workspace, "ctrl-u")
    except BerthError:
        pass


def in_pane(cmux, row, note: str, *, settle=time.sleep, clock=time.monotonic, timeout: float = PANE_TIMEOUT) -> dict:
    """Type ``/compact ``, paste the note behind it, Enter; done when the transcript shows the boundary.

    Call it only when the session is ready (``send.look``): a slash command runs only at a ready prompt.
    """
    from berth.send import DIALOG_REFUSED, TYPING_REFUSED, _workspace

    workspace = _workspace(row)
    session_id, cwd = row["agent_session_id"] or "", row["cwd"] or ""
    before = len(boundaries(session_id, cwd))
    started = _iso()
    try:
        cmux.send_text(workspace, COMMAND)  # typed, so Claude runs it; refused if someone is typing there
    except BerthError as exc:
        if TYPING_REFUSED in exc.message:
            raise BerthError("BUSY", "someone is typing in its prompt box; nothing was sent",
                             {"id": row["id"], "refused": "typing"}) from exc
        if DIALOG_REFUSED in exc.message:
            raise BerthError("BUSY", "a question or dialog is open; nothing was sent",
                             {"id": row["id"], "refused": "dialog"}) from exc
        raise
    try:
        cmux.paste_text(workspace, note, force=True)  # the box holds only our "/compact "
    except BerthError:
        _clear(cmux, workspace)
        raise
    settle(0.6)
    box = _box(cmux, workspace)
    if box and not box.lstrip("❯ ").startswith("/compact"):
        _clear(cmux, workspace)
        raise BerthError("COMPACT_FAILED", "the prompt box did not read /compact after placing the note; cleared it",
                         {"box": box[:200]})
    cmux.send_key(workspace, "enter")
    deadline, nudged = clock() + timeout, False
    while clock() < deadline:
        settle(POLL)
        done = _done_since(session_id, cwd, before, started)
        if done is not None:
            return {"mode": "pane", **done}
        said = refusal_since(session_id, cwd, started)
        if said:
            settle(POLL)  # a boundary written just after the reply still counts
            done = _done_since(session_id, cwd, before, started)
            if done is not None:
                return {"mode": "pane", **done}
            raise BerthError("COMPACT_FAILED", f"Claude did not compact: {said}", {"said": said})
        if not nudged and clock() > deadline - timeout + 4:
            nudged = True
            if _box(cmux, workspace).lstrip("❯ ").startswith("/compact"):
                cmux.send_key(workspace, "enter")  # the first Enter landed before the box took the paste
    raise BerthError("COMPACT_FAILED", f"no compaction recorded within {int(timeout)} s", {"box": _box(cmux, workspace)[:200]})


# --- waiting and sending ---------------------------------------------------

def wait_ready(reread, *, timeout: float, settle=time.sleep, clock=time.monotonic):
    """Poll ``reread()`` until the session is ready. Returns the ready look, or raises BUSY with the last one."""
    deadline = clock() + timeout
    current = reread()
    while current.situation != "ready":
        if clock() >= deadline:
            raise BerthError("BUSY", f"not ready after {int(timeout)} s: {current.situation}",
                             {"situation": current.situation, "why": list(current.activity.reasons)})
        settle(POLL)
        current = reread()
    return current


# --- the job ---------------------------------------------------------------

def plan(db, berth_id: str, *, note: object = None, prompt: object = None, fork: bool = False,
         title: str | None = None, wait_minutes: int = 60, caller: str | None = None, requested_by: str = "",
         cmux=None, focus: bool = False, cwd: str | None = None) -> dict:
    """Check a request and build its job. With ``fork``, the fork's row is made here (parked, no pane yet).

    ``cwd`` (with ``fork``) puts the fork in another working directory: its transcript is written under that
    directory's project and it opens there (tested 2026-10-30), so re-homing a fork into its unit is one step.
    """
    from berth import claude
    from berth.observer import is_screen
    from berth.send import check

    row = db.require_berth(berth_id)
    if is_screen(row):
        raise BerthError("PROTECTED", "the Berth screen is not a session", {"id": berth_id})
    if (row["agent_name"] or "").casefold() != "claude":
        raise BerthError("KIND_UNSAFE", f"{row['title']}: compaction is for Claude sessions", {"kind": row["kind"]})
    note = check_note(note) if note not in (None, "") else None
    frame = None
    if note is not None:
        note, wrapped = framed(note)
        frame = FRAME_VERSION if wrapped else None
    if prompt not in (None, ""):
        prompt = str(prompt)
        check(prompt)
        if len(prompt.encode("utf-8")) > PROMPT_MAX_BYTES:
            raise BerthError("BAD_ARGUMENT", f"the prompt is over {PROMPT_MAX_BYTES} bytes", {})
    else:
        prompt = None
    if cwd not in (None, "") and not fork:
        raise BerthError("BAD_ARGUMENT", "cwd goes with fork; to move a session, update its cwd", {})
    if note is None and not (fork and prompt):
        raise BerthError("BAD_ARGUMENT", "give the compaction note (note or note_file)", {})
    if not 1 <= int(wait_minutes) <= 1440:
        raise BerthError("BAD_ARGUMENT", "wait is 1 to 1440 minutes", {"wait": wait_minutes})
    from berth.mcp import claude_model

    job = {"note": note, "prompt": prompt, "requested_by": requested_by, "wait_seconds": int(wait_minutes) * 60,
           "model": claude_model(), "focus": focus, "frame": frame}
    if fork:
        from berth.ops import fork_berth

        record = fork_berth(db, cmux, berth_id, title=title, open_pane=False, cwd=cwd or None)
        return {**job, "berth_id": record["id"], "mode": "fork", "source_id": row["id"],
                "source_session": row["agent_session_id"]}
    info = claude.transcript(row["agent_session_id"], row["cwd"] or "")
    if info is None or not info.has_messages:
        raise BerthError("SESSION_MISSING", f"{row['title']}: no conversation to compact", {"id": berth_id})
    if row["state"] == "live":
        return {**job, "berth_id": berth_id, "mode": "pane", "own": bool(caller) and
                (row["cmux_workspace_id"] or "").lower() == str(caller).lower()}
    if row["state"] == "parked":
        return {**job, "berth_id": berth_id, "mode": "closed"}
    raise BerthError("IN_PROGRESS", f"{row['title']} is {row['state']}; try again in a moment", {"state": row["state"]})


def jobs_dir(db_path: Path) -> Path:
    path = Path(db_path).parent / "compact"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _attachments(row) -> dict:
    try:
        data = json.loads(row["attachments"] or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def running(row) -> dict | None:
    """The compaction in progress on this row, if a live worker owns it."""
    item = _attachments(row).get("compaction")
    if isinstance(item, dict) and item.get("state") not in ("done", "failed") and _pid_alive(item.get("pid")):
        return item
    return None


def mark(db, berth_id: str, **fields) -> dict:
    row = db.require_berth(berth_id)
    data = _attachments(row)
    item = dict(data.get("compaction") or {})
    item.update(fields, at=utc_now())
    data["compaction"] = item
    with db.immediate():
        db.update_berth(berth_id, attachments=json.dumps(data, sort_keys=True))
    return item


def start(db, job: dict, *, spawn=None, foreground: bool = False) -> dict:
    """Record the job and run it: in a detached worker, or here when ``foreground``."""
    row = db.require_berth(job["berth_id"])
    busy = running(row)
    if busy is not None:
        raise BerthError("IN_PROGRESS", f"{row['title']} is already being compacted ({busy.get('state')})",
                         {"job": busy.get("job")})
    job = {"job": job.get("job") or str(uuid.uuid4()), "queued_at": utc_now(), **job}
    path = jobs_dir(db.path) / f"{job['job']}.json"
    path.write_text(json.dumps(job, indent=2), encoding="utf-8")
    mark(db, row["id"], job=job["job"], state="queued", pid=os.getpid(), why=None, mode=job["mode"])
    db.insert_event(row["id"], "compact_queued", {k: job.get(k) for k in ("job", "mode", "requested_by")}
                    | {"note_bytes": len((job.get("note") or "").encode()), "prompt": bool(job.get("prompt"))})
    if foreground:
        return run_job(db, path)
    log = (jobs_dir(db.path) / f"{job['job']}.log").open("a", encoding="utf-8")
    process = (spawn or subprocess.Popen)([sys.executable, "-m", "berth", "compact-run", str(path), "--catalog", str(db.path)],
                    start_new_session=True, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    mark(db, row["id"], pid=process.pid)
    return {"job": job["job"], "state": "queued", "pid": process.pid}


def run_job(db, path: Path, *, cmux=None, programs_fn=None, settle=time.sleep, clock=time.monotonic,
            run=None) -> dict:
    """The worker: compact (pane or headless), open the session if it was closed, then send the next prompt."""
    from berth.activity import program_states
    from berth.send import deliver, look

    job = json.loads(Path(path).read_text(encoding="utf-8"))
    berth_id, mode = job["berth_id"], job["mode"]
    run = run or subprocess.run
    if cmux is None:
        from berth.cmux import Cmux

        cmux = Cmux()
    programs = programs_fn or (lambda: program_states(db.path.parent))
    mark(db, berth_id, pid=os.getpid())

    def reread():
        return look(cmux, db.require_berth(berth_id), programs=programs())

    def fail(why: str, **detail) -> dict:
        mark(db, berth_id, state="failed", why=why)
        db.insert_event(berth_id, "compact_failed", {"job": job["job"], "why": why, **detail})
        return {"job": job["job"], "state": "failed", "why": why, **detail}

    result: dict = {}
    try:
        note = job.get("note")
        if note and mode == "pane":
            mark(db, berth_id, state="waiting")
            wait_ready(reread, timeout=float(job.get("wait_seconds") or 3600), settle=settle, clock=clock)
            mark(db, berth_id, state="compacting")
            result = in_pane(cmux, db.require_berth(berth_id), note, settle=settle, clock=clock)
        elif note:
            row = db.require_berth(berth_id)
            mark(db, berth_id, state="compacting")
            if mode == "fork":
                result = headless(job["source_session"], row["cwd"] or "", note, fork_id=row["agent_session_id"],
                                  name=row["title"], model=job.get("model"), run=run, config_dir=config_dir_of(row))
            else:
                result = headless(row["agent_session_id"], row["cwd"] or "", note, model=job.get("model"), run=run,
                                  config_dir=config_dir_of(row))
        if result:
            db.insert_event(berth_id, "compact_done", {"job": job["job"], **result})
        prompt = job.get("prompt")
        if mode == "fork" or (prompt and db.require_berth(berth_id)["state"] != "live"):
            from berth.state import play_berth

            mark(db, berth_id, state="opening")
            if db.require_berth(berth_id)["state"] != "live":
                play_berth(db, cmux, berth_id, focus=bool(job.get("focus")))
        if prompt:
            mark(db, berth_id, state="sending")
            wait_ready(reread, timeout=READY_TIMEOUT, settle=settle, clock=clock)
            sent = deliver(cmux, db.require_berth(berth_id), prompt, settle=settle, reread=reread)
            db.insert_event(berth_id, "prompt_sent", {"job": job["job"], **sent})
            result["prompt"] = sent
    except BerthError as exc:
        return fail(exc.message, code=exc.code, **{k: v for k, v in exc.details.items() if k in ("box", "output", "situation")})
    except Exception as exc:  # the worker must always leave a reason behind
        return fail(f"{type(exc).__name__}: {exc}")
    mark(db, berth_id, state="done", why=None, **{k: result.get(k) for k in ("pre_tokens", "post_tokens") if k in result})
    return {"job": job["job"], "state": "done", **result}
