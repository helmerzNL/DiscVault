# MovieVault Handshake Client Contract

Deze notitie beschrijft alleen de DiscVault-clientkant van de MovieVault
koppellaag. Het MovieVault-servercontract blijft de bron van waarheid in de
MovieVault repository.

DiscVault gebruikt intern een MovieVault token-provider. De rest van de app mag
niet weten welke provisioningmethode daarachter zit. De standaardprovider is
`bootstrap_signed`. `hmac_handshake` blijft alleen beschikbaar als legacy of
advanced mode voor private deployments, development en staging.

## Configuratie

DiscVault ondersteunt deze server-side configuratie:

```env
MOVIEVAULT_SEARCH_URL=
MOVIEVAULT_INGEST_URL=
MOVIEVAULT_SHARING_MODE=opt_in
```

Voor standaard deployments gebruikt DiscVault ingebouwde defaults:

```env
MOVIEVAULT_SEARCH_URL=https://search.discvault.eu
MOVIEVAULT_INGEST_URL=https://movies.discvault.eu
```

`MOVIEVAULT_API_TOKEN` blijft alleen als legacy fallback bestaan. De normale
route is dat DiscVault het token server-side via bootstrap of signed recovery
ophaalt en opslaat.

`MOVIEVAULT_DISCVAULT_HANDSHAKE_SECRET` is niet verplicht aan DiscVault-kant.
Alleen wanneer `MOVIEVAULT_AUTH_METHOD=hmac_handshake` of een overeenkomstige
setting is ingesteld gebruikt DiscVault de legacy HMAC-provider.

## Instance Identity

DiscVault genereert bij de eerste MovieVault-koppeling een stabiele
`movievault_instance_id` in de settings store. De waarde begint met `dv_` en
blijft behouden over serverrestarts en tokenrotaties.

Een ingetrokken MovieVault-koppeling wordt lokaal gemarkeerd met
`movievault_link_status=revoked`. In die status gebruikt DiscVault geen lokaal
MovieVault-token meer.

## Standaardprovider: Zero-Config Bootstrap

DiscVault voert lazy bootstrap uit wanneer een server-side MovieVault-call
authenticatie nodig heeft en nog geen bruikbaar token beschikbaar is.

Endpoint:

```http
POST /api/v1/internal/discvault/bootstrap
```

- bij eerste koppeling genereert DiscVault lokaal een instance key pair;
- bootstrap request bevat `instanceId`, `instanceName`, `publicKey`,
  softwareversie en requested scopes;
- token recovery en tokenrotatie worden gesigned met de lokale private key;
- de private key blijft uitsluitend server-side;
- admin UI toont geen token, secret, scopes of standaard URLs.

Signed recovery gebruikt:

```http
POST /api/v1/internal/discvault/handshake
Content-Type: application/json
X-DiscVault-Timestamp: <UTC timestamp>
X-DiscVault-Nonce: <fresh nonce>
X-DiscVault-Key-Id: <instance public key id>
X-DiscVault-Signature: key-v1=<base64url signature>
```

De signature input is:

```text
timestamp + "." + nonce + "." + raw_request_body
```

## Legacy Provider: HMAC Handshake

De HMAC-provider is geisoleerd in de token-provider laag. Search-, template-,
contribution- en settings-code mogen niet direct afhankelijk zijn van HMAC
headers, secrets of endpointdetails.

Legacy endpoint:

```http
POST /api/v1/internal/discvault/handshake
```

met:

```http
X-DiscVault-Signature: sha256=<hex hmac>
```

## Tokenopslag

Het volledige `client.apiToken` wordt alleen server-side en encrypted opgeslagen
onder `movievault_api_token_enc`. De oude plaintext setting
`movievault_api_token` wordt alleen nog als legacy fallback gelezen. De browser
krijgt het volledige token niet terug.

Settings/status mogen alleen tonen:

- token aanwezig: ja/nee
- token prefix of mask
- instance id
- instance naam
- link status
- scopes
- laatste handshake timestamp
- auth method

Contribution-template discovery is publiek en wordt zonder bearer token
opgevraagd. Search- en ingest/write-calls blijven protected en gebruiken het
server-side token.

## Recovery

Bij `401 Unauthorized` op een MovieVault-call vraagt DiscVault de actieve
token-provider maximaal een keer om recovery:

1. token vernieuwen met dezelfde `movievault_instance_id`;
2. opslag van het nieuwe token;
3. retry van de oorspronkelijke request.

Bij `403 instance_revoked` tijdens handshake markeert DiscVault de koppeling als
ingetrokken, verwijdert het lokale token en stopt automatische recovery.

## Logging

DiscVault logt MovieVault-events zonder secrets, signatures of volledige tokens.
Toegestaan zijn onder andere `instanceId`, `instanceName`, `tokenPrefix`,
`requestedScopes`, `handshakeStatus`, `rotated`, `httpStatus` en MovieVault
foutcodes.
