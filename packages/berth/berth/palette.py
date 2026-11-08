"""Folder colors, computed from the folder tree. Nobody picks them.

Colors live in OKLab (Ottosson 2020), where Euclidean distance (``delta_e``)
tracks how different two colors look. Rough scale for small swatches like the
cmux sidebar: below 0.02 most people cannot tell two colors apart, 0.02 to
0.05 is visible side by side, and from ``DISTINCT`` (0.08) they read as
different at a glance. ``report`` measures the pairs that matter against it.

Two goals, in this order:

1. **Every folder gets an equal share.** The hue wheel is cut into one slot
   per folder, at every depth, in tree order: a family is a contiguous run of
   slots, with the parent in the middle of its own family. So a family's arc
   is exactly its share of all folders. A root with 100 subfolders gets 101
   slots and a root with one subfolder gets 2, not half the wheel each. One
   empty slot separates root families. Adding a folder anywhere moves every
   slot, so every color is re-balanced. (This replaces per-level splitting
   in the style of Tennekes and de Jonge's "Tree Colors", 2014, whose arcs
   shrink at every level and turned deep families into one band of brown.)
2. **Any two folders are as far apart as the colors allow.** Each folder
   picks its lightness (``L_STEPS`` levels), and its hue within its own slot
   (``HUE_JITTER``), always at the most chroma sRGB can show there and never
   below ``C_MIN``, so there are no grays. The pairs that must differ at a
   glance come first: slot neighbors, parent and child, adjacent siblings
   ("related" pairs). Then the smallest distance between any two folders is
   made as large as possible:

   * a start that is a greedy coloring of the related pairs with four
     lightness levels, parents before children;
   * a spreading phase that lowers a repulsion energy, the sum of
     ``(DISTINCT / d) ** ENERGY_POWER`` over pairs (the force idea behind
     iwanthue and Riesz-energy point spreading), which sees every neighbor and
     not only the nearest;
   * a maximin polish (Glasbey et al. 2007, "Colour displays for categorical
     images"): the folder with the closest neighbor moves to its option
     farthest from that neighbor, which never lets the smallest distance
     shrink.

   A related pair below ``RELATED_FLOOR`` counts as ``RELATED`` times its
   distance, so it is pushed apart before anything else. A few runs start
   from seeds taken from the tree itself; the best result wins, so the same
   tree always gives the same colors.

The result is cached per tree, because the watcher calls ``compute`` every
tick. Up to ``COMPARE_ALL`` folders every pair is compared. Past
``OPTIMIZE_LIMIT`` folders only the start is used: related pairs still differ
at a glance, but two folders far apart in one huge family can look the same
(``report`` shows it as ``closest_siblings``).

Inbox is "not in a folder" and gets no color. Archived folders are skipped.
"""

from __future__ import annotations

import math
import random
import zlib
from dataclasses import dataclass, field
from functools import lru_cache

DISTINCT = 0.08
NOTICEABLE = 0.02

HUE_START = 25.0
FAMILY_GAP = 1.0
HUE_JITTER = 0.3
L_MIN = 0.45
L_MAX = 0.85
L_STEPS = 9
C_CAP = 0.18
C_MIN = 0.09
CHROMA_STEPS = (1.0,)
RELATED = 0.25
RELATED_FLOOR = 0.1
OPTIMIZE_LIMIT = 250
START_BUDGET = 240
MAX_STARTS = 8
ENERGY_POWER = 6
PASSES = 10
NEAR_HUE = 100.0
COMPARE_ALL = 80


@dataclass
class Node:
    id: str
    parent_id: str | None
    name: str
    sort: int = 0
    children: list["Node"] = field(default_factory=list)

    @property
    def weight(self) -> int:
        return 1 + sum(child.weight for child in self.children)


@dataclass(frozen=True)
class Swatch:
    folder_id: str
    hex: str
    L: float
    C: float
    h: float
    depth: int
    arc: tuple[float, float]


# --- OKLab ---------------------------------------------------------------


