# DiscVault Next UI Contract

DiscVault Next UI should feel calm, modern and app-like. The long-term visual
direction is Apple-inspired: restrained chrome, clear hierarchy, high-quality
spacing, light/dark/system appearance and layouts that scale cleanly from mobile
to large desktop screens.

## Application Shell

- The collection app exposes a System / Light / Dark appearance control.
- The selected appearance is stored client-side and must survive refreshes.
- Shell navigation should stay quiet and functional; avoid marketing-page hero
  patterns inside the logged-in application.
- Large desktop layouts should use available width for scanning and comparison,
  not oversized cards or decorative whitespace.

## Migration UX

The current `/api/next/migration` page is a diagnostic preview for development
and support. It may show detailed source counts, raw report data and advanced
state.

The production first-run migration wizard must be simpler:

- Show a short step flow: legacy data found, what will be imported, start, done.
- Keep raw readiness/report details behind an advanced/details affordance.
- Make owner/admin requirements explicit before starting migration.
- Non-admin users must see a blocked-but-friendly state while migration is
  pending.

The wizard consumes `/api/next/startup/status` and migration endpoints; it should
not duplicate migration decision logic in frontend-only code.
