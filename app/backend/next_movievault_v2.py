"""Anonymous MovieVault v2 synchronization and local lookup bridge."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import re
import socket
import time
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
RELEASE_DETAILS_CONTRACT = "release-technical-1"
DEFAULT_MAX_RELEASE_DETAILS_BYTES = 256 * 1024
DEFAULT_RELEASE_DETAILS_POLL_ATTEMPTS = 4
MAX_RELEASE_DETAILS_POLL_ATTEMPTS = 10
MAX_RELEASE_DETAILS_POLL_WAIT_SECONDS = 5
MAX_RECORDS = 2_000_000
# The delta feed serves one MovieVault publication segment per request, keyed by
# the cursor it starts from. An instance more than one publish cycle behind gets
# an intermediate segment back, not the head - run_sync() walks the chain one
# segment at a time. Bounded so a pathological or misbehaving origin cannot make
# a single sync run forever; an instance this far behind picks up where it left
# off on the next scheduled or manual sync.
MAX_DELTA_HOPS_PER_SYNC = 500
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

# distribution-4 audio track / subtitle language enums (PR #159 on
# MovieVault-v2). Kept as plain sets rather than a DB-level CHECK enum:
# unrecognized codec/channels/immersiveFormat values are stored as-is with a
# logged warning rather than rejecting the whole record - see
# _audio_track() below.
AUDIO_TRACK_CODECS = {
    "pcm",
    "dolby_digital",
    "dolby_digital_plus",
    "dolby_truehd",
    "dts",
    "dts_hd_hr",
    "dts_hd_ma",
    "mpeg_audio",
    "aac",
}
AUDIO_TRACK_CHANNELS = {"1.0", "2.0", "5.1", "6.1", "7.1"}
AUDIO_TRACK_IMMERSIVE_FORMATS = {"dolby_atmos", "dts_x", "auro_3d"}
MAX_AUDIO_TRACKS = 50
MAX_SUBTITLE_LANGUAGES = 50
LANGUAGE_CODE_PATTERN = re.compile(r"^[a-z]{2,8}(-[a-z0-9]{1,8})*$")

# distribution-4 packaging enum. Same forward-compat leniency as the audio
# track enums above - an unrecognized value is stored as-is with a logged
# warning rather than rejecting the whole release record.
PACKAGING_VALUES = {
    "keep_case",
    "amaray",
    "steelbook",
    "slipcover",
    "slipcase",
    "digibook",
    "mediabook",
    "digipak",
    "box",
}
MAX_PACKAGING = 9

# distribution-4 subtitle variants (MovieVault PR #162). A disc carries several
# tracks in the same language - a full track, an SDH track that also transcribes
# sound effects, a forced track for foreign dialogue only - so the feed sends
# objects, not bare language codes, and the pair (language, type) is what
# identifies a track. Same forward-compat leniency as the enums above.
SUBTITLE_TYPES = {"full", "sdh", "forced", "commentary", "closed_caption"}
DEFAULT_SUBTITLE_TYPE = "full"

# distribution-4 video profile (MovieVault PR #161).
VIDEO_RESOLUTIONS = {"480p", "576p", "720p", "1080i", "1080p", "2160p"}
VIDEO_CODECS = {"mpeg2", "vc1", "h264", "hevc", "av1"}
HDR_FORMATS = {"hdr", "hdr10", "hdr10_plus", "hlg", "dolby_vision"}
DISC_REGIONS = {"A", "B", "C", "1", "2", "3", "4", "5", "6", "7", "8", "FREE"}
MAX_VIDEO_CODECS = 5
MAX_HDR_FORMATS = 5
MAX_ASPECT_RATIOS = 8
MAX_DISC_REGIONS = 12
# Deliberately broader than the old release-details regex `^(?:1|2)\.[0-9]{2}:1$`,
# which rejected "16:9", "4:3" and "1.375:1" - all of them real, common ratios.
# MovieVault constrains the whole array to digits, dots and colons; this mirrors
# that while still refusing anything that is not shaped like a ratio.
ASPECT_RATIO_PATTERN = re.compile(r"^[0-9]{1,2}(?:\.[0-9]{1,3})?:[0-9]{1,2}(?:\.[0-9]{1,3})?$")

logger = logging.getLogger(__name__)

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


# The MovieVault v2 instance endpoint is enforced by DiscVault and is NOT
# user-editable. The value is fixed to a hardcoded default and may only be
# overridden out-of-band via the MOVIEVAULT_V2_ORIGIN environment variable
# (validated with normalize_origin; an invalid override falls back to the
# default). Any origin stored in plugin settings is ignored at runtime.
DEFAULT_MOVIEVAULT_V2_ORIGIN = "https://movies2.vaultstack.eu"
MOVIEVAULT_V2_ORIGIN_ENV = "MOVIEVAULT_V2_ORIGIN"


def enforced_origin() -> str:
    """Return the enforced MovieVault v2 origin.

    Resolution order:
    1. ``MOVIEVAULT_V2_ORIGIN`` environment variable, if set and valid.
    2. ``DEFAULT_MOVIEVAULT_V2_ORIGIN`` otherwise.

    An invalid environment override is ignored in favour of the default so a
    misconfigured deployment can never break origin resolution.
    """
    override = os.environ.get(MOVIEVAULT_V2_ORIGIN_ENV)
    if override and override.strip():
        try:
            return normalize_origin(override)
        except MovieVaultV2Error:
            pass
    return normalize_origin(DEFAULT_MOVIEVAULT_V2_ORIGIN)


# The anonymous bucket fallback is enforced by DiscVault for the same reason as
# the origin: it is what resolves a disc the locally synced index does not carry
# yet, so a barcode lookup without it silently misses every title MovieVault has
# not distributed into this instance's index. It is therefore always on and is
# stripped from the plugin's settingsSchema (ENFORCED_PLUGIN_SETTINGS); any value
# left in plugin settings is ignored at runtime.
DEFAULT_MOVIEVAULT_V2_BUCKET_FALLBACK = True
MOVIEVAULT_V2_BUCKET_FALLBACK_ENV = "MOVIEVAULT_V2_BUCKET_FALLBACK"
_BUCKET_FALLBACK_OFF_VALUES = {"0", "false", "no", "off"}


def enforced_bucket_fallback() -> bool:
    """Return whether the anonymous bucket fallback is active.

    Resolution order:
    1. ``MOVIEVAULT_V2_BUCKET_FALLBACK`` environment variable, when it spells out
       a recognised "off" value (``0``/``false``/``no``/``off``, any casing).
    2. ``DEFAULT_MOVIEVAULT_V2_BUCKET_FALLBACK`` otherwise.

    Anything unrecognised - including an empty or malformed override - resolves to
    the default, so a misconfigured deployment can never silently lose the
    fallback.
    """
    override = os.environ.get(MOVIEVAULT_V2_BUCKET_FALLBACK_ENV)
    if override and override.strip().lower() in _BUCKET_FALLBACK_OFF_VALUES:
        return False
    return DEFAULT_MOVIEVAULT_V2_BUCKET_FALLBACK


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
    label: str = "object",
) -> None:
    """Reject an object whose key set does not match the contract exactly.

    Logs which keys are missing or unexpected before raising. The error code is
    deliberately value-free where it crosses the plugin boundary, so without this
    an operator sees a whole sync fail with no indication of which field
    disagreed - the failure mode that makes contract drift between MovieVault and
    DiscVault expensive to diagnose. Key names only; no values are logged.
    """
    keys = set(value)
    missing = required - keys
    unexpected = keys - required - optional
    if missing or unexpected:
        logger.warning(
            "movievault_v2: %s rejected - missing keys %s, unexpected keys %s",
            label,
            sorted(missing) or "none",
            sorted(unexpected) or "none",
        )
        raise MovieVaultV2Error("record_invalid")


def _asset_variant(value: Any, contract_version: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise MovieVaultV2Error("record_invalid")
    _exact_keys(value, required={"path", "checksum"}, optional=set(), label="asset variant")
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
            label="asset",
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
        label="poster",
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


def _language_code(value: Any, *, release_id: str) -> str:
    """Parse a track language code with the same leniency as its neighbours.

    Type and length stay strict - those bound what is stored. The *shape* does
    not: this was the only fatal check among the distribution-4 track fields,
    while codec, channels, immersiveFormat, subtitleType, packaging, resolution,
    aspect ratios, HDR formats and disc regions all log-and-keep an unrecognized
    value. It was also the field most likely to vary, because MovieVault applies
    no pattern of its own to the published value: the v4 schema constrains
    languageCode to a string of at most 35 characters, and the publisher
    re-asserts audioTracks and subtitles as raw rows after the model dump, so its
    own BCP-47 pattern never runs on what ships. A perfectly ordinary "en-US",
    "pt-BR" or "zh-Hans" therefore failed here and cost the entire release
    record, and with it the whole sync. Losing a film over the casing of a
    language tag is the wrong trade.
    """
    if not isinstance(value, str) or not value or len(value) > 35:
        logger.warning(
            "movievault_v2: unusable languageCode %r on release %s - rejecting record",
            value,
            release_id,
        )
        raise MovieVaultV2Error("record_invalid")
    if not LANGUAGE_CODE_PATTERN.fullmatch(value):
        logger.warning(
            "movievault_v2: unrecognized languageCode %r on release %s - storing raw value",
            value,
            release_id,
        )
    return value


def _audio_track(value: Any, *, release_id: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MovieVaultV2Error("record_invalid")
    _exact_keys(
        value,
        required={"languageCode", "codec", "channels", "immersiveFormat"},
        optional=set(),
        label="audio track",
    )
    codec = value["codec"]
    if not isinstance(codec, str) or not codec or len(codec) > 64:
        raise MovieVaultV2Error("record_invalid")
    if codec not in AUDIO_TRACK_CODECS:
        # Forward-compatible on purpose: MovieVault may ship a new codec
        # value before this allow-list is updated. Store it as-is rather
        # than rejecting the whole release record - see the note on
        # AUDIO_TRACK_CODECS above.
        logger.warning(
            "movievault_v2: unrecognized audio codec %r on release %s - storing raw value",
            codec,
            release_id,
        )
    channels = value["channels"]
    if channels is not None:
        if not isinstance(channels, str) or not channels or len(channels) > 16:
            raise MovieVaultV2Error("record_invalid")
        if channels not in AUDIO_TRACK_CHANNELS:
            logger.warning(
                "movievault_v2: unrecognized audio channels %r on release %s - storing raw value",
                channels,
                release_id,
            )
    immersive_format = value["immersiveFormat"]
    if immersive_format is not None:
        if not isinstance(immersive_format, str) or not immersive_format or len(immersive_format) > 64:
            raise MovieVaultV2Error("record_invalid")
        if immersive_format not in AUDIO_TRACK_IMMERSIVE_FORMATS:
            logger.warning(
                "movievault_v2: unrecognized audio immersiveFormat %r on release %s - storing raw value",
                immersive_format,
                release_id,
            )
    return {
        "languageCode": _language_code(value["languageCode"], release_id=release_id),
        "codec": codec,
        "channels": channels,
        "immersiveFormat": immersive_format,
    }


def _audio_tracks(value: Any, *, release_id: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_AUDIO_TRACKS:
        raise MovieVaultV2Error("record_invalid")
    return [_audio_track(item, release_id=release_id) for item in value]


def _subtitle(value: Any, *, release_id: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MovieVaultV2Error("record_invalid")
    _exact_keys(value, required={"languageCode", "subtitleType"}, optional=set(), label="subtitle track")
    subtitle_type = value["subtitleType"]
    if not isinstance(subtitle_type, str) or not subtitle_type or len(subtitle_type) > 24:
        raise MovieVaultV2Error("record_invalid")
    if subtitle_type not in SUBTITLE_TYPES:
        # Same forward-compat rule as the audio enums: MovieVault may add a
        # variant before this allow-list catches up, and losing one track is
        # better than rejecting the whole release.
        logger.warning(
            "movievault_v2: unrecognized subtitleType %r on release %s - storing raw value",
            subtitle_type,
            release_id,
        )
    return {
        "languageCode": _language_code(value["languageCode"], release_id=release_id),
        "subtitleType": subtitle_type,
    }


def _subtitles(value: Any, *, release_id: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_SUBTITLE_LANGUAGES:
        raise MovieVaultV2Error("record_invalid")
    return [_subtitle(item, release_id=release_id) for item in value]


def _enum_list(
    value: Any,
    *,
    maximum: int,
    allowed: set[str],
    label: str,
    release_id: str,
) -> list[str]:
    """Parse a distribution-4 enum array with the feed's forward-compat rule.

    Unrecognized members are logged and kept; exact repeats are dropped rather
    than rejected, because a duplicate is a producer slip with an obvious
    lossless fix while rejection would cost the whole release record.
    """
    if not isinstance(value, list) or len(value) > maximum:
        raise MovieVaultV2Error("record_invalid")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or len(item) > 32:
            raise MovieVaultV2Error("record_invalid")
        if item not in allowed:
            logger.warning(
                "movievault_v2: unrecognized %s value %r on release %s - storing raw value",
                label,
                item,
                release_id,
            )
        if item not in result:
            result.append(item)
    return result


def _aspect_ratios(value: Any, *, release_id: str) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_ASPECT_RATIOS:
        raise MovieVaultV2Error("record_invalid")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or len(item) > 16:
            raise MovieVaultV2Error("record_invalid")
        if not ASPECT_RATIO_PATTERN.fullmatch(item):
            logger.warning(
                "movievault_v2: unrecognized aspect ratio %r on release %s - storing raw value",
                item,
                release_id,
            )
        if item not in result:
            result.append(item)
    return result


def _video_resolution(value: Any, *, release_id: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 16:
        raise MovieVaultV2Error("record_invalid")
    if value not in VIDEO_RESOLUTIONS:
        logger.warning(
            "movievault_v2: unrecognized videoResolution %r on release %s - storing raw value",
            value,
            release_id,
        )
    return value


def _release_video_fields(value: dict[str, Any], *, release_id: str) -> dict[str, Any]:
    """Decode the five distribution-4 video keys in one place.

    Kept together so the feed's shape is described once: if MovieVault ever
    nests these under a `video` object the way `release-technical-1` does, this
    is the only function that has to change.

    Each key is optional: the live feed carries release records published before
    the technical-fields work landed, which omit them entirely. An absent key
    decodes to the same empty value a record with nothing to report would carry,
    so "not published" and "nothing known" are stored identically - neither is a
    reason to reject the release.
    """
    return {
        "videoResolution": _video_resolution(
            value.get("videoResolution"), release_id=release_id
        ),
        "videoCodecs": _enum_list(
            value.get("videoCodecs") or [],
            maximum=MAX_VIDEO_CODECS,
            allowed=VIDEO_CODECS,
            label="videoCodecs",
            release_id=release_id,
        ),
        "hdrFormats": _enum_list(
            value.get("hdrFormats") or [],
            maximum=MAX_HDR_FORMATS,
            allowed=HDR_FORMATS,
            label="hdrFormats",
            release_id=release_id,
        ),
        "aspectRatios": _aspect_ratios(value.get("aspectRatios") or [], release_id=release_id),
        "discRegions": _enum_list(
            value.get("discRegions") or [],
            maximum=MAX_DISC_REGIONS,
            allowed=DISC_REGIONS,
            label="discRegions",
            release_id=release_id,
        ),
    }


def _packaging(value: Any, *, release_id: str) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_PACKAGING:
        raise MovieVaultV2Error("record_invalid")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or len(item) > 24:
            raise MovieVaultV2Error("record_invalid")
        if item not in PACKAGING_VALUES:
            logger.warning(
                "movievault_v2: unrecognized packaging value %r on release %s - storing raw value",
                item,
                release_id,
            )
        result.append(item)
    return result


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
        # Optional rather than required, even though MovieVault's own v4 schema
        # marks them required. The live feed serves release records that carry
        # `poster` but none of the eight technical fields - poster support landed
        # in distribution-4 before the audio/subtitle/packaging/video work did,
        # and records published in between were never re-projected. Demanding
        # them rejected the record, and a rejected record fails the entire sync:
        # 342 of 359 records on the production feed, which is the whole catalog
        # for the sake of supplementary metadata. A release is identified by its
        # ids, title and barcodes; technical specs are enrichment and their
        # absence is not a reason to lose the film.
        optional.update(
            {
                "audioTracks",
                "subtitles",
                "packaging",
                "videoResolution",
                "videoCodecs",
                "hdrFormats",
                "aspectRatios",
                "discRegions",
            }
        )
    _exact_keys(value, required=required, optional=optional, label="release record")
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
        # A pre-v4 contract has no technical fields at all, and a v4 record may
        # simply omit them (see the note above). Both land on the same empty
        # defaults, so an absent field is stored as "nothing known" rather than
        # costing the record.
        "audioTracks": (
            _audio_tracks(value["audioTracks"], release_id=str(value.get("releaseId")))
            if contract_version == MOVIEVAULT_V4_CONTRACT and "audioTracks" in value
            else []
        ),
        "subtitles": (
            _subtitles(value["subtitles"], release_id=str(value.get("releaseId")))
            if contract_version == MOVIEVAULT_V4_CONTRACT and "subtitles" in value
            else []
        ),
        "packaging": (
            _packaging(value["packaging"], release_id=str(value.get("releaseId")))
            if contract_version == MOVIEVAULT_V4_CONTRACT and "packaging" in value
            else []
        ),
        **(
            _release_video_fields(value, release_id=str(value.get("releaseId")))
            if contract_version == MOVIEVAULT_V4_CONTRACT
            else {
                "videoResolution": None,
                "videoCodecs": [],
                "hdrFormats": [],
                "aspectRatios": [],
                "discRegions": [],
            }
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
    _exact_keys(value, required=required, optional=optional, label="box-set member")
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
    _exact_keys(value, required=required, optional=optional, label="box-set record")
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


def _record_identity(value: Any) -> str:
    """Describe a feed record well enough to find it, using ids only.

    Never includes titles or any other free text: this string is written to the
    operator log, and the v2 feed is consumed anonymously."""
    if not isinstance(value, dict):
        return "<non-object record>"
    parts = []
    for key in ("recordType", "operation", "revision", "entityId", "releaseId", "boxSetId"):
        raw = value.get(key)
        if isinstance(raw, (str, int)) and not isinstance(raw, bool):
            parts.append(f"{key}={raw}")
    return " ".join(parts) or "<record without identifiers>"


def validate_record(
    value: Any,
    *,
    contract_version: str = MOVIEVAULT_V2_CONTRACT,
) -> dict[str, Any]:
    """Validate one feed record, logging which record failed before re-raising.

    A rejected record fails the whole sync, so the operator needs to know which
    one. The nested validators log *what* disagreed; this layer adds *where*, and
    catches the cases that raise without any context of their own."""
    try:
        return _validate_record(value, contract_version=contract_version)
    except MovieVaultV2Error as exc:
        if exc.code == "record_invalid":
            logger.warning(
                "movievault_v2: rejected record (%s) on contract %s",
                _record_identity(value),
                contract_version,
            )
        raise


def _validate_record(
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
            label="delete record",
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
    passthrough_statuses: frozenset[int] = frozenset(),
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
        if exc.code in {204, 409} or exc.code in passthrough_statuses:
            return exc.code, b"", {key.lower(): value for key, value in exc.headers.items()}
        if 300 <= exc.code < 400:
            raise MovieVaultV2Error("redirect_rejected") from exc
        raise MovieVaultV2Error("http_error") from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        raise MovieVaultV2Error("network_error") from exc
    if len(content) > maximum_bytes:
        raise MovieVaultV2Error("response_too_large")
    return status, content, headers


def _release_details_object(
    value: Any,
    *,
    required: set[str],
    optional: set[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MovieVaultV2Error("release_details_response_invalid")
    keys = set(value)
    if not required.issubset(keys) or keys - required - optional:
        raise MovieVaultV2Error("release_details_response_invalid")
    return value


def _release_details_text(
    value: Any,
    *,
    minimum: int = 0,
    maximum: int,
    pattern: str | None = None,
) -> str:
    if (
        not isinstance(value, str)
        or len(value) < minimum
        or len(value) > maximum
        or (pattern is not None and re.fullmatch(pattern, value) is None)
    ):
        raise MovieVaultV2Error("release_details_response_invalid")
    return value


def _release_details_integer(value: Any, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise MovieVaultV2Error("release_details_response_invalid")
    return value


def _release_details_barcode_value(value: Any, *, error_code: str, require_check_digit: bool = True) -> str:
    if not isinstance(value, str) or len(value) not in {8, 12, 13, 14} or not value.isdigit():
        raise MovieVaultV2Error(error_code)
    if not require_check_digit:
        return value
    expected = (
        10
        - sum(
            int(digit) * (3 if index % 2 == 0 else 1)
            for index, digit in enumerate(reversed(value[:-1]))
        )
        % 10
    ) % 10
    if expected != int(value[-1]):
        raise MovieVaultV2Error(error_code)
    return value


def _release_details_barcode(value: Any, *, scopes: set[str]) -> dict[str, str]:
    item = _release_details_object(
        value,
        required={"type", "value", "scope"},
        optional=set(),
    )
    barcode = _release_details_barcode_value(
        item["value"],
        error_code="release_details_response_invalid",
    )
    expected_type = {8: "ean8", 12: "upca", 13: "ean13", 14: "gtin14"}[len(barcode)]
    if item["type"] != expected_type or item["scope"] not in scopes:
        raise MovieVaultV2Error("release_details_response_invalid")
    return {"type": expected_type, "value": barcode, "scope": str(item["scope"])}


def _release_details_alias(value: Any) -> dict[str, Any]:
    item = _release_details_object(
        value,
        required={"title", "kind"},
        optional={"languageCode", "countryCode"},
    )
    if item["kind"] not in {"alternate", "localized", "packaging", "retailer"}:
        raise MovieVaultV2Error("release_details_response_invalid")
    result: dict[str, Any] = {
        "title": _release_details_text(item["title"], minimum=1, maximum=500),
        "kind": item["kind"],
    }
    language = item.get("languageCode")
    if language is not None:
        result["languageCode"] = _release_details_text(
            language,
            maximum=35,
            pattern=r"^[a-z]{2,8}(?:-[a-z0-9]{1,8})*$",
        )
    country = item.get("countryCode")
    if country is not None:
        result["countryCode"] = _release_details_text(
            country,
            maximum=2,
            pattern=r"^[A-Z]{2}$",
        )
    return result


def _release_details_aliases(value: Any, *, maximum: int) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > maximum:
        raise MovieVaultV2Error("release_details_response_invalid")
    aliases = [_release_details_alias(item) for item in value]
    keys = {
        (
            alias["title"].casefold(),
            alias["kind"],
            alias.get("languageCode"),
            alias.get("countryCode"),
        )
        for alias in aliases
    }
    if len(keys) != len(aliases):
        raise MovieVaultV2Error("release_details_response_invalid")
    return aliases


def _release_details_identifiers(value: Any) -> dict[str, str]:
    item = _release_details_object(
        value,
        required=set(),
        optional={"tmdbMovieId", "imdbId"},
    )
    result: dict[str, str] = {}
    if item.get("tmdbMovieId") is not None:
        result["tmdbMovieId"] = _release_details_text(
            item["tmdbMovieId"],
            maximum=20,
            pattern=r"^[0-9]{1,20}$",
        )
    if item.get("imdbId") is not None:
        result["imdbId"] = _release_details_text(
            item["imdbId"],
            maximum=18,
            pattern=r"^tt[0-9]{1,16}$",
        )
    return result


def _release_details_film(value: Any) -> dict[str, Any]:
    item = _release_details_object(
        value,
        required={"title", "identifiers", "links"},
        optional={"year"},
    )
    identifiers = _release_details_identifiers(item["identifiers"])
    links = _release_details_object(
        item["links"],
        required=set(),
        optional={"tmdb", "imdb"},
    )
    expected_links = {
        "tmdb": (
            f"https://www.themoviedb.org/movie/{identifiers['tmdbMovieId']}"
            if "tmdbMovieId" in identifiers
            else None
        ),
        "imdb": (
            f"https://www.imdb.com/title/{identifiers['imdbId']}/"
            if "imdbId" in identifiers
            else None
        ),
    }
    if links != {key: value for key, value in expected_links.items() if value is not None}:
        raise MovieVaultV2Error("release_details_response_invalid")
    result: dict[str, Any] = {
        "title": _release_details_text(item["title"], minimum=1, maximum=500),
        "identifiers": identifiers,
        "links": links,
    }
    if item.get("year") is not None:
        result["year"] = _release_details_integer(item["year"], minimum=1870, maximum=2200)
    return result


def _release_details_enum_list(
    value: Any,
    *,
    maximum: int,
    allowed: set[str],
    label: str = "value",
) -> list[str]:
    """Parse an enum array from the release-details resolver.

    Two policy changes from the original, aligning this path with the sync
    feed's parsers (see the note on AUDIO_TRACK_CODECS):

    - An unrecognized member is logged and kept, not rejected. Rejecting raised
      `release_details_response_invalid`, which discards the *entire* resolve
      response - so MovieVault shipping one new codec value blanked every field
      of every lookup until this repo caught up.
    - An exact duplicate is dropped rather than rejected. It is a producer slip
      with an obvious lossless fix, and it is not worth the whole response.
    """
    if not isinstance(value, list) or len(value) > maximum:
        raise MovieVaultV2Error("release_details_response_invalid")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or len(item) > 32:
            raise MovieVaultV2Error("release_details_response_invalid")
        if item not in allowed:
            logger.warning(
                "movievault_v2: unrecognized release-details %s value %r - storing raw value",
                label,
                item,
            )
        if item not in result:
            result.append(item)
    return result


def _release_details_language_list(value: Any, *, maximum: int) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) > maximum
        or any(not isinstance(item, str) for item in value)
        or len(set(value)) != len(value)
    ):
        raise MovieVaultV2Error("release_details_response_invalid")
    return [
        _release_details_text(
            item,
            maximum=35,
            pattern=r"^[a-z]{2,8}(?:-[a-z0-9]{1,8})*$",
        )
        for item in value
    ]


def _release_details_video(value: Any) -> dict[str, Any]:
    item = _release_details_object(
        value,
        required=set(),
        optional={"resolution", "codecs", "hdrFormats", "aspectRatios"},
    )
    result: dict[str, Any] = {}
    if item.get("resolution") is not None:
        result["resolution"] = _video_resolution(item["resolution"], release_id="<resolver>")
    if "codecs" in item:
        result["codecs"] = _release_details_enum_list(
            item["codecs"],
            maximum=MAX_VIDEO_CODECS,
            allowed=VIDEO_CODECS,
            label="videoCodecs",
        )
    if "hdrFormats" in item:
        result["hdrFormats"] = _release_details_enum_list(
            item["hdrFormats"],
            maximum=MAX_HDR_FORMATS,
            allowed=HDR_FORMATS,
            label="hdrFormats",
        )
    if "aspectRatios" in item:
        # The old regex here was `^(?:1|2)\.[0-9]{2}:1$`, which rejected "16:9",
        # "4:3" and "1.375:1" - all real, common ratios - and did so by discarding
        # the whole resolve response. ASPECT_RATIO_PATTERN matches what MovieVault
        # actually permits, and an odd value is now logged and kept.
        result["aspectRatios"] = _aspect_ratios(item["aspectRatios"], release_id="<resolver>")
    return result


def _release_details_subtitle_track(value: Any) -> dict[str, Any]:
    """One structured subtitle track from the v2 resolver.

    `subtitleType` is an **open** enum, matching how the distribution-4 reader
    treats the same field: MovieVault may add a variant before this allow-list
    catches up, and losing a track over a value we simply have not heard of is
    worse than carrying it through. An unreadable *shape* is still refused.
    """
    item = _release_details_object(
        value,
        required={"languageCode", "subtitleType"},
        optional=set(),
    )
    subtitle_type = item["subtitleType"]
    if not isinstance(subtitle_type, str) or not subtitle_type or len(subtitle_type) > 24:
        raise MovieVaultV2Error("release_details_response_invalid")
    return {
        "languageCode": _release_details_text(
            item["languageCode"],
            maximum=35,
            pattern=r"^[a-z]{2,8}(?:-[a-z0-9]{1,8})*$",
        ),
        "subtitleType": subtitle_type,
    }


def _release_details_audio_track(value: Any) -> dict[str, Any]:
    item = _release_details_object(
        value,
        required={"languageCode", "codec"},
        optional={"channels", "immersiveFormat"},
    )
    if item["codec"] not in {
        "pcm",
        "dolby_digital",
        "dolby_digital_plus",
        "dolby_truehd",
        "dts",
        "dts_hd_hr",
        "dts_hd_ma",
        "mpeg_audio",
        "aac",
    }:
        raise MovieVaultV2Error("release_details_response_invalid")
    result: dict[str, Any] = {
        "languageCode": _release_details_text(
            item["languageCode"],
            maximum=35,
            pattern=r"^[a-z]{2,8}(?:-[a-z0-9]{1,8})*$",
        ),
        "codec": item["codec"],
    }
    if item.get("channels") is not None:
        if item["channels"] not in {"1.0", "2.0", "5.1", "6.1", "7.1"}:
            raise MovieVaultV2Error("release_details_response_invalid")
        result["channels"] = item["channels"]
    if item.get("immersiveFormat") is not None:
        if item["immersiveFormat"] not in {"dolby_atmos", "dts_x", "auro_3d"}:
            raise MovieVaultV2Error("release_details_response_invalid")
        result["immersiveFormat"] = item["immersiveFormat"]
    return result


def _release_details_release(value: Any) -> dict[str, Any]:
    item = _release_details_object(
        value,
        required={"barcodes", "title"},
        optional={
            "alternateTitles",
            "format",
            "edition",
            "discCount",
            "regions",
            "packaging",
            "video",
            "audioTracks",
            "subtitleLanguages",
            # Structured subtitles, the same shape distribution-4 uses. Accepted
            # before MovieVault emits it, and that order is not optional: this
            # reader rejects the *whole* response on an unknown key, so a purely
            # additive field on the producer side takes barcode resolution down
            # until this list knows about it. See App-Guidance
            # `docs/apps/discvault/movievault-route-parity.md` §4.
            "subtitles",
        },
    )
    barcodes_value = item["barcodes"]
    if not isinstance(barcodes_value, list) or not 1 <= len(barcodes_value) <= 25:
        raise MovieVaultV2Error("release_details_response_invalid")
    barcodes = [
        _release_details_barcode(barcode, scopes={"package"})
        for barcode in barcodes_value
    ]
    if len({barcode["value"] for barcode in barcodes}) != len(barcodes):
        raise MovieVaultV2Error("release_details_response_invalid")
    result: dict[str, Any] = {
        "barcodes": barcodes,
        "title": _release_details_text(item["title"], minimum=1, maximum=500),
    }
    if "alternateTitles" in item:
        result["alternateTitles"] = _release_details_aliases(item["alternateTitles"], maximum=25)
    for key, maximum in (("format", 80), ("edition", 255)):
        if item.get(key) is not None:
            result[key] = _release_details_text(item[key], maximum=maximum)
    if item.get("discCount") is not None:
        result["discCount"] = _release_details_integer(
            item["discCount"],
            minimum=1,
            maximum=999,
        )
    if "regions" in item:
        result["regions"] = _release_details_enum_list(
            item["regions"],
            maximum=MAX_DISC_REGIONS,
            allowed=DISC_REGIONS,
            label="discRegions",
        )
    if "packaging" in item:
        result["packaging"] = _release_details_enum_list(
            item["packaging"],
            maximum=MAX_PACKAGING,
            allowed={
                "keep_case",
                "amaray",
                "steelbook",
                "slipcover",
                "slipcase",
                "digibook",
                "mediabook",
                "digipak",
                "box",
            },
        )
    if "video" in item:
        result["video"] = _release_details_video(item["video"])
    if "audioTracks" in item:
        tracks = item["audioTracks"]
        if not isinstance(tracks, list) or len(tracks) > 50:
            raise MovieVaultV2Error("release_details_response_invalid")
        result["audioTracks"] = [_release_details_audio_track(track) for track in tracks]
    if "subtitleLanguages" in item:
        result["subtitleLanguages"] = _release_details_language_list(
            item["subtitleLanguages"],
            maximum=50,
        )
    # Structured subtitles win over the flat list when both arrive. They are the
    # same tracks - `subtitleLanguages` is the de-duplicated language view of
    # `subtitles` - so keeping both would hand the merge two representations of
    # one fact and a rule about which is right. Dropping the poorer one here is
    # that rule.
    if "subtitles" in item:
        tracks = item["subtitles"]
        if not isinstance(tracks, list) or len(tracks) > 50:
            raise MovieVaultV2Error("release_details_response_invalid")
        result["subtitles"] = [_release_details_subtitle_track(track) for track in tracks]
        result.pop("subtitleLanguages", None)
    return result


def _release_details_member(value: Any) -> dict[str, Any]:
    item = _release_details_object(
        value,
        required={"position", "title"},
        optional={
            "alternateTitles",
            "year",
            "barcodes",
            "discNumber",
            "discFormat",
            "identifiers",
        },
    )
    result: dict[str, Any] = {
        "position": _release_details_integer(item["position"], minimum=1, maximum=30),
        "title": _release_details_text(item["title"], minimum=1, maximum=500),
    }
    if "alternateTitles" in item:
        result["alternateTitles"] = _release_details_aliases(item["alternateTitles"], maximum=10)
    if item.get("year") is not None:
        result["year"] = _release_details_integer(item["year"], minimum=1870, maximum=2200)
    if "barcodes" in item:
        barcodes = item["barcodes"]
        if not isinstance(barcodes, list) or len(barcodes) > 10:
            raise MovieVaultV2Error("release_details_response_invalid")
        result["barcodes"] = [
            _release_details_barcode(barcode, scopes={"member", "disc"})
            for barcode in barcodes
        ]
        if len({barcode["value"] for barcode in result["barcodes"]}) != len(
            result["barcodes"]
        ):
            raise MovieVaultV2Error("release_details_response_invalid")
    if item.get("discNumber") is not None:
        result["discNumber"] = _release_details_integer(
            item["discNumber"],
            minimum=1,
            maximum=999,
        )
    if item.get("discFormat") is not None:
        result["discFormat"] = _release_details_text(item["discFormat"], maximum=80)
    if item.get("identifiers") is not None:
        result["identifiers"] = _release_details_identifiers(item["identifiers"])
    return result


def _release_details_box_set(value: Any) -> dict[str, Any]:
    item = _release_details_object(
        value,
        required={"state", "title", "members"},
        optional={"alternateTitles", "format", "barcodes"},
    )
    if item["state"] not in {"explicit", "candidate"}:
        raise MovieVaultV2Error("release_details_response_invalid")
    members_value = item["members"]
    if not isinstance(members_value, list) or not 2 <= len(members_value) <= 30:
        raise MovieVaultV2Error("release_details_response_invalid")
    members = [_release_details_member(member) for member in members_value]
    positions = [member["position"] for member in members]
    if positions != sorted(positions) or len(set(positions)) != len(positions):
        raise MovieVaultV2Error("release_details_response_invalid")
    result: dict[str, Any] = {
        "state": item["state"],
        "title": _release_details_text(item["title"], minimum=1, maximum=500),
        "members": members,
    }
    if "alternateTitles" in item:
        result["alternateTitles"] = _release_details_aliases(item["alternateTitles"], maximum=25)
    if item.get("format") is not None:
        result["format"] = _release_details_text(item["format"], maximum=80)
    if "barcodes" in item:
        barcodes = item["barcodes"]
        if not isinstance(barcodes, list) or len(barcodes) > 25:
            raise MovieVaultV2Error("release_details_response_invalid")
        result["barcodes"] = [
            _release_details_barcode(barcode, scopes={"box_set"})
            for barcode in barcodes
        ]
        if len({barcode["value"] for barcode in result["barcodes"]}) != len(
            result["barcodes"]
        ):
            raise MovieVaultV2Error("release_details_response_invalid")
    return result


def _release_details_asset_variant(value: Any) -> dict[str, Any]:
    """Parse a `distribution-4` asset variant carried by a release-details
    response, where `checksum` is optional.

    Only the v4 catalog publishes a checksum; the v2 resolver's poster
    reference specifies just `path`. Requiring one here (as the bulk-sync
    parser does) rejected every resolver poster outright."""
    if not isinstance(value, dict):
        raise MovieVaultV2Error("release_details_response_invalid")
    try:
        _exact_keys(value, required={"path"}, optional={"checksum"}, label="release-details poster")
        path = _text(value["path"], minimum=1, maximum=500)
        if not ASSET_PATH_PATTERNS[MOVIEVAULT_V4_CONTRACT].fullmatch(path):
            raise MovieVaultV2Error("release_details_response_invalid")
        variant = {"path": path}
        if value.get("checksum") is not None:
            variant["checksum"] = _hash(value["checksum"])
    except MovieVaultV2Error as exc:
        raise MovieVaultV2Error("release_details_response_invalid") from exc
    return variant


def _asset_claim(value: Any) -> str | None:
    """Read an `attestation`/`license` claim that the two sources type
    differently: the v4 catalog sends a plain string while the v2 resolver
    renders it as a nested object. An unexpected shape degrades to absent
    rather than failing the record -- a poster is supplementary, and losing a
    release over an artwork sub-field is never the right trade."""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for key in ("value", "name", "id", "type", "license", "attestation"):
            inner = value.get(key)
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
    return None


def _release_details_poster(value: Any) -> dict[str, Any]:
    """Parse a poster carried by a release-details response.

    The bulk-sync parser cannot be reused: it requires `attestation`,
    `license` and a per-variant `checksum`, all of which are optional on this
    path. A *readable* claim is still held to the approved attestation/licence
    sets, so artwork DiscVault is not cleared to show is still refused. Only a
    claim that is absent -- or whose shape cannot be read at all -- is recorded
    as absent, which is what keeps a supplementary artwork sub-field from
    costing the whole record."""
    if not isinstance(value, dict):
        raise MovieVaultV2Error("release_details_response_invalid")
    try:
        _exact_keys(
            value,
            required={"assetId", "assetType", "thumbnail", "display"},
            optional={"attestation", "license"},
            label="release-details asset",
        )
        if value["assetType"] != POSTER_ASSET_TYPE:
            raise MovieVaultV2Error("release_details_response_invalid")
        asset_id = _uuid(value["assetId"])
    except MovieVaultV2Error as exc:
        raise MovieVaultV2Error("release_details_response_invalid") from exc
    attestation = _asset_claim(value.get("attestation"))
    license_name = _asset_claim(value.get("license"))
    if attestation is not None and attestation not in POSTER_ATTESTATIONS:
        raise MovieVaultV2Error("release_details_response_invalid")
    if license_name is not None and license_name not in POSTER_LICENSES:
        raise MovieVaultV2Error("release_details_response_invalid")
    return {
        "assetId": asset_id,
        "assetType": POSTER_ASSET_TYPE,
        "attestation": attestation,
        "license": license_name,
        "thumbnail": _release_details_asset_variant(value["thumbnail"]),
        "display": _release_details_asset_variant(value["display"]),
    }


def validate_release_details_response(value: Any) -> dict[str, Any]:
    item = _release_details_object(
        value,
        required={"contractVersion", "status"},
        optional={
            "verificationStatus",
            "film",
            "release",
            "boxSet",
            "poster",
            "boxSetPoster",
            "moderation",
            "resolutionId",
            "retryAfterSeconds",
            "errorCode",
        },
    )
    if item["contractVersion"] != RELEASE_DETAILS_CONTRACT:
        raise MovieVaultV2Error("release_details_response_invalid")
    status = item["status"]
    if status == "pending":
        _release_details_object(
            item,
            required={"contractVersion", "status", "resolutionId", "retryAfterSeconds"},
            optional=set(),
        )
        try:
            resolution_id = str(uuid.UUID(str(item["resolutionId"])))
        except (ValueError, AttributeError) as exc:
            raise MovieVaultV2Error("release_details_response_invalid") from exc
        return {
            "contractVersion": RELEASE_DETAILS_CONTRACT,
            "status": "pending",
            "resolutionId": resolution_id,
            "retryAfterSeconds": _release_details_integer(
                item["retryAfterSeconds"],
                minimum=1,
                maximum=60,
            ),
        }
    if status in {"canonical_hit", "external_hit"}:
        required = {
            "contractVersion",
            "status",
            "verificationStatus",
            "film",
            "release",
        }
        if status == "external_hit":
            required.add("moderation")
        _release_details_object(
            item,
            required=required,
            optional={"boxSet", "poster", "boxSetPoster"},
        )
        expected_verification = (
            "canonical" if status == "canonical_hit" else "unreviewed_external"
        )
        if item["verificationStatus"] != expected_verification:
            raise MovieVaultV2Error("release_details_response_invalid")
        result: dict[str, Any] = {
            "contractVersion": RELEASE_DETAILS_CONTRACT,
            "status": status,
            "verificationStatus": expected_verification,
            "film": _release_details_film(item["film"]),
            "release": _release_details_release(item["release"]),
        }
        if item.get("boxSet") is not None:
            result["boxSet"] = _release_details_box_set(item["boxSet"])
        # The approved catalog poster is resolved from the canonical entity the
        # requested barcode points at, never from a plugin result, so it is read
        # for `external_hit` exactly like `canonical_hit` - `verificationStatus`
        # describes the provenance of the technical data only (issue #402).
        if item.get("poster") is not None:
            result["poster"] = _release_details_poster(item["poster"])
        if item.get("boxSetPoster") is not None:
            if "boxSet" not in result:
                raise MovieVaultV2Error("release_details_response_invalid")
            result["boxSetPoster"] = _release_details_poster(item["boxSetPoster"])
        if status == "external_hit":
            moderation = _release_details_object(
                item["moderation"],
                required={"candidateId", "status"},
                optional=set(),
            )
            if (
                re.fullmatch(r"^discovery_[A-Za-z0-9_-]{12,64}$", str(moderation["candidateId"]))
                is None
                or moderation["status"] not in {"pending", "accepted", "rejected"}
            ):
                raise MovieVaultV2Error("release_details_response_invalid")
            result["moderation"] = {
                "candidateId": moderation["candidateId"],
                "status": moderation["status"],
            }
        return result
    if status in {"ambiguous", "miss"}:
        _release_details_object(
            item,
            required={"contractVersion", "status"},
            optional=set(),
        )
        return {"contractVersion": RELEASE_DETAILS_CONTRACT, "status": status}
    if status == "failed":
        _release_details_object(
            item,
            required={"contractVersion", "status", "errorCode"},
            optional=set(),
        )
        return {
            "contractVersion": RELEASE_DETAILS_CONTRACT,
            "status": "failed",
            "errorCode": _release_details_text(
                item["errorCode"],
                minimum=3,
                maximum=80,
                pattern=r"^[a-z0-9_]{3,80}$",
            ),
        }
    raise MovieVaultV2Error("release_details_response_invalid")


def _release_details_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or "barcode" not in value:
        raise MovieVaultV2Error("release_details_request_invalid")
    allowed = {"barcode", "title", "year", "edition", "format"}
    if set(value) - allowed:
        raise MovieVaultV2Error("release_details_request_invalid")
    result: dict[str, Any] = {
        "barcode": _release_details_barcode_value(
            value["barcode"],
            error_code="release_details_request_invalid",
            # MovieVault assigned this barcode, not DiscVault - a check digit
            # that disagrees with the textbook mod-10 formula (real, if unusual,
            # in retail packaging) is MovieVault's call, not a reason to refuse
            # asking. Only shape (digits-only, valid length) gates the request.
            require_check_digit=False,
        )
    }
    for key, maximum in (("title", 500), ("edition", 255), ("format", 80)):
        candidate = value.get(key)
        if candidate is None:
            continue
        if not isinstance(candidate, str):
            raise MovieVaultV2Error("release_details_request_invalid")
        clean = " ".join(candidate.split())
        if not clean:
            continue
        if len(clean) > maximum:
            raise MovieVaultV2Error("release_details_request_invalid")
        result[key] = clean
    if value.get("year") is not None:
        year = value["year"]
        if isinstance(year, bool) or not isinstance(year, int) or not 1870 <= year <= 2200:
            raise MovieVaultV2Error("release_details_request_invalid")
        result["year"] = year
    return result


def _release_details_http(
    url: str,
    *,
    method: str,
    timeout_seconds: int,
    payload: dict[str, Any] | None = None,
) -> tuple[int, bytes]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "DiscVault-MovieVault-v2",
    }
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers=headers,
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            status = int(response.status)
            content = response.read(DEFAULT_MAX_RELEASE_DETAILS_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise MovieVaultV2Error("redirect_rejected") from exc
        if exc.code in {404, 409, 422, 429, 503}:
            return exc.code, b""
        raise MovieVaultV2Error("release_details_http_error") from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        raise MovieVaultV2Error("release_details_network_error") from exc
    if len(content) > DEFAULT_MAX_RELEASE_DETAILS_BYTES:
        raise MovieVaultV2Error("release_details_response_too_large")
    return status, content


def _release_details_http_error(status: int) -> MovieVaultV2Error:
    return MovieVaultV2Error(
        {
            404: "release_details_expired",
            409: "release_details_conflict",
            422: "release_details_request_invalid",
            429: "release_details_rate_limited",
            503: "release_details_unavailable",
        }.get(status, "release_details_http_error")
    )


def _decode_release_details_response(status: int, content: bytes) -> dict[str, Any]:
    if status not in {200, 202}:
        raise _release_details_http_error(status)
    try:
        value = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MovieVaultV2Error("release_details_response_invalid") from exc
    result = validate_release_details_response(value)
    if status == 202 and result["status"] != "pending":
        raise MovieVaultV2Error("release_details_response_invalid")
    if status == 200 and result["status"] == "pending":
        raise MovieVaultV2Error("release_details_response_invalid")
    return result


def resolve_release_details(
    settings: dict[str, Any],
    request: dict[str, Any],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    origin = enforced_origin()
    timeout_seconds = _bounded_setting(
        settings.get("requestTimeoutSeconds"),
        default=DEFAULT_TIMEOUT_SECONDS,
        minimum=2,
        maximum=120,
        code="settings_invalid",
    )
    poll_attempts = _bounded_setting(
        settings.get("releaseDetailsPollAttempts"),
        default=DEFAULT_RELEASE_DETAILS_POLL_ATTEMPTS,
        minimum=1,
        maximum=MAX_RELEASE_DETAILS_POLL_ATTEMPTS,
        code="settings_invalid",
    )
    payload = _release_details_payload(request)
    status, content = _release_details_http(
        f"{origin}/v2/release-details/resolve",
        method="POST",
        timeout_seconds=timeout_seconds,
        payload=payload,
    )
    result = _decode_release_details_response(status, content)
    for _attempt in range(poll_attempts):
        if result["status"] != "pending":
            return result
        wait_seconds = result["retryAfterSeconds"]
        if wait_seconds > MAX_RELEASE_DETAILS_POLL_WAIT_SECONDS:
            raise MovieVaultV2Error("release_details_retry_after_invalid")
        sleep(wait_seconds)
        resolution_id = result["resolutionId"]
        status, content = _release_details_http(
            f"{origin}/v2/release-details/resolve/{resolution_id}",
            method="GET",
            timeout_seconds=timeout_seconds,
        )
        result = _decode_release_details_response(status, content)
    if result["status"] == "pending":
        raise MovieVaultV2Error("release_details_poll_timeout")
    return result


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


def _replace_audio_tracks(
    cur: Any, generation: str, release_id: str, tracks: list[dict[str, Any]]
) -> None:
    """Full replacement, not a diff: always delete first, even when `tracks`
    is empty, since an empty array is meaningful (all tracks removed
    upstream)."""
    cur.execute(
        """
        DELETE FROM movievault_v2_release_audio_tracks
        WHERE generation = %s AND release_id = %s
        """,
        (generation, release_id),
    )
    for position, track in enumerate(tracks, start=1):
        cur.execute(
            """
            INSERT INTO movievault_v2_release_audio_tracks (
                generation, release_id, position, language_code, codec,
                channels, immersive_format
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                generation,
                release_id,
                position,
                track["languageCode"],
                track["codec"],
                track["channels"],
                track["immersiveFormat"],
            ),
        )


