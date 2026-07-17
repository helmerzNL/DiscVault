"""Anonymous MovieVault v2 synchronization and local lookup bridge."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import nullcontext
from datetime import date, datetime, timezone
from typing import Any, Callable, ContextManager

try:
    from psycopg.types.json import Jsonb
except ModuleNotFoundError:  # pragma: no cover - allows policy tests without psycopg
    class Jsonb:  # type: ignore[no-redef]
        def __init__(self, value: Any) -> None:
            self.value = value


MOVIEVAULT_V2_PLUGIN_ID = "movievault_v2"
MOVIEVAULT_V2_CONTRACT = "distribution-2"
MOVIEVAULT_V3_CONTRACT = "distribution-3"
MOVIEVAULT_V4_CONTRACT = "distribution-4"
SUPPORTED_CONTRACTS = (MOVIEVAULT_V2_CONTRACT, MOVIEVAULT_V3_CONTRACT, MOVIEVAULT_V4_CONTRACT)
CONTRACT_PATH_VERSIONS = {
    MOVIEVAULT_V2_CONTRACT: "2",
    MOVIEVAULT_V3_CONTRACT: "3",
    MOVIEVAULT_V4_CONTRACT: "4",
}
SYNC_LOCK_KEY = 2_026_261
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_MAX_MANIFEST_BYTES = 64 * 1024
DEFAULT_MAX_ARTIFACT_BYTES = 128 * 1024 * 1024
DEFAULT_MAX_BUCKET_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 2_000_000
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COUNTRY_PATTERN = re.compile(r"^[A-Z]{2}$")
# NOTE: MovieVault public asset paths are deliberately `/v2/assets/...` for both
# distribution-3 and distribution-4 (the producer never versions this path); only
# the contract envelope itself is versioned.
ASSET_PATH_PATTERNS = {
    MOVIEVAULT_V2_CONTRACT: re.compile(r"^/v2/assets/[0-9a-f-]+/(thumbnail|display)$"),
    MOVIEVAULT_V3_CONTRACT: re.compile(r"^/v2/assets/[0-9a-f-]+/(thumbnail|display)$"),
    MOVIEVAULT_V4_CONTRACT: re.compile(r"^/v2/assets/[0-9a-f-]+/(thumbnail|display)$"),
}
POSTER_ASSET_TYPE = "front_cover"
POSTER_ATTESTATIONS = {"original", "licensed"}
POSTER_LICENSES = {"cc0-1.0", "cc-by-4.0", "cc-by-sa-4.0"}

ConnectionFactory = Callable[[], ContextManager[Any]]


class MovieVaultV2Error(RuntimeError):
    """Stable, value-free failure raised across the plugin boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def normalize_origin(value: Any) -> str:
    text = str(value or "").strip()
    try:
        parsed = urllib.parse.urlsplit(text)
        port = parsed.port
    except ValueError as exc:
        raise MovieVaultV2Error("origin_invalid") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise MovieVaultV2Error("origin_invalid")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{host}:{port}" if port is not None else host
    return f"{parsed.scheme.lower()}://{netloc}"


