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
