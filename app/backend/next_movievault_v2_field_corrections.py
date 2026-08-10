"""Field corrections: resolving a local record to a MovieVault target, and
working out what may honestly be said about it.

This is the server-owned half of `contribution-2`. Everything a client could get
wrong lives here: which record upstream a local one is, which fields may be
corrected, what the catalogue currently holds, and what the local row says. A
client asks; it never decides.

That boundary is not tidiness. `docs/contracts/contribution-v2.md` requires it --
the browser and the native apps all reach MovieVault's contribution surface
through their own DiscVault instance, and the credentials never leave the
server. A client that re-derived the eligible-field list would re-derive the
mistakes with it, three times over, in three languages.

The hard part is not the protocol. It is that DiscVault and MovieVault describe
a disc differently, and a field that looks like a counterpart often is not one.
Every exclusion below is a place where translating would have invented data.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

try:
    from .next_metadata import movie_identifiers
    from .next_metadata import movie_locked_fields
    from .next_metadata import movie_technical_specs
    from .next_movievault_v2 import MOVIEVAULT_V2_PLUGIN_ID
    from .next_movievault_connection import _table_exists, _text
    from .next_product_identifiers import catalogue_eans, contributable_eans, movie_identifiers_by_type
except ImportError:  # pragma: no cover - supports direct module execution
    from next_metadata import movie_identifiers
    from next_metadata import movie_locked_fields
    from next_metadata import movie_technical_specs
    from next_movievault_v2 import MOVIEVAULT_V2_PLUGIN_ID
    from next_movievault_connection import _table_exists, _text
    from next_product_identifiers import catalogue_eans, contributable_eans, movie_identifiers_by_type

#: The provider whose identifier names a MovieVault release. Written by the v2
#: plugin since 26.8.9; read here as the first and most reliable way to say
#: which catalogue record a local movie is.
MOVIEVAULT_V2_PROVIDER = "movievault_v2"

_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
#: MovieVault stores `country_code char(2)` and validates `^[A-Z]{2}$`.
_COUNTRY_PATTERN = re.compile(r"^[A-Z]{2}$")
#: The same two letters in any case. `movies.country` is free text, so a record
#: holding "nl" names exactly the country MovieVault spells "NL" -- upper-casing
#: it is a normalisation, not a guess. Anything that needs a lookup table
#: ("Netherlands", "NLD", "Nederland") stays on the other side of that line and
#: is withheld, because there the wrong answer is silent.
_COUNTRY_LIKE_PATTERN = re.compile(r"^[A-Za-z]{2}$")
#: MovieVault puts no pattern on `language_code`, which means it would happily
#: store "Dutch". DiscVault's `movies.language` is free text, so the shape is
#: checked here rather than upstream -- the alternative is polluting a shared
#: catalogue with a value only this instance can read.
_LANGUAGE_PATTERN = re.compile(r"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*$")
#: The disc-region vocabulary, identical on both sides. Stated here rather than
#: imported because DiscVault stores it in a jsonb column with no constraint,
#: so an unrecognised value is a local possibility that must not travel --
#: upstream would refuse the whole envelope, not just the field.
DISC_REGIONS = frozenset({"A", "B", "C", "1", "2", "3", "4", "5", "6", "7", "8", "FREE"})


class FieldCorrectionError(Exception):
    """A refusal a caller can act on, carrying a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


#: What MovieVault will accept for a release (`contribution-2` §"Eligible
#: fields") mapped onto where DiscVault keeps it. A field absent from this table
#: is in `RELEASE_FIELDS_WITHHELD` with the reason it cannot travel.
RELEASE_FIELD_SOURCES: dict[str, tuple[str, str]] = {
    "edition": ("movie", "edition"),
    "format": ("movie", "format"),
    "countryCode": ("movie", "country"),
    "languageCode": ("movie", "language"),
    "releaseDate": ("movie", "release_date"),
    "runtimeMinutes": ("movie", "runtime_minutes"),
    # Held since 26.8.22. Nullable, so "nobody said" stays distinct from
    # "one disc" -- and a record nobody has answered proposes nothing.
    "discCount": ("movie", "disc_count"),
    "distributor": ("metadata", "distributor"),
    # Not a column: the complete EAN list, assembled from
    # `movie_product_identifiers` plus the scanned `movies.barcode`. Withheld
    # until 26.8.20, when DiscVault gained somewhere to hold more than one code.
    "eans": ("identifiers", "eans"),
    # Not on `movies` either: disc regions live on `movie_technical_specs`, and
    # upstream on `release_technical_profiles`. Both sides hold the same
    # normalised vocabulary, which is what makes this the one region-shaped
    # field that can travel -- `region` below is free-text market region and a
    # different fact.
    "discRegions": ("technical", "regions"),
    # The rest of the technical description, correctable upstream since
    # MovieVault-v2#227. Every one of these is a fact a person holding the disc
    # can read off it, which is the test that put them on that list and the
    # reason DiscVault is a good source for them.
    #
    # `packaging` is the only one that is not a straight copy: DiscVault splits
    # what MovieVault keeps flat, so it is re-flattened through the mapping that
    # already existed for the trip in the other direction. See `_packaging`.
    "packaging": ("technical", "packaging"),
    "finishes": ("technical", "finishes"),
    "videoResolution": ("technical", "video_resolution"),
    "videoCodecs": ("technical", "video_codecs"),
    # Spelled `hdr` locally and `hdrFormats` on the wire.
    "hdrFormats": ("technical", "hdr"),
    # Spelled `screen_ratios` locally. Filtered rather than copied: upstream
    # requires a bounded decimal-to-one form, and this column is free text.
    "aspectRatios": ("technical", "screen_ratios"),
    # Both of these are a union of structured tracks and legacy free text, so
    # they travel only when every entry converts -- see `_audio_tracks`.
    "audioTracks": ("technical", "audio_tracks"),
    "subtitles": ("technical", "subtitles"),
    # Not on `movie_technical_specs`: the per-disc breakdown is its own table
    # (`movie_discs`, migration 075), read alongside it.
    "discs": ("discs", "discs"),
}

