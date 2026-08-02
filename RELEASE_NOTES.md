# DiscVault Release Notes

## 26.7.40 - A version bump can no longer collide with a parallel pull request

- `bump_version.py` now reads the target branch's `app/VERSION` and raises its
  floor above it, so a bump computed while another pull request was in flight
  can never land on a value the branch already carries.
- It had only ever incremented from the local file. Two pull requests open at
  the same time both bumped `26.7.35` → `26.7.36`; the first merged fine and the
  second merged a value equal to its own base, which the version guard correctly
  rejected. That left the beta channel publishing no image at all until it was
  cleared by hand — twice.
- Build tooling only; nothing in the application changes.

## 26.7.39 - The MovieVault catalog catches up instead of failing forever

- An instance more than one publication behind could never sync again. Its
  catalog stayed frozen at whatever it last managed to fetch, and every retry
  failed the same way, permanently.
- The delta branch of `run_sync()` fetched `/index/delta` **once** and demanded
  that the response already sit on the manifest's head cursor, raising
  `cursor_invalid` without persisting anything. MovieVault v2 serves one
  publication segment per request, so an instance that had fallen behind by more
  than one publish was always handed an intermediate segment — and always
  rejected it, discarding the progress it had just been given.
- The delta branch now walks the cursor chain hop by hop and commits progress at
  each step until it reaches the manifest's current cursor. A lagging instance
  catches up over several hops instead of failing on the first one.
- Verified against a real PostgreSQL 16 database: the full MovieVault v2
  PostgreSQL suite (29 tests) plus the non-PostgreSQL v2 suites (265 tests, 96
  subtests).

## 26.7.38 - The beta image builds again

- The build pipeline pulled BuildKit from `ghcr.io/moby/buildkit`, which
  publishes no `buildx-stable-1` tag, so every build died in two seconds with
  `denied`. It now comes from `mirror.gcr.io`, Google's pull-through cache for
  Docker Hub — the same image, reached without depending on Docker Hub being
  reachable.
- The same cache is configured as a registry mirror inside the builder, so
  `FROM python:3.12-slim` no longer depends on Docker Hub either. That was the
  next step the original outage would have failed at.
- Build pipeline only. No image had been published since 26.7.36, so this is the
  version that carries 26.7.36 through 26.7.38 to anyone running the beta
  channel.

## 26.7.37 - Version bump to clear the build

- Housekeeping. 26.7.36 had merged carrying a version its own base already used,
  which left the version guard red and the image build blocked. No code changes.

## 26.7.36 - Structured subtitles are accepted before MovieVault sends them

- DiscVault now understands subtitles as `{languageCode, subtitleType}` from the
  release-details resolver, not just as a bare list of languages. Structured
  subtitles replace the plain language list when both arrive.
- **This shipped deliberately early, and the ordering is the point.** MovieVault
  publishes the same physical release on two routes that disagree:
  `distribution-4` carries the structured form, `release-technical-1` carries the
  bare list. The resolver's response is validated against a closed key set, so an
  unknown key fails the *entire* response rather than being ignored. The moment
  MovieVault adds `subtitles` to that payload, every barcode falling through to
  the resolver would return `release_details_response_invalid` and scanning would
  break on discvault.eu. A purely additive change on their side, an outage on
  ours — unless the key is accepted first.
- `subtitleType` is treated as an **open** enum, matching the distribution-4
  reader: MovieVault may introduce a variant before this allow-list catches up,
  and dropping a track over an unrecognised value is worse than carrying it. A
  genuinely unreadable shape is still refused.
- Also in this version, and superseded three versions later: a first attempt at
  moving BuildKit off Docker Hub, which pointed at an image that does not exist.
  See 26.7.38.

## 26.7.35 - A rejected version can no longer reach the registry

- The image build now runs the version guard first and refuses to publish if it
  fails. The image carries `app/VERSION` as its reported version, and the two
  checks previously ran independently — so a build whose version the guard had
  already rejected was published anyway, twice reporting a version that belonged
  to different code.
- Build pipeline only; nothing in the application changes.

## 26.7.34 - Edits made in the PWA reach the phones

- A field you edit in the browser now travels over the mobile sync. The push
  direction already accepted every one of these fields, so an edit made *on* a
  phone arrived and stuck; the same edit made in the PWA stayed invisible to
  every client, which looked like the edit had been lost.