def _replace_subtitles(
    cur: Any, generation: str, release_id: str, subtitles: list[dict[str, Any]]
) -> None:
    """Full replacement, not a diff.

    Always deletes first, even when `subtitles` is empty, since an empty array is
    meaningful: it says the release has no recorded subtitles.
    """
    cur.execute(
        """
        DELETE FROM movievault_v2_release_subtitle_languages
        WHERE generation = %s AND release_id = %s
        """,
        (generation, release_id),
    )
    for position, subtitle in enumerate(subtitles, start=1):
        cur.execute(
            """
            INSERT INTO movievault_v2_release_subtitle_languages (
                generation, release_id, position, language_code, subtitle_type
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                generation,
                release_id,
                position,
                subtitle["languageCode"],
                subtitle["subtitleType"],
            ),
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
            runtime_minutes, assets, revision, poster, packaging,
            video_resolution, video_codecs, hdr_formats, aspect_ratios,
            disc_regions
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s
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
            poster = EXCLUDED.poster,
            packaging = EXCLUDED.packaging,
            video_resolution = EXCLUDED.video_resolution,
            video_codecs = EXCLUDED.video_codecs,
            hdr_formats = EXCLUDED.hdr_formats,
            aspect_ratios = EXCLUDED.aspect_ratios,
            disc_regions = EXCLUDED.disc_regions
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
            Jsonb(record.get("packaging") or []),
            record.get("videoResolution"),
            Jsonb(record.get("videoCodecs") or []),
            Jsonb(record.get("hdrFormats") or []),
            Jsonb(record.get("aspectRatios") or []),
            Jsonb(record.get("discRegions") or []),
        ),
    )
    for lookup_hash in record["eanHashes"]:
        _insert_lookup(cur, generation, lookup_hash, "release", release_id, "release_ean")
    _replace_audio_tracks(cur, generation, release_id, record.get("audioTracks") or [])
    _replace_subtitles(cur, generation, release_id, record.get("subtitles") or [])
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
    reached_cursor: str,
    reached_revision: int,
    final: bool,
) -> dict[str, Any]:
    """Apply one delta segment. ``reached_cursor``/``reached_revision`` are what this
    segment's own response actually advances the sync to - the manifest's cursor and
    revision only when ``final`` is true, an intermediate hop's otherwise (see the
    hop loop in run_sync). ``status`` and ``dataset_checksum`` only move to 'current'
    /the manifest's checksum on the final hop: they describe the complete dataset,
    which an intermediate hop has not reached yet."""
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
                dataset_checksum = CASE WHEN %s THEN %s ELSE dataset_checksum END,
                status = CASE WHEN %s THEN 'current' ELSE status END,
                last_success_at = now(),
                last_error_code = NULL,
                updated_at = now()
            WHERE plugin_id = %s
            """,
            (
                reached_cursor,
                reached_revision,
                final,
                manifest["datasetChecksum"],
                final,
                MOVIEVAULT_V2_PLUGIN_ID,
            ),
        )
    return {
        "state": "current" if final else "syncing",
        "mode": "delta",
        "revision": reached_revision,
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
    origin = enforced_origin()
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
            hops = 0
            records_applied = 0
            while True:
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
                next_cursor = headers.get("x-next-cursor")
                # The delta feed serves one publication segment per request, keyed by
                # the cursor it starts from. An instance more than one MovieVault
                # publish behind gets an intermediate segment back - its next-cursor
                # is the next hop, not the manifest's head - so treating anything
                # short of the head as invalid used to fail the sync every time,
                # permanently, because the stored cursor never advanced past the
                # first hop. Walk the chain instead; apply each segment as it comes.
                if not next_cursor or next_cursor == cursor:
                    raise MovieVaultV2Error("cursor_invalid")
                at_head = next_cursor == manifest["currentCursor"]
                records = parse_ndjson(
                    content,
                    full=False,
                    maximum_revision=manifest["currentRevision"],
                    contract_version=contract_version,
                )
                if (
                    at_head
                    and int(manifest["currentRevision"]) > int(state.get("revision") or 0)
                    and (
                        not records
                        or int(records[-1]["revision"]) != int(manifest["currentRevision"])
                    )
                ):
                    raise MovieVaultV2Error("revision_invalid")
                reached_revision = (
                    int(manifest["currentRevision"])
                    if at_head
                    else (
                        int(records[-1]["revision"])
                        if records
                        else int(state.get("revision") or 0)
                    )
                )
                result = _apply_delta(
                    conn,
                    origin=origin,
                    expected_cursor=cursor,
                    manifest=manifest,
                    records=records,
                    reached_cursor=next_cursor,
                    reached_revision=reached_revision,
                    final=at_head,
                )
                conn.commit()
                records_applied += len(records)
                result["recordsApplied"] = records_applied
                if at_head:
                    return result
                cursor = next_cursor
                state = _sync_state(conn)
                hops += 1
                if hops >= MAX_DELTA_HOPS_PER_SYNC:
                    # Real, persisted progress either way - just not all the way to
                    # the head yet. The next sync (scheduled or manual) picks up the
                    # chain from here rather than starting over.
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
    return f"/api/next/movievault-v2/posters/{media_asset_id}"


POSTER_FIELDS_UNAVAILABLE: dict[str, Any] = {
    "posterUrl": None,
    "posterStatus": "unavailable",
    "posterChecksum": None,
    "posterRevision": None,
}


def _poster_status_fields(conn: Any, poster: dict[str, Any] | None) -> dict[str, Any]:
    """Map a persisted MovieVault poster descriptor onto authenticated,
    DiscVault-local fields. The raw poster object (remote MovieVault asset
    paths) is never returned to a caller; only a local media-asset URL,
    cache status, checksum, and a checksum-derived revision are exposed."""
    if poster is None:
        return dict(POSTER_FIELDS_UNAVAILABLE)
    asset_id = poster.get("assetId")
    display = poster.get("display") or {}
    checksum = display.get("checksum")
    if not asset_id or not checksum:
        return dict(POSTER_FIELDS_UNAVAILABLE)
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


def _remote_asset_url(origin: str, path: Any) -> str | None:
    """Resolve a root-relative `distribution-4` asset path against the
    MovieVault origin.

    ``/v2/assets/{assetId}/{variant}`` is a stable anonymous endpoint (no auth,
    no cookies, no instance identity), so the absolute URL is safe to hand to a
    client that loads it like any other remote poster."""
    text = str(path or "").strip()
    if not text.startswith("/"):
        return None
    return f"{origin.rstrip('/')}{text}"


def _release_details_poster_fields(
    conn: Any,
    origin: str,
    poster: dict[str, Any] | None,
) -> dict[str, Any]:
    """Expose a poster carried by a release-details response.

    With a checksum the bulk-sync cache is reused: enqueue the same bounded,
    anonymous caching job (idempotent on ``assetId``/variant/checksum, so an
    already-cached poster never re-downloads) and return a DiscVault-local URL.

    The v2 resolver publishes no checksum, and bytes that cannot be verified
    must never be stored -- caching them would break the guarantee that
    everything on disk was checked. Such a poster is therefore surfaced as the
    stable anonymous asset URL instead, so the cover still reaches the client
    without an unverifiable copy being written."""
    if poster is None:
        return dict(POSTER_FIELDS_UNAVAILABLE)
    display = poster.get("display") if isinstance(poster.get("display"), dict) else {}
    if not display.get("checksum"):
        remote_url = _remote_asset_url(origin, display.get("path"))
        if not remote_url:
            return dict(POSTER_FIELDS_UNAVAILABLE)
        return {
            "posterUrl": remote_url,
            "posterStatus": "remote",
            "posterChecksum": None,
            "posterRevision": None,
        }
    with conn.transaction():
        with conn.cursor() as cur:
            _enqueue_poster_cache(cur, origin, poster)
    return _poster_status_fields(conn, poster)


def localize_release_details_posters(
    conn: Any,
    origin: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Replace the optional remote poster references on a release-details
    result with authenticated, DiscVault-local poster fields.

    ``poster`` describes the resolved release and ``boxSetPoster`` the box set,
    so each is attached to its own object and never shared with a member
    release. The release -> poster association is taken exclusively from this
    response: when a later response omits ``poster`` the fields fall back to
    ``unavailable``, so a withdrawn or replaced poster stops being shown
    instead of lingering from an earlier lookup."""
    if not isinstance(result, dict) or result.get("status") not in {
        "canonical_hit",
        "external_hit",
    }:
        return result
    localized = dict(result)
    poster = localized.pop("poster", None)
    box_set_poster = localized.pop("boxSetPoster", None)
    release = localized.get("release")
    if isinstance(release, dict):
        localized["release"] = {
            **release,
            **_release_details_poster_fields(conn, origin, poster),
        }
    box_set = localized.get("boxSet")
    if isinstance(box_set, dict):
        localized["boxSet"] = {
            **box_set,
            **_release_details_poster_fields(conn, origin, box_set_poster),
        }
    return localized


def _release_details_without_posters(result: dict[str, Any]) -> dict[str, Any]:
    """Fallback used when the local poster cache cannot be reached: drop the
    remote poster references (they are never exposed to a caller) and report
    the poster as unavailable. The technical release data is returned intact so
    a poster failure never fails the surrounding barcode lookup."""
    if not isinstance(result, dict) or result.get("status") not in {
        "canonical_hit",
        "external_hit",
    }:
        return result
    stripped = dict(result)
    stripped.pop("poster", None)
    stripped.pop("boxSetPoster", None)
    for key in ("release", "boxSet"):
        section = stripped.get(key)
        if isinstance(section, dict):
            stripped[key] = {**section, **POSTER_FIELDS_UNAVAILABLE}
    return stripped


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
        "packaging": _json_value(row.get("packaging") or []),
        "videoResolution": row.get("video_resolution"),
        "videoCodecs": _json_value(row.get("video_codecs") or []),
        "hdrFormats": _json_value(row.get("hdr_formats") or []),
        "aspectRatios": _json_value(row.get("aspect_ratios") or []),
        "discRegions": _json_value(row.get("disc_regions") or []),
    }
    payload.update(_poster_status_fields(conn, row.get("poster")))
    payload["audioTracks"], payload["subtitles"] = _release_track_fields(
        conn, row["generation"], row["release_id"]
    )
    return payload


def _release_track_fields(
    conn: Any, generation: Any, release_id: Any
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT language_code, codec, channels, immersive_format
            FROM movievault_v2_release_audio_tracks
            WHERE generation = %s AND release_id = %s
            ORDER BY position
            """,
            (generation, release_id),
        )
        audio_rows = cur.fetchall()
        cur.execute(
            """
            SELECT language_code, subtitle_type
            FROM movievault_v2_release_subtitle_languages
            WHERE generation = %s AND release_id = %s
            ORDER BY position
            """,
            (generation, release_id),
        )
        subtitle_rows = cur.fetchall()
    audio_tracks = [
        {
            "languageCode": row["language_code"],
            "codec": row["codec"],
            "channels": row.get("channels"),
            "immersiveFormat": row.get("immersive_format"),
        }
        for row in audio_rows
    ]
    subtitles = [
        {
            "languageCode": row["language_code"],
            "subtitleType": row.get("subtitle_type") or DEFAULT_SUBTITLE_TYPE,
        }
        for row in subtitle_rows
    ]
    return audio_tracks, subtitles


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
    origin = enforced_origin()
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
        """Resolve a barcode hash against MovieVault's anonymous bucket index.

        The fallback is supplementary to the locally synced index, so every
        failure mode - an unreachable origin, a malformed or undigestable bucket,
        an incompatible contract - degrades to an empty result set rather than an
        exception. A raise here would cross into the plugin and fail the whole
        barcode lookup, losing the local hit the caller already has."""
        try:
            return bucket_lookup(
                safe_settings,
                request,
                contract_version=contract_version,
            )
        except MovieVaultV2Error as exc:
            return {"state": "unavailable", "results": [], "errorCode": exc.code}

    def release_details_callback(request: dict[str, Any]) -> dict[str, Any]:
        try:
            result = resolve_release_details(safe_settings, request)
        except MovieVaultV2Error as exc:
            return {
                "contractVersion": RELEASE_DETAILS_CONTRACT,
                "status": "failed",
                "errorCode": exc.code,
            }
        try:
            with _connection_scope(conn, connection_factory) as active_conn:
                return localize_release_details_posters(
                    active_conn,
                    enforced_origin(),
                    result,
                )
        except Exception:
            # Poster caching is best effort: an unavailable poster must never
            # fail a barcode lookup that already produced technical data.
            return _release_details_without_posters(result)

    return {
        **context,
        "movievaultV2Lookup": lookup_callback,
        "movievaultV2Status": status_callback,
        "movievaultV2Sync": sync_callback,
        "movievaultV2BucketLookup": bucket_callback,
        "movievaultV2BucketFallback": enforced_bucket_fallback(),
        "movievaultV2ReleaseDetails": release_details_callback,
        "movievaultDistributionContract": contract_version,
        "movievaultDistributionContractRange": {
            "minimum": SUPPORTED_CONTRACTS[0],
            "maximum": SUPPORTED_CONTRACTS[-1],
        },
    }
