# Copilot instructions — DiscVault Core

> **Read [`AGENTS.md`](../AGENTS.md) first. It is canonical.**
>
> The working conventions for DiscVault Core — how work is classified, the
> two-branch model and its promotion rule, the version guard, the iOS/Android
> decision, worktree isolation, translations, the App-Guidance documentation
> contract, and the rules for deployment-file changes — live there and apply to
> every AI client equally. Where this file and `AGENTS.md` disagree, `AGENTS.md`
> wins.

This file adds only what is specific to GitHub Copilot. It deliberately restates
no rule from `AGENTS.md`: the `agent-instructions-lint` job fails the pull request
when a canonical line reappears here.

## Reading order

1. [`AGENTS.md`](../AGENTS.md) — working conventions for this repository (canonical).
2. [`shared/guidelines/project-baseline.md`](https://github.com/Flux76HQ/App-Guidance/blob/main/shared/guidelines/project-baseline.md) — the enforceable Flux76 baseline.
3. [`AUTHORITY.md`](https://github.com/Flux76HQ/App-Guidance/blob/main/AUTHORITY.md) — which document leads for which scope.
4. This file — Copilot-specific tooling, below.

## Copilot-specific tooling

- **Co-author trailer.** The trailer required by
  [`AGENTS.md` §8](../AGENTS.md#8-language-and-formatting-of-git-artifacts) is, for
  this client:

  ```text
  Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
  ```

- **Copilot review on `main`.** Branch protection on `main` expects a review
  before merge, and a Copilot review satisfies it. That is the one place this
  client appears in the branch rules described in
  [`AGENTS.md` §4](../AGENTS.md#4-branch-topology-and-release-workflow).

- **Session branches.** A Copilot coding-agent session reuses one branch across
  several pull requests, which is exactly the case
  [`AGENTS.md` §4](../AGENTS.md#4-branch-topology-and-release-workflow) exempts
  from branch deletion. Say which branch your session owns when asked to prune.

- **Skills and MCP config.** This repository declares none of its own: there is no
  `.github/skills/` and no `.copilot/mcp-config.json`.

## Switching to another client

Nothing needs migrating. Codex and ChatGPT read `AGENTS.md` automatically; Claude
Code reads [`CLAUDE.md`](../CLAUDE.md), which points at the same file. See the
client-surface table in [`AGENTS.md`](../AGENTS.md#client-surfaces).
