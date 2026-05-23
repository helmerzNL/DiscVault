# Backend Refactor Plan

Goal: reduce `app.py` size while keeping every public API route stable for the
frontend and iOS app.

## Status

- [x] Step 1: Add a Flask app factory/bootstrap entrypoint while keeping the
  existing global `app` compatible.
- [x] Step 2: Extract low-risk shared helpers into modules:
  - `config.py`
  - `db.py`
  - `logging_utils.py`
- [ ] Step 3: Split settings/auth/push domains.
- [ ] Step 4: Split containers into a dedicated module/service.
- [ ] Step 5: Split movies/import/lookup after the smaller domains are stable.

## Container Schema Reference

The normalized Vault / Box Set / Collection database model and the temporary
legacy compatibility rules are documented in `CONTAINER_SCHEMA.md`. Use that
file as the reference when splitting container code or migrating the iOS app.

## Mobile API Contract

Native mobile response rules for iOS and future Android clients are documented
in `MOBILE_API_CONTRACT.md`. Keep this contract in sync when changing sync
responses, filmography responses, image URLs, asset manifests or normalized
container payloads. Changes should remain additive for PWA compatibility unless
a coordinated frontend migration explicitly removes a legacy field.

## Step 3 Validation Notes

- Keep endpoint paths and JSON response shapes unchanged.
- Move low-coupling domains first:
  - settings source/API/display toggles
  - push/VAPID routes and helpers
- Keep auth route extraction separate from permission-helper extraction because
  many other domains still depend on `_get_current_user_id`, `_require_admin`,
  `_has_permission`, and related decorators.
- After each extraction, run Python compilation and route-map validation.
