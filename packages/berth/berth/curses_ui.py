"""curses loop. The model and the lines live in app and render.

A trackpad swipe arrives as many up or down keys already sitting in the
terminal buffer. cmux work does not run between those keys. The burst
becomes one capped move. Catalog work runs only when the buffer is empty.

A mouse report is a click or one wheel step. It is not a raw escape.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

from berth.input import (
    TEXT_MODES,
    alt_key,
    burst_cap,
    key_from_bstate,
    normalize,
    plan_keys,
    csi_key,
    sgr_mouse,
    with_mods,
    x10_key,
)

# Bracketed paste: a paste (Cmd-V, Wispr Flow) arrives between these as one block.
PASTE_ON = "\x1b[?2004h"
PASTE_OFF = "\x1b[?2004l"
PASTE_END = "\x1b[201~"
PASTE_MAX = 100_000
# While a text box is open the screen reads the mouse itself, as Claude Code
# and Grok Build do in their prompt: press, drag, and release (1002) in SGR
# form (1006). curses would swallow these as KEY_MOUSE, so its key
# translation is off then and berth.input decodes every sequence.
EDIT_MOUSE_ON = "\x1b[?1002h\x1b[?1006h"
EDIT_MOUSE_OFF = "\x1b[?1002l\x1b[?1006l"
_EDITING = False

PAIRS = {"live": 1, "new": 2, "busy": 2, "warn": 2, "play": 3, "protect": 4, "screen": 5, "idle": 6}
# The same span colors over an account's tint (the account view): curses color numbers, paired with the tint.
SPAN_COLORS = {"live": 2, "new": 3, "busy": 3, "warn": 3, "play": 6, "protect": 1, "screen": 5, "idle": 4}
_TINT_PAIRS: dict[tuple[int, int], int] = {}
_SWATCH_PAIRS: dict[int, int] = {}
_SWATCH_NEXT = 10
# Exact tints (the account view): a terminal that can set its palette gets each tint in a slot of the gray ramp's
# middle, which Berth never draws with (xterm256 maps to the cube; tint256 uses 236, rows' text 253). The terminal
# puts its palette back when the screen exits.
_EXACT_SLOTS = [slot for slot in range(251, 231, -1) if slot != 236]
_EXACT: dict[tuple[int, int, int], int] = {}
TICK_GAP = 0.5


def run(path: Path, opened_at: datetime | None = None, on_tick=None) -> int:
    import locale

    # Before initscr. A long delay lets the next typed key become part of Escape.
    os.environ["ESCDELAY"] = "25"
    if os.environ.get("BERTH_NO_TICK") == "1":
        on_tick = None
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    import curses
    from berth.app import App

    def _main(stdscr) -> int:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        # Raw, not cbreak: ctrl-c, ctrl-z, ctrl-s and the rest reach the text
        # box as keys instead of stopping or suspending the screen.
        curses.raw()
        stdscr.keypad(True)
        _arm_mouse(curses)
        mouse_on = True
        _write(PASTE_ON)
        stdscr.timeout(100)
        colors = _colors(curses)
        app = App(path, opened_at=opened_at)
        next_tick = 0.0
        try:
            return _loop(stdscr, curses, app, colors, next_tick, mouse_on)
        finally:
            _write(EDIT_MOUSE_OFF + PASTE_OFF)

    def _loop(stdscr, curses, app, colors, next_tick, mouse_on) -> int:
        while not app.quit:
            height, width = stdscr.getmaxyx()
            app.set_size(max(width, 1), max(height, 1))
            typing = app.mode in TEXT_MODES
            if typing == mouse_on:
                _edit_mouse(stdscr, curses, typing)
                mouse_on = not typing
            pending = _pending(stdscr, curses)
            if pending:
                feed(app, pending)
            else:
                now = time.monotonic()
                if on_tick is not None and now >= next_tick:
                    on_tick()
                    app.reload()
                    next_tick = time.monotonic() + TICK_GAP
            _paint(stdscr, curses, app.lines(), colors)
        return 0

    curses.wrapper(_main)
    return 0


def feed(app, keys: list[str]) -> None:
    """One batch of keys from the terminal. Only the tree turns a swipe into one capped move."""
    delta, rest = plan_keys(keys, burst_cap(app.height), swipe=app.mode == "tree")
    if delta:
        app.nudge(delta)
    for key in rest:
        if key == "resize":
            continue
        app.handle(key)
        if app.quit:
            break


def _write(text: str) -> None:
    import sys

    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except OSError:
        pass


def _edit_mouse(stdscr, curses, editing: bool) -> None:
    """Hand the mouse to the text box, or back to the list."""
    global _EDITING
    _EDITING = editing
    if editing:
        _disarm_mouse(curses)
        stdscr.keypad(False)
        _write(EDIT_MOUSE_ON)
    else:
        _write(EDIT_MOUSE_OFF)
        stdscr.keypad(True)
        _arm_mouse(curses)


def _disarm_mouse(curses) -> None:
    try:
        curses.mousemask(0)
    except curses.error:
        pass


def _arm_mouse(curses) -> None:
    """Button presses and the wheel. Not motion.

    REPORT_MOUSE_POSITION asks the terminal for every move. Those reports
    were turned into down keys, the list walked on its own, and ordinary
    keys stopped arriving. Clicks do not need that.
    """
    mask = 0
    for name in (
        "BUTTON1_PRESSED",
        "BUTTON1_CLICKED",
        "BUTTON4_PRESSED",
        "BUTTON5_PRESSED",
    ):
        mask |= getattr(curses, name, 0)
    if not mask:
        return
    try:
        curses.mousemask(mask)
    except curses.error:
        return


def _colors(curses) -> bool:
    try:
        if not curses.has_colors():
            return False
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1)
        curses.init_pair(2, curses.COLOR_YELLOW, -1)
        curses.init_pair(3, curses.COLOR_CYAN, -1)
        curses.init_pair(4, curses.COLOR_RED, -1)
        curses.init_pair(5, curses.COLOR_MAGENTA, -1)
        curses.init_pair(6, curses.COLOR_BLUE, -1)  # idle (2026-11-05): closed to free memory, in use again soon
        return True
    except curses.error:
        return False


def _swatch_attr(curses, hex_color: str, cache: dict[int, int]):
    from berth.color import xterm256

    global _SWATCH_NEXT
    index = xterm256(hex_color)
    if index is None:
        return None
    pair_id = cache.get(index)
    if pair_id is None:
        pair_id = _SWATCH_NEXT
        _SWATCH_NEXT += 1
        try:
            curses.init_pair(pair_id, index, -1)
        except curses.error:
            return None
        cache[index] = pair_id
    return curses.color_pair(pair_id) | curses.A_BOLD


def _pair_attr(curses, foreground: int, background: int):
    """A color pair for one foreground over one background, made once; None when the terminal has no room."""
    global _SWATCH_NEXT
    key = (foreground, background)
    pair_id = _TINT_PAIRS.get(key)
    if pair_id is None:
        if getattr(curses, "COLORS", 0) < 256 or _SWATCH_NEXT >= getattr(curses, "COLOR_PAIRS", 0):
            return None
        pair_id = _SWATCH_NEXT
        _SWATCH_NEXT += 1
        try:
            curses.init_pair(pair_id, foreground, background)
        except curses.error:
            return None
        _TINT_PAIRS[key] = pair_id
    return curses.color_pair(pair_id)


def _exact_slot(curses, rgb: tuple[int, int, int]) -> int | None:
    """A palette slot set to exactly ``rgb``, or None: the terminal cannot set colors, or the slots ran out."""
    slot = _EXACT.get(rgb)
    if slot is not None:
        return slot
    if not _EXACT_SLOTS or os.environ.get("COLORTERM", "").lower() not in {"truecolor", "24bit"}:
        return None
    try:
        if getattr(curses, "COLORS", 0) < 256 or not curses.can_change_color():
            return None
        slot = _EXACT_SLOTS[0]
        curses.init_color(slot, *(int(round(value * 1000 / 255)) for value in rgb))
    except (AttributeError, curses.error):
        return None
    _EXACT_SLOTS.pop(0)
    _EXACT[rgb] = slot
    return slot


def _color_index(curses, hex_color: str, *, exact: bool) -> int | None:
    """A color number for ``hex_color``: an exact palette slot for an account's color where the terminal allows,
    else the nearest of the 256 (folder colors are many, so they always take the nearest)."""
    from berth.color import parse_hex, xterm256

    if exact:
        rgb = parse_hex(hex_color)
        slot = _exact_slot(curses, rgb) if rgb is not None else None
        if slot is not None:
            return slot
    return xterm256(hex_color)


def _tint(curses, text: str | None, strong: bool, selected: bool = False) -> tuple[int, int] | None:
    """(background, foreground) for an account's line: its exact color where the terminal allows, else the cube's."""
    from berth.color import tint256, tint_rgb

    exact = tint_rgb(text, strong, selected=selected)
    if exact is not None:
        rgb, dark_text = exact
        slot = _exact_slot(curses, rgb)
        if slot is not None:
            return slot, (16 if dark_text else (231 if strong else 253))
    return None if selected else tint256(text, strong)


def _paint_tinted(stdscr, curses, y: int, line, shown: str, room: int) -> bool:
    """The account view: the whole line in its account's color, spans and swatch over it. False when it cannot."""
    from berth.color import xterm256
    from berth.lineedit import cell_width

    exact_band = bool(line.strong and line.selected)
    tint = _tint(curses, line.tint, line.strong, selected=True) if exact_band else None
    if tint is None:
        exact_band = False
        tint = _tint(curses, line.tint, line.strong)
    if tint is None:
        return False
    background, foreground = tint
    base = _pair_attr(curses, foreground, background)
    if base is None:
        return False
    shown = shown + " " * max(0, room - cell_width(shown))  # the whole bar, not only the text
    attr = base | (curses.A_BOLD if line.strong else 0) | (curses.A_REVERSE if line.selected and not exact_band else 0)
    try:
        stdscr.addnstr(y, 0, shown, len(shown), attr)
    except curses.error:
        pass
    for start, end, name in line.spans:
        frag = shown[start:min(end, len(shown))]
        if not frag:
            continue
        if isinstance(name, str) and name.startswith(("swatch:", "account:")):
            index = _color_index(curses, name.split(":", 1)[1], exact=name.startswith("account:"))
            extra = _pair_attr(curses, index, background) if index is not None else None
            bold = curses.A_BOLD
        elif line.selected or name not in SPAN_COLORS:
            continue
        else:
            extra = _pair_attr(curses, SPAN_COLORS[name], background)
            bold = curses.A_BOLD if name in {"new", "busy", "protect", "screen"} else 0
        if extra is None:
            continue
        try:
            stdscr.addnstr(y, cell_width(shown[:start]), frag, len(frag), extra | bold)
        except curses.error:
            pass
    return True


