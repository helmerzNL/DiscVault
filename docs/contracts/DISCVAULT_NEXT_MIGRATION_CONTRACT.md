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
