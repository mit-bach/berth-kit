import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

from berth.cli import main
from tests.support import OPENED, make_app


def test_dump_matches_the_app(desk, capsys):
    app = make_app(desk)
    code = main(
        [
            "--db",
            str(desk),
            "--dump",
            "--width",
            "96",
            "--height",
            "18",
            "--opened",
            "2026-10-02T21:00:00Z",
        ]
    )
    assert code == 0
    assert capsys.readouterr().out == app.text() + "\n"
    assert OPENED.year == 2026


def test_dump_missing_catalog(tmp_path, capsys):
    code = main(["--db", str(tmp_path / "missing.sqlite"), "--dump"])
    assert code == 1
    assert "no catalog" in capsys.readouterr().err


def test_opened_must_be_a_time(desk, capsys):
    code = main(["--db", str(desk), "--dump", "--opened", "yesterday"])
    assert code == 2
    assert "not a time" in capsys.readouterr().err


def test_package_does_not_call_out():
    root = Path(__file__).resolve().parents[1] / "berth"
    # The screen records intents. The engine, not these modules, calls cmux.
    screen = {
        "app.py",
        "catalog.py",
        "curses_ui.py",
        "fixture.py",
        "render.py",
        "schema.py",
        "tree.py",
    }
    banned_modules = ("subprocess", "socket", "urllib", "requests", "textual", "urwid", "cmux")
    for path in root.rglob("*.py"):
        if path.name not in screen:
            continue
        text = path.read_text(encoding="utf-8")
        assert "os.system" not in text
        assert "Popen" not in text
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                for name in banned_modules:
                    assert name not in stripped


def test_terminal_opens_and_q_quits(desk):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 96, 0, 0))
    env = os.environ.copy()
    root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    env["TERM"] = "xterm-256color"
    def _take_tty() -> None:
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    proc = subprocess.Popen(
        [sys.executable, "-m", "berth", "--db", str(desk)],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        close_fds=True,
        preexec_fn=_take_tty,
    )
    os.close(slave)
    data = b""
    try:
        deadline = time.time() + 4
        while time.time() < deadline and "Berth ·".encode() not in data:
            ready, _, _ = select.select([master], [], [], 0.2)
            if not ready:
                continue
            try:
                data += os.read(master, 4096)
            except OSError:
                break
        assert "Berth ·".encode() in data, data.decode("utf-8", "replace")[-400:]
        os.write(master, b"q")
        deadline = time.time() + 3
        while time.time() < deadline and proc.poll() is None:
            ready, _, _ = select.select([master], [], [], 0.1)
            if not ready:
                continue
            try:
                os.read(master, 4096)
            except OSError:
                break
        code = proc.wait(timeout=2)
        assert code == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)
        os.close(master)


def test_the_real_screen_names_a_folder_with_typed_pasted_and_edited_text(desk):
    """The bytes a Mac sends: letters, a bracketed paste, Option-Left, Cmd-Left, Delete."""
    import sqlite3

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 96, 0, 0))
    env = os.environ.copy()
    root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    env["TERM"] = "xterm-256color"
    env["BERTH_NO_TICK"] = "1"

    def _take_tty() -> None:
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    proc = subprocess.Popen(
        [sys.executable, "-m", "berth", "--db", str(desk)],
        stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True, preexec_fn=_take_tty,
    )
    os.close(slave)

    def drain(until: bytes = b"", seconds: float = 1.0) -> bytes:
        seen = b""
        deadline = time.time() + seconds
        while time.time() < deadline:
            ready, _, _ = select.select([master], [], [], 0.05)
            if ready:
                try:
                    seen += os.read(master, 65536)
                except OSError:
                    break
                if until and until in seen:
                    break
        return seen

    try:
        assert "Berth ·".encode() in drain("Berth ·".encode(), 4)
        steps = [
            b"n",                                   # new folder under the cursor
            b"jk ",                                 # j and k are letters here, and space moves on
            b"\x1b[200~Week\n2\x1b[201~",           # Cmd-V or Wispr Flow: one bracketed paste
            b"\x1bb",                               # Option-Left: one word back, before 2
            b"x",
            b"\x01",                                # Cmd-Left: to the start
            b"\x1b[3~",                             # Delete: the j goes
            b"\r",
        ]
        for chunk in steps:
            os.write(master, chunk)
            drain(seconds=0.25)
        os.write(master, b"q")
        drain(seconds=0.5)
        proc.wait(timeout=3)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)
        os.close(master)
    conn = sqlite3.connect(desk)
    names = [row[0] for row in conn.execute("SELECT name FROM folder")]
    conn.close()
    assert "k Week x2" in names, names


def test_the_real_screen_deletes_a_mouse_selection(desk):
    """Press, drag, release (SGR mouse), then Backspace; a double click, then typing over it."""
    import sqlite3

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 96, 0, 0))
    env = os.environ.copy()
    root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    env["TERM"] = "xterm-256color"
    env["BERTH_NO_TICK"] = "1"

    def _take_tty() -> None:
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    proc = subprocess.Popen(
        [sys.executable, "-m", "berth", "--db", str(desk)],
        stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True, preexec_fn=_take_tty,
    )
    os.close(slave)
    out = b""

    def drain(seconds: float) -> None:
        nonlocal out
        deadline = time.time() + seconds
        while time.time() < deadline:
            ready, _, _ = select.select([master], [], [], 0.05)
            if ready:
                try:
                    out += os.read(master, 65536)
                except OSError:
                    break

    def at(index: int) -> int:
        return len("new folder in / · ") + index + 1  # SGR columns are 1-based

    try:
        drain(2.0)
        assert "Berth ·".encode() in out
        # The cursor starts on Inbox, so the new folder goes at the top level.
        for chunk in (b"n", b"hello world"):
            os.write(master, chunk)
            drain(0.25)
        assert b"\x1b[?1002h" in out  # the box asked for press, drag, and release
        steps = [
            f"\x1b[<0;{at(6)};2M".encode(), f"\x1b[<32;{at(11)};2M".encode(), f"\x1b[<0;{at(11)};2m".encode(),
            b"\x7f",                                         # Backspace removes "world"
            f"\x1b[<0;{at(2)};2M".encode(), f"\x1b[<0;{at(2)};2m".encode(),
            f"\x1b[<0;{at(2)};2M".encode(), f"\x1b[<0;{at(2)};2m".encode(),  # double click: "hello"
            b"bye", b"\r",
        ]
        for chunk in steps:
            os.write(master, chunk)
            drain(0.15)
        os.write(master, b"q")
        drain(0.5)
        proc.wait(timeout=3)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)
        os.close(master)
    conn = sqlite3.connect(desk)
    names = [row[0] for row in conn.execute("SELECT name FROM folder")]
    conn.close()
    assert "bye" in names, names