#: Eligible upstream, deliberately not offered here. Each entry is a place where
#: DiscVault and MovieVault use one word for two things, and translating would
#: have produced a confident wrong answer rather than a visible gap.
RELEASE_FIELDS_WITHHELD: dict[str, str] = {
    # `content.releases.title` is the title of a *pressing*, not of the film:
    # "Spider-Man: Into the Spider-Verse Blu-ray (Spider-Man: A New Universe)
    # (Germany)". DiscVault's `movies.title` is the film title, and a film that
    # is correctly titled therefore reads as a disagreement on every single
    # release. Proposing the film title here would flatten a product name into
    # a film name and lose the edition, the alternate title and the country
    # with it.
    #
    # This is the same shape as `region` and `studio` below - one word naming
    # two different facts - and it is the most misleading of the three, because
    # the two values look comparable. DiscVault has no release-title field to
    # offer instead: what it stores per pressing is `edition` and `format`,
    # which are already correctable separately.
    "title": "different_field_upstream",
    # `content.releases.region` is free text at release level. DiscVault's
    # `regions` is a normalised list of disc regions on the technical spec.
    # Same word, different fact.
    "region": "different_field_upstream",
    # MovieVault has one `studio`; DiscVault has `studios`, a list. Joining a
    # list into one string is lossy in a way a moderator cannot see.
    "studio": "discvault_holds_a_list",
    # Correctable upstream, but DiscVault has nowhere to read it from: there is
    # no alternate-title store on a movie. Recorded as a withholding rather than
    # left off the table, so the gap reads as a decision instead of an oversight.
    "alternateTitles": "not_stored_by_discvault",
}

#: A box set is nearly empty on this route, and that is a fact about the data
#: rather than a limitation of the mechanism. `containers` holds title, barcode,
#: badge_label, year and description -- of MovieVault's six correctable box-set
#: fields, only the title has a counterpart.
BOX_SET_FIELD_SOURCES: dict[str, tuple[str, str]] = {
    "title": ("container", "title"),
}

BOX_SET_FIELDS_WITHHELD: dict[str, str] = {
    "edition": "not_stored_by_discvault",
    "format": "not_stored_by_discvault",
    "countryCode": "not_stored_by_discvault",
    "languageCode": "not_stored_by_discvault",
    # `containers.year` is the box set's own year; MovieVault's `year_range`
    # describes the span of the films inside it. Different questions.
    "yearRange": "different_field_upstream",
}

#: Which local lock names cover a correctable field. The lock vocabulary is
#: snake_case canonical (`movie_locked_fields`), the wire vocabulary is
#: camelCase, and one lock can cover more than one spelling.
_FIELD_LOCK_NAMES: dict[str, tuple[str, ...]] = {
    "title": ("title",),
    "edition": ("edition",),
    "format": ("format",),
    "countryCode": ("country",),
    "languageCode": ("language",),
    "releaseDate": ("release_date",),
    "runtimeMinutes": ("runtime_minutes",),
    "distributor": ("distributor",),
    # The local lock is spelled `regions`; the wire field is `discRegions`.
    # Without this row a user who pinned their disc regions against metadata
    # refresh would have them published anyway.
    "discRegions": ("regions",),
    # The technical fields, same rule as `regions` above and the same failure
    # if left out: a user who pinned a value against metadata refresh has said
    # they do not want it moved, and publishing it upstream moves it further
    # than a refresh would. Locks are spelled with the local column name, which
    # differs from the wire name for three of these.
    "packaging": ("packaging", "carrier_type", "outer_packaging"),
    "finishes": ("finishes",),
    "videoResolution": ("video_resolution",),
    "videoCodecs": ("video_codecs",),
    "hdrFormats": ("hdr",),
    "aspectRatios": ("screen_ratios",),
    "audioTracks": ("audio_tracks",),
    "subtitles": ("subtitles",),
}


def barcode_lookup_hash(barcode: Any) -> str | None:
    """The sha256 MovieVault's lookup index is keyed by.

    Digits only, and only at a length an EAN/UPC/GTIN actually uses -- the same
    normalisation the v2 plugin and both native clients apply, because a hash
    computed differently is simply a different hash and would silently miss.
    """
    digits = re.sub(r"[^0-9]", "", str(barcode or ""))
    if len(digits) not in {8, 12, 13, 14}:
        return None
    return hashlib.sha256(digits.encode("ascii")).hexdigest()


def _active_generation(conn: Any) -> Any:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT active_generation FROM movievault_v2_sync_state WHERE plugin_id = %s",
            (MOVIEVAULT_V2_PLUGIN_ID,),
        )
        row = cur.fetchone()
    return (dict(row).get("active_generation") if row else None) or None


