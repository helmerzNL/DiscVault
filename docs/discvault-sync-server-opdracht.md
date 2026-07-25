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

- De client stuurt bij elke movie-create een **persistente `payload.client_id`** (UUID) mee:
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
4. Genormaliseerde titel + jaar — lowercase, interpunctie gestript, diacrieten gevouwen, en
   alleen een leidend Engels lidwoord `"the"` gestript (bewust conservatief; geen generieke
   meertalige artikelstrip omdat titels als `"Die Hard"` en `"De Aanslag"` anders foutief
   zouden matchen). Alleen als laatste redmiddel én alleen bij first-connect-adoptie (zie 1.4);
   niet bij reguliere creates.

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
- `deleted: true` + `deletedAt: "<RFC3339 timestamp>"` — het definitieve deleted-signaal
  wanneer de ladder/H4-guard een **getombstoned** record teruggeeft (delete-wins bij re-push,
  zie Deel 2). `tombstoned: true` blijft voorlopig als backward-compatible alias; nieuwe clients
  sturen uitsluitend op `deleted === true`.

De client adopteert in beide gevallen het geretourneerde server-`id` als `remoteID`.

**Idempotentie & batch-dedup:**

- Idempotentie geldt **per mutatie-item** (via de bestaande idempotency-replay op
  `clientMutationId`).
- De batch-handler dedupliceert **binnen één request**: dezelfde `clientId` die twee keer in
  hetzelfde batch voorkomt, lost op naar **één** record; de tweede voorkomst krijgt
  `created:false`.

> Veldnamen (`created`, `matchedBy`, `recordClientId`, `entity`, `deleted`, `deletedAt`,
> `tombstoned`) zijn
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
- Een expliciete update op een bekend getombstoned `entityId` is altijd delete-wins en geeft het
  deleted-signaal terug; die update maakt het record niet opnieuw live. Alleen een create-intent
  zonder `entityId` kan volgens de bestaande H4-regel herleven wanneer `payload.updated_at`
  aantoonbaar later is dan `deleted_at`.

## Deel 3 — Opschonen bestaande duplicaten (server-side)

Eenmalig merge-script, want de bestaande duplicaten van barcode-films staan juist op de server:

1. **Dry-run eerst**: detecteer duplicaatgroepen via dezelfde ladder (barcode → tmdb+formaat+
   editie → titel+jaar-binnen-zelfde-formaat) en rapporteer aantallen + voorbeelden. Zet
   bovenaan elk rapport: `script_commit`, `target_database`, `backend_version`. Geen mutaties
   tot akkoord.
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

Definitief contract (geen dual-key fallback meer):

| Concept | Definitieve sleutel |
|---|---|
| Persistente per-record clientId (UUID) | `payload.client_id` |
| TMDB-id voor trede 3 | `payload.metadata.tmdb_id` |
| Bewuste tweede kopie | `payload.duplicate_copy` (bool) |
| Client-edittijd (H4 resurrection-beslissing) | `payload.updated_at` |

> De top-level batch-`clientId` (request-body, niet payload) blijft het **device/installatie-id**
> en is losstaand van de per-record `payload.client_id`.
>
> **Naamgevingswaarschuwing:** `clientId` = device/installatie op de batch; `client_id` =
> persistente recordidentiteit in de mutation-payload; `recordClientId` = canonieke
> recordidentiteit in de itemresponse. Deze drie namen zijn niet uitwisselbaar.

### Responsvelden (output) — per `results[]`-item van een movie-upsert

`status`, `entityType`, `operation`, `entityId`, `clientEntityId`, `revision`, `entity`,
`clientMutationId`, plus dedup-specifiek: `created` (bool), `matchedBy`
(`clientId|barcode|tmdbEdition|titleYear`, alleen bij `created:false`) en `recordClientId`
(de canonieke persistente recordidentiteit).

Bij delete-wins komen exact deze extra velden mee:

- `deleted: true` — het normatieve signaal; ontbreekt bij een live response (nooit
  `deleted:false`).
- `deletedAt: "<RFC3339 timestamp>"` — de server-tombstonetijd.
- `tombstoned: true` — tijdelijke compatibility-alias voor reeds uitgebrachte clients.
- `operation: "upsert"` blijft staan, omdat dit het antwoord op de geweigerde upsert-intent is.
- `created:false`; `matchedBy:"clientId"|"barcode"` bij identity-match en `matchedBy:null` bij
  een expliciete update van het getombstonede `entityId`.
