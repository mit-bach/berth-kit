#!/usr/bin/env python3
"""Survey how this Mac runs Claude Code today. Reads only; changes nothing.

    python3 tools/inventory.py                 # a report on screen
    python3 tools/inventory.py --json out.json # the same, as JSON
    python3 tools/inventory.py --days 30       # conversations active in the last 30 days

What it reports:

* the machine and the tools that matter (Claude Code, cmux, Python 3.12, uv,
  Homebrew, git, Grok);
* which terminal apps are running;
* Claude Code's setup: the default model and permission mode, whether a
  global CLAUDE.md exists, the MCP servers and skills by name;
* every Claude conversation running now: its session id, directory, name,
  status, and the terminal app and tty it runs in (walked up from its process);
* Claude processes that left no session record (an older Claude Code, or one
  that has not written it yet): the migration refuses to run while any of
  these runs outside cmux, since it cannot tell which conversation they hold;
* the conversations active recently that are not running;
* whether cmux and Berth are installed, and Berth's state if it is.

It never prints message text, environment values, tokens, or anything from
an MCP server's configuration but its name. Standard library only, Python 3.9+.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
CLAUDE = HOME / ".claude"
CMUX_APP = Path("/Applications/cmux.app")
CMUX_BIN = CMUX_APP / "Contents/Resources/bin/cmux"
HEAD_LINES = 400
# Claude Code processes that are helpers, not conversations.
HELPER = re.compile(r"--chrome-native-host|\bmcp\s+serve\b|--version\b|\bupdate\b")
TAIL_BYTES = 256 * 1024

# Process names, as `ps -o comm=` shows them, to the terminal they belong to.
HOSTS = [
    ("cmux", "cmux"),
    ("Terminal", "Terminal.app"),
    ("iTerm", "iTerm2"),
    ("ghostty", "Ghostty"),
    ("Warp", "Warp"),
    ("wezterm", "WezTerm"),
    ("kitty", "kitty"),
    ("alacritty", "Alacritty"),
    ("Cursor", "Cursor"),
    ("Code Helper", "VS Code"),
    ("Code.app", "VS Code"),
    ("Hyper", "Hyper"),
    ("tabby", "Tabby"),
    ("sshd", "ssh"),
]


def run(argv: list[str], timeout: int = 15) -> str:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (done.stdout or "").strip()


def which(name: str) -> str:
    return shutil.which(name) or ""


def version(argv: list[str]) -> str:
    text = run(argv)
    return text.splitlines()[0] if text else ""


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def processes() -> dict[int, tuple[int, str, str]]:
    """pid -> (parent pid, command path, tty) for every process."""
    table: dict[int, tuple[int, str, str]] = {}
    for line in run(["ps", "-axo", "pid=,ppid=,tty=,comm="]).splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[3], parts[2])
    return table


def host_of(pid: int, table: dict[int, tuple[int, str, str]]) -> dict:
    """The terminal a process runs in, from its parents. tmux is noted on the way."""
    chain: list[str] = []
    current, seen = pid, set()
    while current in table and current not in seen and current > 1:
        seen.add(current)
        parent, command, _tty = table[current]
        chain.append(command)
        current = parent
    inside_tmux = any(Path(item).name.startswith("tmux") for item in chain)
    for item in chain:
        for needle, name in HOSTS:
            if needle in item:
                return {"host": name, "tmux": inside_tmux}
    return {"host": "unknown", "tmux": inside_tmux}


def terminals_running(table: dict[int, tuple[int, str, str]]) -> list[str]:
    found: set[str] = set()
    for _parent, command, _tty in table.values():
        if "/Applications/" not in command and not command.startswith("/System/Applications/"):
            continue
        for needle, name in HOSTS:
            if needle in command and name not in {"ssh"}:
                found.add(name)
    return sorted(found)


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def claude_setup() -> dict:
    settings = read_json(CLAUDE / "settings.json")
    permissions = settings.get("permissions") if isinstance(settings.get("permissions"), dict) else {}
    config = read_json(HOME / ".claude.json")
    servers = sorted((config.get("mcpServers") or {}).keys()) if isinstance(config.get("mcpServers"), dict) else []
    project_servers: set[str] = set()
    for project in (config.get("projects") or {}).values() if isinstance(config.get("projects"), dict) else []:
        if isinstance(project, dict) and isinstance(project.get("mcpServers"), dict):
            project_servers.update(project["mcpServers"].keys())
    skills = sorted(path.name for path in (CLAUDE / "skills").iterdir() if path.is_dir()) if (CLAUDE / "skills").is_dir() else []
    return {
        "version": version(["claude", "--version"]) if which("claude") else "",
        "binary": which("claude"),
        "model": settings.get("model"),
        "default_mode": permissions.get("defaultMode"),
        "skip_dangerous_prompt": settings.get("skipDangerousModePermissionPrompt"),
        "has_hooks": bool(settings.get("hooks")),
        "has_status_line": bool(settings.get("statusLine")),
        "global_claude_md": (CLAUDE / "CLAUDE.md").is_file(),
        "user_mcp_servers": servers,
        "project_mcp_servers": sorted(project_servers - set(servers)),
        "skills": skills,
    }


def _records(lines):
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            yield item


def transcript_facts(path: Path) -> dict:
    """Title, directory, and last activity from a transcript's head and tail. No message text."""
    facts = {"title": None, "title_kind": None, "cwd": None, "last_message": None}
    try:
        size = path.stat().st_size
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            head = [line for _, line in zip(range(HEAD_LINES), handle)]
        with path.open("rb") as handle:
            handle.seek(max(0, size - TAIL_BYTES))
            tail = handle.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return facts
    for item in list(_records(head)) + list(_records(tail)):
        kind = item.get("type")
        if kind == "custom-title" and isinstance(item.get("customTitle"), str):
            facts["title"], facts["title_kind"] = item["customTitle"], "set"
        elif kind == "ai-title" and isinstance(item.get("aiTitle"), str) and facts["title_kind"] != "set":
            facts["title"], facts["title_kind"] = item["aiTitle"], "generated"
        if isinstance(item.get("cwd"), str):
            facts["cwd"] = item["cwd"]
        if kind in {"user", "assistant"} and isinstance(item.get("timestamp"), str):
            facts["last_message"] = max(facts["last_message"] or "", item["timestamp"])
    return facts


