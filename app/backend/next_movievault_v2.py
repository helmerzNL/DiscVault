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
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import nullcontext
from datetime import date, datetime, timezone
from typing import Any, Callable, ContextManager

try:  # pragma: no cover - supports gunicorn next_app:app
    from .next_packaging import (
        LEGACY_PACKAGING_VALUES,
        ALL_PACKAGING_VALUES,
        FINISH_VALUES,
        MAX_FINISHES,
        MAX_LEGACY_PACKAGING,
        MAX_PACKAGING_V5,
        split_legacy_packaging,
    )
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_packaging import (
        LEGACY_PACKAGING_VALUES,
        ALL_PACKAGING_VALUES,
        FINISH_VALUES,
        MAX_FINISHES,
        MAX_LEGACY_PACKAGING,
        MAX_PACKAGING_V5,
        split_legacy_packaging,
    )

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
MOVIEVAULT_V5_CONTRACT = "distribution-5"
MOVIEVAULT_V6_CONTRACT = "distribution-6"
# Ordered lowest to highest. _negotiated_contract() returns the *maximum* of
# this plugin's declared range - despite the name it does not negotiate, it
# never asks the origin what it actually serves. So listing a contract here is
# inert, but raising the manifest's `distributionContractRange.maximum` is not:
# DiscVault would immediately request /v<max>, and an origin that has not
# activated that contract answers 503, which fails the whole sync. Support is
# added here first; the manifest range is raised only once the origin serves
# the contract AND has it switched on.
SUPPORTED_CONTRACTS = (
    MOVIEVAULT_V2_CONTRACT,
    MOVIEVAULT_V3_CONTRACT,
    MOVIEVAULT_V4_CONTRACT,
    MOVIEVAULT_V5_CONTRACT,
    MOVIEVAULT_V6_CONTRACT,
)
CONTRACT_PATH_VERSIONS = {
    MOVIEVAULT_V2_CONTRACT: "2",
    MOVIEVAULT_V3_CONTRACT: "3",
    MOVIEVAULT_V4_CONTRACT: "4",
    MOVIEVAULT_V5_CONTRACT: "5",
    MOVIEVAULT_V6_CONTRACT: "6",
}
# Contract feature predicates. Every technical field used to be gated on an
# equality check against distribution-4, which quietly means "v4 only" - so
# adding v5 by equality would have had to repeat `or v5` at fourteen sites, and
# missing one would drop that field on v5 with nothing failing. These say what
# is actually meant: the field exists from that version onward.
def _is_v3_or_later(contract_version: str) -> bool:
    return contract_version in (
        MOVIEVAULT_V3_CONTRACT,
        MOVIEVAULT_V4_CONTRACT,
        MOVIEVAULT_V5_CONTRACT,
        MOVIEVAULT_V6_CONTRACT,
    )


def _is_v4_or_later(contract_version: str) -> bool:
    """True for the contracts carrying posters and the technical profile."""
    return contract_version in (
        MOVIEVAULT_V4_CONTRACT,
        MOVIEVAULT_V5_CONTRACT,
        MOVIEVAULT_V6_CONTRACT,
    )


def _is_v5_or_later(contract_version: str) -> bool:
    """True for the contracts carrying the full packaging vocabulary and finishes."""
    return contract_version in (MOVIEVAULT_V5_CONTRACT, MOVIEVAULT_V6_CONTRACT)


def _is_v6_or_later(contract_version: str) -> bool:
    """True for the contracts carrying the per-disc breakdown."""
    return contract_version == MOVIEVAULT_V6_CONTRACT


SYNC_LOCK_KEY = 2_026_261
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_MAX_MANIFEST_BYTES = 64 * 1024
DEFAULT_MAX_ARTIFACT_BYTES = 128 * 1024 * 1024
DEFAULT_MAX_BUCKET_BYTES = 8 * 1024 * 1024
RELEASE_DETAILS_CONTRACT = "release-technical-1"
DEFAULT_MAX_RELEASE_DETAILS_BYTES = 256 * 1024
DEFAULT_RELEASE_DETAILS_POLL_ATTEMPTS = 4
# The Add flow's fallback waits longer than a metadata refresh does. A barcode
# that falls through to a title search reads several pages from a source that
# paces itself at one request every five seconds, so twenty seconds sits inside
# the chain's own pacing and thirty is what a client needs to budget. At the
# resolver's two-second retry interval that is fifteen attempts.
#
# Stopping short does not stop the work: the server finishes the resolution and
# caches it for about fifteen minutes either way, so a short budget only means
# this scan shows nothing while the next scan of the same disc answers instantly.
ADD_FLOW_RELEASE_DETAILS_POLL_ATTEMPTS = 15
MAX_RELEASE_DETAILS_POLL_ATTEMPTS = 20
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
# NOTE: MovieVault public asset paths are deliberately `/v2/assets/...` for every
# contract version (the producer never versions this path); only the contract
# envelope itself is versioned. Which is exactly why this table is easy to
# forget when a version is added -- every value is identical, so it reads like
# a constant and is in fact a required per-version entry: `_asset_variant`
# indexes it with a bare `[contract_version]`, so a missing key is a KeyError
# on the first record carrying artwork, and it takes the whole sync with it.
# distribution-6 shipped without its row and did precisely that.
ASSET_PATH_PATTERNS = {
    MOVIEVAULT_V2_CONTRACT: re.compile(r"^/v2/assets/[0-9a-f-]+/(thumbnail|display)$"),
    MOVIEVAULT_V3_CONTRACT: re.compile(r"^/v2/assets/[0-9a-f-]+/(thumbnail|display)$"),
    MOVIEVAULT_V4_CONTRACT: re.compile(r"^/v2/assets/[0-9a-f-]+/(thumbnail|display)$"),
    MOVIEVAULT_V5_CONTRACT: re.compile(r"^/v2/assets/[0-9a-f-]+/(thumbnail|display)$"),
    MOVIEVAULT_V6_CONTRACT: re.compile(r"^/v2/assets/[0-9a-f-]+/(thumbnail|display)$"),
}
POSTER_ASSET_TYPE = "front_cover"
# Poster attestation/licence vocabularies. These mirror the distribution-v6
# `PublicPosterReference` enums (MovieVault-v2
# docs/contracts/distribution-v6.schema.json) and DiscVault's manifest declares
# `distribution-6` support, so every value the schema permits must be accepted
# here -- a value the feed is allowed to send but this allow-list rejects raises
# `record_invalid` in `_poster`, and because `parse_ndjson` is all-or-nothing
# that single record takes the whole feed sync down with it.
#
# `unverified` / `unverified-fan-submitted` are MovieVault's provisional
# provider-poster values (shipped by MovieVault 0.23.0): a poster shown ahead of
# moderation. DiscVault accepts and shows these like any other poster -- there
# is deliberately no suppression/hide path, because surfacing artwork early is
# the whole point of the provisional feature. The two sets are shared by both
# consumers of a poster claim, `_poster` (bulk distribution sync) and
# `_release_details_poster` (the v2 resolver path a scanned disc uses), so
# widening them here keeps both paths in step.
POSTER_ATTESTATIONS = {"original", "licensed", "unverified"}
POSTER_LICENSES = {
    "cc0-1.0",
    "cc-by-4.0",
    "cc-by-sa-4.0",
    "unverified-fan-submitted",
}

# The provisional attestation and licence are a matched pair: MovieVault's
# `PublicAssetReference.unverified_fields_are_paired` invariant guarantees
# `attestation:"unverified"` iff `license:"unverified-fan-submitted"`, so it
# never emits a mixed pair (e.g. `unverified` + `cc0-1.0`). Mirroring the
# invariant here keeps DiscVault from silently accepting a claim MovieVault's
# own producer would refuse to make. Both individual values are already vetted
# against the enum sets above; this only rejects the cross-field combination.
_POSTER_PROVISIONAL_ATTESTATION = "unverified"
_POSTER_PROVISIONAL_LICENSE = "unverified-fan-submitted"


def _poster_provisional_claims_are_paired(
    attestation: str | None, license_name: str | None
) -> bool:
    return (attestation == _POSTER_PROVISIONAL_ATTESTATION) == (
        license_name == _POSTER_PROVISIONAL_LICENSE
    )

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