- The cause sat on the read side. The sync bootstrap and the delta were two
  hand-maintained SELECT lists with nothing asserting they matched, and they had
  drifted — `release_title` had been added to the delta alone. A field could
  therefore reach a client down one path and never down the other. The two lists
  are now held equal by a test, so a field added to one cannot silently go
  missing from the other.

## 26.7.33 - A scanned disc keeps its cover when the film has none

- A barcode scan that matches a release now falls back to the release's own
  artwork when the film carries none of its own. The scanned candidate is built
  from the film's canonical fields with the release's spec laid over it, and
  artwork was in neither set — so a film without a poster landed in the
  collection with an empty poster slot, even though MovieVault had supplied
  cover art on the release itself. Film artwork still wins wherever it exists,
  so nothing that already had a poster changes.
- Internal only: a report (`app/scripts/prune_landed_branches.py`) and a weekly
  workflow that say which development branches are safe to delete. No effect on
  the application.

## 26.7.32 - A metadata refresh reaches films that carry a MovieVault id

- Refreshing metadata on a film with a `movieVaultId` reached the MovieVault v2
  catalog again. It had been planning a details lookup *instead of* a barcode
  lookup whenever an identifier was present, and that identifier usually cannot
  resolve: a film that arrived through an import carries a locally derived UUID
  that no catalog row will ever match. Both calls are now planned together.
- This failed silently in the worst way — every surface reported success, with
  `resultStatus: "miss"` and "provider returned no usable match" and no error.
  The same edit on a film *without* a `movieVaultId` did arrive, because that
  film still had the barcode path.
- All seven physical formats now receive technical data. Only 4K UHD, Blu-ray
  and DVD were recognised; everything else normalised to an empty format, which
  is read as "no format" and blocks every technical field. An HD DVD, LaserDisc
  or VCD/SVCD could never receive audio tracks, subtitles or video facts, even
  though the collection UI has always offered all seven.
- Two consequences of that worth naming: `HD DVD` is now matched ahead of `DVD`
  (it contains the literal "dvd" token, so the plain-DVD rule used to claim it
  and let DVD release data through onto an HD DVD disc), and `4K UHD + Blu-ray`
  becomes a value of its own rather than collapsing onto 4K.

## 26.7.31 - Audio and subtitle edits reach the mobile apps

- An audio or subtitle edit made in the PWA now reaches the native clients. The
  cause was wider than a missing field: the whole mobile sync surface read and
  wrote the `movies` table in both directions, while this data lives in
  `movie_technical_specs`. There was neither a producer nor a consumer for it —
  `audio_languages`, the key Android has been decoding all along, appeared
  nowhere in the codebase.

## 26.7.30 - The MovieVault catalog syncs again

- 342 of the 359 records on the production feed were being rejected — the entire
  catalog. Eight distribution-4 technical fields (audio tracks, subtitles,
  packaging, video resolution and codecs, HDR formats, aspect ratios, disc
  regions) were treated as required, but the live feed serves records published
  before that work existed and never re-projected since.
- They are now optional. A release is identified by its ids, title and barcodes;
  technical specs are enrichment, and their absence is not a reason to lose the
  film. An absent field decodes to exactly what a record with nothing to report
  already carried, so "not published" and "nothing known" are stored identically.

## 26.7.29 - A regional language tag no longer kills the whole sync

- The MovieVault v2 sync failed with `record_invalid` and nothing further.
  Language codes were the single fatal track field: anything outside a strict
  pattern raised and took the entire release record with it, and with it the
  whole sync. `pt-BR`, `en-US` and `zh-Hans` each destroyed the record.
- Every neighbouring field — codec, channels, immersive format, subtitle type,
  packaging, resolution, video codecs, HDR formats, aspect ratios, disc regions
  — logs an unrecognised value and keeps it. Language codes now behave the same.
- The failure was also undiagnosable, which is why it took a second release to
  find the next one. Rejections now name the record and the offending keys.

## 26.7.28 - MovieVault v2 is the default metadata source

**Two changes here override existing settings. Read the last two bullets.**

- `movievault_v2` now ships enabled and is the highest-priority MovieVault
  metadata source (order 45); `movievault_26` moves to order 55 and now ships
  disabled. A fresh install uses the v2 distribution catalog without an admin
  having to find and enable it first. Order 45 rather than a straight swap:
  metadata sources are sorted by `(order_index, lower(name))`, and on a tie
  "MovieVault 26" sorts ahead of "MovieVault v2".
