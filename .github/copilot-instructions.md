# DiscVault contributor & agent instructions

## Version guard: always bump `app/VERSION`

CI runs **`.github/workflows/version-guard.yml`**, which calls
`app/scripts/check_version_bumped.py`. It fails any push or pull request whose diff touches a
protected app/runtime path **without** changing `app/VERSION`.

Protected paths:

- `.github/workflows/`
- `app/Dockerfile`, `app/docker-compose*`
- `app/backend/`
- `app/frontend/`
- `app/mcp-server/`
- `app/deploy/`
- `app/scripts/`
- `dist/plugins/`

`*.md` and `*.txt` files are ignored, so documentation-only changes do not need a bump.

### What to do

When a change set touches any protected path, bump `app/VERSION` (semver
`MAJOR.MINOR.PATCH` — bump the patch unless a larger change is intended). One bump per
PR/range is enough.

Easiest: let the helper do it. Stage your changes, then run:

```sh
python app/scripts/bump_version.py
```

It bumps the patch and stages `app/VERSION`, but only when a protected path is staged and the
version was not already changed.

### Automate it (recommended)

A `pre-commit` hook auto-runs the helper. Enable it once per clone/worktree:

```sh
git config core.hooksPath .githooks
```

After that, every commit that touches a protected path bumps `app/VERSION` automatically, so
the version guard never fails.

### Re-check the bump right before merging

**A bump is only valid against the base as it is at merge time.** The guard requires
`app/VERSION` to be *strictly greater* than the version on `release/v26-beta`, and the helper
can only compare against the base as it looked when you committed. If another PR merges into
beta while yours is open and bumps to the same patch number, your bump silently becomes stale
and the guard fails with:

```
app/VERSION 26.7.63 is not strictly greater than the actual base 26.7.63 - this is
stale/redundant, not a real bump
```

So before merging — not only before opening the PR:

```sh
git fetch origin release/v26-beta
git rebase origin/release/v26-beta      # or merge the base in
python app/scripts/bump_version.py      # moves past the *new* base
```

**Do not merge a PR whose version guard is red.** Merging anyway lands two different code
states on beta under one version, so the beta image tag stops identifying a build; fixing that
afterwards costs a second PR that does nothing but bump. This has happened: #473/#474, and
again with #516/#517 (repaired by #520).

## Branch & release workflow

DiscVault uses a **two-branch model**. Keep it that way.

| Branch | Role | Images built |
|---|---|---|
| `release/v26-beta` | **Development** — all feature work happens here | beta channel |
| `main` | **Production** — live on discvault.eu | `:stable`, `:latest`, `:v26` |
| `legacy` | Archive only — do not develop here | — |

### How to work

1. **Branch feature work off `release/v26-beta`**, never off `main`.
2. Open the feature PR **into `release/v26-beta`**. Test on the beta channel.
3. **Promote beta → `main` per feature** (or a small batch) once verified.

### ⚠️ Promote with a MERGE COMMIT — never squash

Promotion PRs from `release/v26-beta` to `main` **must** be merged with a real merge
commit:

```sh
gh pr merge <pr> --merge      # ✅ correct
# gh pr merge <pr> --squash   # ❌ NEVER for promotions
```

Squashing rewrites the promoted commits into a brand-new commit that is **not** an ancestor
of `release/v26-beta`. Beta and main then diverge, and the next promotion conflicts (typically
on `app/VERSION` and large backend files).

### Recovering from a squashed / diverged promotion

If a promotion was squashed and beta ↔ main have diverged, back-merge `main` into beta with
beta as the source of truth, then re-promote:

```sh
git checkout release/v26-beta
git merge -s ort -X ours origin/main      # keep beta's content, absorb main's history
git diff origin/release/v26-beta --stat   # MUST be empty (purely historical merge)
git push origin release/v26-beta
```

Then open the promotion PR again and merge it with `--merge`.

### Cleaning up after a merge

Delete a **feature branch** as soon as its PR merges into `release/v26-beta` — stale
branches pile up fast (we once had 19 to prune):

```sh
git push origin --delete <feature-branch>   # or use the PR "Delete branch" button
```

- A branch **ruleset** may print a warning like `Cannot delete this branch`; an admin
  bypass still deletes it — the `[deleted]` line in the output confirms success.
