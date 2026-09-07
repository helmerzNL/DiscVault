#!/usr/bin/env python3
"""Validate the AI-client instruction surfaces.

Three clients read three different entry files -- Codex/ChatGPT read AGENTS.md,
Claude Code reads CLAUDE.md, Copilot reads .github/copilot-instructions.md -- and
only one of them can be canonical. AGENTS.md is; the other two are pointers.

The failure this guards against is not a missing file. It is a rule that gets
copied into a second entry file "so that client sees it too", and then edited in
only one place. Both copies then read as authoritative, and which rule an agent
follows depends on which client happens to be driving. Nothing errors; the work
is simply done to the wrong rule.

So the checks below are about *sameness*: that the pointers point, that no prose
is duplicated between canonical and pointer, that every client surface present in
the repository is named in AGENTS.md, and that the Codex prompt wrappers still
match the Claude skills they wrap.

Run by the `agent-instructions-lint` CI job.
"""
import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]

CANONICAL = ROOT / "AGENTS.md"
POINTERS = (
    ROOT / "CLAUDE.md",
    ROOT / ".github/copilot-instructions.md",
)
CODEX_README = ROOT / ".codex/README.md"
CODEX_CONFIG_EXAMPLE = ROOT / ".codex/config.toml.example"
CODEX_PROMPTS = ROOT / ".codex/prompts"
CLAUDE_SKILLS = ROOT / ".claude/skills"
SETUP_CODEX = ROOT / "scripts/setup_codex.py"

LINKED_DOCS = (CANONICAL, *POINTERS, CODEX_README)

MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.M)

# A duplicated line is only evidence of copy-paste if it is long enough to be
# prose rather than a shared phrase. Sixty characters is comfortably past any
# heading or list label the two files legitimately share.
DUPLICATE_MIN_LENGTH = 60


def slug(heading: str) -> str:
    """GitHub's heading anchor, closely enough for the headings used here."""
    text = heading.strip().lower()
    text = re.sub(r"[`*_]", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[^a-z0-9 \-]", "", text)
    return re.sub(r"\s+", "-", text.strip())


def anchors(path: Path) -> set[str]:
    return {slug(match.group(2)) for match in HEADING_RE.finditer(read(path))}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def rel(path: Path) -> str:
    """Repo-relative path with forward slashes, so a message reads the same on
    Windows and on the Linux CI runner."""
    return path.relative_to(ROOT).as_posix()


def prose_lines(text: str) -> set[str]:
    """Substantive lines, normalised, long enough to indicate a copied paragraph."""
    lines = set()
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw.strip())
        if line.startswith(("#", ">", "|", "```")):
            continue
        line = re.sub(r"^([-*]|\d+\.)\s+", "", line)
        if len(line) >= DUPLICATE_MIN_LENGTH:
            lines.add(line)
    return lines


def check_files_exist() -> list[str]:
    errors = []
    required = [
        (CANONICAL, "Codex and ChatGPT read this file and nothing else"),
        (POINTERS[0], "Claude Code reads this file"),
        (POINTERS[1], "Copilot reads this file"),
    ]
    # The Codex tooling is optional -- a repository can be fully aligned with
    # AGENTS.md alone. But a half-present .codex/ is worse than none: the
    # directory implies the prompts work, and a missing installer is discovered
    # only by the person they do not work for.
    if (ROOT / ".codex").is_dir():
        required += [
            (CODEX_README, "documents the Codex prompts and MCP config"),
            (CODEX_CONFIG_EXAMPLE, "the fragment scripts/setup_codex.py prints"),
        ]
        if CODEX_PROMPTS.is_dir():
            required.append((SETUP_CODEX, "installs the Codex prompts per clone"))
    for path, why in required:
        if not path.is_file():
            errors.append(
                f"missing {rel(path)} ({why}). "
                "Fix: restore it, or see shared/guidelines/agent-instructions-alignment.md"
            )
    return errors


def check_pointers_point() -> list[str]:
    errors = []
    for path in POINTERS:
        if not path.is_file():
            continue
        text = read(path)
        if "AGENTS.md" not in text:
            errors.append(
                f"{rel(path)} never mentions AGENTS.md. "
                "Fix: add the canonical pointer at the top of the file -- a client "
                "reading only this file would otherwise work without the conventions"
            )
    return errors


