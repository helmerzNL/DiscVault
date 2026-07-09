# DiscVault 26 Native iOS API Notes

DiscVault 26 exposes a small native bootstrap contract for the Swift app:

`GET /api/next/mobile/bootstrap`

Call it after the mobile passkey flow has exchanged the one-time code for a
Bearer token. The response is the source of truth for native feature visibility,
profile preferences, notification settings, localization choices and endpoint
paths.

## Auth

Native iOS passkey login can use WebAuthn assertion endpoints directly:

- `POST /api/next/auth/passkeys/login/options`
- `POST /api/next/auth/passkeys/login/verify`

DiscVault also keeps fallback aliases for temporary client compatibility:

- `POST /api/next/auth/passkey/login/options`
- `POST /api/next/auth/login/options`
- `POST /api/next/auth/passkey/login/verify`
- `POST /api/next/auth/login/verify`

The verify response returns a native-usable token payload (`token`,
`api_token.permission_keys`, `user`) plus compatibility fields
(`access_token`, `currentUser`, `session`, `profile`).

Fallback browser flow remains available via `ASWebAuthenticationSession` + PKCE:

1. Open `/api/next/auth/mobile/start` with `callback_scheme`, `state`,
   `code_challenge` and `code_challenge_method=S256`.
2. If the user already has an active DiscVault web session (for example from the
   PWA), the backend immediately redirects to the callback URL with a one-time
   `code` (no extra login prompt).
3. Otherwise, the web session completes the passkey sign-in and then redirects
   to the callback URL with a one-time `code`.
4. Exchange the code through `/api/next/auth/mobile/exchange` with the original
   `code_verifier`.
5. Store the returned Bearer token in the Keychain.
6. Send `Authorization: Bearer <token>` on every Next API request.

The token response may include token-specific `permissionKeys`, but the backend
authorizes requests against effective permissions: API token scopes OR the
linked user's role permissions.

If the user lost their passkey, the recovery-code sign-in on the web login page
completes the same mobile flow: `POST /api/next/auth/recovery` accepts the
`mobile_flow` id and, on success, returns the one-time `callback_url`
(`callbackUrl`/`state`) just like the passkey verify response, so the app finishes
linking and can then prompt the user to add a new passkey.

## Bootstrap

`/api/next/mobile/bootstrap` returns:

- `user`: profile identity and avatar URL.
- `auth.role`, `auth.tokenPermissionKeys`, `auth.effectivePermissionKeys`.
- `capabilities`: server-approved feature visibility for collection, metadata,
  import, containers, groups, personal lists, API/MCP and offline sync.
- `preferences.values`, `preferences.defaults`, `preferences.sections`.
- `notifications.counts`, notification preferences and Web Push metadata.
- `localization.locales` with display names and flags.
- `endpoints`: canonical endpoint paths for sync, import and metadata refresh.

The iOS app should hide actions when the matching capability is false. Backend
permission checks remain authoritative.

## Barcode And Manual Import

Barcode and manual search should follow the same flow as the PWA:

1. Call `POST /api/next/metadata/lookup` with barcode and/or title.
2. Present movie candidates and box-set candidates from the response.
3. If the user selects a single movie, call `POST /api/next/import/movie` with
   `importMode: "movie"` and the selected candidate/metadata result.
4. If the user selects a box-set, call `POST /api/next/import/movie` with
   `importMode: "box-set"` and the selected box-set proposal. The backend creates
   the container and member movies when the proposal contains members.
5. Queue or retry this write when offline; do not pretend a write succeeded
   until the backend confirms it.

## Metadata Refresh

Movie detail screens should call:

`POST /api/next/movies/{movieId}/metadata/refresh`

Container detail screens should call:

`POST /api/next/containers/{containerId}/metadata/refresh`

Use `dryRun: true` for preview UI and `dryRun: false` to apply. The user must
have `metadata.refresh_one` through role permissions or token permissions.

## Preferences

