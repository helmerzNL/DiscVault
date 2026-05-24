# DiscVault Database

DiscVault stores local application data in SQLite.

The detailed database contract and schema notes are intentionally not published
in this repository. Treat the database layout as an internal implementation
detail unless a migration or export/import interface is documented in a release.

For backups, use DiscVault's built-in backup and restore features instead of
depending on internal table structure.
