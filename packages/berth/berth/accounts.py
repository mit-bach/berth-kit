"""Which Claude account each session is on (2026-11-03, for the Accounts Agent).

Settings Agent owns the accounts and their mapping; Berth only reads it, from
the account switcher's own files (any account switcher that writes these files):

* ``config.json`` (``~/.config/claude-accounts/``):
  ``folders``, the config folder of each account that has its own, ``order``,
  every account, and ``default_account``, the account a new Claude session
  starts on (2026-11-04: homestead). ``create`` with no ``config_dir`` records
  that account's folder on the new session, so a later change of the default
  never moves it. A session that never had a folder stays on ``~/.claude``.
* ``state.json`` (``~/.config/claude-accounts/``): ``active``, the account
  ``~/.claude`` is signed in to now, and ``last_switch_at``.
* ``usage.json`` (same folder): each account's ``five_hour`` and ``seven_day``
  ``used_percentage``.

A session on ``~/.claude`` (no ``config_dir``, berth/configdir.py) is on the
active account; one on another folder is on the account that folder belongs
to. A session on ``~/.claude`` that opened before the last switch is
``account_stale``: requests already go out on the new login, but its
connectors and organization settings are still the old account's until it
cycles. Nothing here reads a login; the switcher's ``accounts/`` folder is
never opened. The paths can be overridden with ``BERTH_ACCOUNTS_CONFIG`` and
``BERTH_ACCOUNTS_DATA``.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

WINDOWS = (("five_hour", "5h"), ("seven_day", "7d"))
_HEX = re.compile(r"#[0-9A-Fa-f]{6}")

# The account view (2026-11-05): each account's color, by its place in Settings Agent's ``order``, unless
# config.json names one under ``colors`` ({"uni": "#2DD4BF"}). Hues apart from each other, light enough to read as
# a dot or a header on a dark sidebar; rows are tinted with them faintly (cmux-sidebar/berth.js, berth/curses_ui.py).
PALETTE = ("#2DD4BF", "#A78BFA", "#FB923C", "#F472B6", "#A3E635", "#60A5FA")
UNKNOWN = "?"  # a Claude session whose config folder Settings Agent's files do not name
UNKNOWN_COLOR = "#8E8E93"


def config_path() -> Path:
    return Path(os.environ.get("BERTH_ACCOUNTS_CONFIG")
                or Path.home() / ".config" / "claude-accounts" / "config.json")


def data_dir() -> Path:
    return Path(os.environ.get("BERTH_ACCOUNTS_DATA") or Path.home() / ".config" / "claude-accounts")


_cache: dict = {"key": None, "value": None}


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _stamp(path: Path):
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def load() -> dict:
    """The mapping as Settings Agent's files say now; re-read only when one of them changed."""
    paths = (config_path(), data_dir() / "state.json", data_dir() / "usage.json")
    key = tuple((str(path), _stamp(path)) for path in paths)
    if _cache["key"] == key:
        return _cache["value"]
    config, state, usage = (_read(path) for path in paths)
    folders, folder_of = {}, {}
    for name, folder in (config.get("folders") or {}).items():
        if isinstance(folder, str) and folder:
            folder_of[str(name)] = str(Path(folder).expanduser())
            try:
                folders[str(Path(folder).expanduser().resolve())] = str(name)
            except OSError:
                continue
    switched = state.get("last_switch_at")
    colors = {}
    for name, color in (config.get("colors") or {}).items() if isinstance(config.get("colors"), dict) else ():
        if isinstance(color, str) and _HEX.fullmatch(color.strip()):
            colors[str(name)] = color.strip().upper()
    value = {
        "colors": colors,
        "folders": folders,
        "folder_of": folder_of,
        "default_account": config.get("default_account") if isinstance(config.get("default_account"), str) else None,
        "order": [str(name) for name in config.get("order") or [] if isinstance(name, str)],
        "active": state.get("active") if isinstance(state.get("active"), str) else None,
        "last_switch_at": float(switched) if isinstance(switched, (int, float)) else None,
        "usage": usage,
    }
    _cache.update(key=key, value=value)
    return value


