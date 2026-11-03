# The standalone MovieVault v2 plugin

How DiscVault reaches MovieVault v2, the metadata source it ranks first. The
[README](../README.md#architecture) links here from its *Architecture* section.

DiscVault `26.4.44` and newer provide the local anonymous synchronization
bridge used by the separately released `movievault_v2` plugin. Download the
plugin ZIP and checksum from
[helmerzNL/DiscVault-Plugins](https://github.com/helmerzNL/DiscVault-Plugins),
verify the SHA-256 checksum, and extract its `movievault_v2/` root folder into
`DISCVAULT_PLUGIN_INSTALL_DIR` (normally `/data/plugins` in the persistent
volume).

Restart DiscVault or refresh its plugin registry. The plugin is **enabled by
default** and is DiscVault's highest-priority metadata source (order 5, above
every other bundled source including `tmdb` at 10). DiscVault queues the first
synchronization automatically, and normal barcode, title, release, and box-set
queries then use the derived PostgreSQL index.

Two settings are **enforced by DiscVault and not editable in the plugin-settings
UI**:

- **Endpoint.** DiscVault always uses the enforced origin
  `https://movies2.vaultstack.eu`. Self-hosted operators who need a different
  instance can override it out-of-band with the `MOVIEVAULT_V2_ORIGIN`
  environment variable (an invalid or empty value falls back to the default).
- **Anonymous bucket fallback.** Always on. It is what resolves a disc the
  locally synced index does not carry yet, so a barcode lookup without it
  silently misses every title MovieVault has not distributed into this
  instance's index. Override out-of-band with
  `MOVIEVAULT_V2_BUCKET_FALLBACK=0` only if you have a reason to. The fallback
  applies to barcode and box-set lookups; title queries cannot use it, because
  buckets are keyed by the hash of the EAN.

`movievault_26` has been **removed from DiscVault and uninstalled** from
existing installations. It was DiscVault's only `metadata_receiver`, so the v1
receiver route that contributed back to MovieVault is gone with it, along with
its stored connection (instance keypair, API tokens, handshake state) and the
`MOVIEVAULT_SEARCH_URL` / `MOVIEVAULT_INGEST_URL` /
`MOVIEVAULT_DISCVAULT_HANDSHAKE_SECRET` / `MOVIEVAULT_SHARING_MODE` settings
that configured it. What continues to work is the separate `movievault_v2`
release-contribution and field-correction path, gated by
`movievault_v2_contribution_enabled` in App Admin — that one is untouched.

DiscVault `26.4.57` adds negotiated `distribution-3` support. The matching
deterministic `movievault_v2` feature package (currently 1.5.0) ships as a
bundled default plugin and is also available under `dist/plugins/` for staged
installation. Older plugins continue to use `distribution-2`; v3 is selected
only when both the plugin manifest and core declare support.

DiscVault no longer bundles or distributes a local `bluray_com` metadata
provider. Blu-ray.com release-detail fallback is owned by MovieVault v2 and is
reached only through the standalone `movievault_v2` plugin and DiscVault's
anonymous core bridge. Existing installations must disable and uninstall the
old `bluray_com` metadata provider before enabling this route. The separate
`import_bluray_com` collection-import plugin remains available and unchanged.

DiscVault `26.4.61` adds core consumer support for `distribution-4` (a
strict `distribution-3` superset that adds a required-nullable primary
`poster` on release and box-set upserts, with bounded anonymous background
caching and authenticated local poster URLs). Distribution-4 is negotiated
only when both the installed plugin manifest and DiscVault core declare
support, so it stays **inactive by default** until the matching
`movievault_v2` plugin release advertises `distribution-4` in its
`distributionContractRange`; that plugin change ships separately from
[helmerzNL/DiscVault-Plugins](https://github.com/helmerzNL/DiscVault-Plugins)
and is not part of this repository. Existing `distribution-2`/`distribution-3`
installations are unaffected.

DiscVault `26.5.10` adds a bounded, anonymous `release-technical-1` bridge for
the existing barcode scanner and manual barcode entry. Matching
`movievault_v2` plugin releases keep this technical fallback disabled by
default. When an operator enables it, DiscVault still checks the synchronized
local index and optional hash bucket first. Only a genuine miss sends the
normalized barcode and bounded release hints to MovieVault; no account,
instance, collection, contribution credential, cookie, or client address is
sent. Pending results are polled only by their opaque resolution ID. External
results remain visibly unreviewed and require confirmation.

The same DiscVault server-side bridge is used by PWA, Android, and iOS clients.
Client scanners only submit a barcode to DiscVault and never contact
MovieVault or a metadata provider directly.
