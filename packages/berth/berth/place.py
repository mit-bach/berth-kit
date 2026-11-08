"""Kind, folder, busy, and resume argv.

Folder rule, applied again on every adopt:

1. A cmux group name wins when a root folder has that exact name.
   Adopt creates that root folder when the group exists and the folder does not.
2. Otherwise the longest folder ``default_cwd`` that prefixes the workspace cwd wins.
3. Otherwise the berth goes to Inbox.

Profiles are not applied. A folder may store ``profile_id``; play ignores it.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

from berth.model import (
    AGENT_LAUNCHERS,
    SHELL_LAUNCHERS,
    Folder,
    Group,
    HookRecord,
    Surface,
    Workspace,
)

_VERSION = re.compile(r"(\d+\.\d+\.\d+)")
_WAITING = "waiting for your next prompt"
_FLAGS_WITH_VALUES = {
    "--reasoning-effort", "--effort", "--model", "-m", "--cwd", "--workspace", "--dir",
    # Grok Build 1.0 flags that take a value (grok --help).
    "--permission-mode", "--agent", "--agents", "--add-dir", "--allow", "--deny",
    "--allowedTools", "--allowed-tools", "--disallowedTools", "--disallowed-tools",
    "--rules", "--sandbox", "--max-turns", "--leader-socket", "--output-format",
    "--json-schema", "--debug-file",
}
# One launch only: a fork, a preset session id, a single headless prompt.
# A resume recipe that kept them would fork again or run the prompt again.
_ONE_SHOT_FLAGS = {"--fork-session"}
_ONE_SHOT_WITH_VALUES = {"-s", "--session-id", "-p", "--single", "--prompt-file", "--prompt-json"}
# Claude writes agentLifecycle and hookEventName. Stop and SessionStart are the
# prompt. PreToolUse is work in progress. A lastBody on Stop is the answer.
_QUIET_STATUS = {"idle", "needsinput", "waiting"}
_QUIET_EVENTS = {"stop", "sessionend", "sessionstart", "setup"}
_BUSY_EVENTS = {"pretooluse", "posttooluse", "permissionrequest", "userpromptsubmit"}


def normalize_cwd(path: str | None) -> str:
    if not path:
        return "/"
    text = os.path.expanduser(path)
    if text != "/":
        text = text.rstrip("/")
    return text or "/"


def cwd_matches(prefix: str, cwd: str) -> bool:
    left = normalize_cwd(prefix)
    right = normalize_cwd(cwd)
    return right == left or right.startswith(left + "/")


def choose_folder_id(
    folders: list[Folder],
    *,
    cwd: str,
    group_name: str | None,
) -> str | None:
    """Return the folder id the rule selects, or None when nothing matches."""
    if group_name:
        for folder in folders:
            if folder.parent_id is None and folder.name == group_name and not folder.archived:
                return folder.id
    best_id: str | None = None
    best_len = -1
    for folder in folders:
        if folder.archived or not folder.default_cwd:
            continue
        if cwd_matches(folder.default_cwd, cwd) and len(normalize_cwd(folder.default_cwd)) > best_len:
            best_id = folder.id
            best_len = len(normalize_cwd(folder.default_cwd))
    return best_id


def group_for_workspace(workspace: Workspace, groups: list[Group]) -> str | None:
    keys = set()
    if workspace.id:
        keys.add(workspace.id.lower())
    if workspace.ref:
        keys.add(workspace.ref.lower())
    for group in groups:
        for member in group.members:
            if member.lower() in keys:
                return group.name
    return None


def version_from_executable(path: str | None) -> str | None:
    if not path:
        return None
    match = _VERSION.search(path)
    if match:
        return match.group(1)
    try:
        resolved = os.path.realpath(path)
    except OSError:
        return None
    match = _VERSION.search(resolved)
    return match.group(1) if match else None


def pid_alive(pid: int | None) -> bool:
    """True when the process exists.

    Signal 0, not ``ps`` (2026-11-03): the watcher asks this for every hook
    record on every tick, and a ``ps`` each time made it start dozens of
    processes a second (berth/proc.py). Signal 0 only checks; a process of
    another user answers "not permitted", which still means it exists.
    """
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, OverflowError):
        return False
    return True


def _hook_token(value: str | None) -> str:
    return (value or "").strip().lower().replace("_", "").replace("-", "")


def agent_is_busy(kind: str, hook: HookRecord | None, *, alive: bool) -> bool:
    """Quiet when the agent is at the prompt, idle, or the PID is dead.

    Grok uses ``runtimeStatus``. Claude uses ``agentLifecycle`` and
    ``hookEventName``. A Claude Stop stores the answer in ``lastBody``. That
    text is not a tool call. ``PreToolUse`` while the lifecycle is running is
    busy. ``SessionStart`` is the prompt, so it is quiet.
    """
    if kind not in ("grok", "other-agent"):
        return False
    if hook is None or not alive:
        return False
    if hook.pending_background:
        return True
    body = (hook.last_body or "").strip()
    if _WAITING in body.lower():
        return False
    status = _hook_token(hook.runtime_status)
    event = _hook_token(hook.hook_event)
    if status in _QUIET_STATUS:
        return False
    if event in _QUIET_EVENTS:
        return False
    if status == "running":
        return True
    if event in _BUSY_EVENTS:
        return True
    if body and kind != "other-agent":
        return True
    return False


def _flag_args(arguments: list[str], *, drop: set[str]) -> list[str]:
    cleaned: list[str] = []
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token in drop:
            index += 1
            continue
        if token in ("-r", "--resume") or token in _ONE_SHOT_WITH_VALUES:
            if index + 1 < len(arguments) and not arguments[index + 1].startswith("-"):
                index += 2
            else:
                index += 1
            continue
        if token in _ONE_SHOT_FLAGS:
            index += 1
            continue
        if token.startswith("-"):
            cleaned.append(token)
            if token in _FLAGS_WITH_VALUES and index + 1 < len(arguments):
                cleaned.append(arguments[index + 1])
                index += 2
                continue
        index += 1
    return cleaned


def grok_resume_argv(hook: HookRecord) -> list[str]:
    """`grok -r <session id>` plus the flags the live process was using.

    The session id comes from the hook record. Positional tokens in the
    captured argument list are not copied: on this machine that list mixes
    the launcher name, flags, and an opaque token that is not the session id.
    """
    drop = {hook.launcher or "grok", hook.executable or "", os.path.basename(hook.executable or "")}
    drop.discard("")
    if hook.session_id:
        drop.add(hook.session_id)
    flags = _flag_args(hook.arguments, drop=drop)
    session = hook.session_id or ""
    return ["grok", *flags, "-r", session]


def _grok_program(argv: list[str]) -> str:
    return argv[0] if argv and launcher_name(argv[0]) == "grok" else "grok"


# Flags go before the session id. cmux's hook keeps a Grok launch's arguments
# only up to the first id it rejects, so `grok -r <id> --always-approve` is
# captured as plain `grok`, and the next pause would store a recipe without
# the flags (measured on cmux 0.65.2, 2026-10-23).


def grok_session_argv(argv: list[str], session_id: str) -> list[str]:
    """``grok <flags> -r <session>``: the recipe's flags, without one-shot flags."""
    return [_grok_program(argv), *_flag_args(list(argv[1:]), drop=set()), "-r", session_id]