- `entity` bevat het canonieke serverrecord inclusief `deleted_at`; de client verwijdert zijn
  lokale record en adopteert het geretourneerde `entityId` niet als live item.

Dezelfde `created`/`matchedBy`/`recordClientId` staan ook in de delta-`payload` van een normale
upsert-change. Een tombstone zelf verschijnt als `operation:"delete"` in de delta-feed.

### Exact batchvoorbeeld — create en tombstone

Request:

```json
{
  "clientId": "device-installation-a",
  "mutations": [
    {
      "clientMutationId": "mutation-001",
      "clientEntityId": "local-movie-001",
      "entityType": "movie",
      "operation": "upsert",
      "payload": {
        "client_id": "record-6b159c32",
        "title": "The Matrix",
        "year": "1999",
        "format": "4K_UHD",
        "barcode": "5051890000000",
        "metadata": {"tmdb_id": "603"},
        "duplicate_copy": false,
        "updated_at": "2026-07-24T18:00:00Z"
      }
    }
  ]
}
```

Live match:

```json
{
  "status": "ok",
  "results": [
    {
      "clientMutationId": "mutation-001",
      "status": "applied",
      "entityType": "movie",
      "operation": "upsert",
      "entityId": "2f89df4f-0d24-4e86-94ee-921b4de61f23",
      "clientEntityId": "local-movie-001",
      "revision": 411,
      "created": false,
      "matchedBy": "clientId",
      "recordClientId": "record-6b159c32",
      "entity": {"id": "2f89df4f-0d24-4e86-94ee-921b4de61f23", "deleted_at": null}
    }
  ]
}
```

Delete-wins match (hetzelfde resultaat geldt bij een nieuw top-level device-`clientId`):

```json
{
  "status": "ok",
  "results": [
    {
      "clientMutationId": "mutation-001",
      "status": "applied",
      "entityType": "movie",
      "operation": "upsert",
      "entityId": "2f89df4f-0d24-4e86-94ee-921b4de61f23",
      "clientEntityId": "local-movie-001",
      "revision": 412,
      "created": false,
      "matchedBy": "clientId",
      "recordClientId": "record-6b159c32",
      "deleted": true,
      "deletedAt": "2026-07-24T18:30:00+00:00",
      "tombstoned": true,
      "entity": {
        "id": "2f89df4f-0d24-4e86-94ee-921b4de61f23",
        "client_id": "record-6b159c32",
        "deleted_at": "2026-07-24T18:30:00+00:00"
      }
    }
  ]
}
```

### Volledige identiteitsladder en blockers

De gedeelde catalogus heeft geen per-user ownership-scope voor films: `movies.owner_id` is
nullable en bepaalt geen sync-identiteit. De ladder zoekt daarom collectiebreed:

1. `payload.client_id`: live exact match; altijd eerste trede.
2. Barcode: digits-only vergelijking met behoud van leading zeroes. Overgeslagen bij
   `payload.duplicate_copy:true` en wanneer dezelfde barcode al door een eerdere nieuwe create
   in dezelfde batch is geclaimd.
3. TMDB + formaat + editie: alle drie moeten matchen. Formaat wordt genormaliseerd
   (`4K_UHD`, `4K UHD`, `UHD` → `ultra_hd_blu_ray`). Blockers: verschillende niet-lege
   barcodes, materieel verschillende genormaliseerde titels, of verschillende niet-lege jaren.
   TMDB-extractie uit `movie_identifiers` vergelijkt `provider_id` en `identifier_type`
   hoofdletterongevoelig en geeft `identifier_type=movie_id` voorrang. Lege/alleen-whitespace
   identifierwaarden zijn afwezig. Zonder `movie_id` wint de eerste TMDB-rij in de stabiele
   opslagvolgorde `provider_id, identifier_type, identifier`. De selector trimt de identifierwaarde,
   maar niet de provider/type-sleutels: alle server-writepaden trimmen die sleutels al vóór opslag,
   en dit behoudt pariteit met de clientextractie.
