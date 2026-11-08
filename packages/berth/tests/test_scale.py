from tests.support import make_app


def _depth(snap, folder_id: str) -> int:
    depth = 1
    seen: set[str] = set()
    current = folder_id
    while snap.folders[current].parent_id and current not in seen:
        seen.add(current)
        current = snap.folders[current].parent_id
        depth += 1
    return depth


def test_forty_berths_in_three_levels_stay_on_the_window(scale):
    app = make_app(scale, height=12, width=80)
    assert len(app.snap.berths) >= 40
    assert max(_depth(app.snap, folder_id) for folder_id in app.snap.folders) >= 3
    assert len(app.visible) > app.body_height
    assert len(app.lines()) == 12
    body = [line.text for line in app.lines()[2:-1]]
    assert len(body) <= app.body_height
    assert sum("item-" in line for line in body) < len(app.snap.berths)
    assert "item-001" in app.text()
    for _ in range(25):
        app.handle("j")
    assert app.index() >= app.body_height
    assert "item-001" not in app.text()
    assert app.current is not None
    assert app.current.title in app.text()
    for _ in range(25):
        app.handle("k")
    assert "item-001" in app.text()
    app.handle("G")
    assert "item-042" in app.text()
    assert "item-001" not in app.text()
    app.handle("g")
    assert "item-001" in app.text()
    start = app.index()
    app.handle("ctrl-d")
    assert app.index() >= start + 4
    app.handle("ctrl-u")
    assert app.index() == start
