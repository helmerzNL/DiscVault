# DiscVault Release Notes

## 26.6.15 - Blu-ray.com provider moves to MovieVault v2

- Removed DiscVault's bundled `bluray_com` metadata scraper, package builder,
  package artifacts, and provider-specific tests. Blu-ray.com release-detail
  fallback now runs only inside MovieVault and reaches DiscVault through the
  standalone `movievault_v2` plugin and anonymous core bridge.
- Removed the retired provider from the unconfigured-integration notice.
- Existing installations must disable and uninstall the old `bluray_com`
  metadata provider before enabling the MovieVault v2 route. The separate
  `import_bluray_com` collection importer, the legacy `movievault_26`
  connector, and historical provider provenance remain supported.

## 26.4.70 - Modern Library, Legacy authentication, MovieVault posters, and owned containers

This release promotes the complete `release/v26-beta` feature train from
DiscVault 26.4.54 to 26.4.70. It introduces a redesigned Library and movie
detail experience, optional password and TOTP authentication, secure MovieVault
poster distribution, reversible artwork hiding, and more flexible Member Group
and container management.

### Highlights

- **Modern responsive Library.** The Library is now a sortable table with
  responsive desktop and compact layouts, richer metadata columns, persistent
  watched and Watchlist filters, and improved director fallbacks.
  ([#288](https://github.com/helmerzNL/DiscVault/pull/288),
  [#289](https://github.com/helmerzNL/DiscVault/pull/289),
  [#290](https://github.com/helmerzNL/DiscVault/pull/290))
- **iOS-inspired movie details.** Movie pages now use a full-bleed mobile hero,
  streamlined management controls, redesigned personal-list actions, dated
  rewatch logging, searchable colour-coded tags, and responsive cast, crew,
  artwork, and video galleries.
  ([#281](https://github.com/helmerzNL/DiscVault/pull/281),
  [#282](https://github.com/helmerzNL/DiscVault/pull/282),
  [#284](https://github.com/helmerzNL/DiscVault/pull/284))
- **Optional Legacy authentication.** Administrators can enable Argon2id
  username/password authentication with TOTP, temporary passwords, unified
  single-use recovery codes, per-user passkey policy, Owner bootstrap, and
  expanded Users & Roles management.
  ([#297](https://github.com/helmerzNL/DiscVault/pull/297),
  [#298](https://github.com/helmerzNL/DiscVault/pull/298),
  [#302](https://github.com/helmerzNL/DiscVault/pull/302))
- **MovieVault distribution 3 and 4.** DiscVault adds strict,
  checksum-verified local-index synchronization, exact box-set editions, richer
  release metadata, and secure background caching of MovieVault posters.
  ([#287](https://github.com/helmerzNL/DiscVault/pull/287),
  [#295](https://github.com/helmerzNL/DiscVault/pull/295),
  [#301](https://github.com/helmerzNL/DiscVault/pull/301))
- **Member Groups and owned containers.** Group-shared movies now expose their
  containing box sets and vaults. Group owners can rename eligible groups,
  remove empty groups, and convert owned box sets into vaults or vaults into
  box sets without losing container data.
  ([#305](https://github.com/helmerzNL/DiscVault/pull/305))

### Movie details, artwork, and metadata

- Personal Lists now provide dedicated **Log rewatch** and **Watchlist**
  actions, quick date choices, a native date picker, and a searchable tag
  picker.
- Cast and crew use responsive portrait cards with age-at-release information,
  while media galleries use localized **More** controls and accessible artwork
  actions.
- Hidden posters and backdrops can be reviewed, restored, or permanently
  deleted. Hidden artwork remains excluded from primary-artwork selection and
  metadata refreshes, and its state is retained in backups.
  ([#304](https://github.com/helmerzNL/DiscVault/pull/304))
- Country flags and age classifications now appear consistently in the movie
  header. Metadata refreshes update unlocked ratings while preserving manually
  locked values.
  ([#291](https://github.com/helmerzNL/DiscVault/pull/291),
  [#292](https://github.com/helmerzNL/DiscVault/pull/292))
- Audio and subtitle editing fields have been expanded for multi-line values.
  ([#294](https://github.com/helmerzNL/DiscVault/pull/294))
- Metadata comparison and artwork diagnostics are now restricted to Debug
  mode. ([#283](https://github.com/helmerzNL/DiscVault/pull/283))

### MovieVault and privacy

- Distribution 3 introduces atomic full generations, transactional deltas,
  strict digest validation, nullable studio/distributor/runtime metadata, and
  ordered exact-edition box sets.
- Distribution 4 adds secure poster metadata and bounded background caching.
  Downloads are validated for media type, size, checksum, and decoded image
  dimensions before activation.
- Cached MovieVault posters are served only through authenticated local
  DiscVault URLs with private cache semantics; MovieVault origins and
  credentials are never exposed to clients.
- Failed poster replacements retain the previous valid image and report an
  explicit degraded, pending, or error state.
- Person profiles and filmographies now come exclusively from TMDb. Stored
  MovieVault-derived person data is migrated or rebuilt where appropriate.
  ([#296](https://github.com/helmerzNL/DiscVault/pull/296))

### Member Groups and container ownership

- Members can discover the box sets and vaults that contain movies shared with
  their Member Groups.
- Member Group owners can rename groups and delete a group once no other
  members remain.
- Containers now have durable ownership across creation, synchronization,
  import, background workers, backup, and restore.
- Authorized owners can convert a box set into a vault, or a vault into a box
  set, from the existing editor while preserving movies, ordering, artwork,
  metadata, identifiers, barcode, location, cover, public identity, and
  collection references.

### Additional improvements

- Accessibility, keyboard focus restoration, compact layouts, and responsive
  controls have been improved throughout the Library and movie-detail
  interfaces.
- New and changed user-facing flows include complete translations across all
  29 supported locales.
- Backup and restore coverage now includes Legacy authentication, hidden
  artwork, and container ownership.

### Upgrade notes

- MovieVault distribution 4 remains inactive until the installed
  `movievault_v2` plugin explicitly advertises distribution-4 compatibility.
- Enabling Legacy authentication requires the corresponding deployment and
  environment configuration; existing passkey authentication remains
  supported.
- Database migrations run automatically during the normal upgrade process.

## 26.4.66 - Authenticated MovieVault poster media

- Serves cached MovieVault v2 posters only through a dedicated authenticated
  local route with private browser-cache semantics.
- Blocks the same poster assets on the generic public media route while
  preserving existing intentionally public non-MovieVault media assets.

## 26.4.62 - MovieVault distribution-4 poster caching CI fix

- Fixes the PostgreSQL smoke-test poster-cache tests that assumed the default
  legacy data directory was writable in CI; they now isolate an explicit
  temporary `DISCVAULT_LEGACY_DATA_DIR` like the existing cleanup tests, so the
  cache job can atomically activate the fetched poster bytes and the local
  media-asset route serves them with a `200` instead of a storage-error
  `failed` outcome.
- Fixes a poster-cache-failure regression test that unintentionally sent the
  correct checksum for the fetched bytes, which no longer exercises the
  intended checksum-mismatch/corrupted-download failure path.

## 26.4.61 - MovieVault distribution-4 poster caching

- Consumes the strict `distribution-4` contract (required-nullable `poster` on
  release and box-set upserts) while retaining full `distribution-2`/`-3`
  compatibility and exact box-set member editions.
- Fetches every changed selected poster anonymously and in the background as
  part of index sync only, keyed by asset ID, variant, and checksum; item
  views never trigger a remote fetch.
- Validates HTTP status, exact media type, byte limit, SHA-256, and decoded
  image dimensions before atomically activating a cached poster; failed
  replacements keep the prior valid poster and report an explicit
  degraded/pending/error cache status instead of leaving partial bytes.
- Serves cached posters only through an authenticated, checksum/ETag-backed
  local DiscVault route; release, box-set, search, add, detail, and PWA
  responses map to that local URL, never to a MovieVault origin, token, or
  raw remote payload.
- Adds bounded cleanup for unreferenced cached poster bytes and rows on the
  existing retention convention, independent of collection membership and
  without retaining MovieVault request telemetry.

## 26.4.57 - MovieVault distribution-3 local index

- Negotiates `distribution-3` only with compatible `movievault_v2` plugins while
  retaining strict `distribution-2` support for existing installations.
- Verifies both `X-Content-SHA256` and `Content-Digest`, then applies v3 full
  generations atomically and deltas transactionally.
- Maps nullable studio, distributor, and runtime metadata while preserving
  hash-only lookup indexes and exact ordered box-set editions.
- Ships the deterministic `movievault_v2` 1.1.0 feature package with an explicit
  supported contract range.

## 26.4.50 - MovieVault v2 preview lookup dispatch

- Executes the standalone `movievault_v2` source during preview barcode, title,
  and box-set identification through its synchronized local index.
- Keeps legacy MovieVault authentication, contribution, and metadata receiver
  bridges isolated from the MovieVault v2 adapter.

## 26.4.49 - MovieVault v2 full-snapshot revision ordering

- Accepts deterministic full snapshots ordered by entity identity while still
  requiring every revision to be unique and within the manifest revision.
- Keeps delta artifacts strictly revision-ordered and preserves full-snapshot
  entity, operation, digest, and atomic rollback validation.

## 26.4.48 - MovieVault v2 full-sync checksum correction

- Validates full distribution artifacts against their own
  `X-Content-SHA256` response digest instead of comparing their bytes with the
  separate approved lookup-hash dataset checksum.
- Preserves strict manifest, cursor, revision, atomic generation, and rollback
  validation for initial and cursor-recovery full synchronization.

## 26.4.44 - Automatic MovieVault v2 configuration

- Resolves and displays typed plugin manifest defaults while preserving every
  explicit operator override across registry refreshes and upgrades.
- Repairs the plugin configuration form with native boolean/number controls and
  adjacent saving, success, and error feedback.
- Validates the MovieVault v2 root origin and queues one duplicate-safe initial
  index synchronization when the disabled plugin is first enabled.

## 26.4.43 - MovieVault v2 anonymous local index bridge

- Adds a checksum-verified `distribution-2` synchronizer with atomic full
  generations, transactional deltas, tombstones, cursor recovery, and
  privacy-safe failure codes.
- Stores only SHA-256 lookup hashes and MovieVault-owned release/box-set facts,
  including ordered exact-edition members.
- Exposes narrow local lookup, sync, bucket-fallback, and status callbacks to
  the separately installable `movievault_v2` metadata-source plugin.
- Adds duplicate-safe manual and scheduled `sync_index` jobs while leaving
  `movievault_26` and its attributed contribution transport unchanged.

## 26.4.33 — Wishlist price deal alerts, responsive Library, and mobile poster carousel

This release promotes the full `release/v26-beta` train to production, bumping DiscVault
from `26.4.24` to `26.4.33`. The headline addition is **wishlist price deal alerts**: a
plugin-based price-provider framework that monitors shop prices for items on your wishlist
and surfaces price trends and deals. It also ships a fluidly responsive Library grid and
groundwork for a mobile poster carousel, plus localization coverage across every supported
locale.

Promoted via [#251](https://github.com/helmerzNL/DiscVault/pull/251) (merge commit
`c701648`).

### What's new

- **Wishlist price deal alerts — plugin-based price providers.**
  Wishlist shops can now be monitored through a plugin-based price-provider framework, so
  DiscVault tracks prices and highlights deals for items you want.
  ([#239](https://github.com/helmerzNL/DiscVault/pull/239), commit `caf4521`)

- **Admin price-provider registry tab.**
  A dedicated price-provider tab was added to the admin plugin registry for managing and
  configuring the available providers.
  ([#241](https://github.com/helmerzNL/DiscVault/pull/241), commit `3f3f241`)

- **Zavvi & Arrow provider plugins (plus bundled Keepa and PriceAPI).**
  New price-provider plugins for Zavvi and Arrow ship out of the box, alongside the bundled
  Keepa and PriceAPI providers.
  ([#244](https://github.com/helmerzNL/DiscVault/pull/244), commit `3c3ca6a`)

- **Richer wishlist price-monitor cards.**
  The wishlist price-monitor cards were enhanced to present price data more clearly, and the
  price-trend selector now always appears when trend data exists.
  ([#246](https://github.com/helmerzNL/DiscVault/pull/246), commits `76b8c2e`, `3872c86`)

- **Provider dropdown and shop-URL autodetect.**
  Adding a wishlist shop now offers a provider dropdown and automatically detects the
  provider from a pasted shop URL.
  ([#250](https://github.com/helmerzNL/DiscVault/pull/250), commit `67f4a45`)

- **Mobile 3-poster carousel groundwork.**
  Introduces the groundwork for a three-poster carousel layout on mobile.
  ([#243](https://github.com/helmerzNL/DiscVault/pull/243), commit `0dd89cc`)

- **Responsive Library poster grid.**
  The Library poster grid is now fluidly responsive, mirroring the Discover grid so posters
  scale smoothly across phone, tablet, and desktop widths instead of being locked to a fixed
  column count.
  ([#248](https://github.com/helmerzNL/DiscVault/pull/248), commit `1303d85`)

- **Expanded localization coverage.**
  Translations for the new wishlist, price-provider, and Library flows were added and aligned
  across all supported locales.

### Notable stability fixes included in this train

- Manual price sweeps now run immediately and refresh notifications rather than waiting for
  the next scheduled cycle.
  ([#246](https://github.com/helmerzNL/DiscVault/pull/246), commit `f8477f3`)
- Adding a wishlist shop is idempotent for duplicate URLs, preventing duplicate entries.
  ([#245](https://github.com/helmerzNL/DiscVault/pull/245), commit `a6cb397`)
- Price-sweep audit events now use the background-job UUID for correct correlation.
  ([#245](https://github.com/helmerzNL/DiscVault/pull/245), commit `cb05ff3`)
- Newly bundled default plugins are installed after initialization, so fresh instances pick
  up the shipped providers.
  ([#242](https://github.com/helmerzNL/DiscVault/pull/242), commit `d04477c`)

## 26.3.0 — Location deep links, QR flow upgrades, and navigation cleanup

This release promotes a full set of DiscVault Next UX improvements around
locations and sharing. It introduces dedicated location detail navigation,
improves QR workflows for deep links, and tightens profile action structure.

### What's new

- **Dedicated location detail page flow.**
  Navigation now supports a clearer location detail experience, making it
  easier to move from collection context into location-specific views.

- **Location QR deep-link bridge.**
  Location QR generation now maps directly to deep-link targets so shared QR
  scans open the intended destination in the Next app flow.

- **QR rendering and interaction polish.**
  QR output was improved to square PNG rendering and related controls were
  refined for better reliability and consistency across the location pages.

- **Profile action menu cleanup.**
  Statistics actions are now kept in the Profile action scope to keep top-level
  navigation cleaner and more predictable.

- **Expanded localization coverage.**
  Translation coverage was extended and aligned across all supported locales
  for the new location and QR user flows.

### Notable stability fixes included in this train

- Removed a duplicate QR URL declaration that could cause a blank page.
- Added a regression guard for an app shell JavaScript syntax issue.

## 26.2.9 — Passkey onboarding error handling

This release makes passkey setup on DiscVault Next much clearer when a server
is reached over an address that the browser refuses to register passkeys for.
Instead of a cryptic *"Passkeys are not supported"* message, users now get a
plain-language explanation of what went wrong, how to fix it, and a link to the
FAQ.

### What's new

- **Clear guidance for IP-only / missing-UPN setups.**
  When DiscVault Next is opened on a URL that is only an IP address (for example
  `https://192.168.1.10`), browsers block passkey creation. Onboarding now
  detects this and explains that a UPN/hostname must be configured and the app
  opened on that hostname over HTTPS, rather than failing with a generic
  "not supported" error.

- **Friendly handling of Relying Party ID mismatches.**
  The WebAuthn error *"The relying party ID is not a registrable domain suffix
  of, nor equal to the current domain"* is now caught and translated into an
  actionable message: open DiscVault Next on exactly the UPN/hostname that
  matches the passkey configuration (not an IP address), over HTTPS.

- **Clickable FAQ help link.**
  Every passkey error message now points to
  [https://discvault.eu/faq](https://discvault.eu/faq) as a real, clickable link
  that opens in a new tab — no more copy-pasting a URL out of an error string.

- **Fully localized messages.**
  The new passkey guidance messages are translated into all 29 supported
  languages, so users see the help text in their own language instead of an
  English fallback.

### Notes for administrators

If your users hit passkey errors during onboarding:

1. Make sure a UPN/hostname is configured for the DiscVault Next instance.
2. Have users open DiscVault Next on that hostname over **HTTPS** — not on a
   bare IP address.
3. See [https://discvault.eu/faq](https://discvault.eu/faq) for the
   "Passkeys versus passwords" section and troubleshooting steps.
