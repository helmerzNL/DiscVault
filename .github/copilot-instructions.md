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

### Branch protection

`main` is protected: PRs require review, the version-guard check, and Copilot review before
merge. Do not push directly to `main`.

### Agent behaviour (for Copilot) — ALWAYS follow this

This process is **mandatory**. Follow it automatically on every feature, commit, and promotion —
do not wait to be reminded. If a request would break the two-branch model or the merge-commit
promotion rule, stop and warn the user before acting.

**When the user starts a new feature**

1. Base the work on `release/v26-beta`, **never** on `main` or `legacy`. In a worktree session
   whose branch already targets beta, that is fine; otherwise branch off `origin/release/v26-beta`.
2. Give the branch a short, descriptive kebab-case name.
3. Plan the change against beta and keep the scope to that one feature.

**When the user asks to commit (or you are about to commit)**

1. Confirm you are on a **beta-based feature branch**, not directly on `main`.
2. If the change touches any protected path (see the Version-guard list above), make sure
   `app/VERSION` is bumped — run `python app/scripts/bump_version.py` (or rely on the
   `core.hooksPath .githooks` pre-commit hook). Docs-only (`*.md`/`*.txt`) needs no bump.
3. Include the `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>` trailer
   unless the user opts out.
4. Push and open the PR **into `release/v26-beta`** (feature PRs into beta may be squashed).
5. Let it build/test on the beta channel before considering promotion.
6. **After the PR merges, delete the feature branch** (`git push origin --delete <branch>`) —
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

Full reference: **[DiscVault 26 — Branching & releases](https://wiki.zbonline.nl/en/Projecten/Coding/discvault/branching)**
and the step-by-step **[feature → production workflow](https://wiki.zbonline.nl/en/Projecten/Coding/discvault/feature-workflow)** on the wiki.