def _paint(stdscr, curses, lines, colors: bool) -> None:
    height, width = stdscr.getmaxyx()
    stdscr.erase()
    prompt = len(lines) > 1 and lines[1].role == "prompt"
    caret = getattr(lines[1], "cursor", None) if prompt else None
    from berth.lineedit import cell_width
    from berth.render import cut

    for y, line in enumerate(lines):
        if y >= height:
            break
        # Cut by terminal cells, not characters: a wide character takes two.
        # The last line keeps its last cell free so curses does not scroll.
        shown = cut(line.text, max(0, width - 1 if y == height - 1 else width))
        if colors and getattr(line, "tint", None) and _paint_tinted(
                stdscr, curses, y, line, shown, max(0, width - 1 if y == height - 1 else width)):
            continue
        limit = len(shown)
        attr = curses.A_NORMAL
        if line.selected:
            attr |= curses.A_REVERSE
        elif line.role in {"header", "folder", "banner"}:
            attr |= curses.A_BOLD
        if limit:
            try:
                stdscr.addnstr(y, 0, shown, limit, attr)
            except curses.error:
                pass
        if not colors:
            continue
        for start, end, name in line.spans:
            if name == "select":
                # Selected text in a text box, drawn reversed at its real columns.
                frag = shown[start:end]
                if frag:
                    try:
                        stdscr.addnstr(y, cell_width(shown[:start]), frag, len(frag), curses.A_REVERSE)
                    except curses.error:
                        pass
                continue
            if isinstance(name, str) and name.startswith(("swatch:", "account:")):
                if name.startswith("account:"):  # the account view's color key: the account's exact color
                    index = _color_index(curses, name.split(":", 1)[1], exact=True)
                    pair = _pair_attr(curses, index, -1) if index is not None else None
                    attr_swatch = pair | curses.A_BOLD if pair is not None else None
                else:
                    attr_swatch = _swatch_attr(curses, name.split(":", 1)[1], _SWATCH_PAIRS)
                if attr_swatch is None or start >= limit:
                    continue
                frag = shown[start:min(end, limit)]
                if not frag:
                    continue
                try:
                    stdscr.addnstr(y, cell_width(shown[:start]), frag, len(frag), attr_swatch)
                except curses.error:
                    pass
                continue
            if line.selected:
                continue
            pair = PAIRS.get(name)
            if pair is None or start >= limit:
                continue
            frag = shown[start:min(end, limit)]
            if not frag:
                continue
            extra = curses.color_pair(pair)
            if name in {"new", "busy", "protect", "screen"}:
                extra |= curses.A_BOLD
            try:
                # Spans count characters; the screen counts cells.
                stdscr.addnstr(y, cell_width(shown[:start]), frag, len(frag), extra)
            except curses.error:
                pass
        if line.selected and cell_width(shown) < width - 1:
            try:
                stdscr.chgat(y, 0, min(cell_width(shown), width - 1), attr)
            except curses.error:
                pass
    if prompt:
        column = caret if caret is not None else cell_width(lines[1].text)
        try:
            curses.curs_set(1)
            stdscr.move(1, min(column, max(0, width - 1)))
        except curses.error:
            pass
    else:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
    try:
        stdscr.noutrefresh()
        curses.doupdate()
    except curses.error:
        pass