def _integer(value: Any, *, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise MovieVaultV2Error("record_invalid")
    if maximum is not None and value > maximum:
        raise MovieVaultV2Error("record_invalid")
    return value


def _optional_integer(
    value: Any,
    *,
    minimum: int,
    maximum: int,
) -> int | None:
    if value is None:
        return None
    return _integer(value, minimum=minimum, maximum=maximum)


def _text(value: Any, *, minimum: int = 0, maximum: int) -> str:
    if not isinstance(value, str) or len(value) < minimum or len(value) > maximum:
        raise MovieVaultV2Error("record_invalid")
    return value


def _optional_text(value: Any, *, maximum: int) -> str | None:
    if value is None:
        return None
    return _text(value, maximum=maximum)


def _uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise MovieVaultV2Error("record_invalid")
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise MovieVaultV2Error("record_invalid") from exc


def _hash(value: Any) -> str:
    if not isinstance(value, str) or not HASH_PATTERN.fullmatch(value):
        raise MovieVaultV2Error("record_invalid")
    return value


def _hashes(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > 25:
        raise MovieVaultV2Error("record_invalid")
    hashes = [_hash(item) for item in value]
    if len(set(hashes)) != len(hashes):
        raise MovieVaultV2Error("record_invalid")
    return hashes


def _exact_keys(
    value: dict[str, Any],
    *,
    required: set[str],
    optional: set[str],
) -> None:
    keys = set(value)
    if not required.issubset(keys) or keys - required - optional:
        raise MovieVaultV2Error("record_invalid")


def _asset_variant(value: Any, contract_version: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise MovieVaultV2Error("record_invalid")
    _exact_keys(value, required={"path", "checksum"}, optional=set())
    path = _text(value["path"], minimum=1, maximum=500)
    if not ASSET_PATH_PATTERNS[contract_version].fullmatch(path):
        raise MovieVaultV2Error("record_invalid")
    return {"path": path, "checksum": _hash(value["checksum"])}


def _assets(value: Any, contract_version: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise MovieVaultV2Error("record_invalid")
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise MovieVaultV2Error("record_invalid")
        _exact_keys(
            item,
            required={"assetId", "assetType", "attestation", "license", "thumbnail", "display"},
            optional=set(),
        )
        asset_type = item["assetType"]
        attestation = item["attestation"]
        license_name = item["license"]
        if asset_type not in {"front_cover", "back_cover", "disc", "booklet", "other"}:
            raise MovieVaultV2Error("record_invalid")
        if attestation not in {"original", "licensed"}:
            raise MovieVaultV2Error("record_invalid")
        if license_name not in {"cc0-1.0", "cc-by-4.0", "cc-by-sa-4.0"}:
            raise MovieVaultV2Error("record_invalid")
        result.append(
            {
                "assetId": _uuid(item["assetId"]),
                "assetType": asset_type,
                "attestation": attestation,
                "license": license_name,
                "thumbnail": _asset_variant(item["thumbnail"], contract_version),
                "display": _asset_variant(item["display"], contract_version),
            }
        )
    return result


def _poster(value: Any, contract_version: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise MovieVaultV2Error("record_invalid")
    _exact_keys(
        value,
        required={"assetId", "assetType", "attestation", "license", "thumbnail", "display"},
        optional=set(),
    )
    if value["assetType"] != POSTER_ASSET_TYPE:
        raise MovieVaultV2Error("record_invalid")
    attestation = value["attestation"]
    license_name = value["license"]
    if attestation not in POSTER_ATTESTATIONS:
        raise MovieVaultV2Error("record_invalid")
    if license_name not in POSTER_LICENSES:
        raise MovieVaultV2Error("record_invalid")
    return {
        "assetId": _uuid(value["assetId"]),
        "assetType": POSTER_ASSET_TYPE,
        "attestation": attestation,
        "license": license_name,
        "thumbnail": _asset_variant(value["thumbnail"], contract_version),
        "display": _asset_variant(value["display"], contract_version),
    }


def _release_record(value: dict[str, Any], contract_version: str) -> dict[str, Any]:
    required = {
        "contractVersion",
        "recordType",
        "operation",
        "revision",
        "releaseId",
        "filmId",
        "canonicalTitle",
        "providerIds",
        "releaseTitle",
        "eanHashes",
        "assets",
    }
    optional = {
        "releaseYear",
        "edition",
        "format",
        "region",
        "countryCode",
        "languageCode",
        "releaseDate",
        "discCount",
    }
    if contract_version in (MOVIEVAULT_V3_CONTRACT, MOVIEVAULT_V4_CONTRACT):
        optional.update({"studio", "distributor", "runtimeMinutes"})
    if contract_version == MOVIEVAULT_V4_CONTRACT:
        required.add("poster")
    _exact_keys(value, required=required, optional=optional)
    provider_ids = value["providerIds"]
    if not isinstance(provider_ids, dict):
        raise MovieVaultV2Error("record_invalid")
    normalized_provider_ids: dict[str, str] = {}
    for key, provider_id in provider_ids.items():
        if not isinstance(key, str) or not isinstance(provider_id, str):
            raise MovieVaultV2Error("record_invalid")
        normalized_provider_ids[key] = provider_id
    country_code = value.get("countryCode")
    if country_code is not None and (
        not isinstance(country_code, str) or not COUNTRY_PATTERN.fullmatch(country_code)
    ):
        raise MovieVaultV2Error("record_invalid")
    release_date = value.get("releaseDate")
    if release_date is not None:
        if not isinstance(release_date, str):
            raise MovieVaultV2Error("record_invalid")
        try:
            date.fromisoformat(release_date)
        except ValueError as exc:
            raise MovieVaultV2Error("record_invalid") from exc
    return {
        "contractVersion": contract_version,
        "recordType": "release",
        "operation": "upsert",
        "revision": _integer(value["revision"], minimum=1),
        "releaseId": _uuid(value["releaseId"]),
        "filmId": _uuid(value["filmId"]),
        "canonicalTitle": _text(value["canonicalTitle"], minimum=1, maximum=500),
        "releaseYear": _optional_integer(value.get("releaseYear"), minimum=1870, maximum=2200),
        "providerIds": normalized_provider_ids,
        "releaseTitle": _text(value["releaseTitle"], minimum=1, maximum=500),
        "edition": _optional_text(value.get("edition"), maximum=255),
        "format": _optional_text(value.get("format"), maximum=80),
        "region": _optional_text(value.get("region"), maximum=80),
        "countryCode": country_code,
        "languageCode": _optional_text(value.get("languageCode"), maximum=35),
        "releaseDate": release_date,
        "discCount": _optional_integer(value.get("discCount"), minimum=1, maximum=999),
        "studio": _optional_text(value.get("studio"), maximum=500),
        "distributor": _optional_text(value.get("distributor"), maximum=500),
        "runtimeMinutes": _optional_integer(
            value.get("runtimeMinutes"),
            minimum=1,
            maximum=10000,
        ),
        "eanHashes": _hashes(value["eanHashes"]),
        "assets": _assets(value["assets"], contract_version),
        "poster": (
            _poster(value["poster"], contract_version)
            if contract_version == MOVIEVAULT_V4_CONTRACT
            else None
        ),
    }


def _member(value: Any, contract_version: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MovieVaultV2Error("record_invalid")
    required = {
        "releaseId",
        "filmId",
        "canonicalTitle",
        "releaseTitle",
        "eanHashes",
        "position",
        "relationship",
    }
    optional = {
        "releaseEdition",
        "format",
        "region",
        "discNumber",
        "discFormat",
        "discBarcodeHash",
    }
    if contract_version in (MOVIEVAULT_V3_CONTRACT, MOVIEVAULT_V4_CONTRACT):
        optional.update({"studio", "distributor", "runtimeMinutes"})
    _exact_keys(value, required=required, optional=optional)
    if value["relationship"] != "contains":
        raise MovieVaultV2Error("record_invalid")
    barcode_hash = value.get("discBarcodeHash")
    return {
        "releaseId": _uuid(value["releaseId"]),
        "filmId": _uuid(value["filmId"]),
        "canonicalTitle": _text(value["canonicalTitle"], minimum=1, maximum=500),
        "releaseTitle": _text(value["releaseTitle"], minimum=1, maximum=500),
        "releaseEdition": _optional_text(value.get("releaseEdition"), maximum=255),
        "format": _optional_text(value.get("format"), maximum=80),
        "region": _optional_text(value.get("region"), maximum=80),
        "eanHashes": _hashes(value["eanHashes"]),
        "position": _integer(value["position"], minimum=1),
        "relationship": "contains",
        "discNumber": _optional_integer(value.get("discNumber"), minimum=1, maximum=999),
        "discFormat": _optional_text(value.get("discFormat"), maximum=80),
        "discBarcodeHash": None if barcode_hash is None else _hash(barcode_hash),
        "studio": _optional_text(value.get("studio"), maximum=500),
        "distributor": _optional_text(value.get("distributor"), maximum=500),
        "runtimeMinutes": _optional_integer(
            value.get("runtimeMinutes"),
            minimum=1,
            maximum=10000,
        ),
    }


def _box_set_record(value: dict[str, Any], contract_version: str) -> dict[str, Any]:
    required = {
        "contractVersion",
        "recordType",
        "operation",
        "revision",
        "boxSetId",
        "title",
        "eanHashes",
        "members",
    }
    optional = {"edition", "yearRange", "format", "countryCode", "languageCode"}
    if contract_version == MOVIEVAULT_V4_CONTRACT:
        required.add("poster")
    _exact_keys(value, required=required, optional=optional)
    members_value = value["members"]
    if not isinstance(members_value, list) or len(members_value) > 1000:
        raise MovieVaultV2Error("record_invalid")
    members = [_member(item, contract_version) for item in members_value]
    positions = [member["position"] for member in members]
    if len(set(positions)) != len(positions):
        raise MovieVaultV2Error("record_invalid")
    country_code = value.get("countryCode")
    if country_code is not None and (
        not isinstance(country_code, str) or not COUNTRY_PATTERN.fullmatch(country_code)
    ):
        raise MovieVaultV2Error("record_invalid")
    return {
        "contractVersion": contract_version,
        "recordType": "box_set",
        "operation": "upsert",
        "revision": _integer(value["revision"], minimum=1),
        "boxSetId": _uuid(value["boxSetId"]),
        "title": _text(value["title"], minimum=1, maximum=500),
        "edition": _optional_text(value.get("edition"), maximum=255),
        "yearRange": _optional_text(value.get("yearRange"), maximum=80),
        "format": _optional_text(value.get("format"), maximum=80),
        "countryCode": country_code,
        "languageCode": _optional_text(value.get("languageCode"), maximum=35),
        "eanHashes": _hashes(value["eanHashes"]),
        "members": sorted(members, key=lambda member: member["position"]),
        "poster": (
            _poster(value["poster"], contract_version)
            if contract_version == MOVIEVAULT_V4_CONTRACT
            else None
        ),
    }


def validate_record(
    value: Any,
    *,
    contract_version: str = MOVIEVAULT_V2_CONTRACT,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MovieVaultV2Error("record_invalid")
    if contract_version not in SUPPORTED_CONTRACTS or value.get("contractVersion") != contract_version:
        raise MovieVaultV2Error("contract_incompatible")
    record_type = value.get("recordType")
    operation = value.get("operation")
    if record_type not in {"release", "box_set"}:
        raise MovieVaultV2Error("record_invalid")
    if operation == "delete":
        _exact_keys(
            value,
            required={"contractVersion", "recordType", "operation", "revision", "entityId"},
            optional=set(),
        )
        return {
            "contractVersion": contract_version,
            "recordType": record_type,
            "operation": "delete",
            "revision": _integer(value["revision"], minimum=1),
            "entityId": _uuid(value["entityId"]),
        }
    if operation != "upsert":
        raise MovieVaultV2Error("record_invalid")
    if record_type == "release":
        return _release_record(value, contract_version)
    return _box_set_record(value, contract_version)


def parse_ndjson(
    content: bytes,
    *,
    full: bool,
    maximum_revision: int,
    contract_version: str = MOVIEVAULT_V2_CONTRACT,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    previous_revision = 0
    full_revisions: set[int] = set()
    full_entities: set[tuple[str, str]] = set()
    for raw_line in content.splitlines():
        if not raw_line.strip():
            continue
        if len(records) >= MAX_RECORDS:
            raise MovieVaultV2Error("artifact_record_limit")
        try:
            value = json.loads(raw_line)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MovieVaultV2Error("artifact_invalid") from exc
        record = validate_record(value, contract_version=contract_version)
        revision = record["revision"]
        if revision > maximum_revision:
            raise MovieVaultV2Error("revision_invalid")
        if full:
            if revision in full_revisions:
                raise MovieVaultV2Error("revision_invalid")
            full_revisions.add(revision)
        elif revision <= previous_revision:
            raise MovieVaultV2Error("revision_invalid")
        if full and record["operation"] != "upsert":
            raise MovieVaultV2Error("artifact_invalid")
        if full:
            entity_key = (
                record["recordType"],
                record["releaseId"] if record["recordType"] == "release" else record["boxSetId"],
            )
            if entity_key in full_entities:
                raise MovieVaultV2Error("artifact_invalid")
            full_entities.add(entity_key)
        previous_revision = revision
        records.append(record)
    return records


def validate_manifest(
    value: Any,
    *,
    contract_version: str = MOVIEVAULT_V2_CONTRACT,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MovieVaultV2Error("manifest_invalid")
    expected = {
        "contractVersion",
        "currentRevision",
        "currentCursor",
        "bucketPrefixLength",
        "hashAlgorithm",
        "datasetChecksum",
        "deltaPath",
        "bucketPathTemplate",
    }
    if set(value) != expected:
        raise MovieVaultV2Error("manifest_invalid")
    if (
        contract_version not in SUPPORTED_CONTRACTS
        or value["contractVersion"] != contract_version
        or value["hashAlgorithm"] != "sha256"
        or value["deltaPath"]
        != f"/v{CONTRACT_PATH_VERSIONS[contract_version]}/index/delta"
        or value["bucketPathTemplate"]
        != f"/v{CONTRACT_PATH_VERSIONS[contract_version]}/bucket/{{prefix}}"
        or not isinstance(value["currentCursor"], str)
        or not 20 <= len(value["currentCursor"]) <= 120
    ):
        raise MovieVaultV2Error("manifest_invalid")
    return {
        "contractVersion": contract_version,
        "currentRevision": _manifest_integer(value["currentRevision"], 0, None),
        "currentCursor": value["currentCursor"],
        "bucketPrefixLength": _manifest_integer(value["bucketPrefixLength"], 1, 12),
        "hashAlgorithm": "sha256",
        "datasetChecksum": _manifest_hash(value["datasetChecksum"]),
        "deltaPath": f"/v{CONTRACT_PATH_VERSIONS[contract_version]}/index/delta",
        "bucketPathTemplate": (
            f"/v{CONTRACT_PATH_VERSIONS[contract_version]}/bucket/{{prefix}}"
        ),
    }


def _manifest_integer(value: Any, minimum: int, maximum: int | None) -> int:
    try:
        return _integer(value, minimum=minimum, maximum=maximum)
    except MovieVaultV2Error as exc:
        raise MovieVaultV2Error("manifest_invalid") from exc


def _manifest_hash(value: Any) -> str:
    try:
        return _hash(value)
    except MovieVaultV2Error as exc:
        raise MovieVaultV2Error("manifest_invalid") from exc


def _request(
    url: str,
    *,
    accept: str,
    timeout_seconds: int,
    maximum_bytes: int,
) -> tuple[int, bytes, dict[str, str]]:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": accept,
            "User-Agent": "DiscVault-MovieVault-v2",
        },
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            status = int(response.status)
            content = response.read(maximum_bytes + 1)
            headers = {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as exc:
        if exc.code in {204, 409}:
            return exc.code, b"", {key.lower(): value for key, value in exc.headers.items()}
        if 300 <= exc.code < 400:
            raise MovieVaultV2Error("redirect_rejected") from exc
        raise MovieVaultV2Error("http_error") from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        raise MovieVaultV2Error("network_error") from exc
    if len(content) > maximum_bytes:
        raise MovieVaultV2Error("response_too_large")
    return status, content, headers


def _content_digest_sha256(value: Any) -> bytes:
    if not isinstance(value, str):
        raise MovieVaultV2Error("checksum_invalid")
    matches: list[str] = []
    for item in value.split(","):
        match = re.fullmatch(r"sha-256=:(.*):", item.strip(), flags=re.IGNORECASE)
        if match:
            matches.append(match.group(1))
    if len(matches) != 1:
        raise MovieVaultV2Error("checksum_invalid")
    try:
        digest = base64.b64decode(matches[0], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MovieVaultV2Error("checksum_invalid") from exc
    if len(digest) != hashlib.sha256().digest_size:
        raise MovieVaultV2Error("checksum_invalid")
    return digest


def _verify_digest(
    content: bytes,
    headers: dict[str, str],
    *,
    expected: str | None = None,
    contract_version: str = MOVIEVAULT_V2_CONTRACT,
) -> str:
    digest = hashlib.sha256(content).hexdigest()
    header_digest = headers.get("x-content-sha256")
    if (
        not header_digest
        or not HASH_PATTERN.fullmatch(header_digest)
        or not hmac.compare_digest(header_digest, digest)
    ):
        raise MovieVaultV2Error("checksum_invalid")
    if contract_version in (MOVIEVAULT_V3_CONTRACT, MOVIEVAULT_V4_CONTRACT):
        content_digest = _content_digest_sha256(headers.get("content-digest"))
        if not hmac.compare_digest(content_digest, bytes.fromhex(digest)):
            raise MovieVaultV2Error("checksum_invalid")
    if expected is not None and not hmac.compare_digest(digest, expected):
        raise MovieVaultV2Error("checksum_invalid")
    return digest


def fetch_manifest(
    origin: str,
    *,
    timeout_seconds: int,
    maximum_bytes: int = DEFAULT_MAX_MANIFEST_BYTES,
    contract_version: str = MOVIEVAULT_V2_CONTRACT,
) -> dict[str, Any]:
    if contract_version not in SUPPORTED_CONTRACTS:
        raise MovieVaultV2Error("contract_incompatible")
    version = CONTRACT_PATH_VERSIONS[contract_version]
    status, content, headers = _request(
        f"{origin}/v{version}/index/manifest",
        accept="application/json",
        timeout_seconds=timeout_seconds,
        maximum_bytes=maximum_bytes,
    )
    if status != 200:
        raise MovieVaultV2Error("manifest_unavailable")
    _verify_digest(content, headers, contract_version=contract_version)
    try:
        return validate_manifest(json.loads(content), contract_version=contract_version)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MovieVaultV2Error("manifest_invalid") from exc


def _fetch_feed(
    origin: str,
    *,
    cursor: str | None,
    timeout_seconds: int,
    maximum_bytes: int,
    contract_version: str = MOVIEVAULT_V2_CONTRACT,
) -> tuple[int, bytes, dict[str, str]]:
    if contract_version not in SUPPORTED_CONTRACTS:
        raise MovieVaultV2Error("contract_incompatible")
    url = f"{origin}/v{CONTRACT_PATH_VERSIONS[contract_version]}/index/delta"
    if cursor is not None:
        url = f"{url}?{urllib.parse.urlencode({'since': cursor})}"
    return _request(
        url,
        accept="application/x-ndjson",
        timeout_seconds=timeout_seconds,
        maximum_bytes=maximum_bytes,
    )


def _connection_scope(conn: Any, connection_factory: ConnectionFactory | None):
    if connection_factory is None:
        return nullcontext(conn)
    return connection_factory()


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    return row[index]


def _sync_state(conn: Any, *, lock: bool = False) -> dict[str, Any] | None:
    suffix = " FOR UPDATE" if lock else ""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT plugin_id, origin, active_generation, contract_version, cursor,
                   revision, dataset_checksum, status, stale_threshold_hours,
                   last_attempt_at, last_success_at, last_error_code, updated_at
            FROM movievault_v2_sync_state
            WHERE plugin_id = %s{suffix}
            """,
            (MOVIEVAULT_V2_PLUGIN_ID,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def _mark_syncing(conn: Any, origin: str, stale_threshold_hours: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO movievault_v2_sync_state (
                plugin_id, origin, status, stale_threshold_hours, last_attempt_at, updated_at
            )
            VALUES (%s, %s, 'syncing', %s, now(), now())
            ON CONFLICT (plugin_id) DO UPDATE
            SET status = 'syncing',
                stale_threshold_hours = EXCLUDED.stale_threshold_hours,
                last_attempt_at = now(),
                last_error_code = NULL,
                updated_at = now()
            """,
            (MOVIEVAULT_V2_PLUGIN_ID, origin, stale_threshold_hours),
        )


def _mark_error(conn: Any, code: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE movievault_v2_sync_state
            SET status = 'error',
                last_error_code = %s,
                updated_at = now()
            WHERE plugin_id = %s
            """,
            (code, MOVIEVAULT_V2_PLUGIN_ID),
        )


def _insert_lookup(
    cur: Any,
    generation: str,
    lookup_hash: str,
    entity_type: str,
    entity_id: str,
    source_type: str,
    member_position: int = 0,
) -> None:
    cur.execute(
        """
        INSERT INTO movievault_v2_lookup_hashes (
            generation, lookup_hash, entity_type, entity_id, source_type, member_position
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        (generation, lookup_hash, entity_type, entity_id, source_type, member_position),
    )


def _upsert_release(cur: Any, generation: str, record: dict[str, Any], origin: str) -> None:
    release_id = record["releaseId"]
    cur.execute(
        """
        DELETE FROM movievault_v2_lookup_hashes
        WHERE generation = %s AND entity_type = 'release' AND entity_id = %s
        """,
        (generation, release_id),
    )
    cur.execute(
        """
        INSERT INTO movievault_v2_releases (
            generation, release_id, film_id, canonical_title, release_year,
            provider_ids, release_title, edition, format, region, country_code,
            language_code, release_date, disc_count, studio, distributor,
            runtime_minutes, assets, revision, poster
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s
        )
        ON CONFLICT (generation, release_id) DO UPDATE
        SET film_id = EXCLUDED.film_id,
            canonical_title = EXCLUDED.canonical_title,
            release_year = EXCLUDED.release_year,
            provider_ids = EXCLUDED.provider_ids,
            release_title = EXCLUDED.release_title,
            edition = EXCLUDED.edition,
            format = EXCLUDED.format,
            region = EXCLUDED.region,
            country_code = EXCLUDED.country_code,
            language_code = EXCLUDED.language_code,
            release_date = EXCLUDED.release_date,
            disc_count = EXCLUDED.disc_count,
            studio = EXCLUDED.studio,
            distributor = EXCLUDED.distributor,
            runtime_minutes = EXCLUDED.runtime_minutes,
            assets = EXCLUDED.assets,
            revision = EXCLUDED.revision,
            poster = EXCLUDED.poster
        """,
        (
            generation,
            release_id,
            record["filmId"],
            record["canonicalTitle"],
            record["releaseYear"],
            Jsonb(record["providerIds"]),
            record["releaseTitle"],
            record["edition"],
            record["format"],
            record["region"],
            record["countryCode"],
            record["languageCode"],
            record["releaseDate"],
            record["discCount"],
            record["studio"],
            record["distributor"],
            record["runtimeMinutes"],
            Jsonb(record["assets"]),
            record["revision"],
            Jsonb(record["poster"]) if record.get("poster") is not None else None,
        ),
    )
    for lookup_hash in record["eanHashes"]:
        _insert_lookup(cur, generation, lookup_hash, "release", release_id, "release_ean")
    _enqueue_poster_cache(cur, origin, record.get("poster"))


def _upsert_box_set(cur: Any, generation: str, record: dict[str, Any], origin: str) -> None:
    box_set_id = record["boxSetId"]
    cur.execute(
        """
        DELETE FROM movievault_v2_lookup_hashes
        WHERE generation = %s AND entity_type = 'box_set' AND entity_id = %s
        """,
        (generation, box_set_id),
    )
    cur.execute(
        """
        INSERT INTO movievault_v2_box_sets (
            generation, box_set_id, title, edition, year_range, format,
            country_code, language_code, revision, poster
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (generation, box_set_id) DO UPDATE
        SET title = EXCLUDED.title,
            edition = EXCLUDED.edition,
            year_range = EXCLUDED.year_range,
            format = EXCLUDED.format,
            country_code = EXCLUDED.country_code,
            language_code = EXCLUDED.language_code,
            revision = EXCLUDED.revision,
            poster = EXCLUDED.poster
        """,
        (
            generation,
            box_set_id,
            record["title"],
            record["edition"],
            record["yearRange"],
            record["format"],
            record["countryCode"],
            record["languageCode"],
            record["revision"],
            Jsonb(record["poster"]) if record.get("poster") is not None else None,
        ),
    )
    cur.execute(
        """
        DELETE FROM movievault_v2_box_set_members
        WHERE generation = %s AND box_set_id = %s
        """,
        (generation, box_set_id),
    )
    for lookup_hash in record["eanHashes"]:
        _insert_lookup(cur, generation, lookup_hash, "box_set", box_set_id, "box_set_ean")
    for member in record["members"]:
        cur.execute(
            """
            INSERT INTO movievault_v2_box_set_members (
                generation, box_set_id, position, release_id, film_id,
                canonical_title, release_title, release_edition, format, region,
                relationship, disc_number, disc_format, disc_barcode_hash,
                studio, distributor, runtime_minutes
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s
            )
            """,
            (
                generation,
                box_set_id,
                member["position"],
                member["releaseId"],
                member["filmId"],
                member["canonicalTitle"],
                member["releaseTitle"],
                member["releaseEdition"],
                member["format"],
                member["region"],
                member["relationship"],
                member["discNumber"],
                member["discFormat"],
                member["discBarcodeHash"],
                member["studio"],
                member["distributor"],
                member["runtimeMinutes"],
            ),
        )
        for lookup_hash in member["eanHashes"]:
            _insert_lookup(
                cur,
                generation,
                lookup_hash,
                "box_set",
                box_set_id,
                "member_ean",
                member["position"],
            )
        if member["discBarcodeHash"]:
            _insert_lookup(
                cur,
                generation,
                member["discBarcodeHash"],
                "box_set",
                box_set_id,
                "member_barcode",
                member["position"],
            )
    _enqueue_poster_cache(cur, origin, record.get("poster"))


POSTER_CACHE_JOB_TYPE = "movievault_v2.poster_cache"
POSTER_CLEANUP_JOB_TYPE = "movievault_v2.poster_cleanup"


def _enqueue_poster_cache(cur: Any, origin: str, poster: dict[str, Any] | None) -> None:
    """Discover a changed selected poster during index sync and enqueue a bounded,
    idempotent background caching job for each variant. Keyed by (asset_id, variant,
    checksum) so unchanged posters never re-enqueue and no remote MovieVault URL or
    request telemetry is persisted anywhere other than the transient job payload
    needed to perform the anonymous, bounded fetch."""
    if poster is None:
        return
    asset_id = poster["assetId"]
    for variant_name in ("thumbnail", "display"):
        variant = poster[variant_name]
        checksum = variant["checksum"]
        cur.execute(
            """
            INSERT INTO movievault_v2_poster_cache (
                asset_id, variant, checksum, status, created_at, checked_at
            )
            VALUES (%s, %s, %s, 'pending', now(), now())
            ON CONFLICT (asset_id, variant, checksum) DO NOTHING
            RETURNING id
            """,
            (asset_id, variant_name, checksum),
        )
        row = cur.fetchone()
        if row is None:
            continue
        cache_id = _row_value(row, "id")
        cur.execute(
            """
            INSERT INTO background_jobs (job_type, payload)
            VALUES (%s, %s)
            """,
            (
                POSTER_CACHE_JOB_TYPE,
                Jsonb(
                    {
                        "cacheId": str(cache_id),
                        "assetId": asset_id,
                        "variant": variant_name,
                        "checksum": checksum,
                        "origin": origin,
                        "path": variant["path"],
                    }
                ),
            ),
        )


def _apply_record(cur: Any, generation: str, record: dict[str, Any], origin: str) -> None:
    if record["operation"] == "upsert":
        if record["recordType"] == "release":
            _upsert_release(cur, generation, record, origin)
        else:
            _upsert_box_set(cur, generation, record, origin)
        return
    entity_id = record["entityId"]
    cur.execute(
        """
        DELETE FROM movievault_v2_lookup_hashes
        WHERE generation = %s AND entity_type = %s AND entity_id = %s
        """,
        (generation, record["recordType"], entity_id),
    )
    table = (
        "movievault_v2_releases"
        if record["recordType"] == "release"
        else "movievault_v2_box_sets"
    )
    id_column = "release_id" if record["recordType"] == "release" else "box_set_id"
    cur.execute(
        f"DELETE FROM {table} WHERE generation = %s AND {id_column} = %s",
        (generation, entity_id),
    )


def _apply_full(
    conn: Any,
    *,
    origin: str,
    manifest: dict[str, Any],
    records: list[dict[str, Any]],
    stale_threshold_hours: int,
) -> dict[str, Any]:
    generation = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT plugin_id
            FROM movievault_v2_sync_state
            WHERE plugin_id = %s
            FOR UPDATE
            """,
            (MOVIEVAULT_V2_PLUGIN_ID,),
        )
        for record in records:
            _apply_record(cur, generation, record, origin)
        cur.execute(
            """
            UPDATE movievault_v2_sync_state
            SET origin = %s,
                active_generation = %s,
                contract_version = %s,
                cursor = %s,
                revision = %s,
                dataset_checksum = %s,
                status = 'current',
                stale_threshold_hours = %s,
                last_success_at = now(),
                last_error_code = NULL,
                updated_at = now()
            WHERE plugin_id = %s
            """,
            (
                origin,
                generation,
                manifest["contractVersion"],
                manifest["currentCursor"],
                manifest["currentRevision"],
                manifest["datasetChecksum"],
                stale_threshold_hours,
                MOVIEVAULT_V2_PLUGIN_ID,
            ),
        )
        cur.execute(
            "DELETE FROM movievault_v2_lookup_hashes WHERE generation <> %s",
            (generation,),
        )
        cur.execute(
            "DELETE FROM movievault_v2_box_set_members WHERE generation <> %s",
            (generation,),
        )
        cur.execute(
            "DELETE FROM movievault_v2_box_sets WHERE generation <> %s",
            (generation,),
        )
        cur.execute(
            "DELETE FROM movievault_v2_releases WHERE generation <> %s",
            (generation,),
        )
    return {
        "state": "current",
        "mode": "full",
        "revision": manifest["currentRevision"],
        "recordsApplied": len(records),
    }


def _apply_delta(
    conn: Any,
    *,
    origin: str,
    expected_cursor: str,
    manifest: dict[str, Any],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    state = _sync_state(conn, lock=True)
    if not state or str(state.get("cursor") or "") != expected_cursor:
        raise MovieVaultV2Error("sync_state_changed")
    generation = state.get("active_generation")
    if not generation:
        raise MovieVaultV2Error("full_sync_required")
    with conn.cursor() as cur:
        for record in records:
            if record["revision"] <= int(state["revision"]):
                raise MovieVaultV2Error("revision_invalid")
            _apply_record(cur, str(generation), record, origin)
        cur.execute(
            """
            UPDATE movievault_v2_sync_state
            SET cursor = %s,
                revision = %s,
                dataset_checksum = %s,
                status = 'current',
                last_success_at = now(),
                last_error_code = NULL,
                updated_at = now()
            WHERE plugin_id = %s
            """,
            (
                manifest["currentCursor"],
                manifest["currentRevision"],
                manifest["datasetChecksum"],
                MOVIEVAULT_V2_PLUGIN_ID,
            ),
        )
    return {
        "state": "current",
        "mode": "delta",
        "revision": manifest["currentRevision"],
        "recordsApplied": len(records),
    }


def _mark_current(conn: Any, manifest: dict[str, Any]) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE movievault_v2_sync_state
            SET cursor = %s,
                revision = %s,
                dataset_checksum = %s,
                status = 'current',
                last_success_at = now(),
                last_error_code = NULL,
                updated_at = now()
            WHERE plugin_id = %s
            """,
            (
                manifest["currentCursor"],
                manifest["currentRevision"],
                manifest["datasetChecksum"],
                MOVIEVAULT_V2_PLUGIN_ID,
            ),
        )
    return {
        "state": "current",
        "mode": "current",
        "revision": manifest["currentRevision"],
        "recordsApplied": 0,
    }


def run_sync(
    connection_factory: ConnectionFactory,
    settings: dict[str, Any],
    *,
    contract_version: str = MOVIEVAULT_V2_CONTRACT,
) -> dict[str, Any]:
    if contract_version not in SUPPORTED_CONTRACTS:
        raise MovieVaultV2Error("contract_incompatible")
    origin = normalize_origin(settings.get("origin"))
    timeout_seconds = _bounded_setting(
        settings.get("requestTimeoutSeconds"),
        default=DEFAULT_TIMEOUT_SECONDS,
        minimum=2,
        maximum=120,
        code="settings_invalid",
    )
    stale_threshold_hours = _bounded_setting(
        settings.get("staleThresholdHours"),
        default=48,
        minimum=1,
        maximum=720,
        code="settings_invalid",
    )
    maximum_bytes = _bounded_setting(
        settings.get("maximumArtifactBytes"),
        default=DEFAULT_MAX_ARTIFACT_BYTES,
        minimum=1024,
        maximum=512 * 1024 * 1024,
        code="settings_invalid",
    )
    with connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (SYNC_LOCK_KEY,))
            acquired = bool(_row_value(cur.fetchone(), "acquired"))
        if not acquired:
            raise MovieVaultV2Error("sync_in_progress")
        try:
            _mark_syncing(conn, origin, stale_threshold_hours)
            conn.commit()
            state = _sync_state(conn)
            manifest = fetch_manifest(
                origin,
                timeout_seconds=timeout_seconds,
                contract_version=contract_version,
            )
            if (
                state
                and state.get("origin") == origin
                and state.get("contract_version") == contract_version
                and int(manifest["currentRevision"]) < int(state.get("revision") or 0)
            ):
                raise MovieVaultV2Error("revision_regressed")
            needs_full = bool(
                not state
                or not state.get("active_generation")
                or state.get("origin") != origin
                or state.get("contract_version") != contract_version
                or not state.get("cursor")
            )
            if needs_full:
                status, content, headers = _fetch_feed(
                    origin,
                    cursor=None,
                    timeout_seconds=timeout_seconds,
                    maximum_bytes=maximum_bytes,
                    contract_version=contract_version,
                )
                if status != 200:
                    raise MovieVaultV2Error("full_sync_unavailable")
                _verify_digest(content, headers, contract_version=contract_version)
                records = parse_ndjson(
                    content,
                    full=True,
                    maximum_revision=manifest["currentRevision"],
                    contract_version=contract_version,
                )
                result = _apply_full(
                    conn,
                    origin=origin,
                    manifest=manifest,
                    records=records,
                    stale_threshold_hours=stale_threshold_hours,
                )
                conn.commit()
                return result

            cursor = str(state["cursor"])
            status, content, headers = _fetch_feed(
                origin,
                cursor=cursor,
                timeout_seconds=timeout_seconds,
                maximum_bytes=maximum_bytes,
                contract_version=contract_version,
            )
            if status == 409:
                status, content, headers = _fetch_feed(
                    origin,
                    cursor=None,
                    timeout_seconds=timeout_seconds,
                    maximum_bytes=maximum_bytes,
                    contract_version=contract_version,
                )
                if status != 200:
                    raise MovieVaultV2Error("full_sync_unavailable")
                _verify_digest(content, headers, contract_version=contract_version)
                records = parse_ndjson(
                    content,
                    full=True,
                    maximum_revision=manifest["currentRevision"],
                    contract_version=contract_version,
                )
                result = _apply_full(
                    conn,
                    origin=origin,
                    manifest=manifest,
                    records=records,
                    stale_threshold_hours=stale_threshold_hours,
                )
                conn.commit()
                return result
            if status == 204:
                next_cursor = headers.get("x-next-cursor")
                if next_cursor is not None and next_cursor != manifest["currentCursor"]:
                    raise MovieVaultV2Error("cursor_invalid")
                result = _mark_current(conn, manifest)
                conn.commit()
                return result
            if status != 200:
                raise MovieVaultV2Error("delta_sync_unavailable")
            _verify_digest(content, headers, contract_version=contract_version)
            if headers.get("x-next-cursor") != manifest["currentCursor"]:
                raise MovieVaultV2Error("cursor_invalid")
            records = parse_ndjson(
                content,
                full=False,
                maximum_revision=manifest["currentRevision"],
                contract_version=contract_version,
            )
            if (
                int(manifest["currentRevision"]) > int(state.get("revision") or 0)
                and (
                    not records
                    or int(records[-1]["revision"]) != int(manifest["currentRevision"])
                )
            ):
                raise MovieVaultV2Error("revision_invalid")
            result = _apply_delta(
                conn,
                origin=origin,
                expected_cursor=cursor,
                manifest=manifest,
                records=records,
            )
            conn.commit()
            return result
        except MovieVaultV2Error as exc:
            conn.rollback()
            _mark_error(conn, exc.code)
            conn.commit()
            raise
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (SYNC_LOCK_KEY,))
            conn.commit()


def _bounded_setting(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
    code: str,
) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise MovieVaultV2Error(code)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise MovieVaultV2Error(code) from exc
    if parsed < minimum or parsed > maximum:
        raise MovieVaultV2Error(code)
    return parsed


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _local_media_asset_url(media_asset_id: Any) -> str | None:
    if not media_asset_id:
        return None
    return f"/api/next/media/assets/{media_asset_id}"


def _poster_status_fields(conn: Any, poster: dict[str, Any] | None) -> dict[str, Any]:
    """Map a persisted MovieVault poster descriptor onto authenticated,
    DiscVault-local fields. The raw poster object (remote MovieVault asset
    paths) is never returned to a caller; only a local media-asset URL,
    cache status, checksum, and a checksum-derived revision are exposed."""
    if poster is None:
        return {
            "posterUrl": None,
            "posterStatus": "unavailable",
            "posterChecksum": None,
            "posterRevision": None,
        }
    asset_id = poster.get("assetId")
    display = poster.get("display") or {}
    checksum = display.get("checksum")
    if not asset_id or not checksum:
        return {
            "posterUrl": None,
            "posterStatus": "unavailable",
            "posterChecksum": None,
            "posterRevision": None,
        }
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT status, media_asset_id
            FROM movievault_v2_poster_cache
            WHERE asset_id = %s AND variant = 'display' AND checksum = %s
            """,
            (asset_id, checksum),
        )
        row = cur.fetchone()
    if not row:
        return {
            "posterUrl": None,
            "posterStatus": "pending",
            "posterChecksum": checksum,
            "posterRevision": None,
        }
    media_asset_id = row.get("media_asset_id")
    poster_url = _local_media_asset_url(media_asset_id)
    return {
        "posterUrl": poster_url,
        "posterStatus": str(row.get("status") or "pending"),
        "posterChecksum": checksum,
        "posterRevision": checksum[:16] if poster_url else None,
    }


def _release_payload(conn: Any, row: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "recordType": "release",
        "releaseId": str(row["release_id"]),
        "filmId": str(row["film_id"]),
        "canonicalTitle": row["canonical_title"],
        "releaseYear": row.get("release_year"),
        "providerIds": _json_value(row.get("provider_ids") or {}),
        "releaseTitle": row["release_title"],
        "edition": row.get("edition"),
        "format": row.get("format"),
        "region": row.get("region"),
        "countryCode": row.get("country_code"),
        "languageCode": row.get("language_code"),
        "releaseDate": _json_value(row.get("release_date")),
        "discCount": row.get("disc_count"),
        "studio": row.get("studio"),
        "distributor": row.get("distributor"),
        "runtimeMinutes": row.get("runtime_minutes"),
        "assets": _json_value(row.get("assets") or []),
        "revision": int(row["revision"]),
    }
    payload.update(_poster_status_fields(conn, row.get("poster")))
    return payload


def _box_set_payload(conn: Any, row: dict[str, Any]) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT position, release_id, film_id, canonical_title, release_title,
                   release_edition, format, region, relationship, disc_number,
                   disc_format, studio, distributor, runtime_minutes
            FROM movievault_v2_box_set_members
            WHERE generation = %s AND box_set_id = %s
            ORDER BY position
            """,
            (row["generation"], row["box_set_id"]),
        )
        member_rows = cur.fetchall()
    members = [
        {
            "position": int(member["position"]),
            "releaseId": str(member["release_id"]),
            "filmId": str(member["film_id"]),
            "canonicalTitle": member["canonical_title"],
            "releaseTitle": member["release_title"],
            "releaseEdition": member.get("release_edition"),
            "format": member.get("format"),
            "region": member.get("region"),
            "relationship": member["relationship"],
            "discNumber": member.get("disc_number"),
            "discFormat": member.get("disc_format"),
            "studio": member.get("studio"),
            "distributor": member.get("distributor"),
            "runtimeMinutes": member.get("runtime_minutes"),
        }
        for member in member_rows
    ]
    payload = {
        "recordType": "box_set",
        "boxSetId": str(row["box_set_id"]),
        "title": row["title"],
        "edition": row.get("edition"),
        "yearRange": row.get("year_range"),
        "format": row.get("format"),
        "countryCode": row.get("country_code"),
        "languageCode": row.get("language_code"),
        "members": members,
        "revision": int(row["revision"]),
    }
    payload.update(_poster_status_fields(conn, row.get("poster")))
    return payload


def local_lookup(conn: Any, request: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise MovieVaultV2Error("lookup_invalid")
    kind = request.get("kind")
    limit = _bounded_setting(
        request.get("limit"),
        default=20,
        minimum=1,
        maximum=50,
        code="lookup_invalid",
    )
    state = _sync_state(conn)
    if not state or not state.get("active_generation"):
        return {"state": "unconfigured", "results": []}
    generation = state["active_generation"]
    results: list[dict[str, Any]] = []
    if kind == "barcode":
        lookup_hash = request.get("hash")
        if not isinstance(lookup_hash, str) or not HASH_PATTERN.fullmatch(lookup_hash):
            raise MovieVaultV2Error("lookup_invalid")
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT entity_type, entity_id,
                       array_agg(DISTINCT source_type ORDER BY source_type) AS match_sources
                FROM movievault_v2_lookup_hashes
                WHERE generation = %s AND lookup_hash = %s
                GROUP BY entity_type, entity_id
                ORDER BY entity_type, entity_id
                LIMIT %s
                """,
                (generation, lookup_hash, limit),
            )
            matches = cur.fetchall()
        for match in matches:
            result = _entity_payload(
                conn,
                generation,
                str(match["entity_type"]),
                match["entity_id"],
            )
            if result:
                result["matchSources"] = list(match.get("match_sources") or [])
                results.append(result)
    elif kind == "title":
        query = request.get("query")
        if not isinstance(query, str) or not query.strip() or len(query.strip()) > 500:
            raise MovieVaultV2Error("lookup_invalid")
        pattern = f"%{_escape_like(query.strip().casefold())}%"
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 'release' AS entity_type, release_id AS entity_id,
                       LEAST(
                           strpos(lower(canonical_title), %s),
                           strpos(lower(release_title), %s)
                       ) AS rank
                FROM movievault_v2_releases
                WHERE generation = %s
                  AND (
                      lower(canonical_title) LIKE %s ESCAPE '\\'
                      OR lower(release_title) LIKE %s ESCAPE '\\'
                      OR lower(COALESCE(edition, '')) LIKE %s ESCAPE '\\'
                  )
                UNION ALL
                SELECT 'box_set' AS entity_type, box_set_id AS entity_id,
                       strpos(lower(title), %s) AS rank
                FROM movievault_v2_box_sets
                WHERE generation = %s
                  AND (
                      lower(title) LIKE %s ESCAPE '\\'
                      OR lower(COALESCE(edition, '')) LIKE %s ESCAPE '\\'
                  )
                ORDER BY rank, entity_type, entity_id
                LIMIT %s
                """,
                (
                    query.strip().casefold(),
                    query.strip().casefold(),
                    generation,
                    pattern,
                    pattern,
                    pattern,
                    query.strip().casefold(),
                    generation,
                    pattern,
                    pattern,
                    limit,
                ),
            )
            matches = cur.fetchall()
        for match in matches:
            result = _entity_payload(
                conn,
                generation,
                str(match["entity_type"]),
                match["entity_id"],
            )
            if result:
                results.append(result)
    elif kind in {"release", "box_set"}:
        entity_key = "releaseId" if kind == "release" else "boxSetId"
        entity_id = _uuid(request.get(entity_key))
        result = _entity_payload(conn, generation, kind, entity_id)
        if result:
            results.append(result)
    else:
        raise MovieVaultV2Error("lookup_invalid")
    return {
        "state": _effective_state(state),
        "revision": int(state.get("revision") or 0),
        "results": results[:limit],
    }


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _entity_payload(
    conn: Any,
    generation: Any,
    entity_type: str,
    entity_id: Any,
) -> dict[str, Any] | None:
    if entity_type == "release":
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM movievault_v2_releases
                WHERE generation = %s AND release_id = %s
                """,
                (generation, entity_id),
            )
            row = cur.fetchone()
        return _release_payload(conn, dict(row)) if row else None
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM movievault_v2_box_sets
            WHERE generation = %s AND box_set_id = %s
            """,
            (generation, entity_id),
        )
        row = cur.fetchone()
    return _box_set_payload(conn, dict(row)) if row else None


def _effective_state(state: dict[str, Any]) -> str:
    status = str(state.get("status") or "unconfigured")
    last_success = state.get("last_success_at")
    threshold = int(state.get("stale_threshold_hours") or 48)
    if status == "current" and isinstance(last_success, datetime):
        age = datetime.now(timezone.utc) - last_success.astimezone(timezone.utc)
        if age.total_seconds() > threshold * 3600:
            return "stale"
    return status


def sync_status(conn: Any) -> dict[str, Any]:
    state = _sync_state(conn)
    if not state:
        return {
            "state": "unconfigured",
            "revision": 0,
            "lastSuccessAt": None,
            "lastAttemptAt": None,
            "errorCode": None,
        }
    return {
        "state": _effective_state(state),
        "revision": int(state.get("revision") or 0),
        "lastSuccessAt": _json_value(state.get("last_success_at")),
        "lastAttemptAt": _json_value(state.get("last_attempt_at")),
        "errorCode": state.get("last_error_code"),
    }


def bucket_lookup(
    settings: dict[str, Any],
    request: dict[str, Any],
    *,
    contract_version: str = MOVIEVAULT_V2_CONTRACT,
) -> dict[str, Any]:
    if contract_version not in SUPPORTED_CONTRACTS:
        raise MovieVaultV2Error("contract_incompatible")
    if not isinstance(request, dict):
        raise MovieVaultV2Error("lookup_invalid")
    lookup_hash = request.get("hash")
    if not isinstance(lookup_hash, str) or not HASH_PATTERN.fullmatch(lookup_hash):
        raise MovieVaultV2Error("lookup_invalid")
    origin = normalize_origin(settings.get("origin"))
    timeout_seconds = _bounded_setting(
        settings.get("requestTimeoutSeconds"),
        default=DEFAULT_TIMEOUT_SECONDS,
        minimum=2,
        maximum=120,
        code="settings_invalid",
    )
    manifest = fetch_manifest(
        origin,
        timeout_seconds=timeout_seconds,
        contract_version=contract_version,
    )
    prefix = lookup_hash[: manifest["bucketPrefixLength"]]
    status, content, headers = _request(
        f"{origin}/v{CONTRACT_PATH_VERSIONS[contract_version]}/bucket/{prefix}",
        accept="application/json",
        timeout_seconds=timeout_seconds,
        maximum_bytes=DEFAULT_MAX_BUCKET_BYTES,
    )
    if status != 200:
        raise MovieVaultV2Error("bucket_unavailable")
    _verify_digest(content, headers, contract_version=contract_version)
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MovieVaultV2Error("bucket_invalid") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"contractVersion", "prefix", "records"}
        or payload.get("contractVersion") != contract_version
        or payload.get("prefix") != prefix
        or not isinstance(payload.get("records"), list)
    ):
        raise MovieVaultV2Error("bucket_invalid")
    records = [
        validate_record(record, contract_version=contract_version)
        for record in payload["records"]
    ]
    matches = [record for record in records if _record_contains_hash(record, lookup_hash)]
    return {"state": "remote_bucket", "results": matches[:50]}


def _record_contains_hash(record: dict[str, Any], lookup_hash: str) -> bool:
    if record["operation"] != "upsert":
        return False
    if lookup_hash in record.get("eanHashes", []):
        return True
    return any(
        lookup_hash in member.get("eanHashes", [])
        or lookup_hash == member.get("discBarcodeHash")
        for member in record.get("members", [])
    )


def _negotiated_contract(context: dict[str, Any]) -> str:
    supported_range = context.get("distributionContractRange")
    if not isinstance(supported_range, dict):
        return MOVIEVAULT_V2_CONTRACT
    if set(supported_range) != {"minimum", "maximum"}:
        return MOVIEVAULT_V2_CONTRACT
    minimum = supported_range.get("minimum")
    maximum = supported_range.get("maximum")
    if minimum not in SUPPORTED_CONTRACTS or maximum not in SUPPORTED_CONTRACTS:
        return MOVIEVAULT_V2_CONTRACT
    start = SUPPORTED_CONTRACTS.index(minimum)
    end = SUPPORTED_CONTRACTS.index(maximum)
    if start > end:
        return MOVIEVAULT_V2_CONTRACT
    return SUPPORTED_CONTRACTS[end]


def movievault_v2_plugin_context(
    conn: Any,
    plugin_id: str,
    context: dict[str, Any],
    *,
    connection_factory: ConnectionFactory | None = None,
) -> dict[str, Any]:
    if plugin_id != MOVIEVAULT_V2_PLUGIN_ID:
        return context
    settings = context.get("settings")
    safe_settings = settings if isinstance(settings, dict) else {}
    contract_version = _negotiated_contract(context)

    def lookup_callback(request: dict[str, Any]) -> dict[str, Any]:
        with _connection_scope(conn, connection_factory) as active_conn:
            return local_lookup(active_conn, request)

    def status_callback(_request: dict[str, Any] | None = None) -> dict[str, Any]:
        with _connection_scope(conn, connection_factory) as active_conn:
            return sync_status(active_conn)

    def sync_callback(_request: dict[str, Any] | None = None) -> dict[str, Any]:
        if connection_factory is None:
            raise MovieVaultV2Error("core_bridge_unavailable")
        return run_sync(
            connection_factory,
            safe_settings,
            contract_version=contract_version,
        )

    def bucket_callback(request: dict[str, Any]) -> dict[str, Any]:
        return bucket_lookup(
            safe_settings,
            request,
            contract_version=contract_version,
        )

    return {
        **context,
        "movievaultV2Lookup": lookup_callback,
        "movievaultV2Status": status_callback,
        "movievaultV2Sync": sync_callback,
        "movievaultV2BucketLookup": bucket_callback,
        "movievaultDistributionContract": contract_version,
        "movievaultDistributionContractRange": {
            "minimum": SUPPORTED_CONTRACTS[0],
            "maximum": SUPPORTED_CONTRACTS[-1],
        },
    }
