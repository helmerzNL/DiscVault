# DiscVault Auth, Passkey, API Key And Invite UI Contract

This contract describes how DiscVault currently configures and presents the
security and invite flows in the PWA. It is intended as reusable implementation
context for MovieVault or companion apps that should match DiscVault's technical
behavior and panel design.

## Shared UI Language

DiscVault uses a dark, compact control-panel style.

Core visual tokens:

- `--bg`: near-black page background.
- `--surface`: primary panel/card surface.
- `--surface2`: nested list row or input-block surface.
- `--surface3`: subtle interactive secondary surface.
- `--border`: thin low-contrast borders.
- `--text`: primary text.
- `--text-muted`: secondary descriptive text.
- `--accent`: gold/yellow primary action and highlight.
- `--accent2`: purple secondary accent.
- `--danger`: destructive action color.
- `--success`: success state color.

Shared component conventions:

- Cards use `.card`, `background: var(--surface)`, `border: 1px solid var(--border)`
  and an 8px border radius.
- Card headings use `.card-title`, medium weight and compact sizing.
- Primary actions use `.btn.btn-primary`: gold background, dark text.
- Secondary actions use `.btn.btn-secondary`: transparent/dark surface with border.
- Destructive actions use `.btn.btn-danger`: red/danger text and border styling.
- Inputs use dark backgrounds, `var(--border)` borders, 6px border radius and
  compact padding.
- Sensitive codes and tokens use `DM Mono`, small font sizes and `word-break:
  break-all` where needed.
- Feedback is shown through `.status-msg` with `success`, `error` or `info`
  classes.
- Multi-section settings are organized through a left profile/admin sidebar with
  `.profile-sidebar-btn` buttons and Material Design Icons classes.

## Data Tables

### `users`

Stores local DiscVault users.

Important columns:

- `id`: UUID-like local user id.
- `username`: unique login/user handle.
- `display_name`: optional display name.
- `role`: `admin`, `user` or legacy `MemberGroups`.
- `recovery_hash`: SHA-256 hash of the one-time recovery code.
- `first_name`, `last_name`, `avatar`: profile fields.
- `created_at`: ISO timestamp.

### `credentials`

Stores WebAuthn/passkey credentials.

Important columns:

- `id`: WebAuthn credential id, base64url encoded.
- `user_id`: owner user id.
- `public_key`: stored COSE public key bytes.
- `sign_count`: WebAuthn signature counter.
- `credential_name`: user-facing label, e.g. `MacBook Touch ID`.
- `created_at`: ISO timestamp.

### `settings`

Stores feature switches.

Auth-related keys:

- `auth_enabled`: `true` or `false`.
- `registration_enabled`: `true` or `false`.

### `invite_codes`

Stores admin-created account registration invite codes.

Important columns:

- `id`: local invite id.
- `code_hash`: SHA-256 hash of the 12-character invite code without dashes.
- `username`: the username this invite is reserved for.
- `created_by`: admin user id or `system`.
- `created_at`: ISO timestamp.
- `expires_at`: ISO timestamp, currently 48 hours after creation.
- `used_at`: set when consumed.
- `used_by`: user id that consumed the invite.

The plaintext invite code is returned only once when created.

### `api_keys`

Stores personal API tokens.

Important columns:

- `id`: local token id.
- `user_id`: owner user id.
- `key_hash`: SHA-256 hash of the plaintext token.
- `label`: optional display label.
- `created_at`: ISO timestamp.

The plaintext API key is returned only once at creation time and is never stored.

### `groups`, `user_groups`, `group_invites`

These support user-driven MemberGroup collaboration invites.

`group_invites` columns:

- `id`: local invite id.
- `group_id`: group being shared.
- `inviter_id`: user that sent the invite.
- `invitee_id`: target user.
- `status`: `pending`, `accepted` or `declined`.
- `created_at`: ISO timestamp.

There is a uniqueness constraint on `(group_id, invitee_id)` so one user cannot
receive duplicate invites for the same group.

## Request Authentication

DiscVault protects `/api/*` routes globally when auth is enabled.

Accepted request credentials:

- PWA session token: `Authorization: Bearer <jwt>`.
- Personal API key: `Authorization: Bearer <plaintext-api-key>`.
- Legacy MCP server key: `Authorization: Bearer <MCP_API_KEY>`.

