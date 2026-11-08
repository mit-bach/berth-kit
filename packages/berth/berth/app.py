"""Keys, modes, and the intents the engine has not run yet."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from berth.catalog import (
    CatalogError,
    Snapshot,
    delete_berth,
    insert_intent,
    load,
    mkdir_under,
    move_berth,
    move_folder,
    remove_folder,
    rename_folder,
)

from berth.input import TEXT_MODES, click_row, normalize
from berth.lineedit import CANCEL, CHANGED, SUBMIT, LineEditor
from berth.model import is_idle, is_sleeping
from berth.render import HELP_LINES, apply_click, excerpt_lines, render
from berth.tree import SORTS, Counts, Row, berth_as_row, build_account_tree, build_tree, resolve_folder

FILTERS = ("all", "live", "live + idle", "idle", "live + sleeping", "sleeping", "parked")
# Escape often never arrives from cmux. These keys close help and the excerpt.
CLOSE_OVERLAY = frozenset({"esc", "q", "x", "space", "backspace", "enter", "?"})


class App:
    def __init__(
        self,
        db_path: Path | str,
        *,
        width: int = 96,
        height: int = 24,
        opened_at: datetime | None = None,
        clock=None,
    ) -> None:
        self.db_path = str(db_path)
        self.width = width
        self.height = height
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        if opened_at is None:
            opened_at = self.clock()
        if opened_at.tzinfo is None:
            opened_at = opened_at.replace(tzinfo=timezone.utc)
        self.opened_at = opened_at.astimezone(timezone.utc)
        self.mode = "tree"
        self.query = ""
        self.prompt = ""
        # The text box under the hint line while naming or searching.
        self.editor: LineEditor | None = None
        self.message = ""
        self.noted_intent: tuple[str, str, str] | None = None
        self.pending_id: str | None = None
        self.pending_op = ""  # the close a busy session waits on y for (confirm-busy)
        self.cursor: tuple[str, str] | None = None
        self.top = 0
        self.excerpt_top = 0
        self.help_top = 0
        self.collapsed: set[str] = set()
        self.marked: set[str] = set()
        self.marked_folders: set[str] = set()
        from berth.sidebar import read_sidebar, read_sort

        self.sort = read_sort(Path(self.db_path).parent)
        # How the cmux sidebar is ordered: the tree's order, or folder by folder (o).
        self.sidebar = read_sidebar(Path(self.db_path).parent)
        # The account view (a): this screen's sessions grouped by Claude account. This screen's alone: it never
        # changes cmux's sidebar (Dominik, 2026-11-05), and a new screen opens on the tree.
        self.accounts_view = False
        self._parents: dict[str, str | None] = {}
        self.pending_park: list[str] = []
        # The trash view (ctrl-t): what was deleted in the last 30 days.
        self.trash_items: list[dict] = []
        self.trash_cursor = 0
        self.filter = "all"
        # This screen's own cmux workspace, and the last switch to it already handled (berth/arrival.py).
        self.workspace_id = os.environ.get("CMUX_WORKSPACE_ID", "")
        self.arrival_seen: str | None = None
        self.mkdir_parent: str | None = None
        self.delete_banner = ""
        self.pending_delete: list[str] = []
        self.pending_force: list[str] = []  # protected rows the operator may delete anyway with !
        self.quit = False
        self.problem: str | None = None
        self.snap: Snapshot | None = None
        self.rows: list[Row] = []
        self.visible: list[Row] = []
        self.counts = Counts()
        self.reload()

    def lines(self):
        return render(self)

    def text(self) -> str:
        return "\n".join(line.text.rstrip() for line in self.lines())

    def set_size(self, width: int, height: int) -> None:
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        self._pin()

    @property
    def body_height(self) -> int:
        return max(1, self.height - 3)

    @property
    def current(self) -> Row | None:
        for row in self.visible:
            if (row.kind, row.id) == self.cursor:
                return row
        return None

    def index(self) -> int:
        return self._index()

    def focus_berth(self, berth_id: str) -> None:
        berth = self.snap.berths.get(berth_id) if self.snap else None
        if berth is None:
            raise KeyError(berth_id)
        shown = next((row for row in self.rows if row.kind == "berth" and row.id == berth_id), None)
        self._expand_to(shown.container_id if shown is not None else berth.folder_id)
        self.cursor = ("berth", berth_id)
        self._rebuild()

    def focus_folder(self, folder_id: str) -> None:
        folder = self.snap.folders.get(folder_id) if self.snap else None
        if folder is None:
            raise KeyError(folder_id)
        self._expand_to(folder.parent_id)
        self.cursor = ("folder", folder_id)
        self._rebuild()

    @property
    def filter_label(self) -> str:
        sort = "last used" if self.sort == "recent" else "by name"
        if getattr(self, "accounts_view", False):
            sort = "by account · " + sort
        if getattr(self, "sidebar", "tree") == "folders":
            sort += " · sidebar by folder"
        if self.filter == "all":
            return sort
        return f"{sort} · filter {self.filter}"

    def tick(self) -> None:
        """The 400 ms read. A timeout in the terminal calls this."""
        self.reload()

    def reload(self) -> None:
        try:
            snap = load(self.db_path)
        except CatalogError as exc:
            self._fail(str(exc))
            return
        except sqlite3.Error:
            self._fail("catalog unreadable")
            return
        self.problem = None
        before = self.snap.intents if self.snap is not None else []
        self.snap = snap
        self._rebuild()
        self._report_failed(before)
        self._release_drained_intent()
        self._follow_arrival()

    def _follow_arrival(self) -> None:
        """You switched to this screen from a session: the cursor goes to that session's row.

        Only in the tree; a text box, the trash, or the help keep their place,
        and the switch still counts as handled so it cannot move the cursor later.
        """
        from berth.arrival import arrived_from

        found = arrived_from(Path(self.db_path).parent, self.workspace_id, self.clock(), self.arrival_seen)
        if found is None:
            return
        self.arrival_seen, previous = found
        if self.mode != "tree" or not previous or self.snap is None:
            return
        target = next((b for b in self.snap.berths.values() if b.workspace_id.lower() == previous.lower()), None)
        if target is None:
            return
        if not any(row.kind == "berth" and row.id == target.id for row in self._view_filter(self.rows)):
            return  # the filter or a search hides it; the cursor stays
        self.focus_berth(target.id)  # opens folded folders on the way

    def _report_failed(self, before) -> None:
        """An intent that left the queue with a failure: say what failed and why."""
        gone = {item.id: item for item in before} if before else {}
        for item in self.snap.intents:
            gone.pop(item.id, None)
        if not gone:
            return
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            try:
                marks = ",".join("?" for _ in gone)
                rows = conn.execute(
                    f"SELECT detail FROM event WHERE kind = 'intent_failed' AND berth_id IN ({marks}) ORDER BY at",
                    [item.berth_id for item in gone.values()],
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            return
        for (raw,) in rows:
            try:
                detail = json.loads(raw or "{}")
            except json.JSONDecodeError:
                continue
            item = gone.get(detail.get("intent_id"))
            if item is None:
                continue
            title = item.detail.get("title") or ""
            self.message = f"{item.op} failed · {title} · {detail.get('message') or detail.get('code')}"
            if self.noted_intent and self.noted_intent[1] == item.berth_id:
                self.noted_intent = None

    def handle(self, key: str) -> None:
        key = normalize(key)
        if not key or key == "resize":
            return
        if self.problem:
            if key == "q":
                self.quit = True
            return
        painted = click_row(key)
        if painted is not None:
            self.click_at(painted)
            return
        dispatch = {
            "tree": self._on_tree,
            "search": self._on_text,
            "mkdir": self._on_text,
            "rename": self._on_text,
            "move": self._on_text,
            "excerpt": self._on_excerpt,
            "confirm": self._on_confirm,
            "confirm-play": self._on_confirm_play,
            "confirm-cycle": self._on_confirm_cycle,
            "confirm-busy": self._on_confirm_busy,
            "confirm-cycle-many": self._on_confirm_cycle_many,
            "confirm-delete": self._on_confirm_delete,
            "confirm-rmdir": self._on_confirm_rmdir,
            "confirm-park": self._on_confirm_park,
            "trash": self._on_trash,
            "confirm-sleep": self._on_confirm_sleep,
            "confirm-forget": self._on_confirm_forget,
            "retitle": self._on_text,
            "help": self._on_help,
        }
        dispatch[self.mode](key)

    # Keys that change folders. The account view shows copies of folders, one per account, so they stay in the tree.
    FOLDER_KEYS = frozenset({"n", "r", "m", "d", "p", "S", "i", "v"})

    def _on_tree(self, key: str) -> None:
        if key == "q":
            self.quit = True
            return
        if self.accounts_view and key in self.FOLDER_KEYS:
            row = self.current
            if key in {"n", "r"} or (row is not None and row.kind == "folder"):
                what = "an account heading" if row is not None and row.heading == "account" else "a folder"
                self.message = (f"account view · {what} here is a view of the tree, not the folder itself · "
                                "a goes back to the tree to change folders")
                return
        if key in {"j", "down"}:
            self._move(1)
        elif key in {"k", "up"}:
            self._move(-1)
        elif key in {"ctrl-d", "pagedown"}:
            self._move(max(1, self.body_height // 2))
        elif key in {"ctrl-u", "pageup"}:
            self._move(-max(1, self.body_height // 2))
        elif key in {"g", "home"}:
            self._edge(False)
        elif key in {"G", "end"}:
            self._edge(True)
        elif key == "ctrl-c":
            self.quit = True
        elif key == "ctrl-t":
            self._open_trash()
        elif key == "S":
            self._sleep()
        elif key == "i":
            self._idle()
        elif key == "Z":
            self._sleep_everything()
        elif key == "W":
            self._wake_all()
        elif key in {"h", "left"}:
            self._collapse()
        elif key in {"l", "right"}:
            self._open()
        elif key == "enter":
            self._enter_row()
        elif key == "p":
            self._pause()
        elif key == "s":
            self._cycle_sort()
        elif key == "o":
            self._toggle_sidebar()
        elif key == "a":
            self._toggle_accounts()
        elif key == "F":
            self._fork()
        elif key == "c":
            self._cycle()
        elif key == "space":
            self._open_excerpt()
        elif key == "/":
            self.mode = "search"
            self.editor = LineEditor(self.query)
            self.message = ""
        elif key == "n":
            self._start_mkdir()
        elif key == "r":
            self._start_rename()
        elif key == "t":
            self._start_retitle()
        elif key == "v":
            self._toggle_mark()
        elif key == "d":
            self._delete()
        elif key == "f":
            self._cycle_filter()
        elif key == "m":
            self._start_move()
        elif key == "P":
            self._toggle()
        elif key == "?":
            self.mode = "help"
            self.help_top = 0
            self.message = ""
        elif key == "esc" and self.query:
            self.query = ""
            self.message = ""
            self._rebuild()
        elif key == "esc" and (self.marked or self.marked_folders):
            self.marked.clear()
            self.marked_folders.clear()
            self.message = "move cancelled"

    def _on_text(self, key: str) -> None:
        """Every text box: new folder, rename folder, rename session, move, search.

        The box is a LineEditor, so it edits like a prompt box. Search also
        keeps the arrows that walk the list: up and down, page up and down.
        """
        editor = self.editor
        if editor is None:
            editor = self.editor = LineEditor(self.query if self.mode == "search" else self.prompt)
        if key.startswith("mouse:"):
            self._box_mouse(editor, key)
            return
        if self.mode == "search" and key in {"up", "down", "pageup", "pagedown"}:
            step = {"up": -1, "down": 1, "pageup": -max(1, self.body_height // 2), "pagedown": max(1, self.body_height // 2)}
            self._move(step[key])
            return
        result = editor.handle(key)
        if self.mode == "search":
            self.query = editor.text
        else:
            self.prompt = editor.text
        if editor.note:
            self.message = editor.note
        elif result == CHANGED:
            self.message = ""
        if result == CHANGED and self.mode == "search":
            self._rebuild()
        if result == SUBMIT:
            self._submit_text()
        elif result == CANCEL:
            self._cancel_text()
        if self.mode not in TEXT_MODES:
            self.editor = None

    def _box_mouse(self, editor: LineEditor, key: str) -> None:
        """Click, drag, double and triple click in the box, as in Claude Code's prompt."""
        import time

        from berth.input import mouse_parts
        from berth.render import BOX_ROW, box_origin

        parts = mouse_parts(key)
        if parts is None:
            return
        kind, x, y = parts
        column = x - box_origin(self)
        if kind == "press":
            if y != BOX_ROW:
                return
            editor.press(column, time.monotonic())
        elif kind == "drag":
            editor.drag(column)
        else:
            editor.release(column)
        if editor.note:
            self.message = editor.note

    def _submit_text(self) -> None:
        if self.mode == "search":
            self.mode = "tree"
            self._rebuild()
            return
        {
            "mkdir": self._commit_mkdir,
            "rename": self._commit_rename,
            "retitle": self._commit_retitle,
            "move": self._commit_move,
        }[self.mode]()

    def _cancel_text(self) -> None:
        if self.mode == "search":
            self.query = ""
            self.mode = "tree"
            self.message = ""
            self._rebuild()
            return
        self.mode = "tree"
        self.prompt = ""
        self.pending_id = None
        self.message = "cancelled"

    def _on_excerpt(self, key: str) -> None:
        if key in {"space", "esc", "q", "x"}:
            self._close_frame()
            return
        if key in {"j", "ctrl-d"}:
            step = 1 if key == "j" else max(1, self._excerpt_window() // 2)
            self._scroll_excerpt(step)
            return
        if key in {"k", "ctrl-u"}:
            step = 1 if key == "k" else max(1, self._excerpt_window() // 2)
            self._scroll_excerpt(-step)

    def _on_confirm(self, key: str) -> None:
        if key in {"j", "ctrl-d"}:
            step = 1 if key == "j" else max(1, self._excerpt_window() // 2)
            self._scroll_excerpt(step)
            return
        if key in {"k", "ctrl-u"}:
            step = 1 if key == "k" else max(1, self._excerpt_window() // 2)
            self._scroll_excerpt(-step)
            return
        if key == "y":
            self._accept("pause")
            return
        if key in {"n", "esc"}:
            self._cancel("pause")

    def _on_confirm_play(self, key: str) -> None:
        if key in {"j", "ctrl-d"}:
            step = 1 if key == "j" else max(1, self._excerpt_window() // 2)
            self._scroll_excerpt(step)
            return
        if key in {"k", "ctrl-u"}:
            step = 1 if key == "k" else max(1, self._excerpt_window() // 2)
            self._scroll_excerpt(-step)
            return
        if key == "y":
            self._accept("play")
            return
        if key in {"n", "esc"}:
            self._cancel("play")

    def _on_confirm_cycle(self, key: str) -> None:
        if key in {"j", "ctrl-d"}:
            step = 1 if key == "j" else max(1, self._excerpt_window() // 2)
            self._scroll_excerpt(step)
            return
        if key in {"k", "ctrl-u"}:
            step = 1 if key == "k" else max(1, self._excerpt_window() // 2)
            self._scroll_excerpt(-step)
            return
        if key == "y":
            self._accept("cycle")
            return
        if key in {"n", "esc"}:
            self._cancel("cycle")

    def _ask_busy(self, op: str, row: Row) -> None:
        """A busy session: p, S, or c shows why it is busy and closes it only on y (2026-10-29).

        Before, the screen recorded the close unconfirmed and the engine refused it,
        so a busy or unclear session could not be cycled from here at all.
        """
        self.pending_id = row.id
        self.pending_op = op
        self.excerpt_top = 0
        self.mode = "confirm-busy"
        self.message = ""

    def _on_confirm_busy(self, key: str) -> None:
        if key in {"j", "ctrl-d", "k", "ctrl-u"}:
            step = 1 if key in {"j", "k"} else max(1, self._excerpt_window() // 2)
            self._scroll_excerpt(step if key in {"j", "ctrl-d"} else -step)
            return
        op = getattr(self, "pending_op", "") or "pause"
        if key == "y":
            self._accept(op)
            return
        if key in {"n", "esc"}:
            self._cancel(op)

    def _on_help(self, key: str) -> None:
        if key in CLOSE_OVERLAY:
            self._close_frame()
            return
        if key in {"j", "ctrl-d"}:
            self._scroll_help(1 if key == "j" else max(1, self.body_height // 2))
            return
        if key in {"k", "ctrl-u"}:
            self._scroll_help(-1 if key == "k" else -max(1, self.body_height // 2))

    def _enter_row(self) -> None:
        if self.marked:
            self._enter_marked()
            return
        row = self.current
        if row is None or row.kind != "berth":
            self.message = "enter plays or focuses a berth"
            return
        if row.state in {"playing", "pausing"}:
            self.message = f"in progress · {row.title}"
            return
        if row.berth_kind == "shell":
            self._record(
                "play",
                row,
                message=f"play recorded · {row.title} · opens a shell, not a Grok session",
            )
            return
        if row.state == "parked" and row.berth_kind == "process":
            self.pending_id = row.id
            self.excerpt_top = 0
            self.mode = "confirm-play"
            self.message = ""
            return
        if row.state == "parked":
            self._record("play", row)
            return
        if row.state == "live":
            self._record("focus", row)
            return
        self.message = f"no action · {row.state}"

    def _pause(self) -> None:
        row = self.current
        if row is not None and row.kind == "folder":
            self._park_folder(row)
            return
        if row is None or row.kind != "berth":
            self.message = "p pauses a berth"
            return
        if row.state == "parked" and (is_sleeping(row.state, row.park_reason) or is_idle(row.state, row.park_reason)):
            self._relabel(row, "pause", f"paused · {row.title} · it stays closed until you open it")
            return
        if row.state == "parked":
            self.message = f"already parked · {row.title}"
            return
        if row.state in {"playing", "pausing"}:
            self.message = f"in progress · {row.title}"
            return
        if row.state != "live":
            self.message = f"no pause · {row.title}"
            return
        if row.berth_kind == "process":
            self.pending_id = row.id
            self.excerpt_top = 0
            self.mode = "confirm"
            self.message = ""
            return
        if row.busy:
            self._ask_busy("pause", row)
            return
        self._record("pause", row, {"busy": row.busy, "confirm": False})
        self._step_up_from(row.id)

    def _open_excerpt(self) -> None:
        row = self.current
        if row is None or row.kind != "berth":
            self.message = "space reads a berth"
            return
        self.pending_id = row.id
        self.excerpt_top = 0
        self.mode = "excerpt"
        self.message = ""

    def _accept(self, op: str) -> None:
        row = berth_as_row(self.snap, self.pending_id, self.opened_at) if self.snap else None
        self.mode = "tree"
        self.pending_id = None
        self.excerpt_top = 0
        if row is None:
            self.message = "berth is gone"
            return
        extra = {"confirm": True}
        if op in {"pause", "cycle", "sleep", "idle"}:
            extra["busy"] = row.busy
        self._record(op, row, extra)
        if op in {"pause", "sleep", "idle"}:
            self._step_up_from(row.id)

    def _cancel(self, op: str) -> None:
        title = ""
        if self.pending_id and self.snap and self.pending_id in self.snap.berths:
            title = self.snap.berths[self.pending_id].title
        self.mode = "tree"
        self.pending_id = None
        self.excerpt_top = 0
        self.message = f"{op} cancelled · {title}".strip()

    def _close_frame(self) -> None:
        self.mode = "tree"
        self.pending_id = None
        self.excerpt_top = 0
        self.help_top = 0
        self.message = ""

    def _record(self, op: str, row: Row, extra: dict | None = None, message: str | None = None) -> None:
        # One pending intent per operation and berth. Enter repeat uses the same key.
        # Pause and play do not change berth.state. The engine runs the socket call.
        if any(item.op == op and item.berth_id == row.id for item in self.snap.intents):
            if message and "shell" in message:
                self._show_intent(op, row.id, f"already recorded · {row.title} · opens a shell, not a Grok session")
            else:
                self._show_intent(op, row.id, f"already recorded · {op} · {row.title}")
            return
        detail = {"kind": row.berth_kind, "state": row.state, "title": row.title}
        if extra:
            detail.update(extra)
        insert_intent(self.db_path, op, row.id, detail, self.clock())
        self.reload()
        if message:
            text = message
        elif op == "pause":
            text = f"pause recorded · {row.title} · the engine has the intent"
        elif op == "play":
            text = f"play recorded · {row.title}"
        elif op == "focus":
            text = f"focus recorded · {row.title}"
        else:
            text = f"{op} recorded · {row.title}"
        self._show_intent(op, row.id, text)

    def _show_intent(self, op: str, berth_id: str, text: str) -> None:
        self.noted_intent = (op, berth_id, text)
        self.message = text

    def _release_drained_intent(self) -> None:
        noted = self.noted_intent
        if noted is None or self.snap is None:
            return
        op, berth_id, text = noted
        if any(item.op == op and item.berth_id == berth_id for item in self.snap.intents):
            return
        self.noted_intent = None
        if self.message == text:
            self.message = ""
        if self.snap is None:
            return
        intents = self.snap.intents
        if self.message.startswith("play recorded") and not any(item.op == "play" for item in intents):
            self.message = ""
        elif self.message.startswith("pause recorded") and not any(item.op == "pause" for item in intents):
            self.message = ""
        elif self.message.startswith("delete recorded") and not any(item.op == "delete" for item in intents):
            self.message = ""
        elif self.message.startswith("already recorded") and self.noted_intent is None:
            self.message = ""

    def _start_mkdir(self) -> None:
        """A new folder goes inside the folder under the cursor.

        On a berth it goes inside that berth's folder. Inbox holds no
        folders, so there it goes to the root. ``a/b`` makes both levels.
        A leading ``/`` starts from the root.
        """
        self.prompt = ""
        self.editor = LineEditor("")
        self.mode = "mkdir"
        self.message = ""
        self.mkdir_parent = self._folder_at_cursor()

    def _folder_at_cursor(self) -> str | None:
        row = self.current
        if row is None or self.snap is None:
            return None
        folder_id = row.id if row.kind == "folder" else row.container_id
        folder = self.snap.folders.get(folder_id) if folder_id else None
        if folder is None or (folder.parent_id is None and folder.name == "Inbox"):
            return None
        return folder.id

    @property
    def mkdir_where(self) -> str:
        parent = getattr(self, "mkdir_parent", None)
        if self.prompt.startswith("/") or not parent or self.snap is None:
            return "/"
        return self.snap.folder_path(parent) + "/"

    def _commit_mkdir(self) -> None:
        text = self.prompt.strip()
        parent = getattr(self, "mkdir_parent", None)
        if text.startswith("/"):
            parent = None
        if not text.strip("/").strip():
            self.message = "name the folder"
            return
        try:
            new_id = mkdir_under(self.db_path, parent, text, self.clock())
        except CatalogError as exc:
            self.message = str(exc)
            return
        self.mode = "tree"
        self.prompt = ""
        self.mkdir_parent = None
        self.reload()
        if new_id:
            self.focus_folder(new_id)
        path = self.snap.folder_path(new_id) if self.snap and new_id else text
        self.message = f"folder · {path}"

    def _start_rename(self) -> None:
        row = self.current
        if row is None or row.kind != "folder":
            self.message = "r renames a folder"
            return
        folder = self.snap.folders.get(row.id) if self.snap else None
        if folder is not None and folder.parent_id is None and folder.name == "Inbox":
            # Berth finds Inbox by its name.
            self.message = "Inbox keeps its name"
            return
        self.pending_id = row.id
        self.prompt = row.title
        self.editor = LineEditor(row.title)
        self.mode = "rename"
        self.message = ""

    def _commit_rename(self) -> None:
        name = self.prompt.strip()
        folder_id = self.pending_id
        if not folder_id:
            self.mode = "tree"
            self.message = "folder is gone"
            return
        if not name:
            self.message = "name the folder"
            return
        if "/" in name:
            self.message = "folders are one level"
            return
        try:
            rename_folder(self.db_path, folder_id, name, self.clock())
        except CatalogError as exc:
            self.message = str(exc)
            return
        self.mode = "tree"
        self.prompt = ""
        self.pending_id = None
        self.reload()
        self.focus_folder(folder_id)
        self.message = f"renamed · {name}"

    def _start_move(self) -> None:
        """Pick up, or drop.

        With nothing marked, ``m`` on a folder picks that folder up. With rows
        marked (``v`` marks sessions and folders), ``m`` on a folder drops
        every marked session and folder into it, and clears the marks. Folders
        dropped on Inbox go to the top level, since Inbox holds no folders.
        """
        if self.snap is None:
            return
        row = self.current
        carrying = bool(self.marked or self.marked_folders)
        if not carrying and row is not None and row.kind == "folder":
            self._pick_up_folder(row)
            return
        if not carrying:
            self.message = "v marks the rows, scroll to the folder, then m"
            return
        if row is None or row.kind != "folder":
            self.message = "arrows to the destination folder, then m · esc cancels"
            return
        self._drop_on(row.id)

    def _pick_up_folder(self, row) -> None:
        folder = self.snap.folders.get(row.id)
        if folder is None:
            return
        if folder.parent_id is None and folder.name == "Inbox":
            self.message = "Inbox stays"
            return
        self.marked_folders.add(row.id)
        self.message = f"moving {folder.name} · arrows to the destination folder, m drops it · Inbox is the top level · esc cancels"

    def _drop_on(self, target: str) -> None:
        target_folder = self.snap.folders.get(target)
        to_top = target_folder is not None and target_folder.parent_id is None and target_folder.name == "Inbox"
        dest = self.snap.folder_path(target)
        moved = 0
        refused: list[str] = []
        for folder_id in list(self.marked_folders):
            folder = self.snap.folders.get(folder_id)
            if folder is None or folder.parent_id == (None if to_top else target):
                continue
            try:
                move_folder(self.db_path, folder_id, target, self.clock())
                moved += 1
            except CatalogError as exc:
                refused.append(f"{folder.name}: {exc}")
        for berth_id in list(self.marked):
            berth = self.snap.berths.get(berth_id)
            if berth is None or berth.kind == "berth" or berth.folder_id == target:
                continue
            move_berth(self.db_path, berth_id, target, self.clock())
            moved += 1
        self.mode = "tree"
        self.prompt = ""
        self.pending_id = None
        self.reload()
        self.focus_folder(target)
        if self.marked_folders and to_top and not self.marked:
            dest = "top level"
        self.marked.clear()
        self.marked_folders.clear()
        self.message = f"moved · {moved} · {dest}" + (" · refused " + " · ".join(refused) if refused else "")

    def _commit_move(self) -> None:
        if self.marked and self.pending_id is None:
            self._commit_move_marked()
            return
        berth_id = self.pending_id
        if not self.snap or not berth_id or berth_id not in self.snap.berths:
            self.mode = "tree"
            self.pending_id = None
            self.message = "berth is gone"
            return
        needle = self.prompt.strip().strip("/")
        if not needle:
            self.message = "name the folder"
            return
        found = resolve_folder(self.snap, needle)
        if found.status == "none":
            self.message = "no folder"
            return
        if found.status == "many":
            self.message = "ambiguous · " + " · ".join(found.matches[:6])
            return
        berth = self.snap.berths[berth_id]
        target = found.folder_id or ""
        if berth.folder_id == target:
            dest = self.snap.folder_path(target)
            self.mode = "tree"
            self.prompt = ""
            self.pending_id = None
            self.message = f"already in {dest}"
            return
        try:
            move_berth(self.db_path, berth_id, target, self.clock())
        except CatalogError as exc:
            self.message = str(exc)
            return
        dest = self.snap.folder_path(target)
        title = berth.title
        self.mode = "tree"
        self.prompt = ""
        self.pending_id = None
        self.reload()
        self.focus_berth(berth_id)
        self.message = f"moved · {title} · {dest}"

    def _commit_move_marked(self) -> None:
        if not self.snap:
            return
        needle = self.prompt.strip().strip("/")
        if not needle:
            self.message = "name the folder"
            return
        found = resolve_folder(self.snap, needle)
        if found.status == "none":
            self.message = "no folder"
            return
        if found.status == "many":
            self.message = "ambiguous · " + " · ".join(found.matches[:6])
            return
        target = found.folder_id or ""
        dest = self.snap.folder_path(target)
        moved = 0
        for berth_id in list(self.marked):
            berth = self.snap.berths.get(berth_id)
            if berth is None or berth.folder_id == target:
                continue
            move_berth(self.db_path, berth_id, target, self.clock())
            moved += 1
        self.mode = "tree"
        self.prompt = ""
        self.reload()
        self.message = f"moved · {moved} · {dest}"

    def _view_filter(self, rows: list[Row]) -> list[Row]:
        if self.filter == "all" or self.snap is None:
            return rows
        keep: set[str] = set()
        for row in rows:
            if row.kind != "berth" or not self._berth_in_filter(row):
                continue
            keep.add(row.id)
            current = row.container_id
            seen: set[str] = set()
            while current and current not in seen and self.snap is not None:
                keep.add(current)
                seen.add(current)
                current = self._parent(current) if self._known(current) else None
        return [row for row in rows if row.id in keep]

    def _berth_in_filter(self, row: Row) -> bool:
        if self.filter == "live":
            return row.state == "live"
        if self.filter == "live + idle":
            # What is open, plus what was closed to free memory and is in use again soon (2026-11-05).
            return row.state == "live" or is_idle(row.state, row.park_reason)
        if self.filter == "idle":
            return is_idle(row.state, row.park_reason)
        if self.filter == "live + sleeping":
            # What is open, plus what is closed only for now.
            return row.state == "live" or is_sleeping(row.state, row.park_reason)
        if self.filter == "sleeping":
            return is_sleeping(row.state, row.park_reason)
        if self.filter == "parked":
            # Paused: put away. Idle and sleeping rows have their own filters.
            return (row.state == "parked" and not is_sleeping(row.state, row.park_reason)
                    and not is_idle(row.state, row.park_reason))
        return True

    def _enter_marked(self) -> None:
        """Play every marked parked session: Claude, Grok, other agents, and shells.

        A process is not rerun from a mark; it needs its own Enter and confirm.
        The first play takes focus, the rest open behind it. The marks clear.
        """
        if self.snap is None:
            return
        count = 0
        skipped = 0
        for berth_id in sorted(self.marked, key=lambda item: self._visible_index(item)):
            berth = self.snap.berths.get(berth_id)
            if berth is None or berth.state != "parked" or berth.kind == "berth":
                continue
            if berth.kind == "process":
                skipped += 1
                continue
            row = berth_as_row(self.snap, berth_id, self.opened_at)
            if row is None:
                continue
            if any(item.op == "play" and item.berth_id == berth_id for item in self.snap.intents):
                continue
            detail = {"kind": berth.kind, "state": berth.state, "title": berth.title, "focus": count == 0}
            insert_intent(self.db_path, "play", berth_id, detail, self.clock())
            count += 1
        if count == 0 and not skipped:
            self.message = "no parked session in the marks"
            return
        self.marked.clear()
        self.reload()
        word = "session" if count == 1 else "sessions"
        text = f"play recorded · {count} {word}"
        if skipped:
            text += f" · {skipped} process left: enter on it to confirm"
        self.message = text

    def _visible_index(self, berth_id: str) -> int:
        for index, row in enumerate(self.visible):
            if row.kind == "berth" and row.id == berth_id:
                return index
        return len(self.visible)

    def _toggle_mark(self) -> None:
        row = self.current
        if row is not None and row.kind == "folder":
            folder = self.snap.folders.get(row.id) if self.snap else None
            if folder is None or (folder.parent_id is None and folder.name == "Inbox"):
                self.message = "Inbox stays"
                return
            if row.id in self.marked_folders:
                self.marked_folders.discard(row.id)
                self.message = f"unmarked · {row.title}"
            else:
                self.marked_folders.add(row.id)
                self.message = f"marked · {row.title} · arrows to a folder, then m"
            return
        if row is None or row.kind != "berth":
            self.message = "v marks a berth or a folder"
            return
        if row.id in self.marked:
            self.marked.discard(row.id)
            self.message = f"unmarked · {row.title}"
        else:
            self.marked.add(row.id)
            self.message = f"marked · {row.title}"

    def _fork(self) -> None:
        """Record a fork. The engine opens it next to the original, titled with ⑂."""
        row = self.current
        if row is None or row.kind != "berth":
            self.message = "F forks a Claude or Grok session"
            return
        claude = row.berth_kind == "other-agent" and (row.agent_name or "").casefold() == "claude"
        if row.berth_kind != "grok" and not claude:
            self.message = f"only Claude and Grok sessions fork · {row.title}"
            return
        self._record("fork", row, message=f"fork recorded · {row.title} ⑂ opens next to it")

    def _cycle(self) -> None:
        """Turn a live session off and on again: the engine pauses it, then plays it.

        The same session comes back in a new workspace in the same folder; the
        screen keeps focus. With marks, every marked live session cycles. A
        process reruns its command, so it asks first, and marks skip it.
        """
        if self.marked:
            self._cycle_marked()
            return
        row = self.current
        if row is None or row.kind != "berth":
            self.message = "c cycles a live session · v marks several"
            return
        if row.berth_kind == "berth" or row.title == "Berth":
            self.message = "the Berth screen is not cycled"
            return
        if row.state in {"playing", "pausing"}:
            self.message = f"in progress · {row.title}"
            return
        if row.state != "live":
            self.message = f"parked · {row.title} · enter opens it"
            return
        if row.berth_kind == "process":
            self.pending_id = row.id
            self.excerpt_top = 0
            self.mode = "confirm-cycle"
            self.message = ""
            return
        if row.busy:
            self._ask_busy("cycle", row)
            return
        self._record(
            "cycle", row, {"busy": row.busy, "confirm": False},
            message=f"cycle recorded · {row.title} closes and comes back",
        )

    def _cycle_marked(self) -> None:
        """c with marks: cycle every marked live session. When any is busy, one y covers them all first."""
        if self.snap is None:
            return
        chosen = []
        processes = 0
        for berth_id in sorted(self.marked, key=lambda item: self._visible_index(item)):
            berth = self.snap.berths.get(berth_id)
            if berth is None or berth.state != "live" or berth.kind == "berth" or berth.title == "Berth":
                continue
            if berth.kind == "process":
                processes += 1
                continue
            if any(item.op == "cycle" and item.berth_id == berth_id for item in self.snap.intents):
                continue
            chosen.append(berth)
        if not chosen and not processes:
            self.message = "no live session in the marks"
            return
        busy = [berth for berth in chosen if berth.busy]
        if busy:
            word = "session" if len(chosen) == 1 else "sessions"
            names = ", ".join(berth.title for berth in busy[:3]) + (" ..." if len(busy) > 3 else "")
            self.delete_banner = (f"cycle · {len(chosen)} marked {word} · {len(busy)} busy ({names}): "
                                  "their work is cut off · y cycles them · n cancels")
            self.pending_park = [berth.id for berth in chosen]
            self.pending_processes = processes
            self.mode = "confirm-cycle-many"
            self.message = ""
            return
        self._record_cycles(chosen, processes, confirm=False)

    def _record_cycles(self, chosen: list, processes: int, *, confirm: bool) -> None:
        for berth in chosen:
            detail = {"kind": berth.kind, "state": berth.state, "title": berth.title, "busy": berth.busy,
                      "confirm": confirm}
            insert_intent(self.db_path, "cycle", berth.id, detail, self.clock())
        self.marked.clear()
        self.reload()
        word = "session" if len(chosen) == 1 else "sessions"
        text = f"cycle recorded · {len(chosen)} {word}"
        if processes:
            text += f" · {processes} process left: c on it to confirm"
        self.message = text

    def _on_confirm_cycle_many(self, key: str) -> None:
        if key in {"n", "esc"}:
            self.mode = "tree"
            self.pending_park = []
            self.delete_banner = ""
            self.message = "cycle cancelled"
            return
        if key != "y" or self.snap is None:
            return
        chosen = [self.snap.berths[berth_id] for berth_id in self.pending_park
                  if berth_id in self.snap.berths and self.snap.berths[berth_id].state == "live"]
        self.mode = "tree"
        self.pending_park = []
        self.delete_banner = ""
        self._record_cycles(chosen, getattr(self, "pending_processes", 0), confirm=True)

    def _cycle_sort(self) -> None:
        index = SORTS.index(self.sort)
        self.sort = SORTS[(index + 1) % len(SORTS)]
        self.message = "sort · last used, newest first" if self.sort == "recent" else "sort · by name"
        from berth.sidebar import write_sort

        # The watcher orders the cmux sidebar the same way.
        write_sort(Path(self.db_path).parent, self.sort)
        self._rebuild()

    def _toggle_sidebar(self) -> None:
        """o: the cmux sidebar follows the tree, or goes folder by folder. The tree here does not change."""
        from berth.sidebar import write_sidebar

        self.sidebar = "folders" if self.sidebar == "tree" else "tree"
        write_sidebar(Path(self.db_path).parent, self.sidebar)
        if self.sidebar == "folders":
            self.message = ("sidebar · folder by folder: each folder (any depth) by its own newest session, "
                            "its subfolders don't move it · o switches back")
        else:
            self.message = "sidebar · follows the tree: a folder rises with its newest session anywhere inside · o switches"

    def _toggle_accounts(self) -> None:
        """a: the account view on this screen, or back to the tree. Off unless asked for."""
        self.accounts_view = not self.accounts_view
        self._rebuild()
        if self.accounts_view:
            groups = sum(1 for row in self.rows if row.heading == "account")
            self.message = f"by account · {groups} account{'s' if groups != 1 else ''} · a goes back to the tree"
        else:
            self.message = "the tree · a groups by account"

    def _subtree_folders(self, folder_id: str) -> set[str]:
        found = {folder_id}
        changed = True
        while changed and self.snap is not None:
            changed = False
            for folder in self.snap.folders.values():
                if folder.parent_id in found and folder.id not in found:
                    found.add(folder.id)
                    changed = True
        return found

    def _park_folder(self, row) -> None:
        """Park every live session in this folder and in all of its subfolders.

        One confirm covers them all, so busy sessions and processes park too.
        The Berth screen itself, and the workspace this screen runs in, stay.
        """
        import os

        if self.snap is None:
            return
        folders = self._subtree_folders(row.id)
        here = (os.environ.get("CMUX_WORKSPACE_ID") or "").lower()
        live = [
            berth
            for berth in self.snap.berths.values()
            if berth.folder_id in folders
            and berth.state == "live"
            and berth.kind != "berth"
            and berth.title != "Berth"
            and (not here or (self._workspace_of(berth.id) or "").lower() != here)
        ]
        if not live:
            self.message = f"nothing live in {row.title} or its subfolders"
            return
        nested = len(folders) - 1
        where = f"{row.title} and its {nested} subfolder{'s' if nested != 1 else ''}" if nested else row.title
        busy = sum(1 for berth in live if berth.busy)
        word = "session" if len(live) == 1 else "sessions"
        bits = [f"park · {len(live)} live {word} in {where}"]
        if busy:
            bits.append(f"{busy} busy")
        bits.append("y parks them all · n cancels")
        self.delete_banner = " · ".join(bits)
        self.pending_park = [berth.id for berth in live]
        self.pending_id = row.id
        self.mode = "confirm-park"
        self.message = ""

    def _workspace_of(self, berth_id: str) -> str | None:
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            try:
                found = conn.execute("SELECT cmux_workspace_id FROM berth WHERE id = ?", (berth_id,)).fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            return None
        return found[0] if found else None

    def _on_confirm_park(self, key: str) -> None:
        if key in {"n", "esc"}:
            self.mode = "tree"
            self.pending_park = []
            self.pending_id = None
            self.delete_banner = ""
            self.message = "park cancelled"
            return
        if key != "y" or self.snap is None:
            return
        folder = self.snap.folders.get(self.pending_id or "")
        recorded = 0
        for berth_id in self.pending_park:
            berth = self.snap.berths.get(berth_id)
            if berth is None or berth.state != "live":
                continue
            if any(item.op == "pause" and item.berth_id == berth_id for item in self.snap.intents):
                continue
            detail = {"kind": berth.kind, "state": berth.state, "title": berth.title, "busy": berth.busy,
                      "confirm": True, "via": "folder"}
            insert_intent(self.db_path, "pause", berth_id, detail, self.clock())
            recorded += 1
        self.mode = "tree"
        self.pending_park = []
        self.pending_id = None
        self.delete_banner = ""
        self.reload()
        name = folder.name if folder else "folder"
        self.message = f"park recorded · {recorded} · {name} · the engine parks them now"

    # sleep ------------------------------------------------------------

    def _relabel(self, row, reason: str, message: str) -> None:
        """A closed session switches between sleeping and paused. Nothing opens or closes."""
        from berth import machine
        from berth.db import Catalog
        from berth.errors import BerthError

        try:
            db = Catalog(Path(self.db_path)).open()
            try:
                machine.transition(db, row.id, "relabel", reason=reason, log_kind=reason,
                                   detail={"was": row.park_reason, "via": "screen"})
            finally:
                db.close()
        except (sqlite3.Error, BerthError) as exc:
            self.message = f"could not change · {row.title} · {exc}"
            return
        self.reload()
        self.message = message

    def _sleepable(self, berth) -> bool:
        import os

        here = (os.environ.get("CMUX_WORKSPACE_ID") or "").lower()
        return (
            berth.state == "live"
            and berth.kind != "berth"
            and berth.title != "Berth"
            and (not here or (self._workspace_of(berth.id) or "").lower() != here)
        )

    def _sleep(self) -> None:
        """S: put the session under the cursor to sleep. With marks, the marked ones; on a folder, all of it."""
        if self.snap is None:
            return
        row = self.current
        if self.marked:
            chosen = [self.snap.berths[i] for i in self.marked if i in self.snap.berths]
            self._ask_sleep([b for b in chosen if self._sleepable(b)], f"{len(self.marked)} marked")
            return
        if row is not None and row.kind == "folder":
            folders = self._subtree_folders(row.id)
            live = [b for b in self.snap.berths.values() if b.folder_id in folders and self._sleepable(b)]
            self._ask_sleep(live, row.title + " and its subfolders" if len(folders) > 1 else row.title)
            return
        if row is None or row.kind != "berth":
            self.message = "S puts a session to sleep · Z puts every live session to sleep"
            return
        if row.berth_kind == "berth" or row.title == "Berth":
            self.message = "the Berth screen stays awake"
            return
        if row.state == "parked" and not is_sleeping(row.state, row.park_reason):
            self._relabel(row, "sleep", f"sleeping · {row.title} · W wakes it with the rest")
            return
        if is_sleeping(row.state, row.park_reason):
            self.message = f"already sleeping · {row.title} · W wakes it"
            return
        if row.state != "live":
            self.message = f"in progress · {row.title}"
            return
        if row.busy:
            self._ask_busy("sleep", row)
            return
        self._record("sleep", row, {"busy": row.busy, "confirm": True},
                     message=f"sleep recorded · {row.title} · W wakes it")
        self._step_up_from(row.id)

    def _idle(self) -> None:
        """i: make the session under the cursor idle (2026-11-05): closed to free its memory, in use again soon,
        shown with the live ones. With marks, the marked ones; on a folder, all of it. On a closed one: it becomes
        idle, and nothing opens."""
        if self.snap is None:
            return
        row = self.current
        if self.marked:
            chosen = [self.snap.berths[i] for i in self.marked if i in self.snap.berths]
            self._ask_sleep([b for b in chosen if self._sleepable(b)], f"{len(self.marked)} marked", op="idle")
            return
        if row is not None and row.kind == "folder":
            folders = self._subtree_folders(row.id)
            live = [b for b in self.snap.berths.values() if b.folder_id in folders and self._sleepable(b)]
            self._ask_sleep(live, row.title + " and its subfolders" if len(folders) > 1 else row.title, op="idle")
            return
        if row is None or row.kind != "berth":
            self.message = "i makes a session idle: closed to free memory, shown with the live ones"
            return
        if row.berth_kind == "berth" or row.title == "Berth":
            self.message = "the Berth screen stays open"
            return
        if is_idle(row.state, row.park_reason):
            self.message = f"already idle · {row.title} · enter opens it"
            return
        if row.state == "parked":
            self._relabel(row, "idle", f"idle · {row.title} · enter opens it")
            return
        if row.state != "live":
            self.message = f"in progress · {row.title}"
            return
        if row.busy:
            self._ask_busy("idle", row)
            return
        self._record("idle", row, {"busy": row.busy, "confirm": True},
                     message=f"idle recorded · {row.title} · its memory is freed · enter opens it")
        self._step_up_from(row.id)

    def _sleep_everything(self) -> None:
        """Z: every live session to sleep but the Berth screen, before an update or a cmux restart."""
        if self.snap is None:
            return
        self._ask_sleep([b for b in self.snap.berths.values() if self._sleepable(b)], "everything live")

    def _ask_sleep(self, live: list, where: str, op: str = "sleep") -> None:
        self.pending_close_op = op
        if not live:
            self.message = f"nothing live to {'make idle' if op == 'idle' else 'put to sleep'} in {where}"
            return
        busy = sum(1 for berth in live if berth.busy)
        word = "session" if len(live) == 1 else "sessions"
        bits = [f"{op} · {len(live)} live {word} · {where}"]
        if busy:
            bits.append(f"{busy} busy, their turns stop")
        lossy = sum(1 for berth in live if getattr(berth, "draft", "") not in ("", "saved"))
        if lossy:
            bits.append(f"{lossy} with a draft that cannot be saved exactly")
        bits.append("enter opens each again · y makes them idle · n cancels" if op == "idle"
                    else "W wakes them later · y sleeps them · n cancels")
        self.delete_banner = " · ".join(bits)
        self.pending_park = [berth.id for berth in live]
        self.mode = "confirm-sleep"
        self.message = ""

    def _on_confirm_sleep(self, key: str) -> None:
        if key in {"n", "esc"}:
            self.mode = "tree"
            self.pending_park = []
            self.delete_banner = ""
            self.message = "cancelled"
            return
        if key != "y" or self.snap is None:
            return
        op = getattr(self, "pending_close_op", "sleep") or "sleep"
        recorded = 0
        for berth_id in self.pending_park:
            berth = self.snap.berths.get(berth_id)
            if berth is None or berth.state != "live":
                continue
            if any(item.op == op and item.berth_id == berth_id for item in self.snap.intents):
                continue
            detail = {"kind": berth.kind, "state": berth.state, "title": berth.title, "busy": berth.busy,
                      "confirm": True, "via": f"{op}-all"}
            insert_intent(self.db_path, op, berth_id, detail, self.clock())
            self.marked.discard(berth_id)
            recorded += 1
        self.mode = "tree"
        self.pending_park = []
        self.delete_banner = ""
        self.reload()
        self.message = (f"idle recorded · {recorded} · they close now, freeing memory · enter opens each" if op == "idle"
                        else f"sleep recorded · {recorded} · they close now · W wakes them")

    def _wake_all(self) -> None:
        """W: open every sleeping session again, without taking focus."""
        if self.snap is None:
            return
        recorded = 0
        for berth in self.snap.berths.values():
            if not is_sleeping(berth.state, berth.park_reason) or berth.kind in {"process", "berth"}:
                continue
            if any(item.op == "play" and item.berth_id == berth.id for item in self.snap.intents):
                continue
            detail = {"kind": berth.kind, "state": berth.state, "title": berth.title, "focus": False, "via": "wake-all"}
            insert_intent(self.db_path, "play", berth.id, detail, self.clock())
            recorded += 1
        self.reload()
        self.message = f"waking {recorded} sleeping session{'s' if recorded != 1 else ''}" if recorded else "nothing is sleeping"

    def _cycle_filter(self) -> None:
        index = FILTERS.index(self.filter)
        self.filter = FILTERS[(index + 1) % len(FILTERS)]
        self.message = ""
        self._rebuild()

    def _delete(self) -> None:
        if self.snap is None:
            return
        if not self.marked:
            row = self.current
            if row is not None and row.kind == "folder":
                self._delete_folder()
                return
        if self.marked:
            chosen = [self.snap.berths[item] for item in self.marked if item in self.snap.berths]
        else:
            row = self.current
            if row is None or row.kind != "berth":
                self.message = "d deletes a berth"
                return
            berth = self.snap.berths.get(row.id)
            chosen = [berth] if berth is not None else []
        own = (os.environ.get("CMUX_WORKSPACE_ID") or "").lower()

        def mine(item) -> bool:
            return bool(own) and (item.workspace_id or "").lower() == own

        # A Berth screen, and this screen's own workspace, are protected: a warning, then ! deletes them anyway
        # (2026-10-30, his words: "I should never be in a case where it's impossible to delete something from Berth").
        protected = [item for item in chosen if item.kind == "berth" or mine(item)]
        screens: list = []
        parked = [item for item in chosen if item not in protected and item.state == "parked"]
        live = [item for item in chosen if item not in protected and item.state == "live"]
        if protected:
            self.pending_delete = [item.id for item in parked + live]
            self.pending_force = [item.id for item in sorted(protected, key=mine)]  # this screen's own last
            names = ", ".join(item.title for item in protected[:3]) + (" …" if len(protected) > 3 else "")
            why = "this screen's own workspace" if any(mine(item) for item in protected) else "a Berth screen"
            others = len(parked) + len(live)
            self.delete_banner = (f"delete · {names} · protected: {why} · ! deletes "
                                  f"{'it' if len(protected) == 1 else 'them'} anyway"
                                  + (f" · y deletes the other {others}" if others else "") + " · n cancels")
            self.mode = "confirm-delete"
            self.message = ""
            return
        if live:
            self.pending_delete = [item.id for item in parked + live]
            parked_n = len(parked)
            live_n = len(live)
            if parked_n and live_n:
                self.delete_banner = (
                    f"delete · {parked_n} parked · {live_n} live · y deletes parked and records delete · n cancels"
                )
            elif live_n == 1 and not parked_n:
                self.delete_banner = f"delete · {live[0].title} · live · y records delete · n cancels"
            else:
                self.delete_banner = f"delete · {live_n} live · y records delete · n cancels"
            self.mode = "confirm-delete"
            self.message = ""
            if screens:
                self.delete_banner += " · the Berth screen stays"
            return
        if len(parked) == 1 and not self.marked:
            self._step_up_from(parked[0].id)
        self._delete_parked(parked, screens)

    def _delete_folder(self) -> None:
        row = self.current
        if row is None or self.snap is None:
            return
        folder = self.snap.folders.get(row.id)
        if folder is None:
            self.message = "folder is gone"
            return
        if folder.parent_id is None and folder.name == "Inbox":
            self.message = "Inbox stays"
            return
        berths = sum(1 for item in self.snap.berths.values() if item.folder_id == row.id)
        children = sum(1 for item in self.snap.folders.values() if item.parent_id == row.id)
        if folder.parent_id:
            parent = self.snap.folders.get(folder.parent_id)
            dest = parent.name if parent else "the parent"
        else:
            dest = "Inbox"
        bits = [f"delete folder · {folder.name}"]
        if berths:
            word = "berth" if berths == 1 else "berths"
            bits.append(f"{berths} {word} move to {dest}")
        if children:
            word = "folder" if children == 1 else "folders"
            bits.append(f"{children} {word} move up")
        if not berths and not children:
            bits.append("empty")
        bits.append("y deletes the folder · n cancels")
        self.delete_banner = " · ".join(bits)
        self.pending_id = row.id
        self.mode = "confirm-rmdir"
        self.message = ""

    def _on_confirm_rmdir(self, key: str) -> None:
        if key == "y":
            self._accept_rmdir()
            return
        if key in {"n", "esc"}:
            self.mode = "tree"
            self.pending_id = None
            self.delete_banner = ""
            self.message = "delete cancelled"

    def _accept_rmdir(self) -> None:
        folder_id = self.pending_id
        self.mode = "tree"
        self.pending_id = None
        self.delete_banner = ""
        if not folder_id:
            self.message = "folder is gone"
            return
        try:
            result = remove_folder(self.db_path, folder_id, self.clock())
        except CatalogError as exc:
            self.message = str(exc)
            return
        self.collapsed.discard(folder_id)
        self.reload()
        dest = result.get("to_id")
        if dest and self.snap and dest in self.snap.folders:
            self.focus_folder(dest)
        text = f"deleted folder · {result.get('deleted') or 'folder'}"
        moved = int(result.get("moved") or 0)
        lifted = len(result.get("lifted") or []) + len(result.get("merged") or [])
        if moved:
            text += f" · {moved} moved to {result.get('to')}"
        if lifted:
            text += f" · {lifted} folders up"
        self.message = text

    def _delete_parked(self, parked: list, screens: list) -> None:
        for item in parked:
            delete_berth(self.db_path, item.id, self.clock())
            self.marked.discard(item.id)
        self.reload()
        if screens:
            self.message = f"in the trash · {len(parked)} · the Berth screen stays · ctrl-t shows the trash"
        elif len(parked) == 1:
            self.message = f"in the trash · {parked[0].title} · ctrl-t shows the trash"
        else:
            self.message = f"in the trash · {len(parked)} · ctrl-t shows the trash"

    def _on_confirm_delete(self, key: str) -> None:
        if key == "y":
            self.pending_force = []
            self._accept_delete()
            return
        if key == "!" and self.pending_force:
            forced = list(self.pending_force)
            self.pending_force = []
            self._accept_delete(forced=forced)
            return
        if key in {"n", "esc"}:
            self.mode = "tree"
            self.pending_delete = []
            self.pending_force = []
            self.delete_banner = ""
            self.message = "delete cancelled"

    def _accept_delete(self, forced: list[str] | None = None) -> None:
        if self.snap is None:
            return
        parked = []
        live = []
        for berth_id in self.pending_delete:
            berth = self.snap.berths.get(berth_id)
            if berth is None or berth.kind == "berth":
                continue
            if berth.state == "parked":
                parked.append(berth)
            elif berth.state == "live":
                live.append(berth)
        for item in parked:
            delete_berth(self.db_path, item.id, self.clock())
            self.marked.discard(item.id)
        self.reload()
        for item in live:
            row = berth_as_row(self.snap, item.id, self.opened_at)
            if row is None:
                continue
            self._record("delete", row, message=f"delete recorded · {row.title}")
            self.marked.discard(item.id)
        for berth_id in forced or []:
            row = berth_as_row(self.snap, berth_id, self.opened_at)
            if row is not None:
                # Overridden on purpose: the engine deletes it even though it is protected.
                self._record("delete", row, extra={"force": True}, message=f"delete recorded · {row.title} · overridden")
                self.marked.discard(berth_id)
        self.mode = "tree"
        self.pending_delete = []
        self.delete_banner = ""
        if forced:
            self.message = f"delete recorded · {len(forced)} protected, overridden" + (
                f" · and {len(live) + len(parked)} more" if live or parked else "")
            return
        if live and parked:
            self.message = f"in the trash · {len(parked)} · delete recorded · {len(live)} live"
        elif live and len(live) == 1:
            self.message = f"delete recorded · {live[0].title}"
        elif live:
            self.message = f"delete recorded · {len(live)} live"
        elif len(parked) == 1:
            self.message = f"in the trash · {parked[0].title} · ctrl-t shows the trash"
        else:
            self.message = f"in the trash · {len(parked)} · ctrl-t shows the trash"

    # trash -------------------------------------------------------------

    def _trash_db(self):
        from berth.db import Catalog

        return Catalog(Path(self.db_path)).open()

    def _open_trash(self) -> None:
        """ctrl-t: the deleted sessions of the last 30 days, apart from the tree."""
        from berth import trash

        try:
            db = self._trash_db()
            try:
                self.trash_items = trash.items(db)
            finally:
                db.close()
        except sqlite3.Error:
            self.message = "the trash could not be read"
            return
        self.trash_cursor = min(self.trash_cursor, max(0, len(self.trash_items) - 1))
        self.mode = "trash"
        self.message = ""

    @property
    def trash_current(self) -> dict | None:
        if not self.trash_items:
            return None
        return self.trash_items[min(self.trash_cursor, len(self.trash_items) - 1)]

    def _on_trash(self, key: str) -> None:
        last = max(0, len(self.trash_items) - 1)
        if key in {"esc", "q", "ctrl-t"}:
            self.mode = "tree"
            self.message = ""
        elif key in {"j", "down"}:
            self.trash_cursor = min(last, self.trash_cursor + 1)
        elif key in {"k", "up"}:
            self.trash_cursor = max(0, self.trash_cursor - 1)
        elif key in {"g", "home"}:
            self.trash_cursor = 0
        elif key in {"G", "end"}:
            self.trash_cursor = last
        elif key in {"pagedown", "ctrl-d"}:
            self.trash_cursor = min(last, self.trash_cursor + max(1, self.body_height // 2))
        elif key in {"pageup", "ctrl-u"}:
            self.trash_cursor = max(0, self.trash_cursor - max(1, self.body_height // 2))
        elif key in {"enter", "r"}:
            self._restore_from_trash()
        elif key == "D" and self.trash_current is not None:
            self.mode = "confirm-forget"
            self.message = ""

    def _restore_from_trash(self) -> None:
        from berth import trash
        from berth.errors import BerthError

        item = self.trash_current
        if item is None:
            self.message = "the trash is empty"
            return
        try:
            db = self._trash_db()
            try:
                restored = trash.restore(db, item["id"])
                self.trash_items = trash.items(db)
            finally:
                db.close()
        except (sqlite3.Error, BerthError) as exc:
            self.message = f"could not restore · {item['title']} · {exc}"
            return
        self.trash_cursor = min(self.trash_cursor, max(0, len(self.trash_items) - 1))
        self.reload()
        self.message = f"restored · {restored['title']} · it is in Inbox, parked"

    def _on_confirm_forget(self, key: str) -> None:
        from berth import trash
        from berth.errors import BerthError

        if key in {"n", "esc"}:
            self.mode = "trash"
            self.message = "kept in the trash"
            return
        if key != "y":
            return
        item = self.trash_current
        self.mode = "trash"
        if item is None:
            return
        try:
            db = self._trash_db()
            try:
                trash.forget(db, item["id"])
                self.trash_items = trash.items(db)
            finally:
                db.close()
        except (sqlite3.Error, BerthError) as exc:
            self.message = f"could not delete · {exc}"
            return
        self.trash_cursor = min(self.trash_cursor, max(0, len(self.trash_items) - 1))
        self.message = f"deleted for good · {item['title']}"

    def _start_retitle(self) -> None:
        row = self.current
        if row is None or row.kind != "berth":
            self.message = "t renames a session"
            return
        self.pending_id = row.id
        self.prompt = row.title
        self.editor = LineEditor(row.title)
        self.mode = "retitle"
        self.message = ""

    def _commit_retitle(self) -> None:
        title = " ".join(self.prompt.split())
        berth_id = self.pending_id
        if not berth_id:
            self.mode = "tree"
            self.message = "berth is gone"
            return
        if not title:
            self.message = "name the session"
            return
        if len(title) > 200:
            self.message = "name is too long"
            return
        try:
            from berth.cmux import Cmux
            from berth.db import Catalog
            from berth.errors import BerthError
            from berth.ops import rename_berth

            db = Catalog(self.db_path).open()
            try:
                rename_berth(db, Cmux(), berth_id, title)
            finally:
                db.close()
        except (CatalogError, BerthError) as exc:
            self.mode = "tree"
            self.prompt = ""
            self.pending_id = None
            self.reload()
            if self.snap and berth_id in self.snap.berths:
                self.focus_berth(berth_id)
            self.message = str(exc)
            return
        self.mode = "tree"
        self.prompt = ""
        self.pending_id = None
        self.reload()
        self.focus_berth(berth_id)
        self.message = f"renamed · {title}"

    def _toggle(self) -> None:
        """P: pin or unpin the session under the cursor (berth/pins.py)."""
        from berth.errors import BerthError
        from berth.pins import toggle_pinned

        row = self.current
        if row is None or row.kind != "berth":
            self.message = "P pins a session"
            return
        try:
            pinned = toggle_pinned(self.db_path, row.id)
        except (CatalogError, BerthError) as exc:
            self.message = str(exc)
            return
        title = row.title
        self.reload()
        self.message = f"pinned · {title} · first in cmux's sidebar" if pinned else f"unpinned · {title}"

    def _collapse(self) -> None:
        row = self.current
        if row is None:
            return
        if row.kind == "folder" and row.id not in self.collapsed and self._has_children(row.id):
            self.collapsed.add(row.id)
            self._rebuild()
            self.message = ""
            return
        parent = row.container_id
        if parent:
            if row.kind == "berth":
                self.collapsed.add(parent)
            self.cursor = ("folder", parent)
            self._rebuild()
        self.message = ""

    def _open(self) -> None:
        row = self.current
        if row is None or row.kind != "folder":
            return
        if row.id in self.collapsed:
            self.collapsed.discard(row.id)
            self._rebuild()
            self.message = ""
            return
        idx = self._index()
        if idx + 1 < len(self.visible) and self.visible[idx + 1].depth > row.depth:
            child = self.visible[idx + 1]
            self.cursor = (child.kind, child.id)
            self._pin()
        self.message = ""

    def nudge(self, delta: int) -> None:
        """Move by more than one row. A trackpad burst uses this once."""
        if delta:
            self._move(delta)

    def click_at(self, y: int) -> None:
        """Select the painted row. A folder folds. Enter still plays."""
        apply_click(self, y)

    def _move(self, delta: int) -> None:
        if not self.visible:
            return
        idx = self._index()
        nxt = max(0, min(len(self.visible) - 1, idx + delta))
        row = self.visible[nxt]
        self.cursor = (row.kind, row.id)
        self._pin()
        self.message = ""

    def _edge(self, last: bool) -> None:
        if not self.visible:
            return
        row = self.visible[-1 if last else 0]
        self.cursor = (row.kind, row.id)
        self._pin()
        self.message = ""

    def _scroll_excerpt(self, delta: int) -> None:
        row = berth_as_row(self.snap, self.pending_id, self.opened_at) if self.snap and self.pending_id else None
        if row is None:
            return
        total = len(excerpt_lines(row))
        window = self._excerpt_window()
        max_top = max(0, total - window)
        self.excerpt_top = max(0, min(max_top, self.excerpt_top + delta))

    def _scroll_help(self, delta: int) -> None:
        slots = max(1, self.height - 3)
        max_top = max(0, len(HELP_LINES) - slots)
        self.help_top = max(0, min(max_top, self.help_top + delta))

    def _excerpt_window(self) -> int:
        return max(1, self.height - 4)

    def _fail(self, message: str) -> None:
        if self.snap is None:
            self.snap = Snapshot()
            self.problem = message
            self.visible = []
            self.rows = []
            self.counts = Counts()
        self.message = message

    def _rebuild(self) -> None:
        if self.snap is None:
            return
        previous = self.cursor
        before = getattr(self, "visible", [])
        build = build_account_tree if getattr(self, "accounts_view", False) else build_tree
        self.rows, self.counts = build(self.snap, self.opened_at, self.query, self.sort)
        # Each row's folder, from the rows: in the account view a folder's copies have ids of their own.
        self._parents = {row.id: row.container_id for row in self.rows if row.kind == "folder"}
        self.marked = {item for item in self.marked if item in self.snap.berths}
        self.marked_folders = {item for item in self.marked_folders if item in self.snap.folders}
        filtered = self._view_filter(self.rows)
        self.visible = [row for row in filtered if not self._hidden(row)]
        if previous and any((row.kind, row.id) == previous for row in self.visible):
            self.cursor = previous
        elif previous and self.visible:
            # The row under the cursor left the view (parked out of a filter,
            # deleted, folded away): land near where it was, not at the top.
            self.cursor = self._nearest(before, previous) or (self.visible[0].kind, self.visible[0].id)
        elif self.visible:
            self.cursor = (self.visible[0].kind, self.visible[0].id)
        else:
            self.cursor = None
        self._pin()

    def _nearest(self, before: list, gone: tuple[str, str]) -> tuple[str, str] | None:
        """The session line above where ``gone`` was, else any line above, else the next one below."""
        shown = {(row.kind, row.id) for row in self.visible}
        index = next((i for i, row in enumerate(before) if (row.kind, row.id) == gone), None)
        if index is None:
            return None
        above = [(row.kind, row.id) for row in reversed(before[:index]) if (row.kind, row.id) in shown]
        sessions = [key for key in above if key[0] == "berth"]
        below = [(row.kind, row.id) for row in before[index + 1 :] if (row.kind, row.id) in shown]
        return (sessions or above or below or [None])[0]

    def _step_up_from(self, berth_id: str) -> None:
        """After parking, sleeping, or deleting a session, the cursor goes to the session line above it."""
        index = next((i for i, row in enumerate(self.visible) if row.kind == "berth" and row.id == berth_id), None)
        if index is None:
            return
        above = list(reversed(self.visible[:index]))
        target = next((row for row in above if row.kind == "berth"), None) or (above[0] if above else None)
        if target is not None:
            self.cursor = (target.kind, target.id)
            self._pin()

    def _parent(self, folder_id: str) -> str | None:
        """A folder row's parent: from the rows (the account view's copies too), else from the catalog."""
        parents = getattr(self, "_parents", {})
        if folder_id in parents:
            return parents[folder_id]
        folder = self.snap.folders.get(folder_id) if self.snap else None
        return folder.parent_id if folder is not None else None

    def _known(self, folder_id: str) -> bool:
        return folder_id in getattr(self, "_parents", {}) or bool(self.snap and folder_id in self.snap.folders)

    def _hidden(self, row: Row) -> bool:
        seen: set[str] = set()
        current = row.container_id
        while current and current not in seen:
            if current in self.collapsed:
                return True
            seen.add(current)
            if not self._known(current):
                return False
            current = self._parent(current)
        return False

    def _has_children(self, folder_id: str) -> bool:
        for row in self.rows:
            if (row.kind, row.id) == ("folder", folder_id):
                continue
            if self._under(row, folder_id):
                return True
        return False

    def _under(self, row: Row, folder_id: str) -> bool:
        seen: set[str] = set()
        current = row.container_id
        while current and current not in seen:
            if current == folder_id:
                return True
            seen.add(current)
            if not self._known(current):
                return False
            current = self._parent(current)
        return False

    def _expand_to(self, folder_id: str | None) -> None:
        seen: set[str] = set()
        current = folder_id
        while current and current not in seen and self.snap and self._known(current):
            seen.add(current)
            self.collapsed.discard(current)
            current = self._parent(current)

    def _index(self) -> int:
        for idx, row in enumerate(self.visible):
            if (row.kind, row.id) == self.cursor:
                return idx
        return 0

    def _pin(self) -> None:
        limit = max(1, self.height - 3)
        if not self.visible or self.cursor is None:
            self.top = 0
            return
        idx = self._index()
        if idx < self.top:
            self.top = idx
        elif idx >= self.top + limit:
            self.top = idx - limit + 1
        max_top = max(0, len(self.visible) - limit)
        self.top = min(max(self.top, 0), max_top)

