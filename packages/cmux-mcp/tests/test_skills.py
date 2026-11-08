"""The cmux skills teach the tools the servers really have, and teach them the same way.

cmux-claude drives Claude panes and cmux-grok drives Grok panes. Everything
about Berth (the call tables, folders, fork, open and close, prompts,
watching, refusals) is one block that must be byte-identical in both, so the
two agents can do exactly the same things. Since 2026-10-28 the session
tools are the Berth MCP's (``berth mcp``); this server is taught only for
the pane (``screen``, ``input``).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from cmux_mcp.berth_api import TOOLS  # puts Berth on the import path
from berth.mcp import TOOLS as BERTH_TOOLS  # noqa: E402

SKILLS = Path(__file__).resolve().parents[3] / "skills"
PAIR = ("cmux-claude", "cmux-grok")
START = "<!-- berth-core"
END = "<!-- /berth-core -->"


def _core(name: str) -> str:
    text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    return text[text.index(START) : text.index(END)]


def _table_tools(core: str, start: str, end: str) -> set[str]:
    section = core[core.index(start) : core.index(end)]
    found: set[str] = set()
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) > 3 and cells[2].startswith("`"):
            found.update(re.findall(r"`([a-z_]+)`", cells[2]))
    return found


class SkillTests(unittest.TestCase):
    def test_the_berth_block_is_the_same_in_both_skills(self) -> None:
        self.assertEqual(_core("cmux-claude"), _core("cmux-grok"))

    def test_the_berth_table_names_every_berth_mcp_tool_and_only_those(self) -> None:
        listed = _table_tools(_core("cmux-claude"), "## Call", "## Pane")
        self.assertEqual(listed, {tool["name"] for tool in BERTH_TOOLS})

    def test_the_pane_table_names_only_real_cmux_mcp_tools(self) -> None:
        listed = _table_tools(_core("cmux-claude"), "## Pane", "## Folders")
        self.assertEqual(listed, {"screen", "input"})
        self.assertLessEqual(listed, {tool["name"] for tool in TOOLS})

    def test_both_skills_point_at_the_berth_skill(self) -> None:
        for name in PAIR:
            text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("~/.claude/skills/berth/SKILL.md", text)
            self.assertIn("`berth`", text)

    def test_both_skills_ship_the_same_prompt_guide_and_watcher(self) -> None:
        for relative in ("references/prompts.md", "scripts/wait-stop-files.py"):
            left, right = (SKILLS / name / relative for name in PAIR)
            self.assertEqual(left.read_bytes(), right.read_bytes(), relative)

    def test_no_skill_teaches_typing_fork_or_rename_into_a_pane(self) -> None:
        stale = (
            "The file starts with `/fork",
            "cmux has no fork tool",
            "only Claude forks",
            "send `/rename TITLE`",
            "/rename TITLE` as a short line",
            "/Users/olduser",
        )
        for name in PAIR:
            for path in (SKILLS / name).rglob("*.md"):
                text = path.read_text(encoding="utf-8")
                for phrase in stale:
                    self.assertNotIn(phrase, text, f"{path}: {phrase}")

    def test_subfolders_are_taught_with_the_full_path(self) -> None:
        core = _core("cmux-grok")
        self.assertIn('`{ "action": "create", "path": "<Parent/Child>" }`', core)
        self.assertIn('"into": "<parent>"', core)


if __name__ == "__main__":
    unittest.main()