4. Genormaliseerde titel + jaar + hetzelfde niet-lege formaat: uitsluitend in
   `/api/next/sync/reconcile`, nooit in reguliere creates. Blockers: verschillende niet-lege
   barcodes; structureel verschillende containerlidmaatschappen; of twee verschillende
   niet-lege expliciete edities. Containerlidmaatschap is fysieke-copy-identiteit: zodra één
   kandidaat lid is van een box-set/container moeten de genormaliseerde sets container-id's
   exact gelijk zijn. Lid-versus-standalone en leden van verschillende containers matchen dus
   nooit; twee stubs in dezelfde container mogen matchen. Een ontbrekende editie mag alleen
   een niet-lege editie adopteren wanneer beide kandidaten standalone zijn. Alleen het leidende
   Engelse `"the"` wordt verwijderd; `"Die Hard"` en `"De Aanslag"` blijven intact.

Een blocker betekent geen foutresponse maar **geen match**: de create maakt dan een afzonderlijk
record. Een expliciete tombstone-match wordt vóór een nieuwe insert afgehandeld en volgt de
deleted-semantiek hierboven.

### Merge-winnaar en scoreformule

De canonical dedup-implementatie berekent per kandidaat:

```text
score =
  1 × elk niet-leeg movieveld
    (notes, rating, purchase_date, purchase_price, location, edition, edition_type)
  + 1 × elke relation-row in MOVIE_RELATIONS
  + 3 × elke watch_history-row (extra behoudbonus bovenop de relation-row)
  + 3 wanneer eigen/niet-TMDB artwork bestaat
  + 3 wanneer metadata field_locks bevat
```

`MOVIE_RELATIONS` omvat identifiers, localizations, technical specs, credits, genres,
container/group links, watchlist, watch history, tags, digital media en loan requests. Hoogste
score wint; bij gelijkstand wint oudste `created_at`; bij een volledig gelijke timestamp is de
laagste stabiele record-id de deterministische laatste tie-break. Een geldige Admin-selectie mag
alleen een ander bestaand lid van dezelfde canonical groep aanwijzen.

### Canonical Admin dedup-contract (§8.3)

De browser levert nooit een rapportbody aan voor options of execute. PostgreSQL bewaart elke
preview immutable met UUID, SHA-256 reporthash, aparte collection fingerprint, issued/expiry,
payload en consumption-state. De reporthash dekt de exacte canonical payload; de collection
fingerprint negeert alleen vluchtige top-level metadata (`generated_at`, build/scriptmetadata)
en detecteert wijzigingen in groepen, volledige movie-row-state, signalen en scores.

`GET /api/next/admin/dedup/report` (Owner/Admin):

```json
{
  "status": "ok",
  "reportId": "11111111-1111-4111-8111-111111111111",
  "reportHash": "<64 lowercase hex>",
  "expiresAt": "2026-07-24T20:15:00+00:00",
  "executeEnabled": false,
  "report": {"groups": []}
}
```

`POST /api/next/admin/dedup/options` accepteert uitsluitend:

```json
{
  "reportId": "11111111-1111-4111-8111-111111111111",
  "winnerSelections": [
    {"groupId": "<canonical group_id>", "winnerId": "<member movie id>"}
  ]
}
```

`winnerSelections` mag ontbreken/leeg zijn. Elke selectie moet exact bij de opgeslagen groep
horen. De response bevat WebAuthn `options` plus een **nieuw** immutable `reportId`,
`reportHash` en `expiresAt`; execute gebruikt dat nieuwe ID.

`POST /api/next/admin/dedup/execute` accepteert uitsluitend:

```json
{
  "reportId": "22222222-2222-4222-8222-222222222222",
  "credential": {"id": "<passkey assertion id>", "response": {}}
}
```

De server serialiseert executions met een PostgreSQL transaction advisory lock, blokkeert
collection-writes met `SHARE ROW EXCLUSIVE` locks, lockt het report, controleert
expiry/consumption/integriteit, bouwt de actuele preview opnieuw, vergelijkt de collection
fingerprint, verifieert de passkey, voert uitsluitend de opgeslagen payload uit en markeert het
report in dezelfde transactie consumed. Legacy `report:{...}`-payloads worden expliciet
geweigerd.