Use `/api/next/mobile/bootstrap` to render the profile preference groups:

- `appearance`
- `library`
- `collectors`

Patch changes through `/api/next/preferences` with:

```json
{"preferences":{"theme":"dark"}}
```

## Notifications

Use `/api/next/notifications` for the in-app inbox and
`/api/next/push/preferences` for notification category switches. Native APNS
delivery is marked as planned in the bootstrap response; current backend push
metadata is Web Push compatible.

Loan requests emit notifications with a `payload.kind` the client should handle:

- `loan_request` — the disc owner receives this when a member asks to borrow a
  disc. Payload carries `loanRequestId`, `movieId`, `movieTitle`, `requesterId`,
  `borrowFrom`, `returnBy`. Offer Approve/Decline actions that call the
  loan-request approve/decline endpoints below.
- `loan_request_decided` — the requester receives this when the owner approves or
  declines. Payload carries `loanRequestId`, `status` (`approved` | `declined`),
  `movieId`, and (on approval) `loanId`.

## Loans And Borrow Requests

Gated by the `personal.loanRequests` capability
(`capabilities.personal.loanRequests`, backed by the `lending.request`
permission). When `false`, hide the borrow-request UI.

Own discs (owner) use the loan endpoints in `endpoints.loans`:

- `GET /api/next/loans` — active loans the user has lent out.
- `GET /api/next/loans/borrowed` — discs borrowed by the user.
- `POST /api/next/loans/{loanId}/return` — mark a loan returned.

Discs owned by another group member cannot be lent directly; instead the member
sends a borrow request (`endpoints.loanRequests`):

- `POST /api/next/movies/{movieId}/loan-requests` — create a request.
  Body: `{"borrowFrom":"YYYY-MM-DD","returnBy":"YYYY-MM-DD","note":"optional"}`.
  Both dates are required and `returnBy` must be on or after `borrowFrom`.
  A duplicate pending request for the same disc returns `409`.
- `GET /api/next/loan-requests?role=incoming|outgoing` — list requests.
  `incoming` = requests to decide as the owner; `outgoing` = the user's own
  requests with their current status.
- `POST /api/next/loan-requests/{loanRequestId}/approve` — owner approves; this
  auto-creates a loan (`resultingLoanId`) with the requester as borrower and
  `returnBy` as the due date, and notifies the requester.
- `POST /api/next/loan-requests/{loanRequestId}/decline` — owner declines and
  notifies the requester.
- `POST /api/next/loan-requests/{loanRequestId}/cancel` — requester cancels their
  own pending request.

Request `status` values: `pending`, `approved`, `declined`, `cancelled`. The
per-movie state (in the movie detail payload) exposes the viewer's outgoing
request under `userState.loanRequest` and an `incomingLoanRequests` count for
owners.

### Loans System Toggle (instance-wide)

The entire loans + borrow-request feature can be switched off instance-wide by
an admin. The mobile bootstrap exposes the current state under
`instanceSettings.loansSystemEnabled` (boolean). When `false`, hide **all** loan
and borrow-request UI (loan lists, borrow-request forms, the on-loan section)
for every user, regardless of their `personal.loanRequests` capability. The
loan and loan-request endpoints also reject writes with `409` while disabled.

Admins (users with the `security.manage_loans_system` permission, surfaced as
the `personal.manageLoansSystem` capability) can change it:

- `PATCH /api/next/admin/settings/loans-system` — body `{"enabled": true|false}`.
  Returns `{"status":"ok","loansSystemEnabled": bool}`. Only expose this control
  when `capabilities.personal.manageLoansSystem` is `true`.

Fresh installs default to disabled; installs that already had loan data default
to enabled.

## Offline

Use the Next sync endpoints exposed in `endpoints.sync`:

- `/api/next/sync/state`
- `/api/next/sync/bootstrap`
- `/api/next/sync/delta`
- `/api/next/sync/mutations`

The native app should cache read models locally and queue mutations with stable
idempotency keys.