def _pending(stdscr, curses) -> list[str]:
    """Wait briefly for one key, then take whatever else is already buffered.

    The wait is what stops this loop from spinning. A swipe still arrives as
    many keys, and those are collected before any catalog work.
    """
    stdscr.nodelay(False)
    stdscr.timeout(100)
    first = _read_key(stdscr, curses)
    if first is None:
        return []
    found = [first]
    stdscr.nodelay(True)
    try:
        while len(found) < 200:
            stdscr.nodelay(True)
            key = _read_key(stdscr, curses)
            if key is None:
                break
            found.append(key)
    finally:
        stdscr.nodelay(False)
        stdscr.timeout(100)
    return found


def _read_key(stdscr, curses):
    try:
        ch = stdscr.get_wch()
    except curses.error:
        return None
    if ch == "\x1b":
        return _after_esc(stdscr, curses)
    if ch == getattr(curses, "KEY_MOUSE", None):
        return _mouse_key(stdscr, curses)
    if isinstance(ch, str):
        return normalize(ch)
    named = {
        curses.KEY_DOWN: "down",
        curses.KEY_UP: "up",
        curses.KEY_LEFT: "left",
        curses.KEY_RIGHT: "right",
        curses.KEY_ENTER: "enter",
        curses.KEY_BACKSPACE: "backspace",
        curses.KEY_DC: "delete",
        curses.KEY_NPAGE: "pagedown",
        curses.KEY_PPAGE: "pageup",
        curses.KEY_HOME: "home",
        curses.KEY_END: "end",
        curses.KEY_RESIZE: "resize",
        curses.KEY_SLEFT: "shift-left",
        curses.KEY_SRIGHT: "shift-right",
        curses.KEY_SHOME: "shift-home",
        curses.KEY_SEND: "shift-end",
        curses.KEY_SDC: "shift-delete",
        curses.KEY_BTAB: "shift-tab",
    }.get(ch)
    if named:
        return named
    return _extended(curses, ch)


