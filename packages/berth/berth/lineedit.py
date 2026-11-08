"""One line of editable text: the name boxes and the search box in the screen.

It behaves like a prompt box, using the conventions terminal programs share:

* Readline (emacs mode) keys, which bash, zsh, and Python's REPL use:
  ctrl-a and ctrl-e go to the start and end, ctrl-b and ctrl-f move a
  character, alt-b and alt-f move a word, ctrl-w and alt-backspace delete
  the word before the cursor, alt-d the word after, ctrl-u to the start,
  ctrl-k to the end, ctrl-d or Delete the character under the cursor.
  Ghostty (and so cmux) sends these for the Mac keys: Cmd-Left is ctrl-a,
  Cmd-Right is ctrl-e, Cmd-Backspace is ctrl-u, Option-Left and
  Option-Right are alt-b and alt-f.
* Shift with an arrow, Home, or End selects, as in a text field. Typing or
  a paste replaces the selection; Backspace and Delete remove it.
* ctrl-c copies the selection to the Mac clipboard, ctrl-x cuts it, and
  ctrl-v pastes from the clipboard. ctrl-z undoes, ctrl-y redoes.
* A paste (Cmd-V, Wispr Flow) arrives as one block through the terminal's
  bracketed paste. Line breaks and tabs in it become spaces: this box is
  one line.
* The mouse works as in Claude Code's and Grok Build's prompt: a click puts
  the cursor there, a drag selects, a double click selects a word, a triple
  click selects everything. The selection is the box's own, so Backspace,
  Delete, and typing act on it, and it is copied when the drag ends.

Every printable key is text here, ``j``, ``k``, ``q``, and space included.
"""

from __future__ import annotations

import subprocess
import unicodedata
from dataclasses import dataclass, field

PASTE = "paste:"

# What handle() reports back to the screen.
CHANGED = "changed"
MOVED = "moved"
SUBMIT = "submit"
CANCEL = "cancel"
IGNORED = "ignored"


def cell_width(text: str) -> int:
    """Terminal columns: East Asian wide characters take two, combining marks none."""
    total = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        total += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return total


def clean(text: str) -> str:
    """One line: breaks and tabs become spaces, other control characters go."""
    text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return "".join(char for char in text if char.isprintable())


def _is_word(char: str) -> bool:
    return char.isalnum() or char == "_"


def _clipboard_off() -> bool:
    import os

    # The tests set this, so they never touch the Mac's clipboard.
    return os.environ.get("BERTH_CLIPBOARD") == "off"


