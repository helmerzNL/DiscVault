# Auth And Invites

DiscVault supports passkey/WebAuthn authentication, invite-only registration,
personal API keys, recovery flows, and MemberGroup invites.

The detailed auth, invite, API-key, storage, endpoint, and UI implementation
contract is intentionally not published in this repository. Keep that contract in
private implementation documentation.

Public guidance:

- Configure WebAuthn with `RP_ID` and `RP_ORIGIN` for your deployment.
- Leave `JWT_SECRET` empty only when you want DiscVault to generate one for a
  local/self-hosted instance.
- Store production secrets outside git.
- Personal API keys and invite/recovery codes are shown only once by the app and
  should be treated as sensitive credentials.
