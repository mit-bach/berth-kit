"""Memory, as macOS counts it and as the sessions demand it (2026-11-06, W1 of the memory initiative).

The definitions are the Performance Agent's, and only theirs:
``~/STUDIO/Agents/Performance/Performance-Agent/jobs/2026-11-06-1438-mcp-access-steward-memory-initiative/
W1-DEFINITIONS.md``. ``pstw`` points at the same file, so the two can't drift. Plan: ``docs/MEMORY-ANALYTICS-PLAN.md``.

- **The Mac:** Activity Monitor's terms, from ``host_statistics64``. Memory Used is App Memory (anonymous pages
  minus purgeable), plus Wired, plus Compressed (the RAM the compressor occupies). Cached Files (file-backed plus
  purgeable) are separate. Also physical, swap, pressure, and what the compressor holds at its uncompressed size.
- **Per process:** footprint (``ri_phys_footprint``, ``top``'s MEM) is the one measure that may be summed.
  Resident (RSS) includes shared pages: shown beside it, labelled, never summed.
- **Per session:** its Claude process and everything under it, split four ways by program path, never arguments:
  claude (with its caffeinate), hooks (cmux's hook programs, which stay resident), shell (under /bin/zsh, bash
  or sh), and MCP (anything else), exactly as ``pstw sessions`` splits it. A shell started
  detached (parent launchd) leaves every session; it's a line of its own, named from the heavy-run slot's log when
  it ran there.

Before 2026-11-06, Berth showed "children 39.4 GB": cmux's summed RSS, which counts shared code once per process
and leaves out what is compressed or swapped. It's gone.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
from pathlib import Path

MB = 1024 * 1024
SHELLS = frozenset({"zsh", "bash", "sh", "dash", "fish"})
HOOK_SUFFIX = "/Contents/Resources/bin/cmux"  # cmux's hook programs under claude (inbox-wait, spool-forwarder)
CLAUDE_HELPERS = frozenset({"/usr/bin/caffeinate"})  # Claude Code's own helper: part of Claude (W1-DEFINITIONS.md)
# A session's shells, exactly as ``pstw sessions`` (D40) has them, so the parts agree to the megabyte.
SESSION_SHELLS = frozenset({"/bin/zsh", "/bin/bash", "/bin/sh"})
PRESSURE = {1: "normal", 2: "warn", 4: "critical"}
HEAVY_RUNS = Path.home() / ".local" / "state" / "heavy-runs"

_libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
_libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)


# --- the Mac ------------------------------------------------------------------------------------------------

class _VMStats64(ctypes.Structure):
    _fields_ = [
        ("free_count", ctypes.c_uint32), ("active_count", ctypes.c_uint32), ("inactive_count", ctypes.c_uint32),
        ("wire_count", ctypes.c_uint32), ("zero_fill_count", ctypes.c_uint64), ("reactivations", ctypes.c_uint64),
        ("pageins", ctypes.c_uint64), ("pageouts", ctypes.c_uint64), ("faults", ctypes.c_uint64),
        ("cow_faults", ctypes.c_uint64), ("lookups", ctypes.c_uint64), ("hits", ctypes.c_uint64),
        ("purges", ctypes.c_uint64), ("purgeable_count", ctypes.c_uint32), ("speculative_count", ctypes.c_uint32),
        ("decompressions", ctypes.c_uint64), ("compressions", ctypes.c_uint64), ("swapins", ctypes.c_uint64),
        ("swapouts", ctypes.c_uint64), ("compressor_page_count", ctypes.c_uint32), ("throttled_count", ctypes.c_uint32),
        ("external_page_count", ctypes.c_uint32), ("internal_page_count", ctypes.c_uint32),
        ("total_uncompressed_pages_in_compressor", ctypes.c_uint64),
    ]


class _SwapUsage(ctypes.Structure):
    _fields_ = [("total", ctypes.c_uint64), ("avail", ctypes.c_uint64), ("used", ctypes.c_uint64),
                ("pagesize", ctypes.c_uint32), ("encrypted", ctypes.c_bool)]


def _sysctl(name: str, kind):
    value = kind()
    size = ctypes.c_size_t(ctypes.sizeof(value))
    if _libc.sysctlbyname(name.encode(), ctypes.byref(value), ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
        return None
    return value


def mac() -> dict:
    """The Mac's memory in Activity Monitor's terms (W1-DEFINITIONS.md §1), in MB."""
    stats = _VMStats64()
    count = ctypes.c_uint32(ctypes.sizeof(_VMStats64) // 4)
    host = _libc.mach_host_self()
    if _libc.host_statistics64(host, 4, ctypes.byref(stats), ctypes.byref(count)) != 0:  # HOST_VM_INFO64
        return {}
    page = (_sysctl("hw.pagesize", ctypes.c_int64) or ctypes.c_int64(16384)).value
    to_mb = lambda pages: round(pages * page / MB, 1)  # noqa: E731
    app = to_mb(stats.internal_page_count - stats.purgeable_count)
    wired, compressed = to_mb(stats.wire_count), to_mb(stats.compressor_page_count)
    out = {
        "app_mb": app, "wired_mb": wired, "compressed_mb": compressed,
        "used_mb": round(app + wired + compressed, 1),
        "cached_mb": to_mb(stats.external_page_count + stats.purgeable_count),
        "compressor_holds_mb": to_mb(stats.total_uncompressed_pages_in_compressor),
    }
    memsize = _sysctl("hw.memsize", ctypes.c_uint64)
    if memsize is not None:
        out["physical_mb"] = round(memsize.value / MB, 1)
    swap = _sysctl("vm.swapusage", _SwapUsage)
    if swap is not None:
        out["swap_used_mb"], out["swap_total_mb"] = round(swap.used / MB, 1), round(swap.total / MB, 1)
    level = _sysctl("kern.memorystatus_vm_pressure_level", ctypes.c_int)
    if level is not None:
        out["pressure"] = PRESSURE.get(level.value, str(level.value))
    return out


# --- processes ------------------------------------------------------------------------------------------------

class _RUsageV2(ctypes.Structure):
    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [(name, ctypes.c_uint64) for name in (
        "user_time", "system_time", "pkg_idle_wkups", "interrupt_wkups", "pageins", "wired_size", "resident_size",
        "phys_footprint", "proc_start_abstime", "proc_exit_abstime", "child_user_time", "child_system_time",
        "child_pkg_idle_wkups", "child_interrupt_wkups", "child_pageins", "child_elapsed_abstime",
        "diskio_bytesread", "diskio_byteswritten")]


def usage(pid: int) -> tuple[int, int] | None:
    """(footprint, resident) in bytes, or None when the kernel won't say (another user's process, or gone)."""
    info = _RUsageV2()
    if _libproc.proc_pid_rusage(int(pid), 2, ctypes.byref(info)) != 0:  # RUSAGE_INFO_V2
        return None
    return int(info.phys_footprint), int(info.resident_size)


def _pidpath(pid: int) -> str:
    buf = ctypes.create_string_buffer(4096)
    return buf.value.decode(errors="replace") if _libproc.proc_pidpath(int(pid), buf, 4096) > 0 else ""


def processes(run=subprocess.run) -> dict[int, dict]:
    """Every process: pid, parent, tty, and program path (never its arguments), from one ``ps``."""
    out = run(["/bin/ps", "-axo", "pid=,ppid=,tty=,comm="], capture_output=True, text=True, timeout=10).stdout
    table: dict[int, dict] = {}
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4 or not parts[0].isdigit():
            continue
        pid, ppid, tty, comm = int(parts[0]), int(parts[1]), parts[2], parts[3].strip()
        program = comm if comm.startswith("/") and os.path.isfile(comm) else (_pidpath(pid) or comm)
        table[pid] = {"pid": pid, "ppid": ppid, "tty": "" if tty == "??" else tty, "program": program}
    return table


def _children(table: dict[int, dict]) -> dict[int, list[int]]:
    kids: dict[int, list[int]] = {}
    for pid, row in table.items():
        kids.setdefault(row["ppid"], []).append(pid)
    return kids


def _tree(pid: int, kids: dict[int, list[int]]) -> list[int]:
    out, stack = [], [pid]
    while stack:
        current = stack.pop()
        out.append(current)
        stack.extend(kids.get(current, ()))
    return out


def _base(program: str) -> str:
    return os.path.basename(program or "")


def is_claude(program: str) -> bool:
    return _base(program) == "claude"


def part_of(main_program: str) -> str:
    """A Claude child's part, by its program and never its arguments, exactly as ``pstw sessions`` decides it
    (2026-11-06, the Performance Agent Architect, D40): caffeinate is Claude's, cmux's hook program is hooks,
    /bin/zsh, /bin/bash or /bin/sh is shell, and anything else is MCP."""
    if main_program in CLAUDE_HELPERS:
        return "claude"
    if main_program.endswith(HOOK_SUFFIX):
        return "hooks"
    if main_program in SESSION_SHELLS:
        return "shell"
    return "mcp"


def session_roots(table: dict[int, dict], ttys: dict[str, list[str]]) -> dict[str, int]:
    """Each workspace's Claude process: the one on its terminal's tty whose parent isn't a Claude."""
    by_tty: dict[str, list[int]] = {}
    for pid, row in table.items():
        if row["tty"] and is_claude(row["program"]):
            by_tty.setdefault(row["tty"], []).append(pid)
    roots = {}
    for workspace, names in ttys.items():
        for tty in names:
            found = [pid for pid in by_tty.get(tty.replace("/dev/", ""), [])
                     if not is_claude(table.get(table[pid]["ppid"], {}).get("program", ""))]
            if found:
                roots[workspace.lower()] = min(found)
                break
    return roots


