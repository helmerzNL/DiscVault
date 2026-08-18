---

## 📦 Change Spec — release notes (fill at merge)

<!--
This is the ONE manual writing step in the Flux76 release-notes pipeline. Fill it
in whenever this PR is a user-facing change, so App Store / Play Store / GitHub /
self-hosted notes render with near-zero rewriting.
Source of truth: FLU-5 "Fast Content Pipeline" doc, §2 (Change Spec).
Authoring rules:
  - Benefit-first, not feature-first ("Search is now instant" > "Rewrote search index").
  - One idea per bullet — no ampersand-chaining.
  - No internal jargon — if a small-company user would not recognize the word, cut it.
  - Anything a user must DO goes in `breaking:` — it drives the required-action line.
Internal-only PR (refactor / CI / deps)? Leave the user-facing fields blank, fill
`internal_only:`, and tick the box below.
-->

```yaml
version: ""        # semver of the release this ships in
name: ""           # optional short codename, e.g. "Faster Sync"
headline: ""       # <=60 chars, plain-language, benefit-first — the one thing users get
highlights:        # 1–4 user-facing wins, benefit-first, no jargon
  - ""
fixes:             # user-visible bug fixes (skip internal-only)
  - ""
breaking: []       # anything users must act on (permissions, migrations, re-login)
cta: ""            # optional call to action, e.g. "Update to enable offline mode"
internal_only:     # NOT shipped to users — refactors, CI, deps
  - ""
```

- [ ] Change Spec filled in, **or** this PR is internal-only (no user-facing release notes).