Personal API keys are checked by hashing the bearer value with SHA-256 and
looking it up in `api_keys.key_hash`. When a match is found, `g.current_user_id`
is set for the request and downstream endpoints behave as that user.

Public unauthenticated prefixes include:

- `/api/auth/`
- `/api/health`
- `/api/images/`
- `/api/posters/`
- `/api/profiles/`
- `/api/avatars/`
- `/api/debug/`

## Passkey Setup

### Backend Endpoints

#### `GET /api/auth/status`

Returns current auth state:

```json
{
  "auth_enabled": true,
  "has_users": true,
  "has_credentials": true,
  "rp_id": "example.com",
  "user_count": 1,
  "group_count": 0,
  "role": "admin",
  "registration_enabled": true
}
```

Used by both the login overlay and the settings/admin panels.

#### `POST /api/auth/register/options`

Creates WebAuthn registration options.

Request:

```json
{
  "username": "admin",
  "display_name": "admin",
  "invite_code": "ABCD-EFGH-IJKL"
}
```

Behavior:

- Allows first-user setup without invite.
- Allows authenticated users to add credentials for themselves.
- If users already exist and registration is disabled, an unauthenticated
  registration must include a valid invite code for that exact username.
- Generates and stores a 32-byte challenge.
- Sets RP to `RP_NAME` and `RP_ID`.
- Uses ES256 only: `pubKeyCredParams: [{ type: "public-key", alg: -7 }]`.
- Sets `residentKey: "preferred"` and `userVerification: "preferred"`.
- Adds existing credentials for the user as `excludeCredentials`.

#### `POST /api/auth/register/verify`

Verifies WebAuthn attestation and stores the passkey.

Request:

```json
{
  "user_id": "local-user-id",
  "username": "admin",
  "display_name": "admin",
  "credential_name": "MacBook Touch ID",
  "invite_code": "ABCD-EFGH-IJKL",
  "credential": {
    "id": "...",
    "rawId": "...",
    "response": {
      "attestationObject": "...",
      "clientDataJSON": "..."
    },
    "type": "public-key"
  }
}
```

Behavior:

- Pops the pending challenge for the user.
- Validates `clientDataJSON.type == "webauthn.create"`.
- Validates challenge equality.
- Validates the origin against `RP_ORIGINS`.
- Parses `attestationObject` with CBOR.
- Extracts credential id, COSE key and signature counter.
- Creates a new `users` row if needed.
- First user becomes `admin`; later users become `user`.
- Generates a one-time recovery code for new users and stores only its SHA-256
  hash.
- Consumes the invite code if one was used.
- Inserts the credential into `credentials`.
- Sets `settings.auth_enabled = true`.
- Returns a JWT and, for new users, the plaintext recovery code once.

#### `GET /api/auth/credentials`

Lists credentials. Authenticated users see their own credentials; when no current
user is present, all credentials are returned for bootstrap/admin-like states.

Response items:

```json
{
  "id": "credential-id",
  "credential_name": "MacBook Touch ID",
  "created_at": "2026-05-23T12:00:00",
  "sign_count": 12,
  "username": "admin"
}
```

#### `DELETE /api/auth/credentials/{cred_id}`

Deletes a credential.

Behavior:

- Non-admin users may delete only their own credentials.
- Admin users may delete any credential.
- If no credentials remain, `auth_enabled` is set to `false`.

#### `POST /api/auth/toggle`

Admin-only switch for `auth_enabled`.

Behavior:

- Enabling requires at least one credential.
- Stores `settings.auth_enabled`.

#### `POST /api/auth/users/{user_id}/reset-passkey`

Admin-only reset for a user's credentials. Deletes all passkeys for that user so
they must register again.

### Frontend Flow

PWA JavaScript:

- `checkAuth()` loads `/api/auth/status`.
- `loadAuthSettings()` renders the passkey section.
- `registerPasskey()` handles first passkey setup from Settings.
- `registerAdditionalPasskey()` adds an extra passkey for the current user.
- `_doRegisterPasskey()` performs options -> `navigator.credentials.create()` ->
  verify.
- `deleteCredential()` deletes a credential after confirmation.

### Panel Presentation

