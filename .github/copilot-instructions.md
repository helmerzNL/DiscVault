# DiscVault contributor & agent instructions

## Version guard: CI bumps `app/VERSION`, your PR must not

**Do not bump `app/VERSION` in a pull request.** The bump is applied by CI on
`release/v26-beta` after your PR merges, and a PR that carries one is refused by the guard.

### Why it moved

A bump written by hand is only valid against the base as it stood when it was written, and
GitHub does not re-run a check when the base moves. So a PR could be green at the moment it was
opened and wrong at the moment it merged, with nothing between those two points to notice.

That is not hypothetical: it has happened three times. #473/#474, then #516/#517 (repaired by
#520), then three PRs merging within 26 seconds — #570, #571, #572 — all bumping to 26.8.39.
Two guards went red on beta, `Build & Publish Docker Image` gates on the same job, and beta's
head had no image until #573 bumped it by hand.

The old instruction was "re-check the bump right before merging". Three sessions merging seconds
apart cannot satisfy that: the bump goes stale in between, and no human wins that race by hand.
Applied after the merge, the bump is derived from the branch it lands on and cannot go stale.

### What this means for you

| Where | What happens |
|---|---|
| Your PR into `release/v26-beta` | Leave `app/VERSION` alone. The guard fails if you touch it. |
| The merge into `release/v26-beta` | `Build & Publish Docker Image` bumps the patch, commits to beta, then builds the image from that commit. |
| A promotion PR into `main` | The opposite rule: its diff **must** carry a newer version. "Leave `app/VERSION` alone" applies to PRs into beta only — applying it to a promotion refused every one of them (#621). |
| A promotion into `main` | Carries beta's bump commits. The original "strictly greater" check still applies there. |

The bump commit deliberately carries **no `[skip ci]` marker**. GitHub honours that marker for
`pull_request` as well as `push`, and beta's tip is always a bump commit — so it skipped every
check on every promotion PR, and a required check that never reports blocks a merge exactly like
a red one. A human replaying a bump commit is stood down by an explicit condition on the jobs in
`docker-publish.yml` instead, where it cannot reach another pull request's checks.

Protected paths still exist — they decide whether a bump is *due* — but you no longer act on
them:

- `.github/workflows/`
- `app/Dockerfile`, `app/docker-compose*`
- `app/backend/`
- `app/frontend/`
- `app/mcp-server/`
- `app/deploy/`
- `app/scripts/`
- `dist/plugins/`

`*.md` and `*.txt` files are ignored.

### The hook no longer bumps

`.githooks/pre-commit` still rejects forbidden iOS artifacts, but it no longer calls
`bump_version.py` — doing so would write the one file your PR must leave alone. If you enabled
hooks before this change, nothing needs redoing:

```sh
git config core.hooksPath .githooks
```

`app/scripts/bump_version.py` still exists; CI invokes it with `--force`. Running it by hand on
a feature branch will produce a change the guard then refuses.

### If you see the guard fail

```
app/VERSION must not be changed in a pull request: CI bumps it on release/v26-beta after the merge.
```

Restore the file from the base and commit that:

```sh
git checkout origin/release/v26-beta -- app/VERSION
git commit -m "chore: leave app/VERSION to CI"
```

### One thing that did not change

**Do not merge a PR whose version guard is red**, and say so when someone is about to.

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

**Every feature is an explicit iOS/Android decision**

DiscVault is three apps, not one: Core (this repository, the PWA and backend), the iOS app
(`DiscVaultApp`) and the Android app (`DiscVault-AndroidApp`). A feature built here reaches the
other two only if somebody decides it should.

**There is no default.** A feature that lands in Core with no decision recorded is not "PWA-only" —
it is a gap nobody can later tell apart from a deliberate choice. Six months on, "was this left out
on purpose, or forgotten?" has no answer anywhere, and the honest reading is the pessimistic one.

- **Ask before building, not after.** At the moment you classify the work (the bug/feature/chore
  step above), ask the user: *do we want this on iOS and/or Android, and in what form?* Asked then,
  the answer can still shape the design — a field the mobile apps need has to reach them over the
  sync payload, and that is cheaper to decide before the column exists than after. This is **not
  blocking**: with no answer, build the Core feature and record "not yet decided".
- **The question is owed for a feature only.** A bug fix, a refactor or a chore inherits whatever
  decision its feature already carries.
- **Record the answer in the PR body**, as a required section — same spirit as the deployment-file
  rule below, and for the same reason: silence is not the same as "nothing to do", and a reader
  cannot tell the difference. State **iOS** and **Android** each as wanted / not wanted / not yet
  decided, and when the answer is *not wanted*, say **why**. "Core-only, deliberately: this
  configures the server and has no mobile surface" is a complete answer; an unexplained silence is
  not.
- **Write it on the mobile parity list** in App-Guidance,
  `projects/discvault/specs/discvault-mobile-parity.md`. Every feature gets an entry — including the
  ones decided against, which are the entries that stop the same question being asked twice. An
  entry names the feature, the Core build it shipped in, the decision per platform, and what an
  implementation would need: which sync fields already carry the data, and any semantics the mobile
  side must copy rather than re-derive. Where two platforms could plausibly read the same stored
  value differently, say which reading is correct.
- The entry lands in App-Guidance the way every other write-up does — see the section below,
  including that it goes in through a branch and a pull request rather than straight onto `main`.

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
- **Where the repositories live, and pushing to them:** every Flux76 repository sits under
  **`Flux76HQ`** — App-Guidance, `DiscVaultApp`, `DiscVault-AndroidApp` and the rest. **This
  repository is the exception**: DiscVault Core is `helmerzNL/DiscVault`. Differing owners do
  **not** block anything. When the App-Guidance working copy is attached to the session, commit the
  document there directly and follow that repository's own rules — a branch off its default branch
  and a pull request, never a commit straight to `main`; its `CLAUDE.md` is authoritative, and
  documentation-only edits are exempt from its `VERSION` bump. Only when the working copy is
  genuinely not available does the fallback apply: prepare the document, hand it over, and say
  plainly that it still needs to land. Do not claim you cannot push without having tried.

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

The same applies here: the plugin write-up lands in App-Guidance like any other, directly when its
working copy is attached and as a hand-over only when it is not.

**When the user starts a new feature (or bug/other work)**

1. Base the work on `release/v26-beta`, **never** on `main` or `legacy`. In a worktree session
   whose branch already targets beta, that is fine; otherwise branch off `origin/release/v26-beta`.
2. Name the branch `<type-prefix>/<short-kebab-description>` using the classified type above.
3. Plan the change against beta and keep the scope to that one bug/feature.
4. For a **feature**, ask whether it should also exist on iOS and/or Android before building
   it. Not blocking — with no answer, note "not yet decided" and carry on.

**When the user asks to commit (or you are about to commit)**

1. Confirm you are on a **beta-based feature branch**, not directly on `main`.
2. **Leave `app/VERSION` untouched.** CI bumps it on `release/v26-beta` after the merge, and
   the guard refuses a PR that carries a bump.
3. Start the commit message with the classified type prefix (`fix:`/`feat:`/`docs:`/…) and
   include the `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>` trailer
   unless the user opts out.
4. Push and open the PR **into `release/v26-beta`**, giving the PR the same type prefix in its
   title (feature PRs into beta may be squashed).
5. Confirm translations are complete across all locales (no missing i18n keys and no new
   untranslated UI strings).
6. For a feature, state the **iOS/Android decision** in the PR body — wanted, not wanted
   (with the reason) or not yet decided — and prepare its entry for the mobile parity list
   in App-Guidance.
7. Never merge with a red version guard. There is no longer anything to re-check about the
   bump before merging — that is precisely what moving it into CI removed.
8. Let it build/test on the beta channel before considering promotion.
9. **After the PR merges, delete the feature branch** (`git push origin --delete <branch>`) —
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

### The harder case: a limit with no tracked file at all

A Compose file and an `.env.example` at least *appear* in the diff. The limits enforced
**in front of** DiscVault do not appear in any repository — and since DiscVault is
self-hosted, there is no knowing what is there: nothing, a reverse proxy, a CDN as well.

DiscVault's own limit is the **last** one a request meets, so raising it does not by
itself allow the operation. If something in front refuses first it returns its own
error, and DiscVault never sees the request: nothing is logged, and nothing can explain
it. Every place a person looks for the cause — the app's settings, its documented
maximum, its logs — says the operation is allowed. **The absence of a log line is the
diagnostic.**

So: **a PR that raises a size, rate or timeout limit must state the new value as a
number the operator can act on**, and never as one proxy's setting. "Whatever runs in
front must allow 60 MB" travels; `client_max_body_size` does not — it is nginx-only, and
a reader on any other proxy can neither follow it nor tell that it does not apply to
them. This rule exists because that exact mistake was made on this repository's own
upload work.

The current numbers, what each path allows, and the troubleshooting table live in
[`edge-and-upload-limits.md`](https://github.com/Flux76HQ/App-Guidance/blob/main/docs/apps/discvault/edge-and-upload-limits.md)
in App-Guidance; the general rule is
[`project-baseline.md` §6](https://github.com/Flux76HQ/App-Guidance/blob/main/docs/guidelines/project-baseline.md),
which makes documenting those ceilings a required operational doc.

Full reference: **[DiscVault 26 — Branching & releases](https://wiki.zbonline.nl/en/Projecten/Coding/discvault/branching)**
and the step-by-step **[feature → production workflow](https://wiki.zbonline.nl/en/Projecten/Coding/discvault/feature-workflow)** on the wiki.
