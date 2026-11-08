from berth.input import coalesce, csi_key, key_from_bstate


def test_one_arrow_moves_one():
    assert coalesce(["down"], 4) == (1, [])
    assert coalesce(["up"], 4) == (-1, [])
    assert coalesce(["j"], 4) == (1, [])
    assert coalesce(["k"], 4) == (-1, [])


def test_a_swipe_burst_is_one_capped_move_and_does_not_keep_the_rest():
    keys = ["down"] * 30
    assert coalesce(keys, 4) == (4, [])


def test_opposite_notches_in_one_burst_cancel_before_the_cap():
    assert coalesce(["down", "down", "up"], 4) == (1, [])


def test_a_command_after_the_swipe_is_kept():
    delta, rest = coalesce(["down", "down", "down", "enter"], 4)
    assert delta == 3
    assert rest == ["enter"]


def test_a_mouse_move_is_not_a_scroll():
    assert key_from_bstate(1 << 20, 4, up=0, down=0, click=0, place=1 << 20) is None


def test_kitty_escape_closes_a_screen():
    assert csi_key("27u") == "esc"
    assert csi_key("27;1u") == "esc"


def test_a_command_before_motion_is_not_swallowed():
    delta, rest = coalesce(["enter", "down"], 4)
    assert delta == 0
    assert rest == ["enter", "down"]


def test_modified_keys_decode_as_the_mac_sends_them():
    from berth.input import alt_key, normalize

    cases = {
        "1;2D": "shift-left", "1;3D": "alt-left", "1;5C": "ctrl-right", "1;4D": "shift-alt-left",
        "1;9D": "home", "1;10C": "shift-end", "H": "home", "F": "end", "1;2H": "shift-home",
        "3~": "delete", "3;3~": "alt-delete", "5~": "pageup", "6~": "pagedown",
        "27u": "esc", "127u": "backspace", "127;3u": "alt-backspace", "97;5u": "ctrl-a", "13u": "enter",
    }
    for text, name in cases.items():
        assert csi_key(text) == name, text
    assert [normalize(c) for c in "\x01\x05\x15\x17\x03\x16\x1a\x0b"] == [
        "ctrl-a", "ctrl-e", "ctrl-u", "ctrl-w", "ctrl-c", "ctrl-v", "ctrl-z", "ctrl-k",
    ]
    assert alt_key("b") == "alt-b" and alt_key("\x7f") == "alt-backspace"


def test_j_and_k_are_letters_while_typing_and_motion_in_the_tree():
    from berth.input import plan_keys

    assert plan_keys(["j", "j", "k"], 4, swipe=False) == (0, ["j", "j", "k"])
    assert plan_keys(["j", "j", "k"], 4, swipe=True) == (1, [])


def test_a_text_box_reads_press_drag_and_release():
    from berth.input import mouse_parts, sgr_mouse

    assert sgr_mouse("<0;19;2M") == "mouse:press:18:1"
    assert sgr_mouse("<32;25;2M") == "mouse:drag:24:1"
    assert sgr_mouse("<0;25;2m") == "mouse:release:24:1"
    assert sgr_mouse("<64;5;5M") == "up" and sgr_mouse("<65;5;5M") == "down"
    assert sgr_mouse("<2;5;5M") is None
    assert mouse_parts("mouse:drag:24:1") == ("drag", 24, 1)
