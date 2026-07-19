# DiscVault Server — Sync-idempotentie & dedup (contractdocument)

> **Status:** dit is het **leidende contractdocument** voor béíde kanten (server + iOS-app).
> Wijzigingen alleen in overleg, en dan hier doorvoeren — niet stilzwijgend in code.
> Als de app-repo een `docs/sync-dedup-server-ticket.md` oplevert (Fase 3-ticket uit Claude
> Code), worden de details daarvan hierin gemerged en afwijkingen gemeld.

Context: de iOS-app dupliceert films bij first-connect-sync. Fase 0-onderzoek in de app-repo
(`docs/sync-dedup-onderzoek.md`, branch `claude/sync-dedup-fix`) heeft de hoofdoorzaak
bevestigd: de client pusht anonieme creates vóór de pull/match, en **de server dedupliceert
niet** — geen client-identifier, geen uniqueness-check op barcode of TMDB-id, geen "bestond
al"-response, geen tombstones. De client-kant wordt parallel opgepakt; dit document legt het
gedeelde contract vast.

## Serverarchitectuur (geverifieerd — bepaalt de contractvorm)

- Backend: Python/Flask + PostgreSQL; migraties in `app/backend/migrations_next/`.
- De iOS-sync loopt via het **batch-endpoint** `POST /api/next/sync/mutations`. Dat endpoint
  neemt een lijst mutaties en geeft **HTTP 200** terug met een `results[]`-array; per item een
  `status` + entiteitsvelden. Er is **geen** losse per-create HTTP-endpoint met 201/200.
- `movies` is één **gedeelde catalogus** (single-collection): `owner_id` is nullable en wordt
  in het sync-pad nergens gezet. De per-gebruiker/collectie-scoping uit de oorspronkelijke
  opdracht wordt daarom vertaald naar single-collection scoping (unieke index op `client_id`
  alleen, i.p.v. `(owner_id, client_id)`).
- Bestaande machinerie: `clientEntityId` (per mutatie, gemapt via `client_id_mappings`) en
  `clientMutationId` (wegwerp, idempotency-replay). De persistente `clientId` uit dit contract
  is een **nieuwe, expliciete kolom** op `movies` die naast de legacy-mapping bestaat.

---

## Deel 1 — Idempotente create (kern van de fix)

### 1.1 Stabiele client-identifier

- De client stuurt bij elke movie-create een **persistente `clientId`** (UUID) mee:
  aangemaakt bij het lokale record, onveranderlijk, hergebruikt bij élke retry.
  (Nu: payload-`id` = `remoteID`, dus leeg bij create; `clientMutationId` is
  wegwerp-per-poging — die semantiek vervalt.)
- Server: kolom `client_id` op movies (nullable voor bestaande records),
  **unieke index** op `client_id` (single-collection; partieel waar `client_id IS NOT NULL`).

### 1.2 Gedrag van de create-endpoint

Bij een create-mutatie past de server deze identiteitsladder toe, in strikte volgorde, binnen
de (gedeelde) collectie:

1. `clientId` — bestaat er al een record met deze clientId? → **bestaand record retourneren.**
2. Barcode/EAN — exacte match (genormaliseerd: alleen cijfers, leading zeros behouden).
3. TMDB-id + fysiek formaat + editie — alle drie moeten matchen.
4. Genormaliseerde titel + jaar — lowercase, interpunctie/lidwoorden gestript, diacrieten
   gevouwen. Alleen als laatste redmiddel én alleen bij first-connect-adoptie (zie 1.4); niet
   bij reguliere creates.

**Transport & responsesemantiek (hard onderdeel van het contract):**

De semantiek van "nieuw aangemaakt" vs. "bestond al" wordt niet via HTTP-status uitgedrukt maar
**op itemniveau in de batch-`results[]`** — het batch-endpoint blijft HTTP 200. Elk
`results[]`-item van een movie-upsert draagt:

- `created` — `true` als een nieuw record is aangemaakt, `false` als een bestaand record via de
  ladder is gematcht.
- `matchedBy` — alleen bij `created:false`; enum `"clientId" | "barcode" | "tmdbEdition" |
  "titleYear"`. (`"tmdbEdition"` = trede 3, TMDB-id + formaat + editie.) In het create-pad is
  de `titleYear`-trede bewust **uit** (adoptie-only, zie 1.4); daar zijn de enige waarden
  `"clientId" | "barcode" | "tmdbEdition"`.
- `recordClientId` — de persistente per-record `clientId` wordt teruggeecho'd zodat de client de
  response aan het lokale record kan koppelen. (Let op: dit is een ándere sleutel dan de
  top-level batch-`clientId`, die het device/installatie-id is — vandaar `recordClientId` in de
  itemrespons om verwarring te voorkomen.)