def grok_fork_argv(argv: list[str], parent: str, session_id: str) -> list[str]:
    """``grok <flags> -r <parent> --fork-session -s <session>``.

    Grok copies the parent's conversation into ``session_id``, which Berth
    chose, so the new row knows its session before the pane opens.
    """
    flags = _flag_args(list(argv[1:]), drop=set())
    return [_grok_program(argv), *flags, "-r", parent, "--fork-session", "-s", session_id]


def grok_flags(argv: list[str]) -> list[str]:
    return _flag_args(list(argv[1:]), drop=set())


def other_agent_program(
    argv: list[str],
    *,
    executable: str | None = None,
    launcher: str | None = None,
    agent_name: str | None = None,
) -> str:
    """The agent binary. An absolute path wins over a bare launcher name."""
    if executable and os.path.isabs(executable):
        return executable
    if argv and os.path.isabs(argv[0]):
        return argv[0]
    if launcher:
        return launcher
    if argv and argv[0] and not argv[0].startswith("-"):
        return argv[0]
    if agent_name:
        return agent_name
    return "agent"


def resume_argv(program: str, agent_name: str | None, session_id: str) -> list[str]:
    """The agent's own resume command. Nothing else is copied onto it.

    Claude keeps ``-r``. Codex uses ``resume``. Antigravity uses
    ``--conversation``. Cursor uses ``--resume``.
    """
    name = (agent_name or launcher_name(program) or "").casefold()
    if name == "codex":
        return [program, "resume", session_id]
    if name in {"antigravity", "agy"}:
        return [program, "--conversation", session_id]
    if name in {"cursor", "cursor-agent"}:
        return [program, "--resume", session_id]
    return [program, "-r", session_id]


