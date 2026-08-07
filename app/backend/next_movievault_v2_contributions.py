"""Signed contributions to MovieVault v2.

When a barcode misses everywhere and MovieVault's resolver answers with a
candidate list, the user picks the pressing they are holding. MovieVault learns
nothing from that: a ``candidates`` answer deliberately creates no moderation
candidate, because nothing was confirmed. This module carries the choice back,
so the next person who scans the same disc gets a canonical hit.

Three things are deliberately kept apart from the rest of the v2 client:

*The identity.* ``next_movievault_connection`` holds an Ed25519 key registered
with the *previous* MovieVault under a ``dvpk_`` key id. MovieVault v2 requires a
key id that parses as a UUID, so that identity cannot be reused and this module
registers its own.

*The transport.* ``next_movievault_v2._release_details_http`` is anonymous by
design - the resolver is cookie-free and carries no ``Authorization`` header, and
it must stay that way. Signed writes get their own opener rather than a flag on
that one.

*The timing.* Nothing here is called from a request handler. A signed,
attributable write firing in the same second as an anonymous resolve would tie
the two together for anyone watching the connection; a queued job breaks that,
and it also keeps a MovieVault outage out of the user's save.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

try:
    from .next_movievault_connection import (
        _b64url,
        _decrypt_secret_value,
        _delete_setting,
        _encrypt_secret_value,
        _set_setting,
        _setting_value,
        _table_exists,
        _text,
    )
    from .next_movievault_v2 import (
        DEFAULT_MAX_RELEASE_DETAILS_BYTES,
        MovieVaultV2Error,
        _NoRedirect,
        enforced_origin,
    )
    from .versioning import backend_version
except ImportError:  # pragma: no cover - supports running modules directly
    from next_movievault_connection import (
        _b64url,
        _decrypt_secret_value,
        _delete_setting,
        _encrypt_secret_value,
        _set_setting,
        _setting_value,
        _table_exists,
        _text,
    )
    from next_movievault_v2 import (
        DEFAULT_MAX_RELEASE_DETAILS_BYTES,
        MovieVaultV2Error,
        _NoRedirect,
        enforced_origin,
    )
    from versioning import backend_version

CONTRIBUTION_PROTOCOL = "contribution-1"
FIELD_CORRECTION_PROTOCOL = "contribution-2"
RELEASE_TECHNICAL_ENTITY = "release_technical"
REGISTER_PATH = "/v2/contributions/register"
SUBMIT_PATH = "/v2/contributions"
ROTATE_PATH = "/v2/contributions/keys/rotate"

#: Admin gate. Off by default: a self-hosted instance does not start sending
#: anything outward because it was upgraded.
CONTRIBUTION_ENABLED_KEY = "movievault_v2_contribution_enabled"
INSTANCE_ID_KEY = "movievault_v2_contribution_instance_id"
KEY_ID_KEY = "movievault_v2_contribution_key_id"
REGISTERED_AT_KEY = "movievault_v2_contribution_registered_at"
LAST_ERROR_KEY = "movievault_v2_contribution_last_error"
TOKEN_KEY = "movievault_v2_contribution_token"
PRIVATE_KEY_KEY = "movievault_v2_contribution_private_key"

IDENTITY_KEYS = (
    INSTANCE_ID_KEY,
    KEY_ID_KEY,
    REGISTERED_AT_KEY,
    LAST_ERROR_KEY,
    TOKEN_KEY,
    PRIVATE_KEY_KEY,
)

DEFAULT_TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 64 * 1024

#: MovieVault answers a submission it cannot use with a stable code. Only some
#: of them describe a condition that a later attempt could find changed.
RETRYABLE_ERROR_CODES = frozenset(
    {
        "rate_limited",
        "contribution_rate_limit_unavailable",
    }
)
#: Codes that mean the payload itself is wrong. Retrying is guaranteed to fail
#: identically, and the loud failure is the point: it is a mapping bug here.
TERMINAL_ERROR_CODES = frozenset(
    {
        "payload_invalid",
        "provider_content_forbidden",
        "media_type_invalid",
        "payload_too_large",
        "content_length_invalid",
        "idempotency_conflict",
        "asset_id_conflict",
    }
)


class MovieVaultContributionError(MovieVaultV2Error):
    """A contribution could not be delivered.

    Carries ``retryable`` because the caller cannot re-derive it: a refused
    connection proves the request never arrived, while a timeout proves nothing
    - MovieVault may have accepted and stored it already.
    """

    def __init__(self, code: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.status = status


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _nonce() -> str:
    # 43 characters, inside MovieVault's 24..120 bound, base64url alphabet.
    return secrets.token_urlsafe(32)


def canonical_json(payload: Any) -> bytes:
    """The exact bytes that get signed and sent.

    Serialized once. The signature covers the body verbatim, so anything that
    re-serializes between signing and sending silently invalidates it.
    """
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def idempotency_key(payload: dict[str, Any], *, prefix: str = "dv-rt1") -> str:
    """A key derived from the payload, so a repeat is recognised as one.

    Scanning the same disc twice and picking the same edition produces the same
    bytes and therefore the same key, and MovieVault returns the original
    contribution instead of queueing a second review. Picking a *different*
    edition is different content and legitimately a new contribution.

    The prefix names the shape the digest was taken over. Two entity types
    sharing one prefix would produce keys from one namespace for payloads that
    are not comparable, and the uniqueness is per instance -- so a collision
    would return somebody's unrelated contribution as "the original".
    """
    digest = hashlib.sha256(canonical_json(payload)).hexdigest()
    return f"{prefix}-{digest[:56]}"


def source_reference(scanned_barcode: str, release_ref: str) -> dict[str, str]:
    """A non-personal reference, inside MovieVault's ``[A-Za-z0-9_.:-]`` bound.

    The provider's own ``releaseRef`` is free text up to 500 characters and is
    not sent: it would break the key pattern and leak provider-shaped data into
    a field meant to be opaque.
    """
    barcode_hash = hashlib.sha256(scanned_barcode.encode("ascii")).hexdigest()[:16]
    ref_hash = hashlib.sha256((release_ref or "manual").encode("utf-8")).hexdigest()[:16]
    return {"type": "discvault_release", "key": f"rt1.{barcode_hash}.{ref_hash}"}


def field_correction_source_reference(entity_type: str, entity_id: str) -> dict[str, str]:
    """A non-personal reference for a correction.

    Keyed on the target rather than on a scanned barcode, because a correction
    does not come from a scan -- it comes from someone looking at a record they
    already have. The entity id is hashed rather than sent: it is not secret,
    but this field is meant to be opaque to everyone but the sender, and a
    readable id here would let the moderation queue be joined against the
    catalogue by anyone who could see both.
    """
    digest = hashlib.sha256(f"{entity_type}:{entity_id}".encode("ascii")).hexdigest()[:32]
    kind = "discvault_box_set" if entity_type == "box_set" else "discvault_release"
    return {"type": kind, "key": f"fc2.{digest}"}


def _signed_headers(private_key_pem: str, body: bytes) -> dict[str, str]:
    from cryptography.hazmat.primitives import serialization

    timestamp = _timestamp()
    nonce = _nonce()
    signed = timestamp.encode("utf-8") + b"." + nonce.encode("utf-8") + b"." + body
    private_key = serialization.load_pem_private_key(private_key_pem.encode("ascii"), password=None)
    return {
        "X-DiscVault-Timestamp": timestamp,
        "X-DiscVault-Nonce": nonce,
        "X-DiscVault-Signature": f"key-v1={_b64url(private_key.sign(signed))}",
    }


def _http(url: str, *, body: bytes, headers: dict[str, str], timeout_seconds: int) -> tuple[int, bytes]:
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "DiscVault-MovieVault-v2",
        **headers,
    }
    request = urllib.request.Request(url, data=body, method="POST", headers=request_headers)
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            return int(response.status), response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise MovieVaultContributionError("redirect_rejected") from exc
        return int(exc.code), exc.read(MAX_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (ConnectionRefusedError, socket.gaierror)):
            # The request provably never arrived, so repeating it is a first
            # delivery, not a possible duplicate.
            raise MovieVaultContributionError("contribution_unreachable", retryable=True) from exc
        # A timeout proves nothing: MovieVault may have stored this already.
        # The idempotency key makes a repeat safe anyway, which is the only
        # reason this is retryable at all.
        raise MovieVaultContributionError("contribution_network_error", retryable=True) from exc


def _http_get(url: str, *, headers: dict[str, str], timeout_seconds: int) -> tuple[int, bytes]:
    """The read-back half of the transport.

    Separate from `_http` rather than a `method=` parameter on it: a GET carries
    no body, and the signature covers the body, so the two differ in what is
    signed and not only in the verb. Collapsing them would put an empty-body
    special case inside the one function whose correctness is the signature.
    """
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "DiscVault-MovieVault-v2",
        **headers,
    }
    request = urllib.request.Request(url, method="GET", headers=request_headers)
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            return int(response.status), response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise MovieVaultContributionError("redirect_rejected") from exc
        return int(exc.code), exc.read(MAX_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        # A read has no side effect, so every transport failure here is simply
        # "ask again later" -- none of the ambiguity that makes the POST path
        # depend on an idempotency key.
        raise MovieVaultContributionError("contribution_network_error", retryable=True) from exc


def _error_code(status: int, content: bytes) -> str:
    try:
        parsed = json.loads(content.decode("utf-8"))
        code = parsed.get("error", {}).get("code")
        if isinstance(code, str) and code:
            return code
    except (ValueError, AttributeError):
        pass
    return f"http_{status}"


def _raise_for_status(status: int, content: bytes) -> dict[str, Any]:
    if 200 <= status < 300:
        try:
            return json.loads(content.decode("utf-8"))
        except ValueError as exc:
            raise MovieVaultContributionError("contribution_response_invalid") from exc
    code = _error_code(status, content)
    if code in TERMINAL_ERROR_CODES:
        raise MovieVaultContributionError(code, retryable=False, status=status)
    if code in RETRYABLE_ERROR_CODES or status >= 500:
        raise MovieVaultContributionError(code, retryable=True, status=status)
    raise MovieVaultContributionError(code, retryable=False, status=status)


# --- identity ---------------------------------------------------------------


def registration_state(conn) -> dict[str, Any]:
    """What the admin screen needs to show, without exposing any secret."""
    if not _table_exists(conn, "app_settings"):
        return {"registered": False}
    return {
        "registered": bool(
            _text(_setting_value(conn, KEY_ID_KEY, ""))
            and _text(_setting_value(conn, TOKEN_KEY, "", include_secret=True))
        ),
        "instanceId": _text(_setting_value(conn, INSTANCE_ID_KEY, "")),
        "keyId": _text(_setting_value(conn, KEY_ID_KEY, "")),
        "registeredAt": _text(_setting_value(conn, REGISTERED_AT_KEY, "")),
        "lastError": _text(_setting_value(conn, LAST_ERROR_KEY, "")),
    }


def _generate_key_pair() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return private_pem, _b64url(public_raw)


def ensure_registration(conn, *, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Register this instance with MovieVault once, and only once.

    MovieVault limits registration to 20 a day per address and returns the
    token exactly once, so this never loops: one attempt per call, and callers
    call it once per admin action or once per queued job.
    """
    existing = registration_state(conn)
    if existing.get("registered"):
        return existing

    private_pem, public_key = _generate_key_pair()
    body = canonical_json(
        {
            "protocolVersion": "contribution-registration-1",
            "sourceClient": "discvault",
            "sourceVersion": backend_version()[:80],
            "algorithm": "ed25519",
            "publicKey": public_key,
        }
    )
    status, content = _http(
        f"{enforced_origin()}{REGISTER_PATH}",
        body=body,
        headers=_signed_headers(private_pem, body),
        timeout_seconds=timeout_seconds,
    )
    parsed = _raise_for_status(status, content)

    instance_id = _text(parsed.get("instanceId"))
    key_id = _text(parsed.get("keyId"))
    token = _text(parsed.get("token"))
    if not instance_id or not key_id or not token:
        raise MovieVaultContributionError("contribution_registration_incomplete")

    _set_setting(conn, PRIVATE_KEY_KEY, _encrypt_secret_value(private_pem), is_secret=True)
    _set_setting(conn, TOKEN_KEY, _encrypt_secret_value(token), is_secret=True)
    _set_setting(conn, INSTANCE_ID_KEY, instance_id)
    _set_setting(conn, KEY_ID_KEY, key_id)
    _set_setting(conn, REGISTERED_AT_KEY, _timestamp())
    _set_setting(conn, LAST_ERROR_KEY, "")
    return registration_state(conn)