`backend_version` en `script_commit` in de immutable payload komen in een gepubliceerde image
eerst uit de tijdens de Docker-build vastgelegde `DISCVAULT_IMAGE_VERSION` en
`DISCVAULT_IMAGE_SHA`. Runtime-Composewaarden zoals `BUILD_VERSION=next-dev` mogen deze
imageprovenance niet overschrijven. Een source checkout valt terug op `app/VERSION`, de bestaande
build/commit-omgevingsvariabelen en uiteindelijk de lokale Git-commit.

Stabiele `errorCode`-waarden:

| Situatie | `errorCode` | HTTP |
|---|---|---:|
| Uitvoering feature-flag uit | `admin_dedup_execute_disabled` | 403 |
| Legacy/browser rapportbody | `admin_dedup_legacy_report_rejected` | 400 |
| Malformed report-ID | `admin_dedup_report_id_malformed` | 400 |
| Onbekend report-ID | `admin_dedup_report_unknown` | 404 |
| Verlopen report | `admin_dedup_report_expired` | 410 |
| Collectie gewijzigd | `admin_dedup_report_stale` | 409 |
| Reeds consumed/replay | `admin_dedup_report_consumed` | 409 |
| Ongeldige winner-selectie | `admin_dedup_selection_invalid` | 400 |

Canonical CLI-bron: `app/backend/scripts/sync_dedup_merge.py`. Reproduceerbare dry-runpaden:

```text
# source checkout
python app/scripts/sync_dedup_merge.py

# published v26 image
python /opt/discvault/backend/scripts/sync_dedup_merge.py
```

`app/scripts/sync_dedup_merge.py` is alleen een dunne source-checkoutwrapper; ladder-, report- en
mergelogica bestaan uitsluitend in de canonical backendbron.

### Reconcile (`/api/next/sync/reconcile`)

Request: `{ "clientId": "<device>", "items": [ { client_id, barcode, tmdb_id, format, edition,
title, year, container_ids? } ] }` (max 1000 items). `container_ids`, indien aanwezig, is een
lijst canonical server-UUID's voor structurele box-set/containerlidmaatschappen; ontbrekend of
leeg betekent standalone. Een malformed waarde maakt dat item `invalid`. Response `results[]`
per item: `{ client_id, status:
"matched"|"unknown"|"invalid", matched: bool, entityId?, matchedBy? }`. Dit is het **enige** pad
waar trede 4 (titel+jaar) actief is.

### Checklist

- [x] **P1** Migratie `045_sync_dedup.sql`: `movies.client_id text` + partiële unieke index
      `uq_movies_client_id` (waar `client_id IS NOT NULL`); `deleted_at timestamptz` op `movies`
      én box-set containers + partiële live-indexen; `idx_movies_titleyear_live`.
- [x] **P2** Identiteitsladder in `apply_movie_upsert` (`match_existing_movie`): clientId →
      barcode → tmdb+formaat+editie; `created`/`matchedBy`/`recordClientId` in `results[]`;
      over-merge-bescherming + `duplicate_copy`; batch-lokale clientId-dedup
      (`SyncBatchContext.claimed_client_ids`). Ladder draait alléén in het batch-pad voor echt
      nieuwe records (box-set-import ongemoeid).
- [x] **P3** Soft delete (`apply_movie_delete`, `apply_container_delete` zetten `deleted_at`);
      tombstones uit `bootstrap` gefilterd, wel in `delta`; H4 re-push-afweer
      (`find_tombstoned_movie_by_identity` + delete-wins/resurrect op client-edittijd);
      retentie ≥ 90 dagen (Deel 2).
- [x] **P4** `POST /api/next/sync/reconcile` (adoptie, read-only, titel+jaar-trede alleen hier).
- [x] **P5** Canonical merge-implementatie
      `app/backend/scripts/sync_dedup_merge.py`; `app/scripts/sync_dedup_merge.py` is alleen de
      checkoutwrapper. Dry-run is default; detectie gebruikt dezelfde ladder; winnaar = meeste
      user-data (tie → oudste `created_at`, daarna stabiele id); relaties worden omgehangen;
      verliezers krijgen tombstone; geen client_id-backfill.
- [x] **P6** Tests (`test_next_sync.py` DoD-suite + `app/scripts/tests/test_sync_dedup_merge.py`
      + fail-closed `test_identity_ladder_fixture.py` voor alle categorieën in
      `sync/fixtures/identity-ladder.json`) + versie-bump `app/VERSION`.
