#!/usr/bin/env python3
"""Tests for check_agent_instructions.

The validator reads module-level paths derived from ROOT, so each test builds a
small throwaway repository on disk and repoints those paths at it. That is
deliberate: the checks are about how files relate to each other, and a fake
filesystem is the cheapest way to state "these two files exist and this one
repeats a line from that one".
"""
import shutil
import tempfile
import unittest
from pathlib import Path

import check_agent_instructions as checker

LONG_LINE = (
    "Every MUST rule needs an automated enforcement point, in CI or in a hook."
)


class InstructionRepoTestCase(unittest.TestCase):
    """Builds a minimal aligned repository, then lets each test break one thing."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

        (self.root / ".github").mkdir()
        self.write(
            "AGENTS.md",
            "# AGENTS.md\n\n"
            "## Client surfaces\n\n"
            "CLAUDE.md and .github/copilot-instructions.md point here.\n\n"
            "## 8. Git and pull request workflow\n\n"
            f"{LONG_LINE}\n",
        )
        self.write(
            "CLAUDE.md",
            "# CLAUDE.md\n\nRead [AGENTS.md](AGENTS.md) first.\n",
        )
        self.write(
            ".github/copilot-instructions.md",
            "# Copilot\n\nRead [AGENTS.md](../AGENTS.md) first.\n",
        )

        self.patch_paths()

    def write(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def patch_paths(self) -> None:
        """Repoint the validator's module-level paths at the throwaway repo."""
        originals = {
            name: getattr(checker, name)
            for name in (
                "ROOT",
                "CANONICAL",
                "POINTERS",
                "CODEX_README",
                "CODEX_CONFIG_EXAMPLE",
                "CODEX_PROMPTS",
                "CLAUDE_SKILLS",
                "SETUP_CODEX",
                "LINKED_DOCS",
            )
        }
        self.addCleanup(lambda: [setattr(checker, k, v) for k, v in originals.items()])

        checker.ROOT = self.root
        checker.CANONICAL = self.root / "AGENTS.md"
        checker.POINTERS = (
            self.root / "CLAUDE.md",
            self.root / ".github/copilot-instructions.md",
        )
        checker.CODEX_README = self.root / ".codex/README.md"
        checker.CODEX_CONFIG_EXAMPLE = self.root / ".codex/config.toml.example"
        checker.CODEX_PROMPTS = self.root / ".codex/prompts"
        checker.CLAUDE_SKILLS = self.root / ".claude/skills"
        checker.SETUP_CODEX = self.root / "scripts/setup_codex.py"
        checker.LINKED_DOCS = (checker.CANONICAL, *checker.POINTERS, checker.CODEX_README)

    def assertNoErrors(self) -> None:
        self.assertEqual(checker.collect_errors(), [])

    def assertErrorMentions(self, needle: str) -> None:
        errors = checker.collect_errors()
        self.assertTrue(
            any(needle in error for error in errors),
            f"expected an error mentioning {needle!r}, got {errors}",
        )


class TestAlignedRepository(InstructionRepoTestCase):
    def test_minimal_aligned_repository_passes(self) -> None:
        self.assertNoErrors()

    def test_codex_tooling_is_optional(self) -> None:
        """A repo can be aligned with AGENTS.md alone -- no .codex/ required."""
        self.assertFalse((self.root / ".codex").exists())
        self.assertNoErrors()


class TestEntryFiles(InstructionRepoTestCase):
    def test_missing_canonical_file_is_an_error(self) -> None:
        (self.root / "AGENTS.md").unlink()
        self.assertErrorMentions("missing AGENTS.md")

    def test_missing_claude_entry_file_is_an_error(self) -> None:
        (self.root / "CLAUDE.md").unlink()
        self.assertErrorMentions("missing CLAUDE.md")

    def test_pointer_that_never_names_agents_md_is_an_error(self) -> None:
        self.write("CLAUDE.md", "# CLAUDE.md\n\nSome rules of its own.\n")
        self.assertErrorMentions("never mentions AGENTS.md")


