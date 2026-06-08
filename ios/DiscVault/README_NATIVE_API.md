# DiscVault 26 Native iOS API Notes

DiscVault 26 exposes a small native bootstrap contract for the Swift app:

`GET /api/next/mobile/bootstrap`

Call it after the mobile passkey flow has exchanged the one-time code for a
Bearer token. The response is the source of truth for native feature visibility,
profile preferences, notification settings, localization choices and endpoint
paths.

## Auth

Use `ASWebAuthenticationSession` with PKCE:

1. Open `/api/next/auth/mobile/start` with `callback_scheme`, `state`,
   `code_challenge` and `code_challenge_method=S256`.
2. The web session completes the passkey sign-in and redirects to the callback
   URL with a one-time `code`.
3. Exchange the code through `/api/next/auth/mobile/exchange` with the original
   `code_verifier`.
4. Store the returned Bearer token in the Keychain.
5. Send `Authorization: Bearer <token>` on every Next API request.

The token response may include token-specific `permissionKeys`, but the backend
authorizes requests against effective permissions: API token scopes OR the
linked user's role permissions.

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

## Offline

Use the Next sync endpoints exposed in `endpoints.sync`:

- `/api/next/sync/state`
- `/api/next/sync/bootstrap`
- `/api/next/sync/delta`
- `/api/next/sync/mutations`

The native app should cache read models locally and queue mutations with stable
idempotency keys.