def _to_linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _from_linear(channel: float) -> float:
    return 12.92 * channel if channel <= 0.0031308 else 1.055 * channel ** (1 / 2.4) - 0.055


def oklab_to_linear_srgb(L: float, a: float, b: float) -> tuple[float, float, float]:
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_**3, m_**3, s_**3
    return (
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    )


def srgb_to_oklab(red: float, green: float, blue: float) -> tuple[float, float, float]:
    r, g, b = (_to_linear(item) for item in (red, green, blue))
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (math.copysign(abs(item) ** (1 / 3), item) for item in (l, m, s))
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def _in_gamut(L: float, C: float, rad: float) -> bool:
    rgb = oklab_to_linear_srgb(L, C * math.cos(rad), C * math.sin(rad))
    return all(-1e-6 <= item <= 1 + 1e-6 for item in rgb)


def oklch_to_hex(L: float, C: float, h: float) -> tuple[str, float]:
    """The sRGB hex, and the chroma used: the most of ``C`` that sRGB can show."""
    rad = math.radians(h)
    used = C
    if not _in_gamut(L, C, rad):
        low, high = 0.0, C
        for _ in range(30):
            mid = (low + high) / 2
            if _in_gamut(L, mid, rad):
                low = mid
            else:
                high = mid
        used = low
    rgb = oklab_to_linear_srgb(L, used * math.cos(rad), used * math.sin(rad))
    channels = [round(max(0.0, min(1.0, _from_linear(max(0.0, item)))) * 255) for item in rgb]
    return "#{:02x}{:02x}{:02x}".format(*channels), used


def hex_to_oklab(text: str) -> tuple[float, float, float]:
    raw = text.lstrip("#")
    return srgb_to_oklab(*(int(raw[index : index + 2], 16) / 255 for index in (0, 2, 4)))


def delta_e(left: str, right: str) -> float:
    a = hex_to_oklab(left)
    b = hex_to_oklab(right)
    return math.dist(a, b)


# --- allocation ----------------------------------------------------------


def build_tree(folders) -> list[Node]:
    """Roots in sort order. ``folders`` rows need id, parent_id, name, sort, archived."""
    nodes: dict[str, Node] = {}
    for row in folders:
        if row["archived"]:
            continue
        nodes[row["id"]] = Node(row["id"], row["parent_id"], row["name"], row["sort"] or 0)
    roots: list[Node] = []
    for node in nodes.values():
        parent = nodes.get(node.parent_id) if node.parent_id else None
        if parent is None:
            roots.append(node)
        else:
            parent.children.append(node)
    key = lambda node: (node.sort, node.name.casefold(), node.id)  # noqa: E731
    for node in nodes.values():
        node.children.sort(key=key)
    roots.sort(key=key)
    return roots


def family_order(node: Node, depth: int = 0) -> list[tuple[Node, int]]:
    """The subtree in slot order: the first half of the children, the node, the rest."""
    half = len(node.children) // 2
    out: list[tuple[Node, int]] = []
    for child in node.children[:half]:
        out.extend(family_order(child, depth + 1))
    out.append((node, depth))
    for child in node.children[half:]:
        out.extend(family_order(child, depth + 1))
    return out


@lru_cache(maxsize=8192)
def max_chroma(L: float, h: float) -> float:
    """The most chroma sRGB can show at this lightness and hue."""
    rad = math.radians(h)
    low, high = 0.0, 0.4
    for _ in range(22):
        mid = (low + high) / 2
        if _in_gamut(L, mid, rad):
            low = mid
        else:
            high = mid
    return low


@dataclass(frozen=True)
class _Pick:
    L: float
    C: float
    h: float
    lab: tuple[float, float, float]


def compute(folders, *, inbox_name: str = "Inbox") -> dict[str, Swatch]:
    """Colors for every folder but Inbox and archived ones. Cached per tree."""
    key = tuple(
        sorted(
            (str(row["id"]), row["parent_id"] or "", str(row["name"]), int(row["sort"] or 0), bool(row["archived"]))
            for row in folders
        )
    )
    return dict(_compute(key, inbox_name))