class TestDuplicatedProse(InstructionRepoTestCase):
    def test_a_long_line_copied_into_a_pointer_is_an_error(self) -> None:
        self.write(
            "CLAUDE.md",
            f"# CLAUDE.md\n\nRead [AGENTS.md](AGENTS.md) first.\n\n{LONG_LINE}\n",
        )
        self.assertErrorMentions("repeats a line from AGENTS.md")

    def test_duplication_is_detected_through_list_markers(self) -> None:
        """A rule copied as a bullet is the same copy, not a different one."""
        self.write(
            "CLAUDE.md",
            f"# CLAUDE.md\n\nRead [AGENTS.md](AGENTS.md) first.\n\n- {LONG_LINE}\n",
        )
        self.assertErrorMentions("repeats a line from AGENTS.md")

    def test_a_short_shared_phrase_is_not_duplication(self) -> None:
        self.write(
            "CLAUDE.md",
            "# CLAUDE.md\n\nRead [AGENTS.md](AGENTS.md) first.\n\n## Client surfaces\n",
        )
        self.assertNoErrors()


class TestClientSurfaceTable(InstructionRepoTestCase):
    def test_unlisted_existing_surface_is_an_error(self) -> None:
        self.write(
            "AGENTS.md",
            "# AGENTS.md\n\n## Client surfaces\n\nOnly CLAUDE.md is named here.\n",
        )
        self.assertErrorMentions(".github/copilot-instructions.md, which exists")

    def test_codex_readme_must_be_listed_once_it_exists(self) -> None:
        self.write(".codex/README.md", "# Codex\n")
        self.write(".codex/config.toml.example", "# example\n")
        self.assertErrorMentions(".codex/README.md, which exists")


