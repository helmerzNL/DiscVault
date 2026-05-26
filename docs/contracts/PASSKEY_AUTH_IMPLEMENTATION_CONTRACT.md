# Passkey Auth Implementation Contract

Dit contract beschrijft hoe de passkey-authenticatie in DiscVault is opgezet,
zodat dezelfde aanpak door een andere applicatie kan worden geimplementeerd.

De implementatie gebruikt WebAuthn/passkeys voor de browser-authenticatie en geeft
na een succesvolle registratie of login een normale server-side JWT uit voor alle
verdere API-calls.

## Doel

De app moet zonder wachtwoorden kunnen werken:

- De browser maakt en bewaart de private passkey via `navigator.credentials`.
- De server bewaart alleen de publieke sleutel van de passkey.
- De server verifieert WebAuthn-attestation bij registratie.
- De server verifieert WebAuthn-assertion bij login.
- Na succesvolle WebAuthn-verificatie geeft de server een JWT uit.
- De frontend stuurt die JWT mee als `Authorization: Bearer <token>`.
- Als auth is uitgeschakeld, blijft de app bruikbaar zonder login.
- Zodra de eerste passkey is geregistreerd, wordt authenticatie ingeschakeld.

## Belangrijke ontwerpkeuzes

1. Gebruik geen wachtwoorden.
2. Bewaar nooit private keys op de server.
3. Gebruik WebAuthn alleen voor registratie en login.
4. Gebruik daarna een korte JWT-sessie voor normale API-authenticatie.
5. Sla challenges server-side op en maak ze single-use.
6. Controleer altijd `challenge`, `origin`, `type`, `rpIdHash` en signature.
7. Maak de eerste geregistreerde gebruiker automatisch `admin`.
8. Geef bij de eerste registratie een recovery code uit.
9. Laat recovery bestaande passkeys verwijderen, waarna de gebruiker opnieuw een passkey moet registreren.
10. Zet `auth_enabled` pas aan wanneer er minimaal een credential bestaat.

## Benodigde backend dependencies

DiscVault gebruikt in Python:

```txt
Flask
PyJWT
cbor2
cryptography
```

`cbor2` wordt gebruikt om `attestationObject` en COSE public keys uit WebAuthn te
parsen. `cryptography` wordt gebruikt om ES256 signatures te verifieren.

## Configuratie

De app heeft minimaal deze configuratie nodig:

| Naam | Voorbeeld | Betekenis |
| --- | --- | --- |
| `JWT_SECRET` | random 32+ bytes secret | Signen van JWT-sessies |
| `RP_ID` | `example.com` of `localhost` | WebAuthn relying party id |
| `RP_NAME` | `DiscVault` | Naam die de browser toont bij passkey-registratie |
| `RP_ORIGIN` | `https://app.example.com` | Toegestane browser origin |
| `RP_ORIGINS` | `https://app.example.com,https://www.example.com` | Meerdere toegestane origins |

Regels:

- `RP_ID` moet overeenkomen met het domein waarop de passkey geldig is.
- `RP_ORIGIN(S)` moeten exact overeenkomen met de browser-origin, inclusief scheme.
- WebAuthn werkt in productie alleen betrouwbaar op HTTPS.
- `localhost` mag voor lokale ontwikkeling.
- Sla `JWT_SECRET` buiten git op.

## Database contract

### `settings`

Een bestaande key-value settings tabel is voldoende.

Verplichte key:

```txt
auth_enabled = "true" | "false"
registration_enabled = "true" | "false"
```

Gedrag:

- Bij een nieuwe installatie staat `auth_enabled` op `"false"`.
- Na succesvolle eerste passkey-registratie wordt dit `"true"`.
- Als de laatste credential wordt verwijderd, wordt dit `"false"`.
- Auth mag niet handmatig worden ingeschakeld als er geen credentials bestaan.
- `registration_enabled` bepaalt of nieuwe users zichzelf mogen registreren.
- Als `registration_enabled = "false"`, is registratie invite-only.
- Als invite-only actief is, toont de frontend op het login-scherm alleen login,
  plus een aparte "Register with invite code" flow wanneer de backend aangeeft dat
  registratie gesloten is maar invite-registratie mogelijk is.