def transcript_path(session_id: str) -> Path | None:
    hits = glob.glob(str(CLAUDE / "projects" / "*" / f"{session_id}.jsonl"))
    return Path(hits[0]) if hits else None


def running_conversations(table: dict[int, tuple[int, str, str]]) -> list[dict]:
    found = []
    for path in sorted((CLAUDE / "sessions").glob("*.json")) if (CLAUDE / "sessions").is_dir() else []:
        record = read_json(path)
        pid = record.get("pid")
        if not isinstance(pid, int) or not alive(pid):
            continue
        session_id = record.get("sessionId") or ""
        transcript = transcript_path(session_id) if session_id else None
        facts = transcript_facts(transcript) if transcript else {}
        host = host_of(pid, table)
        found.append({
            "pid": pid,
            "session_id": session_id,
            "name": record.get("name") or facts.get("title"),
            "cwd": record.get("cwd") or facts.get("cwd"),
            "status": record.get("status"),
            "kind": record.get("kind"),
            "entrypoint": record.get("entrypoint"),
            "started": _stamp(record.get("startedAt")),
            "host": host["host"],
            "tmux": host["tmux"],
            "tty": table.get(pid, (0, "", ""))[2],
            "has_transcript": transcript is not None,
        })
    return found


def unrecorded_claude(table: dict[int, tuple[int, str, str]], recorded: set[int]) -> list[dict]:
    """Claude processes with no ~/.claude/sessions record, outside a recorded Claude's own children."""
    args = {}
    for line in run(["ps", "-axo", "pid=,args="]).splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            args[int(parts[0])] = parts[1]
    found = []
    for pid, (_parent, command, tty) in table.items():
        line = args.get(pid, "")
        is_claude = Path(command).name == "claude" or (
            Path(command).name == "node" and ("claude-code" in line or "/bin/claude" in line))
        if not is_claude or pid in recorded or HELPER.search(line):
            continue
        current, seen, inside = table.get(pid, (0, "", ""))[0], set(), False
        while current in table and current not in seen and current > 1:
            if current in recorded:
                inside = True
                break
            seen.add(current)
            current = table[current][0]
        if inside:
            continue
        host = host_of(pid, table)
        found.append({"pid": pid, "host": host["host"], "tmux": host["tmux"], "tty": tty, "command": line[:120]})
    return found


def recent_conversations(days: int, running_ids: set[str]) -> list[dict]:
    cutoff = time.time() - days * 86400
    found = []
    for path in glob.glob(str(CLAUDE / "projects" / "*" / "*.jsonl")):
        file = Path(path)
        try:
            stat = file.stat()
        except OSError:
            continue
        if stat.st_mtime < cutoff or file.stem in running_ids:
            continue
        facts = transcript_facts(file)
        found.append({
            "session_id": file.stem,
            "title": facts["title"],
            "cwd": facts["cwd"],
            "last_message": facts["last_message"],
            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
            "size_kb": stat.st_size // 1024,
        })
    found.sort(key=lambda item: item["modified"], reverse=True)
    return found


def _stamp(value) -> str | None:
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10**11 else value
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(seconds))
    return None


