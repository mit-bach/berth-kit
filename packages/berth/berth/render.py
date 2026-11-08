"""Draw one frame. Only the visible window becomes text."""

from __future__ import annotations

import re
from dataclasses import dataclass

from berth.model import is_idle, is_sleeping
from berth.pins import PIN_MARK

HINT = "h l fold · n new folder here · r rename · t title · v mark · P pin · m move · d delete · F fork · c cycle · i idle · S sleep · Z sleep all · W wake all · f filter · s sort · o sidebar · a by account · ^T trash · ? keys · q quits"
# The account view: what works on sessions works as in the tree; folders are changed in the tree.
ACCOUNT_HINT = "by account · a back to the tree · h l fold · enter open · p pause · i idle · S sleep · c cycle · F fork · t title · P pin · v mark · d delete · f filter · / search · ? keys"

EDIT_KEYS = "enter writes · esc cancels · ← → ⌥← ⌥→ ⌘← ⌘→ move · drag or shift selects · ⌫ deletes it · ^C ^X ^V copy cut paste · ^Z undo"

HELP_LINES = (
    "j k move     h l fold a folder     g G first and last",
    "ctrl-u d     half a page     arrows are h j k l",
    "click selects a row, click a folder folds it, click does not play.",
    "enter        Grok records play or focus. the screen does not launch Grok",
    "enter        Shell records play and says it opens a shell",
    "enter        with marks: open every marked parked session (Claude, Grok, shells); the first takes focus",
    "p S c        pause, sleep, cycle. a busy session says why and asks: y cuts its work off",
    "P            pin or unpin. a pinned session stays in its place here (📌) and goes to the top of cmux's sidebar",
    "p            on a folder, confirm once, then park every live session in it and in all its subfolders",
    "s            sort: last used, newest first (default; folders and sessions interleave) or by name",
    "o            cmux sidebar order: follow the tree (default), or folder by folder: every folder on its own,",
    "             by its own newest session, so activity in a subfolder does not move its parent's sessions",
    "a            by account: a heading per Claude account (its color, sessions, usage), then the folders its",
    "             sessions are in. a folder split across accounts shows under each. a again goes back.",
    "             only this screen changes. folders are changed in the tree, sessions work here as there",
    "F            fork a Claude or Grok session: a copy of the conversation opens next to it, named with ⑂",
    "c            cycle: turn a live session off and on again. pause, then play the same session in a new pane",
    "c            with marks: cycle every marked live session. a process asks first; its command reruns",
    "             bg is a Claude background job. enter attaches to it",
    "space        excerpt, and does not play",
    "/            search title, note, folder, excerpt, cwd",
    "n            new folder inside the one under the cursor. a/b makes both. /name is a root",
    "r            rename a folder     t rename a session",
    "v            mark or unmark the berth. the header counts marks",
    "v            on a folder, mark the folder to move it with the marked rows",
    "m            on a folder with nothing marked, pick the folder up. arrows, then m drops it",
    "m            on a folder with marks, drop every marked session and folder into it",
    "m            folders dropped on Inbox go to the top level. esc cancels a move",
    "d            delete a parked row (it goes to the trash). a live row confirms, then records delete",
    "d            on a folder, confirm, then remove it. berths move to the parent or Inbox",
    "d            a folder delete closes nothing. a Berth row stays. marks still delete rows",
    "f            cycle all, live, live + idle, idle, live + sleeping, sleeping, parked. parked is what you paused",
    "i            idle: close a session to free its memory when you will use it again soon. it stays in view with the",
    "             live ones (f: live + idle); enter opens it. with marks, the marked ones; on a folder, all of it",
    "S            sleep: close the session to bring it back soon. on a folder or with marks, all of them (asks first)",
    "i S p        on a closed session: i makes it idle, S sleeping, p paused. nothing opens",
    "Z            put every live session to sleep but the Berth screen (asks first). for an update or a cmux restart",
    "W            wake every sleeping session. enter wakes one. a cmux restart wakes what it closed by itself",
    "             folder colors are automatic: subfolders are shades of the parent's color",
    "text boxes   (new folder, rename, search) edit like a prompt: every letter types, j and k included",
    "             ← → move, ⌥← ⌥→ by word, ⌘← ⌘→ (or ctrl-a ctrl-e, home end) to the ends",
    "             shift with any of those selects. typing or a paste replaces the selection",
    "             backspace and delete, ⌥⌫ (ctrl-w) a word, ⌘⌫ (ctrl-u) to the start, ctrl-k to the end",
    "             ctrl-c copies the selection (with none it cancels), ctrl-x cuts, ctrl-v pastes, ctrl-z undoes",
    "             ⌘V and Wispr Flow paste as one block",
    "             mouse: click puts the cursor, drag selects (and copies), double click a word, triple click all",
    "             then backspace or delete removes the selection, or typing replaces it",
    "ctrl-t       the trash: sessions deleted in the last 30 days. enter brings one back to Inbox, D deletes it for good",
    "?            opens this list",
    "close this list with q, x, space, enter, or backspace. esc too, if it arrives",
)


