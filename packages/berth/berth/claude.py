"""What Claude Code itself says about its sessions. Berth trusts this over hooks.

Claude Code writes ``~/.claude/sessions/<pid>.json`` for every running
process: the conversation it is on (``sessionId``), its directory, whether it
is a pane (``kind: interactive``) or a background job (``kind: bg``, with a
``jobId``), and its name. The cmux hook only reports the id a process had at
``SessionStart``. ``/resume``, a fork, or ``/clear`` inside a running Claude
moves it to another conversation and the hook id goes stale, sometimes to an
id that never gets a transcript at all. Resuming such an id fails and the
workspace closes.

A fork copies the whole conversation into a new transcript with a new id.
Every transcript in a family starts with the same first message, so its
``uuid`` is the family key (``lineage``).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


def sessions_dir() -> Path:
    return Path.home() / ".claude" / "sessions"


@dataclass(frozen=True)
class Process:
    pid: int
    session_id: str
    cwd: str
    kind: str
    name: str
    job_id: str | None
    updated_at: float
    # cli: a terminal Claude, which a pane can show. sdk-cli: one the Claude Agent SDK started from a script, which
    # inherits the pane's environment but is never what the pane shows (2026-11-08, Lee Course Builder).
    entrypoint: str = "cli"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def sessions_dirs() -> list[Path]:
    """``sessions/`` in ~/.claude and in every other Claude config folder (one per account, berth/configdir.py)."""
    from berth.configdir import extra_roots, unique

    return unique([sessions_dir()] + [root / "sessions" for root in extra_roots()])


def processes() -> dict[int, Process]:
    """Running Claude processes by pid, from every config folder. Records of dead pids are ignored."""
    found: dict[int, Process] = {}
    paths = [path for directory in sessions_dirs() if directory.is_dir() for path in directory.glob("*.json")]
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        pid = data.get("pid")
        session_id = data.get("sessionId")
        if not isinstance(pid, int) or not isinstance(session_id, str) or not _alive(pid):
            continue
        found[pid] = Process(
            pid=pid,
            session_id=session_id,
            cwd=data.get("cwd") if isinstance(data.get("cwd"), str) else "",
            kind=data.get("kind") if isinstance(data.get("kind"), str) else "interactive",
            name=data.get("name") if isinstance(data.get("name"), str) else "",
            job_id=data.get("jobId") if isinstance(data.get("jobId"), str) else None,
            updated_at=float(data.get("updatedAt") or 0),
            entrypoint=data.get("entrypoint") if isinstance(data.get("entrypoint"), str) else "cli",
        )
    return found


def process_tree(top_table: str) -> dict[str, list[tuple[str, str]]]:
    """``cmux top --flat`` as parent -> [(kind, ref)]: window, workspace, pane, surface, process."""
    children: dict[str, list[tuple[str, str]]] = {}
    for line in top_table.splitlines():
        parts = line.split("\t")
        if len(parts) < 6 or not parts[5]:
            continue
        children.setdefault(parts[5], []).append((parts[3], parts[4]))
    return children


def pane_pids(tree: dict[str, list[tuple[str, str]]], workspace_ref: str | None, workspace_id: str) -> set[int]:
    """Every pid under one workspace: its panes, surfaces, and their child processes."""
    roots = [workspace_ref] if workspace_ref else []
    roots += [key for key in tree if key.lower().startswith(f"workspace:{workspace_id.lower()}")]
    seen: set[str] = set()
    stack = [root for root in roots if root]
    pids: set[int] = set()
    while stack:
        key = stack.pop()
        for kind, ref in tree.get(key, []):
            if ref in seen:
                continue
            seen.add(ref)
            if kind == "process" and ref.isdigit():
                pids.add(int(ref))
            stack.append(ref)
    return pids


def pane_conversation(pids: set[int], running: dict[int, Process],
                      tree: dict[str, list[tuple[str, str]]] | None = None) -> Process | None:
    """The conversation the pane is showing: its interactive Claude process.

    Only the outermost Claude in the pane counts. A ``claude -p`` that the
    pane's Claude runs through its Bash tool is a newer Claude process under
    the same pane, and is not what the pane shows (2026-10-29: scripted
    ``claude -p`` checks run from Berth Developer parked its row as "replaced" and
    adopted the scratch conversation in its place, once per run).
    """
    # A background job, or a Claude the Agent SDK runs from a script (its entrypoint sdk-cli), is never the pane's
    # conversation: 2026-11-08, Lee Course Builder's scripts ran SDK Claudes in its scratchpad that carried its
    # pane's environment, and each one parked the real conversation as "replaced" (21 rows in ten hours).
    here = {pid: running[pid] for pid in pids
            if pid in running and running[pid].kind != "bg" and not running[pid].entrypoint.startswith("sdk")}
    if tree is not None and len(here) > 1:
        parent = {ref: key for key, children in tree.items() for kind, ref in children if kind == "process"}

        def nested(pid: int) -> bool:
            key, seen = parent.get(str(pid)), set()
            while key is not None and key.isdigit() and key not in seen:
                if int(key) in here:
                    return True
                seen.add(key)
                key = parent.get(key)
            return False

        here = {pid: item for pid, item in here.items() if not nested(pid)}
    if not here:
        return None
    return max(here.values(), key=lambda item: item.updated_at)


def background_jobs(running: dict[int, Process]) -> list[Process]:
    return [item for item in running.values() if item.kind == "bg"]


@dataclass(frozen=True)
class Transcript:
    path: Path
    has_messages: bool
    lineage: str | None
    cwd: str | None


_cache: dict[str, tuple[int, int, Transcript]] = {}
_HEAD_LINES = 400
_TAIL_BYTES = 256 * 1024


def _records(lines):
    for line in lines:
        if '"type"' not in line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("type") in ("user", "assistant"):
            yield item


def transcript(session_id: str | None, cwd: str = "") -> Transcript | None:
    """The family key, whether it has a message, and its directory.

    Reads the head (the first message is the family key) and the tail (the
    last message's directory), not the whole file: active transcripts are tens
    of megabytes and change every few seconds. None when Claude never wrote
    a transcript for this id.
    """
    if not session_id:
        return None
    from berth.identity import claude_transcript

    path = claude_transcript(session_id, cwd)
    if path is None:
        return None
    try:
        st = path.stat()
    except OSError:
        return None
    key = str(path)
    cached = _cache.get(key)
    if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
        return cached[2]
    lineage = None
    last_cwd = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            head = [line for _, line in zip(range(_HEAD_LINES), handle)]
        for item in _records(head):
            if isinstance(item.get("uuid"), str):
                lineage = item["uuid"]
                break
        with path.open("rb") as handle:
            handle.seek(max(0, st.st_size - _TAIL_BYTES))
            tail = handle.read().decode("utf-8", errors="replace").splitlines()
        for item in _records(tail):
            if isinstance(item.get("cwd"), str):
                last_cwd = item["cwd"]
    except OSError:
        return None
    info = Transcript(path, lineage is not None, lineage, last_cwd)
    _cache[key] = (st.st_mtime_ns, st.st_size, info)
    return info


def resumable(session_id: str | None, cwd: str = "") -> bool:
    found = transcript(session_id, cwd)
    return found is not None and found.has_messages


def running_job(session_id: str | None, running: dict[int, Process] | None = None) -> Process | None:
    if not session_id:
        return None
    for item in (running if running is not None else processes()).values():
        if item.kind == "bg" and item.session_id == session_id:
            return item
    return None


def stop_job(job: Process, *, timeout: float = 20.0) -> bool:
    """Stop a background session with ``claude stop``. Its conversation is kept.

    Waits until the job's process has exited, so a resume right after does
    not race the old copy. True when it is gone.
    """
    import time

    subprocess.run([claude_binary(), "stop", job.job_id or job.session_id], capture_output=True, text=True, check=False)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _alive(job.pid):
            return True
        time.sleep(0.2)
    return not _alive(job.pid)


def claude_binary() -> str:
    local = Path.home() / ".local" / "bin" / "claude"
    if local.exists():
        return str(local)
    found = subprocess.run(["/bin/sh", "-lc", "command -v claude"], capture_output=True, text=True, check=False)
    return (found.stdout or "").strip() or "claude"