### `users`

```sql
CREATE TABLE users (
    id            TEXT PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    display_name  TEXT,
    role          TEXT NOT NULL DEFAULT 'user',
    recovery_hash TEXT,
    first_name    TEXT,
    last_name     TEXT,
    avatar        TEXT,
    created_at    TEXT NOT NULL
);
```

Minimaal nodig:

- `id`: stabiele interne user id, bijvoorbeeld UUID hex.
- `username`: loginnaam.
- `display_name`: weergavenaam.
- `role`: minimaal `admin` en `user`.
- `recovery_hash`: SHA-256 hash van de eenmalige recovery code.
- `created_at`: audit/ordering.

De eerste gebruiker wordt automatisch `admin`. Latere gebruikers worden `user`,
tenzij een aparte admin/user-management flow dit wijzigt.

### `credentials`

```sql
CREATE TABLE credentials (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    public_key      BLOB NOT NULL,
    sign_count      INTEGER NOT NULL DEFAULT 0,
    credential_name TEXT,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
```

Betekenis:

- `id`: base64url credential id uit de authenticator.
- `user_id`: eigenaar van de passkey.
- `public_key`: rauwe COSE public key bytes uit `attestationObject`.
- `sign_count`: teller uit authenticator data.
- `credential_name`: label zoals "iPhone", "Windows Hello", "YubiKey".
- `created_at`: audit/ordering.

### `challenges`

DiscVault gebruikt een database-backed challenge store zodat meerdere server workers
dezelfde pending challenges kunnen lezen.

```sql
CREATE TABLE challenges (
    key        TEXT PRIMARY KEY,
    challenge  BLOB,
    created_at REAL
);
```

Gedrag:

- Maak challenges met minimaal 32 random bytes.
- Bewaar challenges maximaal 5 minuten.
- Verwijder verlopen challenges bij opslaan.
- Gebruik `INSERT OR REPLACE` per challenge key.
- Bij verify wordt de challenge gelezen en direct verwijderd.
- Een challenge is dus single-use.

Challenge keys:

- Registratie: gebruik `user_id` als key.
- Login: gebruik een vaste key zoals `_login` of, beter, een sessiegebonden key als de app parallelle login attempts moet ondersteunen.

### `invite_codes`

DiscVault gebruikt invite codes om registratie invite-only te kunnen maken.

```sql
CREATE TABLE invite_codes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code_hash   TEXT NOT NULL UNIQUE,
    username    TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    used_at     TEXT,
    used_by     TEXT
);
```

Betekenis:

- `code_hash`: SHA-256 hash van de invite code zonder streepjes.
- `username`: de username waarvoor deze invite geldig is.
- `created_by`: user id van de admin die de invite maakte.
- `created_at`: aanmaakmoment.
- `expires_at`: verloopmoment, in DiscVault standaard 48 uur.
- `used_at`: moment waarop de invite is gebruikt.
- `used_by`: user id die met deze invite is aangemaakt.

Regels:

- Bewaar nooit de invite code zelf, alleen de hash.
- Toon de invite code alleen direct na aanmaken.
- Maak invite codes single-use.
- Koppel een invite code aan een specifieke username.
- Verwijder streepjes en normaliseer naar uppercase voordat je de hash berekent.
- Accepteer een invite alleen als `used_at IS NULL` en `expires_at > now`.
- Markeer de invite als gebruikt na succesvolle passkey-registratie.

## API contract

Alle response-binary velden voor WebAuthn worden als base64url zonder padding
verstuurd. De frontend zet ze om naar `ArrayBuffer` voordat `navigator.credentials`
wordt aangeroepen.

### `GET /api/auth/status`

Doel: frontend laten bepalen of login nodig is en welke auth UI getoond moet worden.

Response:

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

Regels:

- Dit endpoint is publiek.
- Als er een geldige JWT is, mag `role` van de huidige user worden teruggegeven.
- Zonder JWT is `role` `null`.
- `registration_enabled` bepaalt het gedrag van het login-scherm:
  - `true`: registratieknop tonen.
  - `false` met bestaande users: alleen login tonen, plus invite-registratie als de app invite codes ondersteunt.
  - geen users: eerste setup altijd toestaan, ook als registratie later invite-only wordt.

