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

  Sections, in order:
    - Title + version + date
    - Highlights (1–3 sentences, plain language)
    - ✨ Features        — new user-facing capabilities
    - 🐛 Bug fixes       — corrected behaviour
    - 📄 Docs / Internal — documentation & tooling, no app impact
    - 🔧 Commits         — every commit hash + subject in the release
    - 🔀 Included PRs     — PR numbers merged into this release
    - 📦 Version          — version bump and any bundled component versions
-->

# vX.Y.Z — <short release title>

_Released: YYYY-MM-DD_

## Highlights

<1–3 sentences in plain language describing what this release delivers.>

## ✨ Features

- **<Feature name>.** <What it does and why it matters to the user.>

## 🐛 Bug fixes

- **<Area>.** <What was broken and how it now behaves.>

## 📄 Docs / Internal

- <Documentation or tooling change with no impact on the app itself.>

## 🔧 Commits

- `<hash>` <commit subject>

## 🔀 Included PRs

- #<number> — <PR title>

## 📦 Version

`app/VERSION`: `<old>` → `<new>`
<Optional: bundled component version changes, e.g. plugin X 1.1.0 → 1.3.0>