def other_agent_resume_argv(hook: HookRecord) -> list[str]:
    """The binary and the session id, in that agent's resume form.

    The session file keeps the model, effort, name, and permission mode.
    Copying those flags into the terminal command drops their values.
    """
    program = other_agent_program(
        hook.arguments,
        executable=hook.executable,
        launcher=hook.launcher or os.path.basename(hook.executable or "") or None,
        agent_name=hook.agent_name,
    )
    if hook.session_id:
        return resume_argv(program, hook.agent_name, hook.session_id)
    return [program]


def agent_from_process(comm: str, command: str = "") -> str | None:
    """Canonical agent name for a foreground process, or none.

    ``agy`` is Antigravity. ``agent`` is Cursor only when the command line
    names cursor-agent. A bare ``node`` is not an agent.
    """
    name = (comm or "").strip().casefold()
    if name == "codex":
        return "codex"
    if name in {"agy", "antigravity"}:
        return "antigravity"
    if name == "cursor-agent":
        return "cursor"
    text = command.casefold()
    if "cursor-agent" in text or "/bin/agent" in text or text.startswith("agent "):
        return "cursor"
    return None


def agents_in_top(
    text: str,
    workspaces: list[Workspace],
    command_fn=None,
) -> dict[str, tuple[str, int, str]]:
    """Map a workspace id to ``(agent, pid, comm)`` from ``cmux top`` TSV.

    The chosen process is the known agent on that workspace. Login, zsh, and
    webkit are ignored. ``node`` counts only when its command line names
    cursor-agent.
    """
    by_ref = {item.ref: item.id for item in workspaces if item.ref}
    link: dict[str, str] = {}
    processes: list[tuple[str, int, str]] = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        kind, ident, belongs, comm = parts[3], parts[4], parts[5], parts[6]
        if kind in {"workspace", "pane", "surface", "process"}:
            link[ident] = belongs
        if kind != "process":
            continue
        try:
            pid = int(ident)
        except ValueError:
            continue
        processes.append((belongs, pid, comm))

    def surface_of(start: str) -> str:
        current = start
        seen: set[str] = set()
        while current and current not in seen:
            if current.startswith("surface:"):
                return current
            seen.add(current)
            current = link.get(current, "")
        return ""

    found: dict[str, tuple[str, int, str]] = {}
    for belongs, pid, comm in processes:
        surface = surface_of(belongs)
        if not surface:
            continue
        pane = link.get(surface, "")
        workspace_id = by_ref.get(link.get(pane, ""))
        if not workspace_id or workspace_id in found:
            continue
        command = ""
        if command_fn is not None and comm.casefold() in {"node", "agent", "python", "python3"}:
            command = command_fn(pid) or ""
        agent = agent_from_process(comm, command)
        if agent is None:
            continue
        found[workspace_id.lower()] = (agent, pid, comm)
    return found


CMUX_CLAUDE_WRAPPER = "/Applications/cmux.app/Contents/Resources/bin/cmux-claude-wrapper"


def other_agent_exec_text(argv: list[str]) -> str:
    """`exec <binary> -r <id>`. No model, effort, name, or permission flags.

    Claude goes through cmux's wrapper when cmux has one, as a `claude` typed in a cmux pane does.
    Without it the session misses cmux's hooks: agent messages (`cmux agent message`) stay queued
    and notifications never fire. The wrapper finds the real claude on PATH and passes -r through.
    """
    if not argv:
        return ""
    argv = list(argv)
    if (launcher_name(argv[0]) == "claude" and os.path.exists(CMUX_CLAUDE_WRAPPER)
            and not os.environ.get("BERTH_NO_CMUX_WRAPPER")):
        argv[0] = CMUX_CLAUDE_WRAPPER
    return "exec " + shlex.join(argv)


def is_berth_command(parts: list[str]) -> bool:
    """True when the surface command is the berth binary, not a shell or Grok."""
    if not parts:
        return False
    if launcher_name(parts[0]) == "berth":
        return True
    if launcher_name(parts[0]).startswith("python") and len(parts) >= 3:
        return parts[1] == "-m" and parts[2] == "berth"
    return False


