"""Turn a catalog snapshot into the nested rows the screen draws."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from berth.catalog import Berth, Snapshot
from berth.model import is_idle, is_sleeping

LIVE_STATES = frozenset({"live", "playing", "pausing"})


@dataclass
class Row:
    kind: str
    id: str
    depth: int
    title: str
    state: str = ""
    note: str = ""
    path: str = ""
    path_label: str = ""
    protect: bool = False
    pinned: bool = False
    urgent: bool = False
    follow_up: bool = False
    account: str = ""  # a Claude session's account (berth/accounts.py)
    account_stale: bool = False
    new: bool = False
    busy: bool = False
    container_id: str | None = None
    live_n: int = 0
    parked_n: int = 0
    idle_n: int = 0
    sleeping_n: int = 0
    playing_n: int = 0
    pausing_n: int = 0
    new_n: int = 0
    busy_n: int = 0
    grok_n: int = 0
    shell_n: int = 0
    archived: bool = False
    color: str | None = None
    berth_kind: str = ""
    agent_name: str = ""
    park_reason: str = ""
    recency: str = ""
    cwd: str = ""
    recipe_display: str = ""
    excerpt: str = ""
    excerpt_at: str = ""
    agent_version: str = ""
    # The account view (build_account_tree): the account's color, its group's key, and "account" on its heading.
    tint: str | None = None
    account_key: str = ""
    by_account: bool = False  # a row of the account view: its side square is its account's color
    heading: str = ""


@dataclass
class Counts:
    live: int = 0
    parked: int = 0
    idle: int = 0
    sleeping: int = 0
    playing: int = 0
    pausing: int = 0
    new: int = 0
    busy: int = 0
    grok: int = 0
    shell: int = 0


@dataclass(frozen=True)
class Resolve:
    status: str
    folder_id: str | None
    matches: tuple[str, ...]


SORTS = ("recent", "name")


def build_tree(snap: Snapshot, opened_at: datetime, query: str, sort: str = "recent") -> tuple[list[Row], Counts]:
    """``recent``: newest use first, folders and sessions interleaved at every level.

    A folder is as recent as the newest session anywhere under it. ``name``
    is the older order: subfolders first in their stored order, then live
    sessions, then parked, each by title.
    """
    catalog = _walk(snap, opened_at, "", sort)
    counts = _counts(catalog)
    rows = catalog if not query else _walk(snap, opened_at, query, sort)
    return rows, counts


# --- the account view (2026-11-05) --------------------------------------------------------------------------------
#
# Dominik: sessions grouped by the Claude account they are on, each group with the same folders as the tree, and
# only those folders: a folder whose sessions are on three accounts shows three times, one only on uni shows once.
# Each account becomes a top-level folder of its own snapshot, holding copies of the real folders its sessions are
# in (and their parents), so folding, the cursor, filters and search work as in the tree. A copy's id is the account
# heading's id, SEP, and the real folder's id; sessions keep their own ids, since each is in one account only.

ACCOUNT_ROOT = "account:"
SEP = "\x1f"


def account_key(berth: Berth) -> str:
    """The account group a session goes in: its account, ``?`` for a Claude session on an unknown one, else ""."""
    if getattr(berth, "account", ""):
        return berth.account
    if berth.kind == "other-agent" and (berth.agent_name or "").casefold() == "claude":
        from berth.accounts import UNKNOWN

        return UNKNOWN
    return ""


def real_folder_id(row_id: str) -> str | None:
    """The real folder behind a row of the account view (or of the tree); None for an account heading."""
    if row_id.startswith(ACCOUNT_ROOT):
        return row_id.split(SEP, 1)[1] if SEP in row_id else None
    return row_id


class AccountSnapshot(Snapshot):
    """One account's slice of the catalog. Paths leave the account heading out: they are the real folders' paths."""

    root: str = ""

    def folder_path(self, folder_id: str) -> str:
        path = super().folder_path(folder_id)
        if folder_id == self.root:
            return path
        return path.split("/", 1)[1] if "/" in path else path


def _account_snapshot(snap: Snapshot, key: str, berth_ids: list[str]) -> AccountSnapshot:
    from dataclasses import replace

    from berth import accounts
    from berth.catalog import Folder

    root = ACCOUNT_ROOT + key

    def copy_id(folder_id: str) -> str:
        return f"{root}{SEP}{folder_id}"

    keep: set[str] = set()
    for berth_id in berth_ids:
        current, seen = snap.berths[berth_id].folder_id, set()
        while current and current in snap.folders and current not in seen:
            seen.add(current)
            keep.add(current)
            current = snap.folders[current].parent_id
    view = AccountSnapshot(status=snap.status, intents=snap.intents)
    view.root = root
    view.folders[root] = Folder(id=root, parent_id=None, name=accounts.label_of(key), sort=accounts.rank_of(key),
                                note="", archived=False, profile_id=None, default_cwd=None, color=accounts.color_of(key))
    for folder_id in keep:
        folder = snap.folders[folder_id]
        parent = copy_id(folder.parent_id) if folder.parent_id in keep else root
        view.folders[copy_id(folder_id)] = replace(folder, id=copy_id(folder_id), parent_id=parent)
    view.children[None] = [root]
    view.children[root] = [copy_id(child) for child in snap.children.get(None, []) if child in keep]
    view.children[root] += [copy_id(fid) for fid in sorted(keep) if snap.folders[fid].parent_id not in snap.folders
                            and fid not in snap.children.get(None, [])]
    for folder_id in keep:
        view.children[copy_id(folder_id)] = [copy_id(child) for child in snap.children.get(folder_id, [])
                                             if child in keep]
    chosen = set(berth_ids)
    for folder_id in keep:
        ids = [berth_id for berth_id in snap.berths_in.get(folder_id, []) if berth_id in chosen]
        if ids:
            view.berths_in[copy_id(folder_id)] = ids
    for berth_id in berth_ids:
        berth = snap.berths[berth_id]
        view.berths[berth_id] = replace(berth, folder_id=copy_id(berth.folder_id))
    return view


def account_groups(snap: Snapshot) -> list[tuple[str, list[str]]]:
    """Each account's sessions, accounts in Settings Agent's order, then unknown Claude sessions, then the rest."""
    from berth import accounts

    groups: dict[str, list[str]] = {}
    for berth in snap.berths.values():
        if berth.folder_id in snap.folders:
            groups.setdefault(account_key(berth), []).append(berth.id)
    return sorted(groups.items(), key=lambda item: (accounts.rank_of(item[0]), item[0]))


def build_account_tree(snap: Snapshot, opened_at: datetime, query: str,
                       sort: str = "recent") -> tuple[list[Row], Counts]:
    """The account view: one heading per account, then the tree of only that account's sessions under it.

    The counts in the screen's header are the whole catalog's, as in the tree.
    """
    counts = _counts(_walk(snap, opened_at, "", sort))
    rows: list[Row] = []
    for key, berth_ids in account_groups(snap):
        view = _account_snapshot(snap, key, berth_ids)
        block = _walk_folder(view, view.root, 0, False, False, opened_at, query, frozenset(), sort)
        tint = view.folders[view.root].color
        for row in block:
            row.tint = tint
            row.account_key = key
            row.by_account = True
        if block:
            block[0].heading = "account"
        rows.extend(block)
    return rows, counts


def berth_as_row(snap: Snapshot, berth_id: str | None, opened_at: datetime) -> Row | None:
    if not berth_id or berth_id not in snap.berths:
        return None
    berth = snap.berths[berth_id]
    if berth.folder_id not in snap.folders:
        return None
    path = snap.folder_path(berth.folder_id)
    return make_berth_row(snap, berth, path.count("/") + 1, path, opened_at)


def resolve_folder(snap: Snapshot, needle: str) -> Resolve:
    pairs = sorted((snap.folder_path(fid), fid) for fid in snap.folders)
    exact = [fid for path, fid in pairs if path == needle]
    if len(exact) == 1:
        return Resolve("one", exact[0], (needle,))
    folded = needle.casefold()
    exact_ci = [fid for path, fid in pairs if path.casefold() == folded]
    if len(exact_ci) == 1:
        return Resolve("one", exact_ci[0], tuple(snap.folder_path(item) for item in exact_ci))
    if len(exact_ci) > 1:
        return Resolve("many", None, tuple(snap.folder_path(item) for item in exact_ci))
    sub = [fid for path, fid in pairs if folded in path.casefold()]
    labels = tuple(snap.folder_path(item) for item in sub)
    if len(sub) == 1:
        return Resolve("one", sub[0], labels)
    if not sub:
        return Resolve("none", None, ())
    return Resolve("many", None, labels)


def make_berth_row(snap: Snapshot, berth: Berth, depth: int, path: str, opened_at: datetime) -> Row:
    twins = [
        other.id
        for other in snap.berths.values()
        if other.folder_id == berth.folder_id and other.title == berth.title
    ]
    label = path if len(twins) <= 1 else f"{path} · {berth.id}"
    is_new = berth.state == "live" and berth.created_at is not None and berth.created_at > opened_at
    folder = snap.folders.get(berth.folder_id)
    return Row(
        kind="berth",
        id=berth.id,
        depth=depth,
        title=berth.title,
        state=berth.state,
        note=berth.note,
        path=path,
        path_label=label,
        protect=berth.protect,
        pinned=berth.pinned,
        urgent=getattr(berth, "urgent", False),
        follow_up=getattr(berth, "follow_up", False),
        account=getattr(berth, "account", ""),
        account_stale=getattr(berth, "account_stale", False),
        new=is_new,
        busy=berth.busy,
        container_id=berth.folder_id,
        color=folder.color if folder is not None else None,
        berth_kind=berth.kind,
        agent_name=berth.agent_name,
        park_reason=berth.park_reason,
        recency=berth.last_used_at,
        cwd=berth.cwd,
        recipe_display=berth.recipe_display,
        excerpt=berth.excerpt,
        excerpt_at=berth.excerpt_at,
        agent_version=berth.agent_version,
    )


def _counts(rows: list[Row]) -> Counts:
    counts = Counts()
    for row in rows:
        if row.kind != "berth":
            continue
        if row.state == "live":
            counts.live += 1
        elif is_idle(row.state, row.park_reason):
            counts.idle += 1
        elif is_sleeping(row.state, row.park_reason):
            counts.sleeping += 1
        elif row.state == "parked":
            counts.parked += 1
        elif row.state == "playing":
            counts.playing += 1
        elif row.state == "pausing":
            counts.pausing += 1
        if row.new:
            counts.new += 1
        if row.busy and row.state == "live":
            counts.busy += 1
        if row.berth_kind == "grok":
            counts.grok += 1
        elif row.berth_kind == "shell":
            counts.shell += 1
    return counts


def _walk(snap: Snapshot, opened_at: datetime, query: str, sort: str = "recent") -> list[Row]:
    blocks = [
        _walk_folder(snap, fid, 0, False, False, opened_at, query, frozenset(), sort)
        for fid in snap.children.get(None, [])
    ]
    blocks = [block for block in blocks if block]
    if sort == "recent":
        blocks = _by_recency(blocks, key=lambda block: block[0])
    rows: list[Row] = []
    for block in blocks:
        rows.extend(block)
    return rows


def _by_recency(items: list, *, key) -> list:
    """Newest first. Never-used last. Ties by title, so the order is stable."""
    return sorted(items, key=lambda item: (_newest_first(key(item).recency), key(item).title.casefold(), key(item).id))


def _newest_first(stamp: str) -> tuple[int, str]:
    if not stamp:
        return (1, "")
    # Invert each character so an ascending sort puts the newest stamp first.
    return (0, "".join(chr(0x10FFFF - ord(char)) for char in stamp))


def _walk_folder(
    snap: Snapshot,
    fid: str,
    depth: int,
    archived: bool,
    ancestor_hit: bool,
    opened_at: datetime,
    query: str,
    stack: frozenset[str],
    sort: str = "recent",
) -> list[Row]:
    if fid in stack or fid not in snap.folders:
        return []
    stack = stack | {fid}
    folder = snap.folders[fid]
    here_archived = archived or folder.archived
    path = snap.folder_path(fid)
    needle = query.casefold()
    self_hit = bool(needle) and (
        needle in folder.name.casefold() or needle in path.casefold() or needle in folder.note.casefold()
    )
    hit = ancestor_hit or self_hit
    child_blocks = [
        _walk_folder(snap, cid, depth + 1, here_archived, hit, opened_at, query, stack, sort)
        for cid in snap.children.get(fid, [])
    ]
    berth_rows: list[Row] = []
    for bid in snap.berths_in.get(fid, []):
        berth = snap.berths[bid]
        if here_archived and berth.state not in LIVE_STATES:
            continue
        if needle and not hit and not _berth_match(berth, path, needle):
            continue
        berth_rows.append(make_berth_row(snap, berth, depth + 1, path, opened_at))
    berth_rows.sort(key=lambda row: (0 if row.state in LIVE_STATES else 1, row.title.casefold(), row.id))
    live = parked = playing = pausing = new_n = busy_n = grok_n = shell_n = 0
    for row in berth_rows:
        live, parked, playing, pausing, new_n, busy_n, grok_n, shell_n = _add_berth(
            row, live, parked, playing, pausing, new_n, busy_n, grok_n, shell_n
        )
    # Parked counts paused rows only; idle and sleeping ones are counted on their own.
    sleeping_n = sum(1 for row in berth_rows if is_sleeping(row.state, row.park_reason))
    idle_n = sum(1 for row in berth_rows if is_idle(row.state, row.park_reason))
    parked -= sleeping_n + idle_n
    flat: list[Row] = []
    for block in child_blocks:
        if not block:
            continue
        head = block[0]
        live += head.live_n
        parked += head.parked_n
        sleeping_n += head.sleeping_n
        idle_n += head.idle_n
        playing += head.playing_n
        pausing += head.pausing_n
        new_n += head.new_n
        busy_n += head.busy_n
        grok_n += head.grok_n
        shell_n += head.shell_n
        flat.extend(block)
    if not flat and not berth_rows:
        if here_archived or (needle and not self_hit):
            return []
    parent = folder.parent_id if folder.parent_id in snap.folders else None
    recency = max(
        [row.recency for row in berth_rows if row.recency]
        + [block[0].recency for block in child_blocks if block and block[0].recency],
        default="",
    )
    own = Row(
        kind="folder",
        id=fid,
        depth=depth,
        title=folder.name,
        path=path,
        path_label=path,
        container_id=parent,
        live_n=live,
        parked_n=parked,
        sleeping_n=sleeping_n,
        idle_n=idle_n,
        playing_n=playing,
        pausing_n=pausing,
        new_n=new_n,
        busy_n=busy_n,
        grok_n=grok_n,
        shell_n=shell_n,
        archived=folder.archived,
        color=folder.color,
        note=folder.note,
        recency=recency,
    )
    if sort != "recent":
        return [own, *flat, *berth_rows]
    items: list = [block for block in child_blocks if block] + [[row] for row in berth_rows]
    ordered: list[Row] = [own]
    for item in _by_recency(items, key=lambda entry: entry[0]):
        ordered.extend(item)
    return ordered


def _add_berth(
    row: Row,
    live: int,
    parked: int,
    playing: int,
    pausing: int,
    new_n: int,
    busy_n: int,
    grok_n: int,
    shell_n: int,
) -> tuple[int, int, int, int, int, int, int, int]:
    if row.state == "live":
        live += 1
    elif row.state == "parked":
        parked += 1
    elif row.state == "playing":
        playing += 1
    elif row.state == "pausing":
        pausing += 1
    if row.new:
        new_n += 1
    if row.busy and row.state == "live":
        busy_n += 1
    if row.berth_kind == "grok":
        grok_n += 1
    elif row.berth_kind == "shell":
        shell_n += 1
    return live, parked, playing, pausing, new_n, busy_n, grok_n, shell_n


def _berth_match(berth: Berth, path: str, needle: str) -> bool:
    fields = (berth.title, berth.note, berth.excerpt, berth.cwd, path)
    return any(needle in (field or "").casefold() for field in fields)