- `entity` (+ `entityId`) — het **canonieke server-record**, zodat de client bij een match het
  bestaande server-`id` als `remoteID` adopteert. Bij een nieuwe create idem: het net
  aangemaakte record.
- `tombstoned: true` — extra vlag wanneer de ladder/H4-guard een **getombstoned** record
  teruggeeft (delete-wins bij re-push, zie Deel 2), zodat de client leert dat het record weg is.

De client adopteert in beide gevallen het geretourneerde server-`id` als `remoteID`.

**Idempotentie & batch-dedup:**

- Idempotentie geldt **per mutatie-item** (via de bestaande idempotency-replay op
  `clientMutationId`).
- De batch-handler dedupliceert **binnen één request**: dezelfde `clientId` die twee keer in
  hetzelfde batch voorkomt, lost op naar **één** record; de tweede voorkomst krijgt
  `created:false`.

> Veldnamen (`created`, `matchedBy`, `recordClientId`, `entity`, `tombstoned`) zijn
> geïmplementeerd zoals hierboven; zie de Implementatiestatus onderaan voor de definitieve
> payload-veldnamen (input) en responsvelden (output).

### 1.3 Over-merge-bescherming (kritiek voor een verzamelaarsapp)

- Formaat en editie zijn **onderscheidend**: de DVD en de 4K UHD van dezelfde film zijn terecht
  twee records. Trede 3 en 4 mogen nooit over formaatgrenzen heen matchen; bij trede 4 geldt:
  ontbreekt het formaat aan één kant → géén match, gewoon aanmaken.
- Zelfde barcode nogmaals aangemaakt (tweede fysiek exemplaar van dezelfde disc): match
  retourneren (`created:false`, `matchedBy:"barcode"`), tenzij de client expliciet
  `"duplicateCopy": true` meestuurt — dan bewust een tweede record aanmaken. Silent duplicates
  zijn erger dan een expliciete vraag.

### 1.4 Adoptie-endpoint voor first connect (optioneel maar aanbevolen)

Een batch-endpoint (`POST /api/next/sync/reconcile`) waarmee de client bij first connect zijn
volledige lokale lijst (clientId + barcode + tmdbId + formaat + editie + titel+jaar) in één keer
aanbiedt en per item terugkrijgt: `matched` (met server-id) of `unknown`. Scheelt N losse
creates en maakt trede 4 beheersbaar (alleen hier actief).

## Deel 2 — Tombstones & deletions

- Soft delete: `deleted_at` op movies (en box sets); harde delete vervalt in de sync-API.
- De pull/delta-endpoint levert deletions sinds de sync-cursor mee, zodat de client lokaal kan
  opruimen en CloudKit-terugkeer (H4 uit het onderzoek) wordt afgevangen: een re-import van een
  getombstoned record wordt server-side geweigerd of opnieuw als deleted gemarkeerd, afhankelijk
  van `deleted_at` vs. client-timestamp.
- Retentie: tombstones minimaal 90 dagen bewaren (langer dan de langste realistische
  offline-periode van een device).

## Deel 3 — Opschonen bestaande duplicaten (server-side)

Eenmalig merge-script, want de bestaande duplicaten van barcode-films staan juist op de server:

1. **Dry-run eerst**: detecteer duplicaatgroepen via dezelfde ladder (barcode → tmdb+formaat+
   editie → titel+jaar-binnen-zelfde-formaat) en rapporteer aantallen + voorbeelden. Geen
   mutaties tot akkoord.
2. Merge-regel: winnaar = record met de meeste user-data (eigen artwork, locked fields, watch
   history, notities); relaties van verliezers omhangen naar de winnaar.
3. Verliezers krijgen een **tombstone** (Deel 2), geen harde delete — anders pusht een client met
   een oude kopie ze terug.
4. Backfill: winnaars zonder `client_id` laten staan (wordt gevuld zodra een client adopteert);
   niets genereren server-side, anders botst de unieke index met de echte client-UUID's.

## Deel 4 — Uitrolvolgorde & compatibiliteit

1. Serverwijzigingen zijn **backward-compatible** (clientId nullable, oude clients blijven werken)
   en gaan éérst live.
2. Daarna pas de app-release die op idempotentie leunt (client Fase 3).
3. Merge-script (Deel 3) draait ná de serverdeploy maar vóór de app-release, zodat nieuwe clients
   tegen een schone dataset adopteren.
4. API-versie of feature-flag meesturen in de sync-handshake, zodat de client weet of de server
   idempotentie ondersteunt (fallback: het huidige voorzichtige client-gedrag).

## Definition of done (server)

- Tests: dubbele create met zelfde clientId → één record, tweede call `created:false`; retry na
  gesimuleerde timeout → geen duplicaat; barcode-create van bestaand record → `matchedBy:
  "barcode"`; DVD + 4K van dezelfde film → twee records (anti-over-merge); delete → tombstone in
  delta-feed; re-push van getombstoned record → geen wederopstanding.