def _sum(pids, sizes) -> tuple[int, int]:
    footprint = resident = 0
    for pid in pids:
        got = sizes.get(pid)
        if got:
            footprint += got[0]
            resident += got[1]
    return footprint, resident


def session(root: int, table: dict[int, dict], kids: dict[int, list[int]], sizes: dict) -> dict:
    """One session's tree, in MB: claude, mcp, hooks, shell, footprint, and resident (shared included)."""
    parts = {"claude_mb": 0.0, "mcp_mb": 0.0, "hooks_mb": 0.0, "shell_mb": 0.0}
    own = sizes.get(root) or (0, 0)
    parts["claude_mb"] = own[0] / MB
    resident = own[1]
    for child in kids.get(root, ()):
        footprint, res = _sum(_tree(child, kids), sizes)
        parts[part_of(table[child]["program"]) + "_mb"] += footprint / MB
        resident += res
    out = {key: round(value, 1) for key, value in parts.items()}
    out["footprint_mb"] = round(sum(parts.values()), 1)
    out["resident_mb"] = round(resident / MB, 1)  # shared pages included: never summed across sessions
    return out


def _heavy_runs() -> dict[int, str]:
    """Running heavy-run slot jobs: pid → who (the Container Agent's logs)."""
    running: dict[int, str] = {}
    try:
        files = sorted(HEAVY_RUNS.glob("*.jsonl"))
    except OSError:
        return running
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[-400:]
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            pid = event.get("pid")
            if not isinstance(pid, int):
                continue
            if event.get("event") == "heavy-run-start":
                running[pid] = str(event.get("who") or "a heavy run")
            elif event.get("event") == "heavy-run-end":
                running.pop(pid, None)
    return running


