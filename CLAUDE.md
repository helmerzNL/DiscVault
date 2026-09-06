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

## Version guard: CI bumps `app/VERSION`, a PR must not

**Never bump `app/VERSION` in a pull request.** The bump is applied by CI on
`release/v26-beta` after the merge; a PR that carries one is refused by
`.github/workflows/version-guard.yml` with:

```
app/VERSION must not be changed in a pull request: CI bumps it on release/v26-beta after the merge.
```

### Why it moved out of the PR

A hand-written bump is only valid against the base as it stood when it was written, and GitHub
does not re-run a check when the base moves. A PR could be green when opened and wrong when
merged, with nothing in between to notice.

This happened three times: #473/#474, #516/#517 (repaired by #520), and finally #570/#571/#572
merging within 26 seconds, all bumping to 26.8.39 — two guards red on beta, no image built for
beta's head until #573 bumped it by hand.

The old rule was "re-check the bump right before merging". Three sessions merging seconds apart
cannot satisfy it: the bump goes stale in between and no human wins that race. Applied after the
merge, the bump is derived from the branch it lands on and cannot go stale.

### Where each thing now happens

| Event | Behaviour |
|---|---|
| PR into `release/v26-beta` | `app/VERSION` must be **unchanged** (`check_version_bumped.py --forbid-change`) |
| Push to `release/v26-beta` | The `version-bump` job in `docker-publish.yml` bumps the patch, commits to beta, and the image is built **from that commit** |
| PR into `main` (a promotion) | The opposite rule: the diff **must** carry a newer version, checked with `--aggregate`. "Leave the file alone" belongs to PRs into beta only |
| Push to `main` (a promotion) | Unchanged: the version must be strictly greater, and promotions carry beta's bump commits |