# The seasons a television release covers. MovieVault caps its own list at 100
# and season numbers at 0-200; both are mirrored here rather than derived,
# because this side must stay sane if the producer ever widens its own limits.
# Season 0 is specials, on TMDB and on the disc, which is why the floor is 0.
MAX_SEASONS = 100
MIN_SEASON_NUMBER = 0
MAX_SEASON_NUMBER = 200

# distribution-4 packaging enum. Same forward-compat leniency as the audio
# track enums above - an unrecognized value is stored as-is with a logged
# warning rather than rejecting the whole release record.
#
# The vocabulary itself lives in next_packaging, not here: it drives the edit
# form and the collection views too, so a sync provider is the wrong owner.
# Re-exported under the original names so existing call sites keep working.
PACKAGING_VALUES = LEGACY_PACKAGING_VALUES
MAX_PACKAGING = MAX_LEGACY_PACKAGING

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

try:
    from .dedup_identity import MEDIA_TYPE_MOVIE, MEDIA_TYPE_SHOW, normalize_media_type
except ImportError:  # pragma: no cover - supports running modules directly
    from dedup_identity import MEDIA_TYPE_MOVIE, MEDIA_TYPE_SHOW, normalize_media_type

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
    if not _poster_provisional_claims_are_paired(attestation, license_name):
        raise MovieVaultV2Error("record_invalid")
    return {
        "assetId": _uuid(value["assetId"]),
        "assetType": POSTER_ASSET_TYPE,
        "attestation": attestation,
        "license": license_name,
        "thumbnail": _asset_variant(value["thumbnail"], contract_version),
        "display": _asset_variant(value["display"], contract_version),
    }


def _backdrop(value: Any, *, release_id: str) -> None:
    """Log-and-discard MovieVault-v2's distribution-4 `backdrop` field.

    Added after `poster` (Fanart.tv artwork source, MovieVault ADR 0008) as a
    nullable field on every release upsert. DiscVault has no backdrop feature
    or storage for it yet, so this intentionally never persists or enforces
    its shape - only `poster`'s existing required-key/enum strictness matters,
    because DiscVault actually consumes that value. Treating an unrecognized
    or malformed backdrop as fatal would repeat the exact mistake the
    surrounding optional technical fields were made lenient for: a field this
    version does not use costing the whole release record, and with a full
    sync being all-or-nothing per `parse_ndjson()`, the whole catalog."""
    if value is not None and not isinstance(value, dict):
        logger.warning(
            "movievault_v2: malformed backdrop %r on release %s - ignoring",
            value,
            release_id,
        )


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


def _packaging(value: Any, *, release_id: str, contract_version: str) -> list[str]:
    """Parse the feed's flat packaging list.

    The cap depends on the contract: v4 publishes at most the nine values it
    knows, v5 the twelve the catalogue stores. Note the asymmetry in
    strictness, which is deliberate and long-standing - an unrecognized *value*
    is logged and kept, because MovieVault may ship vocabulary before this
    allow-list catches up, but an over-long list or an over-long item raises
    `record_invalid`, which fails the whole synchronization rather than one
    record. The caps must therefore match MovieVault's MAX_PACKAGING exactly;
    they are a contract term, not a local sanity check.
    """
    maximum = MAX_PACKAGING_V5 if _is_v5_or_later(contract_version) else MAX_PACKAGING
    if not isinstance(value, list) or len(value) > maximum:
        raise MovieVaultV2Error("record_invalid")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or len(item) > 24:
            raise MovieVaultV2Error("record_invalid")
        if item not in ALL_PACKAGING_VALUES:
            logger.warning(
                "movievault_v2: unrecognized packaging value %r on release %s - storing raw value",
                item,
                release_id,
            )
        result.append(item)
    return result


def _finishes(value: Any, *, release_id: str) -> list[str]:
    """Parse the feed's finish list. distribution-5 and later only.

    Same posture as _packaging: lenient about vocabulary, strict about size.
    """
    if not isinstance(value, list) or len(value) > MAX_FINISHES:
        raise MovieVaultV2Error("record_invalid")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or len(item) > 24:
            raise MovieVaultV2Error("record_invalid")
        if item not in FINISH_VALUES:
            logger.warning(
                "movievault_v2: unrecognized finish value %r on release %s - storing raw value",
                item,
                release_id,
            )
        result.append(item)
    return result


MAX_FEED_DISCS = 99


def _discs(value: Any, *, release_id: str) -> list[dict[str, Any]]:
    """Parse the per-disc breakdown. distribution-6 and later.

    Same posture as ``_seasons`` directly below, for the same paid-for lesson:
    skip what cannot be read, log it, keep the rest. A release whose disc list
    is unusable arrives with no discs, which is exactly the state it was in
    before this field existed -- while a raise here is not one lost breakdown
    but a dead catalog for every instance.

    Entries are kept verbatim (the mirror stores what the feed said, enums
    open), with only shape checks: a dict, an int position 1..99, and the
    remaining keys strings or lists as published. Order is by position, which
    is the producer's own ordering rule.
    """
    if not isinstance(value, list):
        logger.warning(
            "movievault_v2: discs on release %s is not a list - ignoring",
            release_id,
        )
        return []
    if len(value) > MAX_FEED_DISCS:
        logger.warning(
            "movievault_v2: discs on release %s exceeds %d entries - truncating",
            release_id,
            MAX_FEED_DISCS,
        )
        value = value[:MAX_FEED_DISCS]
    discs: list[dict[str, Any]] = []
    seen_positions: set[int] = set()
    for entry in value:
        if not isinstance(entry, dict):
            logger.warning(
                "movievault_v2: skipping non-object disc on release %s", release_id
            )
            continue
        position = entry.get("position")
        if (
            not isinstance(position, int)
            or isinstance(position, bool)
            or not 1 <= position <= MAX_FEED_DISCS
            or position in seen_positions
        ):
            logger.warning(
                "movievault_v2: skipping disc with unusable position %r on release %s",
                position,
                release_id,
            )
            continue
        seen_positions.add(position)
        discs.append(entry)
    return sorted(discs, key=lambda disc: disc["position"])