- The anonymous bucket fallback is now real and always on (bundled
  `movievault_v2` 1.5.0). It was declared as a setting but nothing ever read it,
  so a barcode that the locally synced index did not carry simply missed - which
  is every title MovieVault has not distributed into this instance's index yet.
  A barcode or box-set lookup that misses locally now asks MovieVault's
  hash-keyed bucket index before giving up. Title queries cannot use it: buckets
  are keyed by the hash of the EAN.
- "Allow anonymous bucket fallback" has disappeared from the plugin-settings UI
  and any stored value is removed. Like the MovieVault v2 endpoint, it is now
  enforced by DiscVault rather than switchable; `MOVIEVAULT_V2_BUCKET_FALLBACK=0`
  overrides it out-of-band. Every bucket failure - unreachable origin, malformed
  bucket, incompatible contract - degrades to a miss and never fails the lookup
  that produced it.
- Saving credentials for TMDb, Plex, Jellyfin or Trakt now enables that plugin
  automatically. Storing a TMDb key and then having to hunt for a separate
  toggle was a dead end; configuring one of these integrations is the intent to
  use it. Deliberately one-directional: clearing a key never disables the plugin,
  so a key rotation cannot drop a metadata source mid-rotation. Price providers
  are not included - starting to scrape shops stays a separate, deliberate act.
- Fixed: the periodic MovieVault v2 sync never ran. The scheduler required a
  stored `origin` in plugin settings, but the origin has been enforced
  server-side and stripped from the settings schema since `26.6.x`, so that value
  is empty on every install - leaving the index permanently unsynced. Readiness
  is now decided by the plugin being installed and enabled, and a plugin whose
  config was never saved no longer needs a settings row at all.
- **Existing installs are flipped once.** The migration sets `movievault_v2` to
  enabled at order 45 and `movievault_26` to disabled at order 55, overriding a
  deliberate operator choice if one was made. There is no way to tell a
  deliberate setting from an untouched default, so re-apply your preference after
  upgrading if it differed.
- **Contribution back to MovieVault stops until you re-enable it.**
  `movievault_26` is DiscVault's only `metadata_receiver` - the channel that
  sends barcode updates, container updates and activity summaries back to
  MovieVault. `movievault_v2` is read-only and has none of those capabilities.
  With 26 disabled, contribution silently no-ops. Enable `movievault_26` again in
  App Admin → Plugins if you want to keep contributing; it costs nothing, since
  v2 still outranks it as a metadata source.

## 26.7.27 - The estimated value has a currency, and the PWA can set it

- The PWA can now set a film's estimated value. It had shipped as an API- and
  sync-only field: the server accepted it and both native apps edited it, but
  there was no input for it anywhere in the web UI.
- The amount now carries a currency. It had been a bare number since it was
  introduced, which quietly assumes everyone thinks in one currency; a
  collection bought across borders does not, and the number means nothing
  without saying what it is.
- The currency is nullable rather than defaulting to EUR. Rows already exist
  that carry a value and no currency, and inventing a unit for them would state
  something nobody entered.

## 26.7.26 - Audio tracks and subtitles are editable, not "[object Object]"

- The per-track audio and subtitle editors, with localised language names, are
  present. Beta had received the ingestion half of this work without the rest:
  structured tracks reached storage as objects while the edit screen was still
  the free-text field, which renders an object as `[object Object]`. The data
  had arrived; the view could not show it.
- Not new functionality — two PRs had merged into their stacked base branches
  rather than into beta, which is how the halves came apart.
- Also in this version: a tester guide for a short library count or a freezing
  app, at `docs/troubleshooting/library-count-and-app-freeze.md`.

## 26.7.24 - The library shows every film, and the app stops freezing

- A collection of 228 films showed 200 of them, and after updating the app
  stopped working altogether — first in Firefox, later in Chrome. Neither
  symptom came from the movie cap, which had already been lifted.
- The cause was the PWA service worker. It classified every same-origin `/api/`
  request as data, and the app document and its frontend modules are served from
  `/api/` routes too. On any fetch failure they were answered with a JSON stub
  instead of the page, so the app served itself a fragment of API data where its
  own code should have been.
- **This upgrade needs one manual reset.** The listener that shows the reload
  banner ships *in* this version, so the bundle you are upgrading *from* has
  nobody listening. The new worker activates and purges the old caches silently,
  and an open tab keeps running pre-fix code with no signal that anything
  changed. Reload once by hand after updating; later upgrades announce
  themselves. The troubleshooting guide added in 26.7.26 walks through it.