@dataclass(frozen=True)
class Layout:
    width: int
    gutter: int
    state_w: int
    flag_w: int
    kind_w: int
    note_w: int
    path_w: int


@dataclass
class Line:
    text: str
    role: str = "body"
    selected: bool = False
    spans: tuple[tuple[int, int, str], ...] = ()
    row_id: str = ""
    # Terminal column of the text cursor, on the line with a text box.
    cursor: int | None = None
    # The account view: the account's color, painted behind the whole line; strong on the account's heading.
    tint: str | None = None
    strong: bool = False


CURSOR_W = 1
SWATCH_W = 3  # "██ ", or three spaces on a row with no folder color: every row keeps it
STATE_W = 9  # "live busy"
FLAG_W = 3
AGE_W = 4
TITLE_MIN = 12
NOTE_MIN = 12  # the age, two spaces, and the start of the note
PATH_MAX = 24
PATH_ROOM = 30  # the path column shows only when the note keeps this much


def title_cells(row: object) -> int:
    """Columns a row's name needs: its indent, the fold mark or two spaces, the name."""
    from berth.lineedit import cell_width

    return 2 * max(0, int(getattr(row, "depth", 0))) + 2 + cell_width(getattr(row, "title", "") or "")


def layout_for(width: int, rows=()) -> Layout:
    """Column widths for one frame, measured in terminal cells.

    The name column is as wide as the longest name in the whole tree (folded
    folders included, so folding does not move the columns), indent and all.
    It narrows only when the frame cannot hold it next to the fixed columns
    and the start of a note. Then the note takes what is left, and the
    folder path shows only when the note still has room to spare.
    """
    from berth.lineedit import cell_width

    width = max(int(width), 20)
    rows = list(rows)
    need = max((title_cells(row) for row in rows), default=TITLE_MIN)
    kinds = [cell_width(kind_word(row.berth_kind, row.agent_name, getattr(row, "account", ""),
                                  getattr(row, "account_stale", False))) for row in rows if row.kind == "berth"]
    kind_w = max([5, *kinds])
    fixed = CURSOR_W + SWATCH_W + 1 + STATE_W + 1 + FLAG_W + 1 + kind_w
    room = width - fixed - 1 - NOTE_MIN
    gutter = need if need <= room else max(min(need, TITLE_MIN), room)
    note_w = max(0, width - fixed - gutter - 1)
    paths = [cell_width(row.path_label or "") for row in rows if row.kind == "berth"]
    path_w = min(max(paths, default=0), PATH_MAX)
    if path_w and note_w - (path_w + 1) >= PATH_ROOM:
        note_w -= path_w + 1
    else:
        path_w = 0
    return Layout(width, gutter, STATE_W, FLAG_W, kind_w, note_w, path_w)


def fit(text: str, width: int) -> str:
    """Exactly ``width`` cells: clipped with an ellipsis, or padded with spaces."""
    from berth.lineedit import cell_width

    shown = clip(text, width)
    return shown + " " * max(0, width - cell_width(shown))


def cut(text: str, width: int) -> str:
    """At most ``width`` cells, no ellipsis: the last guard before paint."""
    from berth.lineedit import cell_width

    if cell_width(text) <= width:
        return text
    out = ""
    for char in text:
        if cell_width(out + char) > width:
            break
        out += char
    return out


def format_rss(value: int | None) -> str:
    if value is None:
        return "children unknown"
    if value >= 1_000_000_000:
        return f"children {value / 1_000_000_000:.1f} GB"
    if value >= 1_000_000:
        return f"children {value / 1_000_000:.0f} MB"
    return f"children {value} B"


def format_watch(value: str | None) -> str:
    if value is None:
        return "watch unknown"
    word = value.strip().lower()
    if word in {"up", "down"}:
        return f"watch {word}"
    return "watch unknown"


def excerpt_lines(row: object) -> list[str]:
    raw = row.excerpt.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    # A tab would move the hardware cursor. The excerpt is a column of text.
    lines = raw.split("\n")
    if not any(line.strip() for line in lines):
        return ["(no screen text stored)"]
    return lines


def render(app: object) -> list[Line]:
    mode = app.mode
    if mode in {"excerpt", "confirm", "confirm-play", "confirm-cycle", "confirm-busy", "confirm-delete", "confirm-rmdir",
                "confirm-park", "confirm-sleep", "confirm-cycle-many"}:
        return render_overlay(app)
    if mode == "help":
        return render_help(app)
    if mode in {"trash", "confirm-forget"}:
        return render_trash(app)
    return render_tree(app)


TRASH_HINT = "enter restores it to Inbox · D deletes it for good · j k move · esc closes"