class TestCodexPrompts(InstructionRepoTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.write(".codex/README.md", "# Codex\n")
        self.write(".codex/config.toml.example", "# example\n")
        self.write("scripts/setup_codex.py", "# installer\n")
        self.write(
            "AGENTS.md",
            "# AGENTS.md\n\n## Client surfaces\n\n"
            "CLAUDE.md, .github/copilot-instructions.md, .codex/README.md\n",
        )
        self.write(".claude/skills/speckit-plan/SKILL.md", "# plan skill\n")

    def test_skill_without_a_wrapper_is_an_error(self) -> None:
        self.write(".codex/prompts/.keep", "")
        self.assertErrorMentions("has no Codex wrapper")

    def test_wrapper_without_a_skill_is_an_error(self) -> None:
        self.write(
            ".codex/prompts/speckit-plan.md",
            "Read .claude/skills/speckit-plan/SKILL.md\n",
        )
        self.write(
            ".codex/prompts/speckit-gone.md",
            "Read .claude/skills/speckit-gone/SKILL.md\n",
        )
        self.assertErrorMentions("wraps a skill that no longer exists")

    def test_wrapper_that_does_not_delegate_is_an_error(self) -> None:
        self.write(
            ".codex/prompts/speckit-plan.md",
            "# plan\n\nHere is my own restatement of the whole procedure.\n",
        )
        self.assertErrorMentions("does not cite .claude/skills/speckit-plan/SKILL.md")

    def test_missing_installer_when_prompts_exist_is_an_error(self) -> None:
        (self.root / "scripts/setup_codex.py").unlink()
        self.write(
            ".codex/prompts/speckit-plan.md",
            "Read .claude/skills/speckit-plan/SKILL.md\n",
        )
        self.assertErrorMentions("missing scripts/setup_codex.py")

    def test_matching_wrapper_passes(self) -> None:
        self.write(
            ".codex/prompts/speckit-plan.md",
            "Read .claude/skills/speckit-plan/SKILL.md and follow it.\n",
        )
        self.assertNoErrors()


class TestMcpParity(InstructionRepoTestCase):
    """The three clients must be offered the same MCP servers."""

    CLAUDE = '{"mcpServers": {"squad_state": {"command": "npx"}}}\n'
    COPILOT = '{"mcpServers": {"squad_state": {"command": "npx"}}}\n'
    CODEX = '[mcp_servers.squad_state]\ncommand = "npx"\n'
    EMPTY = '{"mcpServers": {}}\n'

    def test_matching_servers_pass(self) -> None:
        self.write(".mcp.json", self.CLAUDE)
        self.write(".copilot/mcp-config.json", self.COPILOT)
        self.write(".codex/config.toml.example", self.CODEX)
        self.write(".codex/README.md", "# Codex\n")
        self.write(
            "AGENTS.md",
            "# AGENTS.md\n\n## Client surfaces\n\nCLAUDE.md, "
            ".github/copilot-instructions.md, .codex/README.md\n",
        )
        self.assertNoErrors()

    def test_server_missing_for_one_client_is_an_error(self) -> None:
        self.write(".mcp.json", self.CLAUDE)
        self.write(".copilot/mcp-config.json", self.EMPTY)
        self.assertErrorMentions("not for Copilot")

    def test_example_file_is_used_when_the_live_file_is_absent(self) -> None:
        """CI never sees .mcp.json -- it is gitignored -- so the example stands in."""
        self.write(".mcp.json.example", self.CLAUDE)
        self.write(".copilot/mcp-config.json.example", self.EMPTY)
        self.assertErrorMentions("not for Copilot")

    def test_live_file_wins_over_the_example(self) -> None:
        self.write(".mcp.json", self.EMPTY)
        self.write(".mcp.json.example", self.CLAUDE)
        self.write(".copilot/mcp-config.json", self.COPILOT)
        self.assertErrorMentions("not for Claude")

    def test_example_prefixed_servers_are_ignored(self) -> None:
        """EXAMPLE-* documents the shape of an entry; it is not configuration."""
        self.write(".mcp.json", self.CLAUDE)
        self.write(
            ".copilot/mcp-config.json",
            '{"mcpServers": {"squad_state": {}, "EXAMPLE-github": {}}}\n',
        )
        self.assertNoErrors()

    def test_a_single_client_config_is_not_compared(self) -> None:
        """Nothing to compare against is not a finding."""
        self.write(".mcp.json", self.CLAUDE)
        self.assertNoErrors()

    def test_invalid_json_is_reported(self) -> None:
        self.write(".mcp.json", "{not json")
        self.write(".copilot/mcp-config.json", self.COPILOT)
        self.assertErrorMentions("is not valid JSON")


class TestLinks(InstructionRepoTestCase):
    def test_broken_relative_link_is_an_error(self) -> None:
        self.write(
            "CLAUDE.md",
            "# CLAUDE.md\n\nRead [AGENTS.md](AGENTS.md) and "
            "[the baseline](shared/guidelines/project-baseline.md).\n",
        )
        self.assertErrorMentions("broken link in CLAUDE.md")

    def test_broken_anchor_is_an_error(self) -> None:
        self.write(
            "CLAUDE.md",
            "# CLAUDE.md\n\nSee [AGENTS.md](AGENTS.md#9-a-section-that-is-gone).\n",
        )
        self.assertErrorMentions("broken anchor in CLAUDE.md")

    def test_valid_anchor_passes(self) -> None:
        self.write(
            "CLAUDE.md",
            "# CLAUDE.md\n\nSee "
            "[AGENTS.md](AGENTS.md#8-git-and-pull-request-workflow).\n",
        )
        self.assertNoErrors()

    def test_external_links_are_ignored(self) -> None:
        self.write(
            "CLAUDE.md",
            "# CLAUDE.md\n\n[AGENTS.md](AGENTS.md), "
            "[docs](https://example.invalid/nope#frag).\n",
        )
        self.assertNoErrors()


class TestSlug(unittest.TestCase):
    def test_numbered_heading(self) -> None:
        self.assertEqual(
            checker.slug("8. Git and pull request workflow"),
            "8-git-and-pull-request-workflow",
        )

    def test_punctuation_and_code_spans_are_dropped(self) -> None:
        self.assertEqual(
            checker.slug("8. Agent pull requests: standing authorisation to merge"),
            "8-agent-pull-requests-standing-authorisation-to-merge",
        )
        self.assertEqual(
            checker.slug("17. Agent instructions are client-neutral"),
            "17-agent-instructions-are-client-neutral",
        )
        self.assertEqual(checker.slug("`AGENTS.md` is canonical"), "agentsmd-is-canonical")


if __name__ == "__main__":
    unittest.main()