def reset_registration(conn) -> None:
    """Forget this instance's identity entirely.

    MovieVault cannot recover a lost token or key - no account is attached to
    the pseudonymous instance by design - so the only recovery is registering a
    new one. Deleting all six keys makes the next call do exactly that.
    """
    for key in IDENTITY_KEYS:
        _delete_setting(conn, key)


def rotate_key(conn, *, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Replace the signing key, proving possession of both old and new."""
    state = registration_state(conn)
    if not state.get("registered"):
        raise MovieVaultContributionError("contribution_not_registered")
    old_private = _decrypt_secret_value(_setting_value(conn, PRIVATE_KEY_KEY, "", include_secret=True))
    token = _decrypt_secret_value(_setting_value(conn, TOKEN_KEY, "", include_secret=True))
    instance_id = _text(_setting_value(conn, INSTANCE_ID_KEY, ""))
    key_id = _text(_setting_value(conn, KEY_ID_KEY, ""))

    from uuid import UUID

    from cryptography.hazmat.primitives import serialization

    new_private_pem, new_public_key = _generate_key_pair()
    new_private = serialization.load_pem_private_key(new_private_pem.encode("ascii"), password=None)
    proof_input = (
        b"movievault-key-rotation-v1."
        + UUID(instance_id).bytes
        + b"."
        + base64.urlsafe_b64decode(new_public_key + "=" * (-len(new_public_key) % 4))
    )
    body = canonical_json(
        {
            "protocolVersion": "contribution-key-rotation-1",
            "algorithm": "ed25519",
            "publicKey": new_public_key,
            "newKeyProof": _b64url(new_private.sign(proof_input)),
            "revokeCurrent": True,
        }
    )
    # Signed by the OLD key: the request itself still has to prove the current
    # identity, while `newKeyProof` proves the replacement is really ours.
    headers = _signed_headers(old_private, body)
    headers["Authorization"] = f"Bearer {token}"
    headers["X-DiscVault-Key-Id"] = key_id
    status, content = _http(
        f"{enforced_origin()}{ROTATE_PATH}",
        body=body,
        headers=headers,
        timeout_seconds=timeout_seconds,
    )
    parsed = _raise_for_status(status, content)
    new_key_id = _text(parsed.get("keyId")) or key_id
    _set_setting(conn, PRIVATE_KEY_KEY, _encrypt_secret_value(new_private_pem), is_secret=True)
    _set_setting(conn, KEY_ID_KEY, new_key_id)
    return registration_state(conn)


# --- submission -------------------------------------------------------------


def submit_release_technical(
    conn,
    payload: dict[str, Any],
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Send one contributed release, registering first if needed."""
    state = ensure_registration(conn, timeout_seconds=timeout_seconds)
    if not state.get("registered"):
        raise MovieVaultContributionError("contribution_not_registered")
    token = _decrypt_secret_value(_setting_value(conn, TOKEN_KEY, "", include_secret=True))
    key_id = _text(_setting_value(conn, KEY_ID_KEY, ""))
    private_pem = _decrypt_secret_value(_setting_value(conn, PRIVATE_KEY_KEY, "", include_secret=True))

    envelope = {
        "protocolVersion": CONTRIBUTION_PROTOCOL,
        "sourceClient": "discvault",
        "sourceVersion": backend_version()[:80],
        "idempotencyKey": idempotency_key(payload),
        "entityType": RELEASE_TECHNICAL_ENTITY,
        "sourceReference": source_reference(
            _text(payload.get("scannedBarcode")),
            _text(payload.get("_releaseRef")),
        ),
        "payload": {key: value for key, value in payload.items() if not key.startswith("_")},
    }
    body = canonical_json(envelope)
    if len(body) > DEFAULT_MAX_RELEASE_DETAILS_BYTES:
        raise MovieVaultContributionError("payload_too_large")
    headers = _signed_headers(private_pem, body)
    headers["Authorization"] = f"Bearer {token}"
    headers["X-DiscVault-Key-Id"] = key_id
    status, content = _http(
        f"{enforced_origin()}{SUBMIT_PATH}",
        body=body,
        headers=headers,
        timeout_seconds=timeout_seconds,
    )
    return _raise_for_status(status, content)


def submit_field_correction(
    conn,
    correction: dict[str, Any],
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Send one field correction, registering first if needed.

    Shares the identity, the signing, the transport and the error
    classification with `submit_release_technical` -- only the envelope differs,
    because only the envelope is what makes this contribution-2 rather than
    contribution-1. Two entity types, one registered instance: MovieVault keys
    reputation on the instance, so a second identity here would split one
    contributor into two.
    """
    state = ensure_registration(conn, timeout_seconds=timeout_seconds)
    if not state.get("registered"):
        raise MovieVaultContributionError("contribution_not_registered")
    token = _decrypt_secret_value(_setting_value(conn, TOKEN_KEY, "", include_secret=True))
    key_id = _text(_setting_value(conn, KEY_ID_KEY, ""))
    private_pem = _decrypt_secret_value(_setting_value(conn, PRIVATE_KEY_KEY, "", include_secret=True))

    target = correction.get("target") or {}
    entity_type = _text(target.get("entityType"))
    entity_id = _text(target.get("entityId"))
    changes = correction.get("changes") or []
    if not entity_type or not entity_id or not changes:
        raise MovieVaultContributionError("payload_invalid")

    envelope = {
        "protocolVersion": FIELD_CORRECTION_PROTOCOL,
        "sourceClient": "discvault",
        "sourceVersion": backend_version()[:80],
        "idempotencyKey": idempotency_key(
            {"target": target, "changes": changes},
            prefix="dv-fc2",
        ),
        "entityType": entity_type,
        "sourceReference": field_correction_source_reference(entity_type, entity_id),
        "target": {"entityId": entity_id, "baseRevision": int(target.get("baseRevision") or 0)},
        "changes": changes,
    }
    body = canonical_json(envelope)
    if len(body) > DEFAULT_MAX_RELEASE_DETAILS_BYTES:
        raise MovieVaultContributionError("payload_too_large")
    headers = _signed_headers(private_pem, body)
    headers["Authorization"] = f"Bearer {token}"
    headers["X-DiscVault-Key-Id"] = key_id
    status, content = _http(
        f"{enforced_origin()}{SUBMIT_PATH}",
        body=body,
        headers=headers,
        timeout_seconds=timeout_seconds,
    )
    return _raise_for_status(status, content)


# MovieVault's whole status vocabulary is pending, quarantined, accepted,
# partially_accepted and rejected (migration 0041). Only the last three are
# decisions. `quarantined` deliberately is not: a moderator may still review a
# quarantined contribution, so treating it as final would stop asking exactly
# when the answer is most likely to change. A duplicate is not a status here -
# it is an ordinary status plus a `duplicateOf` pointer.
TERMINAL_CONTRIBUTION_STATUSES = frozenset({"accepted", "partially_accepted", "rejected"})


def read_contribution(
    conn,
    contribution_id: str,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """What became of one contribution we sent.

    Unsigned, matching the route: a GET has no body, and the signature covers
    the body, so the bearer token is what proves possession here. The answer is
    scoped upstream to the submitting instance, so this cannot read anyone
    else's submission even by guessing an id.

    Does not register: a read-back for a contribution id we hold implies we
    registered to send it, and registering here would turn a lost token into a
    silent new identity that the original contribution does not belong to.
    """
    identifier = _text(contribution_id)
    if not identifier:
        raise MovieVaultContributionError("payload_invalid")
    token = _decrypt_secret_value(_setting_value(conn, TOKEN_KEY, "", include_secret=True))
    key_id = _text(_setting_value(conn, KEY_ID_KEY, ""))
    if not token or not key_id:
        raise MovieVaultContributionError("contribution_not_registered")
    status, content = _http_get(
        f"{enforced_origin()}{SUBMIT_PATH}/{urllib.parse.quote(identifier, safe='')}",
        headers={"Authorization": f"Bearer {token}", "X-DiscVault-Key-Id": key_id},
        timeout_seconds=timeout_seconds,
    )
    return _raise_for_status(status, content)


def field_correction_enabled(conn, user_id: Any = None) -> bool:
    """Whether this user's edits may be offered as corrections.

    The same two-gate composition as `release_contribution_enabled`, with a
    second user preference rather than a reuse of the first. Offering a disc you
    scanned and editing a record everyone else reads are different acts with
    different consequences, and one checkbox for both would mean consenting to
    the second by having agreed to the first.

    The owner flag is shared: it answers "does anything leave this instance at
    all", which is one question however many kinds of contribution exist.
    """
    gate = field_correction_gate(conn, user_id)
    return gate["owner"] and gate["user"]


def field_correction_gate(conn, user_id: Any = None) -> dict[str, bool]:
    """The two halves of the gate, separately.

    Composed they answer "may this be sent". Apart they answer "and who has to
    do something about it", which is a different question and the one a screen
    needs. Collapsing them into one boolean made a client offer the user a
    consent that could not possibly help: the owner flag was off, the user
    agreed to sharing, and the send was refused anyway with a message that read
    as though agreeing had not registered.

    An owner flag that is off is nothing the user can act on. A preference that
    is off is nothing anyone else can act on. One boolean cannot say which.
    """
    if not _table_exists(conn, "app_settings"):
        return {"owner": False, "user": False}
    enabled = _setting_value(conn, CONTRIBUTION_ENABLED_KEY, False)
    owner = enabled is True or _text(enabled).lower() in {"1", "true", "yes", "on"}
    try:
        from .next_preferences import app_effective_preferences
    except ImportError:  # pragma: no cover - supports running modules directly
        from next_preferences import app_effective_preferences
    preferences = app_effective_preferences(conn, user_id)
    return {"owner": owner, "user": bool(preferences.get("share_field_corrections"))}


def record_failure(conn, code: str) -> None:
    _set_setting(conn, LAST_ERROR_KEY, code)


def disable_after_block(conn) -> None:
    """A blocked instance must stop sending, visibly.

    MovieVault refuses everything from a blocked instance, so continuing to
    queue jobs would pile up failures nobody reads. The admin gate goes off and
    the reason is recorded where the owner screen shows it.
    """
    _set_setting(conn, CONTRIBUTION_ENABLED_KEY, False)
    _set_setting(conn, LAST_ERROR_KEY, "instance_blocked")


# --- the gate ---------------------------------------------------------------


def release_contribution_enabled(conn, user_id: Any = None) -> bool:
    """Whether this user's selections may be sent to MovieVault.

    Two gates, and the composition is deliberately not symmetric. The owner
    setting is the outer one: on a self-hosted instance the person running it
    decides whether anything leaves at all, and no user preference can override
    that. The user preference only narrows it further.

    The admin flag deliberately does NOT live in `APP_PREFERENCE_DEFAULTS`.
    `app_effective_preferences` is global-default-then-user-override, so a key
    placed there is by construction something a user can flip - which is the
    opposite of a gate.
    """
    if not _table_exists(conn, "app_settings"):
        return False
    enabled = _setting_value(conn, CONTRIBUTION_ENABLED_KEY, False)
    if not (enabled is True or _text(enabled).lower() in {"1", "true", "yes", "on"}):
        return False
    try:
        from .next_preferences import app_effective_preferences
    except ImportError:  # pragma: no cover - supports running modules directly
        from next_preferences import app_effective_preferences
    preferences = app_effective_preferences(conn, user_id)
    return bool(preferences.get("share_release_selections"))
