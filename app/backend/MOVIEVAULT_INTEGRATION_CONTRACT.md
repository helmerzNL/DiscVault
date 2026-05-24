# MovieVault Integration

MovieVault support in DiscVault is optional. It can be configured as an external
shared metadata service for lookup and contribution flows.

This public repository intentionally does not include the private MovieVault API
contract, operational endpoint details, internal contribution templates, retry
rules, or service-side behavior. Keep those materials in the private MovieVault
repository.

## Public Configuration Surface

DiscVault recognizes these environment variables:

```env
MOVIEVAULT_SEARCH_URL=
MOVIEVAULT_INGEST_URL=
MOVIEVAULT_API_TOKEN=
MOVIEVAULT_SHARING_MODE=opt_in
```

Use placeholders in public examples. Real service URLs and tokens belong in local
deployment configuration only.

## Privacy Boundary

DiscVault must never share private collection state with external metadata
services. This includes user identifiers, passkeys, sessions, private notes,
purchase data, shelf/location, watch history, watchlist, personal ratings, group
membership, local file paths, and private media-server details.

Only public movie, release, box-set, and cast/crew metadata may be considered for
sharing, and only when the user or deployment configuration allows it.
