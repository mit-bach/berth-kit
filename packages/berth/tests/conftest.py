"""Keep pytest off the live cmux socket, the real catalog, and the hook file."""

from __future__ import annotations

from pathlib import Path

import pytest

from berth.db import Catalog
from berth.fixture import build_desk, build_scale


@pytest.fixture(autouse=True)
def isolate_berth(monkeypatch, tmp_path):
    monkeypatch.setenv("BERTH_CATALOG", str(tmp_path / "berth.sqlite"))
    monkeypatch.setenv("CMUX_SOCKET_PATH", str(tmp_path / "missing.sock"))
    monkeypatch.setenv("BERTH_HOOK_DIR", str(tmp_path / "hooks"))
    monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
    # Text boxes copy on select; the tests must not reach the Mac clipboard.
    monkeypatch.setenv("BERTH_CLIPBOARD", "off")
    monkeypatch.setenv("BERTH_KEYS", "off")  # no real berth-keys helper from a test (berth/keys.py)
    monkeypatch.setattr("berth.standard.settings_path", lambda: tmp_path / "claude-settings.json")
    monkeypatch.setattr("berth.configdir.extra_roots", lambda: [])  # never the real ~/.claude-* folders
    monkeypatch.setenv("BERTH_ACCOUNTS_CONFIG", str(tmp_path / "accounts-config.json"))  # nor the real accounts
    monkeypatch.setenv("BERTH_ACCOUNTS_DATA", str(tmp_path / "accounts-data"))
    # Claude Code's own records: an empty home, never the real ~/.claude.
    monkeypatch.setattr("berth.claude.sessions_dir", lambda: tmp_path / "claude-sessions")
    monkeypatch.setattr("berth.identity.claude_projects_root", lambda: tmp_path / "claude-projects")
    from berth import claude

    claude._cache.clear()


@pytest.fixture
def catalog(tmp_path):
    db = Catalog(tmp_path / "berth.sqlite").open()
    yield db
    db.close()


@pytest.fixture
def desk(tmp_path: Path) -> Path:
    return build_desk(tmp_path / "desk.sqlite")


@pytest.fixture
def scale(tmp_path: Path) -> Path:
    return build_scale(tmp_path / "scale.sqlite")
