# CLAUDE.md — Claude Code entry point

> **Read [`AGENTS.md`](AGENTS.md) first. It is canonical.**
>
> The working conventions for DiscVault Core — how work is classified, the
> two-branch model and its promotion rule, the version guard, the iOS/Android
> decision, worktree isolation, translations, the App-Guidance documentation
> contract, and the rules for deployment-file changes — live there and apply to
> every AI client equally. Where this file and `AGENTS.md` disagree, `AGENTS.md`
> wins.

This file adds only what is specific to Claude Code. It deliberately restates no
rule from `AGENTS.md`: the `agent-instructions-lint` job fails the pull request
when a canonical line reappears here.

## Reading order

1. [`AGENTS.md`](AGENTS.md) — working conventions for this repository (canonical).
2. [`shared/guidelines/project-baseline.md`](https://github.com/Flux76HQ/App-Guidance/blob/main/shared/guidelines/project-baseline.md) — the enforceable Flux76 baseline.
3. [`AUTHORITY.md`](https://github.com/Flux76HQ/App-Guidance/blob/main/AUTHORITY.md) — which document leads for which scope.
4. This file — Claude-specific tooling, below.

## Claude-specific tooling

- **Co-author trailer.** The trailer required by
  [`AGENTS.md` §8](AGENTS.md#8-language-and-formatting-of-git-artifacts) is, for
  this client:

  ```text
  Co-Authored-By: Claude <noreply@anthropic.com>
  ```

- **Worktrees.** [`AGENTS.md` §3](AGENTS.md#3-a-new-feature-gets-a-fresh-worktree-and-its-own-session)
  asks for the platform's own worktree tool rather than a hand-run
  `git worktree add`. In Claude Code that is `EnterWorktree` / `/worktree`. It
  bases a new worktree on the repository's *default* branch, which here is `main`
  — the wrong base. Create the branch off `origin/release/v26-beta` yourself under
  the ignored `app/.local/worktrees/`, then enter it by path.

- **Skills and MCP servers.** This repository declares none of its own: there is
  no `.claude/skills/` and no `.mcp.json`. Whatever your user or plugin
  configuration provides is all that is available here.

## Switching to another client

Nothing needs migrating. Codex and ChatGPT read `AGENTS.md` automatically; Copilot
reads [`.github/copilot-instructions.md`](.github/copilot-instructions.md), which
points at the same file. See the client-surface table in
[`AGENTS.md`](AGENTS.md#client-surfaces).