def detached(table: dict[int, dict], kids: dict[int, list[int]], sizes: dict, *, min_mb: float = 50.0) -> list[dict]:
    """Shells started detached (parent launchd): no session holds them (W1-DEFINITIONS.md §3, the known gap)."""
    slot = _heavy_runs()
    out = []
    for pid, row in table.items():
        if row["ppid"] != 1 or _base(row["program"]) not in SHELLS:
            continue
        tree = _tree(pid, kids)
        footprint, _ = _sum(tree, sizes)
        if footprint / MB < min_mb:
            continue
        who = next((slot[p] for p in tree if p in slot), None)
        out.append({"pid": pid, "footprint_mb": round(footprint / MB, 1), "who": who or "detached, no session"})
    return sorted(out, key=lambda item: -item["footprint_mb"])


def measure(ttys: dict[str, list[str]], *, run=subprocess.run, usage_fn=usage) -> dict:
    """Everything W1 shows, at one moment: the Mac, each workspace's session, the sessions' total, detached work."""
    table = processes(run)
    kids = _children(table)
    sizes = {pid: got for pid in table for got in [usage_fn(pid)] if got is not None}
    roots = session_roots(table, ttys)
    sessions = {workspace: session(root, table, kids, sizes) for workspace, root in roots.items()}
    total = {key: round(sum(item.get(key, 0.0) for item in sessions.values()), 1)
             for key in ("footprint_mb", "claude_mb", "mcp_mb", "hooks_mb", "shell_mb")}
    return {"mac": mac(), "sessions": total, "count": len(sessions), "by_workspace": sessions,
            "detached": detached(table, kids, sizes)}