### Bootstrap Cast And Crew

`GET /api/next/sync/bootstrap` includes stable people relationships in
`payload.moviePeople`, split into convenience arrays `payload.movieCast` and
`payload.movieCrew`.

Each relationship uses PostgreSQL UUIDs:

```json
{
  "creditId": "3d4b6e50-...",
  "movieId": "7b4d4e12-...",
  "personId": "8bf2cfa8-...",
  "personPublicId": "person-rutger-hauer",
  "tmdbId": "585",
  "name": "Rutger Hauer",
  "profileUrl": "https://...",
  "creditType": "actor",
  "department": "cast",
  "character": "Roy Batty",
  "job": "",
  "sortOrder": 1
}
```

Use `personId` as the stable local identity. The same actor or crew member has
the same `personId` across all movies, so the iOS cache can deduplicate people
without relying on names.

## People And Filmography

Person detail:

`GET /api/next/people/{personId}?language=nl`

The response keeps the existing web payload in `detail.person`, and also exposes
an iOS-friendly flattened person payload at `person` and on `detail`:

```json
{
  "status": "ok",
  "person": {
    "id": "8bf2cfa8-...",
    "publicId": "person-rutger-hauer",
    "tmdbId": "585",
    "name": "Rutger Hauer",
    "profileUrl": "https://...",
    "birthday": "1944-01-23",
    "deathday": "2019-07-19",
    "placeOfBirth": "Breukelen, Utrecht, Netherlands",
    "biography": "Nederlandse biografie...",
    "biography_nl": "Nederlandse biografie...",
    "biography_en": "English biography...",
    "biographyByLanguage": {
      "nl": "Nederlandse biografie...",
      "en": "English biography..."
    }
  }
}
```

`language` is optional. When provided, `biography` prefers that localization,
then its base language, then Dutch, then English, then metadata fallback.

Filmography:

`GET /api/next/people/{personId}/filmography?language=nl`

The backend owns filmography hydration. iOS should not call TMDb directly for a
person page. When a person has a linked `tmdbId` but no stored filmography yet,
the endpoint asks the enabled TMDb metadata plugin for combined credits, stores
the result in `people.metadata.filmography` / `people.metadata.combined_credits`,
then returns the stored data enriched with local DiscVault state.

Even when TMDb is unavailable, the endpoint still falls back to local
`movie_credits` for that `personId`. Local entries include collection ownership,
poster URLs, movie identifiers and digital platform availability.

The response separates cast and crew and also provides `items` for one combined
list:

```json
{
  "status": "ok",
  "refresh": {
    "status": "refreshed",
    "plugin": {"id": "tmdb", "name": "TMDb"},
    "execution": {"status": "ok", "entrypoint": "person_filmography"}
  },
  "personId": "8bf2cfa8-...",
  "tmdbId": "585",
  "language": "nl",
  "cast": [
    {
      "tmdbId": "78",
      "movieId": "7b4d4e12-...",
      "title": "Blade Runner",
      "year": "1982",
      "releaseDate": "1982-06-25",
      "posterUrl": "https://...",
      "character": "Roy Batty",
      "job": "",
      "department": "cast",
      "creditType": "actor",
      "inCollection": true,
      "inDigital": true,
      "digitalItems": [
        {
          "id": "2c7d...",
          "platform": "Plex",
          "sourceName": "Plex",
          "sourceType": "plex",
          "pluginId": "plex",
          "playbackUrl": "https://..."
        }
      ],
      "digitalPlatformUrls": [
        {"platform": "Plex", "url": "https://..."}
      ]
    }
  ],
  "crew": []
}
```

`refresh` is `null` when stored filmography already existed. It can be
`{"status":"skipped", ...}` when TMDb is not configured; in that case iOS should
still render any returned local `cast`, `crew` and `items`.

`movieId` is present when the TMDb film is in the local collection.
`inCollection` and `inDigital` are independent booleans: a movie can be physical
only, digital only, both, or neither.