def render_trash(app: object) -> list[Line]:
    """ctrl-t: what was deleted in the last 30 days. Enter brings one back to Inbox.

    Columns, in terminal cells: the name (as wide as the longest, or what the
    frame allows), the kind, when it was deleted, the days it has left, and
    where it was.
    """
    from berth.lineedit import cell_width
    from berth.trash import TRASH_DAYS

    items = list(getattr(app, "trash_items", []))
    width = app.width
    count = f"{len(items)} session{'s' if len(items) != 1 else ''}"
    lines = [Line(clip(f"Trash · {count} · each kept {TRASH_DAYS} days after it is deleted", width), role="header")]
    current = getattr(app, "trash_current", None)
    if app.mode == "confirm-forget" and current is not None:
        banner = f"delete for good · {current['title']} · y deletes it, it cannot come back · n keeps it"
        lines.append(Line(clip(banner, width), role="banner"))
    else:
        lines.append(Line(clip(TRASH_HINT, width), role="hint"))
    slots = max(0, app.height - 3)
    if not items:
        body = [Line("the trash is empty", role="message")]
    else:
        kind_w = max(5, *(cell_width(kind_word(item["kind"], item["agent"])) for item in items))
        when_w, left_w = len("deleted 99w ago"), len("29d left")
        fixed = 2 + 1 + kind_w + 1 + when_w + 1 + left_w + 1
        name_w = max(8, min(max(cell_width(item["title"]) for item in items), width - fixed - 12))
        cursor = min(getattr(app, "trash_cursor", 0), len(items) - 1)
        top = max(0, min(cursor - slots + 1, len(items) - slots)) if cursor >= slots else 0
        body = []
        for index, item in enumerate(items[top : top + slots], start=top):
            mark = ">" if index == cursor else " "
            ago = age_label(item["deleted_at"])
            when = "deleted now" if ago == "now" else f"deleted {ago} ago"
            where = f"from {item['folder']}" if item["folder"] else (
                "folder not recorded" if item["recovered"] else "from Inbox")
            notes = []
            if item.get("held_by"):
                notes.append(f"same conversation as {item['held_by']}")
            if item["reason"] == "merged":
                notes.append("merged into another row")
            if item["recovered"]:
                notes.append("rebuilt from history, no note or excerpt")
            tail = " · ".join([where, *notes])
            text = (
                f"{mark} {fit(item['title'], name_w)} {fit(kind_word(item['kind'], item['agent']), kind_w)} "
                f"{fit(when, when_w)} {fit(str(item['days_left']) + 'd left', left_w)} "
            )
            text = (text + clip(tail, max(0, width - cell_width(text)))).rstrip()
            body.append(Line(text, role="berth", selected=index == cursor, row_id=item["id"]))
    while len(body) < slots:
        body.append(Line(""))
    lines.extend(body)
    footer = app.message or "a restored session comes back parked, in Inbox, with its conversation"
    lines.append(Line(clip(footer, width), role="message"))
    return fit_height(lines, app.height)


def render_tree(app: object) -> list[Line]:
    layout = layout_for(app.width, getattr(app, "rows", ()) or getattr(app, "visible", ()))
    lines = [header_line(app), hint_line(app)]
    lines.extend(tree_body(app, layout))
    lines.append(Line(clip(footer_text(app), app.width).rstrip(), role="message"))
    return fit_height(lines, app.height)


def render_help(app: object) -> list[Line]:
    lines = [header_line(app), Line("keys", role="banner")]
    slots = max(0, app.height - 3)
    for item in HELP_LINES[app.help_top : app.help_top + slots]:
        lines.append(Line(clip(item, app.width).rstrip(), role="excerpt"))
    while len(lines) < max(app.height - 1, 0):
        lines.append(Line(""))
    lines.append(Line("q  x  space  enter  close this", role="message"))
    return fit_height(lines, app.height)


def render_overlay(app: object) -> list[Line]:
    from berth.tree import berth_as_row

    width = app.width
    if app.mode in {"confirm-delete", "confirm-rmdir", "confirm-park", "confirm-sleep", "confirm-cycle-many"}:
        fallback = (
            "delete folder · y deletes the folder · n cancels"
            if app.mode == "confirm-rmdir"
            else "delete · y records delete · n cancels"
        )
        lines = [
            header_line(app),
            Line(clip(app.delete_banner or fallback, width).rstrip(), role="banner"),
        ]
        while len(lines) < app.height - 1:
            lines.append(Line(""))
        lines.append(Line(clip(overlay_footer(app), width).rstrip(), role="message"))
        return fit_height(lines, app.height)
    row = berth_as_row(app.snap, app.pending_id, app.opened_at) if app.pending_id else None
    lines = [header_line(app)]
    if row is None:
        lines.append(Line("berth is gone", role="banner"))
        source: list[str] = []
        recipe = ""
    else:
        lines.append(Line(clip(banner_text(app, row), width).rstrip(), role="banner"))
        recipe = clip(recipe_text(row), width).rstrip()
        source = excerpt_lines(row)
    if app.height >= 3:
        lines.append(Line(recipe, role="excerpt"))
    slots = max(0, app.height - 4)
    for item in source[app.excerpt_top : app.excerpt_top + slots]:
        lines.append(Line(clip(item, width).rstrip(), role="excerpt"))
    while len(lines) < app.height - 1:
        lines.append(Line(""))
    lines.append(Line(clip(overlay_footer(app), width).rstrip(), role="message"))
    return fit_height(lines, app.height)