- **Never delete** the permanent branches: `main`, `release/v26-beta`, `legacy`.
- Promotions (beta → `main`) only **merge** — they never delete `release/v26-beta`.
- **Exception — active Copilot worktree / session branches:** do NOT delete a branch an
  open session is still using. Session branches are **reused across multiple PRs**
  (e.g. one session may open #144, #148, #149 from the same branch). Delete such a
  branch only after the session is finished.

#### Finding what is safe to prune

"Commits ahead of beta" does **not** answer this. A branch usually lands through a
*different* commit than the one it carries — a squash, a cherry-pick, or a
re-implementation on top of newer beta — so that count stays above zero long after the
work shipped. Deciding from it deletes live work.

Use the report instead:

```sh
python app/scripts/prune_landed_branches.py --min-age-days 14
```

It calls a branch landed only when it is certain — merged, patch-equivalent
(`git cherry`), carrying nothing but an `app/VERSION` bump, or content-identical to the
base — and reports everything else as `keep`. A branch whose work was *re-implemented*
differently on beta also reads as `keep`: the tool cannot tell that apart from unmerged
work, and it errs toward keeping. Confirm those by hand before deleting.

The `DiscVault Prune Landed Branches` workflow runs the same report every Monday and
never deletes on a schedule. To delete, dispatch it with `apply: true`; it still refuses
to touch a permanent branch, a branch with an open PR, or one pushed within
`min_age_days`. Protect an active session branch explicitly with the `keep` input.

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

### Branch protection

`main` is protected: PRs require review, the version-guard check, and Copilot review before
merge. Do not push directly to `main`.

### Agent behaviour (for Copilot) — ALWAYS follow this

This process is **mandatory**. Follow it automatically on every feature, commit, and promotion —
do not wait to be reminded. If a request would break the two-branch model or the merge-commit
promotion rule, stop and warn the user before acting.

**Language: always write Git artifacts in English**

Regardless of the conversation language, **all branch names, commit messages, PR titles, PR
descriptions, and merge commit messages must be written in English.** This keeps the Git history
and GitHub consistent and reviewable. (Chat replies to the user stay in the conversation
language — only the Git/GitHub artifacts are English.)

**Formatting: use clean, readable Markdown — no literal escape sequences**

In every commit message, PR title, PR description, and merge message, write **real line breaks
and real Markdown**. Never emit literal escape sequences such as `\n`, `\t`, or `\"` as text —
use an actual newline for a new line, real `-`/`*` bullet lists, real ``code`` backticks, and
proper headings. The description must render cleanly on GitHub, not show raw `\n` characters.

**At the start of every new piece of work — classify it first**

Before creating a branch or writing any code, **ask the user what kind of work this is**:
a **bug** fix, a **feature**, or **something else** (docs, chore, refactor, etc.). Skip the
question only when the answer is already unambiguous from the request (then state the type you
inferred). Carry the chosen type through the whole flow:

| Type | Branch prefix | Commit / PR prefix |
|---|---|---|
| bug | `fix/` | `fix:` |
| feature | `feat/` | `feat:` |
| docs | `docs/` | `docs:` |
| chore | `chore/` | `chore:` |
| refactor | `refactor/` | `refactor:` |

- **Branch name:** `<prefix>/<short-kebab-description>` (e.g. `fix/loans-toggle-missing`).
- **Commit + PR title:** start with the matching Conventional-Commits prefix (e.g. `feat: …`).
- **Merge:** the type does not change the merge rule — feature PRs into beta may be squashed,
  promotions to `main` are always merge-commits — but keep the prefix in the resulting title.
- In an existing worktree session whose branch is already fixed, keep the existing branch but
  still apply the type prefix to commits and the PR title.

**Translations are part of done (feature + bug fix)**

- Every feature and bug fix must include complete i18n updates for all supported locales.
- Do not open or merge a PR while translations are incomplete.
- Before PR creation, run an i18n completeness pass (no missing keys, and no newly introduced
  hardcoded UI text without i18n keys).

**Record decisions in the App-Guidance documentation repo**

Documentation lives in [`Flux76HQ/App-Guidance`](https://github.com/Flux76HQ/App-Guidance), not in
this repository. Whenever a session settles something that outlives its own PR, write it up there —
do not wait to be asked.

- **What to record:** a route or contract between DiscVault and another system (MovieVault, the iOS
  app, a plugin API), an ownership or precedence rule (which source may supply a field, which value
  wins), a deliberate policy and why it exists, and the symptom-to-cause mapping that made a bug
  findable. A plain bug fix with no rule behind it does not need an entry; the release notes cover
  that.
- **How to record it:** describe the rule and the reasoning, not the diff — the document has to stay
  true after the code moves. Reference DiscVault symbols and paths so a reader can find the
  implementation, and keep an appendix mapping each rule to its source location. Carry open questions
  across as open questions instead of quietly resolving them. Write it in English.
- **Attaching the repo:** App-Guidance sits under a different owner (`Flux76HQ`) than this repository
  (`helmerzNL`), and a session cannot attach a repo from another owner. A session working in
  DiscVault therefore cannot push there: prepare the document, hand it over, and say plainly that it
  still needs to land in App-Guidance. To commit it directly, start a session with App-Guidance as
  its initial source.

**Adding or updating a plugin means updating the documentation**

Follow this automatically on every plugin change — do not wait to be asked. A plugin change is
not done when the code passes. Every new plugin under `app/backend/next_plugins/`, and every
version bump of an existing one, must carry its documentation in the same change set.

DiscVault keeps no plugin documentation of its own, so "the documentation" means App-Guidance,
per the section above. A plugin is a contract with an external system, which is exactly the
category that section says to record: what the plugin reaches, which fields it may supply, and
how it loses against other sources.

- **A new plugin** needs an entry covering its purpose, the source it speaks to, and its
  precedence relative to the plugins that answer the same question.
- **A version bump** needs an entry only when behaviour a reader depends on changed — a new
  field, a changed precedence, a different upstream. A routine fix is release-notes material.
- **A packaged artefact under `dist/plugins/`** is a release step, not a substitute: shipping a
  new zip without repointing whatever pins the version leaves installations on the old one.
- Say in the PR body what you recorded, or that you judged the change documentation-neutral.
  An unexplained silence is not the same as "nothing to record".

Note the ownership constraint above: a session working only in DiscVault cannot push to
App-Guidance. Prepare the write-up and hand it over, saying plainly that it still needs to land.

**When the user starts a new feature (or bug/other work)**

1. Base the work on `release/v26-beta`, **never** on `main` or `legacy`. In a worktree session
   whose branch already targets beta, that is fine; otherwise branch off `origin/release/v26-beta`.
2. Name the branch `<type-prefix>/<short-kebab-description>` using the classified type above.
3. Plan the change against beta and keep the scope to that one bug/feature.

**When the user asks to commit (or you are about to commit)**

1. Confirm you are on a **beta-based feature branch**, not directly on `main`.
2. If the change touches any protected path (see the Version-guard list above), make sure
   `app/VERSION` is bumped — run `python app/scripts/bump_version.py` (or rely on the
   `core.hooksPath .githooks` pre-commit hook). Docs-only (`*.md`/`*.txt`) needs no bump.
3. Start the commit message with the classified type prefix (`fix:`/`feat:`/`docs:`/…) and
   include the `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>` trailer
   unless the user opts out.
4. Push and open the PR **into `release/v26-beta`**, giving the PR the same type prefix in its
   title (feature PRs into beta may be squashed).
5. Confirm translations are complete across all locales (no missing i18n keys and no new
   untranslated UI strings).
6. **Before the PR is merged, re-check the bump against the current base** — beta may have moved
   while the PR was open, which makes an earlier bump stale (see "Re-check the bump right before
   merging" above). Never merge with a red version guard.
7. Let it build/test on the beta channel before considering promotion.
8. **After the PR merges, delete the feature branch** (`git push origin --delete <branch>`) —
   unless it is the active Copilot session/worktree branch (reused across PRs) or a
   permanent branch (`main`, `release/v26-beta`, `legacy`). If unsure, ask before deleting.

**When the user asks to "ship", "promote", or "push to main"**

- Confirm the change landed on `release/v26-beta` first.
- Promote via a PR merged with `--merge` (a merge commit). **Never squash a promotion.**
- Treat `release/v26-beta` as the source of truth; if beta and main diverge, use the recovery
  recipe above.
- After promotion, verify `main` and `release/v26-beta` are content-identical
  (`git diff origin/main origin/release/v26-beta --stat` empty).
- Promotion only merges — **never delete `release/v26-beta`** afterwards.

---

## Deployment-file changes must be spelled out in the PR

When a change touches a **Compose file** (`docker-compose*.yml`, `compose*.yml`) or an
**environment template** (`.env.example`), the PR body must state, explicitly and in one
place, exactly what the operator has to change in their own files.

The reason is that these two files are *templates*, not the deployed configuration. A
tracked `.env.example` and a tracked Compose file are read by CI and by the repository;
the `.env` and the overrides that actually run the deployment are untracked and live on
the host. So a diff that looks complete in the PR can still leave a running deployment
missing a variable, and nothing fails until the setting is needed.

State it as an operator instruction, not a diff summary:

- the **exact variable name**, its default, and whether it must be added by hand;
- the **exact Compose mapping** line, if one was added or changed;
- whether an existing deployment keeps working untouched, or must be edited before the
  next deploy.

"No deployment-file changes in this PR" is a fine answer when true. Silence is not: the
reader cannot tell the difference between "nothing to do" and "not mentioned".

Full reference: **[DiscVault 26 — Branching & releases](https://wiki.zbonline.nl/en/Projecten/Coding/discvault/branching)**
and the step-by-step **[feature → production workflow](https://wiki.zbonline.nl/en/Projecten/Coding/discvault/feature-workflow)** on the wiki.
