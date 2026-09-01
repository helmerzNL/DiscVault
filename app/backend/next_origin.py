"""The film's country of origin and original language.

Both are TMDB-owned facts about the *film*, deliberately kept apart from
``movies.country`` and ``movies.language``, which describe the *disc*: which
market a pressing was made for. A Dutch Blu-ray of a Japanese film has
``country='NL'`` and an origin of ``JP``, and conflating the two would record
the Netherlands as the origin of Ran for everyone who owns a European edition.

Nothing here holds a catalogue of countries or languages. Unlike genres -- 19
closed TMDB ids whose labels ship as ``genre.<key>`` i18n keys -- these are open
sets of several hundred codes whose display names come from ``Intl.DisplayNames``
at render time. So the only thing worth validating is the *shape* of a code; a
hand-maintained allowlist could not add anything and would eventually reject a
real answer TMDB gave us.
"""

from __future__ import annotations

import re
from typing import Any


# ISO 3166-1 alpha-2, upper case. TMDB is consistent about this.
_REGION_RE = re.compile(r"^[A-Za-z]{2}$")

# A BCP-47 primary subtag plus optional script/region subtags. TMDB returns
# ISO 639-1 ('ja'), occasionally 639-2 ('cmn'), and occasionally a subtagged
# form ('cmn-Hans'). All three are stored verbatim rather than folded, because
# folding 'cmn-Hans' to 'cmn' throws away the only part that distinguishes it.
_LANGUAGE_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")

# TMDB uses this for "no language" on silent films and some documentaries. It is
# not a language and must not be stored as one -- it would show up in the filter
# as an entry named after whatever Intl.DisplayNames makes of 'xx'.
_UNKNOWN_LANGUAGE_CODES = frozenset({"xx", "zxx", "und", "mul"})


def normalize_region_code(value: Any) -> str:
    """Return an upper-case ISO 3166-1 alpha-2 code, or '' when unusable."""
    text = str(value or "").strip()
    if not _REGION_RE.match(text):
        return ""
    return text.upper()


def normalize_region_codes(values: Any) -> list[str]:
    """Deduplicate a list of region codes, preserving the order given.

    Order is preserved because TMDB lists the lead producer of a co-production
    first, and that ordering is information.
    """
    if values is None:
        return []
    if isinstance(values, (str, bytes)):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        return []
    codes: list[str] = []
    seen: set[str] = set()
    for raw in values:
        code = normalize_region_code(raw)
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def normalize_language_code(value: Any) -> str:
    """Return a stored-form language subtag, or '' when unusable.

    The primary subtag is lower-cased because BCP-47 says so and because a
    mixture of 'JA' and 'ja' would filter as two languages. Subtags keep their
    given case ('cmn-Hans', not 'cmn-hans').
    """
    text = str(value or "").strip()
    if not text:
        return ""
    parts = text.split("-")
    parts[0] = parts[0].lower()
    candidate = "-".join(parts)
    if not _LANGUAGE_RE.match(candidate):
        return ""
    if parts[0] in _UNKNOWN_LANGUAGE_CODES:
        return ""
    return candidate


def origin_from_tmdb_details(data: Any) -> dict[str, Any]:
    """Extract ``{originalLanguage, originCountries}`` from a TMDB movie payload.

    ``origin_country`` wins over ``production_countries`` when both are present.
    They answer different questions: origin_country is where the film is *from*,
    production_countries is every country that put money in, so a US-financed
    Japanese film lists both and only the first is the origin. TMDB does not
    populate origin_country on every record, hence the fallback.

    Returns a dict with both keys always present, so a caller can tell "TMDB
    answered and the film has no origin" from "TMDB was not asked" -- the latter
    is a ``None`` at the call site, never an empty dict.
    """
    payload = data if isinstance(data, dict) else {}
    countries = normalize_region_codes(payload.get("origin_country"))
    if not countries:
        countries = normalize_region_codes(
            [
                item.get("iso_3166_1")
                for item in payload.get("production_countries") or []
                if isinstance(item, dict)
            ]
        )
    return {
        "originalLanguage": normalize_language_code(payload.get("original_language")),
        "originCountries": countries,
    }


def normalize_film_origin(value: Any) -> dict[str, Any] | None:
    """Validate a ``filmOrigin`` block coming back from a plugin.

    ``None`` means "no answer" and leaves stored associations alone. A dict --
    including one that is entirely empty -- means "answered", and an empty answer
    clears what is stored. That is the same provided-vs-absent distinction the
    genre pipeline makes, and it is the difference between a film whose origin
    TMDB does not know and a film nobody asked about.
    """
    if not isinstance(value, dict):
        return None
    return {
        "originalLanguage": normalize_language_code(value.get("originalLanguage")),
        "originCountries": normalize_region_codes(value.get("originCountries")),
    }
