"""The text boxes edit like a prompt box."""

from __future__ import annotations

from berth.lineedit import CANCEL, CHANGED, IGNORED, MOVED, SUBMIT, LineEditor, cell_width


def _box(text: str = "", cursor: int = -1) -> tuple[LineEditor, list[str]]:
    copied: list[str] = []
    box = LineEditor(text, cursor=cursor, copy=lambda value: copied.append(value) or True, paste=lambda: "from clip")
    return box, copied


def _type(box: LineEditor, keys) -> None:
    for key in keys:
        box.handle(key)


def test_every_printable_key_types_including_j_k_q_and_space():
    box, _ = _box()
    _type(box, ["j", "k", "space", "q", " ", "G", "/", "⑂"])
    assert box.text == "jk q G/⑂"
    assert box.cursor == len(box.text)


def test_arrows_move_and_typing_inserts_at_the_cursor():
    box, _ = _box("Tutors")
    _type(box, ["left", "left", "left", "x"])
    assert box.text == "Tutxors"
    _type(box, ["home", "A", "end", "Z"])
    assert box.text == "ATutxorsZ"
    _type(box, ["ctrl-a", "ctrl-d"])
    assert box.text == "TutxorsZ"


def test_word_motion_and_word_deletion():
    box, _ = _box("HOMESTEAD tutor prime")
    assert box.handle("alt-b") == MOVED and box.cursor == len("HOMESTEAD tutor ")
    box.handle("alt-backspace")
    assert box.text == "HOMESTEAD prime"
    box.handle("ctrl-a")
    box.handle("alt-f")
    assert box.cursor == len("HOMESTEAD")
    box.handle("alt-d")
    assert box.text == "HOMESTEAD"
    box.handle("ctrl-w")
    assert box.text == ""


def test_cmd_backspace_and_kill_to_end():
    box, _ = _box("one two three", cursor=4)
    box.handle("ctrl-k")
    assert box.text == "one "
    box.handle("ctrl-u")
    assert box.text == ""


def test_shift_selects_and_typing_replaces_the_selection():
    box, _ = _box("Week 1")
    _type(box, ["shift-left", "shift-left"])
    assert box.selected() == " 1"
    box.handle("2")
    assert box.text == "Week2"
    _type(box, ["shift-home"])
    assert box.selected() == "Week2"
    box.handle("backspace")
    assert box.text == "" and box.selection() is None


def test_an_arrow_with_a_selection_lands_on_its_edge():
    box, _ = _box("abcdef", cursor=4)
    _type(box, ["shift-left", "shift-left"])
    box.handle("right")
    assert box.cursor == 4 and box.selection() is None


def test_copy_cut_and_paste_use_the_clipboard():
    box, copied = _box("alpha beta")
    _type(box, ["shift-alt-left"])
    assert box.handle("ctrl-c") == MOVED and copied == ["beta"] and box.note == "copied"
    assert box.handle("ctrl-x") == CHANGED and box.text == "alpha " and copied == ["beta", "beta"]
    box.handle("ctrl-v")
    assert box.text == "alpha from clip"


def test_ctrl_c_with_no_selection_cancels_and_esc_first_drops_a_selection():
    box, _ = _box("abc")
    box.handle("shift-left")
    assert box.handle("esc") == MOVED and box.selection() is None
    assert box.handle("esc") == CANCEL
    assert box.handle("ctrl-c") == CANCEL
    assert box.handle("enter") == SUBMIT


def test_a_paste_is_one_block_and_line_breaks_become_spaces():
    box, _ = _box("x", cursor=0)
    assert box.handle("paste:Tutor\nPrime\tjk\r\n") == CHANGED
    assert box.text == "Tutor Prime jk x"


def test_undo_and_redo():
    box, _ = _box()
    _type(box, ["a", "b", "paste:cd"])
    box.handle("ctrl-z")
    assert box.text == "ab"
    box.handle("ctrl-z")
    assert box.text == "a"
    box.handle("ctrl-y")
    assert box.text == "ab"


def test_the_limit_holds_and_unknown_keys_are_ignored():
    box = LineEditor("", limit=5)
    box.handle("paste:abcdefgh")
    assert box.text == "abcde"
    assert box.handle("f") == IGNORED
    assert box.handle("tab") == IGNORED and box.handle("ctrl-g") == IGNORED


def test_view_scrolls_to_the_cursor_and_counts_wide_characters():
    box = LineEditor("a" * 50)
    shown, cursor, _ = box.view(20)
    assert cursor == 19 and shown.endswith("a") and len(shown) <= 20
    box.handle("home")
    shown, cursor, _ = box.view(20)
    assert cursor == 0 and len(shown) == 20
    assert cell_width("日本") == 4
    wide = LineEditor("日本 x")
    assert wide.view(40)[1] == cell_width("日本 x")


def test_a_trailing_space_moves_the_cursor():
    box = LineEditor("Week")
    box.handle("space")
    assert box.view(40)[1] == 5


def test_a_drag_selects_copies_and_backspace_deletes_it():
    box, copied = _box("hello world")
    box.view(40)
    box.press(6, 1.0)
    box.drag(11)
    assert box.release(11) == MOVED and box.selected() == "world" and copied == ["world"]
    assert box.handle("backspace") == CHANGED and box.text == "hello "


def test_a_click_places_the_cursor_and_double_and_triple_clicks_select():
    box, copied = _box("alpha beta")
    box.view(40)
    box.press(3, 1.0)
    box.release(3)
    assert box.cursor == 3 and box.selection() is None and copied == []
    box.press(7, 5.0)
    box.press(7, 5.1)
    assert box.selected() == "beta"
    box.handle("x")
    assert box.text == "alpha x"
    box.press(1, 9.0)
    box.press(1, 9.1)
    box.press(1, 9.2)
    assert box.selected() == "alpha x"
    box.handle("delete")
    assert box.text == ""


def test_columns_follow_the_scrolled_view():
    box = LineEditor("0123456789" * 3)
    box.view(10)
    assert box.index_at(0) == box._start and box._start > 0