def header_line(app: object) -> Line:
    counts = app.counts
    status = app.snap.status
    # No account usage up here (Dominik, 2026-11-05: "get rid of the account usage stats from the top tool bar").
    parts = [f"{counts.live} live"]
    if counts.idle:
        parts.append(f"{counts.idle} idle")
    if counts.sleeping:
        parts.append(f"{counts.sleeping} sleeping")
    parts.append(f"{counts.parked} parked")
    if counts.playing:
        parts.append(f"{counts.playing} playing")
    if counts.pausing:
        parts.append(f"{counts.pausing} pausing")
    if counts.busy:
        parts.append(f"{counts.busy} busy")
    marked = len(getattr(app, "marked", ())) + len(getattr(app, "marked_folders", ()))
    if marked:
        parts.append(f"{marked} marked")
    parts.append(f"{counts.grok} Grok")
    parts.append(f"{counts.shell} Shell")
    # Memory, named: the Mac as Activity Monitor counts it, and what the sessions demand (berth/memory.py).
    # Never a sum of RSS again ("children 39.4 GB" on a 36 GB Mac, 2026-11-06).
    from pathlib import Path as _Path

    from berth.memory import mac_line, sessions_line, stored

    measured = stored(_Path(app.db_path).parent) if getattr(app, "db_path", None) else None
    if measured:
        parts.extend(part for part in (mac_line(measured.get("mac") or {}, measured.get("at")),
                                       sessions_line(measured.get("sessions") or {})) if part)
    parts.append(format_watch(status.watcher))
    right = "  ".join(parts)
    left = "Berth"
    from berth.health import changed_since_start

    if changed_since_start():
        # This screen runs the code it started with; the code on disk is newer (berth/health.py).
        left = "Berth · ⚠ new code: q, then berth"
    screens = sum(1 for item in app.snap.berths.values() if item.kind == "berth" and item.state == "live")
    if screens > 1:
        # Never quietly: only one screen may be open (berth/screens.py). The extra one goes with d, then !.
        left = f"Berth · ⚠ {screens} Berth screens: d then ! on the extra one"
    label = getattr(app, "filter_label", "")
    if label:
        left = f"{left} · {label}"
    width = app.width
    if len(left) + 2 + len(right) <= width:
        text = left + (" " * (width - len(left) - len(right))) + right
    else:
        text = clip(f"{left}  {right}", width)
    return Line(text, role="header", spans=count_spans(text))


PROMPT_MODES = {"search", "mkdir", "move", "rename", "retitle"}


def hint_line(app: object) -> Line:
    editor = getattr(app, "editor", None)
    if app.mode in PROMPT_MODES and editor is not None:
        return box_line(app, editor)
    if app.mode == "tree" and getattr(app, "accounts_view", False) and not app.query:
        return legend_line(app)
    text = clip(hint_text(app), app.width).rstrip()
    role = "prompt" if app.mode in PROMPT_MODES else "hint"
    return Line(text, role=role)


def legend_line(app: object) -> Line:
    """The account view's second line: which color is which account, then the keys.

    ``by account   ██ uni   ██ homestead   ██ personal   ·  a back to the tree · h l fold · …``
    """
    from berth.accounts import label_of

    text, spans = "by account  ", []
    for row in getattr(app, "rows", []):
        if getattr(row, "heading", "") != "account" or not getattr(row, "tint", None):
            continue
        name = row.account_key if row.account_key else label_of(row.account_key)
        if row.account_key == "?":
            name = "unknown"
        spans.append((len(text) + 1, len(text) + 3, f"account:{row.tint}"))
        text += f" ██ {name}  "
    text += " ·  " + ACCOUNT_HINT.split(" · ", 1)[1]
    text = clip(text, app.width).rstrip()
    return Line(text, role="hint", spans=tuple(span for span in spans if span[1] <= len(text)))