def launcher_name(token: str | None) -> str:
    if not token:
        return ""
    return os.path.basename(token).lower()


def split_command(command: str) -> list[str]:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = []
    return parts


def command_has_shell_syntax(command: str) -> bool:
    return any(mark in command for mark in (";", "&&", "||", "|", "\n", "`", "$(", ">", "<"))


def recipe_from_command(command: str | None) -> tuple[str, list[str], str]:
    """Return kind, argv, display for a surface that has no agent hook.

    A shell launcher, or no command at all, is kind shell. The berth binary
    is kind berth. Anything else is kind process. Shell syntax is stored as
    `zsh -lc` so play can hand the same text back to `cmux workspace create --command`.
    """
    text = (command or "").strip()
    if not text:
        return "shell", [], "shell"
    parts = split_command(text)
    if is_berth_command(parts):
        return "berth", parts, shlex.join(parts)
    first = launcher_name(parts[0]) if parts else ""
    if first in SHELL_LAUNCHERS and not command_has_shell_syntax(text):
        return "shell", parts or [], "shell"
    if first in AGENT_LAUNCHERS:
        # No hook record. Do not invent a session id from the command line.
        return "shell", [], "shell"
    if command_has_shell_syntax(text) or not parts:
        shell = os.environ.get("SHELL") or "/bin/zsh"
        return "process", [shell, "-lc", text], text
    return "process", parts, shlex.join(parts)


def surface_command(surfaces: list[Surface]) -> str | None:
    for surface in surfaces:
        if surface.initial_command and surface.initial_command.strip():
            return surface.initial_command.strip()
    for surface in surfaces:
        if surface.resume_command and surface.resume_command.strip():
            return surface.resume_command.strip()
    return None


def surface_looks_like_agent(surfaces: list[Surface]) -> bool:
    for surface in surfaces:
        kind = (surface.resume_kind or "").lower()
        launcher = launcher_name(surface.launcher or surface.launch_executable or "")
        if kind in AGENT_LAUNCHERS or launcher in AGENT_LAUNCHERS:
            return True
        command = surface.initial_command or surface.resume_command or ""
        parts = split_command(command)
        if parts and launcher_name(parts[0]) in AGENT_LAUNCHERS:
            return True
    return False


# The login shell that cmux types a command into does not have `grok` on PATH.
# This is the binary that answers on this machine.
GROK_BINARY = Path.home() / ".grok" / "bin" / "grok"


def grok_exec_text(argv: list[str]) -> str:
    """`exec <absolute grok> -r <id>` plus the flags already on the recipe.

    `cmux workspace create --command` types into a login shell. `exec`
    replaces that shell, so the foreground process is grok.
    """
    parts = list(argv) if argv else ["grok"]
    if launcher_name(parts[0]) == "grok" and GROK_BINARY.is_file():
        parts[0] = str(GROK_BINARY)
    return "exec " + shlex.join(parts)


def command_text(argv: list[str], display: str) -> str | None:
    """Text for `cmux workspace create --command`. None opens a shell."""
    if not argv:
        return None
    if len(argv) >= 3 and argv[1] in {"-lc", "-c"} and launcher_name(argv[0]) in SHELL_LAUNCHERS | {""}:
        return argv[2]
    if display and display != "shell" and command_has_shell_syntax(display):
        return display
    return shlex.join(argv)


def session_on_disk(root: Path, session_id: str) -> bool:
    """True when a directory or file under the sessions root is named the id.

    The layout on this machine is ``<encoded cwd>/<session id>/``. The walk
    does not read session files.
    """
    if not session_id or not root.is_dir():
        return False
    root = root.resolve()
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        try:
            depth = len(current_path.relative_to(root).parts)
        except ValueError:
            dirnames.clear()
            continue
        if depth > 3:
            dirnames.clear()
            continue
        if session_id in dirnames or session_id in filenames:
            return True
        for name in filenames:
            if Path(name).stem == session_id:
                return True
    return False


def blank_grok_allowed(kind: str, session_id: str | None, argv: list[str], attachments: dict) -> bool:
    """A new berth may start `grok` with no session. A resume may not."""
    if kind != "grok":
        return False
    if session_id:
        return False
    if attachments.get("blank") is True:
        return True
    return "-r" not in argv and "--resume" not in argv