def account_of(config_dir: str | None) -> str | None:
    """The account a session's config folder is on; None when Settings Agent's files do not say."""
    mapping = load()
    if not config_dir:
        return mapping["active"]
    try:
        resolved = str(Path(config_dir).expanduser().resolve())
    except OSError:
        resolved = config_dir
    if resolved == str((Path.home() / ".claude").resolve()):
        return mapping["active"]
    return mapping["folders"].get(resolved)


def default_folder() -> str | None:
    """The config folder a new Claude session starts on: ``default_account``'s, or None for ~/.claude.

    None too when the default is the account ~/.claude is on now, or names no folder Settings Agent lists.
    """
    mapping = load()
    name = mapping["default_account"]
    if not name or name == mapping["active"]:
        return None
    return mapping["folder_of"].get(name)


def tag(name: str | None) -> str:
    """The short tag a row shows: the account's first three letters (uni, hom, per)."""
    return (name or "")[:3].lower()


def stale(config_dir: str | None, opened_at: str | None) -> bool:
    """A ~/.claude session that opened before the last switch: its connectors are still the old account's."""
    if config_dir:
        return False
    switched = load()["last_switch_at"]
    if switched is None or not opened_at:
        return False
    try:
        opened = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return False
    return opened < switched


def usage_parts() -> list[str]:
    """One short part per account for the screen's header: "uni 5h 42% 7d 63%"."""
    mapping = load()
    names = mapping["order"] or sorted(mapping["usage"])
    parts = []
    for name in names:
        record = mapping["usage"].get(name)
        if not isinstance(record, dict):
            continue
        shown = []
        for key, label in WINDOWS:
            value = (record.get(key) or {}).get("used_percentage") if isinstance(record.get(key), dict) else None
            if isinstance(value, (int, float)):
                shown.append(f"{label} {value:.0f}%")
        if shown:
            parts.append(f"{tag(name)} " + " ".join(shown))
    return parts


# --- the account view (2026-11-05) --------------------------------------------------------------------------------


def names() -> list[str]:
    """Every account, in Settings Agent's ``order``, then any other it names (a folder, usage), by name."""
    mapping = load()
    out = list(dict.fromkeys(mapping["order"]))
    extra = set(mapping["folder_of"]) | {name for name in mapping["usage"] if isinstance(name, str)}
    if mapping["active"]:
        extra.add(mapping["active"])
    out.extend(sorted(name for name in extra if name not in out))
    return out


def rank_of(name: str | None) -> int:
    """Where an account's group goes: Settings Agent's order, then unknown Claude sessions, then no account."""
    if not name:
        return 1000
    if name == UNKNOWN:
        return 900
    known = names()
    return known.index(name) if name in known else 800


def color_of(name: str | None) -> str | None:
    """The account's color: config.json's ``colors``, else the palette by its place. None for no account."""
    if not name:
        return None
    if name == UNKNOWN:
        return UNKNOWN_COLOR
    chosen = load()["colors"].get(name)
    if chosen:
        return chosen
    rank = rank_of(name)
    return PALETTE[rank % len(PALETTE)] if rank < 800 else PALETTE[sum(map(ord, name)) % len(PALETTE)]


def label_of(name: str | None) -> str:
    if not name:
        return "No account"
    if name == UNKNOWN:
        return "Claude, account unknown"
    return name


def usage_of(name: str | None) -> str:
    """One account's usage for its header: "5h 42% · 7d 63%", or "" when the switcher has no reading."""
    record = load()["usage"].get(name) if name else None
    if not isinstance(record, dict):
        return ""
    shown = []
    for key, label in WINDOWS:
        value = (record.get(key) or {}).get("used_percentage") if isinstance(record.get(key), dict) else None
        if isinstance(value, (int, float)):
            shown.append(f"{label} {value:.0f}%")
    return " · ".join(shown)


def marks_of(name: str | None) -> list[str]:
    """What else the header says: "default" (new Claude sessions start on it), "~/.claude" (signed in there now)."""
    if not name or name == UNKNOWN:
        return []
    mapping = load()
    out = []
    if (mapping["default_account"] or mapping["active"]) == name:
        out.append("default")
    if mapping["active"] == name:
        out.append("~/.claude")
    return out
