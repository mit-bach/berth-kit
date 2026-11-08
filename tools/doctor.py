#!/usr/bin/env python3
"""Check that each onboarding stage is really done. Reads only; changes nothing.

    python3 tools/doctor.py              # every stage
    python3 tools/doctor.py --stage 2    # one stage: 1 cmux, 2 Berth, 3 agents, 4 Grok (optional)
    python3 tools/doctor.py --json

Each check prints ok, FIX (what to do), or note (something recommended that the
operator may have declined on purpose). The exit code is 0 when no FIX is
left in the stages asked for; notes do not count. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True  # keep the kit clean of __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parent))
import inventory  # noqa: E402

HOME = Path.home()
KIT = Path(__file__).resolve().parents[1]
CMUX_JSON = HOME / ".config/cmux/cmux.json"


def jsonc(text: str) -> dict:
    """cmux.json is JSON with comments and trailing commas."""
    out, i, quote = [], 0, False
    while i < len(text):
        c = text[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                quote = False
        elif c == '"':
            quote = True
            out.append(c)
        elif text.startswith("//", i):
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = len(text) if end < 0 else end + 2
            continue
        else:
            out.append(c)
        i += 1
    cleaned = re.sub(r",(\s*[}\]])", r"\1", "".join(out))
    data = json.loads(cleaned) if cleaned.strip() else {}
    return data if isinstance(data, dict) else {}


def cmux_config() -> dict:
    try:
        return jsonc(CMUX_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def check_cmux() -> list[tuple[bool, str, str]]:
    cli = inventory.which("cmux") or (str(inventory.CMUX_BIN) if inventory.CMUX_BIN.exists() else "")
    results = [(inventory.CMUX_APP.is_dir(), "cmux app in /Applications", "install cmux (onboarding/terminal/stages/1-cmux.md)")]
    results.append((bool(cli), "cmux command available", "cmux's CLI is in /Applications/cmux.app/Contents/Resources/bin; inside cmux it is on PATH"))
    up = False
    if cli:
        code, _out, _err = _call([cli, "workspace", "list", "--json"])
        up = code == 0
    results.append((up, "cmux is running and answers", "open the cmux app; if it is open and this runs outside cmux, the socket mode below is the cause"))
    text = inventory.version([cli, "--version"]) if cli else ""
    match = re.search(r"(\d+)\.(\d+)", text)
    results.append((bool(match) and (int(match.group(1)), int(match.group(2))) >= (0, 65), f"cmux 0.65 or newer ({text or 'unknown'})", "update cmux (the kit was tested on 0.65.2)"))
    mode = (cmux_config().get("automation") or {}).get("socketControlMode")
    results.append((mode in ("automation", "allowAll"),
                    f"cmux lets Berth's watcher connect (automation.socketControlMode = {mode or 'default, cmuxOnly'})",
                    "tools/install.sh cmux (docs/DECISIONS.md, \"cmux socket access\")"))
    tools = inventory.which("uv") or inventory.which("python3.12") or (HOME / ".local/bin/python3.12").exists()
    results.append((bool(tools), "uv or Python 3.12 for Berth's virtualenv", "install uv (onboarding/terminal/stages/1-cmux.md, step 1)"))
    wrapper = inventory.CMUX_APP / "Contents/Resources/bin/cmux-claude-wrapper"
    results.append((wrapper.exists(), "cmux's Claude wrapper", "a cmux without cmux-claude-wrapper cannot report Claude sessions; update cmux"))
    resume = (cmux_config().get("terminal") or {}).get("autoResumeAgentSessions")
    results.append((resume is False, "cmux's own agent resume is off (terminal.autoResumeAgentSessions false)", "tools/install.sh cmux"))
    config = cmux_config()
    links = [(config.get(group) or {}).get(key) for group, key in (
        ("browser", "openTerminalLinksInCmuxBrowser"), ("browser", "interceptTerminalOpenCommandInCmuxBrowser"),
        ("sidebar", "openPullRequestLinksInCmuxBrowser"), ("sidebar", "openPortLinksInCmuxBrowser"))]
    results.append((all(value is False for value in links) or None, "links open in the system's default browser",
                    "tools/install.sh cmux, unless they chose cmux's built-in browser (--cmux-browser)"))
    return results


def check_berth() -> list[tuple[bool, str, str]]:
    link = HOME / ".local/bin/berth"
    berth = inventory.which("berth") or (str(link) if link.exists() else "")
    results = [(bool(berth), "the berth command (~/.local/bin/berth)", "tools/install.sh berth")]
    if not berth:
        return results
    results.append((":" + str(HOME / ".local/bin") + ":" in ":" + _path() + ":", "~/.local/bin is on PATH", "add ~/.local/bin to PATH in your shell profile"))
    code, out, _err = _call([berth, "status", "--json"])
    status = _loads(out) if code == 0 else {}
    results.append((status.get("watcher") == "up", "the watcher runs (berth status)", "berth install, then read ~/.local/share/berth/watch.err"))
    plist = HOME / "Library/LaunchAgents/com.bach.berth.watch.plist"
    results.append((plist.exists(), "the watcher's LaunchAgent", "berth install"))
    code, out, _err = _call([berth, "check", "--json"])
    report = _loads(out)
    results.append((code == 0 and report.get("ok") is not False, "Berth matches cmux (berth check)", "read the violations from berth check; restart the watcher if it was just installed"))
    results.append((_watcher_reaches_cmux(), "the watcher reaches cmux (its last check is under two minutes old)",
                    "read ~/.local/share/berth/watch.err; 'Access denied' means cmux's socket mode (tools/install.sh cmux)"))
    sidebar = HOME / ".config/cmux/sidebars/berth.js"
    results.append((sidebar.exists() or None, "the Berth sidebar in cmux", "berth sidebar install, unless they chose cmux's own sidebar"))
    action = (cmux_config().get("actions") or {}).get("berth") or {}
    results.append((bool(action) or None, "a cmux shortcut opens the Berth screen (actions.berth)", "tools/install.sh berth, unless they declined it"))
    return results


def _watcher_reaches_cmux() -> bool:
    """The watcher writes invariant.json on every tick it reached cmux."""
    try:
        data = json.loads((HOME / ".local/share/berth/invariant.json").read_text(encoding="utf-8"))
        at = datetime.fromisoformat(str(data.get("at", "")).replace("Z", "+00:00"))
    except (OSError, ValueError):
        return False
    return data.get("cmux") == "up" and (datetime.now(timezone.utc) - at).total_seconds() < 120


def check_agents() -> list[tuple[bool, str, str]]:
    claude = inventory.which("claude")
    results = [(bool(claude), "claude on PATH", "install Claude Code")]
    if claude:
        for name in ("berth", "cmux-mcp"):
            code, out, _err = _call([claude, "mcp", "get", name], timeout=60)
            connected = code == 0 and "Connected" in out
            results.append((connected, f"the {name} MCP server is registered and connects", "tools/install.sh agents"))
            if name == "cmux-mcp" and code == 0:
                root = re.search(r"BERTH_ROOT=(\S+)", out)
                same = bool(root) and Path(root.group(1)).resolve() == (KIT / "packages/berth").resolve()
                results.append((same, "cmux-mcp uses this kit's Berth (BERTH_ROOT)", "tools/install.sh agents"))
    for skill in ("berth", "cmux-claude"):
        path = HOME / ".claude/skills" / skill / "SKILL.md"
        results.append((path.exists(), f"the {skill} skill in ~/.claude/skills", "tools/install.sh agents"))
    return results


def check_grok() -> list[tuple[bool, str, str]]:
    config = HOME / ".grok/config.toml"
    if not (HOME / ".grok").is_dir():
        return [(True, "Grok is not installed (nothing to check)", "")]
    text = config.read_text(encoding="utf-8") if config.exists() else ""
    return [
        ("[mcp_servers.berth]" in text, "Grok has the berth MCP", "tools/install.sh grok"),
        ("[mcp_servers.cmux-mcp]" in text, "Grok has cmux-mcp", "tools/install.sh grok"),
        ((HOME / ".grok/skills/cmux-grok/SKILL.md").exists(), "the cmux-grok skill", "tools/install.sh grok"),
        ((HOME / ".grok/skills/berth/SKILL.md").exists(), "the berth skill for Grok", "tools/install.sh grok"),
    ]


STAGES = {1: ("cmux", check_cmux), 2: ("Berth", check_berth), 3: ("agents", check_agents), 4: ("Grok", check_grok)}


def _call(argv, timeout=30):
    """(exit code, stdout, stderr); a command that cannot start counts as a failure."""
    try:
        done = inventory.subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, inventory.subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)
    return done.returncode, done.stdout, done.stderr


def _loads(text: str) -> dict:
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _path() -> str:
    import os

    return os.environ.get("PATH", "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--stage", type=int, choices=sorted(STAGES), action="append", help="check only this stage (repeatable)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    stages = args.stage or [1, 2, 3]
    document, failed = {}, 0
    for number in stages:
        name, check = STAGES[number]
        rows = check()
        document[name] = [{"ok": ok is True, "note": ok is None, "check": what, "fix": fix} for ok, what, fix in rows]
        failed += sum(1 for ok, _what, _fix in rows if ok is False)
    if args.json:
        print(json.dumps(document, indent=2))
    else:
        for name, rows in document.items():
            print(f"\n{name}")
            for row in rows:
                mark = "ok  " if row["ok"] else ("note" if row["note"] else "FIX ")
                print(f"  {mark}  {row['check']}" + ("" if row["ok"] else f"\n        -> {row['fix']}"))
        print(f"\n{'all good' if not failed else str(failed) + ' to fix'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