def box_line(app: object, editor) -> Line:
    """The label, then the text box: scrolled to the cursor, the selection marked.

    Trailing spaces are kept, so the cursor moves on space like anywhere else.
    """
    from berth.lineedit import cell_width

    label = clip(box_label(app), max(1, app.width // 2))
    room = max(1, app.width - box_origin(app) - 1)
    shown, cursor, selection = editor.view(room)
    spans: tuple = ()
    if selection:
        # Columns to characters within the shown text.
        start = _char_at(shown, selection[0])
        end = _char_at(shown, selection[1])
        spans = ((len(label) + start, len(label) + end, "select"),)
    return Line(label + shown, role="prompt", spans=spans, cursor=cell_width(label) + cursor)


BOX_ROW = 1


def box_origin(app: object) -> int:
    """The screen column where the text box's text starts, after its label."""
    from berth.lineedit import cell_width

    return cell_width(clip(box_label(app), max(1, app.width // 2)))


def _char_at(text: str, column: int) -> int:
    from berth.lineedit import cell_width

    for index in range(len(text) + 1):
        if cell_width(text[:index]) >= column:
            return index
    return len(text)


def box_label(app: object) -> str:
    if app.mode == "mkdir":
        return f"new folder in {getattr(app, 'mkdir_where', '/')} · "
    if app.mode == "rename":
        return "rename folder · "
    if app.mode == "retitle":
        return "rename session · "
    if app.mode == "move":
        title = ""
        if app.pending_id and app.snap and app.pending_id in app.snap.berths:
            title = app.snap.berths[app.pending_id].title
        return f"move {title} · "
    return "/ "


def hint_text(app: object) -> str:
    if app.mode == "tree" and getattr(app, "accounts_view", False) and not app.query:
        return ACCOUNT_HINT
    if app.mode == "mkdir":
        where = getattr(app, "mkdir_where", "/")
        return f"new folder in {where} · {app.prompt}"

    if app.mode == "rename":
        return f"rename folder · {app.prompt}"
    if app.mode == "retitle":
        return f"rename session · {app.prompt}"
    if app.mode == "move":
        title = ""
        if app.pending_id and app.pending_id in app.snap.berths:
            title = app.snap.berths[app.pending_id].title
        return f"move {title} · {app.prompt}"
    if app.mode == "search":
        return f"/ {app.query}"
    if app.query:
        return f"/ {app.query}   esc clears"
    folder_hint = _folder_hint(app)
    return folder_hint or HINT


def _folder_hint(app: object) -> str:
    """On a folder the two folder moves are spelled out: nest one, or move this one."""
    row = getattr(app, "current", None)
    snap = getattr(app, "snap", None)
    if row is None or snap is None or row.kind != "folder":
        return ""
    carried = len(getattr(app, "marked", ())) + len(getattr(app, "marked_folders", ()))
    if carried:
        word = "row" if carried == 1 else "rows"
        target = "the top level" if folder_is_inbox(snap, row.id) else row.title
        return f"m drops {carried} {word} into {target} · arrows to another folder · esc cancels"
    folder = snap.folders.get(row.id)
    if folder is None or (folder.parent_id is None and folder.name == "Inbox"):
        return ""
    return f"n new folder inside {folder.name} · m pick up {folder.name} to move it · p park all in it · v mark · r rename · d delete · ? keys"


def folder_is_inbox(snap, folder_id: str) -> bool:
    folder = snap.folders.get(folder_id)
    return folder is not None and folder.parent_id is None and folder.name == "Inbox"


def footer_text(app: object) -> str:
    if app.message:
        return app.message
    if app.mode in {"mkdir", "rename", "retitle"}:
        return EDIT_KEYS
    if app.mode == "search":
        return "enter keeps the filter · esc clears · up down walk the list · " + EDIT_KEYS.split(" · ", 2)[2]

    if app.mode == "move":
        return move_footer(app)
    return ""


def move_footer(app: object) -> str:
    from berth.tree import resolve_folder

    needle = app.prompt.strip().strip("/")
    if not needle:
        return "enter moves · esc cancels"
    found = resolve_folder(app.snap, needle)
    if found.status == "one":
        return "enter moves · " + found.matches[0]
    if found.status == "none":
        return "enter moves · esc cancels · no folder"
    return "ambiguous · " + " · ".join(found.matches[:5])


def overlay_footer(app: object) -> str:
    if app.mode == "confirm":
        return "the workspace stays up until the engine takes the intent"
    if app.mode == "confirm-delete":
        return "y records delete · n cancels · the screen does not close the workspace"
    if app.mode == "confirm-rmdir":
        return "y deletes the folder · n cancels · workspaces stay open"
    if app.mode == "confirm-park":
        return "y records a pause for each · the recipe is saved before each workspace closes · n cancels"
    if app.mode == "confirm-sleep":
        return "y puts them to sleep · each recipe is saved before its workspace closes · W wakes them · n cancels"
    if app.mode == "confirm-play":
        return "y records play · n cancels · does not start the process"
    if app.mode == "confirm-cycle":
        return "y records a cycle · the process stops, then its command runs again · n cancels"
    if app.mode == "confirm-busy":
        return "y cuts off what keeps it busy: its turn, background shells or agents, an open question · n cancels"
    if app.mode == "confirm-cycle-many":
        return "y cycles them · the busy ones lose their turn, background work, or open question · n cancels"
    return "q x space enter close · does not play"


def banner_text(app: object, row: object) -> str:
    if app.mode == "confirm-delete":
        return app.delete_banner or "delete · y records delete · n cancels"
    if app.mode == "confirm":
        word = "busy" if row.busy else "process"
        return f"{word} · {row.title} · {row.state} · {row.path} · y records pause · n cancels"
    if app.mode == "confirm-play":
        return f"process · {row.title} · {row.state} · {row.path} · y records play · n cancels"
    if app.mode == "confirm-cycle":
        return f"process · {row.title} · {row.state} · {row.path} · y records cycle · n cancels"
    if app.mode == "confirm-busy":
        berth = app.snap.berths.get(row.id) if getattr(app, "snap", None) else None
        why = "; ".join(getattr(berth, "busy_why", ()) or ()) or "busy"
        op = getattr(app, "pending_op", "") or "pause"
        return f"busy · {row.title} · {why} · y {op}s it anyway · n cancels"
    stored = f"stored {row.excerpt_at}" if row.excerpt_at else "nothing stored"
    return f"excerpt · {row.title} · {row.state} · {kind_word(row.berth_kind, row.agent_name)} · {row.path} · {stored}"


def recipe_text(row: object) -> str:
    display = row.recipe_display.strip() or "(no recipe)"
    text = f"will play · {display}"
    if row.cwd:
        text += f" · cwd {row.cwd}"
    if row.agent_version:
        text += f" · recorded {row.agent_version}"
    return text


def tree_body(app: object, layout: Layout) -> list[Line]:
    slots = max(0, app.height - 3)
    if app.problem and not app.visible:
        rows = [Line(clip(app.problem, app.width).rstrip(), role="message")]
    elif not app.visible and app.query:
        rows = [Line("no match", role="message")]
    elif not app.visible:
        rows = [Line("catalog is empty", role="message")]
    else:
        window = app.visible[app.top : app.top + slots]
        rows = [render_row(app, row, layout) for row in window]
    while len(rows) < slots:
        rows.append(Line(""))
    return rows


def render_row(app: object, row: object, layout: Layout) -> Line:
    selected = app.cursor == (row.kind, row.id)
    cursor = ">" if selected else " "
    if row.kind == "folder" and getattr(row, "heading", "") == "account":
        return render_account(cursor, row, layout, selected, row.id in app.collapsed)
    if row.kind == "folder":
        carried = row.id in getattr(app, "marked_folders", ())
        line = render_folder(cursor, row, layout, selected, row.id in app.collapsed, carried)
    else:
        line = render_berth(app, cursor, row, layout, selected)
    line.tint = getattr(row, "tint", None)
    return line


def render_account(cursor: str, row: object, layout: Layout, selected: bool, closed: bool) -> Line:
    """An account's heading in the account view: a band in its color across the frame.

    ``▾ uni   45 live · 20 sleeping · 27 parked · 3 busy   5h 19% · 7d 65%   default · ~/.claude``
    """
    from berth import accounts
    from berth.lineedit import cell_width

    key = getattr(row, "account_key", "")
    counts = [f"{row.live_n} live"]
    if row.idle_n:
        counts.append(f"{row.idle_n} idle")
    if row.sleeping_n:
        counts.append(f"{row.sleeping_n} sleeping")
    counts.append(f"{row.parked_n} parked")
    if row.busy_n:
        counts.append(f"{row.busy_n} busy")
    if not key:
        counts.append("Grok, shells, processes")
    parts = [" · ".join(counts)]
    usage = accounts.usage_of(key) if key and key != accounts.UNKNOWN else ""
    if usage:
        parts.append(usage)
    marks = accounts.marks_of(key)
    if marks:
        parts.append(" · ".join(marks))
    head = f"{cursor} {'▸' if closed else '▾'} {row.title}"
    text = head + "   " + "   ".join(parts)
    text = clip(text, layout.width)
    text = text + " " * max(0, layout.width - cell_width(text))  # the band runs the whole width
    return Line(text, role="account", selected=selected, row_id=row.id, tint=row.tint or "#8E8E93", strong=True)


def render_folder(
    cursor: str, row: object, layout: Layout, selected: bool, closed: bool, carried: bool = False
) -> Line:
    from berth.lineedit import cell_width

    gutter = folder_gutter(row.title, row.depth, not closed, layout.gutter)
    swatch, spans = _side(cursor, row)
    prefix = f"{cursor}{swatch}{gutter} "
    summary = folder_summary(row)
    if carried:
        summary = "v moving  " + summary
    summary = clip(summary, max(0, layout.width - cell_width(prefix)))
    text = (prefix + summary).rstrip()
    spans.extend(count_spans(text))
    return Line(text, role="folder", selected=selected, spans=tuple(spans), row_id=row.id)


def age_label(stamp: str, now=None) -> str:
    """How long since you last used it: now, 5m, 3h, 2d, 6w, 1y."""
    if not stamp:
        return ""
    from datetime import datetime, timezone

    from berth.timeutil import parse_time

    try:
        seconds = ((now or datetime.now(timezone.utc)) - parse_time(stamp)).total_seconds()
    except ValueError:
        return ""
    if seconds < 60:
        return "now"
    for size, unit in ((365 * 86400, "y"), (7 * 86400, "w"), (86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            return f"{int(seconds // size)}{unit}"
    return "now"


def render_berth(app: object, cursor: str, row: object, layout: Layout, selected: bool) -> Line:
    gutter = berth_gutter(row.title, row.depth, layout.gutter)
    swatch, spans = _side(cursor, row)
    state = state_text(row)
    state_field = fit(state, layout.state_w)
    # Marks: ! urgent or » follow up (the session raised a flag, berth/attention.py; one at a time) and pinned
    # (berth/pins.py). The old ! (protect) and * (new) marks are gone.
    raised = "!" if getattr(row, "urgent", False) else ("»" if getattr(row, "follow_up", False) else "")
    flag = raised + (PIN_MARK if getattr(row, "pinned", False) else "")
    if row.id in getattr(app, "marked", ()):
        flag += "v"
    flag_field = fit(flag, layout.flag_w)
    kind = kind_word(row.berth_kind, row.agent_name, getattr(row, "account", ""), getattr(row, "account_stale", False))
    kind_field = fit(kind, layout.kind_w)
    note = row.note
    age = age_label(getattr(row, "recency", ""))
    note = f"{age:>{AGE_W}}  {note}".rstrip() if age else f"{'':>{AGE_W}}  {note}".rstrip()
    note_field = fit(note, layout.note_w) if layout.note_w else ""
    raw = f"{cursor}{swatch}{gutter} {state_field} {flag_field} {kind_field}"
    if layout.note_w:
        raw += f" {note_field}"
    if layout.path_w:
        raw += " " + clip_left(row.path_label, layout.path_w)
    state_at = len(cursor) + len(swatch) + len(gutter) + 1
    flag_at = state_at + len(state_field) + 1
    kind_at = flag_at + len(flag_field) + 1
    text = raw.rstrip()
    pair = {"live": "live", "live busy": "busy", "playing": "play", "pausing": "play", "sleeping": "play",
            "idle": "idle"}.get(state, "")
    if pair and state_at < len(text):
        spans.append((state_at, min(len(text), state_at + len(state)), pair))
    if row.berth_kind in {"grok", "other-agent"} and kind_at < len(text):
        spans.append((kind_at, min(len(text), kind_at + len(kind)), "live"))
    elif kind == "Shell" and kind_at < len(text):
        spans.append((kind_at, min(len(text), kind_at + len(kind)), "play"))
    elif row.berth_kind == "berth" and kind_at < len(text):
        # Agents are green and shells cyan; the Berth screen is neither.
        spans.append((kind_at, min(len(text), kind_at + len(kind)), "screen"))
    return Line(text, role="berth", selected=selected, spans=tuple(spans), row_id=row.id)


def folder_summary(row: object) -> str:
    parts = [f"{row.live_n} live"]
    if row.idle_n:
        parts.append(f"{row.idle_n} idle")
    if row.sleeping_n:
        parts.append(f"{row.sleeping_n} sleeping")
    parts.append(f"{row.parked_n} parked")
    if row.playing_n:
        parts.append(f"{row.playing_n} playing")
    if row.pausing_n:
        parts.append(f"{row.pausing_n} pausing")
    if row.busy_n:
        parts.append(f"{row.busy_n} busy")
    # In the account view an account's folders hold only its Claude sessions: no "0 Grok 0 Shell" on each.
    quiet = bool(getattr(row, "account_key", ""))
    if row.grok_n or not quiet:
        parts.append(f"{row.grok_n} Grok")
    if row.shell_n or not quiet:
        parts.append(f"{row.shell_n} Shell")
    if row.title.casefold() == "inbox":
        parts.insert(0, "not in a folder")
    if row.archived:
        parts.append("archived")
    return "  ".join(parts)


def _side(cursor: str, row: object) -> tuple[str, list[tuple[int, int, str]]]:
    """The square on a row's left: its folder's color in the tree; its account's color in the account view
    (Dominik, 2026-11-05: "I don't know what color is assigned to what system"), blank under "No account"."""
    if getattr(row, "by_account", False):
        color = getattr(row, "tint", None)
        if not color:
            return " " * SWATCH_W, []
        start = len(cursor)
        return "██ ", [(start, start + 2, f"account:{color}")]
    return _swatch(cursor, row.color)


def _swatch(cursor: str, color: str | None) -> tuple[str, list[tuple[int, int, str]]]:
    """Two cells the painter fills with the folder color. The hex is not the swatch."""
    if not color:
        return " " * SWATCH_W, []
    start = len(cursor)
    return "██ ", [(start, start + 2, f"swatch:{color}")]


def kind_word(kind: str, agent_name: str | None = None, account: str = "", stale: bool = False) -> str:
    """The agent column. A Claude session also shows its account's tag ("Claude hom"), with * when it opened
    on ~/.claude before the last account switch (berth/accounts.py)."""
    if account and kind == "other-agent":
        from berth.accounts import tag

        return f"{kind_word(kind, agent_name)} {tag(account)}{'*' if stale else ''}"
    if kind == "grok":
        return "Grok"
    if kind == "shell":
        return "Shell"
    if kind == "berth":
        return "Berth"
    if kind == "process":
        return "Process"
    if kind == "other-agent":
        name = (agent_name or "").strip()
        if name:
            return name[:1].upper() + name[1:]
        return "Agent"
    return (kind or "?")[:5]


def state_text(row: object) -> str:
    if row.state == "live" and row.busy:
        return "live busy"
    if row.state == "parked" and getattr(row, "park_reason", "") == "background":
        return "bg"
    if is_idle(row.state, getattr(row, "park_reason", "")):
        return "idle"
    if is_sleeping(row.state, getattr(row, "park_reason", "")):
        return "sleeping"
    return row.state


def folder_gutter(name: str, depth: int, open_: bool, width: int) -> str:
    prefix = ("  " * depth) + ("▾ " if open_ else "▸ ")
    if len(prefix) >= width:
        return fit(prefix + name, width)
    return prefix + fit(name, width - len(prefix))


def berth_gutter(title: str, depth: int, width: int) -> str:
    prefix = ("  " * depth) + "  "
    if len(prefix) >= width:
        return fit(prefix + title, width)
    return prefix + fit(title, width - len(prefix))


def count_spans(text: str) -> tuple[tuple[int, int, str], ...]:
    found: list[tuple[int, int, str]] = []
    for label, name in (
        ("children unknown", "warn"),
        ("watch unknown", "warn"),
        ("watch down", "warn"),
        ("watch up", "live"),
    ):
        idx = text.find(label)
        if idx != -1:
            found.append((idx, idx + len(label), name))
    for match in re.finditer(r"\d+ new", text):
        found.append((match.start(), match.end(), "new"))
    for match in re.finditer(r"\d+ busy", text):
        found.append((match.start(), match.end(), "busy"))
    for match in re.finditer(r"\d+ Grok", text):
        found.append((match.start(), match.end(), "live"))
    for match in re.finditer(r"\d+ Shell", text):
        found.append((match.start(), match.end(), "play"))
    return tuple(found)


def clip(text: str, width: int) -> str:
    """At most ``width`` terminal cells, ending in an ellipsis when cut."""
    from berth.lineedit import cell_width

    if width <= 0:
        return ""
    if cell_width(text) <= width:
        return text
    if width == 1:
        return "…"
    return cut(text, width - 1) + "…"


def clip_left(text: str, width: int) -> str:
    """At most ``width`` terminal cells, keeping the end, starting with an ellipsis when cut."""
    from berth.lineedit import cell_width

    if width <= 0:
        return ""
    if cell_width(text) <= width:
        return text
    if width == 1:
        return "…"
    kept = ""
    for char in reversed(text):
        if cell_width(char + kept) > width - 1:
            break
        kept = char + kept
    return "…" + kept


def fit_height(lines: list[Line], height: int) -> list[Line]:
    height = max(int(height), 1)
    if len(lines) < height:
        lines = [*lines, *[Line("") for _ in range(height - len(lines))]]
    return lines[:height]


def target_at(lines: list[Line], y: int) -> tuple[str, str] | None:
    """The folder or berth painted on line y. Any other line is no target."""
    if isinstance(y, bool) or not isinstance(y, int):
        return None
    if y < 0 or y >= len(lines):
        return None
    line = lines[y]
    if line.role not in {"folder", "berth", "account"} or not line.row_id:
        return None
    return ("folder" if line.role == "account" else line.role, line.row_id)  # an account heading folds like a folder


def point(
    collapsed: set[str], lines: list[Line], y: int
) -> tuple[tuple[str, str] | None, set[str], bool]:
    """Select the painted row. A folder also folds. Nothing is played."""
    target = target_at(lines, y)
    if target is None:
        return None, collapsed, False
    kind, row_id = target
    folded = set(collapsed)
    if kind == "folder":
        if row_id in folded:
            folded.remove(row_id)
        else:
            folded.add(row_id)
    return (kind, row_id), folded, True


def apply_click(screen: object, y: int) -> None:
    """Move the cursor to that row. A folder folds or opens. A click does not play."""
    if screen.problem or screen.mode not in {"tree", "search"}:
        return
    cursor, collapsed, hit = point(screen.collapsed, screen.lines(), y)
    if not hit or cursor is None:
        return
    screen.collapsed = collapsed
    screen.cursor = cursor
    screen.message = ""
    if cursor[0] == "folder":
        screen._rebuild()
        return
    screen._pin()