### `GET /api/settings/registration`

Doel: admin panel laten zien of open registratie aan staat.

Regels:

- Alleen admin.
- Returnt de huidige registratie-instelling.

Response:

```json
{
  "registration_enabled": false
}
```

### `POST /api/settings/registration`

Doel: admin kan open registratie aan- of uitzetten.

Request:

```json
{
  "registration_enabled": false
}
```

Regels:

- Alleen admin.
- `true`: iedereen die het login-scherm ziet kan een account/passkey registreren.
- `false`: nieuwe accounts mogen alleen via invite code worden aangemaakt.
- Als dit uit staat, toont de frontend standaard alleen de login-flow. De register-flow
  mag alleen zichtbaar worden als "Register with invite code" en moet dan een invite
  code veld tonen.

Response:

```json
{
  "registration_enabled": false
}
```

### `POST /api/auth/invite`

Doel: admin maakt een tijdelijke invite code voor een nieuwe gebruiker.

Request:

```json
{
  "username": "newuser"
}
```

Backend stappen:

1. Alleen admin toestaan.
2. Controleer dat `username` gevuld is.
3. Controleer dat er nog geen user met deze username bestaat.
4. Genereer een random code van 12 tekens.
5. Gebruik een alfabet zonder verwarrende tekens, bijvoorbeeld:

```txt
ABCDEFGHJKLMNPQRSTUVWXYZ23456789
```

6. Format de code voor weergave als `XXXX-XXXX-XXXX`.
7. Verwijder intern de streepjes voordat je hasht.
8. Hash de uppercase code met SHA-256.
9. Sla alleen `code_hash`, `username`, `created_by`, `created_at`, `expires_at` op.
10. Geef de leesbare code eenmalig terug aan de admin.

Response:

```json
{
  "id": 12,
  "code": "ABCD-EFGH-JKLM",
  "username": "newuser",
  "expires_at": "2026-05-28T12:00:00"
}
```

Belangrijk:

- De code kan later niet opnieuw worden opgehaald, want alleen de hash staat in de database.
- De admin UI moet daarom direct een kopieerknop tonen.
- De admin moet de code zelf aan de gebruiker doorgeven.

### `GET /api/auth/invite`

Doel: admin kan bestaande invites zien.

Response:

```json
[
  {
    "id": 12,
    "username": "newuser",
    "created_at": "2026-05-26T12:00:00",
    "expires_at": "2026-05-28T12:00:00",
    "used_at": null
  }
]
```

Regels:

- Alleen admin.
- Geef de invite code zelf niet terug.
- Toon in de frontend status: active, used of expired.

### `DELETE /api/auth/invite/{invite_id}`

Doel: admin kan een invite intrekken.

Regels:

- Alleen admin.
- Verwijder de invite row.
- Een ingetrokken code kan niet meer worden gebruikt.

Response:

```json
{
  "status": "deleted"
}
```

### `POST /api/auth/register/options`

Doel: server maakt een WebAuthn creation challenge.

Request:

```json
{
  "username": "admin",
  "display_name": "Admin",
  "invite_code": "OPTIONEEL"
}
```

Backend stappen:

1. Lees `username`, default eventueel naar `admin`.
2. Zoek of de user al bestaat.
3. Als de user niet bestaat, genereer alvast een nieuwe `user_id`.
4. Controleer registratiebeleid:
   - Eerste user mag altijd registreren.
   - Bestaande ingelogde user mag extra passkey registreren.
   - Als registratie gesloten is, eis een geldige invite code.
   - De invite code moet horen bij dezelfde `username`.
   - De invite code mag niet verlopen of al gebruikt zijn.
5. Haal bestaande credentials van deze user op.
6. Genereer 32 random bytes challenge.
7. Sla challenge op met key `user_id`.
8. Geef WebAuthn creation options terug.

Response:

```json
{
  "user_id": "uuidhex",
  "username": "admin",
  "options": {
    "rp": {
      "name": "AppName",
      "id": "example.com"
    },
    "user": {
      "id": "base64url-user-id",
      "name": "admin",
      "displayName": "Admin"
    },
    "challenge": "base64url-challenge",
    "pubKeyCredParams": [
      { "type": "public-key", "alg": -7 }
    ],
    "timeout": 60000,
    "authenticatorSelection": {
      "residentKey": "preferred",
      "userVerification": "preferred"
    },
    "excludeCredentials": [
      { "type": "public-key", "id": "base64url-credential-id" }
    ],
    "attestation": "none"
  }
}
```

DiscVault accepteert ES256 (`alg = -7`). Als de andere app ook RSA of EdDSA wil
ondersteunen, moet de COSE parser en signature verifier daarvoor expliciet worden
uitgebreid.

### `POST /api/auth/register/verify`

Doel: browser-attestation controleren en credential opslaan.

Request:

```json
{
  "user_id": "uuidhex",
  "username": "admin",
  "display_name": "Admin",
  "credential_name": "Windows Hello",
  "invite_code": "OPTIONEEL",
  "credential": {
    "id": "browser-credential-id",
    "rawId": "base64url-raw-id",
    "type": "public-key",
    "authenticatorAttachment": "platform",
    "response": {
      "attestationObject": "base64url-attestation-object",
      "clientDataJSON": "base64url-client-data-json"
    }
  }
}
```

Backend verificatie:

1. Haal en verwijder de pending challenge met key `user_id`.
2. Decodeer `clientDataJSON`.
3. Controleer `clientDataJSON.type == "webauthn.create"`.
4. Decodeer `clientDataJSON.challenge` en vergelijk exact met de server challenge.
5. Controleer `clientDataJSON.origin` tegen `RP_ORIGINS`.
6. Decodeer `attestationObject` met CBOR.
7. Lees `authData` uit het attestation object.
8. Parse authenticator data:
   - bytes 0-31: `rp_id_hash`
   - byte 32: flags
   - bytes 33-36: `sign_count`
   - bytes 37-52: AAGUID
   - bytes 53-54: credential id length
   - daarna: credential id
   - daarna: COSE public key
9. Bewaar credential id als base64url.
10. Bewaar de COSE public key bytes als `public_key`.
11. Bewaar `sign_count`.
12. Als user nog niet bestaat:
    - maak user aan.
    - eerste user krijgt `role = "admin"`.
    - genereer recovery code, bijvoorbeeld 8 uppercase hex chars.
    - bewaar SHA-256 hash van recovery code in `recovery_hash`.
    - als er een invite code is gebruikt, markeer die invite als gebruikt.
13. Sla de credential op in `credentials`.
14. Zet `auth_enabled = "true"`.
15. Geef een JWT terug.
16. Geef de recovery code alleen terug bij nieuwe user creatie.

Response:

```json
{
  "status": "ok",
  "token": "jwt",
  "recovery_code": "A1B2C3D4"
}
```

Let op:

- De recovery code wordt slechts eenmalig zichtbaar gemaakt.
- De server bewaart alleen de hash.

### `POST /api/auth/login/options`

Doel: server maakt een WebAuthn login challenge.

Request:

```json
{}
```

Backend stappen:

1. Haal alle credential ids op uit `credentials`.
2. Genereer 32 random bytes challenge.
3. Sla challenge op met login-key.
4. Geef WebAuthn request options terug.

Response:

```json
{
  "options": {
    "challenge": "base64url-challenge",
    "timeout": 60000,
    "rpId": "example.com",
    "allowCredentials": [
      { "type": "public-key", "id": "base64url-credential-id" }
    ],
    "userVerification": "preferred"
  }
}
```

### `POST /api/auth/login/verify`

Doel: assertion signature controleren en JWT uitgeven.

Request:

```json
{
  "credential": {
    "id": "base64url-credential-id",
    "rawId": "base64url-raw-id",
    "type": "public-key",
    "authenticatorAttachment": "platform",
    "response": {
      "authenticatorData": "base64url-authenticator-data",
      "clientDataJSON": "base64url-client-data-json",
      "signature": "base64url-signature",
      "userHandle": "base64url-user-handle-or-null"
    }
  }
}
```

Backend verificatie:

1. Haal en verwijder de pending login challenge.
2. Zoek credential op met `credential.id`.
3. Decodeer `clientDataJSON`.
4. Controleer `clientDataJSON.type == "webauthn.get"`.
5. Controleer challenge exact.
6. Controleer origin tegen `RP_ORIGINS`.
7. Decodeer `authenticatorData`.
8. Controleer `rpIdHash == SHA256(RP_ID)`.
9. Decodeer signature.
10. Maak `client_data_hash = SHA256(clientDataJSON bytes)`.
11. Verifieer signature over:

```txt
authenticatorData || client_data_hash
```

12. Gebruik de opgeslagen COSE public key.
13. Parse nieuwe `sign_count`.
14. Update `credentials.sign_count`.
15. Zoek bijbehorende user.
16. Geef JWT terug.

Response:

```json
{
  "status": "ok",
  "token": "jwt",
  "username": "admin"
}
```

DiscVault update `sign_count` naar de nieuwe waarde. Een strengere implementatie kan
ook clone-detectie toevoegen: als de nieuwe teller lager is dan de opgeslagen teller,
log dit als security warning of blokkeer de login.

### `GET /api/auth/credentials`

Doel: passkeys tonen in security/profile settings.

Response:

```json
[
  {
    "id": "base64url-credential-id",
    "credential_name": "Windows Hello",
    "created_at": "2026-05-26T12:00:00",
    "sign_count": 12,
    "username": "admin"
  }
]
```

Regels:

- Ingelogde niet-admin ziet alleen eigen credentials.
- Admin mag alle credentials zien.

### `DELETE /api/auth/credentials/{credential_id}`

Doel: passkey verwijderen.

Gedrag:

1. Controleer rechten.
2. Niet-admin mag alleen eigen credential verwijderen.
3. Verwijder credential.
4. Tel overgebleven credentials.
5. Als er geen credentials meer zijn, zet `auth_enabled = "false"`.

Response:

```json
{
  "status": "deleted",
  "remaining": 1
}
```

### `POST /api/auth/toggle`

Doel: admin kan auth in- of uitschakelen.

Request:

```json
{
  "enabled": true
}
```

Regels:

- Alleen admin.
- Inschakelen mag alleen als er minimaal een credential bestaat.
- Uitschakelen laat credentials bestaan, maar beschermt API routes niet meer.

Response:

```json
{
  "auth_enabled": true
}
```

### `POST /api/auth/recovery`

Doel: gebruiker kan met recovery code terug binnenkomen als alle passkeys verloren
zijn.

Request:

```json
{
  "username": "admin",
  "recovery_code": "A1B2C3D4"
}
```

Backend stappen:

1. Zoek user op `username`.
2. Hash aangeboden recovery code met SHA-256.
3. Vergelijk met `users.recovery_hash`.
4. Als ongeldig: 401.
5. Als geldig:
   - genereer direct een nieuwe recovery code.
   - bewaar de hash van de nieuwe code.
   - verwijder alle credentials voor deze user.
   - geef JWT terug.
6. De gebruiker moet daarna een nieuwe passkey registreren.

Response:

```json
{
  "status": "ok",
  "token": "jwt",
  "new_recovery_code": "F1E2D3C4",
  "message": "Passkeys removed. Please register a new passkey."
}
```

## JWT contract

Payload:

```json
{
  "sub": "user-id",
  "usr": "username",
  "exp": "now + 24h"
}
```

Signing:

```txt
HS256 met JWT_SECRET
```

Gebruik:

- Frontend bewaart token lokaal.
- Iedere beschermde API-call krijgt:

```http
Authorization: Bearer <jwt>
```

- Als de backend `401` met `auth_required: true` teruggeeft, wist de frontend het token
  en toont opnieuw de login overlay.

## Global API middleware

Alle `/api/` routes zijn beschermd wanneer `auth_enabled = "true"`, behalve publieke
routes zoals:

- `/api/auth/*`
- `/api/health`
- publieke media endpoints die bewust zonder login beschikbaar moeten blijven

Middleware gedrag:

1. Lees `Authorization` header.
2. Als Bearer token aanwezig is, probeer JWT te verifieren.
3. Zet bij succes `current_user_id` en `current_username` in request context.
4. Laat publieke endpoints altijd door.
5. Als `auth_enabled = false`, laat request door.
6. Als auth aan staat en er is geen geldige user context, return:

```json
{
  "error": "Unauthorized",
  "auth_required": true
}
```

met HTTP 401.

## Frontend contract

### Base64url helpers

De server en frontend wisselen WebAuthn binary data uit als base64url strings.

Frontend heeft twee helpers nodig:

```js
function base64urlToBuffer(base64url) {
  let s = base64url.replace(/-/g, '+').replace(/_/g, '/');
  while (s.length % 4) s += '=';
  const raw = atob(s);
  const buf = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) buf[i] = raw.charCodeAt(i);
  return buf.buffer;
}

function bufferToBase64url(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  bytes.forEach(b => binary += String.fromCharCode(b));
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
```

### Login flow frontend

1. POST `/api/auth/login/options`.
2. Zet `options.challenge` om naar `ArrayBuffer`.
3. Zet elke `allowCredentials[].id` om naar `ArrayBuffer`.
4. Roep browser API aan:

```js
const assertion = await navigator.credentials.get({ publicKey: options });
```

5. Serialiseer response:

```js
const credential = {
  id: assertion.id,
  rawId: bufferToBase64url(assertion.rawId),
  response: {
    authenticatorData: bufferToBase64url(assertion.response.authenticatorData),
    clientDataJSON: bufferToBase64url(assertion.response.clientDataJSON),
    signature: bufferToBase64url(assertion.response.signature),
    userHandle: assertion.response.userHandle
      ? bufferToBase64url(assertion.response.userHandle)
      : null
  },
  type: assertion.type,
  authenticatorAttachment: assertion.authenticatorAttachment
};
```

6. POST naar `/api/auth/login/verify`.
7. Als response een token bevat:
   - bewaar token.
   - sluit login overlay.
   - initialiseer app.

### Registratie flow frontend

1. POST `/api/auth/register/options` met username en eventueel invite code.
2. Zet `options.challenge` om naar `ArrayBuffer`.
3. Zet `options.user.id` om naar `ArrayBuffer`.
4. Zet elke `excludeCredentials[].id` om naar `ArrayBuffer`.
5. Roep browser API aan:

```js
const attestation = await navigator.credentials.create({ publicKey: options });
```

6. Serialiseer response:

```js
const credential = {
  id: attestation.id,
  rawId: bufferToBase64url(attestation.rawId),
  response: {
    attestationObject: bufferToBase64url(attestation.response.attestationObject),
    clientDataJSON: bufferToBase64url(attestation.response.clientDataJSON)
  },
  type: attestation.type,
  authenticatorAttachment: attestation.authenticatorAttachment
};
```

7. POST naar `/api/auth/register/verify`.
8. Als response een token bevat:
   - bewaar token.
   - toon recovery code als die aanwezig is.
   - laad auth settings opnieuw.

### Fetch wrapper

De frontend moet alle API-calls automatisch voorzien van het token, behalve publieke
auth calls.

Pseudo-code:

```js
if (url.pathname.startsWith('/api/') && !isPublicAuthRoute(url.pathname)) {
  headers.Authorization = `Bearer ${authToken}`;
}
```

Publieke auth routes:

- `/api/auth/login/options`
- `/api/auth/login/verify`
- `/api/auth/register/options`
- `/api/auth/register/verify`
- `/api/auth/recovery`
- `/api/auth/status`

Bij HTTP 401 met `auth_required: true`:

1. Verwijder token uit local storage.
2. Wis huidige user state.
3. Toon login overlay.

## UI contract

Minimale schermen:

### Login overlay

Moet bevatten:

- Knop "Login with passkey".
- Optie "Register passkey" als eerste setup of als open registratie aan staat.
- Optie "Register with invite code" wanneer:
  - auth aan staat,
  - er al users bestaan,
  - `registration_enabled = false`,
  - en de app invite-only registratie ondersteunt.
- Een invite code veld in de register-flow wanneer invite-only actief is.
- Recovery login optie.

Frontend gedrag:

- Als `registration_enabled = true`, mag het registerformulier zonder invite code zichtbaar zijn.
- Als `registration_enabled = false`, mag de normale registerknop niet zichtbaar zijn.
- Als `registration_enabled = false`, mag alleen de invite-registerknop zichtbaar zijn.
- Als de gebruiker invite-register kiest, moet het formulier `username`,
  `credential_name` en `invite_code` vragen.
