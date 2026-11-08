"""Install a LaunchAgent that runs ``berth watch`` and restarts it.

The label is ``com.bach.berth.watch``. The plist is copied into
``~/Library/LaunchAgents`` and bootstrapped into the user's GUI domain.
The same bytes are written under ``launchd/`` in the install tree so the
repo holds the agent that was asked for.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from berth.cmux import default_socket
from berth.watch import LABEL

REPO_PLIST = Path(__file__).resolve().parents[1] / "launchd" / f"{LABEL}.plist"


def berth_executable() -> str:
    argv0 = Path(sys.argv[0]).resolve()
    if argv0.exists() and argv0.name.startswith("berth"):
        return str(argv0)
    found = shutil.which("berth")
    if found:
        return str(Path(found).resolve())
    return str(argv0)


def agent_plist(executable: str) -> dict:
    home = Path.home()
    log_dir = home / ".local" / "share" / "berth"
    path = os.environ.get("PATH", "/opt/homebrew/bin:/usr/bin:/bin")
    binary_dir = str(Path(executable).parent)
    if binary_dir not in path.split(":"):
        path = binary_dir + ":" + path
    cmux_dir = str(Path(shutil.which("cmux") or "/opt/homebrew/bin/cmux").parent)
    if cmux_dir not in path.split(":"):
        path = cmux_dir + ":" + path
    return {
        "Label": LABEL,
        "ProgramArguments": [executable, "watch"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 5,
        "WorkingDirectory": str(home),
        "StandardOutPath": str(log_dir / "watch.log"),
        "StandardErrorPath": str(log_dir / "watch.err"),
        "EnvironmentVariables": {
            "PATH": path,
            "HOME": str(home),
            "CMUX_SOCKET_PATH": str(default_socket()),
            "PYTHONUNBUFFERED": "1",
        },
    }


def render(plist: dict) -> bytes:
    return plistlib.dumps(plist, sort_keys=False)


def write_plist(plist: dict, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    destination.write_bytes(render(plist))
    os.chmod(destination, 0o644)
    return destination


def launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def installed_plist_path() -> Path:
    return launch_agents_dir() / f"{LABEL}.plist"


def bootstrap(plist_path: Path) -> tuple[bool, str]:
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{LABEL}"
    subprocess.run(["launchctl", "bootout", target], capture_output=True, text=True, check=False)
    result = subprocess.run(
        ["launchctl", "bootstrap", domain, str(plist_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "bootstrap failed").strip()
        return False, message
    subprocess.run(["launchctl", "enable", target], capture_output=True, text=True, check=False)
    return True, ""


def install_agent(*, repo_plist: Path | None = None) -> dict:
    executable = berth_executable()
    plist = agent_plist(executable)
    log_dir = Path.home() / ".local" / "share" / "berth"
    log_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(log_dir, 0o700)
    destination = write_plist(plist, installed_plist_path())
    repo = repo_plist or REPO_PLIST
    try:
        write_plist(plist, repo)
    except OSError as exc:
        repo_error = str(exc)
    else:
        repo_error = ""
    ok, message = bootstrap(destination)
    return {
        "plist": str(destination),
        "repo_plist": str(repo),
        "repo_error": repo_error,
        "label": LABEL,
        "executable": executable,
        "bootstrapped": ok,
        "bootstrap_error": message,
    }


# --- the shared MCP server (2026-11-07, Dominik's yes: "Make it a background server") -------------------------
#
# ``berth mcp --http`` serves every session's berth and cmux-mcp tools from one process (berth/mcp_http.py,
# docs/SHARED-MCP-SERVER-PLAN.md). Its own LaunchAgent, apart from the watcher, so neither can stall the other.

MCP_LABEL = "com.bach.berth.mcp"
MCP_REPO_PLIST = Path(__file__).resolve().parents[1] / "launchd" / f"{MCP_LABEL}.plist"
# cmux-mcp's home until it moves into FORGE: its stdio registration puts the same folder on PYTHONPATH.
CMUX_MCP_HOME = Path(os.environ.get("BERTH_CMUX_MCP_PATH") or Path.home() / "work" / "bach-coding-stack" / "packages" / "cmux-mcp")


def mcp_plist(executable: str, port: int) -> dict:
    plist = agent_plist(executable)
    log_dir = Path.home() / ".local" / "share" / "berth"
    plist.update({
        "Label": MCP_LABEL,
        "ProgramArguments": [executable, "mcp", "--http", "--port", str(port)],
        "StandardOutPath": str(log_dir / "mcp-http.log"),
        "StandardErrorPath": str(log_dir / "mcp-http.err"),
    })
    env = dict(plist["EnvironmentVariables"])
    env["BERTH_ROOT"] = str(Path(__file__).resolve().parents[1])
    if (CMUX_MCP_HOME / "cmux_mcp" / "__init__.py").is_file():
        env["PYTHONPATH"] = str(CMUX_MCP_HOME)
    plist["EnvironmentVariables"] = env
    return plist


def _bootstrap_label(plist_path: Path, label: str) -> tuple[bool, str]:
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{label}"
    subprocess.run(["launchctl", "bootout", target], capture_output=True, text=True, check=False)
    result = subprocess.run(["launchctl", "bootstrap", domain, str(plist_path)], capture_output=True, text=True,
                            check=False)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "bootstrap failed").strip()
    subprocess.run(["launchctl", "enable", target], capture_output=True, text=True, check=False)
    return True, ""


def mcp_pid() -> int | None:
    """The shared server's pid, from launchd."""
    out = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{MCP_LABEL}"], capture_output=True, text=True,
                         check=False).stdout
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("pid = ") and line[6:].isdigit():
            return int(line[6:])
    return None


