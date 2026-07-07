# DiscVault Release Notes

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