- De frontend stuurt dezelfde invite code mee naar:
  - `POST /api/auth/register/options`
  - `POST /api/auth/register/verify`
- Als beide calls niet dezelfde invite code krijgen, kan de backend de registratie weigeren.

### Security/profile settings

Moet bevatten:

- Auth status.
- Toggle om authenticatie in/uit te schakelen.
- Lijst van geregistreerde passkeys.
- Knop om extra passkey toe te voegen.
- Knop om passkey te verwijderen.
- Recovery code melding na eerste registratie of recovery.

### Admin settings

Optioneel, maar DiscVault ondersteunt:

- Users tonen.
- Rollen wijzigen.
- User verwijderen.
- Passkey reset voor user.
- Registratie open/dicht zetten.
- Invite code genereren.
- Invite codes tonen met status active/used/expired.
- Invite code intrekken.

Invite-only admin UI:

- Voeg een toggle toe: "Allow new registrations" of "Open registration".
- Aan: nieuwe gebruikers kunnen zichzelf registreren vanaf het login-scherm.
- Uit: alleen bestaande gebruikers kunnen inloggen; nieuwe gebruikers hebben een invite code nodig.
- Voeg een invite code paneel toe waarin de admin:
  - een username invult,
  - een invite code aanmaakt,
  - de eenmalige code kan kopieren,
  - de vervaltijd ziet,
  - actieve invites kan intrekken.
- Toon gebruikte/verlopen invites zonder de geheime code zelf.

## Related Origins

DiscVault heeft een endpoint:

```http
GET /.well-known/webauthn
```

Response:

```json
{
  "origins": [
    "https://app.example.com",
    "https://www.example.com"
  ]
}
```

Gebruik dit wanneer passkeys tussen meerdere geconfigureerde origins gedeeld moeten
worden. Zet `Content-Type: application/json` en sta publieke toegang toe.

Als de frontend achter nginx draait, proxy `/.well-known/` naar de backend.

## Security checklist

Verplicht:

- [ ] WebAuthn alleen op HTTPS of localhost gebruiken.
- [ ] `JWT_SECRET` sterk en buiten git.
- [ ] Challenges minimaal 32 bytes random.
- [ ] Challenges single-use maken.
- [ ] Challenges laten verlopen na korte tijd, bijvoorbeeld 5 minuten.
- [ ] `clientDataJSON.type` controleren.
- [ ] Challenge exact controleren.
- [ ] Origin exact controleren tegen allowlist.
- [ ] `rpIdHash` controleren bij login.
- [ ] Signature controleren met opgeslagen public key.
- [ ] Alleen public keys opslaan.
- [ ] Recovery codes alleen gehashed opslaan.
- [ ] Recovery codes maar eenmalig tonen.
- [ ] Auth niet inschakelen zonder credential.
- [ ] Laatste credential verwijderen schakelt auth uit of blokkeert verwijdering.

Aanbevolen:

- [ ] Clone-detectie op `sign_count`.
- [ ] Audit logging voor login, registratie, recovery en credential deletion.
- [ ] Rate limiting op login/recovery endpoints.
- [ ] Per-browser/session challenge keys in plaats van een globale `_login` key.
- [ ] Token expiry kort houden, bijvoorbeeld 24 uur.
- [ ] Admin-only endpoints altijd via centrale middleware of decorator beschermen.

## Implementatievolgorde voor een andere app

1. Voeg backend dependencies toe.
2. Voeg configuratie toe voor `JWT_SECRET`, `RP_ID`, `RP_NAME`, `RP_ORIGIN(S)`.
3. Maak database-tabellen `users`, `credentials`, `challenges` en settings key `auth_enabled`.
4. Implementeer base64url helpers op backend.
5. Implementeer challenge store:
   - create/store
   - pop/delete
   - cleanup expired
6. Implementeer WebAuthn parsers:
   - attestation object CBOR decode
   - authenticator data parser
   - COSE ES256 public key parser
   - signature verifier
7. Implementeer JWT helpers:
   - create token
   - verify token