The promotion row is not a special case bolted on — it is what the rule always meant. CI
applies the bump on beta, so a promotion PR is the one pull request whose whole purpose is
to carry those bumps to production. Applying "leave `app/VERSION` alone" there refused every
promotion outright (#621).

The bump commit deliberately carries **no `[skip ci]` marker**. GitHub honours that marker for
`pull_request` as well as `push`, and beta's tip is always a bump commit — so it skipped every
check on every promotion PR, and a required check that never reports blocks a merge exactly
like a red one. A human replaying a bump commit is stood down by an explicit condition on the
jobs in `docker-publish.yml` instead, where it cannot reach another pull request's checks.

Protected paths are unchanged — they decide whether a bump is *due*, not who applies it:

- `.github/workflows/`
- `app/Dockerfile`, `app/docker-compose*`
- `app/backend/`
- `app/frontend/`
- `app/mcp-server/`
- `app/deploy/`
- `app/scripts/`
- `dist/plugins/`

`*.md` and `*.txt` are ignored.

### Two consequences worth holding on to

**The pre-commit hook no longer bumps.** `.githooks/pre-commit` still rejects forbidden iOS
artifacts, but calling `bump_version.py` there would write the one file a PR must leave alone.
`app/scripts/bump_version.py` still exists — CI invokes it with `--force` — but running it by
hand on a feature branch produces a change the guard then refuses.

**The bump job may never force-push.** `Block force pushes` is active on every branch in this
repository. A rejected push is retried by restarting from beta's new tip, never with `--force`.

If a guard is red, **do not merge**, and say so when someone is about to.

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

## Every feature is an explicit iOS/Android decision

DiscVault is three apps, not one: Core (this repository, the PWA and backend), the iOS app
(`DiscVaultApp`) and the Android app (`DiscVault-AndroidApp`). A feature built here reaches the
other two only if somebody decides it should.

**There is no default.** A feature that lands in Core with no decision recorded is not "PWA-only" —
it is a gap nobody can later tell apart from a deliberate choice. Six months on, the question "was
this left out on purpose, or forgotten?" has no answer anywhere, and the honest reading is the
pessimistic one: it was forgotten.

Follow this automatically on every feature — do not wait to be reminded.

### Ask before building, not after

Ask at the moment you classify the work (the bug/feature/chore step above): **do we want this on
iOS and/or Android, and in what form?** Asked then, the answer can still shape the design — a field
the mobile apps need has to reach them over the sync payload, and that is cheaper to decide before
the column exists than after.

This is **not blocking**. With no answer, build the Core feature and record "not yet decided"; do
not stall the work waiting for one.

The question is only owed for a *feature*. A bug fix, a refactor or a chore inherits whatever
decision its feature already carries.

### Record the answer in the PR body

A required section, in the same spirit as the deployment-file rule below and for the same reason:
**silence is not the same as "nothing to do"**, and a reader cannot tell the difference. State,
explicitly and in one place:

- **iOS** — wanted / not wanted / not yet decided;
- **Android** — the same;
- and when the answer is *not wanted*, **why** — so it reads as a decision rather than an omission.

"Core-only, deliberately: this configures the server and has no mobile surface" is a complete
answer. An unexplained silence is not.

### Write it on the mobile parity list

The list lives in App-Guidance, at
[`projects/discvault/specs/discvault-mobile-parity.md`](https://github.com/Flux76HQ/App-Guidance/blob/main/projects/discvault/specs/discvault-mobile-parity.md).
Every feature gets an entry — including the ones decided *against*, which are the entries that stop
the same question being asked twice.

An entry names the feature, the Core build it shipped in, the decision per platform, and what an
implementation would need: which sync fields already carry the data, and any semantics the mobile
side must copy rather than re-derive. Where two platforms could plausibly read the same stored value
differently, say which reading is correct — that is the difference between parity and two apps that
merely look alike.

The entry lands in App-Guidance the way every other write-up does — see the section below for how,
including that it goes in through a branch and a pull request rather than straight onto `main`.

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

### Adding or updating a plugin means updating the documentation

Follow this automatically on every plugin change — do not wait to be asked. A plugin change is
not done when the code passes. Every new plugin under `app/backend/next_plugins/`, and every
version bump of an existing one, must carry its documentation in the same change set.

DiscVault keeps no plugin documentation of its own, so "the documentation" means App-Guidance.
A plugin is a contract with an external system — exactly the category this section says to
record: what the plugin reaches, which fields it may supply, and how it loses against other
sources.

- **A new plugin** needs an entry covering its purpose, the source it speaks to, and its
  precedence relative to the plugins answering the same question.
- **A version bump** needs an entry only when behaviour a reader depends on changed — a new
  field, a changed precedence, a different upstream. A routine fix is release-notes material.
- **A packaged artefact under `dist/plugins/`** is a release step, not a substitute: shipping a
  new zip without repointing whatever pins the version leaves installations on the old one.
- Say in the PR body what you recorded, or that you judged the change documentation-neutral.
  An unexplained silence is not the same as "nothing to record".

The same applies here: the plugin write-up lands in App-Guidance like any other.

**Where the repositories live, and pushing to them:** every Flux76 repository sits under
**`Flux76HQ`** — App-Guidance, `DiscVaultApp`, `DiscVault-AndroidApp` and the rest. **This
repository is the exception**: DiscVault Core is `helmerzNL/DiscVault`.

Differing owners do **not** block anything. When the App-Guidance working copy is attached to the
session, commit the document there directly and follow that repository's own rules — a branch off
its default branch and a pull request, never a commit straight to `main`; its `CLAUDE.md` is
authoritative for its workflow, and documentation-only edits are exempt from its `VERSION` bump.

Only when the working copy is genuinely not available does the fallback apply: prepare the
document, hand it over, and say plainly that it still needs to land. Do not claim you cannot push
without having tried — that leaves a write-up sitting in a scratchpad that everyone believes was
filed.

---

## Checklists

### When starting new work

1. Base the work on `release/v26-beta`, **never** on `main` or `legacy`
   (`git fetch origin release/v26-beta && git checkout -b <prefix>/<desc> origin/release/v26-beta`).
2. Name the branch with the classified type prefix.
3. Keep the scope to that one bug/feature.
4. For a **feature**, ask whether it should also exist on iOS and/or Android before
   building it. Not blocking — with no answer, note "not yet decided" and carry on.

### When committing

1. Confirm you are on a **beta-based branch**, not on `main`.
2. **Leave `app/VERSION` untouched** — CI bumps it on beta after the merge, and the guard
   refuses a PR that carries a bump.
3. Start the commit message with the classified type prefix (`fix:`/`feat:`/`docs:`/…).
4. Add a Claude co-author trailer, e.g. `Co-Authored-By: Claude <noreply@anthropic.com>`
   (mirroring the Copilot trailer the Copilot doc mandates), unless the user opts out.
5. Push and open the PR **into `release/v26-beta`** with the same type prefix in its title.
6. Confirm translations are complete across all locales.
7. For a feature, state the **iOS/Android decision** in the PR body — wanted, not
   wanted (with the reason) or not yet decided — and prepare its entry for the mobile
   parity list in App-Guidance.
8. Never merge a PR whose version guard is red. There is nothing left to re-check about the
   bump before merging — moving it into CI is what removed that step.
9. Let it build/test on the beta channel before considering promotion.
10. After the PR merges, delete the feature branch — unless it is the active session branch or a
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
[`edge-and-upload-limits.md`](https://github.com/Flux76HQ/App-Guidance/blob/main/projects/discvault/specs/edge-and-upload-limits.md)
in App-Guidance; the general rule is
[`project-baseline.md` §6](https://github.com/Flux76HQ/App-Guidance/blob/main/shared/guidelines/project-baseline.md),
which makes documenting those ceilings a required operational doc.

---

Full reference:
[DiscVault 26 — Branching & releases](https://wiki.zbonline.nl/en/Projecten/Coding/discvault/branching)
and the [feature → production workflow](https://wiki.zbonline.nl/en/Projecten/Coding/discvault/feature-workflow)
on the wiki.
