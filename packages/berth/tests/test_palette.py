"""The guarantees in berth/palette.py, on the edge cases that matter."""

from __future__ import annotations

import math

import pytest

from berth.palette import C_MIN, DISTINCT, L_MAX, L_MIN, compute, delta_e, hex_to_oklab, report


def _tree(spec):
    rows = []

    def add(name, parent, kids):
        folder_id = f"f{len(rows)}"
        rows.append({"id": folder_id, "parent_id": parent, "name": name, "sort": len(rows), "archived": 0})
        for child in kids:
            add(child[0], folder_id, child[1])

    for name, kids in spec:
        add(name, None, kids)
    return rows


def _leaves(prefix, count):
    return [(f"{prefix}{index}", []) for index in range(count)]


def _hue(hex_value):
    _, a, b = hex_to_oklab(hex_value)
    return math.degrees(math.atan2(b, a)) % 360


def _inside(hue, arc):
    start, end = arc
    return (start <= hue <= end) if start <= end else (hue >= start or hue <= end)


CASES = {
    "one folder": [("A", [])],
    "two folders": [("A", []), ("B", [])],
    "one sub": [("A", [("A1", [])])],
    "1 sub and 900 subs": [("A", [("A1", [])]), ("B", _leaves("B", 900))],
    "seven roots": [(f"R{index}", []) for index in range(7)],
    "thirty roots": [(f"R{index}", []) for index in range(30)],
    "four deep": [
        ("A", [("A1", [("A11", [("A111", []), ("A112", [])]), ("A12", [])]), ("A2", []), ("A3", [])]),
        ("B", _leaves("B", 2)),
    ],
    "forty subs": [("A", _leaves("A", 40)), ("B", [])],
    "100 subs and 1 sub": [("A", _leaves("A", 100)), ("B", [("B1", [])])],
    "128 nested": [(f"R{r}", [(f"R{r}c{c}", _leaves(f"R{r}c{c}x", 4)) for c in range(3)]) for r in range(8)],
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_neighbors_parents_and_children_are_distinct(name):
    rows = _tree(CASES[name])
    swatches = compute(rows)
    result = report(rows, swatches)
    assert result["ok"], result
    for key in ("adjacent_roots", "parent_child", "adjacent_siblings"):
        low = result[key]["min"]
        assert low is None or low >= DISTINCT


def test_every_descendant_stays_inside_its_roots_hue_arc():
    rows = _tree(CASES["four deep"] + [("C", _leaves("C", 12))])
    swatches = compute(rows)
    by_id = {row["id"]: row for row in rows}

    def root_of(folder_id):
        while by_id[folder_id]["parent_id"]:
            folder_id = by_id[folder_id]["parent_id"]
        return folder_id

    for row in rows:
        root = swatches[root_of(row["id"])]
        assert _inside(swatches[row["id"]].h, root.arc), row["name"]
    arcs = sorted(swatches[row["id"]].arc for row in rows if row["parent_id"] is None)
    for left, right in zip(arcs, arcs[1:]):
        assert left[1] <= right[0]


def _width(swatch):
    return (swatch.arc[1] - swatch.arc[0]) % 360


def test_hue_share_follows_the_subtree_size():
    rows = _tree([("small", _leaves("s", 1)), ("mid", _leaves("m", 6)), ("big", _leaves("b", 20))])
    swatches = compute(rows)
    width = {row["name"]: _width(swatches[row["id"]]) for row in rows if row["parent_id"] is None}
    assert width["small"] < width["mid"] < width["big"]


def test_every_folder_gets_an_equal_share_of_the_wheel():
    """A family of 101 and a family of 2 do not split the wheel in half: 101 slots to 2."""
    rows = _tree([("A", _leaves("A", 100)), ("B", [("B1", [])])])
    swatches = compute(rows)
    big, small = (swatches[row["id"]] for row in rows if row["parent_id"] is None)
    assert math.isclose(_width(big) / _width(small), 101 / 2, rel_tol=1e-6)
    assert _width(small) < 360 * 3 / 103


def test_a_new_subfolder_anywhere_rebalances_every_family():
    rows = _tree([("A", [("A1", [("A11", [])]), ("A2", [])]), ("B", _leaves("B", 3)), ("C", _leaves("C", 2))])
    first = {key: item.h for key, item in compute(rows).items()}
    rows.append({"id": "deep", "parent_id": rows[1]["id"], "name": "A1b", "sort": 99, "archived": 0})
    second = {key: item.h for key, item in compute(rows).items()}
    others = [row["id"] for row in rows if row["name"].startswith(("B", "C"))]
    assert all(abs(first[key] - second[key]) > 1e-6 for key in others)


def test_a_deep_family_like_homestead_is_distinct_everywhere():
    """2026-10-23: HOMESTEAD's five nested folders were browns 0.03 apart."""
    rows = _tree(
        [
            ("BERTH", []),
            ("HOMESTEAD", [("education", [("Competitor", [("Kyriba", []), ("Stuut", []), ("Trovata", [])])]), ("Onsite", [])]),
            ("PERSONAL", [("Bach", []), ("Claude", []), ("OBSIDIAN", [])]),
        ]
    )
    swatches = compute(rows)
    result = report(rows, swatches)
    assert result["ok"]
    assert result["any_two"]["min"] >= 0.15


def test_every_color_is_vivid_and_readable():
    for spec in CASES.values():
        rows = _tree(spec)
        for swatch in compute(rows).values():
            assert L_MIN - 1e-9 <= swatch.L <= L_MAX + 1e-9
            _, a, b = hex_to_oklab(swatch.hex)
            assert math.hypot(a, b) >= C_MIN - 0.01, swatch


def test_a_parent_sits_in_the_middle_of_its_family():
    rows = _tree([("A", _leaves("A", 6)), ("B", [])])
    swatches = compute(rows)
    parent = swatches[rows[0]["id"]]
    middle = (parent.arc[0] + _width(parent) / 2) % 360
    assert abs((parent.h - middle + 180) % 360 - 180) < 360 / 9


def test_a_900_child_family_reports_that_it_ran_out_of_colors():
    rows = _tree(CASES["1 sub and 900 subs"])
    result = report(rows, compute(rows))
    assert result["ok"]
    assert result["closest_siblings"]["min"] < DISTINCT


def test_the_same_tree_gives_the_same_colors_and_a_new_folder_rebalances():
    from berth import palette

    rows = _tree(CASES["four deep"])
    first = {key: item.hex for key, item in compute(rows).items()}
    palette._compute.cache_clear()
    assert first == {key: item.hex for key, item in compute(rows).items()}
    rows = _tree(CASES["seven roots"])
    first = {key: item.hex for key, item in compute(rows).items()}
    rows.append({"id": "new", "parent_id": None, "name": "R7", "sort": 99, "archived": 0})
    second = {key: item.hex for key, item in compute(rows).items()}
    assert sum(first[key] != second[key] for key in first) >= 6


def test_inbox_and_archived_folders_get_no_color():
    rows = _tree([("Inbox", []), ("A", [])])
    rows.append({"id": "old", "parent_id": None, "name": "Old", "sort": 9, "archived": 1})
    swatches = compute(rows)
    assert set(swatches) == {rows[1]["id"]}


def test_delta_e_is_zero_for_the_same_color_and_large_for_opposites():
    assert delta_e("#336699", "#336699") == 0
    assert delta_e("#000000", "#ffffff") > 0.9
