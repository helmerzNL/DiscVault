# Backend Refactor Plan

Goal: split the oversized Next backend into domain modules while keeping every
public API route stable for the web frontend, MCP/API clients, and native iOS
app.

The immediate target is `next_app.py`. On 2026-06-10 it contained about 51k
lines, which is too large to review safely and makes small feature work
unnecessarily risky.

## Rules

- Keep endpoint paths, HTTP methods, auth behavior, and JSON response shapes
  unchanged unless a separate migration explicitly documents a contract change.
- Move one domain per PR/commit series. Avoid broad formatting-only churn.
- Prefer pure helper/service extraction before moving route registration.
- Keep compatibility imports or thin wrappers in `next_app.py` where tests or
  older modules still import helpers directly.
- Run validation after every step:
  - `python -m py_compile app\backend\next_app.py`
  - targeted backend tests for the moved domain
  - route-map comparison when routes are moved
  - GitHub Actions smoke and Docker publish after push

## Status

- [x] Step 1: Add a Flask app factory/bootstrap entrypoint while keeping the
  existing global `app` compatible.
- [x] Step 2: Extract low-risk shared helpers into modules:
  - `config.py`
  - `db.py`
  - `logging_utils.py`
- [x] Step 3: Extract shared infrastructure helpers into `next_common.py`
  (`NextApiError`, `json_ready`, `response`, `parse_int_arg`, `parse_uuid`,
  `parse_bool_value`, `parse_uuid_list`, `table_exists`, `count_table`).
  `next_app.py` re-imports these names for backward compatibility.
- [x] Step 4: Split profile, API tokens, audit activity, and MCP activity into
  `next_profile.py`, `next_api_token.py`, `next_audit.py`, and
  `next_mcp_activity.py`. `next_app.py` re-imports these names for backward
  compatibility. Runtime dependencies that still live in `next_app.py`
  (`permission_key_catalog`, `table_exists`, `media_asset_public_url`,
  `next_user_primary_role`) are resolved lazily to avoid an import cycle.
  Flask route handlers are registered via `register_next_profile_routes`,
  `register_next_audit_routes`, and `register_next_mcp_routes`; all three are
  called from `register_routes()` in `next_app.py` and re-exported for
  backward compatibility.
- [x] Step 5: Split people and native filmography into `next_people.py`.
- [x] Step 6: Split settings, preferences, notifications, and push/PWA into
  `next_preferences.py`, `next_notifications.py`, and `next_push.py` with
  compatibility re-exports in `next_app.py` and route registration via
  `register_next_preferences_routes`, `register_next_notifications_routes`, and
  `register_next_push_routes`.
- [ ] Step 7: Split collection movies, containers, and media groups.
- [ ] Step 8: Split import, migration, metadata, and plugin operations.
- [ ] Step 9: Split server-rendered dashboard/detail HTML views.
- [ ] Step 10: Retire compatibility wrappers once callers use the new modules.

## Proposed Module Layout

The shared infrastructure helpers now live in the flat `app/backend/next_common.py`
module (consistent with the existing `next_*.py`, `config.py`, and `db.py`
siblings). Remaining domains may follow the same flat-module convention; the
package layout below describes the logical grouping rather than a literal path:

- `next_common.py`: `NextApiError`, `json_ready`, `response`, request parsing,
  UUID helpers, and table helpers.
- `next_audit.py`: audit-event persistence, request-IP resolution, API/MCP audit
  metadata, sensitive-payload redaction, and profile API audit filters.
- `next_api_token.py`: API token permission catalogs, token payload helpers, and
  the profile API access payload.
- `next_profile.py`: profile account payloads (user details and recovery-code
  status).
- `next_mcp_activity.py`: MCP tool catalog and API-token extraction for MCP
  requests.
- `next_people.py`: person read models, biography/language fallbacks, TMDb and
  local filmography merging, native iOS payload builders, and people route
  registration.
- `next/i18n.py`: locale catalog loading, supported locales, flags, and
  language normalization.
- `next/security.py`: permission checks, visibility SQL, actor helpers, and
  audit permission-denied helpers.
- `next/profile.py`: profile payloads, API token payloads, profile audit/MCP
  activity filters, and profile-facing routes.