def check_no_duplicated_prose() -> list[str]:
    if not CANONICAL.is_file():
        return []
    canonical_lines = prose_lines(read(CANONICAL))
    errors = []
    for path in POINTERS:
        if not path.is_file():
            continue
        shared = sorted(canonical_lines & prose_lines(read(path)))
        for line in shared:
            excerpt = line if len(line) <= 90 else line[:87] + "..."
            errors.append(
                f"{rel(path)} repeats a line from AGENTS.md: \"{excerpt}\". "
                "Fix: delete it here and link to the AGENTS.md section instead -- two "
                "copies of a rule drift, and the stale one looks just as authoritative"
            )
    return errors


def check_surfaces_are_listed() -> list[str]:
    if not CANONICAL.is_file():
        return []
    text = read(CANONICAL)
    errors = []
    surfaces = {
        "CLAUDE.md": POINTERS[0],
        ".github/copilot-instructions.md": POINTERS[1],
        ".codex/README.md": CODEX_README,
    }
    for name, path in surfaces.items():
        if path.is_file() and name not in text:
            errors.append(
                f"AGENTS.md does not name the client surface {name}, which exists. "
                "Fix: add it to the client-surface table -- an unlisted surface is one "
                "nobody switching clients will find"
            )
    return errors


def check_codex_prompts_match_skills() -> list[str]:
    if not CLAUDE_SKILLS.is_dir() or not CODEX_PROMPTS.is_dir():
        return []
    skills = {p.name for p in CLAUDE_SKILLS.iterdir() if (p / "SKILL.md").is_file()}
    prompts = {p.stem for p in CODEX_PROMPTS.glob("*.md")}
    errors = []
    for name in sorted(skills - prompts):
        errors.append(
            f"skill {name} has no Codex wrapper at .codex/prompts/{name}.md. "
            "Fix: run scripts/setup_codex.py after adding the wrapper, so the "
            "command exists on both clients"
        )
    for name in sorted(prompts - skills):
        errors.append(
            f".codex/prompts/{name}.md wraps a skill that no longer exists at "
            f".claude/skills/{name}/SKILL.md. Fix: delete the wrapper"
        )
    for name in sorted(skills & prompts):
        expected = f".claude/skills/{name}/SKILL.md"
        if expected not in read(CODEX_PROMPTS / f"{name}.md"):
            errors.append(
                f".codex/prompts/{name}.md does not cite {expected}. "
                "Fix: the wrapper must delegate to the skill rather than restate it"
            )
    return errors


def check_links() -> list[str]:
    errors = []
    for path in LINKED_DOCS:
        if not path.is_file():
            continue
        for raw in MARKDOWN_LINK_RE.findall(read(path)):
            target = raw.split(maxsplit=1)[0].strip("<>")
            if target.startswith(("http://", "https://", "mailto:")) or "://" in target:
                continue
            file_part, _, anchor = target.partition("#")
            file_part = unquote(file_part)
            if file_part:
                resolved = (path.parent / file_part).resolve()
                if not resolved.exists():
                    errors.append(
                        f"broken link in {rel(path)}: {raw}. "
                        "Fix: update the link or restore the target"
                    )
                    continue
            else:
                resolved = path
            if anchor and resolved.suffix == ".md" and resolved.is_file():
                if unquote(anchor) not in anchors(resolved):
                    errors.append(
                        f"broken anchor in {rel(path)}: {raw}. "
                        "Fix: the heading it points at was renamed or removed -- a "
                        "pointer that lands on the wrong section is worse than none"
                    )
    return errors


def collect_errors() -> list[str]:
    errors: list[str] = []
    errors.extend(check_files_exist())
    errors.extend(check_pointers_point())
    errors.extend(check_no_duplicated_prose())
    errors.extend(check_surfaces_are_listed())
    errors.extend(check_codex_prompts_match_skills())
    errors.extend(check_links())
    return errors


def main() -> int:
    errors = collect_errors()
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        print(f"{len(errors)} agent-instruction problem(s) found.", file=sys.stderr)
        return 1
    print("Agent instruction surfaces are consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