# --- showing it -----------------------------------------------------------------------------------------------

def gb(mb: float | None) -> str:
    return "?" if mb is None else f"{mb / 1024:.1f}"


def at_label(at: str | None) -> str:
    """The watcher's sample time, local, as HH:MM: the figure is a 30 s sample, not a live read."""
    if not at:
        return ""
    from datetime import datetime

    try:
        return datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone().strftime("%H:%M")
    except ValueError:
        return ""


def mac_line(m: dict, at: str | None = None) -> str:
    """``Mac 30.5/36 GB at 14:58 · swap 13.1 · pressure warn``: Memory Used, never top's used-plus-cached."""
    if not m:
        return ""
    when = at_label(at)
    parts = [f"Mac {gb(m.get('used_mb'))}/{gb(m.get('physical_mb')).removesuffix('.0')} GB" + (f" at {when}" if when else "")]
    if m.get("swap_used_mb"):
        parts.append(f"swap {gb(m['swap_used_mb'])}")
    if m.get("pressure") and m["pressure"] != "normal":
        parts.append(f"pressure {m['pressure']}")
    return " · ".join(parts)


def sessions_line(total: dict) -> str:
    return f"sessions {gb(total.get('footprint_mb'))} GB" if total else ""


# --- stored by the watcher -------------------------------------------------------------------------------------

EVERY = 30.0  # seconds between the watcher's measures
FILE = "memory.json"
_read_cache: dict = {"key": None, "value": None}


def store(data_dir: Path, result: dict, *, at: str) -> None:
    """The watcher's latest measure, beside the catalog (written whole, then moved into place)."""
    path = Path(data_dir) / FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"at": at, **result}, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def stored(data_dir: Path) -> dict | None:
    """The latest measure, or None; re-read only when the file changed."""
    path = Path(data_dir) / FILE
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    if _read_cache["key"] != key:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            value = None
        _read_cache.update(key=key, value=value)
    return _read_cache["value"]


def of_workspace(data_dir: Path, workspace: str | None) -> dict | None:
    """One live session's parts, from the latest measure."""
    found = stored(data_dir)
    if not found or not workspace:
        return None
    item = (found.get("by_workspace") or {}).get(workspace.lower())
    return dict(item, at=found.get("at")) if item else None


def summary(found: dict | None) -> dict | None:
    """What status shows: the Mac, the sessions' total and its parts, detached work, and when."""
    if not found:
        return None
    return {key: found.get(key) for key in ("at", "mac", "sessions", "count", "detached")}