def _seasons(value: Any, *, release_id: str) -> list[dict[str, Any]]:
    """Parse the seasons a release covers. distribution-4 and later.

    Unlike every other parser in this file, this one never raises. 563 landed the
    tolerance for this key precisely because it cannot arrive gradually -- upstream
    publishes it on *every* release record, so a rejection here is not one lost
    season list but a dead catalog for every instance. Having just paid for that
    lesson, refusing a malformed shape one layer deeper would reintroduce it.

    So the posture is: skip what cannot be read, log it, keep the rest. A release
    whose season list is unusable arrives with no seasons, which is exactly the
    state it was in before this column existed.

    `[]` and `None` are different answers and both are returned as-is by the
    caller: `[]` is MovieVault saying the release covers no particular season,
    while a missing key means it has not said. Only the first is a statement.
    """
    if not isinstance(value, list):
        logger.warning(
            "movievault_v2: seasons on release %s is not a list - ignoring",
            release_id,
        )
        return []
    if len(value) > MAX_SEASONS:
        logger.warning(
            "movievault_v2: seasons on release %s exceeds %d entries - truncating",
            release_id,
            MAX_SEASONS,
        )
        value = value[:MAX_SEASONS]
    seasons: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in value:
        if not isinstance(item, dict):
            logger.warning(
                "movievault_v2: season entry on release %s is not an object - skipping",
                release_id,
            )
            continue
        number = item.get("seasonNumber")
        # bool is an int in Python, and `True` must not become season 1.
        if not isinstance(number, int) or isinstance(number, bool):
            logger.warning(
                "movievault_v2: season on release %s has no usable seasonNumber - skipping",
                release_id,
            )
            continue
        if not MIN_SEASON_NUMBER <= number <= MAX_SEASON_NUMBER:
            logger.warning(
                "movievault_v2: season %d on release %s is out of range - skipping",
                number,
                release_id,
            )
            continue
        if number in seen:
            # Upstream enforces one row per (film, season number) and rejects a
            # duplicate before publishing, so this can only be producer drift.
            # Keeping the first is arbitrary but stable; the alternative is
            # writing the same season twice into series_seasons, which its own
            # unique index would then refuse mid-sync.
            logger.warning(
                "movievault_v2: duplicate season %d on release %s - keeping the first",
                number,
                release_id,
            )
            continue
        seen.add(number)
        seasons.append(
            {
                "seasonNumber": number,
                "title": item["title"] if isinstance(item.get("title"), str) else None,
                "releaseYear": (
                    item["releaseYear"]
                    if isinstance(item.get("releaseYear"), int)
                    and not isinstance(item.get("releaseYear"), bool)
                    else None
                ),
                "episodeCount": (
                    item["episodeCount"]
                    if isinstance(item.get("episodeCount"), int)
                    and not isinstance(item.get("episodeCount"), bool)
                    and item["episodeCount"] >= 0
                    else None
                ),
            }
        )
    return seasons


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
    if _is_v3_or_later(contract_version):
        optional.update({"studio", "distributor", "runtimeMinutes"})
    if _is_v4_or_later(contract_version):
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
    if _is_v5_or_later(contract_version):
        # distribution-5's one addition. Optional for the same reason as the
        # eight above: a v5 record projected before the field existed simply
        # has no key, and a missing key must never cost the record.
        optional.add("finishes")
    if _is_v4_or_later(contract_version):
        # Nullable, added after poster/assets (Fanart.tv artwork source, ADR
        # 0008). Not stored - see _backdrop()'s own docstring.
        optional.add("backdrop")
        # `movie` or `tv`: whether the underlying work is a film or a series.
        # Optional for the same reason as `backdrop` above, and the reason is
        # worth restating because forgetting it has cost a full catalog outage
        # once already: a published artefact is immutable, so records projected
        # before MovieVault added this field simply have no key, and one
        # unrecognised key fails the *whole* feed rather than the record that
        # carries it. Listing the field here is what keeps a MovieVault release
        # from taking every DiscVault instance's sync down with it.
        optional.add("workType")
        # Which seasons a television release covers. Listed here purely so the
        # key is tolerated; nothing consumes it yet.
        #
        # This one is not merely additive-in-principle: MovieVault publishes
        # `seasons` on *every* v4 release record, including `[]` for a film,
        # because upstream an empty list is a statement ("the complete series,
        # or unspecified") rather than an omission. So the moment an origin
        # ships that change, every record carries a key this allow-list did not
        # have - and by the rule above that is not a degraded sync but no sync
        # at all. Tolerating the key has to reach instances before the origin
        # does, which is why it lands separately from anything that reads it.
        optional.add("seasons")
    if _is_v6_or_later(contract_version):
        # distribution-6's one addition: the per-disc breakdown. Optional
        # because the producer omits it for a release nobody has broken down,
        # which is most of the catalogue.
        optional.add("discs")
    _exact_keys(value, required=required, optional=optional, label="release record")
    if _is_v4_or_later(contract_version):
        _backdrop(value.get("backdrop"), release_id=str(value.get("releaseId")))
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
            if _is_v4_or_later(contract_version)
            else None
        ),
        # A pre-v4 contract has no technical fields at all, and a v4 record may
        # simply omit them (see the note above). Both land on the same empty
        # defaults, so an absent field is stored as "nothing known" rather than
        # costing the record.
        "audioTracks": (
            _audio_tracks(value["audioTracks"], release_id=str(value.get("releaseId")))
            if _is_v4_or_later(contract_version) and "audioTracks" in value
            else []
        ),
        "subtitles": (
            _subtitles(value["subtitles"], release_id=str(value.get("releaseId")))
            if _is_v4_or_later(contract_version) and "subtitles" in value
            else []
        ),
        "packaging": (
            _packaging(
                value["packaging"],
                release_id=str(value.get("releaseId")),
                contract_version=contract_version,
            )
            if _is_v4_or_later(contract_version) and "packaging" in value
            else []
        ),
        "finishes": (
            _finishes(value["finishes"], release_id=str(value.get("releaseId")))
            if _is_v5_or_later(contract_version) and "finishes" in value
            else []
        ),
        # None means "the feed has not said", which must stay distinguishable
        # from an explicit "movie": only the second may overwrite a stored type.
        # Anything outside MovieVault's vocabulary is treated as unsaid rather
        # than guessed at.
        "workType": (
            value["workType"]
            if _is_v4_or_later(contract_version)
            and value.get("workType") in ("movie", "tv")
            else None
        ),
        # None and [] are different answers and the difference is load-bearing.
        # A missing key means the feed has not said - a record projected before
        # MovieVault carried seasons, or a pre-v4 contract. `[]` means it has
        # said, and the answer is "no particular season": a film, or a
        # complete-series set. Only the second may clear an existing season list.
        "seasons": (
            _seasons(value["seasons"], release_id=str(value.get("releaseId")))
            if _is_v4_or_later(contract_version) and "seasons" in value
            else None
        ),
        # Same None-versus-[] rule as seasons: a missing key is "the feed has
        # not said", an empty list is a statement, and only the second may
        # clear a stored breakdown.
        "discs": (
            _discs(value["discs"], release_id=str(value.get("releaseId")))
            if _is_v6_or_later(contract_version) and "discs" in value
            else None
        ),
        **(
            _release_video_fields(value, release_id=str(value.get("releaseId")))
            if _is_v4_or_later(contract_version)
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
    if _is_v3_or_later(contract_version):
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
    if _is_v4_or_later(contract_version):
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
            if _is_v4_or_later(contract_version)
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


# Unknown release-details keys already logged, as (label, key). Only bounded by
# MovieVault's own vocabulary, so it cannot grow without a producer change - the
# point is that a per-scan warning does not become a per-scan log flood.
_RELEASE_DETAILS_UNKNOWN_KEYS_SEEN: set[tuple[str, str]] = set()


def _release_details_object(
    value: Any,
    *,
    required: set[str],
    optional: set[str],
    label: str = "object",
) -> dict[str, Any]:
    """Read one object out of a release-details answer.

    A missing *required* key is still fatal - it means the object does not
    describe what this branch claims it describes. An **unknown** key is not:
    it is logged once and dropped, and the caller gets a copy without it.

    This used to reject the whole response, which inverted the safe direction
    of change: a purely additive field on MovieVault's side took barcode
    scanning down on discvault.eu until this repo caught up. It happened with
    `subtitles` (2026-08-04) and again with `finishes` (2026-08-09), and the
    remedy recorded after the first one - ship the consumer first - is an
    ordering convention that nothing enforces. See App-Guidance
    `docs/apps/discvault/movievault-route-parity.md` §4.

    Dropping rather than passing through is the load-bearing half: every parser
    in this family builds its result key by key from names it declared, so an
    ignored key can never reach the database, the picker, or a contribution
    sent back to MovieVault.

    The bulk sync feed keeps its exact-key reader (`_exact_keys`) on purpose.
    There a surprise key means the record is not what it claims, and the
    synchronization can be repeated; a resolve answer cannot.
    """
    if not isinstance(value, dict):
        raise MovieVaultV2Error("release_details_response_invalid")
    keys = set(value)
    if not required.issubset(keys):
        raise MovieVaultV2Error("release_details_response_invalid")
    unknown = keys - required - optional
    if not unknown:
        return value
    fresh = sorted(key for key in unknown if (label, key) not in _RELEASE_DETAILS_UNKNOWN_KEYS_SEEN)
    if fresh:
        _RELEASE_DETAILS_UNKNOWN_KEYS_SEEN.update((label, key) for key in fresh)
        logger.warning(
            "movievault_v2: ignoring unknown release-details %s key(s) %s",
            label,
            fresh,
        )
    return {key: item for key, item in value.items() if key not in unknown}


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
        label="barcode",
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
        label="alternate title",
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
        label="film identifiers",
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
        label="film",
    )
    identifiers = _release_details_identifiers(item["identifiers"])
    # A link DiscVault did not derive itself is never rendered, so the two known
    # links must agree exactly with the identifiers beside them. An *unknown*
    # link is a different matter: the resolver chain already runs a Wikidata
    # step, and comparing the whole dict would turn the day it publishes a third
    # link into an outage. Checked per key, and only the two are kept.
    links = _release_details_object(
        item["links"],
        required=set(),
        optional={"tmdb", "imdb"},
        label="film links",
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
    for key, expected in expected_links.items():
        if links.get(key) != expected:
            raise MovieVaultV2Error("release_details_response_invalid")
    links = {key: value for key, value in expected_links.items() if value is not None}
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
        label="video profile",
    )
    result: dict[str, Any] = {}
    # `_video_resolution` and `_aspect_ratios` are shared with the sync feed and
    # raise its `record_invalid`. That code is not in the resolver's failure-kind
    # table, so it audits a shape DiscVault refused as MovieVault's failure -
    # the same misattribution this whole path is being fixed for. Re-raise under
    # the release-details code, as the poster and box-set readers already do.
    if item.get("resolution") is not None:
        try:
            result["resolution"] = _video_resolution(item["resolution"], release_id="<resolver>")
        except MovieVaultV2Error as exc:
            raise MovieVaultV2Error("release_details_response_invalid") from exc
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
        try:
            result["aspectRatios"] = _aspect_ratios(item["aspectRatios"], release_id="<resolver>")
        except MovieVaultV2Error as exc:
            raise MovieVaultV2Error("release_details_response_invalid") from exc
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
        label="subtitle track",
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


