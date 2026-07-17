# DiscVault Release Notes

## 26.4.53 - MovieVault distribution-3 local index

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