Location:

- Profile -> Security (`profileSubSecurity`) for personal passkeys.
- Admin -> Security (`adminSubSecurity`) for global auth and registration toggles.

Personal passkey card:

- Card title: `Authenticatie (Passkey)`.
- If no credentials exist, a gold-tinted warning panel is shown with:
  - username input,
  - passkey name input,
  - primary button `Passkey Registreren`.
- Existing passkeys are rendered as compact rows:
  - key icon,
  - passkey label,
  - username, created date and sign count in mono muted text,
  - red danger delete button.
- Extra passkey section appears when credentials exist:
  - label input,
  - secondary `Toevoegen` button.
- Successful first-user registration reveals a recovery-code card:
  - gold-tinted bordered panel,
  - monospace recovery code,
  - secondary dismiss button.

Admin auth card:

- Card title: `Authenticatie`.
- Contains an `authToggle` checkbox styled with `accent-color: var(--accent)`.
- Shows a small `authStatusBadge`.
- Shows logout button aligned to the right when applicable.
- Shows `registrationToggle` only for admins when auth is enabled.

## Login Page With Invite-Only Option

### Backend Endpoints

#### `POST /api/auth/login/options`

Returns WebAuthn assertion options.

Behavior:

- Loads all credential ids as `allowCredentials`.
- Stores a `_login` challenge.
- Uses `rpId = RP_ID`.
- Sets `userVerification: "preferred"`.

#### `POST /api/auth/login/verify`

Verifies WebAuthn assertion.

Behavior:

- Pops the `_login` challenge.
- Finds the stored credential by id.
- Validates `clientDataJSON.type == "webauthn.get"`.
- Validates challenge and origin.
- Validates RP ID hash.
- Verifies the signature with the stored public key.
- Updates `credentials.sign_count`.
- Returns a JWT for normal PWA login.
- If `mobile_flow` is supplied, returns a custom-scheme callback URL with a
  short-lived one-time code for iOS.

#### `POST /api/auth/recovery`

Allows login with username and one-time recovery code.

Behavior:

- Compares SHA-256 hash of submitted code with `users.recovery_hash`.
- Generates a new recovery code and stores only its hash.
- Deletes existing passkeys for that user.
- Returns a JWT and new recovery code.

### Invite-Only Registration Rules

Invite-only mode is active when:

- auth is enabled,
- at least one user exists,
- `settings.registration_enabled` is `false`.

In that state:

- The login overlay still shows a registration button.
- The button text switches to `Registreren met uitnodigingscode`.
- The register form shows an invite-code field.
- Both registration options and verification validate the invite code against
  `invite_codes`.

### Frontend Flow

PWA JavaScript:

- `showLoginOverlay(statusData)` decides which login/register controls are shown.
- `loginPasskey()` performs options -> `navigator.credentials.get()` -> verify.
- `toggleRecoveryLogin()` reveals the recovery-code form.
- `recoveryLogin()` posts to `/api/auth/recovery`.
- `toggleLoginRegister()` reveals registration fields.
- `loginRegisterPasskey()` performs invite-aware account registration.

### Login Overlay Presentation

The login view is a full-screen overlay:

- `#loginOverlay` is fixed, full viewport, z-index `5000`.
- Background is `var(--bg)`.
- Safe-area top padding is respected for mobile/PWA.
- A decorative `.login-covers` layer fills the background with many tilted movie
  cover tiles.
- The centered login block has max width `360px`, padding `40px` and z-index `2`.
- Logo is an inline SVG disc/keyhole mark:
  - dark rounded-square base,
  - gold and purple disc rings,
  - `4K` gold badge.
- Text stack:
  - `DiscVault` in serif display font,
  - `4K Collection` badge,
  - version line in `DM Mono`,
  - prompt text in muted color.
- Main login button is full-width primary.
- Recovery and register buttons are full-width secondary buttons below it.
- Register form fields:
  - username,
  - invite code when invite-only is active,
  - passkey name.
- Invite code input is monospace, uppercase and uses letter spacing.

## Personal API Keys

### Backend Endpoints

#### `GET /api/user/api-keys`

Lists the current user's personal API keys.

Response:

```json
[
  {
    "id": 1,
    "label": "Claude Desktop",
    "created_at": "2026-05-23T12:00:00"
  }
]
```