def _release_details_open_enum(
    value: Any,
    *,
    allowed: set[str],
    maximum: int,
    label: str,
) -> str:
    """One enum scalar from the resolver: shape strict, vocabulary open.

    A value of the right type and within its bound is kept verbatim; only its
    membership of the allow-list is advisory, and an unrecognized one is logged.
    This is the posture `_release_details_enum_list` and
    `_release_details_subtitle_track` already argue for, and the reason is the
    same: losing a whole resolve answer over a codec name we have not heard of
    is worse than carrying the name through. Downstream is ready for it -
    `next_metadata.normalize_audio_track_entry` validates track *keys*, not
    their vocabulary.

    Not for values that select a code path (`status`, `verificationStatus`,
    `source`, `moderation.status`, a barcode's `type`/`scope`). There an
    unrecognized value has no safe default, so those stay closed.
    """
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise MovieVaultV2Error("release_details_response_invalid")
    if value not in allowed:
        logger.warning(
            "movievault_v2: unrecognized release-details %s value %r - storing raw value",
            label,
            value,
        )
    return value


def _release_details_audio_track(value: Any) -> dict[str, Any]:
    item = _release_details_object(
        value,
        required={"languageCode", "codec"},
        optional={"channels", "immersiveFormat"},
        label="audio track",
    )
    result: dict[str, Any] = {
        "languageCode": _release_details_text(
            item["languageCode"],
            maximum=35,
            pattern=r"^[a-z]{2,8}(?:-[a-z0-9]{1,8})*$",
        ),
        # The bounds mirror what `normalize_audio_track_entry` truncates to, so
        # a kept-raw value surprises nothing further down.
        "codec": _release_details_open_enum(
            item["codec"],
            allowed=AUDIO_TRACK_CODECS,
            maximum=64,
            label="audio codec",
        ),
    }
    if item.get("channels") is not None:
        result["channels"] = _release_details_open_enum(
            item["channels"],
            allowed=AUDIO_TRACK_CHANNELS,
            maximum=16,
            label="audio channels",
        )
    if item.get("immersiveFormat") is not None:
        result["immersiveFormat"] = _release_details_open_enum(
            item["immersiveFormat"],
            allowed=AUDIO_TRACK_IMMERSIVE_FORMATS,
            maximum=64,
            label="immersive format",
        )
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
            # Structured subtitles, the same shape distribution-4 uses.
            "subtitles",
            # Surface treatment, the distribution-5 axis. Naming a key here no
            # longer decides whether the response survives - `_release_details_object`
            # drops what it does not know - it decides whether the field is
            # *read*. See App-Guidance `docs/apps/discvault/movievault-route-parity.md` §4.
            "finishes",
        },
        label="release",
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
            allowed=PACKAGING_VALUES,
            label="packaging",
        )
    if "finishes" in item:
        result["finishes"] = _release_details_enum_list(
            item["finishes"],
            maximum=MAX_FINISHES,
            allowed=FINISH_VALUES,
            label="finishes",
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


def _release_details_release_summary(value: Any) -> dict[str, Any]:
    """One entry of a `candidates` answer's `releases[]`.

    Deliberately more permissive than `_release_details_release`: a summary can
    describe a pressing an unreviewed external source merely claims to know, so
    barcodes and the technical profile are optional here while the technical
    release requires them.

    `releaseRef` is opaque - a canonical release UUID, or a provider candidate
    reference - and is never parsed. It is echoed back untouched when the user
    picks that edition, which is the only thing it is for.
    """
    item = _release_details_object(
        value,
        required={"releaseRef", "source", "title"},
        optional={
            "edition",
            "format",
            "countryCode",
            "region",
            "discRegions",
            "languageCode",
            "releaseDate",
            "discCount",
            "runtimeMinutes",
            "studio",
            "distributor",
            "barcodes",
            "packaging",
            "finishes",
            "video",
            "audioTracks",
            "subtitles",
            "subtitleLanguages",
        },
        label="release summary",
    )
    if item["source"] not in {"canonical", "external"}:
        raise MovieVaultV2Error("release_details_response_invalid")
    result: dict[str, Any] = {
        "releaseRef": _release_details_text(item["releaseRef"], minimum=1, maximum=500),
        "source": item["source"],
        "title": _release_details_text(item["title"], minimum=1, maximum=500),
    }
    for key, maximum in (
        ("edition", 255),
        ("format", 80),
        ("region", 80),
        ("studio", 255),
        ("distributor", 255),
    ):
        if item.get(key) is not None:
            result[key] = _release_details_text(item[key], minimum=1, maximum=maximum)
    if item.get("countryCode") is not None:
        result["countryCode"] = _release_details_text(
            item["countryCode"],
            maximum=2,
            pattern=r"^[A-Z]{2}$",
        )
    if item.get("languageCode") is not None:
        result["languageCode"] = _release_details_text(
            item["languageCode"],
            maximum=35,
            pattern=r"^[a-z]{2,8}(?:-[a-z0-9]{1,8})*$",
        )
    if item.get("releaseDate") is not None:
        result["releaseDate"] = _release_details_text(
            item["releaseDate"],
            maximum=10,
            pattern=r"^\d{4}-\d{2}-\d{2}$",
        )
    if item.get("discCount") is not None:
        result["discCount"] = _release_details_integer(item["discCount"], minimum=1, maximum=999)
    if item.get("runtimeMinutes") is not None:
        result["runtimeMinutes"] = _release_details_integer(
            item["runtimeMinutes"],
            minimum=1,
            maximum=10000,
        )
    if "discRegions" in item:
        result["discRegions"] = _release_details_enum_list(
            item["discRegions"],
            maximum=MAX_DISC_REGIONS,
            allowed=DISC_REGIONS,
            label="discRegions",
        )
    if "barcodes" in item:
        barcodes_value = item["barcodes"]
        if not isinstance(barcodes_value, list) or len(barcodes_value) > 25:
            raise MovieVaultV2Error("release_details_response_invalid")
        result["barcodes"] = [
            _release_details_barcode(barcode, scopes={"package"})
            for barcode in barcodes_value
        ]
    if "packaging" in item:
        result["packaging"] = _release_details_enum_list(
            item["packaging"],
            maximum=MAX_PACKAGING,
            allowed=PACKAGING_VALUES,
            label="packaging",
        )
    # The field that broke this path on 2026-08-09: MovieVault emits it on every
    # candidate, this list did not name it, and the closed key set turned that
    # into `release_details_response_invalid` for the whole answer. It is also a
    # real distinguishing fact between two pressings of one film, which is the
    # picker's entire job - so it is read, not merely tolerated.
    if "finishes" in item:
        result["finishes"] = _release_details_enum_list(
            item["finishes"],
            maximum=MAX_FINISHES,
            allowed=FINISH_VALUES,
            label="finishes",
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
    # Same rule as the technical release: the structured tracks are the fact and
    # `subtitleLanguages` is their de-duplicated language view, so keeping both
    # would hand the picker two representations of one thing.
    if "subtitles" in item:
        tracks = item["subtitles"]
        if not isinstance(tracks, list) or len(tracks) > 100:
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
        label="box set member",
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
        label="box set",
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
    # Mirror MovieVault's pairing invariant on this path too (see
    # `_poster_provisional_claims_are_paired`): a lone `unverified` /
    # `unverified-fan-submitted` claim is a defect, and because posters degrade
    # here it costs only the poster, never the record.
    if not _poster_provisional_claims_are_paired(attestation, license_name):
        raise MovieVaultV2Error("release_details_response_invalid")
    return {
        "assetId": asset_id,
        "assetType": POSTER_ASSET_TYPE,
        "attestation": attestation,
        "license": license_name,
        "thumbnail": _release_details_asset_variant(value["thumbnail"]),
        "display": _release_details_asset_variant(value["display"]),
    }


# Release-details poster labels already logged as dropped, keyed by call-site
# label. A malformed supplementary poster is bounded by MovieVault's own
# vocabulary, so - like the unknown-key log above - one warning per label is
# enough to surface a producer regression without a per-scan log flood.
_RELEASE_DETAILS_DROPPED_POSTER_SEEN: set[str] = set()


def _release_details_optional_poster(value: Any, *, label: str) -> dict[str, Any] | None:
    """Parse a supplementary poster, degrading a poster-only defect to absence.

    A poster is artwork layered on top of a record whose load-bearing fields
    (`status`, `film`, `release`) the caller has already validated. Following
    the same safe-direction rule that made unknown keys and the optional
    technical fields non-fatal, a poster whose claim is out-of-enum or whose
    variant is malformed must cost only itself: the film and release still
    reach the client, and only a structural defect blanks the response. This is
    the poster-shaped instance of the degrade-don't-discard fixes already made
    for `subtitles` (2026-08-04) and `finishes` (2026-08-09), and it is what
    `_release_details_poster`'s own docstring promises but its callers did not
    yet honour (FLU-42 provisional-poster contract)."""
    try:
        return _release_details_poster(value)
    except MovieVaultV2Error:
        if label not in _RELEASE_DETAILS_DROPPED_POSTER_SEEN:
            _RELEASE_DETAILS_DROPPED_POSTER_SEEN.add(label)
            logger.warning(
                "movievault_v2: dropping malformed release-details %s - "
                "record kept without it",
                label,
            )
        return None


def validate_release_details_response(value: Any) -> dict[str, Any]:
    item = _release_details_object(
        value,
        required={"contractVersion", "status"},
        optional={
            "verificationStatus",
            "film",
            "release",
            # Only ever present on `candidates`, and never alongside `release`.
            "releases",
            "boxSet",
            "poster",
            "boxSetPoster",
            "moderation",
            "resolutionId",
            "retryAfterSeconds",
            "errorCode",
            # Only ever present on `candidates`; the per-status branch validates
            # and keeps it. Named here as well because this outer reader runs
            # first and drops whatever it does not list - which is what silently
            # blanked the picker's barcode-confidence line (iOS carried it, the
            # PWA did not).
            "barcodeConfirmed",
        },
        label="response",
    )
    if item["contractVersion"] != RELEASE_DETAILS_CONTRACT:
        raise MovieVaultV2Error("release_details_response_invalid")
    status = item["status"]
    # `release` and `releases` answer the same question in two incompatible
    # ways - one confirmed pressing, or a choice between several - and `status`
    # says which was asked. Carrying both is a contradiction, not an additive
    # field, so this stays a refusal even though unknown keys no longer are. It
    # used to be enforced only by the per-status key sets below.
    if "release" in item and "releases" in item:
        raise MovieVaultV2Error("release_details_response_invalid")
    if status == "pending":
        _release_details_object(
            item,
            required={"contractVersion", "status", "resolutionId", "retryAfterSeconds"},
            optional=set(),
            label="pending response",
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
            label="hit response",
        )
        # A confirmed hit carrying a candidate list is the mirror of the
        # contradiction guarded above.
        if "releases" in item:
            raise MovieVaultV2Error("release_details_response_invalid")
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
            poster = _release_details_optional_poster(item["poster"], label="poster")
            if poster is not None:
                result["poster"] = poster
        if item.get("boxSetPoster") is not None:
            # A box-set poster with no box set to hang it on is a poster-only
            # anomaly, not a structural one: drop it and keep the record rather
            # than blanking the answer over a supplementary artwork field.
            if "boxSet" not in result:
                if "boxSetPoster (no box set)" not in _RELEASE_DETAILS_DROPPED_POSTER_SEEN:
                    _RELEASE_DETAILS_DROPPED_POSTER_SEEN.add("boxSetPoster (no box set)")
                    logger.warning(
                        "movievault_v2: dropping release-details boxSetPoster "
                        "carried without a boxSet - record kept without it",
                    )
            else:
                box_set_poster = _release_details_optional_poster(
                    item["boxSetPoster"], label="boxSetPoster"
                )
                if box_set_poster is not None:
                    result["boxSetPoster"] = box_set_poster
        if status == "external_hit":
            moderation = _release_details_object(
                item["moderation"],
                required={"candidateId", "status"},
                optional=set(),
                label="moderation",
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
    if status == "candidates":
        # A source identified the film but could not choose between its
        # pressings. That is a choice for the person holding the disc, so it
        # arrives as a list rather than as a failure - see App-Guidance
        # `docs/apps/discvault/adding-a-title.md`.
        # `poster` here is the 0.25.0 film-level cover: a source recognised the
        # film but not the pressing, yet can still show its artwork while the
        # user picks an edition. It degrades exactly like the hit-path posters.
        #
        # `barcodeConfirmed` is the resolver's statement that the scanned code is
        # itself printed on the pressings listed, as opposed to the film having
        # been found by title with the barcode unconfirmed. It has to be named
        # here or the object reader below drops it as unknown - which is exactly
        # what silently blanked the confidence signal the picker shows, so the
        # PWA always fell back to the cautious "barcode was not confirmed" line
        # even when MovieVault had confirmed it. iOS carried the flag; DiscVault
        # dropped it. See release_details_search_payload, which reads it back.
        _release_details_object(
            item,
            required={"contractVersion", "status", "film", "releases"},
            optional={"verificationStatus", "poster", "barcodeConfirmed"},
            label="candidates response",
        )
        releases_value = item["releases"]
        if not isinstance(releases_value, list) or not 1 <= len(releases_value) <= 50:
            raise MovieVaultV2Error("release_details_response_invalid")
        result = {
            "contractVersion": RELEASE_DETAILS_CONTRACT,
            "status": "candidates",
            "film": _release_details_film(item["film"]),
            "releases": [
                _release_details_release_summary(entry) for entry in releases_value
            ],
        }
        if item.get("poster") is not None:
            poster = _release_details_optional_poster(item["poster"], label="poster")
            if poster is not None:
                result["poster"] = poster
        if item.get("verificationStatus") is not None:
            if item["verificationStatus"] not in {"canonical", "unreviewed_external"}:
                raise MovieVaultV2Error("release_details_response_invalid")
            result["verificationStatus"] = item["verificationStatus"]
        # Tri-state, carried through only as a real boolean. `true`/`false` are
        # the resolver's own claim; absent stays absent so an older MovieVault
        # that says nothing is never made to assert the barcode is unconfirmed.
        if item.get("barcodeConfirmed") is not None:
            if not isinstance(item["barcodeConfirmed"], bool):
                raise MovieVaultV2Error("release_details_response_invalid")
            result["barcodeConfirmed"] = item["barcodeConfirmed"]
        return result
    if status in {"ambiguous", "miss"}:
        _release_details_object(
            item,
            required={"contractVersion", "status"},
            optional=set(),
            label=f"{status} response",
        )
        return {"contractVersion": RELEASE_DETAILS_CONTRACT, "status": status}
    if status == "failed":
        _release_details_object(
            item,
            required={"contractVersion", "status", "errorCode"},
            optional=set(),
            label="failed response",
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


def _release_details_search_payload(value: Any) -> dict[str, Any]:
    """The barcode-free title entry point's request body.

    `title` is required and `barcode` is rejected outright - the two entry
    points are separate routes upstream precisely so a title search can never
    be mistaken for a barcode lookup that happens to carry a hint.
    """
    if not isinstance(value, dict) or "title" not in value:
        raise MovieVaultV2Error("release_details_request_invalid")
    allowed = {"title", "year", "edition", "format"}
    if set(value) - allowed:
        raise MovieVaultV2Error("release_details_request_invalid")
    result: dict[str, Any] = {}
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
    if not result.get("title"):
        raise MovieVaultV2Error("release_details_request_invalid")
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
        # A refused connection or a name that does not resolve proves the
        # request never arrived, so retrying it is provably a *first* delivery.
        # A timeout or a dropped connection proves nothing of the sort - the
        # server may already have minted a resolution job - so the two are kept
        # apart under different codes rather than lumped together as "network".
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (ConnectionRefusedError, socket.gaierror)):
            raise MovieVaultV2Error("release_details_unreachable") from exc
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
    try:
        result = validate_release_details_response(value)
    except MovieVaultV2Error as exc:
        # Unknown keys are tolerated above, so a refusal here is a genuine
        # contract violation - but the audit trail still shows one opaque code
        # for every remaining check. The innermost frame names the check, which
        # is what the `finishes` incident (2026-08-09) lacked: it was findable
        # only by replaying the response against the validator by hand.
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        logger.warning(
            "movievault_v2: release-details response rejected (%s) at %s:%s: %s",
            exc.code,
            frame.name,
            frame.lineno,
            frame.line,
        )
        raise
    if status == 202 and result["status"] != "pending":
        raise MovieVaultV2Error("release_details_response_invalid")
    if status == 200 and result["status"] == "pending":
        raise MovieVaultV2Error("release_details_response_invalid")
    return result


def _release_details_bounds(settings: dict[str, Any]) -> tuple[int, int]:
    return (
        _bounded_setting(
            settings.get("requestTimeoutSeconds"),
            default=DEFAULT_TIMEOUT_SECONDS,
            minimum=2,
            maximum=120,
            code="settings_invalid",
        ),
        _bounded_setting(
            settings.get("releaseDetailsPollAttempts"),
            default=DEFAULT_RELEASE_DETAILS_POLL_ATTEMPTS,
            minimum=1,
            maximum=MAX_RELEASE_DETAILS_POLL_ATTEMPTS,
            code="settings_invalid",
        ),
    )


def _poll_release_details(
    result: dict[str, Any],
    *,
    origin: str,
    timeout_seconds: int,
    poll_attempts: int,
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    """Drive a `pending` answer to a terminal one.

    There is exactly one poll route and a title search uses it too - the search
    entry point hands back the same `resolutionId` shape rather than minting a
    second polling path.
    """
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


def resolve_release_details(
    settings: dict[str, Any],
    request: dict[str, Any],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    origin = enforced_origin()
    timeout_seconds, poll_attempts = _release_details_bounds(settings)
    payload = _release_details_payload(request)
    status, content = _release_details_http(
        f"{origin}/v2/release-details/resolve",
        method="POST",
        timeout_seconds=timeout_seconds,
        payload=payload,
    )
    return _poll_release_details(
        _decode_release_details_response(status, content),
        origin=origin,
        timeout_seconds=timeout_seconds,
        poll_attempts=poll_attempts,
        sleep=sleep,
    )


def search_release_details(
    settings: dict[str, Any],
    request: dict[str, Any],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Resolve a film *title* to the physical releases that carry it.

    The entry point a scan falls back to when no source could identify the
    scanned barcode: the film is known by name, the pressing is not, and the
    answer is a list to choose from rather than a single release.
    """
    origin = enforced_origin()
    timeout_seconds, poll_attempts = _release_details_bounds(settings)
    payload = _release_details_search_payload(request)
    status, content = _release_details_http(
        f"{origin}/v2/release-details/search",
        method="POST",
        timeout_seconds=timeout_seconds,
        payload=payload,
    )
    return _poll_release_details(
        _decode_release_details_response(status, content),
        origin=origin,
        timeout_seconds=timeout_seconds,
        poll_attempts=poll_attempts,
        sleep=sleep,
    )


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
    if _is_v3_or_later(contract_version):
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


def _record_sync_failure(conn: Any, code: str) -> None:
    """Persist why a sync failed, without ever becoming the failure itself.

    The caller is already raising. If recording the reason raises too, the
    original exception is replaced by a bookkeeping error and the diagnosis is
    lost - which is precisely how a CheckViolation spent an afternoon looking
    like "current transaction is aborted".
    """
    try:
        conn.rollback()
        _mark_error(conn, code)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


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


# The two "own identity" source types: a hash that is the entity's *own* barcode,
# not a disc it merely contains.
_OWN_IDENTITY_SOURCES = ("release_ean", "box_set_ean")


def _purge_foreign_identity_claims(
    cur: Any,
    generation: str,
    own_hashes: list[str],
    *,
    keep_entity_type: str,
    keep_entity_id: str,
) -> None:
    """Make an entity's own barcode exclusive to it within the generation.

    A delta is applied *in place* on the active generation, and each upsert only
    clears its **own** prior lookup rows (`entity_id = self`). So when a barcode
    is reassigned from one box set/release to another, the record that *loses* it
    is not re-emitted unless it changed for another reason -- and with the feed
    compacted, it usually is not. The losing entity's stale
    `hash -> that entity` row then survives, and because `local_lookup` returns
    every claimant of a hash, the scanned barcode resolves to the wrong box set
    (real symptom: EAN 786936826951 resolved to a 4K "Avengers Assembled:
    Complete 4-Movie Collection" it had been moved off, not the Blu-ray "Marvel
    Cinematic Universe: Phase One" it now belongs to).

    An entity's *own* barcode (`release_ean`/`box_set_ean`) identifies exactly one
    product, so when this entity claims it as its own, any *other* entity's
    own-identity claim on the same hash is stale by definition and is removed here.
    `member_ean`/`member_barcode` rows are deliberately left alone: a disc barcode
    legitimately belongs to a standalone release *and* to every box set that
    contains it, so those are not exclusive and must keep coexisting.
    """
    if not own_hashes:
        return
    cur.execute(
        """
        DELETE FROM movievault_v2_lookup_hashes
        WHERE generation = %s
          AND lookup_hash = ANY(%s)
          AND source_type = ANY(%s)
          AND NOT (entity_type = %s AND entity_id = %s)
        """,
        (
            generation,
            list(own_hashes),
            list(_OWN_IDENTITY_SOURCES),
            keep_entity_type,
            keep_entity_id,
        ),
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
            runtime_minutes, assets, revision, poster, packaging, finishes,
            video_resolution, video_codecs, hdr_formats, aspect_ratios,
            disc_regions, work_type, seasons, discs
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s
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
            finishes = EXCLUDED.finishes,
            video_resolution = EXCLUDED.video_resolution,
            video_codecs = EXCLUDED.video_codecs,
            hdr_formats = EXCLUDED.hdr_formats,
            aspect_ratios = EXCLUDED.aspect_ratios,
            disc_regions = EXCLUDED.disc_regions,
            work_type = EXCLUDED.work_type,
            seasons = EXCLUDED.seasons,
            discs = EXCLUDED.discs
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
            Jsonb(record.get("finishes") or []),
            record.get("videoResolution"),
            Jsonb(record.get("videoCodecs") or []),
            Jsonb(record.get("hdrFormats") or []),
            Jsonb(record.get("aspectRatios") or []),
            Jsonb(record.get("discRegions") or []),
            record.get("workType"),
            # `or []` is wrong here, unlike every list beside it: it would turn
            # "the feed has not said" into "the feed says no seasons", and the
            # consuming side is entitled to tell those apart. NULL stays NULL.
            Jsonb(record["seasons"]) if record.get("seasons") is not None else None,
            Jsonb(record["discs"]) if record.get("discs") is not None else None,
        ),
    )
    _purge_foreign_identity_claims(
        cur,
        generation,
        record["eanHashes"],
        keep_entity_type="release",
        keep_entity_id=release_id,
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
    _purge_foreign_identity_claims(
        cur,
        generation,
        record["eanHashes"],
        keep_entity_type="box_set",
        keep_entity_id=box_set_id,
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
RELEASE_CONTRIBUTION_JOB_TYPE = "movievault_v2.release_contribution"
FIELD_CORRECTION_JOB_TYPE = "movievault_v2.field_correction"
# Moderation is a human act that takes days, so this is not a retry ladder for a
# failure -- it is a poll for an answer that is not written yet. It lives on its
# own job because the submit job is finished the moment MovieVault has the
# contribution, and holding that job open for a week to watch it would keep a
# worker slot busy waiting for a person.
CONTRIBUTION_STATUS_JOB_TYPE = "movievault_v2.contribution_status"


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
            _record_sync_failure(conn, exc.code)
            raise
        except Exception as exc:
            # Anything that is not a MovieVaultV2Error used to leave the status
            # on 'syncing' with no code, because _mark_error was only reachable
            # from the clause above. Worse, an aborted transaction made the
            # cleanup below raise its own error on top, so the job recorded
            # "current transaction is aborted" and the real cause was gone. A
            # distribution-5 CheckViolation on movievault_v2_sync_state hid
            # behind exactly that for hours - see migration 073.
            _record_sync_failure(conn, f"unexpected_error:{type(exc).__name__}")
            raise
        finally:
            # Rolls back first: after a failed statement every command in the
            # transaction is refused, including this unlock. And it must never
            # replace the exception on its way out - a bookkeeping failure is
            # not the diagnosis.
            try:
                conn.rollback()
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (SYNC_LOCK_KEY,))
                conn.commit()
            except Exception:
                pass


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

    This is the *live* enrichment path: the anonymous resolver answers per
    barcode and is where a poster arrives for a just-scanned or just-contributed
    disc, before any v4 index sync has published it locally. Unlike the bulk
    catalog, the resolver serves the poster from a stable, anonymous
    ``/v2/assets/{assetId}/{variant}`` endpoint (no auth, no identity), so its
    absolute URL is always safe to hand straight to a client.

    With a checksum the bulk-sync cache is still reused: the same bounded,
    idempotent caching job is enqueued so a *verified* DiscVault-local copy
    replaces the remote reference on the next lookup. But the client must never
    be made to wait on that asynchronous job -- doing so was the defect this
    path was created to avoid. So:

      * if the verified local copy is already cached, return its DiscVault-local
        URL (no external round-trip for the client);
      * otherwise return the stable anonymous asset URL now, so the cover shows
        immediately and the enrichment fallback stays a real safety net for the
        "just contributed, not yet cached / not yet synced" case, instead of
        collapsing to ``pending`` and reaching the client as no poster at all.

    The v2 resolver may also publish no checksum. Bytes that cannot be verified
    must never be stored -- caching them would break the guarantee that
    everything on disk was checked -- so a checksum-less poster is surfaced as
    the anonymous asset URL only, with nothing enqueued."""
    if poster is None:
        return dict(POSTER_FIELDS_UNAVAILABLE)
    display = poster.get("display") if isinstance(poster.get("display"), dict) else {}
    remote_url = _remote_asset_url(origin, display.get("path"))
    if not display.get("checksum"):
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
    fields = _poster_status_fields(conn, poster)
    # The verified local copy wins the moment it exists. Until then the cover
    # still has to reach the client: fall back to the stable anonymous asset URL
    # rather than surfacing a URL-less ``pending``, which the metadata pipeline
    # drops entirely (no ``poster_url`` -> no ``mediaUpdates.poster`` -> the
    # client shows a placeholder despite MovieVault carrying the artwork). The
    # cache job still runs, so a later lookup upgrades to the DiscVault-local URL.
    if not fields.get("posterUrl") and remote_url:
        return {
            "posterUrl": remote_url,
            "posterStatus": "remote",
            "posterChecksum": display.get("checksum"),
            "posterRevision": None,
        }
    return fields


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
        "finishes": _json_value(row.get("finishes") or []),
        "videoResolution": row.get("video_resolution"),
        "videoCodecs": _json_value(row.get("video_codecs") or []),
        "hdrFormats": _json_value(row.get("hdr_formats") or []),
        "aspectRatios": _json_value(row.get("aspect_ratios") or []),
        "discRegions": _json_value(row.get("disc_regions") or []),
        "workType": row.get("work_type"),
        # Carried through as None when the column is NULL, keeping "not said"
        # distinct from the `[]` that means "no particular season".
        "seasons": (
            _json_value(row["seasons"]) if row.get("seasons") is not None else None
        ),
        "discs": (
            _json_value(row["discs"]) if row.get("discs") is not None else None
        ),
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
            # Order the freshest claimant first. A hash may legitimately match
            # more than one entity (a disc barcode belongs to its standalone
            # release *and* to every box set that contains it), so results are a
            # list, not a single row. But it may also carry a *stale* claim: an
            # in-place delta cannot always remove the row of an entity a barcode
            # was moved off (see `_purge_foreign_identity_claims`). Ordering by
            # the entity's own revision DESC puts the entity that most recently
            # claimed the barcode at the top, so a residual stale row can no
            # longer outrank the current owner in what the client shows.
            cur.execute(
                """
                SELECT lh.entity_type, lh.entity_id,
                       array_agg(DISTINCT lh.source_type ORDER BY lh.source_type)
                           AS match_sources,
                       COALESCE(r.revision, b.revision) AS entity_revision
                FROM movievault_v2_lookup_hashes AS lh
                LEFT JOIN movievault_v2_releases AS r
                    ON lh.entity_type = 'release'
                   AND r.generation = lh.generation
                   AND r.release_id = lh.entity_id
                LEFT JOIN movievault_v2_box_sets AS b
                    ON lh.entity_type = 'box_set'
                   AND b.generation = lh.generation
                   AND b.box_set_id = lh.entity_id
                WHERE lh.generation = %s AND lh.lookup_hash = %s
                GROUP BY lh.entity_type, lh.entity_id, COALESCE(r.revision, b.revision)
                ORDER BY entity_revision DESC NULLS LAST, lh.entity_type, lh.entity_id
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


# --- Contributing a chosen release back to MovieVault -----------------------

#: MovieVault's release-technical contract bounds aspect ratios far more
#: tightly than DiscVault stores them. `ASPECT_RATIO_PATTERN` above deliberately
#: accepts "16:9", "4:3" and "1.375:1" because those are real ratios; MovieVault
#: rejects the whole submission for any of them. Narrowed here rather than
#: loosened there: a value we cannot express upstream is dropped, not coerced
#: into a different ratio.
CONTRIBUTION_ASPECT_RATIO_PATTERN = re.compile(r"^(?:1|2)\.[0-9]{2}:1$")
CONTRIBUTION_BARCODE_LENGTHS = {8, 12, 13, 14}
MAX_CONTRIBUTION_BARCODES = 25
MAX_CONTRIBUTION_SUBTITLES = 100
MAX_CONTRIBUTION_ALTERNATE_TITLES = 25
IMDB_ID_PATTERN = re.compile(r"^tt[0-9]{1,16}$")
TMDB_ID_PATTERN = re.compile(r"^[0-9]{1,20}$")


def contribution_barcode(value: Any) -> str:
    """A GTIN with separators removed, or "" when it is not one.

    Returning "" rather than raising: a disc whose barcode we cannot express is
    still worth contributing for its technical data, and the scanned barcode is
    validated separately by the caller that needs it.
    """
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    return digits if len(digits) in CONTRIBUTION_BARCODE_LENGTHS else ""


def _contribution_enum_list(values: Any, allowed: set[str], limit: int) -> list[str]:
    result: list[str] = []
    for item in values if isinstance(values, list) else []:
        text = str(item or "").strip()
        if text in allowed and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _contribution_audio_tracks(values: Any) -> list[dict[str, Any]]:
    tracks: list[dict[str, Any]] = []
    for item in values if isinstance(values, list) else []:
        if not isinstance(item, dict):
            continue
        language = str(item.get("languageCode") or item.get("language_code") or "").strip().lower()
        codec = str(item.get("codec") or "").strip()
        # MovieVault requires both; a track missing either is dropped rather
        # than guessed at, since neither has a safe default.
        if not LANGUAGE_CODE_PATTERN.fullmatch(language) or codec not in AUDIO_TRACK_CODECS:
            continue
        track: dict[str, Any] = {"languageCode": language, "codec": codec}
        channels = str(item.get("channels") or "").strip()
        if channels in AUDIO_TRACK_CHANNELS:
            track["channels"] = channels
        immersive = str(item.get("immersiveFormat") or item.get("immersive_format") or "").strip()
        if immersive in AUDIO_TRACK_IMMERSIVE_FORMATS:
            track["immersiveFormat"] = immersive
        tracks.append(track)
        if len(tracks) >= MAX_AUDIO_TRACKS:
            break
    return tracks


def _contribution_subtitles(values: Any) -> list[dict[str, str]]:
    subtitles: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in values if isinstance(values, list) else []:
        if isinstance(item, dict):
            language = str(item.get("languageCode") or item.get("language_code") or "").strip().lower()
            variant = str(item.get("subtitleType") or item.get("subtitle_type") or "").strip()
        else:
            language = str(item or "").strip().lower()
            variant = ""
        if not LANGUAGE_CODE_PATTERN.fullmatch(language):
            continue
        variant = variant if variant in SUBTITLE_TYPES else DEFAULT_SUBTITLE_TYPE
        key = (language, variant)
        if key in seen:
            continue
        seen.add(key)
        subtitles.append({"languageCode": language, "subtitleType": variant})
        if len(subtitles) >= MAX_CONTRIBUTION_SUBTITLES:
            break
    return subtitles


def _contribution_video(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    video: dict[str, Any] = {}
    resolution = str(source.get("resolution") or "").strip()
    if resolution in VIDEO_RESOLUTIONS:
        video["resolution"] = resolution
    codecs = _contribution_enum_list(source.get("codecs"), VIDEO_CODECS, MAX_VIDEO_CODECS)
    if codecs:
        video["codecs"] = codecs
    hdr = _contribution_enum_list(
        source.get("hdrFormats") or source.get("hdr_formats"), HDR_FORMATS, MAX_HDR_FORMATS
    )
    if hdr:
        video["hdrFormats"] = hdr
    ratios: list[str] = []
    raw_ratios = source.get("aspectRatios") or source.get("aspect_ratios")
    for item in raw_ratios if isinstance(raw_ratios, list) else []:
        text = str(item or "").strip()
        if CONTRIBUTION_ASPECT_RATIO_PATTERN.fullmatch(text) and text not in ratios:
            ratios.append(text)
        if len(ratios) >= MAX_ASPECT_RATIOS:
            break
    if ratios:
        video["aspectRatios"] = ratios
    return video


def _contribution_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def release_technical_contribution_payload(
    candidate: Any,
    *,
    scanned_barcode: str,
    film: Any,
    provenance: str,
) -> dict[str, Any]:
    """Map a chosen release onto MovieVault's `release_technical` payload.

    Only what MovieVault stores about a physical release travels: public film
    identity, the disc's own technical description, and the barcodes. Nothing
    about the owner, the copy, or what any provider said about the film.

    The scanned barcode is carried as `scannedBarcode` and never folded into
    `release.barcodes`. Those are different claims - see the contribution
    contract - and collapsing them would assert upstream that a barcode belongs
    to a pressing nobody confirmed it for.

    `subtitleLanguages` is never emitted: MovieVault derives it from the tracks,
    and sending both spellings of one fact is how two views drift apart.

    Returns {} when there is nothing worth a moderator's time.
    """
    source = candidate if isinstance(candidate, dict) else {}
    film_source = film if isinstance(film, dict) else {}
    barcode = contribution_barcode(scanned_barcode)
    if not barcode or provenance not in {"candidate_selection", "manual_entry"}:
        return {}

    film_title = _contribution_text(film_source.get("title"), 500)
    release_title = _contribution_text(source.get("title"), 500) or film_title
    if not film_title or not release_title:
        return {}

    release: dict[str, Any] = {"title": release_title}
    for key, limit in (("edition", 255), ("format", 80)):
        text = _contribution_text(source.get(key), limit)
        if text:
            release[key] = text
    disc_count = source.get("discCount") or source.get("disc_count")
    if isinstance(disc_count, int) and not isinstance(disc_count, bool) and 1 <= disc_count <= 999:
        release["discCount"] = disc_count
    regions = _contribution_enum_list(
        source.get("discRegions") or source.get("regions"), DISC_REGIONS, 8
    )
    if regions:
        release["regions"] = regions
    packaging = _contribution_enum_list(
        source.get("packaging"), PACKAGING_VALUES, MAX_PACKAGING
    )
    if packaging:
        release["packaging"] = packaging
    video = _contribution_video(source.get("video"))
    if video:
        release["video"] = video
    audio_tracks = _contribution_audio_tracks(
        source.get("audioTracks") or source.get("audio_tracks")
    )
    if audio_tracks:
        release["audioTracks"] = audio_tracks
    subtitles = _contribution_subtitles(source.get("subtitles"))
    if subtitles:
        release["subtitles"] = subtitles

    barcodes: list[str] = []
    for item in source.get("barcodes") if isinstance(source.get("barcodes"), list) else []:
        value = item.get("value") if isinstance(item, dict) else item
        normalized = contribution_barcode(value)
        if normalized and normalized not in barcodes:
            barcodes.append(normalized)
        if len(barcodes) >= MAX_CONTRIBUTION_BARCODES:
            break
    if barcodes:
        release["barcodes"] = barcodes

    # A title plus a barcode is a lookup, not a contribution: it costs a
    # moderator a review and tells them nothing they could act on.
    if not any(
        release.get(key)
        for key in ("edition", "discCount", "regions", "packaging", "video", "audioTracks", "subtitles")
    ):
        return {}

    payload_film: dict[str, Any] = {"title": film_title}
    # MOVIE/SHOW is DiscVault's vocabulary; MovieVault's is movie/tv. Mapped
    # through `normalize_media_type` rather than a second lookup table, so the
    # two spellings of one fact stay one mapping.
    #
    # Absence is deliberate and is *not* "movie": it means this side did not
    # say, and MovieVault leaves whatever it already recorded. A client with no
    # opinion must not be able to downgrade a series somebody else established.
    media_type = normalize_media_type(
        source.get("workType")
        or source.get("work_type")
        or film_source.get("workType")
        or film_source.get("mediaType")
        or film_source.get("media_type")
    )
    if media_type == MEDIA_TYPE_SHOW:
        payload_film["workType"] = "tv"
    elif media_type == MEDIA_TYPE_MOVIE:
        payload_film["workType"] = "movie"
    year = film_source.get("year")
    if isinstance(year, int) and not isinstance(year, bool) and 1870 <= year <= 2200:
        payload_film["year"] = year
    # Digit strings, the spelling MovieVault itself emits and stores. An
    # integer is rejected upstream rather than coerced.
    tmdb_id = str(film_source.get("tmdbMovieId") or film_source.get("tmdb_movie_id") or "").strip()
    tmdb_tv_id = str(film_source.get("tmdbTvId") or film_source.get("tmdb_tv_id") or "").strip()
    # The two TMDB id spaces are separate and mutually exclusive upstream, and
    # an id that contradicts the stated type is refused there rather than
    # silently resolved. Send only the one the work type agrees with, so a
    # mismatch in our own data never becomes a rejected submission.
    if payload_film.get("workType") == "tv":
        if TMDB_ID_PATTERN.fullmatch(tmdb_tv_id):
            payload_film["tmdbTvId"] = tmdb_tv_id
    elif TMDB_ID_PATTERN.fullmatch(tmdb_id):
        payload_film["tmdbMovieId"] = tmdb_id
    imdb_id = str(film_source.get("imdbId") or film_source.get("imdb_id") or "").strip()
    if IMDB_ID_PATTERN.fullmatch(imdb_id):
        payload_film["imdbId"] = imdb_id

    if provenance == "manual_entry":
        # MovieVault holds a hand-typed record to more, having no provider
        # behind it. Fail here rather than send something it will refuse.
        if not release.get("format") or payload_film.get("year") is None:
            return {}
        stated = sum(
            1
            for key in ("edition", "discCount", "regions", "packaging")
            if release.get(key)
        )
        if release.get("video", {}).get("resolution"):
            stated += 1
        if stated < 2:
            return {}
        # Only the person holding the package can attest to a barcode, and
        # nothing in the flow asks them, so a manual entry never states one.
        release.pop("barcodes", None)

    payload: dict[str, Any] = {
        "scannedBarcode": barcode,
        "provenance": provenance,
        "film": payload_film,
        "release": release,
    }
    release_ref = _contribution_text(source.get("releaseRef"), 500)
    if release_ref:
        # Underscored: consumed by the submitter to build the opaque source
        # reference and stripped before signing. MovieVault forbids it.
        payload["_releaseRef"] = release_ref
    return payload