def clipboard_copy(text: str) -> bool:
    if _clipboard_off():
        return False
    try:
        subprocess.run(["pbcopy"], input=text, text=True, check=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def clipboard_paste() -> str:
    if _clipboard_off():
        return ""
    try:
        done = subprocess.run(["pbpaste"], capture_output=True, text=True, check=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout


@dataclass
class LineEditor:
    text: str = ""
    limit: int = 300
    cursor: int = -1
    anchor: int | None = None
    copy: object = clipboard_copy
    paste: object = clipboard_paste
    note: str = ""
    _undo: list[tuple[str, int]] = field(default_factory=list)
    _redo: list[tuple[str, int]] = field(default_factory=list)
    _start: int = 0
    _clicks: tuple[float, int, int] = (0.0, -1, 0)
    dragging: bool = False

    def __post_init__(self) -> None:
        self.text = clean(self.text)[: self.limit]
        if self.cursor < 0 or self.cursor > len(self.text):
            self.cursor = len(self.text)

    # selection --------------------------------------------------------

    def selection(self) -> tuple[int, int] | None:
        if self.anchor is None or self.anchor == self.cursor:
            return None
        return (min(self.anchor, self.cursor), max(self.anchor, self.cursor))

    def selected(self) -> str:
        span = self.selection()
        return self.text[span[0] : span[1]] if span else ""

    # edits ------------------------------------------------------------

    def _remember(self) -> None:
        if self._undo and self._undo[-1][0] == self.text:
            return
        self._undo.append((self.text, self.cursor))
        del self._undo[:-100]
        self._redo.clear()

    def _replace(self, start: int, end: int, new: str) -> bool:
        new = clean(new)
        room = self.limit - (len(self.text) - (end - start))
        new = new[: max(0, room)]
        if start == end and not new:
            return False
        self._remember()
        self.text = self.text[:start] + new + self.text[end:]
        self.cursor = start + len(new)
        self.anchor = None
        return True

    def insert(self, new: str) -> bool:
        span = self.selection() or (self.cursor, self.cursor)
        return self._replace(span[0], span[1], new)

    def _delete_to(self, target: int) -> bool:
        span = self.selection()
        if span:
            return self._replace(span[0], span[1], "")
        start, end = sorted((self.cursor, max(0, min(len(self.text), target))))
        return self._replace(start, end, "")

    # motion -----------------------------------------------------------

    def _move(self, target: int, select: bool) -> bool:
        target = max(0, min(len(self.text), target))
        if select:
            if self.anchor is None:
                self.anchor = self.cursor
        else:
            span = self.selection()
            if span is not None and target in {self.cursor - 1, self.cursor + 1}:
                # An arrow with a selection lands on that side of it, as in a text field.
                target = span[0] if target < self.cursor else span[1]
            self.anchor = None
        moved = target != self.cursor
        self.cursor = target
        return moved

    def word_left(self) -> int:
        index = self.cursor
        while index > 0 and not _is_word(self.text[index - 1]):
            index -= 1
        while index > 0 and _is_word(self.text[index - 1]):
            index -= 1
        return index

    def word_right(self) -> int:
        index = self.cursor
        size = len(self.text)
        while index < size and not _is_word(self.text[index]):
            index += 1
        while index < size and _is_word(self.text[index]):
            index += 1
        return index

    # keys -------------------------------------------------------------

    def handle(self, key: str) -> str:
        """Apply one key. Returns changed, moved, submit, cancel, or ignored."""
        self.note = ""
        if key.startswith(PASTE):
            return CHANGED if self.insert(key[len(PASTE) :]) else IGNORED
        if key == "enter":
            return SUBMIT
        if key == "esc":
            if self.selection():
                self.anchor = None
                return MOVED
            return CANCEL
        motion = {
            "left": (lambda: self.cursor - 1, False),
            "ctrl-b": (lambda: self.cursor - 1, False),
            "right": (lambda: self.cursor + 1, False),
            "ctrl-f": (lambda: self.cursor + 1, False),
            "shift-left": (lambda: self.cursor - 1, True),
            "shift-right": (lambda: self.cursor + 1, True),
            "alt-left": (self.word_left, False),
            "alt-b": (self.word_left, False),
            "ctrl-left": (self.word_left, False),
            "alt-right": (self.word_right, False),
            "alt-f": (self.word_right, False),
            "ctrl-right": (self.word_right, False),
            "shift-alt-left": (self.word_left, True),
            "shift-ctrl-left": (self.word_left, True),
            "shift-alt-right": (self.word_right, True),
            "shift-ctrl-right": (self.word_right, True),
            "home": (lambda: 0, False),
            "ctrl-a": (lambda: 0, False),
            "end": (lambda: len(self.text), False),
            "ctrl-e": (lambda: len(self.text), False),
            "shift-home": (lambda: 0, True),
            "shift-up": (lambda: 0, True),
            "shift-end": (lambda: len(self.text), True),
            "shift-down": (lambda: len(self.text), True),
        }
        if key in motion:
            target, select = motion[key]
            return MOVED if self._move(target(), select) else IGNORED
        deletion = {
            "backspace": lambda: self.cursor - 1,
            "ctrl-h": lambda: self.cursor - 1,
            "delete": lambda: self.cursor + 1,
            "ctrl-d": lambda: self.cursor + 1,
            "ctrl-w": self.word_left,
            "alt-backspace": self.word_left,
            "alt-d": self.word_right,
            "alt-delete": self.word_right,
            "ctrl-u": lambda: 0,
            "ctrl-k": lambda: len(self.text),
        }
        if key in deletion:
            return CHANGED if self._delete_to(deletion[key]()) else IGNORED
        if key == "ctrl-c":
            chosen = self.selected()
            if not chosen:
                return CANCEL
            self.note = "copied" if self.copy(chosen) else "copy failed"
            return MOVED
        if key == "ctrl-x":
            chosen = self.selected()
            if not chosen:
                return IGNORED
            self.note = "cut" if self.copy(chosen) else "copy failed"
            span = self.selection()
            self._replace(span[0], span[1], "")
            return CHANGED
        if key == "ctrl-v":
            return CHANGED if self.insert(self.paste()) else IGNORED
        if key in {"ctrl-z", "ctrl-_"}:
            return CHANGED if self._step(self._undo, self._redo) else IGNORED
        if key == "ctrl-y":
            return CHANGED if self._step(self._redo, self._undo) else IGNORED
        if key == "space":
            return CHANGED if self.insert(" ") else IGNORED
        if len(key) == 1 and key.isprintable():
            return CHANGED if self.insert(key) else IGNORED
        return IGNORED

    def _step(self, source: list, target: list) -> bool:
        if not source:
            return False
        target.append((self.text, self.cursor))
        self.text, self.cursor = source.pop()
        self.anchor = None
        return True

    # mouse ------------------------------------------------------------

    def index_at(self, column: int) -> int:
        """The text index at a column of the box, as last drawn: before the character there."""
        offset = 0
        index = self._start
        while index < len(self.text):
            width = cell_width(self.text[index])
            if offset + width > max(0, column):
                return index
            offset += width
            index += 1
        return len(self.text)

    def press(self, column: int, now: float) -> str:
        """A button press in the box. One click places, two select a word, three everything."""
        index = self.index_at(column)
        last_time, last_index, count = self._clicks
        count = count + 1 if now - last_time <= 0.4 and abs(index - last_index) <= 1 else 1
        self._clicks = (now, index, count)
        if count >= 3:
            self.anchor, self.cursor = 0, len(self.text)
            self.dragging = False
            return MOVED
        if count == 2:
            start = end = min(index, len(self.text))
            while start > 0 and _is_word(self.text[start - 1]):
                start -= 1
            while end < len(self.text) and _is_word(self.text[end]):
                end += 1
            self.anchor, self.cursor = start, end
            self.dragging = False
            return MOVED
        self.anchor = index
        self.cursor = index
        self.dragging = True
        return MOVED

    def drag(self, column: int) -> str:
        if not self.dragging:
            return IGNORED
        index = self.index_at(column)
        if index == self.cursor:
            return IGNORED
        self.cursor = index
        return MOVED

    def release(self, column: int) -> str:
        """The drag ends. A selection is copied, as the terminal copies on select."""
        if self.dragging:
            self.drag(column)
        self.dragging = False
        chosen = self.selected()
        if chosen:
            self.note = "copied" if self.copy(chosen) else ""
            return MOVED
        self.anchor = None
        return IGNORED

    # drawing ----------------------------------------------------------

    def view(self, width: int) -> tuple[str, int, tuple[int, int] | None]:
        """The part that fits in ``width`` columns, the cursor column, the selection columns.

        The window scrolls so the cursor stays in view, with one spare column
        at the end for the cursor itself.
        """
        width = max(1, width)
        start = 0
        while start < self.cursor and cell_width(self.text[start : self.cursor]) > width - 1:
            start += 1
        end = start
        while end < len(self.text) and cell_width(self.text[start : end + 1]) <= width:
            end += 1
        self._start = start
        shown = self.text[start:end]
        cursor_col = cell_width(self.text[start : self.cursor])
        span = self.selection()
        cols = None
        if span:
            left = max(span[0], start)
            right = min(span[1], end)
            if left < right:
                cols = (cell_width(self.text[start:left]), cell_width(self.text[start:right]))
        return shown, cursor_col, cols
