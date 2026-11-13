"""The Mac's process table, read once a second instead of one ``ps`` per question (2026-11-03).

The Performance Agent's architect measured Berth's watcher starting about 60
processes a second (929 in 15 s; about 12% of a core over four hours): a
``ps -p`` for each pid it checked, a ``ps -t`` for each terminal, and the cmux
command line for each pane. The pane reads now go over cmux's socket
(``berth/cmux.py``), a pid check is a signal (``berth.place.pid_alive``), and
what is left is answered here from one ``ps -A`` at most once a second: the
foreground processes of a terminal, and a process's command line.
"""

from __future__ import annotations

import subprocess
import time

TTL = 1.0
_tables: dict[str, tuple[float, dict]] = {}


def _read(kind: str, fields: str, parse) -> dict:
    now = time.monotonic()
    cached = _tables.get(kind)
    if cached is not None and now - cached[0] < TTL:
        return cached[1]
    try:
        out = subprocess.run(["ps", "-A", "-o", fields], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    table = parse(out)
    _tables[kind] = (now, table)
    return table


def _foregrounds(out: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4 and "+" in parts[1] and parts[2] not in ("??", "-"):
            found.setdefault(parts[2], []).append(parts[3].strip())
    return found


def _commands(out: str) -> dict[int, str]:
    found: dict[int, str] = {}
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if parts and parts[0].isdigit():
            found[int(parts[0])] = parts[1].strip() if len(parts) > 1 else ""
    return found


def foreground(tty: str) -> list[str]:
    """The foreground process names on one terminal (``ps``'s ``+``), as ``ps -o comm=`` names them."""
    name = tty.removeprefix("/dev/")
    return list(_read("foreground", "pid=,stat=,tty=,comm=", _foregrounds).get(name, []))


def command(pid) -> str:
    """A process's command line, or "" when it is gone."""
    try:
        return _read("command", "pid=,command=", _commands).get(int(pid), "")
    except (TypeError, ValueError):
        return ""


def forget() -> None:
    """Drop the cached tables (tests, or a caller that must see a process started this second)."""
    _tables.clear()


def environ_value(pid: int, name: str) -> str | None:
    """One variable from a process's own environment (sysctl ``KERN_PROCARGS2``), or None. Only ``name`` is kept:
    the rest of the environment is read past and never stored (2026-11-08: which Claude config folder a process an
    adopted row points at really runs on)."""
    import ctypes

    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        mib = (ctypes.c_int * 3)(1, 49, int(pid))  # CTL_KERN, KERN_PROCARGS2
        size = ctypes.c_size_t(0)
        if libc.sysctl(mib, 3, None, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0 or not size.value:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
            return None
    except (OSError, ValueError):
        return None
    raw = buf.raw[: size.value]
    argc = int.from_bytes(raw[:4], "little")
    parts = raw[4:].split(b"\0")
    index = 1  # past the executable's path
    while index < len(parts) and parts[index] == b"":
        index += 1  # its padding
    index += argc  # past the arguments
    prefix = name.encode() + b"="
    for item in parts[index:]:
        if item.startswith(prefix):
            return item[len(prefix):].decode(errors="replace")
    return None