def berth_state() -> dict:
    binary = which("berth") or (str(HOME / ".local/bin/berth") if (HOME / ".local/bin/berth").exists() else "")
    if not binary:
        return {"installed": False}
    text = run([binary, "status", "--json"], timeout=30)
    try:
        status = json.loads(text) if text else {}
    except ValueError:
        status = {"raw": text[:200]}
    return {"installed": True, "binary": binary, "status": status}


def survey(days: int) -> dict:
    table = processes()
    running = running_conversations(table)
    cmux_cli = which("cmux") or (str(CMUX_BIN) if CMUX_BIN.exists() else "")
    return {
        "taken": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "machine": {
            "macos": version(["sw_vers", "-productVersion"]),
            "arch": run(["uname", "-m"]),
            "user_home": str(HOME),
        },
        "tools": {
            "python3.12": which("python3.12") or (str(HOME / ".local/bin/python3.12") if (HOME / ".local/bin/python3.12").exists() else ""),
            "uv": which("uv"),
            "brew": which("brew"),
            "git": which("git"),
            "grok": which("grok") or (str(HOME / ".grok/bin/grok") if (HOME / ".grok/bin/grok").exists() else ""),
        },
        "terminals_running": terminals_running(table),
        "claude": claude_setup(),
        "cmux": {
            "app": CMUX_APP.is_dir(),
            "cli": cmux_cli,
            "version": version([cmux_cli, "--version"]) if cmux_cli else "",
            "claude_wrapper": (CMUX_APP / "Contents/Resources/bin/cmux-claude-wrapper").exists(),
        },
        "berth": berth_state(),
        "running": running,
        "unrecorded": unrecorded_claude(table, {item["pid"] for item in running}),
        "recent": recent_conversations(days, {item["session_id"] for item in running}),
        "days": days,
    }


def report(data: dict) -> str:
    out = []
    claude, cmux = data["claude"], data["cmux"]
    out.append(f"# Claude setup survey, {data['taken']}\n")
    out.append(f"macOS {data['machine']['macos']} ({data['machine']['arch']})")
    out.append("Tools: " + ", ".join(f"{name} {'yes' if path else 'no'}" for name, path in data["tools"].items()))
    out.append(f"Terminals running: {', '.join(data['terminals_running']) or 'none found'}")
    out.append(f"\n## Claude Code\n{claude['version'] or 'not on PATH'}")
    out.append(f"default model: {claude['model']}; permission mode: {claude['default_mode']}; skip-dangerous-prompt: {claude['skip_dangerous_prompt']}")
    out.append(f"global CLAUDE.md: {claude['global_claude_md']}; hooks: {claude['has_hooks']}; status line: {claude['has_status_line']}")
    out.append(f"MCP servers (user): {', '.join(claude['user_mcp_servers']) or 'none'}")
    if claude["project_mcp_servers"]:
        out.append(f"MCP servers (project only): {', '.join(claude['project_mcp_servers'])}")
    out.append(f"skills: {', '.join(claude['skills']) or 'none'}")
    out.append(f"\n## cmux\napp: {cmux['app']}; cli: {cmux['cli'] or 'no'}; {cmux['version']}; Claude wrapper: {cmux['claude_wrapper']}")
    berth = data["berth"]
    out.append(f"\n## Berth\n{'installed: ' + berth.get('binary', '') if berth['installed'] else 'not installed'}")
    out.append(f"\n## Claude conversations running now ({len(data['running'])})")
    if data["running"]:
        out.append("| name | status | where | directory | session id |")
        out.append("| --- | --- | --- | --- | --- |")
        for item in data["running"]:
            where = item["host"] + (" + tmux" if item["tmux"] else "") + (f" ({item['tty']})" if item["tty"] not in ("", "??") else "")
            out.append(f"| {item['name'] or '(unnamed)'} | {item['status']} | {where} | {item['cwd']} | {item['session_id']} |")
    if data["unrecorded"]:
        out.append(f"\n## Claude processes with no session record ({len(data['unrecorded'])})")
        out.append("These hold a conversation this survey cannot name. An older Claude Code may not write ~/.claude/sessions; "
                   "update it and restart these, or end them, before migrating.")
        for item in data["unrecorded"]:
            out.append(f"- pid {item['pid']} in {item['host']}" + (f" ({item['tty']})" if item["tty"] not in ("", "??") else "") + f": {item['command']}")
    out.append(f"\n## Not running, active in the last {data['days']} days ({len(data['recent'])})")
    for item in data["recent"][:60]:
        out.append(f"- {item['modified']}  {item['title'] or '(untitled)'}  {item['cwd']}  {item['session_id']}")
    if len(data["recent"]) > 60:
        out.append(f"- ... and {len(data['recent']) - 60} more (see --json)")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", metavar="PATH", help="also write the survey as JSON to PATH")
    parser.add_argument("--days", type=int, default=14, help="how far back recent conversations go (default 14)")
    args = parser.parse_args()
    data = survey(args.days)
    if args.json:
        Path(args.json).expanduser().write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(report(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
