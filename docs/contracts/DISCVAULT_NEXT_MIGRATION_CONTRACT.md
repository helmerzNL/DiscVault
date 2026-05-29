# DiscVault Next Migration Contract

This contract records the current SQLite-to-PostgreSQL migration behavior for
DiscVault Next.

## Default Legacy Import Scope

The normal in-place migration reads the existing legacy `/data/discvault.db` and
keeps legacy media files on the filesystem. It imports:

- collection data: movies, people, credits, box sets, vaults, collections and
  their relationships;
- local media references for posters, backdrops, profiles and avatars;
- legacy users;
- legacy passkey credentials;
- legacy media groups, group members, group-to-movie links, group invites and
  group digital-access rows.

Security import is on by default for the in-place migration because existing
users must be able to keep signing in after the upgrade. Personal data such as
watch history and watchlists remains opt-in and is not part of the default
functional migration.

## Passkey Compatibility

Legacy passkeys are copied as stored public WebAuthn credentials. They will only
continue to work when the DiscVault Next `RP_ID` and allowed origin match the
domain context where those passkeys were originally registered. If the domain
changes, users may need to register new passkeys.

Recovery hashes and raw recovery secrets are not imported into the normal Next
migration.

## Owner and Migration Gate

If DiscVault Next has no active passkey authentication yet, migration can be
started as a bootstrap operation.

If an Owner/authenticated environment already exists, migration start requires an
authenticated user with `collection.import`; in Basic mode this effectively means
Owner or Beheerder/Admin. Non-admin users who sign in before the first migration
has completed must see a clear migration-pending message and cannot continue into
the collection UI.

## Backfill Rule

If a beta instance already completed an older collection migration without
security data, the readiness API may expose a security/group backfill state. That
backfill imports missing users, passkeys and media groups for the same legacy
source without requiring the PostgreSQL collection tables to be emptied first.

When an older migration run cannot be matched by source hash, the readiness API
may still allow this backfill if the target database clearly contains a legacy
DiscVault import, for example `legacy-movie-*` or `legacy-*` public identifiers,
and the target security/group counts are lower than the detected legacy source
counts.

Once a completed migration run records `include_security = true`, the backfill is
considered closed and readiness must stop offering `ready_for_security_backfill`.

## Startup Orchestration

DiscVault Next exposes a startup status endpoint for the PWA shell:

```txt
GET /api/next/startup/status
```

The endpoint combines auth readiness, current user role, schema state and
migration readiness into one phase:

- `owner_setup`: no usable owner/passkey exists and no migration is blocking
  startup.
- `migration_required`: legacy data is detected and the current actor may start
  or continue migration.
- `migration_running`: a migration job is active.
- `migration_pending_non_admin`: a non-admin user is signed in before migration
  has completed.
- `schema_blocked`: PostgreSQL schema migrations are pending or unhealthy.
- `sign_in_required`: authentication is active and setup requires a signed-in
  user.
- `ready`: normal collection UI may load.

The collection UI must use this startup state before loading collection data.
Only `ready` permits the normal collection view. All other phases show a setup
or migration panel and keep the collection area blocked.

The payload also includes `steps`, `canCreateOwner`, `canSignIn`,
`canSwitchAccount` and `canStartMigration`. The PWA shell uses those fields to
render a first-run setup rail:

- Owner passkey: create the first owner or wait for legacy users/passkeys to be
  imported.
- Legacy data: show source counts for movies, users, groups and passkeys when a
  legacy database is mounted.
- Migration: start, follow or block migration according to the readiness state.
- Collection: remains unavailable until startup reaches `ready`.
