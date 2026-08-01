# CLAUDE.md — DiscVault agent instructions

This is the Claude-facing counterpart to
[`.github/copilot-instructions.md`](.github/copilot-instructions.md). Both files describe the
**same** contributor workflow; keep them in sync — when one changes, mirror the change in the
other. If they ever disagree, the Copilot document is the source of truth for the workflow rules.

Follow this process automatically on every feature, fix, commit, and promotion — do not wait to
be reminded. **If a request would break the two-branch model or the merge-commit promotion rule,
stop and warn the user before acting.**

---

## Branch & release workflow

DiscVault uses a **two-branch model**. Keep it that way.

| Branch | Role | Images built |
|---|---|---|
| `release/v26-beta` | **Development** — all feature and fix work happens here | beta channel |
| `main` | **Production** — live on discvault.eu | `:stable`, `:latest`, `:v26` |
| `legacy` | Archive only — do not develop here | — |

### How to work

1. **Branch feature/fix work off `release/v26-beta`**, never off `main` or `legacy`.
2. Open the PR **into `release/v26-beta`**. Test on the beta channel.
3. **Promote beta → `main` per feature** (or a small batch) once verified.

> ⚠️ Never open a feature/fix PR directly into `main`, and never push directly to `main`.
> `main` is production; changes reach it only through a deliberate promotion PR.

### Promote with a MERGE COMMIT — never squash

Promotion PRs from `release/v26-beta` to `main` **must** be merged with a real merge commit
(`--merge`), never squashed. Squashing rewrites the promoted commits into a new commit that is
**not** an ancestor of `release/v26-beta`; beta and main then diverge and the next promotion
conflicts (typically on `app/VERSION` and large backend files). Feature/fix PRs *into beta* may
be squashed — only promotions must use a merge commit.

### Recovering from a diverged beta ↔ main

- If a promotion was **squashed** and beta lost history, back-merge `main` into beta with beta as
  the source of truth, then re-promote:

  ```sh
  git checkout release/v26-beta
  git merge -s ort -X ours origin/main      # keep beta's content, absorb main's history
  git diff origin/release/v26-beta --stat   # MUST be empty (purely historical merge)
  git push origin release/v26-beta
  ```

- If work landed on **main first** and beta simply lags behind, back-merge main into beta so both
  become content-identical again (a fast-forward when beta has no unique commits):

  ```sh
  git checkout release/v26-beta
  git merge origin/main
  git diff origin/main --stat               # MUST be empty (content-identical)
  git push origin release/v26-beta
  ```

After any reconciliation, verify `git diff origin/main origin/release/v26-beta --stat` is empty.

### Branch protection

`main` is protected: PRs require review, the version-guard check, and a review before merge.
Do not push directly to `main`.

### Cleaning up after a merge

Delete a **feature branch** once its PR merges into `release/v26-beta` — stale branches pile up
fast:

```sh
git push origin --delete <feature-branch>
```

- **Never delete** the permanent branches: `main`, `release/v26-beta`, `legacy`.
- Promotions (beta → `main`) only **merge** — they never delete `release/v26-beta`.
- Do **not** delete a branch an open session is still using: session branches are reused across
  multiple PRs. Delete only after the session is finished. If unsure, ask first.

#### Finding what is safe to prune

"Commits ahead of beta" does **not** answer this. A branch usually lands through a *different*
commit than the one it carries — a squash, a cherry-pick, or a re-implementation on top of newer
beta — so that count stays above zero long after the work shipped. Deciding from it deletes live
work.

Use the report instead:

```sh
python app/scripts/prune_landed_branches.py --min-age-days 14
```

It calls a branch landed only when it is certain — merged, patch-equivalent (`git cherry`),
carrying nothing but an `app/VERSION` bump, or content-identical to the base — and reports
everything else as `keep`. A branch whose work was *re-implemented* differently on beta also
reads as `keep`: the tool cannot tell that apart from unmerged work, and it errs toward keeping.
Confirm those by hand before deleting.

The `DiscVault Prune Landed Branches` workflow runs the same report every Monday and never
deletes on a schedule. To delete, dispatch it with `apply: true`; it still refuses to touch a
permanent branch, a branch with an open PR, or one pushed within `min_age_days`. Protect an
active session branch explicitly with the `keep` input.

### Follow-up work after a PR merges

**Before every push or PR, check whether the branch's previous PR is already merged.** Fetch the
base and confirm whether your last pushed commit is already an ancestor of it
(`git fetch origin release/v26-beta && git merge-base --is-ancestor <last-commit> origin/release/v26-beta`).
A merged PR is finished — it cannot track new work and **must not be reused**; never stack new
commits on top of already-merged history.

If it was merged, treat the follow-up as a fresh change:

1. Restart the branch from the latest base, keeping the **same** name:
   `git fetch origin release/v26-beta && git checkout -B <branch> origin/release/v26-beta`.
2. Re-apply only the still-unmerged commits on top (cherry-pick / rebase) — drop the ones already
   promoted into the base.
3. Push (force-with-lease is fine when the branch carried only merged history) and open a **new**
   PR into `release/v26-beta`. Any PR opened for it is a new PR, not the merged one.

---

## Version guard: always bump `app/VERSION`

CI runs `.github/workflows/version-guard.yml` (via `app/scripts/check_version_bumped.py`) and
**fails any push or PR that touches a protected path without changing `app/VERSION`**.

Protected paths:

- `.github/workflows/`
- `app/Dockerfile`, `app/docker-compose*`
- `app/backend/`
- `app/frontend/`
- `app/mcp-server/`
- `app/deploy/`
- `app/scripts/`
- `dist/plugins/`

`*.md` and `*.txt` files are ignored — documentation-only changes need no bump.

**What to do:** when a change set touches a protected path, bump `app/VERSION` (semver
`MAJOR.MINOR.PATCH` — bump the patch unless a larger change is intended). One bump per PR/range is
enough. Easiest is to let the helper do it after staging your changes:

```sh
python app/scripts/bump_version.py     # bumps the patch and stages app/VERSION
```

Or enable the pre-commit hook once per clone/worktree so it happens automatically:

```sh
git config core.hooksPath .githooks
```

---

## Classify the work first — bug / feature / other

Before creating a branch or writing code, know what kind of work this is. Ask the user when it is
not already obvious from the request; otherwise state the type you inferred and carry it through
the whole flow.

| Type | Branch prefix | Commit / PR prefix |
|---|---|---|
| bug | `fix/` | `fix:` |
| feature | `feat/` | `feat:` |
| docs | `docs/` | `docs:` |
| chore | `chore/` | `chore:` |
| refactor | `refactor/` | `refactor:` |

- **Branch name:** `<prefix>/<short-kebab-description>` (e.g. `fix/loans-toggle-missing`).
- **Commit + PR title:** start with the matching Conventional-Commits prefix.
- In an existing session whose branch is already fixed, keep that branch but still apply the type
  prefix to commits and the PR title.

---

## Language & formatting of Git artifacts

- **Always write Git artifacts in English** — branch names, commit messages, PR titles, PR
  descriptions, and merge commit messages — regardless of the conversation language. (Chat replies
  to the user stay in the conversation language; only the Git/GitHub artifacts are English.)
- **Use real Markdown, no literal escape sequences.** Write actual newlines, real `-`/`*` bullets,
  real `` `code` `` backticks, and proper headings. Never emit literal `\n`, `\t`, or `\"` as text —
  the description must render cleanly on GitHub.

---

## Translations are part of "done" (feature + bug fix)

- Every feature and bug fix must include complete i18n updates for all supported locales.
- Do not open or merge a PR while translations are incomplete.
- Before opening a PR, run an i18n completeness pass: no missing keys, and no newly introduced
  hardcoded UI text without i18n keys.

---

## Record decisions in the App-Guidance documentation repo

Documentation lives in **[`Flux76HQ/App-Guidance`](https://github.com/Flux76HQ/App-Guidance)**, not in
this repository. Whenever a session settles something that outlives its own PR, write it up there —
do not wait to be asked.

**What to record:** a route or contract between DiscVault and another system (MovieVault, the iOS app,
a plugin API), an ownership or precedence rule (which source may supply a field, which value wins), a
deliberate policy and why it exists, and the symptom-to-cause mapping that made a bug findable. A
plain bug fix with no rule behind it does not need an entry; the release notes cover that.

**How to record it:** describe the rule and the reasoning, not the diff — the document has to stay
true after the code moves. Reference DiscVault symbols and paths so a reader can find the
implementation, and keep an appendix mapping each rule to its source location. Carry open questions
across as open questions instead of quietly resolving them. Write it in English, like every other
shared artifact.

**Attaching the repo:** App-Guidance sits under a different owner (`Flux76HQ`) than this repository
(`helmerzNL`), and a session cannot attach a repo from another owner. A session working in DiscVault
therefore cannot push there: prepare the document, hand it over, and say plainly that it still needs
to land in App-Guidance. To commit it directly, start a session with App-Guidance as its initial
source.

---

## Checklists

### When starting new work

1. Base the work on `release/v26-beta`, **never** on `main` or `legacy`
   (`git fetch origin release/v26-beta && git checkout -b <prefix>/<desc> origin/release/v26-beta`).
2. Name the branch with the classified type prefix.
3. Keep the scope to that one bug/feature.

### When committing

1. Confirm you are on a **beta-based branch**, not on `main`.
2. If the change touches a protected path, ensure `app/VERSION` is bumped (helper or pre-commit
   hook). Docs-only (`*.md`/`*.txt`) needs no bump.
3. Start the commit message with the classified type prefix (`fix:`/`feat:`/`docs:`/…).
4. Add a Claude co-author trailer, e.g. `Co-Authored-By: Claude <noreply@anthropic.com>`
   (mirroring the Copilot trailer the Copilot doc mandates), unless the user opts out.
5. Push and open the PR **into `release/v26-beta`** with the same type prefix in its title.
6. Confirm translations are complete across all locales.
7. Let it build/test on the beta channel before considering promotion.
8. After the PR merges, delete the feature branch — unless it is the active session branch or a
   permanent branch.

### When asked to "ship", "promote", or "push to main"

1. Confirm the change landed on `release/v26-beta` first.
2. Promote via a PR merged with `--merge` (a merge commit). **Never squash a promotion.**
3. Treat `release/v26-beta` as the source of truth; if beta and main diverge, use the recovery
   recipe above.
4. After promotion, verify `main` and `release/v26-beta` are content-identical
   (`git diff origin/main origin/release/v26-beta --stat` empty).
5. Promotion only merges — **never delete `release/v26-beta`**.

---

Full reference:
[DiscVault 26 — Branching & releases](https://wiki.zbonline.nl/en/Projecten/Coding/discvault/branching)
and the [feature → production workflow](https://wiki.zbonline.nl/en/Projecten/Coding/discvault/feature-workflow)
on the wiki.