def _stored_release_id(conn: Any, movie_id: Any) -> str | None:
    """The catalogue release this movie was matched to, if it carries one.

    Preferred over the barcode because it survives a barcode the moderator
    declined: such a record has no lookup hash at all, and the stored id is then
    the only thread back to it.
    """
    identifiers = movie_identifiers(conn, movie_id)
    value = str(identifiers.get("movieVaultId") or "").strip()
    if not value or not _UUID_PATTERN.fullmatch(value):
        return None
    # `movieVaultId` is only a v2 release id when a v2 identifier row is what
    # produced it. `select_movievault_identifier` prefers the v2 generation, but
    # an instance with only a `movievault_26` row would land a non-UUID here --
    # and an import-derived UUIDv5 would land a UUID that names nothing. The
    # provider is therefore checked rather than the shape alone.
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM movie_identifiers
            WHERE movie_id = %s AND provider_id = %s
              AND identifier_type = 'movie_id' AND identifier = %s
            """,
            (movie_id, MOVIEVAULT_V2_PROVIDER, value),
        )
        return value if cur.fetchone() else None


def _entity_revision(conn: Any, generation: Any, entity_type: str, entity_id: Any) -> int | None:
    table, key = (
        ("movievault_v2_releases", "release_id")
        if entity_type == "release"
        else ("movievault_v2_box_sets", "box_set_id")
    )
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT revision FROM {table} WHERE generation = %s AND {key} = %s",
            (generation, entity_id),
        )
        row = cur.fetchone()
    return int(dict(row)["revision"]) if row else None


def _lookup_entity(conn: Any, generation: Any, lookup_hash: str) -> tuple[str, Any] | None:
    """One barcode may name several records; only an unambiguous hit is a target.

    A barcode that resolves to two entities is exactly the ambiguity the
    fallback picker exists for, and guessing one here would attach a correction
    to a record nobody chose.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT entity_type, entity_id
            FROM movievault_v2_lookup_hashes
            WHERE generation = %s AND lookup_hash = %s
            """,
            (generation, lookup_hash),
        )
        rows = [dict(row) for row in cur.fetchall()]
    if len(rows) != 1:
        return None
    return str(rows[0]["entity_type"]), rows[0]["entity_id"]


def resolve_target(conn: Any, *, entity: str, record: dict[str, Any]) -> dict[str, Any] | None:
    """Which MovieVault record this local one is, and which version was seen.

    Order matters: the stored identifier first, the barcode second. A record
    that has both should be named by the id, because the id says "this is that
    release" while a barcode says "something with this barcode" -- and the
    second is only as good as the mirror's current contents.

    Returns None when nothing resolves. That is not an error; for a movie it
    means the record is a proposal rather than a correction, and for a box set
    it means there is nothing to correct.
    """
    generation = _active_generation(conn)
    if not generation:
        return None

    if entity == "movie":
        release_id = _stored_release_id(conn, record.get("id"))
        if release_id:
            revision = _entity_revision(conn, generation, "release", release_id)
            if revision is not None:
                return {
                    "entityType": "release",
                    "entityId": str(release_id),
                    "baseRevision": revision,
                    "matchedBy": "identifier",
                }

    lookup_hash = barcode_lookup_hash(record.get("barcode"))
    if not lookup_hash:
        return None
    match = _lookup_entity(conn, generation, lookup_hash)
    if not match:
        return None
    entity_type, entity_id = match
    # A movie may not correct a box set and a container may not correct a
    # release. The barcode index holds both, and a box-set barcode on a movie
    # row is a real shape -- it means the disc is part of a set.
    if (entity == "movie") != (entity_type == "release"):
        return None
    revision = _entity_revision(conn, generation, entity_type, entity_id)
    if revision is None:
        return None
    return {
        "entityType": entity_type,
        "entityId": str(entity_id),
        "baseRevision": revision,
        "matchedBy": "barcode",
    }


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        text = " ".join(value.split())
        return text or None
    return value


def _normalise_country(value: Any) -> Any:
    """Upper-case a two-letter country value, and change nothing else.

    `movies.country` is free text, so the same country arrives written several
    ways. Two ASCII letters name a country unambiguously whatever their case,
    so `nl` -> `NL` costs nothing and stops a user being told their value is
    not a country code when it is one. Everything longer needs a lookup table,
    which is a data decision with a wrong-answer mode: those stay withheld.
    """
    if isinstance(value, str) and _COUNTRY_LIKE_PATTERN.fullmatch(value):
        return value.upper()
    return value


def _mirror_disc_regions(value: Any) -> list[str] | None:
    """The catalogue's disc regions, normalised the same way the local ones are.

    Both sides are compared as whole lists, so they have to be sorted the same
    way or every record with regions reads as a disagreement. An absent or
    empty set is `None`, matching `_disc_regions`: upstream's column defaults to
    an empty array, and "nothing recorded" is not "plays nowhere".
    """
    if not isinstance(value, list) or not value:
        return None
    values = sorted({str(item).strip().upper() for item in value if str(item).strip()})
    return [item for item in values if item in DISC_REGIONS] or None


#: The vocabularies MovieVault will accept for the technical fields. Identical
#: to DiscVault's own, which is what makes these fields travel at all -- both
#: sides took them from `release-technical-1`. Stated here rather than imported
#: from the edit form so that a divergence upstream shows up as a *withheld*
#: value instead of a rejected contribution: anything outside these sets is
#: dropped rather than sent and refused.
_MV_RESOLUTIONS = frozenset({"480p", "576p", "720p", "1080i", "1080p", "2160p"})
_MV_VIDEO_CODECS = frozenset({"mpeg2", "vc1", "h264", "hevc", "av1"})
#: `next_packaging.FINISHES` and MovieVault's `FinishType` are the same nine
#: values. Restated rather than imported, for the reason above -- if one side
#: adds a tenth, the unknown value is withheld until the other catches up.
_MV_FINISHES = frozenset(
    {
        "spot_uv",
        "embossed",
        "debossed",
        "foil",
        "holofoil",
        "matte",
        "glossy",
        "reversible_cover",
        "padded",
    }
)
_MV_HDR_FORMATS = frozenset({"hdr", "hdr10", "hdr10_plus", "hlg", "dolby_vision"})
_MV_AUDIO_CODECS = frozenset(
    {
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
)
#: MovieVault's flat packaging alphabet. DiscVault's two axes map onto a subset
#: of it, so the filter is what keeps a purely local term -- a carrier value
#: with no legacy counterpart -- from being offered as a correction.
_MV_PACKAGING = frozenset(
    {
        "keep_case", "amaray", "steelbook", "slipcover", "slipcase", "digibook",
        "mediabook", "digipak", "box", "eco_case", "multi_disc_case", "futurepak",
        "hardbox", "digisleeve", "card_wallet", "paper_sleeve", "o_card",
        "fullslip", "half_slip", "quarter_slip", "lenticular_slip",
        "double_lenticular_slip", "rigid_box", "one_click",
    }
)
_MV_CHANNELS = frozenset({"1.0", "2.0", "5.1", "6.1", "7.1"})
_MV_IMMERSIVE = frozenset({"dolby_atmos", "dts_x", "auro_3d"})
_MV_SUBTITLE_TYPES = frozenset({"full", "sdh", "forced", "commentary", "closed_caption"})
#: Upstream refuses anything else, so a boutique `1.66:1` passes while `16:9`
#: does not. Filtered rather than refused: one unusable ratio must not cost the
#: record its other three.
_MV_ASPECT_RATIO = re.compile(r"^(?:1|2)\.[0-9]{2}:1$")
_MV_LANGUAGE = re.compile(r"^[a-z]{2,8}(?:-[a-z0-9]{1,8})*$")


def _vocabulary_list(value: Any, allowed: frozenset[str]) -> list[str] | None:
    """A set-shaped field, sorted and filtered to what upstream accepts.

    Sorted because MovieVault sorts these too, and the two sides are compared
    as whole lists -- two orderings of one answer would otherwise read as a
    disagreement and refuse a correction nobody disputed. Empty is `None`, not
    `[]`: `[]` upstream is the claim "there are none", and a movie with nothing
    recorded locally is saying "nobody said".
    """
    if not isinstance(value, list):
        return None
    items = sorted({str(item).strip() for item in value if str(item).strip()})
    return [item for item in items if item in allowed] or None


def _aspect_ratios(value: Any) -> list[str] | None:
    """Screen ratios in the bounded form upstream requires, order preserved.

    Not sorted, unlike the sets above: a transfer's primary ratio comes first,
    and that ordering is information rather than an accident of typing.
    """
    if not isinstance(value, list):
        return None
    ratios: list[str] = []
    for item in value:
        text = str(item).strip()
        if _MV_ASPECT_RATIO.match(text) and text not in ratios:
            ratios.append(text)
    return ratios or None


def _audio_tracks(value: Any) -> list[dict[str, Any]] | None:
    """The audio list, but only when every track can travel intact.

    DiscVault's column is a union: structured tracks *and* legacy free text
    like "English (DTS-HD MA 5.1)", kept verbatim since before MovieVault
    published structured tracks. Upstream requires a language and a codec from
    a closed set, so prose cannot be sent -- and must not be guessed at either,
    because reconstructing a codec from a string misfires on "Commentary with
    the director", which is exactly why the local normaliser keeps it as text.

    All or nothing per record, therefore. One unconvertible track withholds the
    whole field rather than proposing a list without it: this is a replacement
    list, so a partial answer would quietly delete the tracks it could not
    express.
    """
    if not isinstance(value, list) or not value:
        return None
    tracks: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        language = str(item.get("languageCode") or item.get("language_code") or "").strip()
        codec = str(item.get("codec") or "").strip()
        if not _MV_LANGUAGE.match(language) or codec not in _MV_AUDIO_CODECS:
            return None
        channels = str(item.get("channels") or "").strip()
        immersive = str(item.get("immersiveFormat") or item.get("immersive_format") or "").strip()
        tracks.append(
            {
                "languageCode": language,
                "codec": codec,
                "channels": channels if channels in _MV_CHANNELS else None,
                "immersiveFormat": immersive if immersive in _MV_IMMERSIVE else None,
            }
        )
    return tracks or None


def _subtitles(value: Any) -> list[dict[str, Any]] | None:
    """The subtitle list, under the same all-or-nothing rule as the audio one.

    A bare language code is not legacy prose -- it is the pre-variant shape,
    and an unqualified subtitle listing on a disc means the full variant. That
    conversion is what `normalize_subtitle_entry` already does locally, so it
    is applied here rather than withheld over.
    """
    if not isinstance(value, list) or not value:
        return None
    subtitles: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, str):
            language, variant = item.strip(), "full"
        elif isinstance(item, dict):
            language = str(item.get("languageCode") or item.get("language_code") or "").strip()
            variant = str(item.get("subtitleType") or item.get("subtitle_type") or "full").strip()
        else:
            return None
        if not _MV_LANGUAGE.match(language) or variant not in _MV_SUBTITLE_TYPES:
            return None
        subtitles.append({"languageCode": language, "subtitleType": variant})
    return subtitles or None


def _packaging(technical: dict[str, Any]) -> list[str] | None:
    """DiscVault's two packaging axes, flattened back to MovieVault's one list.

    Migration 067 split the flat list into carrier plus outer packaging because
    one bucket could not stop a record claiming both `steelbook` and `amaray`.
    MovieVault deliberately kept it flat -- it is fed by plugins scraping tokens
    out of release titles, and asking each source whether "slipcover" describes
    the case or the wrapper is asking it to guess.

    `derive_legacy_packaging` is the mapping that already existed for the trip
    in the other direction, so the round trip is closed rather than
    approximated a second time.
    """
    try:
        from .next_packaging import derive_legacy_packaging
    except ImportError:  # pragma: no cover - supports direct module execution
        from next_packaging import derive_legacy_packaging

    values = derive_legacy_packaging(
        technical.get("carrier_type"), technical.get("outer_packaging")
    )
    return _vocabulary_list(values, _MV_PACKAGING)


def _mirror_discs(value: Any) -> list[dict[str, Any]] | None:
    """The catalogue's disc list, as the feed published it.

    Unlike the local rows this is already in the wire spelling -- the mirror
    stores what v6 sent, verbatim -- so the only work is dropping what upstream
    would not accept back and stripping `position`. Positions are restated by
    list order on the way in and refused if they disagree, so carrying them
    into `expected` would compare a key the proposal does not send.
    """
    if not isinstance(value, list) or not value:
        return None
    discs: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        entry = {
            key: item[key]
            for key in (
                "discType",
                "discRole",
                "discTypeOther",
                "label",
                "videoResolution",
                "videoCodecs",
                "hdrFormats",
                "aspectRatios",
                "regions",
                "audioTracks",
                "subtitles",
            )
            if item.get(key) not in (None, [], "")
        }
        discs.append(entry)
    return discs or None


def _movie_discs(rows: Any) -> list[dict[str, Any]] | None:
    """The per-disc breakdown, in the shape the contract and v6 already share.

    Positions are not stated: upstream renumbers from list order and refuses a
    position that disagrees with it, so the stored order says it once. Season
    and episode links stay local -- MovieVault holds its own season structure
    and a DiscVault uuid means nothing there.

    Withheld entirely when any disc's tracks cannot travel, for the reason
    `_audio_tracks` is all or nothing: this replaces every disc, so a disc sent
    without the tracks it actually has would delete them upstream.
    """
    if not isinstance(rows, list) or not rows:
        return None
    discs: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            return None
        entry: dict[str, Any] = {}
        for wire, column in (
            ("discType", "disc_type"),
            ("discRole", "disc_role"),
            ("discTypeOther", "disc_type_other"),
            ("label", "label"),
        ):
            text = _clean(row.get(column))
            if text is not None:
                entry[wire] = text
        resolution = _clean(row.get("video_resolution"))
        if resolution in _MV_RESOLUTIONS:
            entry["videoResolution"] = resolution
        for wire, column, allowed in (
            ("videoCodecs", "video_codecs", _MV_VIDEO_CODECS),
            ("hdrFormats", "hdr", _MV_HDR_FORMATS),
            ("regions", "regions", DISC_REGIONS),
        ):
            values = _vocabulary_list(row.get(column), allowed)
            if values:
                entry[wire] = values
        ratios = _aspect_ratios(row.get("screen_ratios"))
        if ratios:
            entry["aspectRatios"] = ratios
        for wire, column, convert in (
            ("audioTracks", "audio_tracks", _audio_tracks),
            ("subtitles", "subtitles", _subtitles),
        ):
            raw = row.get(column)
            if isinstance(raw, list) and raw:
                converted = convert(raw)
                if converted is None:
                    return None
                entry[wire] = converted
        discs.append(entry)
    return discs or None


def _disc_regions(technical: dict[str, Any], metadata: dict[str, Any]) -> list[str] | None:
    """The local disc regions, normalised the way upstream compares them.

    Read from `movie_technical_specs` first and from `metadata` second, which is
    the same precedence the edit panel and the sync payload use. Sorted and
    deduplicated because the value is compared against `expected` as a whole,
    so two orderings of one answer would read as a disagreement about the discs.

    An empty set is `None` rather than `[]`: upstream treats an empty list as
    "this release plays nowhere", and a movie with nothing recorded locally
    means "nobody said", not that.
    """
    raw = technical.get("regions")
    if not isinstance(raw, list) or not raw:
        raw = metadata.get("regions")
    if not isinstance(raw, list) or not raw:
        return None
    values = sorted({str(item).strip().upper() for item in raw if str(item).strip()})
    return [value for value in values if value in DISC_REGIONS] or None


def _local_discs(conn: Any, movie_id: Any) -> list[dict[str, Any]]:
    """The movie's own disc rows, or nothing when the table is not there yet.

    Imported at call time rather than at module scope: `next_app` imports this
    module, so a top-level import would close the cycle. The reader itself is
    the one the detail screen uses, so a correction proposes exactly the discs
    the user can see.
    """
    if not movie_id:
        return []
    try:
        from .next_app import movie_disc_entities
    except ImportError:  # pragma: no cover - supports direct module execution
        from next_app import movie_disc_entities
    try:
        return movie_disc_entities(conn, movie_id)
    except Exception:  # pragma: no cover - a missing table is not a failed edit
        return []


def _technical_value(
    field: str,
    column: str,
    technical: dict[str, Any],
    metadata: dict[str, Any],
) -> Any:
    """One technical field, converted to what upstream will accept.

    `regions` keeps its own reader because it is the one that falls back to
    `metadata` -- the same precedence the edit panel and the sync payload use.
    The others live only on `movie_technical_specs`, so there is nothing to
    fall back to and a missing spec row simply means nobody has said.
    """
    if field == "discRegions":
        return _disc_regions(technical, metadata)
    if field == "packaging":
        return _packaging(technical)
    if field == "videoResolution":
        value = _clean(technical.get(column))
        return value if value in _MV_RESOLUTIONS else None
    if field == "aspectRatios":
        return _aspect_ratios(technical.get(column))
    if field == "audioTracks":
        return _audio_tracks(technical.get(column))
    if field == "subtitles":
        return _subtitles(technical.get(column))
    allowed = {
        "finishes": _MV_FINISHES,
        "videoCodecs": _MV_VIDEO_CODECS,
        "hdrFormats": _MV_HDR_FORMATS,
    }[field]
    return _vocabulary_list(technical.get(column), allowed)


def _local_release_values(
    record: dict[str, Any],
    metadata: dict[str, Any],
    identifiers: list[dict[str, str]] | None = None,
    technical: dict[str, Any] | None = None,
    discs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    specs = technical or {}
    for field, (source, column) in RELEASE_FIELD_SOURCES.items():
        if source == "discs":
            values[field] = _movie_discs(discs)
            continue
        if source == "technical":
            values[field] = _technical_value(field, column, specs, metadata)
            continue
        if source == "identifiers":
            # A list rather than a column, and empty means "nothing to say"
            # rather than "delete everything upstream": an empty replacement
            # list would wipe the release's identifiers, so it is never offered.
            eans = contributable_eans(identifiers or [], barcode=record.get("barcode"))
            values[field] = eans or None
            continue
        raw = metadata.get(column) if source == "metadata" else record.get(column)
        value = _clean(raw)
        if field == "countryCode":
            # Normalise before anything reads it, so the eligibility check, the
            # `proposed` value and the submitted payload cannot disagree.
            value = _normalise_country(value)
        if field == "releaseDate" and value is not None:
            value = value.isoformat() if hasattr(value, "isoformat") else str(value)
        if field in {"runtimeMinutes", "discCount"} and value is not None:
            value = int(value)
        values[field] = value
    return values


def _local_box_set_values(record: dict[str, Any]) -> dict[str, Any]:
    return {field: _clean(record.get(column)) for field, (_, column) in BOX_SET_FIELD_SOURCES.items()}


def mirror_values(conn: Any, target: dict[str, Any]) -> dict[str, Any] | None:
    """What the catalogue holds, in the wire vocabulary.

    This is what becomes `expected`. It has to come from the mirror rather than
    from anything the client sent: `expected` is the claim "I was looking at
    this", and only the server knows what was actually shown.
    """
    generation = _active_generation(conn)
    if not generation:
        return None
    if target["entityType"] == "release":
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT release_title, edition, format, country_code, language_code,
                       release_date, runtime_minutes, distributor, disc_regions,
                       disc_count, packaging, finishes, video_resolution,
                       video_codecs, hdr_formats, aspect_ratios, discs
                FROM movievault_v2_releases
                WHERE generation = %s AND release_id = %s
                """,
                (generation, target["entityId"]),
            )
            row = cur.fetchone()
            if not row:
                return None
            cur.execute(
                """
                SELECT language_code, codec, channels, immersive_format
                FROM movievault_v2_release_audio_tracks
                WHERE generation = %s AND release_id = %s
                ORDER BY position
                """,
                (generation, target["entityId"]),
            )
            tracks = [
                {
                    "languageCode": item["language_code"],
                    "codec": item["codec"],
                    "channels": item["channels"],
                    "immersiveFormat": item["immersive_format"],
                }
                for item in (dict(entry) for entry in cur.fetchall())
            ]
            cur.execute(
                """
                SELECT language_code, subtitle_type
                FROM movievault_v2_release_subtitle_languages
                WHERE generation = %s AND release_id = %s
                ORDER BY position
                """,
                (generation, target["entityId"]),
            )
            subtitles = [
                {
                    "languageCode": item["language_code"],
                    "subtitleType": item["subtitle_type"],
                }
                for item in (dict(entry) for entry in cur.fetchall())
            ]
        row = dict(row)
        release_date = row.get("release_date")
        return {
            "title": _clean(row.get("release_title")),
            "edition": _clean(row.get("edition")),
            "format": _clean(row.get("format")),
            "countryCode": _clean(row.get("country_code")),
            "languageCode": _clean(row.get("language_code")),
            "releaseDate": release_date.isoformat() if release_date else None,
            "runtimeMinutes": int(row["runtime_minutes"]) if row.get("runtime_minutes") else None,
            "discCount": int(row["disc_count"]) if row.get("disc_count") else None,
            "distributor": _clean(row.get("distributor")),
            # The mirror does carry this one, unlike `eans` -- so a correction
            # can be composed offline and `expected` is still a real value.
            "discRegions": _mirror_disc_regions(row.get("disc_regions")),
            # The rest of the technical description, mirrored since
            # distribution-4 (`packaging`, video) and -6 (`discs`). Normalised
            # through the same converters the local side uses, so `expected`
            # and `proposed` are comparable as whole values -- a mirror sorted
            # differently from the local list would read as a disagreement
            # about the discs rather than about the order somebody typed.
            "packaging": _vocabulary_list(row.get("packaging"), _MV_PACKAGING),
            "finishes": _vocabulary_list(row.get("finishes"), _MV_FINISHES),
            "videoResolution": (
                _clean(row.get("video_resolution"))
                if _clean(row.get("video_resolution")) in _MV_RESOLUTIONS
                else None
            ),
            "videoCodecs": _vocabulary_list(row.get("video_codecs"), _MV_VIDEO_CODECS),
            "hdrFormats": _vocabulary_list(row.get("hdr_formats"), _MV_HDR_FORMATS),
            "aspectRatios": _aspect_ratios(row.get("aspect_ratios")),
            # The mirror stores what the feed published, verbatim, so the wire
            # spelling is already what upstream holds -- unlike the local rows,
            # which are DiscVault's own columns.
            "discs": _mirror_discs(row.get("discs")),
            # Their own tables since distribution-4, not columns. Read through
            # the same converters, so a mirror row and a local row that say the
            # same thing produce the same value.
            "audioTracks": _audio_tracks(tracks),
            "subtitles": _subtitles(subtitles),
        }
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT title FROM movievault_v2_box_sets
            WHERE generation = %s AND box_set_id = %s
            """,
            (generation, target["entityId"]),
        )
        row = cur.fetchone()
    return {"title": _clean(dict(row).get("title"))} if row else None


def _withheld_reason(field: str, entity: str, value: Any) -> str | None:
    """Why a field with a local counterpart still may not travel.

    Both cases are about shape rather than policy. MovieVault validates
    `countryCode` as two capitals and would reject "Netherlands" outright; it
    puts no pattern at all on `languageCode`, which means it would *accept*
    "Dutch" and quietly poison a shared catalogue. The first is a bad request,
    the second is worse, and neither is worth guessing a mapping for.
    """
    if value is None:
        return None
    if field == "countryCode" and not _COUNTRY_PATTERN.fullmatch(str(value)):
        return "local_value_is_not_a_country_code"
    if field == "languageCode" and not _LANGUAGE_PATTERN.fullmatch(str(value)):
        return "local_value_is_not_a_language_code"
    return None


def eligible_fields(
    *,
    entity: str,
    local: dict[str, Any],
    locked: set[str],
    extra_withheld: dict[str, str] | None = None,
) -> tuple[list[str], dict[str, str]]:
    """The fields this record may offer, and why each of the others may not.

    The refusals are returned rather than dropped so a client can explain
    itself. "Not offered" and "offered but unchanged" look identical in a UI
    that only receives a list.

    `extra_withheld` carries refusals that depend on this *call* rather than on
    the two data models -- today only `eans`, which needs a live catalogue read
    to have an honest `expected`. They are merged in the same map so a caller
    has one place to look.
    """
    sources = RELEASE_FIELD_SOURCES if entity == "movie" else BOX_SET_FIELD_SOURCES
    withheld = dict(RELEASE_FIELDS_WITHHELD if entity == "movie" else BOX_SET_FIELDS_WITHHELD)
    withheld.update(extra_withheld or {})
    allowed: list[str] = []
    for field in sources:
        if field in withheld:
            continue
        lock_names = _FIELD_LOCK_NAMES.get(field, ())
        if any(name in locked for name in lock_names):
            # A lock is a local override, pinned against metadata refresh.
            # Publishing it would push one person's preference into a shared
            # fact -- the exact inversion of what a lock is for.
            withheld[field] = "locked_locally"
            continue
        reason = _withheld_reason(field, entity, local.get(field))
        if reason:
            withheld[field] = reason
            continue
        allowed.append(field)
    return allowed, withheld


def build_changes(
    *,
    allowed: list[str],
    local: dict[str, Any],
    mirror: dict[str, Any],
    fields: list[str] | None = None,
) -> list[dict[str, Any]]:
    """The `changes[]` a correction would carry.

    Only fields that actually differ. A change that proposes what the catalogue
    already holds costs a moderator a decision and teaches nobody anything, and
    `contribution-2` bounds a submission at 25 fields -- spending them on
    agreement would be the wrong trade.
    """
    selected = set(fields) if fields is not None else None
    changes: list[dict[str, Any]] = []
    for field in allowed:
        if selected is not None and field not in selected:
            continue
        proposed = local.get(field)
        expected = mirror.get(field)
        if proposed is None or proposed == expected:
            continue
        changes.append({"field": field, "expected": expected, "proposed": proposed})
    return changes



# ---- The conflict pre-flight ---------------------------------------------
#
# The mirror is a snapshot of a distribution generation, so it is exactly as
# old as the last index sync. A correction composed against it states an
# `expected` that may already have moved, and `expected` is the whole conflict
# check upstream -- so the moderator sees a conflict on a field the contributor
# read correctly at the time. The contributor learns this days later, if at
# all.
#
# `GET /v2/films/{film_id}/releases` answers with the *current* canonical value
# of every field a release correction may touch, keyed by `releaseRef`, which
# for a canonical release is the release id. Anonymous, cached nowhere, and the
# same route the fallback picker already uses -- no new upstream surface.
#
# Run at preview time only. Eligibility renders on every detail screen and just
# needs to know whether anything differs at all; paying a network round trip
# there to sharpen a number nobody has looked at yet would be the wrong trade.

PREFLIGHT_TIMEOUT_SECONDS = 8


def live_release_values(film_id: Any, release_id: Any, *, timeout_seconds: int = PREFLIGHT_TIMEOUT_SECONDS) -> dict[str, Any] | None:
    """The catalogue's current values for one release, or None.

    None means "could not be established" and never "nothing changed": the
    caller falls back to the mirror rather than treating an unreachable
    MovieVault as agreement. Silent on every failure for that reason -- an
    offline instance must still be able to compose a correction.
    """
    if not film_id or not release_id:
        return None
    try:
        from .next_movievault_v2 import _release_details_http, enforced_origin
    except ImportError:  # pragma: no cover - supports direct module execution
        from next_movievault_v2 import _release_details_http, enforced_origin
    try:
        status, content = _release_details_http(
            f"{enforced_origin()}/v2/films/{film_id}/releases",
            method="GET",
            timeout_seconds=timeout_seconds,
        )
        if status != 200 or not content:
            return None
        payload = json.loads(content.decode("utf-8"))
    except Exception:  # pragma: no cover - every failure is the same answer
        return None
    if not isinstance(payload, dict):
        return None
    wanted = str(release_id)
    for summary in payload.get("releases") or []:
        if not isinstance(summary, dict) or str(summary.get("releaseRef")) != wanted:
            continue
        return {
            "edition": _clean(summary.get("edition")),
            "format": _clean(summary.get("format")),
            "countryCode": _clean(summary.get("countryCode")),
            "languageCode": _clean(summary.get("languageCode")),
            "releaseDate": _clean(summary.get("releaseDate")),
            "runtimeMinutes": int(summary["runtimeMinutes"]) if summary.get("runtimeMinutes") else None,
            "discCount": int(summary["discCount"]) if summary.get("discCount") else None,
            "distributor": _clean(summary.get("distributor")),
            "eans": catalogue_eans(summary.get("barcodes")),
            "discRegions": _mirror_disc_regions(summary.get("discRegions")),
            # The technical description, read live. A summary that predates the
            # field simply omits it, and the converters turn that into None --
            # "the catalogue did not say" -- rather than an empty claim.
            "packaging": _vocabulary_list(summary.get("packaging"), _MV_PACKAGING),
            "finishes": _vocabulary_list(summary.get("finishes"), _MV_FINISHES),
            "videoResolution": (
                _clean(summary.get("videoResolution"))
                if _clean(summary.get("videoResolution")) in _MV_RESOLUTIONS
                else None
            ),
            "videoCodecs": _vocabulary_list(summary.get("videoCodecs"), _MV_VIDEO_CODECS),
            "hdrFormats": _vocabulary_list(summary.get("hdrFormats"), _MV_HDR_FORMATS),
            "aspectRatios": _aspect_ratios(summary.get("aspectRatios")),
            "audioTracks": _audio_tracks(summary.get("audioTracks")),
            "subtitles": _subtitles(summary.get("subtitles")),
            "discs": _mirror_discs(summary.get("discs")),
        }
    # The film is known but this release is not among its active ones: it was
    # merged, retired or deleted. Correcting a record that is no longer served
    # is not something to guess at, so this is a refusal rather than a
    # fallback -- distinct from None by carrying the marker below.
    return {"_gone": True}


def live_box_set_values(box_set_id: Any, *, timeout_seconds: int = PREFLIGHT_TIMEOUT_SECONDS) -> dict[str, Any] | None:
    """The catalogue's current values for one box set, or None.

    Same contract as `live_release_values`: None means "could not be
    established", and `_gone` means the catalogue no longer serves it. Kept as
    a separate function rather than a branch because the two read different
    routes with different shapes, and the film one returns a *list* to search.
    """
    if not box_set_id:
        return None
    try:
        from .next_movievault_v2 import _release_details_http, enforced_origin
    except ImportError:  # pragma: no cover - supports direct module execution
        from next_movievault_v2 import _release_details_http, enforced_origin
    try:
        status, content = _release_details_http(
            f"{enforced_origin()}/v2/box-sets/{box_set_id}",
            method="GET",
            timeout_seconds=timeout_seconds,
        )
        # 404 is *not* read as "retired upstream" here, unlike the release
        # path where absence from a 200 list can only mean that. A DiscVault
        # newer than its MovieVault gets 404 from a route that does not exist
        # yet, and treating that as gone would mark every box set unavailable
        # for the length of a deploy skew. Falling back to the mirror is the
        # behaviour that predates this check, so the worst case is no worse
        # than before rather than a regression.
        if status != 200 or not content:
            return None
        payload = json.loads(content.decode("utf-8"))
    except Exception:  # pragma: no cover - every failure is the same answer
        return None
    if not isinstance(payload, dict) or not payload.get("title"):
        return None
    return {"title": _clean(payload.get("title"))}


def _mirror_film_id(conn: Any, target: dict[str, Any]) -> str | None:
    generation = _active_generation(conn)
    if not generation or target.get("entityType") != "release":
        return None
    with conn.cursor() as cur:
        cur.execute(
            "SELECT film_id FROM movievault_v2_releases WHERE generation = %s AND release_id = %s",
            (generation, target["entityId"]),
        )
        row = cur.fetchone()
    return str(dict(row)["film_id"]) if row and dict(row).get("film_id") else None

def correction_preview(
    conn: Any,
    *,
    entity: str,
    record: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    fields: list[str] | None = None,
    preflight: bool = True,
) -> dict[str, Any]:
    """Everything a client needs to show a confirmation, in one answer.

    Deliberately not four round trips: the diff is only meaningful against the
    revision it was computed from, and a client that fetched the target, then
    the mirror, then the locks would be assembling a view the server could have
    handed it consistent.

    `preflight` checks the catalogue's live values before answering. On by
    default because this runs when someone reaches for the button, not on every
    render -- eligibility passes it off, since it only needs to know whether
    anything differs at all.
    """
    target = resolve_target(conn, entity=entity, record=record)
    if not target:
        return {
            "mode": "proposal" if entity == "movie" else "unavailable",
            "target": None,
            "changes": [],
            "withheld": {},
        }
    mirror = mirror_values(conn, target)
    if mirror is None:
        # The lookup index named a record the mirror no longer holds. A stale
        # index is not a correction opportunity.
        return {"mode": "unavailable", "target": None, "changes": [], "withheld": {}}

    # `expected` is the conflict check upstream, so the fresher the value the
    # fewer corrections are refused for having been composed against a snapshot.
    # A failed check is not agreement: falling back to the mirror keeps an
    # offline instance able to contribute, and the answer says which was used.
    source = "mirror"
    if preflight:
        live = (
            live_release_values(_mirror_film_id(conn, target), target["entityId"])
            if target["entityType"] == "release"
            else live_box_set_values(target["entityId"])
        )
        if live and live.get("_gone"):
            # The catalogue no longer serves this record - merged, retired or
            # deleted. Correcting one is not something to guess at.
            return {"mode": "unavailable", "target": None, "changes": [], "withheld": {}}
        if live:
            mirror = live
            source = "catalogue"

    extra_withheld: dict[str, str] = {}
    if entity == "movie":
        local = _local_release_values(
            record,
            metadata or {},
            movie_identifiers_by_type(conn, record.get("id")),
            movie_technical_specs(conn, record.get("id")) if record.get("id") else {},
            _local_discs(conn, record.get("id")),
        )
        locked = movie_locked_fields(metadata or {})
        if source != "catalogue" or not isinstance(mirror.get("eans"), list):
            # `eans` is a complete replacement list and the mirror cannot say
            # what it would replace: `movievault_v2_releases` has no barcode
            # column and the lookup index holds hashes, by design.
            #
            # A live read that came back without a barcode list is refused for
            # the same reason rather than a weaker one. "The catalogue did not
            # say" would otherwise leave `expected` as None, and a replacement
            # list proposed against None is a deletion nobody reviewed -- which
            # is exactly what withholding this field has been preventing.
            #
            # Refused out loud: a field that silently vanishes from the sheet
            # reads as a bug rather than as a reason.
            extra_withheld["eans"] = "needs_live_catalogue"
    else:
        local = _local_box_set_values(record)
        # Containers have no field locks -- the only container lock is artwork,
        # and no artwork field is correctable. So nothing is excluded here, and
        # equally nothing on a box set can be protected from being offered.
        locked = set()

    allowed, withheld = eligible_fields(
        entity=entity, local=local, locked=locked, extra_withheld=extra_withheld
    )
    return {
        "mode": "correction",
        "target": target,
        "changes": build_changes(allowed=allowed, local=local, mirror=mirror, fields=fields),
        "withheld": withheld,
        # Which of the two the diff was computed against. A client shows the
        # difference: a diff from the mirror may already have moved, and saying
        # so is more honest than presenting both the same way.
        "comparedAgainst": source,
    }


# ---- The contribution log ------------------------------------------------
#
# Written when a correction is queued, updated when MovieVault acknowledges it
# and again when a moderator decides. Read by the detail screens to answer the
# only question the sender has afterwards.

TERMINAL_LOG_STATUSES = frozenset({"accepted", "partially_accepted", "rejected", "failed"})


def record_contribution(
    conn: Any,
    *,
    entity: str,
    record: dict[str, Any],
    target: dict[str, Any],
    changes: list[dict[str, Any]],
    job_id: Any,
    actor_id: Any = None,
) -> None:
    """Log one correction at the moment it is queued.

    Written here rather than when it is sent, because "queued and never
    delivered" is itself an outcome worth being able to see. A row that stays
    at `queued` says the worker never got to it, which is a different fault
    from one that says `failed`.
    """
    if not _table_exists(conn, "movievault_v2_contributions"):
        return
    entity_type = _text(target.get("entityType"))
    entity_id = _text(target.get("entityId"))
    if not entity_type or not entity_id or not job_id:
        return
    is_movie = entity == "movie"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO movievault_v2_contributions (
                id, entity_type, entity_id, movie_id, container_id, job_id,
                fields, base_revision, submitted_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (job_id) WHERE job_id IS NOT NULL DO NOTHING
            """,
            (
                uuid.uuid4(),
                entity_type,
                entity_id,
                record.get("id") if is_movie else None,
                None if is_movie else record.get("id"),
                job_id,
                [_text(item.get("field")) for item in changes],
                int(target.get("baseRevision") or 0) or None,
                actor_id,
            ),
        )