8. Implementeer global auth middleware.
9. Implementeer `/api/auth/status`.
10. Implementeer registratie setting endpoints.
11. Implementeer invite code tabel en invite endpoints.
12. Implementeer registratie options endpoint met invite-validatie.
13. Implementeer registratie verify endpoint met invite-validatie en invite-consumptie.
14. Implementeer login options endpoint.
15. Implementeer login verify endpoint.
16. Implementeer credentials list/delete.
17. Implementeer auth toggle.
18. Implementeer recovery login.
19. Bouw frontend base64url helpers.
20. Bouw frontend login flow met `navigator.credentials.get`.
21. Bouw frontend registratie flow met `navigator.credentials.create`.
22. Bouw invite-only login/register UI.
23. Bouw admin toggle voor open registratie.
24. Bouw admin invite code paneel.
25. Bouw fetch wrapper met Bearer token injectie.
26. Bouw login overlay en security settings.
27. Test met:
    - nieuwe installatie
    - eerste admin passkey
    - logout/login
    - extra passkey
    - open registratie aan
    - open registratie uit
    - invite code aanmaken
    - invite code gebruiken op login-scherm
    - invite code hergebruiken
    - verlopen invite code
    - invite voor verkeerde username
    - credential deletion
    - recovery code
    - verlopen/ongeldig token
    - verkeerde origin

## Acceptatiecriteria

De implementatie is functioneel gelijkwaardig aan DiscVault wanneer:

1. Een nieuwe installatie zonder auth opent.
2. De eerste passkey registratie een admin-user maakt.
3. De eerste registratie `auth_enabled` aanzet.
4. De server alleen public key materiaal opslaat.
5. Login zonder wachtwoord werkt via passkey.
6. API-calls na login een JWT Bearer token gebruiken.
7. Beschermde API-calls zonder geldige JWT een 401 krijgen.
8. Recovery verwijdert bestaande passkeys en geeft een tijdelijke ingelogde sessie.
9. De gebruiker na recovery een nieuwe passkey kan registreren.
10. Een verkeerde challenge, origin, RP ID hash of signature wordt geweigerd.
11. Een verwijderde credential niet meer kan inloggen.
12. Auth niet kan worden ingeschakeld zonder credential.
13. Een admin open registratie kan aan- en uitzetten.
14. Als open registratie uit staat, toont de frontend geen normale registratie-flow.
15. Als open registratie uit staat, kan een nieuwe user alleen met geldige invite code registreren.
16. Een invite code is username-gebonden, tijdelijk en single-use.
17. De invite code wordt alleen eenmalig aan de admin getoond en daarna alleen gehashed bewaard.

## DiscVault referentiepunten

In DiscVault staat de hoofdimplementatie in:

- `app/backend/app.py`
  - WebAuthn helpers
  - JWT middleware
  - `/api/auth/*` endpoints
  - recovery flow
- `app/backend/config.py`
  - `JWT_SECRET`
  - `RP_ID`
  - `RP_NAME`
  - `RP_ORIGIN(S)`
- `app/frontend/js/auth.js`
  - frontend passkey login
  - frontend passkey registration
  - token storage
  - fetch wrapper
  - recovery login
- `app/frontend/nginx.conf`
  - proxy voor `/.well-known/webauthn`
- `app/backend/requirements.txt`
  - `PyJWT`, `cbor2`, `cryptography`

## Niet overnemen zonder bewuste keuze

DiscVault gebruikt een simpele globale login challenge key (`_login`). Dat werkt
voor normale app-login, maar een drukke multi-user app kan beter een sessiegebonden
challenge key gebruiken, bijvoorbeeld gekoppeld aan een tijdelijke cookie of request
id.

DiscVault bewaart de JWT in browser local storage. Dat is praktisch voor een PWA,
maar een andere app kan ook kiezen voor een `HttpOnly; Secure; SameSite` cookie als
dat beter past bij het dreigingsmodel.

DiscVault ondersteunt in deze passkey-parser ES256. Als de andere app authenticators
wil ondersteunen die een ander algoritme gebruiken, moet dat expliciet worden
toegevoegd aan `pubKeyCredParams`, COSE parsing en signature verification.