@lru_cache(maxsize=32)
def _compute(key: tuple, inbox_name: str) -> tuple[tuple[str, Swatch], ...]:
    rows = [{"id": i, "parent_id": p or None, "name": n, "sort": s, "archived": a} for i, p, n, s, a in key]
    roots = [node for node in build_tree(rows) if not (node.parent_id is None and node.name == inbox_name)]
    if not roots:
        return ()
    # 1. Slots: one per folder, families contiguous, a gap between root families.
    order: list[tuple[Node, int]] = []
    position: dict[str, float] = {}
    at = 0.0
    for root in roots:
        for node, depth in family_order(root):
            order.append((node, depth))
            position[node.id] = at
            at += 1.0
        at += FAMILY_GAP
    slot = 360.0 / at
    index_of = {node.id: index for index, (node, _depth) in enumerate(order)}
    hues = [(HUE_START + (position[node.id] + 0.5) * slot) % 360 for node, _depth in order]
    related = _related(roots, order, index_of)
    # 2. Every vivid color each folder may take: lightness steps, chroma
    # steps, and small hue moves that stay inside its own slot.
    optimize = len(order) <= OPTIMIZE_LIMIT
    jitter = (0.0, -HUE_JITTER, HUE_JITTER, -HUE_JITTER / 2, HUE_JITTER / 2) if optimize else (0.0,)
    levels = [L_MIN + (L_MAX - L_MIN) * step / (L_STEPS - 1) for step in range(L_STEPS)]
    choices = [_options(hues[index], slot, jitter, levels) for index in range(len(order))]
    # 3. A start that already keeps related folders apart: a greedy coloring
    # of the related pairs with four lightness levels across what each hue
    # can show vividly, in slot order.
    preorder: list[int] = []
    stack = list(reversed(roots))
    while stack:
        node = stack.pop()
        preorder.append(index_of[node.id])
        stack.extend(reversed(node.children))
    chosen = _start(choices, hues, related, preorder)
    # 4. Maximin refinement over every pair of folders. One run only polishes
    # that start, so the result is never worse than it; the others spread
    # first, from the start and from seeded ones (the seed is the tree, so the
    # same tree gives the same colors). The best result wins.
    if optimize:
        slots = [position[node.id] for node, _ in order]
        seed = zlib.crc32(repr(key).encode())
        runs = max(1, min(MAX_STARTS, START_BUDGET // len(order)))
        plans = [(chosen, False), (chosen, True)] + [
            ([random.Random(seed + number).randrange(len(options)) for options in choices], True)
            for number in range(runs - 1)
        ]
        best = None
        for start, spread in plans:
            trial = list(start)
            value = _refine(choices, trial, slots, at, NEAR_HUE / slot, related, spread=spread)
            if best is None or value > best[0]:
                best = (value, trial)
        chosen = best[1]
    out: list[tuple[str, Swatch]] = []
    for index, (node, depth) in enumerate(order):
        pick = choices[index][chosen[index]]
        lo, hi = _subtree_span(node, position)
        arc = ((HUE_START + lo * slot) % 360, (HUE_START + (hi + 1) * slot) % 360)
        hex_value, used = oklch_to_hex(pick.L, pick.C, pick.h)
        out.append((node.id, Swatch(node.id, hex_value, pick.L, used, pick.h, depth, arc)))
    return tuple(out)


def _options(base: float, slot: float, jitter, levels) -> list[_Pick]:
    found = []
    for offset in jitter:
        h = (base + offset * slot) % 360
        rad = math.radians(h)
        for L in levels:
            top = min(C_CAP, max_chroma(round(L, 4), round(h, 3)))
            for factor in CHROMA_STEPS:
                chroma = top * factor
                if chroma >= C_MIN:
                    found.append(_Pick(L, chroma, h, (L, chroma * math.cos(rad), chroma * math.sin(rad))))
    if found:
        return found
    # A hue sRGB cannot show vividly at any allowed lightness: its most vivid.
    best = max(levels, key=lambda L: max_chroma(round(L, 4), round(base, 3)))
    chroma = min(C_CAP, max_chroma(round(best, 4), round(base, 3)))
    rad = math.radians(base)
    return [_Pick(best, chroma, base, (best, chroma * math.cos(rad), chroma * math.sin(rad)))]


def _start(choices: list[list[_Pick]], hues: list[float], related: list[set[int]], preorder: list[int]) -> list[int]:
    """Each folder takes a lightness level away from its related folders already placed.

    Parents go before their children, so a child only has to avoid its
    parent, its previous sibling, and whatever slot neighbor is placed.
    """
    chosen: list[int] = [0] * len(choices)
    placed: dict[int, float] = {}
    for index in preorder:
        options = choices[index]
        straight = [k for k, item in enumerate(options) if item.h == hues[index]] or list(range(len(options)))
        band = [options[k].L for k in straight]
        low, high = min(band), max(band)
        targets = [low, high, low + (high - low) / 3, low + 2 * (high - low) / 3]
        taken = [placed[other] for other in related[index] if other in placed]

        def room(target: float) -> float:
            return min((abs(target - level) for level in taken), default=1.0)

        enough = (high - low) / 3 - 1e-9
        target = next((item for item in targets if room(item) >= enough), max(targets, key=room))
        pick = min(straight, key=lambda k: (abs(options[k].L - target), -options[k].C))
        chosen[index] = pick
        placed[index] = options[pick].L
    return chosen


def _related(roots: list[Node], order: list[tuple[Node, int]], index_of: dict[str, int]) -> list[set[int]]:
    """Pairs that must stay apart first: slot neighbors, parent and child, adjacent siblings."""
    count = len(order)
    related: list[set[int]] = [set() for _ in range(count)]

    def tie(left: int, right: int) -> None:
        if left != right:
            related[left].add(right)
            related[right].add(left)

    for index in range(count):
        tie(index, (index + 1) % count)
    for group in [roots] + [node.children for node, _depth in order]:
        for left, right in zip(group, group[1:]):
            tie(index_of[left.id], index_of[right.id])
    for node, _depth in order:
        for child in node.children:
            tie(index_of[node.id], index_of[child.id])
    return related


def _subtree_span(node: Node, position: dict[str, float]) -> tuple[float, float]:
    spots = [position[node.id]]
    stack = list(node.children)
    while stack:
        item = stack.pop()
        spots.append(position[item.id])
        stack.extend(item.children)
    return min(spots), max(spots)


def _refine(
    choices: list[list[_Pick]],
    chosen: list[int],
    slots: list[float],
    total: float,
    window: float,
    related: list[set[int]],
    *,
    spread: bool = True,
) -> tuple[float, float]:
    """Move one folder at a time to the option farthest from its nearest neighbor.

    The folder whose nearest neighbor is closest goes first. A move is kept
    only when it makes that folder's nearest distance (then its second
    nearest) larger, so the smallest distance never shrinks. A related pair
    (slot neighbors, parent and child, adjacent siblings) closer than
    ``RELATED_FLOOR`` counts as ``RELATED`` times its distance, so those
    pairs are pushed past the distinct line first.
    Up to ``COMPARE_ALL`` folders every pair is compared. Past that, folders
    more than ``window`` slots apart differ in hue by more than ``NEAR_HUE``,
    which at ``C_MIN`` already keeps them well apart, and are skipped.
    """
    count = len(chosen)
    labs = [choices[index][chosen[index]].lab for index in range(count)]

    def apart(left: int, right: int) -> float:
        gap = abs(slots[left] - slots[right])
        return min(gap, total - gap)

    if count <= COMPARE_ALL:
        window = total
    near = [
        [other for other in range(count) if other != index and (apart(index, other) <= window or other in related[index])]
        for index in range(count)
    ]
    weight = [[RELATED if other in related[index] else 1.0 for other in near[index]] for index in range(count)]

    def score(index: int, lab) -> tuple[float, float]:
        first = second = math.inf
        for other, factor in zip(near[index], weight[index]):
            distance = math.dist(lab, labs[other])
            if distance < RELATED_FLOOR:
                distance *= factor
            if distance < first:
                first, second = distance, first
            elif distance < second:
                second = distance
        return first, second

    def energy(index: int, lab) -> float:
        total_energy = 0.0
        for other, factor in zip(near[index], weight[index]):
            distance = math.dist(lab, labs[other])
            if distance < RELATED_FLOOR:
                distance *= factor
            total_energy += (DISTINCT / max(distance, 1e-6)) ** ENERGY_POWER
        return total_energy

    # Spread first: every folder moves to the option with the least repulsion
    # from all its neighbors, which sees more than the single nearest one.
    for _ in range(PASSES if spread else 0):
        moved = False
        for index in range(count):
            current = energy(index, labs[index])
            best, best_energy = chosen[index], current
            for option, pick in enumerate(choices[index]):
                value = energy(index, pick.lab)
                if value < best_energy * (1 - 1e-9):
                    best, best_energy = option, value
            if best != chosen[index]:
                chosen[index] = best
                labs[index] = choices[index][best].lab
                moved = True
        if not moved:
            break
    # Then polish the closest pairs.
    for _ in range(PASSES):
        moved = False
        ranked = sorted(range(count), key=lambda index: (score(index, labs[index])[0], index))
        for index in ranked:
            best = chosen[index]
            best_score = score(index, labs[index])
            for option, pick in enumerate(choices[index]):
                if option == best:
                    continue
                candidate = score(index, pick.lab)
                if candidate[0] > best_score[0] + 1e-9 or (
                    abs(candidate[0] - best_score[0]) <= 1e-9 and candidate[1] > best_score[1] + 1e-9
                ):
                    best, best_score = option, candidate
            if best != chosen[index]:
                chosen[index] = best
                labs[index] = choices[index][best].lab
                moved = True
        if not moved:
            break
    return min((score(index, labs[index]) for index in range(count)), default=(math.inf, math.inf))


# --- how good is it ------------------------------------------------------


def report(folders, swatches: dict[str, Swatch], *, inbox_name: str = "Inbox") -> dict:
    """Every pair that has to be told apart, measured.

    ``ok`` covers the pairs that must differ at a glance: adjacent roots, each
    parent and child, adjacent siblings. Also reported: the closest pair among
    all siblings of one parent, which is where a family with hundreds of
    children runs out of distinguishable colors, and the closest two folders
    anywhere (``any_two``), since every folder shares one sidebar.
    """
    roots = [node for node in build_tree(folders) if not (node.parent_id is None and node.name == inbox_name)]
    checks: dict[str, list[tuple[str, str, float]]] = {
        "adjacent_roots": [],
        "parent_child": [],
        "adjacent_siblings": [],
    }
    closest_siblings: list[tuple[str, str, float]] = []

    def name_of(node: Node) -> str:
        return node.name

    for left, right in zip(roots, roots[1:] + roots[:1] if len(roots) > 2 else roots[1:]):
        checks["adjacent_roots"].append(
            (name_of(left), name_of(right), delta_e(swatches[left.id].hex, swatches[right.id].hex))
        )

    def walk(node: Node) -> None:
        for child in node.children:
            checks["parent_child"].append(
                (name_of(node), name_of(child), delta_e(swatches[node.id].hex, swatches[child.id].hex))
            )
        for left, right in zip(node.children, node.children[1:]):
            checks["adjacent_siblings"].append(
                (name_of(left), name_of(right), delta_e(swatches[left.id].hex, swatches[right.id].hex))
            )
        if 2 <= len(node.children) <= 400:
            best = None
            points = [(child, hex_to_oklab(swatches[child.id].hex)) for child in node.children]
            for index, (left, a) in enumerate(points):
                for right, b in points[index + 1 :]:
                    distance = math.dist(a, b)
                    if best is None or distance < best[2]:
                        best = (name_of(left), name_of(right), distance)
            if best is not None:
                closest_siblings.append(best)
        elif len(node.children) > 400:
            closest_siblings.append((name_of(node), f"{len(node.children)} children", _grid_estimate(node, swatches)))
        for child in node.children:
            walk(child)

    for root in roots:
        walk(root)

    def summary(items):
        if not items:
            return {"min": None, "below": []}
        low = min(items, key=lambda item: item[2])
        return {
            "min": round(low[2], 4),
            "below": [(a, b, round(d, 4)) for a, b, d in items if d < DISTINCT],
        }

    result = {name: summary(items) for name, items in checks.items()}
    result["closest_siblings"] = summary(closest_siblings)
    result["any_two"] = summary(_closest_pair(roots, swatches))
    result["distinct"] = DISTINCT
    result["ok"] = all(not result[name]["below"] for name in checks)
    return result


def _closest_pair(roots: list[Node], swatches: dict[str, Swatch]) -> list[tuple[str, str, float]]:
    """The two folders anywhere in the tree whose colors are closest. Skipped past 400 folders."""
    nodes: list[Node] = []
    stack = list(roots)
    while stack:
        node = stack.pop()
        nodes.append(node)
        stack.extend(node.children)
    if not 2 <= len(nodes) <= 400:
        return []
    points = [(node, hex_to_oklab(swatches[node.id].hex)) for node in nodes]
    best = None
    for index, (left, a) in enumerate(points):
        for right, b in points[index + 1 :]:
            distance = math.dist(a, b)
            if best is None or distance < best[2]:
                best = (left.name, right.name, distance)
    return [best] if best else []


def _grid_estimate(node: Node, swatches: dict[str, Swatch]) -> float:
    """Typical spacing for a very large family: region area over count."""
    points = [hex_to_oklab(swatches[child.id].hex) for child in node.children]
    ls = [p[0] for p in points]
    hues = [math.atan2(p[2], p[1]) for p in points]
    chroma = sum(math.hypot(p[1], p[2]) for p in points) / len(points)
    spread_h = (max(hues) - min(hues)) * chroma
    spread_l = max(ls) - min(ls)
    return math.sqrt(max(spread_h, 1e-9) * max(spread_l, 1e-9) / len(points))


# --- keeping the catalog and cmux on the computed colors -----------------


def _norm_hex(value) -> str:
    return (value or "").strip().lower()


def sync(db, cmux, workspaces) -> dict:
    """Make folder colors and live workspace colors what the tree says.

    Declarative: the colors are computed from the folder rows every time,
    each folder row whose stored color differs is rewritten, and each live
    workspace whose cmux color differs from its folder's is set (or cleared,
    for Inbox). ``workspaces`` is a fresh ``workspace list``; when it is None
    only the catalog is updated.
    """
    from berth.errors import BerthError
    from berth.model import INBOX_NAME

    folders = db.all_folders()
    swatches = compute(folders, inbox_name=INBOX_NAME)
    changed = []
    with db.immediate():
        for row in folders:
            want = swatches[row["id"]].hex if row["id"] in swatches else None
            if _norm_hex(row["color"]) != _norm_hex(want):
                db.conn.execute("UPDATE folder SET color = ? WHERE id = ?", (want, row["id"]))
                changed.append(row["id"])
        if changed:
            db.insert_event(None, "palette", {"folders": len(folders), "changed": len(changed)})
    painted = []
    if workspaces is not None:
        by_id = {item.id.lower(): item for item in workspaces}
        rows = db.conn.execute(
            "SELECT folder_id, cmux_workspace_id FROM berth WHERE state = 'live' AND cmux_workspace_id IS NOT NULL"
        ).fetchall()
        for row in rows:
            workspace = by_id.get(row["cmux_workspace_id"].lower())
            if workspace is None:
                continue
            want = swatches[row["folder_id"]].hex if row["folder_id"] in swatches else None
            if _norm_hex(workspace.color) == _norm_hex(want):
                continue
            try:
                cmux.set_workspace_color(workspace.id, want)
            except BerthError:
                continue
            painted.append(workspace.id)
    return {"folders_changed": changed, "workspaces_painted": painted}
