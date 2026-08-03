<!--
  DiscVault — GitHub Release Note template

  CONVENTION: DiscVault release notes are ALWAYS written in English, regardless
  of the language used in issues, PRs or chat. Keep them user-facing and concise.

  How to use:
    1. Copy the section below (everything under the divider) into GitHub's
       "Draft a new release" body, or into docs/releases/vX.Y.Z.md.
    2. Fill in every placeholder. Delete sections that do not apply
       (e.g. "Bug fixes" if the release only adds features).
    3. Always list the commits and PRs that went into the release so the
       history is traceable from the release note alone.
    4. Tag name: vX.Y.Z — must match app/VERSION.

    5. Name the PR next to each entry, not just in the PR list. A reader who wants
       the reasoning goes straight to it instead of scanning two lists.
    6. Say what was broken, not only that it is fixed. "The catalog syncs again"
       tells nobody whether it explains the symptom they saw.
    7. If a section is empty, say so in one line rather than deleting it —
       "Upgrade notes: nothing to do" is information; a missing section is not.

  Sections, in order:
    - Title + version + date
    - Highlights (1–3 sentences, plain language)
    - ✨ Features        — new user-facing capabilities
    - 🐛 Bug fixes       — corrected behaviour
    - 📄 Docs / Internal — documentation & tooling, no app impact
    - 🔧 Commits         — every commit hash + subject in the release
    - 🔀 Included PRs     — PR numbers merged into this release
    - ⚠️ Upgrade notes    — anything the operator or user must DO
    - 📦 Version          — version bump, bundled components, migrations

  About "Upgrade notes" — this section exists because three separate releases
  needed one and did not have it:
    - 26.7.24 required one manual reload per user (the banner announcing the
      update shipped *in* that version, so the bundle being upgraded from had
      nobody listening) and testers reported the bug as unfixed.
    - 26.7.28 disabled `movievault_26`, silently stopping contribution back to
      MovieVault, and the only warning lived in a PR description.
    - The 26.7.35 promotion ran ten database migrations against production.
  If a release needs a backup taken, a plugin re-enabled, a reload forced, or a
  setting re-applied, it belongs here — not in a commit message nobody reads.
-->

# vX.Y.Z — <short release title>

_Released: YYYY-MM-DD_

## Highlights

<1–3 sentences in plain language describing what this release delivers.>

## ✨ Features

- **<Feature name>** (#<PR>). <What it does and why it matters to the user.>

## 🐛 Bug fixes

- **<What the user saw>** (#<PR>). <What was broken, why it behaved that way, and
  how it behaves now. Name the symptom a user would recognise, so someone who hit
  it can tell whether this is their bug.>

## 📄 Docs / Internal

- <Documentation or tooling change with no impact on the app itself.>

## 🔧 Commits

- `<hash>` <commit subject>

## 🔀 Included PRs

- #<number> — <PR title>

## ⚠️ Upgrade notes

<Anything the operator or the user must DO. Write "Nothing to do." when that is
true — an empty section reads as an oversight, an explicit "nothing" does not.>

- **Back up first** if this release runs database migrations.
- **Manual reload required** if the service worker or frontend bundle changed in a
  way the running bundle cannot announce.
- **Settings overridden** if a migration changes a plugin's enabled state or order,
  and how to restore the previous choice.

## 📦 Version

`app/VERSION`: `<old>` → `<new>`

Bundled plugins: <e.g. `movievault_v2` 1.5.0 → 1.5.2, or "unchanged">

Database migrations: <list them, or "none">

<!--
  Note on tagging: a `v26.*.*` tag triggers .github/workflows/docker-publish.yml,
  which publishes the :stable and :v26 production images. Only tag a commit that
  is already on `main` and already deployed — tagging is a publish, not a label.
-->
