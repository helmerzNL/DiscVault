"""Typed product identifiers for a movie.

`movies.barcode` holds exactly one value and carries a UNIQUE constraint,
because one scan has to resolve to one film. That is the right shape for
*resolution* and the wrong shape for *description*: a single pressing routinely
carries an EAN for Europe, a UPC for North America, an Amazon ASIN and a
distributor catalogue number, and MovieVault models all four
(`content.release_identifiers`, keyed `(identifier_type, identifier_value)`).

This module adds the descriptive half beside the resolving one. `movies.barcode`
is untouched and still decides what a scan matches; `movie_product_identifiers`
records everything else the product is known by.

Two of the five types can be derived from a scan and three cannot:

- **ean / upc / isbn** are GS1 barcodes. The digit count names the symbology and
  the check digit proves the read, so the type is computed rather than asked.
- **asin** is an Amazon-internal code printed in text, never in the barcode.
- **catalog_number** is printed on the spine by the distributor.

The last two are typed by a person, so nothing here guesses at them.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

#: MovieVault's vocabulary, copied deliberately rather than invented. The
#: constraint on `content.release_identifiers` allows exactly these five, and a
#: sixth type here would be one DiscVault could record and never contribute.
IDENTIFIER_TYPES: tuple[str, ...] = ("ean", "upc", "isbn", "asin", "catalog_number")

#: The types a barcode scan can produce. The other two are text on a package.
SCANNABLE_TYPES: frozenset[str] = frozenset({"ean", "upc", "isbn"})

#: 10 characters, letters and digits. A purely numeric 10 is an ISBN-10 rather
#: than an ASIN, which is why the all-digit case is rejected below.
_ASIN_PATTERN = re.compile(r"^[A-Z0-9]{10}$")

#: Bookland: an ISBN-13 is an EAN-13 in the 978/979 prefix range.
_ISBN_PREFIXES = ("978", "979")


def gs1_check_digit_ok(digits: str) -> bool:
    """The GS1 mod-10 check, the same arithmetic MovieVault validates with.

    Kept identical to `normalize_ean` in MovieVault's `content/models.py` on
    purpose: a value DiscVault accepts and MovieVault rejects would be a
    contribution that fails at the far end for a reason the sender cannot see.
    """
    if len(digits) < 2 or not digits.isdigit():
        return False
    expected = (
        10
        - sum(
            int(digit) * (3 if index % 2 == 0 else 1)
            for index, digit in enumerate(reversed(digits[:-1]))
        )
        % 10
    ) % 10
    return expected == int(digits[-1])


def classify_scanned_identifier(value: Any) -> tuple[str, str] | None:
    """`(type, digits)` for a scanned barcode, or None if it is not one.

    The digits are returned exactly as scanned -- 8, 12, 13 or 14 of them, with
    any leading zero intact. Rewriting them would be tempting for the 13-digit
    UPC-A case below and is wrong: MovieVault matches an identifier on its
    literal value, so a normalised form is a different identifier.
    """
    if not isinstance(value, str):
        return None
    if any(not character.isdigit() and character not in {" ", "-"} for character in value):
        return None
    digits = "".join(character for character in value if character.isdigit())
    if len(digits) not in {8, 12, 13, 14} or not gs1_check_digit_ok(digits):
        return None
    if len(digits) == 12:
        return ("upc", digits)
    if len(digits) == 13:
        if digits.startswith(_ISBN_PREFIXES):
            return ("isbn", digits)
        # GS1 made UPC-A a subset of EAN-13 by prefixing a zero, and most
        # scanners hand back the padded form. So a leading zero is not an EAN
        # that happens to start with 0 -- it is a North American UPC, and
        # calling it one is what lets a US pressing match a US pressing.
        if digits.startswith("0"):
            return ("upc", digits)
        return ("ean", digits)
    # EAN-8 and GTIN-14. Neither has a type of its own upstream, and both are
    # article numbers in the same scheme.
    return ("ean", digits)


def normalize_identifier(identifier_type: str, value: Any) -> str | None:
    """The storable form of a typed identifier, or None if it is not valid.

    Used for values a person typed, where the type is stated rather than
    derived. A stated type is still checked: someone typing an EAN into the
    ASIN box should be told, not silently recorded.
    """
    if identifier_type not in IDENTIFIER_TYPES or not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    if identifier_type in SCANNABLE_TYPES:
        classified = classify_scanned_identifier(text)
        if not classified or classified[0] != identifier_type:
            return None
        return classified[1]
    if identifier_type == "asin":
        candidate = text.upper()
        # An ASIN for a book *is* its ISBN-10, so an all-digit 10 is ambiguous
        # and belongs under `isbn`. Refusing it here keeps one value out of two
        # rows claiming to be different identifiers.
        if not _ASIN_PATTERN.fullmatch(candidate) or candidate.isdigit():
            return None
        return candidate
    return text[:120] if len(text) <= 120 else None


def table_available(conn: Any) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.movie_product_identifiers') IS NOT NULL AS present")
        row = cur.fetchone()
    return bool(dict(row).get("present")) if row else False


def movie_identifiers_by_type(conn: Any, movie_id: UUID) -> list[dict[str, str]]:
    """Every typed identifier for one movie, in a stable order.

    Ordered by type and then value so two reads of an unchanged record produce
    the same payload -- a client diffing them should see a change only when one
    happened.
    """
    if not movie_id or not table_available(conn):
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT identifier_type, identifier_value
            FROM movie_product_identifiers
            WHERE movie_id = %s
            ORDER BY identifier_type, identifier_value
            """,
            (movie_id,),
        )
        return [
            {"type": str(dict(row)["identifier_type"]), "value": str(dict(row)["identifier_value"])}
            for row in cur.fetchall()
        ]