- Dry-run-rapport van het merge-script gedeeld en akkoord bevonden vóór uitvoering.
- Dit document bijgewerkt met de definitieve endpoint-paden en payload-velden zoals
  geïmplementeerd.

---

## Implementatiestatus (server) — definitief zoals geïmplementeerd

Serverwijzigingen zijn backward-compatible en live-baar vóór de app-release (Deel 4).
Alle nieuwe kolommen zijn nullable; oude clients blijven werken.

### Endpoints (definitief)

| Doel | Methode + pad |
|---|---|
| Batch-mutaties (creates/updates/deletes) | `POST /api/next/sync/mutations` |
| Delta/pull (changes sinds cursor, incl. deletions) | `GET /api/next/sync/delta?since=<rev>&limit=<n>` |
| First-connect adoptie (read-only match) | `POST /api/next/sync/reconcile` |
| Bootstrap (volledige snapshot, tombstones uitgesloten) | `GET /api/next/sync/bootstrap` |

### Payload-velden (input) — movie-upsert in `mutations[]`

Elke variant wordt geaccepteerd (camelCase + snake_case), eerste niet-lege wint:

| Concept | Geaccepteerde sleutels |
|---|---|
| Persistente per-record clientId (UUID) | `clientId` / `client_id` (in `payload`) |
| TMDB-id | `tmdbId` / `tmdb_id` / `tmdbID` |
| Bewuste tweede kopie | `duplicateCopy` / `duplicate_copy` (bool) |
| Client-edittijd (H4 resurrection-beslissing) | `updatedAt` / `updated_at` / `clientUpdatedAt` |

> De top-level batch-`clientId` (request-body, niet payload) blijft het **device/installatie-id**
> en is losstaand van de per-record `clientId` in de payload.

### Responsvelden (output) — per `results[]`-item van een movie-upsert

`status`, `entityType`, `operation`, `entityId`, `clientEntityId`, `revision`, `entity`,
`clientMutationId`, plus dedup-specifiek: `created` (bool), `matchedBy`
(`clientId|barcode|tmdbEdition|titleYear`, alleen bij `created:false`), `recordClientId`
(echo van de per-record clientId), en `tombstoned:true` wanneer een getombstoned record wordt
teruggegeven (delete-wins). Dezelfde `created`/`matchedBy`/`recordClientId` staan ook in de
delta-`payload` van de emitted `sync_change`.

### Reconcile (`/api/next/sync/reconcile`)

Request: `{ "clientId": "<device>", "items": [ { clientId, barcode, tmdbId, format, edition,
title, year } ] }` (max 1000 items). Response `results[]` per item: `{ clientId, status:
"matched"|"unknown"|"invalid", matched: bool, entityId?, matchedBy? }`. Dit is het **enige** pad
waar trede 4 (titel+jaar) actief is.

### Checklist

- [x] **P1** Migratie `045_sync_dedup.sql`: `movies.client_id text` + partiële unieke index
      `uq_movies_client_id` (waar `client_id IS NOT NULL`); `deleted_at timestamptz` op `movies`
      én box-set containers + partiële live-indexen; `idx_movies_titleyear_live`.
- [x] **P2** Identiteitsladder in `apply_movie_upsert` (`match_existing_movie`): clientId →
      barcode → tmdb+formaat+editie; `created`/`matchedBy`/`recordClientId` in `results[]`;
      over-merge-bescherming + `duplicateCopy`; batch-lokale clientId-dedup
      (`SyncBatchContext.claimed_client_ids`). Ladder draait alléén in het batch-pad voor echt
      nieuwe records (box-set-import ongemoeid).
- [x] **P3** Soft delete (`apply_movie_delete`, `apply_container_delete` zetten `deleted_at`);
      tombstones uit `bootstrap` gefilterd, wel in `delta`; H4 re-push-afweer
      (`find_tombstoned_movie_by_identity` + delete-wins/resurrect op client-edittijd);
      retentie ≥ 90 dagen (Deel 2).
- [x] **P4** `POST /api/next/sync/reconcile` (adoptie, read-only, titel+jaar-trede alleen hier).
- [x] **P5** Merge-script `app/scripts/sync_dedup_merge.py` (dry-run default, `--execute`
      guarded). Detectie via dezelfde ladder; winnaar = meeste user-data (tie → oudste
      `created_at`); relaties omgehangen; verliezers krijgen tombstone; geen client_id-backfill.
- [x] **P6** Tests (`test_next_sync.py` DoD-suite + `app/scripts/tests/test_sync_dedup_merge.py`)
      + versie-bump `app/VERSION`.