- `next/mcp.py`: MCP proxy, MCP catalog audit helpers, and MCP diagnostics.
- `next/people.py`: person detail, localizations, credits, biography,
  filmography hydration, and native people payloads.
- `next_preferences.py`: app preferences, mobile capability contract payloads,
  and preferences/mobile route registration.
- `next_notifications.py`: notification preference map, counts, rows, creation,
  and notifications route registration.
- `next_push.py`: push VAPID + subscription/native delivery helpers, PWA
  manifest/head/asset helpers, and push/PWA route registration.
- `next/collection.py`: movies, identifiers, credits, personal lists, digital
  availability, and collection snapshots.
- `next/containers.py`: containers, memberships, aggregate members, receiver
  payloads, and delete behavior.
- `next/media.py`: artwork/media assets, upload handling, trash/restore, and
  media groups.
- `next/imports.py`: import source inspection, review queue, box-set proposals,
  uploads, and rollback.
- `next/operations.py`: admin operations dashboard data and job/audit summaries.
- `next/views.py`: server-rendered HTML for dashboards, detail pages, and
  shared markup helpers.
- `next/routes.py`: route registration composed from the domain modules.

## Work Packages

### 1. Shared Infrastructure

Move helpers that do not depend on application domains:

- `NextApiError`, `json_ready`, `response`, `parse_int_arg`, `parse_uuid`,
  `parse_bool_value`, date helpers, `table_exists`, `count_table`.
- PWA/i18n helpers can follow in the same package if the diff stays small.
- Keep imports in `next_app.py` so existing tests can still import the same
  names during the transition.

Acceptance:

- `next_app.py` imports these helpers from the new module.
- No endpoint implementation is moved yet.
- Compile check passes.

### 2. Profile, API Tokens, Audit, MCP Activity

Extract the domain that recently changed and has clear boundaries:

- API token permission catalogs and token payload helpers.
- Profile API access payloads.
- `normalize_profile_api_audit_category`, `profile_api_audit_search_term`,
  category/token/search conditions.
- API/MCP audit metadata and profile activity rows.
- MCP proxy diagnostics and catalog activity logging.

Acceptance:

- Profile API activity and MCP activity continue to show the same audit rows.
- Existing API token tests pass.
- Project issue notes include any remaining diagnostic gaps.

### 3. People And Native Filmography

Extract person-specific read models and native payload builders:

- Person identifiers/localizations/credits.
- Biography and language fallback helpers.
- TMDb-backed filmography hydration and local ownership/digital enrichment.
- Native people detail and filmography payloads.

Acceptance:

- `/api/next/people/{personId}` unchanged.
- `/api/next/people/{personId}/filmography?language=nl` still hydrates,
  persists, and returns local availability.
- Native iOS contract docs stay current.

### 4. Preferences, Notifications, Push/PWA

Extract smaller user-facing domains:

- App preferences and mobile capabilities.
- Notification preference map, counts, rows, creation.
- Push VAPID and subscription helpers.
- PWA manifest/head/asset helpers if not already moved.

Acceptance:

- Notification filter/profile sync behavior remains unchanged.
- PWA manifest and assets still resolve.

### 5. Collection, Containers, Media

Extract larger collection domains in separate passes:

- Collection movie read models and personal list state.
- Container details, memberships, aggregate members, receiver payloads.
- Media groups and artwork/media asset management.

Acceptance:

- Native sync/bootstrap and collection detail endpoints retain stable payloads.
- Container member position/order behavior remains unchanged.

### 6. Import, Metadata, Operations, Views

Move the high-churn admin/HTML areas last:

- Import source review, box-set detection, uploads, rollback.
- Metadata refresh jobs and plugin operations.
- Operations dashboard data builders.
- Server-rendered HTML views and markup helpers.

Acceptance:

- Import review behavior and box-set proposals match current behavior.
- Operations dashboard counts/cards remain stable.
- Route-map comparison shows no accidental route loss.

## Private References

Detailed container schema notes and native mobile API contracts are maintained
outside the public repository. Keep public changes additive unless a coordinated
frontend/mobile migration explicitly removes a legacy field.