def attach_movie_identifiers(conn: Any, movies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach `productIdentifiers` to each movie in one query.

    Same shape as `attach_movie_technical_specs`: a bootstrap carries up to
    1000 movies and a per-movie lookup would be 1000 round trips. A movie with
    no rows gets an empty list rather than an absent key, because the clients
    read an absent key as "no opinion, keep what you have".
    """
    if not movies:
        return movies
    if not table_available(conn):
        for movie in movies:
            movie["productIdentifiers"] = []
        return movies
    movie_ids = [movie.get("id") for movie in movies if movie.get("id")]
    by_movie: dict[str, list[dict[str, str]]] = {}
    if movie_ids:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT movie_id, identifier_type, identifier_value
                FROM movie_product_identifiers
                WHERE movie_id = ANY(%s)
                ORDER BY identifier_type, identifier_value
                """,
                (movie_ids,),
            )
            for raw in cur.fetchall():
                row = dict(raw)
                by_movie.setdefault(str(row["movie_id"]), []).append(
                    {"type": str(row["identifier_type"]), "value": str(row["identifier_value"])}
                )
    for movie in movies:
        movie["productIdentifiers"] = by_movie.get(str(movie.get("id")), [])
    return movies


def set_movie_identifiers(conn: Any, movie_id: UUID, entries: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Replace the typed identifiers for one movie, and return what was stored.

    A replacement rather than a merge, because the edit surface presents the
    whole set: with a merge there is no way to express "remove this one".
    Invalid entries are dropped rather than raising -- the caller has already
    validated for the user, and a single bad row should not lose the good ones.
    """
    if not table_available(conn):
        return []
    seen: set[tuple[str, str]] = set()
    rows: list[tuple[str, str]] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        identifier_type = str(entry.get("type") or "").strip().lower()
        value = normalize_identifier(identifier_type, entry.get("value"))
        if not value:
            continue
        key = (identifier_type, value)
        if key in seen:
            continue
        seen.add(key)
        rows.append(key)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM movie_product_identifiers WHERE movie_id = %s", (movie_id,))
        for identifier_type, value in rows:
            cur.execute(
                """
                INSERT INTO movie_product_identifiers (movie_id, identifier_type, identifier_value)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (movie_id, identifier_type, value),
            )
    return sorted(
        ({"type": identifier_type, "value": value} for identifier_type, value in rows),
        key=lambda item: (item["type"], item["value"]),
    )


def contributable_eans(identifiers: list[dict[str, str]], *, barcode: Any = None) -> list[str]:
    """The complete `eans` replacement list for a field correction.

    **EAN-typed values only.** MovieVault's correction field looks narrower than
    its table: `_replace_release_eans` deletes and inserts under
    `identifier_type = 'ean'` and nothing else, and its conflict check queries
    the same one type. So a UPC sent here would be written as an EAN, and a UPC
    the release already holds under `identifier_type = 'upc'` would be invisible
    to the check -- one value in two rows claiming to be two identifiers.

    UPC, ISBN, ASIN and catalogue numbers therefore stay local: upstream has no
    correction field for them, and inventing one by widening this list would
    corrupt the namespace it writes into.

    Sorted, because upstream reads its side `order by identifier_value` and this
    list is compared against that one.
    """
    values: list[str] = []
    for entry in identifiers or []:
        if entry.get("type") == "ean" and entry.get("value"):
            values.append(str(entry["value"]))
    scanned = classify_scanned_identifier(barcode) if barcode else None
    if scanned and scanned[0] == "ean":
        # The scanned barcode is the one value this instance is certain about,
        # so it belongs in the list even when nobody typed it in as well.
        values.append(scanned[1])
    return sorted(dict.fromkeys(values))


def catalogue_eans(barcodes: Any) -> list[str] | None:
    """`expected` for `eans`, read off a live `ReleaseSummary`.

    The mirror cannot answer this: `movievault_v2_releases` has no barcode
    column, and the lookup index holds hashes rather than values, by design. So
    `eans` is correctable only when the pre-flight reached the catalogue --
    which is the right constraint anyway. The field is a complete replacement,
    and proposing one without knowing what it replaces is the deletion this was
    withheld to prevent.

    The wire vocabulary is per-symbology (`ean8`/`ean13`/`gtin14`/`upca`) while
    the stored one is per-namespace (`ean`/`upc`), so everything that is not a
    `upca` is an EAN-typed row upstream. A mis-derivation here surfaces as a
    conflict on `expected` rather than a bad write -- the correction is refused,
    not misapplied.
    """
    if not isinstance(barcodes, list):
        return None
    values: list[str] = []
    for item in barcodes:
        if not isinstance(item, dict):
            continue
        if str(item.get("type")) == "upca":
            continue
        value = item.get("value")
        if isinstance(value, str) and value.isdigit():
            values.append(value)
    return sorted(dict.fromkeys(values))