_EXTENDED = {"kLFT": "left", "kRIT": "right", "kUP": "up", "kDN": "down", "kHOM": "home", "kEND": "end", "kDC": "delete"}


def _extended(curses, ch):
    """ncurses names modified keys from terminfo: kLFT3 is Option-Left, kRIT6 Ctrl-Shift-Right."""
    try:
        name = curses.keyname(ch).decode("ascii", "replace")
    except (curses.error, ValueError, TypeError):
        return None
    for prefix, base in _EXTENDED.items():
        if name.startswith(prefix) and name[len(prefix):].isdigit():
            return with_mods(base, int(name[len(prefix):]))
    return None


def _mouse_key(stdscr, curses):
    """KEY_MOUSE is one click or one wheel step. It is not left as an escape."""
    try:
        _id, _x, y, _z, bstate = curses.getmouse()
    except curses.error:
        # The 4-button ABI rejects wheel down unless position reports are armed.
        return "down"
    return key_from_bstate(
        bstate,
        y,
        up=getattr(curses, "BUTTON4_PRESSED", 0),
        down=getattr(curses, "BUTTON5_PRESSED", 0),
        click=getattr(curses, "BUTTON1_PRESSED", 0) | getattr(curses, "BUTTON1_CLICKED", 0),
        place=getattr(curses, "REPORT_MOUSE_POSITION", 0),
    )


