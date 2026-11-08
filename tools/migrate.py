#!/usr/bin/env python3
"""Move Claude conversations from plain terminal tabs into cmux, where Berth files them.

    python3 tools/migrate.py plan --out ~/.berth-onboarding/migration.json
    #   edit the file: set "move" false for any you leave, a Berth "folder" for each
    python3 tools/migrate.py apply ~/.berth-onboarding/migration.json --dry-run
    python3 tools/migrate.py apply ~/.berth-onboarding/migration.json
    python3 tools/migrate.py status ~/.berth-onboarding/migration.json

A conversation is Claude's transcript, not the tab it runs in. Moving one means:
the person ends Claude in the old tab (type /exit there), then this script
opens a cmux workspace in the conversation's directory running Claude through
cmux's wrapper with ``-r <session id>``. The same conversation continues there.
When Berth is installed its watcher files the new workspace by itself; with a
``folder`` in the plan, the script then moves the row into that Berth folder.
When Berth already has a row for the conversation, the script opens that row
instead (``berth wake`` for a sleeping one, ``berth play --background`` for a
parked one), so nothing is cataloged twice.

Only interactive conversations in a terminal are marked to move in a new plan;
headless runs (``claude -p``) and sessions an editor extension started are
listed with ``"move": false``. ``apply`` refuses to run while a Claude process
outside cmux has left no session record (``tools/inventory.py`` lists them):
it could be one of the conversations being moved.

The script never kills a process and never types into another terminal. A
conversation that is still running outside cmux is refused, with the tab to
exit named. Moving one conversation into two places at once is never done.
Standard library only, Python 3.9+.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True  # keep the kit clean of __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parent))
import inventory  # noqa: E402

WRAPPER = Path("/Applications/cmux.app/Contents/Resources/bin/cmux-claude-wrapper")


def cmux_cli() -> str:
    return inventory.which("cmux") or (str(inventory.CMUX_BIN) if inventory.CMUX_BIN.exists() else "")


def berth_cli() -> str:
    found = inventory.which("berth")
    if found:
        return found
    local = Path.home() / ".local/bin/berth"
    return str(local) if local.exists() else ""


def call(argv: list[str], timeout: int = 60) -> tuple[int, str, str]:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)
    return done.returncode, done.stdout, done.stderr


def berth_row(berth: str, session_id: str) -> dict | None:
    """The Berth row holding this conversation, if any."""
    if not berth:
        return None
    code, out, _err = call([berth, "sessions", "--query", session_id, "--state", "live,sleeping,parked",
                            "--fields", "park_reason", "--json"])
    if code != 0:
        return None
    try:
        data = json.loads(out)
    except ValueError:
        return None
    for group in data.get("folders", []):
        for row in group.get("sessions", []):
            if row.get("session") == session_id:
                return dict(row, folder=group.get("folder"))
    return None


def plan(args) -> int:
    data = inventory.survey(args.days)
    entries = []
    for item in data["running"]:
        if item["host"] == "cmux":
            continue
        interactive = item.get("kind") in (None, "interactive") and item.get("entrypoint") in (None, "cli")
        entries.append({
            "session_id": item["session_id"],
            "title": item["name"] or "",
            "cwd": item["cwd"],
            "now": f"running in {item['host']}" + (" + tmux" if item["tmux"] else "") + (f" ({item['tty']})" if item["tty"] not in ("", "??") else "")
                   + ("" if interactive else f"; {item.get('kind')} via {item.get('entrypoint')}, so not marked"),
            "move": interactive,
            "folder": "",
            "flags": "",
        })
    if args.include_recent:
        for item in data["recent"]:
            entries.append({
                "session_id": item["session_id"],
                "title": item["title"] or "",
                "cwd": item["cwd"],
                "now": f"not running (last active {item['modified']})",
                "move": False,
                "folder": "",
                "flags": "",
            })
    document = {
        "about": "Set move true for each conversation to open in cmux, folder to the Berth folder it goes in "
                 "(Parent/Child; empty leaves it where Berth files it), flags to extra Claude flags such as "
                 "--permission-mode plan. A running conversation must be ended in its old tab (/exit) first.",
        "made": data["taken"],
        "entries": entries,
    }
    text = json.dumps(document, indent=2) + "\n"
    if args.out:
        path = Path(args.out).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}: {len(entries)} conversations ({sum(1 for e in entries if e['move'])} marked to move)")
    else:
        sys.stdout.write(text)
    return 0


def running_where(session_id: str, table) -> dict | None:
    for item in inventory.running_conversations(table):
        if item["session_id"] == session_id:
            return item
    return None


def apply(args) -> int:
    document = json.loads(Path(args.plan).expanduser().read_text(encoding="utf-8"))
    entries = [entry for entry in document.get("entries", []) if entry.get("move")]
    cmux, berth = cmux_cli(), berth_cli()
    if not cmux:
        print("cmux is not installed; install it first (your track's START-HERE).")
        return 1
    code, _out, err = call([cmux, "workspace", "list", "--json"], timeout=20)
    if code != 0:
        print(f"cmux is not answering ({err.strip()[:120]}); open the cmux app first.")
        return 1
    if not WRAPPER.exists():
        print(f"{WRAPPER} is missing; this cmux has no Claude wrapper, so Berth could not follow these sessions.")
        return 1
    table = inventory.processes()
    unknown = [item for item in inventory.unrecorded_claude(table, {r["pid"] for r in inventory.running_conversations(table)})
               if item["host"] != "cmux"]
    if unknown and not args.allow_unrecorded:
        print("Claude processes outside cmux have left no session record, so they could hold one of these conversations:")
        for item in unknown:
            print(f"  pid {item['pid']} in {item['host']} {item['tty']}: {item['command']}")
        print("End them (/exit), or update Claude Code and restart them, then apply again. Pass --allow-unrecorded only "
              "when the operator has checked that none of them is a conversation in this plan.")
        return 1
    failures = 0
    for entry in entries:
        session_id = entry["session_id"]
        title = entry.get("title") or session_id[:8]
        label = f"{title} ({session_id[:8]})"
        running = running_where(session_id, table)
        if running and running["host"] == "cmux":
            print(f"skip  {label}: already running in cmux")
            continue
        if running:
            where = running["host"] + (f" {running['tty']}" if running["tty"] not in ("", "??") else "")
            print(f"WAIT  {label}: still running in {where}. Type /exit in that tab, then run apply again.")
            failures += 1
            continue
        if inventory.transcript_path(session_id) is None:
            print(f"FAIL  {label}: Claude has no transcript for it, so there is nothing to resume.")
            failures += 1
            continue
        cwd = entry.get("cwd") or ""
        if not cwd or not Path(cwd).is_dir():
            print(f"FAIL  {label}: its directory {cwd!r} does not exist.")
            failures += 1
            continue
        row = berth_row(berth, session_id)
        if row and row.get("state") == "live":
            print(f"skip  {label}: Berth already has it live ({row['id'][:8]})")
            continue
        if row:
            sleeping = row.get("state") == "sleeping"
            command = [berth, "wake", row["id"], "--json"] if sleeping else [berth, "play", row["id"], "--background", "--json"]
            verb = "wake" if sleeping else "play"
            print(("would " if args.dry_run else "") + f"{verb}  {label}: Berth row {row['id'][:8]} in {row.get('folder')}")
            if not args.dry_run:
                code, out, err = call(command, timeout=90)
                result = _loads(out)
                if sleeping:
                    outcome = (result.get("results") or [{}])[0]
                    ok = code == 0 and outcome.get("result") == "woken"
                    why = outcome.get("message") or outcome.get("code") or "Berth did not wake it"
                else:
                    ok = code == 0 and not result.get("error")
                    why = (result.get("error") or {}).get("message") or (err or out).strip()[:200]
                if not ok:
                    print(f"FAIL  {label}: berth {verb}: {why}")
                    failures += 1
                    continue
        else:
            flags = shlex.split(entry.get("flags") or "")
            claude = " ".join(shlex.quote(part) for part in [str(WRAPPER), "-r", session_id, *flags])
            command = [cmux, "workspace", "create", "--name", title, "--cwd", cwd, "--focus", "false", "--command", f"exec {claude}"]
            print(("would " if args.dry_run else "") + f"open  {label} in {cwd}")
            if args.dry_run:
                print("        " + " ".join(shlex.quote(part) for part in command))
                continue
            code, out, err = call(command, timeout=60)
            if code != 0:
                print(f"FAIL  {label}: cmux workspace create: {(err or out).strip()[:200]}")
                failures += 1
                continue
        folder = (entry.get("folder") or "").strip().strip("/")
        if folder and berth and not args.dry_run:
            placed = None
            for _ in range(30):
                placed = berth_row(berth, session_id)
                if placed:
                    break
                time.sleep(1)
            if not placed:
                print(f"note  {label}: opened, but Berth has not filed it yet; move it later with: berth move <id> {shlex.quote(folder)}")
                continue
            call([berth, "mkdir", folder])
            code, out, err = call([berth, "move", placed["id"], folder])
            print(f"file  {label} -> {folder}" if code == 0 else f"note  {label}: berth move failed: {(err or out).strip()[:200]}")
        elif folder and not berth:
            print(f"note  {label}: Berth is not installed, so the folder {folder!r} waits until it is")
        if not args.dry_run:
            time.sleep(2)
    print(f"\n{len(entries)} marked to move, {failures} not moved yet." if entries else "Nothing is marked to move.")
    return 1 if failures else 0


def _loads(text: str) -> dict:
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def status(args) -> int:
    document = json.loads(Path(args.plan).expanduser().read_text(encoding="utf-8"))
    table = inventory.processes()
    berth = berth_cli()
    for entry in document.get("entries", []):
        if not entry.get("move"):
            continue
        session_id = entry["session_id"]
        running = running_where(session_id, table)
        row = berth_row(berth, session_id)
        where = (running["host"] if running else "not running")
        filed = f"Berth {row['state']} in {row.get('folder')}" if row else "not in Berth"
        print(f"{(entry.get('title') or session_id[:8])[:40]:40}  {where:14}  {filed}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan", help="list the conversations to move, as an editable JSON plan")
    p.add_argument("--out", help="write the plan here (default: print it)")
    p.add_argument("--days", type=int, default=14, help="with --include-recent: how far back")
    p.add_argument("--include-recent", action="store_true", help="also list recent conversations that are not running (marked move: false)")
    p.set_defaults(run=plan)
    a = sub.add_parser("apply", help="open each conversation marked move in cmux")
    a.add_argument("plan")
    a.add_argument("--dry-run", action="store_true", help="say what would happen; change nothing")
    a.add_argument("--allow-unrecorded", action="store_true",
                   help="apply although Claude processes with no session record run outside cmux (only after checking them)")
    a.set_defaults(run=apply)
    s = sub.add_parser("status", help="where each planned conversation is now")
    s.add_argument("plan")
    s.set_defaults(run=status)
    args = parser.parse_args()
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
