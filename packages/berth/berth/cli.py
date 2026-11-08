"""Console script ``berth``. Every command accepts ``--json``.

``berth`` with no subcommand opens the screen. ``berth screen`` does the same.
While that screen is open, this process drains ``intent``.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from berth import __version__
from berth.adopt import adopt_known
from berth.cmux import Cmux
from berth.db import Catalog, default_catalog_path
from berth.errors import BerthError, QueryAmbiguous
from berth.hooks import hook_for, load_hooks
from berth.install import install_agent
from berth.ops import (
    archive_folder,
    counts,
    excerpt_of,
    focus_berth,
    folder_path,
    format_bytes,
    human_status,
    human_tree,
    log_of,
    mkdir_path,
    move_berth,
    new_berth,
    pause_folder,
    delete_berth,
    rename_berth,
    resolve_query,
    resolve_workspace_env,
    rmdir_folder,
    run_reconcile,
    set_note,
    set_session,
    tree,
)
from berth.place import pid_alive
from berth.state import pause_berth, play_berth
from berth.watch import serve, watcher_state


def _common() -> argparse.ArgumentParser:
    # SUPPRESS so a subparser default does not wipe a flag placed before the command.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="print one JSON object",
    )
    common.add_argument(
        "--catalog",
        type=Path,
        default=argparse.SUPPRESS,
        help="sqlite path (tests and overrides)",
    )
    common.add_argument(
        "--db",
        type=Path,
        default=argparse.SUPPRESS,
        help="same path as --catalog",
    )
    return common


def _screen_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dump", action="store_true", help="print one frame and exit")
    parser.add_argument("--width", type=int, default=0, help="frame width for --dump")
    parser.add_argument("--height", type=int, default=0, help="frame height for --dump")
    parser.add_argument("--opened", default="", help="screen clock as ISO-8601")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="berth",
        description="Catalog cmux workspaces. `berth` opens the screen. Capture is automatic. Pause is not.",
        parents=[_common()],
    )
    parser.add_argument("--version", action="version", version=f"berth {__version__}")
    _screen_flags(parser)
    sub = parser.add_subparsers(dest="command")

    screen = sub.add_parser("screen", parents=[_common()], help="open the catalog screen")
    _screen_flags(screen)

    sub.add_parser("status", parents=[_common()], help="live count, parked count, watcher, child RSS")
    sub.add_parser("check", parents=[_common()], help="does the catalog match cmux; exit 1 if not")
    sub.add_parser("restore", parents=[_common()], help="reopen the sessions the last cmux quit closed")
    tree_cmd = sub.add_parser("tree", parents=[_common()], help="nested folders and berths")
    tree_cmd.add_argument("--all", action="store_true", help="include archived folders")

    adopt = sub.add_parser("adopt", parents=[_common()], help="live records for workspaces; closes nothing")
    adopt.add_argument(
        "--workspace",
        action="append",
        default=[],
        help="adopt this workspace UUID only; repeatable. Omit to adopt every workspace.",
    )

    sub.add_parser("watch", parents=[_common()], help="block and adopt each new workspace")
    install_cmd = sub.add_parser("install", parents=[_common()], help="install and bootstrap the LaunchAgent")
    install_cmd.add_argument("--mcp", action="store_true",
                             help="the shared MCP server's LaunchAgent (com.bach.berth.mcp) instead of the watcher's")
    install_cmd.add_argument("--uninstall", action="store_true", help="with --mcp: stop it and remove its LaunchAgent")
    install_cmd.add_argument("--port", type=int, default=0, help="with --mcp: the port (default 4792)")
    cmux_sidebar = sub.add_parser("sidebar", parents=[_common()],
                                  help="cmux's sidebar and notifications in Berth's words: install, or uninstall")
    cmux_sidebar.add_argument("action", choices=("install", "uninstall"))
    sub.add_parser("notify-hook", parents=[_common()], help="cmux's notification hook (cmux runs it; JSON in, JSON out)")

    fork = sub.add_parser("fork", parents=[_common()], help="fork a Claude or Grok session into a new workspace next to it")
    fork.add_argument("query")
    fork.add_argument("--title", help="name for the fork (default: the title plus ⑂)")
    fork.add_argument("--background", action="store_true", help="open it without taking focus")
    fork.add_argument("--cwd", metavar="DIRECTORY", help="fork a Claude conversation into this working directory (not for Grok)")
    cwd_cmd = sub.add_parser(
        "cwd", parents=[_common()],
        help="show or set a session's working directory (on disk; not its Berth folder). It opens there at its next wake or cycle",
    )
    cwd_cmd.add_argument("query")
    cwd_cmd.add_argument("directory", nargs="?", default=None, help="an absolute path to a directory that exists")
    cwd_cmd.add_argument("--cycle", action="store_true", help="cycle a live session now so it opens there (a busy one is refused)")
    compact_cmd = sub.add_parser(
        "compact", parents=[_common()],
        help="compact a Claude session with a note, checked on disk, then optionally send its next prompt (berth/compact.py)",
    )
    compact_cmd.add_argument("query")
    compact_cmd.add_argument("--note", help="TASK (and SEAT); Berth frames it, refuses keep/drop lists, and types /compact itself")
    compact_cmd.add_argument("--note-file", help="the note, from a file (any length up to 32 KB, any number of lines)")
    compact_cmd.add_argument("--then", dest="then_text", help="a prompt to send once the compaction is done")
    compact_cmd.add_argument("--then-file", help="that prompt, from a file")
    compact_cmd.add_argument("--fork", action="store_true", help="fork the session first and compact the fork; the original is untouched")
    compact_cmd.add_argument("--title", help="the fork's title (with --fork)")
    compact_cmd.add_argument("--cwd", metavar="DIRECTORY", help="with --fork: the fork's working directory (it runs there, "
                                                                "and its transcript is written under that directory's project)")
    compact_cmd.add_argument("--wait", type=int, default=60, help="minutes to wait for a busy session to become ready (default 60)")
    compact_cmd.add_argument("--background", action="store_true",
                             help="return at once; a detached worker does it. Use it from an agent's shell, whose "
                                  "commands time out, and it is automatic for your own pane")
    send_cmd = sub.add_parser(
        "send", parents=[_common()],
        help="give a session a prompt from a program: typed into its prompt box once it is ready, in order, with a "
             "receipt (berth/delivery.py)",
    )
    send_cmd.add_argument("query", help="the session: a Berth id, a title, or an agent session id")
    send_cmd.add_argument("--text", help="the prompt")
    send_cmd.add_argument("--file", help="the prompt, from a file")
    send_cmd.add_argument("--sender", required=True, help="who sends it, e.g. slack-hooks; only it can replace or take it back")
    send_cmd.add_argument("--key", help="replaces this sender's own still-queued prompt with the same key")
    send_cmd.add_argument("--meta", help="JSON kept with the receipt, for the sender (Berth does not read it)")
    send_cmd.add_argument("--wake", action="store_true", help="open the session first if it is sleeping or parked")
    send_cmd.add_argument("--wait", type=int, default=None, help="minutes to keep trying before it expires (default 1440)")
    send_cmd.add_argument("--busy", action="store_true",
                          help="type it while a Claude session works too, where Claude queues it (after a line asking "
                               "it to finish its current work first), not only once it is ready; never into a "
                               "question, a permission or a draft. Grok and slash commands wait for a ready prompt")
    send_cmd.add_argument("--on-settle", dest="on_settle",
                          help="a command Berth runs once when the receipt ends (delivered, expired, failed, cancelled), "
                               "with BERTH_DELIVERY and BERTH_STATE set: a JSON list, or words split like a shell's")
    receipt_cmd = sub.add_parser("receipt", parents=[_common()], help="one prompt's receipt: its state, and why")
    receipt_cmd.add_argument("delivery")
    receipts_cmd = sub.add_parser("receipts", parents=[_common()], help="prompts' receipts, newest first")
    receipts_cmd.add_argument("--sender")
    receipts_cmd.add_argument("--session", help="a Berth id, a title, or an agent session id")
    receipts_cmd.add_argument("--open", action="store_true", help="only prompts still waiting")
    receipts_cmd.add_argument("--limit", type=int, default=200)
    unsend_cmd = sub.add_parser("unsend", parents=[_common()], help="take back a queued prompt (its own sender only)")
    unsend_cmd.add_argument("delivery")
    unsend_cmd.add_argument("--sender", required=True)
    orch_cmd = sub.add_parser("orchestrator", parents=[_common()],
                              help="a session's orchestrator: the one the nearest folder up its tree names")
    orch_cmd.add_argument("query", help="the session, or me")
    orch_cmd.add_argument("--hook", action="store_true",
                          help="print Claude's SessionStart hook output (or nothing); never fails")
    orchestrate_cmd = sub.add_parser("orchestrate", parents=[_common()], help="name a folder's orchestrator")
    orchestrate_cmd.add_argument("folder")
    orchestrate_cmd.add_argument("session", help="the orchestrator session; none (its sessions have none, the walk "
                                                  "stops); or inherit (clear it, take the one from above)")
    config_cmd = sub.add_parser("config-dir", parents=[_common()],
                                help="show or set the Claude Code config folder (one per Claude account) a Claude "
                                     "session runs on, from its next open; --cycle moves it once idle")
    config_cmd.add_argument("query")
    config_cmd.add_argument("folder", nargs="?", help="a ~/.claude-* folder, or default (~/.claude)")
    config_cmd.add_argument("--cycle", action="store_true", help="cycle it onto the folder as soon as it is idle")
    standard_cmd = sub.add_parser("standard", parents=[_common()],
                                  help="every Claude session in auto mode at xhigh effort: what is off, and the fixing")
    standard_cmd.add_argument("--correct", choices=("on", "off"), help="Berth fixes it at idle (on, the default) or "
                                                                         "only reports it (off)")
    mode_cmd = sub.add_parser("mode", parents=[_common()],
                              help="open a Claude session in this permission mode the next time it opens, once")
    mode_cmd.add_argument("query")
    mode_cmd.add_argument("mode", help="auto, manual, acceptEdits, plan, dontAsk, or bypassPermissions")
    compact_run = sub.add_parser("compact-run", parents=[_common()], help=argparse.SUPPRESS)
    compact_run.add_argument("job")
    cycle = sub.add_parser("cycle", parents=[_common()], help="turn a live session off and on again: pause, then play it")
    cycle.add_argument("query")
    cycle.add_argument("--confirm", action="store_true", help="required for a process: its command runs again")
    cycle.add_argument("--force", action="store_true", help="cycle a busy session anyway: its turn and background work are cut off")
    cycle.add_argument("--when", choices=("now", "idle"), default=None,
                       help="idle: Berth's watcher cycles it as soon as it is idle (your own session always goes this way)")
    cycle.add_argument("--focus", action="store_true", help="select the new workspace")
    play = sub.add_parser("play", parents=[_common()], help="focus a live berth or create one from the recipe")
    play.add_argument("query", nargs="?", default="")
    play.add_argument("--focus", choices=("true", "false"), default="true")
    play.add_argument("--background", action="store_true", help="create without taking focus")
    play.add_argument("--run-command", action="store_true", help="required to play a process berth")
    play.add_argument("--sessions", type=Path, help="Grok sessions root (tests)")

    pause = sub.add_parser("pause", parents=[_common()], help="write the recipe, then close the workspace")
    pause.add_argument("query", nargs="?", default="")
    pause.add_argument("--force", action="store_true", help="pause a busy agent")
    pause.add_argument("--confirm", action="store_true", help="pause a process, or confirm a folder pause")
    pause.add_argument("--folder", default="", help="pause live berths in this folder")

    focus = sub.add_parser("focus", parents=[_common()], help="select the live workspace")
    focus.add_argument("query")

    new = sub.add_parser("new", parents=[_common()], help="new parked grok berth, then play")
    new.add_argument("--folder", required=True)
    new.add_argument("--title", required=True)
    new.add_argument("--cwd", default="")
    new.add_argument("--focus", choices=("true", "false"), default="true")

    mkdir = sub.add_parser("mkdir", parents=[_common()], help="create folders, including parents")
    mkdir.add_argument("path")

    move = sub.add_parser("move", parents=[_common()], help="move a berth into a folder")
    move.add_argument("query")
    move.add_argument("folder")

    note = sub.add_parser("note", parents=[_common()], help="set the one-line note")
    note.add_argument("query")
    note.add_argument("text", nargs="+")

    pin = sub.add_parser("pin", parents=[_common()], help="pin a session: first in cmux's sidebar, 📌 in the screen")
    pin.add_argument("query")
    unpin = sub.add_parser("unpin", parents=[_common()], help="unpin a session")
    unpin.add_argument("query")
    # The two flags a session raises (berth/attention.py): the same as the MCP's update urgent / follow_up, for a
    # session whose MCP server predates follow_up (2026-10-31) and for scripts. "me" is the session you run in.
    for name, helptext in (("urgent", "say a session needs the operator urgently: red, first, a notification"),
                           ("follow-up", "say something only the operator can do blocks a session (an action, not "
                                         "a question or an approval): magenta, until a prompt reaches it")):
        flag_cmd = sub.add_parser(name, parents=[_common()], help=helptext)
        flag_cmd.add_argument("query", help="the session, me (where this runs), or selected (cmux's selected workspace)")
        flag_cmd.add_argument("why", nargs="*", help="one line: what is left, or what you need")
        flag_cmd.add_argument("--off", action="store_true", help="take the flag back")
        if name == "follow-up":
            flag_cmd.add_argument("--dismiss", action="store_true",
                                  help="the operator does not want it (⌘⇧X in cmux, berth/keys.py): lower it, and "
                                       "refuse the same why again")

    excerpt = sub.add_parser("excerpt", parents=[_common()])
    excerpt.add_argument("query")

    reconcile = sub.add_parser("reconcile", parents=[_common()], help="repair pausing and playing; adopt strays when asked")
    reconcile.add_argument(
        "--no-adopt",
        action="store_true",
        help="do not catalog workspaces that have no berth",
    )

    log = sub.add_parser("log", parents=[_common()])
    log.add_argument("query")

    rmdir = sub.add_parser(
        "rmdir",
        parents=[_common()],
        help="delete a folder. berths move to the parent or Inbox. nothing closes",
    )
    rmdir.add_argument("path")
    archive = sub.add_parser("archive", parents=[_common()], help="hide a folder that has no live berth")
    archive.add_argument("path")
    move_folder = sub.add_parser(
        "move-folder", parents=[_common()], help="put a folder, with everything in it, inside another folder"
    )
    move_folder.add_argument("path")
    move_folder.add_argument("into", nargs="?", default="", help="destination folder; omit for the top level")
    rename_folder = sub.add_parser("rename-folder", parents=[_common()], help="rename one folder in place")
    rename_folder.add_argument("path")
    rename_folder.add_argument("name")

    rename = sub.add_parser("rename", parents=[_common()])
    rename.add_argument("query")
    rename.add_argument("title", nargs="+")

    recipe = sub.add_parser("recipe", parents=[_common()], help="set a Grok session id on a berth")
    recipe.add_argument("query")
    recipe.add_argument("--session", required=True)

    delete = sub.add_parser("delete", parents=[_common()], help="move one catalog row to the trash (30 days)")
    delete.add_argument("query")
    delete.add_argument("--force", action="store_true",
                        help="delete it even if it is a Berth screen or the workspace this command runs in")
    sleep_cmd = sub.add_parser("sleep", parents=[_common()], help="put a session (or --all live ones) to sleep; wake brings them back")
    sleep_cmd.add_argument("query", nargs="?", default="")
    sleep_cmd.add_argument("--all", action="store_true", help="every live session but the Berth screen")
    sub.add_parser("memory", parents=[_common()],
                   help="memory now: the Mac as Activity Monitor counts it, and each session's footprint by part "
                        "(Claude, MCP, hooks, shell), from the Performance Agent's definitions")
    sub.add_parser("whoami", parents=[_common()],
                   help="the Berth session this process runs in (by Claude's session id, else its open workspace); "
                        "NOT_FOUND when it is in none")
    idle_cmd = sub.add_parser("idle", parents=[_common()],
                              help="make a session idle: closed to free its memory, in use again soon; wake opens it")
    idle_cmd.add_argument("query")
    wake_cmd = sub.add_parser("wake", parents=[_common()], help="open a sleeping session again, or --all of them")
    wake_cmd.add_argument("query", nargs="?", default="")
    wake_cmd.add_argument("--all", action="store_true", help="every sleeping session")
    trash_cmd = sub.add_parser("trash", parents=[_common()], help="the trash: list, restore (to Inbox), or forget")
    trash_cmd.add_argument("action", nargs="?", default="list", choices=["list", "restore", "forget"])
    trash_cmd.add_argument("query", nargs="?", default="", help="trash id, id prefix, or title")

    sub.add_parser("colors", parents=[_common()], help="the computed folder colors and how far apart they are")

    mcp_cmd = sub.add_parser("mcp", parents=[_common()],
                             help="serve the Berth MCP on stdio (for Claude, Grok, and other agents); --http serves "
                                  "every session from one process, with cmux-mcp beside it")
    mcp_cmd.add_argument("--http", action="store_true",
                         help="one shared server on 127.0.0.1 (/berth, /cmux-mcp), for Claude's http MCP type")
    mcp_cmd.add_argument("--port", type=int, default=0, help="with --http: the port (default 4792)")
    mcp_cmd.add_argument("--check", action="store_true",
                         help="the shared server's health: does each path answer a ping (exit 1 when berth is down)")
    sessions_cmd = sub.add_parser(
        "sessions", parents=[_common()],
        help="the sessions in a scope: a folder, levels below (--down) and above (--up); the Berth MCP's sessions tool",
    )
    sessions_cmd.add_argument("--folder", help="path, unique name, or / (default: this pane's folder, else everything)")
    sessions_cmd.add_argument("--down", help="levels of subfolders, or all")
    sessions_cmd.add_argument("--up", help="ancestors to add, or all")
    sessions_cmd.add_argument("--branches", action="store_true", help="with --up: the ancestors' other subfolders too")
    sessions_cmd.add_argument("--query", help="words in the title, or an id, session id, or workspace id")
    sessions_cmd.add_argument("--state", help="comma list: live, busy, idle, sleeping, parked, background, playing, pausing")
    sessions_cmd.add_argument("--agent", help="comma list: claude, grok, shell, process, ...")
    sessions_cmd.add_argument("--fields", help="comma list of extra fields, or all")
    sessions_cmd.add_argument("--limit", type=int, default=None)
    sessions_cmd.add_argument("--offset", type=int, default=None)

    offer = sub.add_parser("offer", parents=[_common()], help="print this Mac's portable catalog")
    offer.add_argument("--serve", action="store_true", help="serve it on 127.0.0.1 for Tailscale")
    offer.add_argument("--port", type=int, default=0, help="offer port. Default 4791")

    sub.add_parser("tailscale", parents=[_common()], help="publish the offer on the tailnet")
    sub.add_parser("peers", parents=[_common()], help="other machines on the tailnet")

    preview = sub.add_parser("preview", parents=[_common()], help="show a remote catalog against this one")
    preview.add_argument("--from", dest="source", required=True, help="peer name, https URL, or snapshot file")
    preview.add_argument("--folder", action="append", default=[], help="limit the preview to this folder")

    merge = sub.add_parser("merge", parents=[_common()], help="add chosen remote folders as parked rows")
    merge.add_argument("--from", dest="source", required=True, help="peer name, https URL, or snapshot file")
    merge.add_argument("--folder", action="append", default=[], help="folder to add. Repeatable")
    merge.add_argument("--all", action="store_true", help="add every folder in the offer")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    as_json = bool(getattr(args, "json", False))
    if args.command is None or args.command == "screen":
        return _screen(args)
    if args.command == "orchestrator" and args.hook:
        return _orchestrator_hook(args)
    try:
        if args.command == "watch":
            catalog = _open(args)
            cmux = Cmux()
            serve(catalog, cmux)
            return 0
        if args.command == "mcp":
            if args.check:
                from berth.mcp_http import DEFAULT_PORT, check

                health = check(args.port or DEFAULT_PORT)
                print(json.dumps(health, sort_keys=True) if as_json else
                      f"shared MCP server on {health['port']}: berth {health['berth']} · cmux-mcp {health['cmux-mcp']}")
                return 0 if health["berth"] == "up" else 1
            if args.http:
                from berth.mcp_http import DEFAULT_PORT, serve as serve_http

                return serve_http(args.port or DEFAULT_PORT, db_path=_catalog_path(args))
            from berth.mcp import Api, serve as serve_mcp

            return serve_mcp(Api(_catalog_path(args)))
        if args.command == "sessions":
            from berth.scope import query as scope_query

            options = {name: getattr(args, name) for name in ("folder", "down", "up", "query", "state", "agent", "limit", "offset")
                       if getattr(args, name) is not None}
            if args.branches:
                options["branches"] = True
            if args.fields:
                options["fields"] = [item.strip() for item in args.fields.split(",") if item.strip()]
            result = scope_query(_catalog_path(args), options)
            _emit(result, as_json, human=_human_sessions)
            return 0
        if args.command == "sidebar":
            from berth import cmux_sidebar as sidebar_module

            result = sidebar_module.install() if args.action == "install" else sidebar_module.uninstall()
            _emit(result, as_json, human=lambda doc: json.dumps(doc, indent=2, ensure_ascii=False))
            return 0
        if args.command == "notify-hook":
            from berth.cmux_sidebar import notify_hook

            return notify_hook(sys.stdin, sys.stdout, _catalog_path(args))
        if args.command == "install" and args.mcp:
            from berth.install import install_mcp, uninstall_mcp

            result = uninstall_mcp() if args.uninstall else install_mcp(args.port or None)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if args.uninstall or (result["bootstrapped"] and result["health"].get("berth") == "up") else 1
        if args.command == "install":
            result = install_agent()
            _emit(result, as_json, human=_human_install)
            if not result["bootstrapped"]:
                return 1
            return 0
        catalog = _open(args)
        if args.command == "restore":
            from berth.restore import last_quit, restore_rows

            result = {"results": restore_rows(catalog, Cmux(), last_quit(catalog))}
            _emit(result, as_json, human=lambda doc: "\n".join(
                f"{item['result']:<9} {catalog.require_berth(item['berth_id'])['title']}" for item in doc["results"]
            ) or "nothing to restore")
            return 0
        if args.command == "check":
            result = _check(catalog, Cmux())
            _emit(result, as_json, human=_human_check)
            return 0 if result["ok"] else 1
        result = _dispatch(args, catalog)
        _emit(result, as_json, human=_human_for(args.command))
        return 0
    except QueryAmbiguous as exc:
        payload = {
            "matches": exc.matches,
            "error": {
                "code": "NOT_FOUND",
                "message": "query matched more than one berth",
                "details": {"matches": exc.matches},
            },
        }
        if as_json:
            _write_json(payload)
        else:
            print("ambiguous", file=sys.stderr)
            for match in exc.matches:
                print(f"  {match['id']}  {match['title']}", file=sys.stderr)
        return 2
    except BerthError as exc:
        if as_json:
            _write_json(exc.object())
        else:
            print(f"{exc.code}: {exc.message}", file=sys.stderr)
            excerpt = exc.details.get("excerpt")
            if excerpt:
                print(excerpt, file=sys.stderr)
        return 1


def _catalog_path(args) -> Path:
    chosen = getattr(args, "catalog", None)
    if chosen is None:
        chosen = getattr(args, "db", None)
    if chosen is None:
        return default_catalog_path()
    return Path(chosen).expanduser()


def _human_sessions(document: dict) -> str:
    scope = document["scope"]
    head = f"{scope['folder']}  down {scope['down']}  up {scope['up']}" + ("  branches" if scope.get("branches") else "")
    lines = [head + f"  ·  {document['total']} sessions"]
    for group in document["folders"]:
        extra = []
        if group.get("deeper"):
            extra.append(f"{group['deeper']['folders']} folders below")
        if group.get("beside"):
            extra.append(f"{group['beside']['folders']} folders beside")
        lines.append(f"{'  ' * max(0, group['level'] - min(g['level'] for g in document['folders']))}{group['folder']}"
                     + (f"  ({', '.join(extra)})" if extra else ""))
        for row in group["sessions"]:
            mark = " *" if row.get("you") else ""
            state = "busy" if row.get("busy") else row["state"]
            lines.append(f"    {state:<10} {row['title']}{mark}  {row.get('session') or ''}".rstrip())
    return "\n".join(lines)


def _open(args) -> Catalog:
    return Catalog(_catalog_path(args)).open()


def _screen(args) -> int:
    from berth.catalog import parse_time

    path = _catalog_path(args)
    opened = None
    if getattr(args, "opened", ""):
        opened = parse_time(args.opened)
        if opened is None:
            print("berth: --opened is not a time", file=sys.stderr)
            return 2
    if not path.is_file():
        print(f"berth: no catalog at {path}", file=sys.stderr)
        print("Pass --catalog, or build a demo with python -m berth.fixture.", file=sys.stderr)
        return 1
    width = getattr(args, "width", 0) or 96
    height = getattr(args, "height", 0) or 24
    if getattr(args, "dump", False):
        from berth.app import App

        app = App(path, width=width, height=height, opened_at=opened)
        sys.stdout.write(app.text() + "\n")
        return 0
    if not sys.stdout.isatty():
        print("berth: the screen needs a terminal. Pass --dump to print one frame.", file=sys.stderr)
        return 1
    from berth.curses_ui import run
    from berth.drain import drain_once
    from berth.figures import refresh_figures
    from berth.hooks import load_hooks
    from berth.place import pid_alive
    from berth.watch import watcher_state

    catalog = Catalog(path).open()
    cmux = Cmux()
    sampled = {"at": 0.0}
    # One Berth screen, in the workspace named Berth (berth/screens.py).
    from berth.screens import go_home, lock, own_workspace, place, promote

    workspace_id = own_workspace()
    held, other = lock(path.parent, workspace_id)
    if held is not None:
        from berth.health import register

        register(path.parent, "screen")
    if held is None:
        title = _workspace_title(cmux, other.get("workspace_id")) or "another terminal"
        try:
            if other.get("workspace_id"):
                cmux.select_workspace(other["workspace_id"])
        except BerthError:
            pass
        catalog.close()
        print(f"berth: Berth is already open in {title!r} (pid {other.get('pid')}). Only one Berth screen runs at "
              f"a time; switched to it. ⌘⇧B always goes there.", file=sys.stderr)
        return 3
    where, home = place(cmux, workspace_id)
    if where == "home":
        os.close(held)  # the screen starts at home, not here
        started = go_home(cmux, home)
        catalog.close()
        print("berth: Berth lives in the workspace named Berth; " + (
            "switched there and started it." if started else
            "switched there. Something is running in it, so nothing was typed: quit it, then type berth."),
            file=sys.stderr)
        return 3
    if where == "promote":
        try:
            promote(catalog, cmux, workspace_id)  # this workspace becomes Berth's home: one name everywhere
        except BerthError:
            pass

    def on_tick() -> None:
        try:
            now = time.monotonic()
            hooks = load_hooks()
            if sampled["at"] == 0.0 or now - sampled["at"] >= 2:
                refresh_figures(
                    catalog,
                    rss_bytes=None,  # memory is the watcher's measure now (berth/memory.py), not cmux's RSS sum
                    watcher=watcher_state(catalog.path.parent),
                    hooks=hooks,
                    pid_alive_fn=pid_alive,
                )
                sampled["at"] = now
            drain_once(
                catalog,
                cmux,
                hooks=hooks,
                pid_alive_fn=pid_alive,
                caller_workspace_id=os.environ.get("CMUX_WORKSPACE_ID"),
            )
            # The watcher's observer is the only thing that parks or adopts
            # from what cmux shows. The screen runs your intents and reads.
        except sqlite3.Error:
            return
        except BerthError:
            return

    try:
        return run(path, opened_at=opened, on_tick=on_tick)
    finally:
        catalog.close()
        os.close(held)


def _workspace_title(cmux, workspace_id: str | None) -> str | None:
    if not workspace_id:
        return None
    try:
        for item in cmux.list_workspaces():
            if item.id.lower() == workspace_id.lower():
                return item.title
    except Exception:
        return None
    return None


def _orchestrator_hook(args) -> int:
    """Claude's SessionStart hook (matcher compact): the orchestrator in four lines, or nothing. Never fails.

    Nothing inside Berth's own headless compaction (berth/compact.py, ``COMPACTING_ENV``): there it started a turn.
    """
    if os.environ.get("BERTH_COMPACTING") == "1":
        return 0
    try:
        import select

        hook: dict = {}
        if not sys.stdin.isatty() and select.select([sys.stdin], [], [], 0.2)[0]:
            try:
                hook = json.loads(sys.stdin.read() or "{}")
            except ValueError:
                hook = {}
        from berth.orchestrator import for_hook, hook_json

        db = Catalog(_catalog_path(args)).open()
        try:
            found = for_hook(db, session_id=hook.get("session_id"), workspace=os.environ.get("CMUX_WORKSPACE_ID"),
                             cwd=hook.get("cwd") or os.getcwd())
        finally:
            db.close()
        text = hook_json(found)
        if text:
            print(text)
    except Exception:
        pass
    return 0


def _requester(db: Catalog, caller: str | None, command: str) -> str:
    """Who asked, for the record: the Berth session this runs in, else the program that ran it (2026-11-03).

    "berth cycle" alone said nothing: 39 cycles queued at 15:06 could not be traced to whoever asked.
    """
    if caller:
        row = db.conn.execute("SELECT title FROM berth WHERE state = 'live' AND lower(cmux_workspace_id) = lower(?)",
                              (caller,)).fetchone()
        if row is not None:
            return f"{row['title']} ({command})"
    from berth import proc

    parent = os.getppid()
    program = " ".join(proc.command(parent).split()[:3])[:80] or "unknown"
    return f"{command} from pid {parent}: {program}"

def memory_now(db: Catalog, cmux) -> dict:
    """``berth memory``: one measure now (berth/memory.py), each session named by its Berth row."""
    from berth import memory
    from berth.timeutil import utc_now

    result = memory.measure(cmux.terminals())
    rows = {(row["cmux_workspace_id"] or "").lower(): row
            for row in db.conn.execute("SELECT id, title, cmux_workspace_id FROM berth WHERE state = 'live'")}
    by_session = []
    for workspace, parts in result.pop("by_workspace").items():
        row = rows.get(workspace)
        by_session.append({"id": row["id"] if row else None, "title": row["title"] if row else "(no Berth row)",
                           "workspace": workspace, **parts})
    by_session.sort(key=lambda item: -item["footprint_mb"])
    return {"at": utc_now(), **result, "by_session": by_session}


def whoami(db: Catalog, environ=None) -> dict:
    """The Berth session this process runs in (2026-11-06, for Slack watch: "me" from a process).

    Claude's session id (``CLAUDE_CODE_SESSION_ID``, in every shell its Bash tool starts) decides first: it names one
    conversation and survives a cycle. Else the open workspace (``CMUX_WORKSPACE_ID``) of a live row. Neither:
    NOT_FOUND, so a caller outside Berth gets a clean refusal, never a guess.
    """
    from berth.ops import folder_path

    env = os.environ if environ is None else environ
    row, via = None, ""
    session = (env.get("CLAUDE_CODE_SESSION_ID") or "").strip()
    if session:
        row = db.conn.execute("SELECT * FROM berth WHERE agent_session_id = ? AND state != 'deleted' "
                              "ORDER BY (state = 'live') DESC, updated_at DESC LIMIT 1", (session,)).fetchone()
        via = "session"
    workspace = (env.get("CMUX_WORKSPACE_ID") or "").strip()
    if row is None and workspace:
        row = db.conn.execute("SELECT * FROM berth WHERE lower(cmux_workspace_id) = lower(?) AND state = 'live'",
                              (workspace,)).fetchone()
        via = "workspace"
    if row is None:
        raise BerthError("NOT_FOUND", "this process is not in a Berth session",
                         {"session": session or None, "workspace": workspace or None})
    from berth.model import closed_state

    return {"id": row["id"], "title": row["title"], "folder": folder_path(db, row["folder_id"]),
            "session": row["agent_session_id"], "agent": (row["agent_name"] or row["kind"] or "").casefold(),
            "state": closed_state(row["state"], row["park_reason"]), "workspace": row["cmux_workspace_id"], "via": via}


def _dispatch(args, db: Catalog):
    cmux = Cmux()
    hooks = load_hooks()
    caller = os.environ.get("CMUX_WORKSPACE_ID")
    if args.command == "status":
        return _status(db, cmux)
    if args.command == "whoami":
        return whoami(db)
    if args.command == "memory":
        return memory_now(db, cmux)
    if args.command == "tree":
        return tree(db, include_archived=args.all)
    if args.command == "adopt":
        only = set(args.workspace) if args.workspace else None
        records = adopt_known(db, cmux, hooks, only_ids=only)
        return {"adopted": records, "count": len(records)}
    if args.command == "cycle":
        from berth.ops import cycle_berth

        record = resolve_query(db, args.query)
        own = bool(caller) and (record["cmux_workspace_id"] or "").lower() == caller.lower()
        if own or args.when == "idle":
            # This command runs in its own pane: it cannot close that pane and live to reopen it.
            from berth.deferred import SELF_GRACE_SECONDS, queue

            idle = args.when != "now"
            item = queue(db, record["id"], "cycle", idle=idle, force=args.force,
                         delay_seconds=SELF_GRACE_SECONDS if own else 0,
                         requested_by=_requester(db, caller, "berth cycle"))
            return {"queued": True, "id": record["id"], "title": record["title"], "when": "idle" if idle else "now",
                    "until": item["until"], "by": "Berth's watcher"}
        return cycle_berth(
            db,
            cmux,
            record["id"],
            confirm=args.confirm or args.force,
            focus=args.focus,
            hook=hook_for(hooks, record["cmux_workspace_id"]),
            caller_workspace_id=caller,
            pid_alive_fn=pid_alive,
        )
    if args.command == "fork":
        from berth.ops import fork_berth

        record = resolve_query(db, args.query)
        return fork_berth(db, cmux, record["id"], focus=not args.background, title=args.title, cwd=args.cwd)
    if args.command in ("send", "receipt", "receipts", "unsend"):
        from berth import delivery

        if args.command == "receipt":
            return delivery.receipt(db, args.delivery)
        if args.command == "unsend":
            return delivery.cancel(db, args.delivery, sender=args.sender)
        if args.command == "receipts":
            berth_id = resolve_query(db, args.session)["id"] if args.session else None
            return {"receipts": delivery.receipts(db, sender=args.sender, berth_id=berth_id, open_only=args.open,
                                                  limit=args.limit)}
        if (args.text is None) == (args.file is None):
            raise BerthError("BAD_ARGUMENT", "give the prompt as --text or --file", {})
        text = Path(args.file).expanduser().read_text(encoding="utf-8") if args.file else args.text
        meta = None
        if args.meta:
            try:
                meta = json.loads(args.meta)
            except ValueError as exc:
                raise BerthError("BAD_ARGUMENT", "--meta is JSON", {}) from exc
        record = resolve_query(db, args.query)
        from berth.activity import program_states
        from berth.send import look

        def reread():
            return look(cmux, db.require_berth(record["id"]), programs=program_states(db.path.parent))

        notify = None
        if args.on_settle:
            import shlex

            try:
                notify = json.loads(args.on_settle) if args.on_settle.lstrip().startswith("[") else shlex.split(args.on_settle)
            except ValueError as exc:
                raise BerthError("BAD_ARGUMENT", "--on-settle is a JSON list or a command line", {}) from exc
        return delivery.offer(db, cmux, record["id"], text, sender=args.sender, key=args.key, meta=meta,
                              wake=args.wake, wait_minutes=args.wait, notify=notify, look=reread, busy=args.busy)
    if args.command == "orchestrator":
        from berth.orchestrator import of_session

        where = args.query.strip().lower()
        if where == "me":
            mine = db.conn.execute("SELECT id FROM berth WHERE state = 'live' AND lower(cmux_workspace_id) = lower(?)",
                                   (caller or "",)).fetchone()
            if mine is None:
                raise BerthError("NOT_FOUND", "me is the session this runs in, and this is not in a Berth session", {})
            berth_id = mine["id"]
        else:
            berth_id = resolve_query(db, args.query)["id"]
        found = of_session(db, berth_id)
        if found is not None and found.get("root"):
            return {"id": berth_id, **found}  # a root orchestrator: its peers (2026-11-06)
        return {"id": berth_id, "orchestrator": found}
    if args.command == "orchestrate":
        from berth.orchestrator import set_orchestrator

        from berth.orchestrator import NONE

        word = args.session.strip().lower()
        target = NONE if word == "none" else None if word in ("inherit", "") else resolve_query(db, args.session)["id"]
        return set_orchestrator(db, args.folder, target, by=_requester(db, caller, "berth orchestrate"))
    if args.command == "config-dir":
        from berth import configdir

        row = db.require_berth(resolve_query(db, args.query)["id"])
        if args.folder is not None:
            row = configdir.set_config_dir(db, row["id"], args.folder, by=caller or "cli")
        out = {"id": row["id"], "title": row["title"], "config_dir": configdir.of(row) or "~/.claude"}
        if args.cycle and row["state"] == "live":
            from berth.deferred import queue

            queue(db, row["id"], "cycle", idle=True, requested_by=_requester(db, caller, "berth config-dir"))
            out["cycle"] = "queued: Berth's watcher cycles it onto the folder as soon as it is idle"
        return out
    if args.command == "standard":
        from berth import standard

        if args.correct:
            standard.set_correcting(db.path.parent, args.correct == "on")
        return standard.report(db, db.path.parent)
    if args.command == "mode":
        from berth.state import set_reopen_mode

        return set_reopen_mode(db, resolve_query(db, args.query)["id"], args.mode)
    if args.command == "compact-run":
        from berth.compact import run_job

        return run_job(db, Path(args.job), cmux=cmux)
    if args.command == "compact":
        from berth.compact import plan

        def read(text, path, label):
            if text is not None and path:
                raise BerthError("BAD_ARGUMENT", f"give {label} as text or a file, not both", {})
            return Path(path).expanduser().read_text(encoding="utf-8") if path else text

        record = resolve_query(db, args.query)
        job = plan(db, record["id"], note=read(args.note, args.note_file, "the note"),
                   prompt=read(args.then_text, args.then_file, "the next prompt"), fork=args.fork, title=args.title,
                   wait_minutes=args.wait, caller=caller, requested_by=_requester(db, caller, "berth compact"), cmux=cmux, cwd=args.cwd)
        from berth.compact import start

        # Your own pane cannot be ready while this command runs in it: a foreground run would wait on itself.
        detach = args.background or bool(job.get("own"))
        result = start(db, job, foreground=not detach)
        if detach:
            result["follow"] = f"berth log {job['berth_id']} shows compact_done or compact_failed, then prompt_sent"
        return result
    if args.command == "cwd":
        from berth.workdir import pending_note, set_cwd

        record = resolve_query(db, args.query)
        if args.directory is None:
            row = db.require_berth(record["id"])
            out = {"id": row["id"], "title": row["title"], "cwd": row["cwd"], "folder": folder_path(db, row["folder_id"])}
            if pending_note(row):
                out["cwd_note"] = pending_note(row)
            return out
        result = set_cwd(db, record["id"], args.directory, by="berth cwd")
        row = db.require_berth(record["id"])
        out = {"id": row["id"], "title": row["title"], "cwd": row["cwd"], "cwd_was": result.get("cwd_was", row["cwd"]),
               "folder": folder_path(db, row["folder_id"]), "cwd_note": pending_note(row)}
        if args.cycle and row["state"] == "live":
            from berth.ops import cycle_berth

            if caller and (row["cmux_workspace_id"] or "").lower() == caller.lower():
                from berth.deferred import queue

                queue(db, row["id"], "cycle", idle=True, delay_seconds=3,
                      requested_by=_requester(db, caller, "berth cwd --cycle"))
                out["cycle"] = "queued: Berth's watcher cycles this session once its turn ends"
            else:
                cycle_berth(db, cmux, row["id"], confirm=False, focus=False,
                            hook=hook_for(hooks, row["cmux_workspace_id"]), caller_workspace_id=caller, pid_alive_fn=pid_alive)
                out["cycle"] = "cycled: open in the new directory now"
                out["cwd_note"] = pending_note(db.require_berth(row["id"]))
        return out
    if args.command == "play":
        record = resolve_query(db, args.query)
        focus = args.focus == "true" and not args.background
        return play_berth(
            db,
            cmux,
            record["id"],
            focus=focus,
            run_command=args.run_command,
            sessions_root=args.sessions,
        )
    if args.command == "pause":
        if args.folder:
            return pause_folder(
                db,
                cmux,
                args.folder,
                force=args.force,
                confirm=args.confirm,
                caller_workspace_id=caller,
                hooks=hooks,
            )
        if args.query:
            record = resolve_query(db, args.query)
        else:
            record = resolve_workspace_env(db, caller)
        hook = hook_for(hooks, record.get("cmux_workspace_id"))
        return pause_berth(
            db,
            cmux,
            record["id"],
            force=args.force,
            confirm=args.confirm,
            hook=hook,
            caller_workspace_id=caller,
            pid_alive_fn=pid_alive,
        )
    if args.command == "focus":
        record = resolve_query(db, args.query)
        return focus_berth(db, cmux, record["id"])
    if args.command == "new":
        return new_berth(
            db,
            cmux,
            folder=args.folder,
            title=args.title,
            cwd=args.cwd or None,
            focus=args.focus == "true",
            sessions_root=None,
        )
    if args.command == "mkdir":
        return mkdir_path(db, args.path)
    if args.command == "move":
        record = resolve_query(db, args.query)
        return move_berth(db, record["id"], args.folder)
    if args.command == "note":
        record = resolve_query(db, args.query)
        return set_note(db, record["id"], " ".join(args.text))
    if args.command in ("urgent", "follow-up"):
        from berth.attention import FOLLOW_UP, URGENT, lower_flag, raise_flag

        level = URGENT if args.command == "urgent" else FOLLOW_UP
        where = args.query.strip().lower()
        if where in ("me", "selected"):
            if where == "selected":
                # The workspace selected in cmux: what Dominik is looking at when he presses ⌘⇧X (berth/keys.py).
                chosen = next((item.id for item in cmux.list_workspaces() if item.selected), None)
                if chosen is None:
                    raise BerthError("NOT_FOUND", "cmux has no selected workspace", {})
            else:
                chosen = caller
            mine = [row for row in db.conn.execute("SELECT * FROM berth WHERE state = 'live'")
                    if chosen and (row["cmux_workspace_id"] or "").lower() == chosen.lower()]
            if not mine:
                raise BerthError("NOT_FOUND", f"{where} is not a Berth session", {"workspace": chosen})
            berth_id = mine[0]["id"]
        else:
            berth_id = resolve_query(db, args.query)["id"]
        if getattr(args, "dismiss", False):
            from berth.attention import dismiss_flag

            lowered = dismiss_flag(db, berth_id, by=caller or "cli")
        elif args.off:
            lowered = lower_flag(db, berth_id, level=level)
        else:
            raise_flag(db, berth_id, " ".join(args.why), by=caller or "cli", cmux=cmux, level=level)
            lowered = None
        row = db.require_berth(berth_id)
        out = {"id": row["id"], "title": row["title"], "flag": row["attention"], "why": row["attention_why"]}
        if lowered is not None:
            out["lowered"] = lowered
        elif level == FOLLOW_UP and row["attention"] == URGENT:
            out["note"] = "it is urgent: follow-up does not replace that"
        return out
    if args.command in ("pin", "unpin"):
        from berth.pins import set_pinned

        record = resolve_query(db, args.query)
        return set_pinned(db, record["id"], args.command == "pin")
    if args.command == "excerpt":
        record = resolve_query(db, args.query)
        return excerpt_of(db, record["id"])
    if args.command == "reconcile":
        return run_reconcile(db, cmux, adopt_missing=not args.no_adopt)
    if args.command == "log":
        record = resolve_query(db, args.query)
        return log_of(db, record["id"])
    if args.command == "rmdir":
        return rmdir_folder(db, args.path)
    if args.command == "archive":
        return archive_folder(db, args.path)
    if args.command == "move-folder":
        from berth.ops import move_folder_path

        return move_folder_path(db, args.path, args.into)
    if args.command == "rename-folder":
        from berth.ops import rename_folder_path

        return rename_folder_path(db, args.path, args.name)
    if args.command == "rename":
        record = resolve_query(db, args.query)
        return rename_berth(db, cmux, record["id"], " ".join(args.title))
    if args.command == "recipe":
        record = resolve_query(db, args.query)
        return set_session(db, record["id"], args.session)
    if args.command == "delete":
        record = resolve_query(db, args.query)
        return delete_berth(db, cmux, record["id"], caller_workspace_id=caller, force=args.force)
    if args.command == "sleep":
        from berth.observer import is_screen
        from berth.ops import sleep_berth

        if args.all:
            rows = [row for row in db.conn.execute("SELECT * FROM berth WHERE state = 'live'") if not is_screen(row)]
        elif args.query:
            rows = [db.require_berth(resolve_query(db, args.query)["id"])]
        else:
            raise BerthError("NOT_FOUND", "name a session, or pass --all", {})
        slept, left = [], []
        for row in rows:
            if caller and (row["cmux_workspace_id"] or "").lower() == caller.lower():
                left.append({"id": row["id"], "title": row["title"], "code": "PROTECTED"})
                continue
            try:
                sleep_berth(db, cmux, row["id"], pid_alive_fn=pid_alive, caller_workspace_id=caller,
                            hook=hook_for(hooks, row["cmux_workspace_id"]), confirm=True)
                slept.append({"id": row["id"], "title": row["title"]})
            except BerthError as exc:
                left.append({"id": row["id"], "title": row["title"], "code": exc.code})
        return {"slept": slept, "left": left}
    if args.command == "idle":
        from berth.ops import idle_berth

        row = db.require_berth(resolve_query(db, args.query)["id"])
        if caller and (row["cmux_workspace_id"] or "").lower() == caller.lower():
            raise BerthError("PROTECTED", f"{row['title']} is the workspace running this command", {"id": row["id"]})
        return idle_berth(db, cmux, row["id"], pid_alive_fn=pid_alive, caller_workspace_id=caller,
                          hook=hook_for(hooks, row["cmux_workspace_id"]), confirm=True)
    if args.command == "wake":
        from berth.ops import wake_all

        if args.all:
            return {"results": wake_all(db, cmux)}
        if not args.query:
            raise BerthError("NOT_FOUND", "name a session, or pass --all", {})
        return {"results": wake_all(db, cmux, ids=[resolve_query(db, args.query)["id"]])}
    if args.command == "trash":
        from berth import trash

        if args.action == "list":
            return {"trash": trash.items(db)}
        item = trash.find(db, args.query)
        if args.action == "restore":
            return trash.restore(db, item["id"])
        return trash.forget(db, item["id"])
    if args.command == "colors":
        return _colors(db)
    if args.command == "offer":
        from berth.offer import OFFER_PORT, plan, serve_forever, snapshot

        if args.serve:
            serve_forever(db.path, args.port or OFFER_PORT)
        document = snapshot(db)
        if as_json_offer(args):
            return document
        return plan(db, document, take_all=True)
    if args.command == "tailscale":
        from berth.offer import publish

        return publish(db.path)
    if args.command == "peers":
        from berth.offer import peers

        return {"peers": peers()}
    if args.command == "preview":
        from berth.offer import fetch_snapshot, plan

        incoming = fetch_snapshot(args.source)
        return plan(db, incoming, folders=args.folder or None, take_all=not args.folder)
    if args.command == "merge":
        from berth.offer import fetch_snapshot, merge

        incoming = fetch_snapshot(args.source)
        return merge(db, incoming, folders=args.folder or None, take_all=args.all)
    raise BerthError("NOT_FOUND", f"unknown command {args.command}", {})


def as_json_offer(args) -> bool:
    return bool(getattr(args, "json", False))


def _colors(db: Catalog) -> dict:
    """Read-only. The colors the watcher keeps, and the distinguishability report."""
    from berth.model import INBOX_NAME
    from berth.ops import folder_path
    from berth.palette import compute, report

    folders = db.all_folders()
    swatches = compute(folders, inbox_name=INBOX_NAME)
    rows = []
    for folder in folders:
        swatch = swatches.get(folder["id"])
        rows.append(
            {
                "path": folder_path(db, folder["id"]),
                "color": swatch.hex if swatch else None,
                "depth": swatch.depth if swatch else 0,
                "hue": round(swatch.h, 1) if swatch else None,
                "lightness": round(swatch.L, 3) if swatch else None,
            }
        )
    rows.sort(key=lambda item: item["path"].casefold())
    return {"folders": rows, "report": report(folders, swatches, inbox_name=INBOX_NAME)}


def _human_colors(document: dict) -> str:
    lines = []
    for item in document["folders"]:
        indent = "  " * item["depth"]
        name = item["path"].rsplit("/", 1)[-1]
        lines.append(f"{item['color'] or 'none   '}  {indent}{name}")
    report = document["report"]
    lines.append("")
    lines.append(f"distinct at a glance means OKLab ΔE >= {report['distinct']}")
    for key in ("adjacent_roots", "parent_child", "adjacent_siblings", "closest_siblings", "any_two"):
        item = report[key]
        low = "-" if item["min"] is None else f"{item['min']:.3f}"
        flag = f"  {len(item['below'])} below" if item["below"] else ""
        lines.append(f"  {key.replace('_', ' '):<18} min {low}{flag}")
    return "\n".join(lines)


def _check(db: Catalog, cmux: Cmux) -> dict:
    """Read-only. Prints the invariant violations against a fresh workspace list."""
    from berth.invariant import run

    return run(db, cmux)


def _human_check(document: dict) -> str:
    if document["cmux"] == "down":
        return "cmux_down: nothing to check"
    lines = [
        f"{'ok' if document['ok'] else 'DRIFT'}  {document.get('workspaces')} workspaces  {document.get('live')} live rows"
    ]
    for item in document["violations"]:
        lines.append(f"  {item['rule']:<14} {item['message']}")
    return "\n".join(lines)


def _status(db: Catalog, cmux: Cmux) -> dict:
    workspaces = None
    if cmux.socket_up():
        try:
            workspaces = len(cmux.list_workspaces())
        except BerthError:
            workspaces = None
    tallies = counts(db)
    document = {
        "live": tallies["live"],
        "parked": tallies["parked"],
        "playing": tallies["playing"],
        "pausing": tallies["pausing"],
        "workspaces": workspaces,
        "watcher": watcher_state(db.path.parent),
        "keys": __import__("berth.keys", fromlist=["read_state"]).read_state(db.path.parent),  # ⌘⇧X (berth/keys.py)
        "health": __import__("berth.health", fromlist=["report"]).report(db, db.path.parent),
        # Memory as macOS counts it, and what the sessions demand (berth/memory.py, every 30 s by the watcher).
        # Before 2026-11-06 this was child_rss, cmux's summed RSS, which counted shared code once per process.
        "memory": __import__("berth.memory", fromlist=["summary"]).summary(
            __import__("berth.memory", fromlist=["stored"]).stored(db.path.parent)),
        "cmux": "up" if cmux.socket_up() else "down",
        # The shared MCP server (com.bach.berth.mcp, berth/mcp_http.py): does /berth answer a ping.
        "mcp_http": __import__("berth.mcp_http", fromlist=["check"]).check(timeout=0.5).get("berth"),
    }
    return document


def _human_for(command: str):
    if command == "colors":
        return _human_colors
    if command == "trash":
        return _human_trash
    if command == "status":
        return human_status
    if command == "tree":
        return human_tree
    if command == "excerpt":
        return lambda document: document.get("excerpt") or ""
    if command == "log":
        return _human_log
    if command in {"offer", "preview", "merge"}:
        from berth.offer import format_plan

        return format_plan
    if command == "peers":
        return lambda document: "\n".join(
            f"{row['name']}  {row['address']}  {'online' if row['online'] else 'offline'}"
            for row in document.get("peers", [])
        )
    if command == "memory":
        def human(document):
            from berth.memory import gb, mac_line, sessions_line

            mac, total = document.get("mac") or {}, document.get("sessions") or {}
            lines = [f"{mac_line(mac)}  (used = app {gb(mac.get('app_mb'))} + wired {gb(mac.get('wired_mb'))} + "
                     f"compressed {gb(mac.get('compressed_mb'))}, holding {gb(mac.get('compressor_holds_mb'))}; "
                     f"cached files {gb(mac.get('cached_mb'))})",
                     f"{sessions_line(total)} footprint over {document.get('count')} sessions: Claude "
                     f"{gb(total.get('claude_mb'))} · MCP {gb(total.get('mcp_mb'))} · hooks {gb(total.get('hooks_mb'))}"
                     f" · shell {gb(total.get('shell_mb'))}"]
            for item in document.get("by_session", [])[:15]:
                lines.append(f"  {item['footprint_mb']:>8.0f} MB  {item['title'][:40]:<40}  claude {item['claude_mb']:.0f}"
                             f" · mcp {item['mcp_mb']:.0f} · hooks {item['hooks_mb']:.0f} · shell {item['shell_mb']:.0f}"
                             f"   resident {item['resident_mb']:.0f} (shared included)")
            for item in document.get("detached", []):
                lines.append(f"  {item['footprint_mb']:>8.0f} MB  {item['who']} (pid {item['pid']})")
            return "\n".join(lines)
        return human
    if command == "tailscale":
        return lambda document: document.get("url") or document.get("serve") or ""
    return _human_record


def _human_trash(document) -> str:
    if "trash" not in document:
        return _human_record(document)
    rows = document["trash"]
    if not rows:
        return "the trash is empty"
    from berth.render import age_label

    return "\n".join(
        f"{item['id'][:8]}  {item['title']}  deleted {age_label(item['deleted_at'])} ago  "
        f"{item['days_left']}d left  from {item['folder'] or 'Inbox'}"
        for item in rows
    )


def _human_record(document) -> str:
    if isinstance(document, dict) and "title" in document and "state" in document:
        path = document.get("folder_path") or ""
        prefix = f"{path}  " if path else ""
        return f"{prefix}{document['title']}  {document['state']}  {document['kind']}  {document['id']}"
    return json.dumps(document, indent=2, sort_keys=True)


def _human_log(document: dict) -> str:
    lines = []
    for event in document.get("events", []):
        lines.append(f"{event['at']}  {event['kind']}")
    return "\n".join(lines)


def _human_install(document: dict) -> str:
    lines = [document["plist"]]
    if not document["bootstrapped"]:
        lines.append(document["bootstrap_error"])
    return "\n".join(lines)


def _emit(document, as_json: bool, human) -> None:
    if as_json:
        _write_json(document)
        return
    text = human(document)
    print(text)


def _write_json(document) -> None:
    print(json.dumps(document, indent=2, sort_keys=True, default=str))