def _after_esc(stdscr, curses):
    """Eat a CSI sequence so a wheel cannot leave the arrow parser mid-byte.

    A wheel that arrives as ESC [ < 64 ; ... M becomes one up or one down.
    Anything else in that sequence is dropped. A bare Escape is esc.
    """
    stdscr.nodelay(True)
    try:
        try:
            nxt = stdscr.get_wch()
        except curses.error:
            return "esc"
    finally:
        stdscr.nodelay(False)
    if nxt == "O":
        # SS3: arrows, Home, and End in application cursor mode.
        stdscr.nodelay(True)
        try:
            try:
                final = stdscr.get_wch()
            except curses.error:
                return "alt-O"
        finally:
            stdscr.nodelay(False)
        return csi_key(final) if isinstance(final, str) else None
    if nxt != "[":
        if isinstance(nxt, str):
            return alt_key(nxt)
        return None
    chars: list[str] = []
    stdscr.nodelay(True)
    try:
        while len(chars) < 32:
            try:
                part = stdscr.get_wch()
            except curses.error:
                break
            if not isinstance(part, str):
                break
            chars.append(part)
            if "@" <= part <= "~":
                break
    finally:
        stdscr.nodelay(False)
    text = "".join(chars)
    if text == "M":
        return _x10_tail(stdscr, curses)
    if text == "200~":
        return _read_paste(stdscr, curses)
    if _EDITING and text.startswith("<"):
        return sgr_mouse(text)
    parsed = csi_key(text)
    if parsed:
        return parsed
    if not text:
        return "esc"
    return None


def _read_paste(stdscr, curses):
    """Everything up to Escape [ 201 ~ is one paste, however fast it arrives."""
    from berth.lineedit import PASTE

    chars: list[str] = []
    stdscr.timeout(500)
    try:
        while len(chars) < PASTE_MAX:
            try:
                part = stdscr.get_wch()
            except curses.error:
                break
            if not isinstance(part, str):
                continue
            chars.append(part)
            if part == "~" and "".join(chars[-6:]) == PASTE_END:
                del chars[-6:]
                break
    finally:
        stdscr.timeout(100)
    return PASTE + "".join(chars)


def _x10_tail(stdscr, curses):
    """Eat Cb Cx Cy so a wheel coordinate cannot fall through as j or k."""
    grabbed: list[str] = []
    stdscr.nodelay(True)
    try:
        while len(grabbed) < 3:
            try:
                part = stdscr.get_wch()
            except curses.error:
                break
            if not isinstance(part, str) or len(part) != 1:
                break
            grabbed.append(part)
    finally:
        stdscr.nodelay(False)
    if len(grabbed) != 3:
        return None
    return x10_key(grabbed[0], grabbed[1], grabbed[2])