## 26.7.11 - Synced box-set covers survive and show in the library

- A box set pushed from another client keeps its cover. The container sync
  upsert stored only title, barcode, year and metadata, so the artwork the
  sending client had resolved (MovieVault's own box-set cover) was dropped on
  arrival. The container then had no artwork of its own and fell back to the
  first member film's poster — a different film's cover. Pushed poster and
  backdrop URLs are now registered as container artwork.
- The pushed cover becomes the container's default without ever overriding a
  chosen one: it is attached as a selectable option and only promoted to
  primary when the container has no primary artwork of that kind yet, so a
  cover picked or uploaded in DiscVault always wins. Registering artwork runs
  in its own savepoint, so a container still syncs if its cover cannot be
  stored.
- The library list now shows a container's own primary artwork. It previously
  selected no artwork at all and relied on a metadata URL, so a cover living in
  `entity_media` — synced, scanned, or uploaded — appeared on the detail page
  but left the library card blank.

## 26.7.10 - MovieVault covers reach the PWA

- A scanned disc now shows the MovieVault cover in the PWA, for box-sets and
  individual films alike. The cover a scan produces arrives on MovieVault's
  release-details resolver, which DiscVault registered but never called, so the
  object carrying the artwork was never requested. The `movievault_v2` plugin
  now resolves the scanned barcode and uses that poster whenever the synced
  catalog record does not carry one yet (bundled `movievault_v2` 1.3.0). A
  box-set uses the set's own cover in preference to a single release inside it,
  and set members carry no artwork of their own.
- The resolver's poster is now accepted. Its reference publishes only a `path`
  (no checksum, which only the bulk catalog provides) and types `attestation`
  and `license` as nested objects, all of which the strict bulk-sync parser
  rejected outright. Absent or unreadable claims are recorded as absent so a
  supplementary artwork field never costs a whole record, while a readable but
  unapproved attestation or licence is still refused.
- A poster without a checksum is served from MovieVault's stable anonymous
  asset URL instead of being copied into the local artwork cache: bytes that
  cannot be verified must never be stored, so nothing unverifiable is written
  to disk. Checksummed catalog posters keep using the verified local cache.

## 26.7.9 - MovieVault owns the physical release's artwork

- Scanning a barcode now shows the MovieVault cover in the PWA, for individual
  films and box-sets alike. The metadata policy treated artwork as
  enrichment-only, so a MovieVault result had its poster stripped on three
  paths at once: `poster_url` was filtered out of the movie and metadata
  updates, `mediaUpdates` (which persists artwork) was cleared, and the
  candidate the user picks from was sanitized down to identification fields
  without any poster. The scan card therefore fell back to a letter
  placeholder even though MovieVault had supplied a cover.
- MovieVault identity sources may now own artwork — a physical release has its
  own front cover, which the identity source is the authority on. Poster and
  backdrop fields survive on the movie, in `mediaUpdates`, and on the
  candidate. Every other enrichment answer (plot, cast and crew, runtime,
  ratings, trailers) still belongs to the enrichment provider, and plugins that
  are not identity sources remain blocked from supplying artwork.

## 26.7.8 - MovieVault v2 covers in the PWA

- Barcode scans now show the MovieVault v2 cover in the PWA for both individual
  films and box-sets, matching the native iOS behavior. The `movievault_v2`
  plugin previously dropped the poster while building its release and box-set
  candidates, so the cover never reached the client. The plugin now forwards
  the localized, DiscVault-served poster URL (bundled as `movievault_v2` 1.2.0,
  auto-upgraded in place from 1.1.0).
- The PWA image guard (`usableImage`) now recognizes the
  `/api/next/movievault-v2/posters/` route in addition to `/api/next/media/`,
  so locally served MovieVault v2 covers render instead of falling back to a
  placeholder.

## 26.6.18 - Exact TMDb enrichment for MovieVault barcode hits

- TMDb preview enrichment now requires an exact normalized title match and,
  when available, the requested release year. DiscVault no longer attaches
  metadata or artwork from a similarly named film when MovieVault supplied the
  correct barcode identity.

## 26.6.17 - MovieVault barcode fallback without external IDs

- MovieVault v2 release-detail hits no longer require a TMDb or IMDb identifier
  when a valid film title and physical-release result are present. Strict link,
  field, and provider-content validation remains enforced.
- This fixes valid unreviewed Blu-ray.com results appearing as an unrecognized
  barcode in the DiscVault Import Center.

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
