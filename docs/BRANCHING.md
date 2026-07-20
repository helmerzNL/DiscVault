# Branch & release strategy

DiscVault uses one clean promotion chain with **two logical images**. All
development happens in the beta branch; features then flow in a controlled
manner — per feature — through to production.

```
feature-branch  ──PR──▶  release/v26-beta   (integration/test)   image :v26-beta + :beta
   (from main)    │
                  └──PR──▶  main             (production)         image :latest + :v26 + :stable
                           └─ release.yml ──▶ tag v26.x.x         image :v26.x.x (+ :v26, :stable)
```

## Two images

| Image | Source | Docker tags |
|---|---|---|
| **Production** | branch `main` | `:latest`, `:v26`, `:stable` |
| **Development** | branch `release/v26-beta` | `:v26-beta`, `:beta` |
| **Release snapshot** | git tag `v26.x.x` on a `main` commit | `:v26.x.x`, `:v26`, `:stable` |
| **Manual escape** | workflow_dispatch (Actions) | `:dev`, `:dev-<sha>` |

`:stable` has followed **`main`** since the 2-image model (no longer just a
standalone tag). As a result, `:latest` and `:stable` are always the same image
and `:stable` can no longer accidentally get ahead of production because a tag
was set from beta.

> `release/v26` is deprecated: it produced the same `:latest`/`:v26`
> images as `main` and caused tag races. Use `main`.

## Update channels (in the app)

The in-app update check has three channels. Each channel reads its "latest
version" from `app/VERSION` on a branch — no longer dependent on separately
cut GitHub Releases:

| Channel | Source branch | Image |
|---|---|---|
| `stable` | `main` | `:stable` |
| `beta` | `release/v26-beta` | `:v26-beta` |
| `auto` | heuristic (chooses stable or beta) | — |

The old `v26` channel is discontinued; a saved `v26` preference is automatically
treated as `stable` (`main` == the old v26 line).

## Daily workflow (per feature)

1. Branch a feature branch **from `main`**.
2. Open a PR to **`release/v26-beta`**. CI builds the `:v26-beta`/`:beta`
   image for integration testing.
3. Complete translations for all supported locales (no missing i18n keys and no newly introduced
   hardcoded UI text) **before** the PR is merged.
4. Merge the PR into `release/v26-beta` once it is green and reviewed.

Because each feature branch is branched from `main`, you can later **individually**
promote it without pulling in the rest of beta.

## Promoting a feature to production

When a feature is production-ready, open a **second PR** from that same feature
branch to `main`:

```sh
gh pr create --base main --head <feature-branch> \
  --title "promote: <feature>" --fill
# after green checks + review:
gh pr merge --merge
```

`main` is protected (PR required, `version-guard` must be green, Copilot review),
so promotion always goes through a PR — never via a direct push. After the merge,
`main` builds `:latest` + `:v26` + `:stable`.

> Does a feature not yet decouple from other beta changes? Put it behind a
> **feature flag** instead of cherry-picking commits — this keeps the lineage
> clean and prevents drift.

## Cutting a release

`app/VERSION` is the single source of truth for the version number. After
promotion PR(s) have landed on `main` and the version-guard has bumped
`app/VERSION`, review and commit the version-specific release notes at
`docs/releases/vX.Y.Z.md`, then cut a release via the **`Release (tag main)`**
workflow:

1. GitHub -> **Actions** -> **Release (tag main)** -> **Run workflow**.
2. Choose branch **`main`** and start.

The workflow reads `app/VERSION`, requires the matching reviewed
`docs/releases/v<VERSION>.md` file, checks that the tag doesn't exist yet, puts
`v<VERSION>` on the current `main` commit, and publishes that file verbatim as
the GitHub Release notes. The tag push then triggers `docker-publish.yml`, which
builds the release snapshot (`:v26.x.x` + refreshed `:v26`/`:stable`) from
exactly that commit.

> Avoid manually cherry-picking/porting individual commits between branches:
> that was the cause of the earlier divergence between `main`, `v26`, and
> `v26-beta`.

## Version management

Changes to protected paths (`app/**`, `.github/workflows/`, `app/deploy/`,
`dist/plugins/`, ...) require a bump of [`app/VERSION`](../app/VERSION).
See the version-guard workflow. Documentation (`*.md`, `*.txt`) is exempt.

Auto-bump:

```sh
git config core.hooksPath .githooks   # once per clone/worktree
```
