"""Records the engine passes between cmux, the hook files, and the catalog."""

from __future__ import annotations

from dataclasses import dataclass, field


KINDS = ("grok", "other-agent", "shell", "process")
STATES = ("parked", "playing", "live", "pausing")
INBOX_NAME = "Inbox"

# A parked row is asleep when it was closed to come back soon: put to sleep
# (S, Z, the MCP sleep tool), or closed by a cmux quit, which wakes it again
# by itself. Any other parked row is paused: put away until someone wants it.
SLEEP_REASONS = frozenset({"sleep", "cmux_quit"})


# Idle (2026-11-05, Dominik): closed to free its memory, and coming back soon; between live and sleeping. It shows
# with the live sessions (the screen's "live + idle", every agent's sessions), where sleeping is kept out of the way.
IDLE_REASON = "idle"


def is_sleeping(state: str | None, park_reason: str | None) -> bool:
    return state == "parked" and (park_reason or "") in SLEEP_REASONS


def is_idle(state: str | None, park_reason: str | None) -> bool:
    return state == "parked" and (park_reason or "") == IDLE_REASON


def closed_state(state: str | None, park_reason: str | None) -> str:
    """A row's state as Berth names it: live, idle, sleeping, parked (paused), background, playing, or pausing."""
    if state != "parked":
        return state or ""
    if park_reason == "background":
        return "background"
    if is_idle(state, park_reason):
        return "idle"
    return "sleeping" if is_sleeping(state, park_reason) else "parked"

# Launchers that mean "an agent", so adopt waits for a hook record.
AGENT_LAUNCHERS = frozenset(
    {
        "grok",
        "claude",
        "codex",
        "pi",
        "opencode",
        "gemini",
        "amp",
        "cursor",
        "cursor-agent",
        "agy",
        "antigravity",
        "aider",
    }
)
SHELL_LAUNCHERS = frozenset({"zsh", "bash", "sh", "fish", "dash", "login", "tmux", "nu"})


@dataclass
class HookRecord:
    agent_name: str
    session_id: str | None
    workspace_id: str
    surface_id: str | None
    pid: int | None
    cwd: str | None
    runtime_status: str | None
    last_body: str | None
    executable: str | None
    arguments: list[str] = field(default_factory=list)
    launcher: str | None = None
    updated_at: float | None = None
    hook_event: str | None = None
    pending_background: bool = False


@dataclass
class Workspace:
    id: str
    title: str
    cwd: str
    ref: str | None = None
    index: int | None = None
    selected: bool = False
    window_id: str | None = None
    custom: bool = False
    color: str | None = None
    submitted_at: str | None = None
    submitted_message: str | None = None  # cmux's latest submitted text (another agent's message lands here too)
    pinned: bool = False


@dataclass
class Surface:
    ref: str | None = None
    title: str | None = None
    type: str | None = None
    initial_command: str | None = None
    cwd: str | None = None
    resume_kind: str | None = None
    resume_command: str | None = None
    launch_arguments: list[str] = field(default_factory=list)
    launch_executable: str | None = None
    launcher: str | None = None


@dataclass
class Group:
    name: str
    ref: str | None
    members: list[str]


@dataclass
class Folder:
    id: str
    parent_id: str | None
    name: str
    sort: int
    profile_id: str | None
    default_cwd: str | None
    note: str
    archived: int