Plaintext keys are never returned by this endpoint.

#### `POST /api/user/api-keys`

Creates a personal API token.

Request:

```json
{
  "label": "Claude Desktop"
}
```

Behavior:

- Requires an authenticated user.
- Label is optional and truncated to 80 characters.
- Generates `secrets.token_urlsafe(32)`.
- Stores only `sha256(plaintext)` in `api_keys.key_hash`.
- Returns plaintext key once in `key`.
- Returns HTTP `201`.

Response:

```json
{
  "id": 1,
  "label": "Claude Desktop",
  "created_at": "2026-05-23T12:00:00",
  "key": "plain-token-shown-once"
}
```

#### `DELETE /api/user/api-keys/{key_id}`

Revokes a personal API key.

Behavior:

- Requires authenticated user.
- Deletes only keys belonging to the current user.
- Returns `404` if the id does not belong to the user.
- Returns `{ "ok": true }` on success.

### Frontend Flow

PWA JavaScript:

- `loadApiKeys()` loads the key list and renders empty/list states.
- `generateApiKey()` posts the label and reveals the one-time plaintext key.
- `copyApiKey()` copies the revealed key.
- `revokeApiKey(id)` deletes a key and reloads the list.

### Panel Presentation

Location:

- Profile -> API Keys (`profileSubApiKeys`).

MCP API keys card:

- Card title: `MCP API Sleutels`.
- Muted explanatory paragraph describes usage for AI clients such as Claude or
  Cursor via MCP.
- Inline compact form:
  - optional label input,
  - primary `Sleutel aanmaken` button.
- One-time reveal panel:
  - gold-tinted warning block,
  - warning text: copy now, key is never shown again,
  - monospace `<code>` element with wrapping key text,
  - secondary copy button,
  - small MCP config hint using `Authorization: Bearer <sleutel>`.
- Existing keys render as compact `surface2` rows:
  - label or muted unnamed label,
  - created date,
  - danger `Intrekken` button.

General API keys card:

- Present in the UI as a disabled, faded placeholder.
- Shows `Binnenkort`/`Coming soon`.
- Uses `opacity: 0.5`, `pointer-events: none` and disabled controls.

## Account Invite Codes

These are admin-created codes that allow new users to register when public
registration is disabled.

### Backend Endpoints

#### `POST /api/auth/invite`

Admin-only creation endpoint.

Request:

```json
{
  "username": "jan"
}
```

Behavior:

- Requires admin.
- Rejects empty usernames.
- Rejects usernames that already exist.
- Generates a 12-character code from `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`.
- Displays the code as `XXXX-XXXX-XXXX`.
- Stores only SHA-256 hash of the raw code without dashes.
- Stores username, creator, creation timestamp and 48-hour expiry.
- Returns plaintext code once.

Response:

```json
{
  "id": 1,
  "code": "ABCD-EFGH-IJKL",
  "username": "jan",
  "expires_at": "2026-05-25T12:00:00"
}
```

#### `GET /api/auth/invite`

Admin-only list endpoint.

Response:

```json
[
  {
    "id": 1,
    "username": "jan",
    "created_at": "2026-05-23T12:00:00",
    "expires_at": "2026-05-25T12:00:00",
    "used_at": null
  }
]
```

The plaintext code is not listed.

#### `DELETE /api/auth/invite/{invite_id}`

Admin-only revocation endpoint. Deletes the invite code row.

### Frontend Flow

PWA JavaScript:

- `toggleRegistration()` writes the `registration_enabled` setting.
- `createInviteCode()` creates a code and reveals it once.
- `copyInviteCode()` copies the currently revealed code.
- `loadInviteCodes()` lists active/used/expired invite rows.
- `revokeInviteCode(id)` deletes an invite and reloads the list.

### Panel Presentation

Location:

- Admin -> Security (`adminSubSecurity`).

Registration toggle:

- Checkbox row in the auth card.
- Uses `accent-color: var(--accent)`.
- Only visible to admins when auth is enabled.
- Descriptive helper text explains that disabling it blocks new users from the
  login screen unless they have an invite code.

Invite codes card:

- Card title: `Uitnodigingscodes`.
- Muted description text explains temporary account registration codes.
- Compact form with:
  - username input,
  - primary `Code aanmaken` button.
