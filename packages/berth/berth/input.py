"""Key bursts from a trackpad. One swipe must not replay as a long walk.

A wheel report is the same kind of motion as an arrow. The cap drops the rest.
"""

from __future__ import annotations

import re

MOTION_DOWN = frozenset({"j", "down"})
MOTION_UP = frozenset({"k", "up"})
MOTION = MOTION_DOWN | MOTION_UP

_SGR_PRESS = re.compile(r"^<(\d+);(\d+);(\d+)M$")


def coalesce(keys: list[str], cap: int) -> tuple[int, list[str]]:
    """Turn a leading run of up/down into one move, capped.

    The rest of the keys stay in order. A cap below 1 is treated as 1.
    Extra motion in that leading run is dropped, not saved for later.
    """
    limit = max(1, cap)
    delta = 0
    index = 0
    while index < len(keys) and keys[index] in MOTION:
        delta += 1 if keys[index] in MOTION_DOWN else -1
        index += 1
    if delta > limit:
        delta = limit
    elif delta < -limit:
        delta = -limit
    return delta, keys[index:]


def burst_cap(height: int) -> int:
    """How far one swipe may move. A short screen still moves one row."""
    body = max(1, int(height) - 3)
    return max(1, body // 4)


def normalize(key: str) -> str:
    if not isinstance(key, str) or not key:
        return ""
    if key in {"\n", "\r", "enter"}:
        return "enter"
    if key in {"\x1b", "esc"}:
        return "esc"
    if key in {"\x7f", "\b", "backspace"}:
        return "backspace"
    if key in {" ", "space"}:
        return "space"
    if key == "\t":
        return "tab"
    if len(key) == 1 and 1 <= ord(key) <= 26:
        return "ctrl-" + chr(ord(key) + 96)
    if key == "\x1f":
        return "ctrl-_"
    return key


def alt_key(char: str) -> str | None:
    """Escape then one character: Option (alt) with that key, as Ghostty sends it."""
    if char in {"\x7f", "\b"}:
        return "alt-backspace"
    if char == "\x1b":
        return "esc"
    if len(char) == 1 and char.isprintable():
        return "alt-" + ("space" if char == " " else char)
    return None


TEXT_MODES = frozenset({"search", "mkdir", "rename", "retitle", "move"})


def plan_keys(keys: list[str], cap: int, *, swipe: bool) -> tuple[int, list[str]]:
    """Coalesce a run of up and down into one capped move only when ``swipe``: in the tree.

    Every other view gets each key as it came: a text box types j and k, and
    the trash, help, excerpt, and confirm views move their own cursor or
    scroll. (2026-10-28: coalescing everywhere but text boxes turned the
    trash view's arrows into moves of the hidden tree.)
    """
    if not swipe:
        return 0, drop_repeat_clicks(keys)
    return plan_burst(keys, cap)


_CSI_FINAL = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end"}
_CSI_TILDE = {"1": "home", "7": "home", "4": "end", "8": "end", "2": "insert", "3": "delete", "5": "pageup", "6": "pagedown"}
_CSI_U = {9: "tab", 13: "enter", 27: "esc", 127: "backspace", 8: "backspace"}


def with_mods(base: str, code: int) -> str:
    """xterm modifier code (1 + shift 1, alt 2, ctrl 4, super 8) on a key name.

    Cmd (super) with an arrow is treated as Home or End, as Ghostty's own
    Cmd-Left and Cmd-Right do.
    """
    bits = max(0, code - 1)
    shift, alt, ctrl, superkey = bits & 1, bits & 2, bits & 4, bits & 8
    if superkey and base in {"left", "right"}:
        base = "home" if base == "left" else "end"
        alt = ctrl = 0
    name = base
    if ctrl:
        name = "ctrl-" + name
    elif alt:
        name = "alt-" + name
    if shift:
        name = "shift-" + name
    return name


def _csi_u(text: str) -> str | None:
    """Kitty keyboard protocol: CSI code[;mods]u."""
    body = text[:-1]
    head, _, mods = body.partition(";")
    code_text = head.split(":")[0]
    if not code_text.isdigit():
        return None
    code = int(code_text)
    modcode = int(mods.split(":")[0]) if mods.split(":")[0].isdigit() else 1
    bits = max(0, modcode - 1)
    if code in _CSI_U:
        base = _CSI_U[code]
        if base == "backspace" and bits & 2:
            return "alt-backspace"
        if base == "backspace" and bits & 4:
            return "ctrl-w"
        return base
    if code < 32 or code > 0x10FFFF:
        return None
    char = chr(code)
    if bits & 4 and char.isalpha():
        return "ctrl-" + char.lower()
    if bits & 2:
        return "alt-" + char
    if bits & 1 and char.isalpha():
        return char.upper()
    return "space" if char == " " else char


def click_row(key: str) -> int | None:
    """Screen row from a click key. The header is row 0. A bad key is None."""
    if not isinstance(key, str) or not key.startswith("click:"):
        return None
    raw = key.split(":", 1)[1]
    if not raw.isdigit():
        return None
    return int(raw)


def csi_key(text: str) -> str | None:
    """One key from the bytes after Escape [.

    A bare Escape is handled by the caller. cmux sends Escape as the kitty
    sequence ``27u``, which used to be dropped, so the help screen stayed open.
    """
    parsed = sgr_key(text)
    if parsed:
        return parsed
    if text in _CSI_FINAL:
        return _CSI_FINAL[text]
    if text == "Z":
        return "shift-tab"
    if len(text) >= 2 and text[-1] in _CSI_FINAL and text[:-1].startswith("1;"):
        mods = text[2:-1]
        if mods.isdigit():
            return with_mods(_CSI_FINAL[text[-1]], int(mods))
    if text.endswith("~"):
        head, _, mods = text[:-1].partition(";")
        base = _CSI_TILDE.get(head)
        if base is None:
            return None
        return with_mods(base, int(mods)) if mods.isdigit() else base
    if text.endswith("u"):
        return _csi_u(text)
    return None


def sgr_key(text: str) -> str | None:
    """One key from the CSI payload after Escape [.

    Wheel prefixes <64 and <65 stay up and down. A left press is click:y.
    The row on the wire is 1-based. A release is not a click.
    """
    if text.startswith("<64"):
        return "up"
    if text.startswith("<65"):
        return "down"
    match = _SGR_PRESS.match(text)
    if match is None:
        return None
    button = int(match.group(1))
    row = int(match.group(3))
    # Bit 6 is the wheel. Bit 5 is a drag. The low two bits are the button.
    if button & 0x60 or button & 3:
        return None
    y = row - 1
    if y < 0:
        return None
    return f"click:{y}"


_SGR_ANY = re.compile(r"^<(\d+);(\d+);(\d+)([Mm])$")


def sgr_mouse(text: str) -> str | None:
    """A text box's mouse report (SGR, button-event tracking): press, drag, or release.

    The result is ``mouse:<kind>:<column>:<row>``, 0-based. The wheel is up
    or down. Only the left button selects; other buttons are not keys.
    """
    match = _SGR_ANY.match(text)
    if match is None:
        return None
    button, column, row, final = int(match.group(1)), int(match.group(2)) - 1, int(match.group(3)) - 1, match.group(4)
    if button & 64:
        return "up" if button & 3 == 0 else ("down" if button & 3 == 1 else None)
    if button & 3:
        return None
    if final == "m":
        kind = "release"
    elif button & 32:
        kind = "drag"
    else:
        kind = "press"
    return f"mouse:{kind}:{max(0, column)}:{max(0, row)}"


def mouse_parts(key: str) -> tuple[str, int, int] | None:
    parts = key.split(":")
    if len(parts) != 4 or parts[0] != "mouse" or not parts[2].isdigit() or not parts[3].isdigit():
        return None
    return parts[1], int(parts[2]), int(parts[3])


def x10_key(cb: str, cx: str, cy: str) -> str | None:
    """One key from the three bytes after CSI M. Coordinates are eaten by the caller."""
    if len(cb) != 1 or len(cx) != 1 or len(cy) != 1:
        return None
    code = ord(cb) - 32
    y = ord(cy) - 33
    if code & 64:
        low = code & 3
        if low == 0:
            return "up"
        if low == 1:
            return "down"
        return None
    if code & 32 or code & 3:
        return None
    if y < 0:
        return None
    return f"click:{y}"


def key_from_bstate(
    bstate: int,
    y: int,
    *,
    up: int,
    down: int,
    click: int,
    place: int,
) -> str | None:
    """A mouse report becomes one wheel step or one click.

    y is the painted line, already 0-based. Wheel down on the 4-button ABI
    arrives as a position report, not as button 5. Anything else is not a key.
    """
    if isinstance(bstate, bool) or not isinstance(bstate, int):
        return None
    if isinstance(y, bool) or not isinstance(y, int):
        return None
    if up and bstate & up:
        return "up"
    if down and bstate & down:
        return "down"
    if click and bstate & click and y >= 0:
        return f"click:{y}"
    # A move report is not a scroll. Treating it as down made the list walk.
    return None


def drop_repeat_clicks(keys: list[str]) -> list[str]:
    """A press and a click on the same row in one burst are one click."""
    kept: list[str] = []
    last = ""
    for key in keys:
        if key.startswith("click:") and key == last:
            continue
        kept.append(key)
        last = key if key.startswith("click:") else ""
    return kept


def plan_burst(keys: list[str], cap: int) -> tuple[int, list[str]]:
    """Leading wheel or arrow reports become one capped move. The extra reports drop."""
    delta, rest = coalesce(keys, cap)
    return delta, drop_repeat_clicks(rest)