def update_contribution_by_job(conn: Any, job_id: Any, **values: Any) -> None:
    _update_contribution(conn, "job_id", job_id, values)


def update_contribution_by_contribution_id(conn: Any, contribution_id: str, **values: Any) -> dict[str, Any] | None:
    """Record an answer, and say whether *this* call is what settled it.

    Returns the row only on the transition into a terminal status, and only
    once: the guard is in the WHERE clause, so two workers racing the same
    verdict produce one winner and one None. That matters because the caller
    turns a non-None into a notification, and a verdict announced twice is
    worse than one announced late.
    """
    key = _text(contribution_id)
    status = _text(values.get("status"))
    if not key or not _table_exists(conn, "movievault_v2_contributions"):
        return None
    if status not in TERMINAL_LOG_STATUSES:
        _update_contribution(conn, "contribution_id", key, values)
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE movievault_v2_contributions
            SET status = %s,
                canonical_target_id = COALESCE(%s, canonical_target_id),
                duplicate_of = COALESCE(%s, duplicate_of),
                updated_at = now()
            WHERE contribution_id = %s AND NOT (status = ANY(%s))
            RETURNING id, status, fields, submitted_by, movie_id, container_id
            """,
            (
                status,
                values.get("canonical_target_id"),
                values.get("duplicate_of"),
                key,
                list(TERMINAL_LOG_STATUSES),
            ),
        )
        row = cur.fetchone()
    if not row:
        return None
    row = dict(row)
    return {
        "id": str(row["id"]),
        "status": _text(row.get("status")),
        "fields": list(row.get("fields") or []),
        "submittedBy": str(row["submitted_by"]) if row.get("submitted_by") else None,
        "movieId": str(row["movie_id"]) if row.get("movie_id") else None,
        "containerId": str(row["container_id"]) if row.get("container_id") else None,
    }


def _update_contribution(conn: Any, column: str, key: Any, values: dict[str, Any]) -> None:
    """Update the log row, ignoring keys the caller did not set.

    Silent when there is no row: the log is a record of what happened, never a
    precondition for it. A correction whose log row was cleaned up must still
    be delivered and still be reported.
    """
    if not key or not _table_exists(conn, "movievault_v2_contributions"):
        return
    allowed = ("status", "contribution_id", "canonical_target_id", "duplicate_of", "last_error")
    assignments = [f"{name} = %s" for name in allowed if name in values]
    if not assignments:
        return
    parameters = [values[name] for name in allowed if name in values]
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE movievault_v2_contributions "
            f"SET {', '.join(assignments)}, updated_at = now() WHERE {column} = %s",
            (*parameters, key),
        )


def latest_contribution(conn: Any, *, entity: str, record: dict[str, Any]) -> dict[str, Any] | None:
    """The most recent correction sent for this record, if any.

    Only the latest: the screen answers "did my correction land", and a history
    of every attempt is a different feature with a different surface.
    """
    if not _table_exists(conn, "movievault_v2_contributions"):
        return None
    record_id = record.get("id")
    if not record_id:
        return None
    column = "movie_id" if entity == "movie" else "container_id"
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT status, fields, contribution_id, canonical_target_id,
                   duplicate_of, last_error, created_at, updated_at
            FROM movievault_v2_contributions
            WHERE {column} = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (record_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    row = dict(row)
    status = _text(row.get("status")) or "queued"
    return {
        "status": status,
        "settled": status in TERMINAL_LOG_STATUSES,
        "fields": list(row.get("fields") or []),
        "contributionId": _text(row.get("contribution_id")) or None,
        "canonicalTargetId": _text(row.get("canonical_target_id")) or None,
        "duplicateOf": _text(row.get("duplicate_of")) or None,
        "lastError": _text(row.get("last_error")) or None,
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def contribution_history(conn: Any, *, actor_id: Any = None, limit: int = 50) -> list[dict[str, Any]]:
    """What this instance has contributed, newest first.

    Scoped to one contributor when `actor_id` is given. An owner reading the
    whole instance and a user reading their own are different questions, and
    the caller decides which is being asked -- the log itself takes no view.

    The record's current title is joined in rather than stored on the row: a
    contribution is about a record, not about what that record was called at
    the time, and a stored copy would slowly drift into naming something that
    no longer exists under that name.
    """
    if not _table_exists(conn, "movievault_v2_contributions"):
        return []
    conditions = ["TRUE"]
    parameters: list[Any] = []
    if actor_id:
        conditions.append("c.submitted_by = %s")
        parameters.append(actor_id)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT c.id, c.entity_type, c.status, c.fields, c.contribution_id,
                   c.canonical_target_id, c.duplicate_of, c.last_error,
                   c.created_at, c.updated_at, c.movie_id, c.container_id,
                   m.title AS movie_title, m.public_id AS movie_public_id,
                   ct.title AS container_title, ct.public_id AS container_public_id
            FROM movievault_v2_contributions c
            LEFT JOIN movies m ON m.id = c.movie_id
            LEFT JOIN containers ct ON ct.id = c.container_id
            WHERE {' AND '.join(conditions)}
            ORDER BY c.created_at DESC
            LIMIT %s
            """,
            (*parameters, max(1, min(int(limit or 50), 200))),
        )
        rows = cur.fetchall()
    history: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        is_movie = row.get("movie_id") is not None
        status = _text(row.get("status")) or "queued"
        history.append(
            {
                "id": str(row["id"]),
                "entity": "movie" if is_movie else ("container" if row.get("container_id") else None),
                "entityType": _text(row.get("entity_type")),
                # Null when the local record was deleted after contributing.
                # The correction still happened, and saying so beats hiding it.
                "recordId": str(row["movie_id"] or row["container_id"]) if (row.get("movie_id") or row.get("container_id")) else None,
                "publicId": _text(row.get("movie_public_id") if is_movie else row.get("container_public_id")) or None,
                "title": _text(row.get("movie_title") if is_movie else row.get("container_title")) or None,
                "status": status,
                "settled": status in TERMINAL_LOG_STATUSES,
                "fields": list(row.get("fields") or []),
                "contributionId": _text(row.get("contribution_id")) or None,
                "canonicalTargetId": _text(row.get("canonical_target_id")) or None,
                "duplicateOf": _text(row.get("duplicate_of")) or None,
                "lastError": _text(row.get("last_error")) or None,
                "createdAt": row.get("created_at"),
                "updatedAt": row.get("updated_at"),
            }
        )
    return history
