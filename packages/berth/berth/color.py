"""Folder color as a visible terminal cell and as a cmux workspace color."""

from __future__ import annotations


def parse_hex(text: str) -> tuple[int, int, int] | None:
    raw = text.strip()
    if len(raw) != 7 or not raw.startswith("#"):
        return None
    try:
        return int(raw[1:3], 16), int(raw[3:5], 16), int(raw[5:7], 16)
    except ValueError:
        return None


def xterm256(text: str) -> int | None:
    """Nearest color in the 6x6x6 cube. This curses build cannot set raw RGB."""
    rgb = parse_hex(text)
    if rgb is None:
        return None
    red, green, blue = rgb

    def level(value: int) -> int:
        return max(0, min(5, (value * 5 + 127) // 255))

    return 16 + 36 * level(red) + 6 * level(green) + level(blue)


def paint_workspace(db, cmux, workspace_id: str | None, folder_id: str | None) -> bool:
    """Set one workspace to its folder color.

    A folder with no color is left alone. A cmux that cannot set a color
    does not fail the adopt that filed the workspace.
    """
    if not workspace_id or not folder_id:
        return False
    folder = db.folder_by_id(folder_id)
    if folder is None or not folder["color"]:
        return False
    setter = getattr(cmux, "set_workspace_color", None)
    if setter is None:
        return False
    setter(workspace_id, folder["color"])
    return True


def paint_berth(db, cmux, berth_id: str) -> bool:
    """Color the live workspace of one berth from the folder it is in."""
    row = db.get_berth(berth_id)
    if row is None or row["state"] != "live":
        return False
    return paint_workspace(db, cmux, row["cmux_workspace_id"], row["folder_id"])


# The account view's exact colors (2026-11-05), for a terminal that can set its palette (cmux's Ghostty): a row is
# its account's color mixed into the dark background at ROW_SHARE, a heading at BAND_SHARE.
DARK = (30, 30, 32)  # cmux's default dark theme, which the tints mix into
ROW_SHARE, BAND_SHARE = 0.22, 0.80


def _luminance(rgb: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(value) for value in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def tint_rgb(text: str | None, strong: bool = False, *, selected: bool = False) -> tuple[tuple[int, int, int], bool] | None:
    """An account's exact color behind a line: (background rgb, dark text?). Dark text when it reads better.

    A selected heading is the account's own color, brighter than the band, rather than reversed to a dark bar.
    """
    rgb = parse_hex(text or "")
    if rgb is None:
        return None
    share = (1.0 if selected else BAND_SHARE) if strong else ROW_SHARE
    mixed = tuple(int(round(value * share + base * (1 - share))) for value, base in zip(rgb, DARK))
    light = _luminance(mixed)
    return mixed, (light + 0.05) / 0.05 > 1.05 / (light + 0.05)


def tint256(text: str | None, strong: bool = False) -> tuple[int, int] | None:
    """An account's color behind a line of the Berth screen: (background, foreground) in the 256-color cube.

    A row gets the color darkened to about a third, so the terminal's own colors still read on it; an account's
    heading gets it at about two thirds, with black or white text, whichever reads better on it.
    """
    rgb = parse_hex(text or "")
    if rgb is None:
        return None
    share = 0.62 if strong else 0.34
    mixed = tuple(int(round(value * share)) for value in rgb)
    background = xterm256("#%02X%02X%02X" % mixed)
    if background is None:
        return None
    if background == 16:
        background = 236  # a color too dark for the cube's first step: a dark gray, not black
    red, green, blue = mixed
    light = 0.2126 * red + 0.7152 * green + 0.0722 * blue > 140
    return background, (16 if light else (231 if strong else 253))