def quick_measure(pid: int) -> str:
    """background.md's install-time check: ``top -l 3 -s 2 -pid <pid> -stats cpu,mem``, the last sample."""
    out = subprocess.run(["top", "-l", "3", "-s", "2", "-pid", str(pid), "-stats", "cpu,mem"], capture_output=True,
                         text=True, check=False, timeout=30).stdout
    rows = [line.split() for line in out.splitlines() if line.strip() and line.split()[0].replace(".", "").isdigit()]
    return f"cpu {rows[-1][0]}% · mem {rows[-1][1]}" if rows and len(rows[-1]) >= 2 else "no sample"


def install_mcp(port: int | None = None, *, wait_s: float = 15.0) -> dict:
    """Install and start the shared server, then check it answers and measure it (background.md rule 5)."""
    import time

    from berth.mcp_http import DEFAULT_PORT, check

    port = port or DEFAULT_PORT
    plist = mcp_plist(berth_executable(), port)
    destination = write_plist(plist, launch_agents_dir() / f"{MCP_LABEL}.plist")
    try:
        write_plist(plist, MCP_REPO_PLIST)
        repo_error = ""
    except OSError as exc:
        repo_error = str(exc)
    ok, message = _bootstrap_label(destination, MCP_LABEL)
    health: dict = {}
    deadline = time.monotonic() + wait_s
    while ok and time.monotonic() < deadline:
        health = check(port)
        if health.get("berth") == "up":
            break
        time.sleep(0.5)
    pid = mcp_pid()
    return {"label": MCP_LABEL, "plist": str(destination), "repo_plist": str(MCP_REPO_PLIST), "repo_error": repo_error,
            "port": port, "bootstrapped": ok, "bootstrap_error": message, "pid": pid, "health": health,
            "measured": quick_measure(pid) if pid else None,
            "undo": "berth install --mcp --uninstall"}


def uninstall_mcp() -> dict:
    """Stop the shared server and remove its LaunchAgent (the repo keeps its plist)."""
    target = f"gui/{os.getuid()}/{MCP_LABEL}"
    result = subprocess.run(["launchctl", "bootout", target], capture_output=True, text=True, check=False)
    path = launch_agents_dir() / f"{MCP_LABEL}.plist"
    removed = path.exists()
    if removed:
        path.unlink()
    return {"label": MCP_LABEL, "booted_out": result.returncode == 0, "plist_removed": removed}