- One-time code result panel:
  - gold-tinted background,
  - monospace code in large text with letter spacing,
  - secondary copy button,
  - expiry text in muted small type.
- Invite list rows:
  - `surface2` background,
  - username in bold,
  - expiry timestamp,
  - status label:
    - used: muted,
    - expired: danger,
    - active: success,
  - danger `Intrekken` button for unused invites.

## MemberGroup User Invites

These are collaboration invites from one user/group owner to another existing
DiscVault user. They are separate from account registration invite codes.

### Backend Endpoints

#### `POST /api/groups/{group_id}/invite`

Sends an invite to an existing user by username.

Behavior:

- Requires authenticated user when auth is enabled.
- Caller must be admin or owner of the group.
- Rejects missing username.
- Rejects unknown users.
- Rejects self-invites.
- Rejects users that are already group members.
- Inserts `group_invites` with status `pending`.
- Duplicate invite for the same group/user returns conflict because of the unique
  constraint.
- Sends a push notification when the invitee has the `group_invite` preference
  enabled.

#### `GET /api/invites`

Returns pending invites for the current user.

Response item:

```json
{
  "id": 1,
  "group_id": 12,
  "status": "pending",
  "created_at": "2026-05-23T12:00:00",
  "group_name": "Family Collection",
  "inviter_username": "admin",
  "inviter_display_name": "Admin"
}
```

#### `POST /api/invites/{invite_id}/accept`

Accepts a pending invite for the current user.

Behavior:

- Inserts the user into `user_groups` as `member`.
- Updates invite status to `accepted`.

#### `POST /api/invites/{invite_id}/decline`

Declines a pending invite for the current user by setting status to `declined`.

### Frontend Flow

PWA JavaScript:

- `sendGroupInvite(groupId, groupName)` sends an invite from the group member
  management panel.
- `loadInviteNotifications()` checks pending invites and updates the bell badge.
- `openInvitePanel()` renders the invite modal.
- `respondInvite(inviteId, action)` posts accept/decline and refreshes the
  collection/group filter when accepted.

### Panel Presentation

Send invite panel:

- Appears inside the user's group member management card.
- Uses a top border divider, small section title and compact row:
  - username input,
  - primary invite button.
- Inline status message appears below.

Invite notification:

- Header invite bell is hidden when there are no pending invites.
- Badge is a small circular gold counter with dark text.
- Service worker can open `/#invites`; the app listens for `open-invites`.

Invite modal:

- Full-screen translucent dark overlay.
- Centered panel with `var(--surface)` background, border and 12px radius.
- Header contains `Uitnodigingen` and an `x` close button.
- Each invite row uses `surface2`, border and 8px radius.
- Row content:
  - group name with folder icon,
  - inviter display name/username and date in muted text,
  - primary accept button,
  - secondary decline button.

## Logging And Security Notes

- Passkey registration, login, deletion, auth toggling, user deletion and passkey
  resets write auth/settings logs.
- Account invite creation/revocation currently does not include explicit log
  entries beyond frontend status messages.
- Personal API key creation/revocation currently does not write explicit log
  entries.
- Plaintext passkeys, API keys and invite codes are never stored.
- Invite codes and API keys are SHA-256 hashed before storage.
- Recovery codes are SHA-256 hashed and rotated after recovery login.
- WebAuthn origins must match configured `RP_ORIGINS`.
- WebAuthn RP ID must match `RP_ID`.
- Related origins are exposed through `/.well-known/webauthn`.

## Implementation Files

Backend:

- `app/backend/app.py`: auth endpoints, WebAuthn helpers, personal API keys,
  invite-code endpoints and group-invite endpoints.
- `app/backend/settings/routes.py`: registration setting endpoints.

Frontend:

- `app/frontend/index.html`: login overlay and settings/profile/admin panel markup.
- `app/frontend/js/auth.js`: passkey, login, account invite and API key flows.
- `app/frontend/js/social.js`: MemberGroup invite flows.
- `app/frontend/styles.css`: shared cards, buttons, status messages, login cover
  background and sidebar styling.
- `app/frontend/i18n/translations.json`: localized labels for passkeys, login,
  invites and API keys.

