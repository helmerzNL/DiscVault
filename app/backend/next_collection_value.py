"""What a DiscVault collection is worth, now and over time.

Two things live here:

1.  **The ownership rule.** A movie's ``estimated_value`` counts toward the
    collection only while the movie is *not* a member of a box set. A box set is
    bought as one product for one price, so once a disc sits inside one, the set
    carries the money and the member's own amount is suppressed — see
    :func:`movie_value_locked_sql`. The amount is never erased: membership only
    hides it, so pulling a film back out restores exactly what was there.

    Vaults and collections carry no value of their own. Theirs is the sum of what
    they hold, which is why there is no ``estimated_value`` write path for them.

2.  **The snapshot contract.** ``collection_value_snapshots`` keeps one row per
    user, scope and calendar day (migration 062). The totals are overwritten in
    place as the user edits, so a value-over-time series cannot be reconstructed
    afterwards — it has to be captured as it happens. Every price-affecting write
    calls :func:`record_collection_value_snapshot`, and the last write of a day
    wins, which keeps the series one point per day rather than one point per edit.

The scoping predicates (which movies and containers an actor may see) are passed
in rather than imported: ``visible_movie_where_sql`` and
``visible_container_where_sql`` live in ``next_app``, which imports this module.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

try:
    from psycopg.types.json import Jsonb
except ModuleNotFoundError:  # pragma: no cover - test environments without psycopg
    class Jsonb:  # type: ignore[no-redef]
        def __init__(self, value: Any) -> None:
            self.value = value

try:
    from .next_common import table_exists
    from .next_preferences import PRICE_DISPLAY_FALLBACK_RATES, price_display_exchange_rates
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_common import table_exists
    from next_preferences import PRICE_DISPLAY_FALLBACK_RATES, price_display_exchange_rates

logger = logging.getLogger("discvault.next.collection_value")

# Snapshots are stored in EUR because the rate table the price display already
# fetches is EUR-based (next_preferences.price_display_exchange_rates).
SNAPSHOT_BASE_CURRENCY = "EUR"

# Amounts stored without a currency (movies.estimated_value_currency is nullable
# on purpose, see migration 057) land in this bucket. They are reported, never
# converted — guessing their unit would misstate money.
UNKNOWN_CURRENCY_KEY = ""

# Collections nest arbitrarily deep and cycles exist in the wild, so the walk is
# both cycle-guarded and depth-capped.
COLLECTION_MAX_DEPTH = 32

SNAPSHOT_SCOPE_TYPES = ("total", "vault", "collection")


def movie_value_locked_sql(alias: str = "m") -> str:
    """SQL predicate: this movie belongs to at least one live box set.

    A locked movie's own estimated value is not editable and does not count
    toward any total — the box set carries the value instead.
    """
    return f"""
        EXISTS (
            SELECT 1
            FROM container_movies val_cm
            JOIN containers val_c ON val_c.id = val_cm.container_id
            WHERE val_cm.movie_id = {alias}.id
              AND val_c.container_type = 'box_set'
              AND val_c.deleted_at IS NULL
        )
    """


def movie_value_lock(conn, movie_id: UUID | str) -> dict[str, Any] | None:
    """The box set that owns this movie's value, or ``None`` when it is free.

    Returns the first box set by title so the UI can name it in the hint it shows
    beside the disabled price field.
    """
    if not table_exists(conn, "container_movies") or not table_exists(conn, "containers"):
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.public_id, c.title
            FROM container_movies cm
            JOIN containers c ON c.id = cm.container_id
            WHERE cm.movie_id = %s
              AND c.container_type = 'box_set'
              AND c.deleted_at IS NULL
            ORDER BY lower(c.title), c.id
            LIMIT 1
            """,
            (movie_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "id": str(row.get("id")),
        "publicId": row.get("public_id"),
        "title": row.get("title"),
    }


# ---------------------------------------------------------------------------
# Currency bucket helpers
#
# A bucket is {currency_code: Decimal}. Sums stay per currency for as long as
# possible so the stored snapshot keeps the raw amounts and a later reader can
# re-express a point without inheriting the rate that applied on capture day.
# ---------------------------------------------------------------------------


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _currency_key(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text or UNKNOWN_CURRENCY_KEY


def _add_bucket(target: dict[str, Decimal], source: dict[str, Decimal]) -> dict[str, Decimal]:
    for currency, amount in source.items():
        target[currency] = target.get(currency, Decimal("0")) + amount
    return target


def _bucket_from_rows(rows: list[dict[str, Any]]) -> dict[str, Decimal]:
    bucket: dict[str, Decimal] = {}
    for row in rows or []:
        bucket[_currency_key(row.get("currency"))] = (
            bucket.get(_currency_key(row.get("currency")), Decimal("0")) + _decimal(row.get("amount"))
        )
    return bucket


def convert_bucket(
    bucket: dict[str, Decimal],
    *,
    base_currency: str = SNAPSHOT_BASE_CURRENCY,
    exchange_rates: dict[str, float] | None = None,
) -> tuple[Decimal, Decimal]:
    """Convert a per-currency bucket into ``base_currency``.

    Returns ``(converted_total, unconvertible_total)``. Amounts in the unknown
    bucket, or in a currency the rate table does not carry, are reported
    separately instead of being folded in at a made-up rate.
    """
    rates = dict(exchange_rates or PRICE_DISPLAY_FALLBACK_RATES)
    base_rate = rates.get(str(base_currency or "").upper())
    total = Decimal("0")
    unconvertible = Decimal("0")
    for currency, amount in bucket.items():
        if currency == UNKNOWN_CURRENCY_KEY or not base_rate:
            unconvertible += amount
            continue
        from_rate = rates.get(currency)
        if not from_rate:
            unconvertible += amount
            continue
        total += (amount / _decimal(from_rate)) * _decimal(base_rate)
    return total.quantize(Decimal("0.01")), unconvertible.quantize(Decimal("0.01"))


def _bucket_payload(bucket: dict[str, Decimal]) -> dict[str, str]:
    return {currency: str(amount.quantize(Decimal("0.01"))) for currency, amount in sorted(bucket.items())}


# ---------------------------------------------------------------------------
# Value computation
# ---------------------------------------------------------------------------


def compute_collection_value(
    conn,
    *,
    movie_where: str,
    movie_params: list[Any],
    container_where: str,
    container_params: list[Any],
    base_currency: str = SNAPSHOT_BASE_CURRENCY,
    exchange_rates: dict[str, float] | None = None,
) -> dict[str, Any]:
    """What the visible collection is worth, in total and per vault/collection.

    ``movie_where``/``container_where`` are the actor-scoped predicates produced
    by ``visible_movie_where_sql(conn, actor, "m")`` and
    ``visible_container_where_sql(conn, actor, "c")``.
    """
    empty = {
        "baseCurrency": base_currency,
        "total": "0.00",
        "byCurrency": {},
        "unconvertible": "0.00",
        "pricedCount": 0,
        "unpricedCount": 0,
        "scopes": {"vaults": [], "collections": []},
    }
    if not table_exists(conn, "movies") or not table_exists(conn, "containers"):
        return empty

    has_container_movies = table_exists(conn, "container_movies")
    has_collection_items = table_exists(conn, "collection_items")
    locked_sql = movie_value_locked_sql("m") if has_container_movies else "FALSE"

    def rows(sql: str, params: list[Any]) -> list[dict[str, Any]]:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() or []

    # --- free movies (not owned by a box set) --------------------------------
    movie_rows = rows(
        f"""
        SELECT COALESCE(NULLIF(TRIM(UPPER(m.estimated_value_currency)), ''), '') AS currency,
               SUM(m.estimated_value) AS amount,
               COUNT(*)::int AS count
        FROM movies m
        WHERE {movie_where}
          AND m.estimated_value IS NOT NULL
          AND NOT {locked_sql}
        GROUP BY 1
        """,
        list(movie_params),
    )
    movie_bucket = _bucket_from_rows(movie_rows)
    priced_count = sum(int(row.get("count") or 0) for row in movie_rows)

    unpriced_rows = rows(
        f"""
        SELECT COUNT(*)::int AS count
        FROM movies m
        WHERE {movie_where}
          AND m.estimated_value IS NULL
          AND NOT {locked_sql}
        """,
        list(movie_params),
    )
    unpriced_count = int((unpriced_rows[0] if unpriced_rows else {}).get("count") or 0)

    # --- box sets carry their own value --------------------------------------
    box_set_rows = rows(
        f"""
        SELECT c.id AS container_id,
               COALESCE(NULLIF(TRIM(UPPER(c.estimated_value_currency)), ''), '') AS currency,
               c.estimated_value AS amount
        FROM containers c
        WHERE ({container_where})
          AND c.container_type = 'box_set'
          AND c.deleted_at IS NULL
        """,
        list(container_params),
    )
    box_set_values: dict[str, dict[str, Decimal]] = {}
    box_set_bucket: dict[str, Decimal] = {}
    for row in box_set_rows:
        container_id = str(row.get("container_id"))
        if row.get("amount") is None:
            unpriced_count += 1
            box_set_values[container_id] = {}
            continue
        bucket = {_currency_key(row.get("currency")): _decimal(row.get("amount"))}
        box_set_values[container_id] = bucket
        _add_bucket(box_set_bucket, bucket)
        priced_count += 1

    total_bucket = _add_bucket(dict(movie_bucket), box_set_bucket)

    # --- per-vault ------------------------------------------------------------
    vault_buckets: dict[str, dict[str, Decimal]] = {}
    vault_titles: dict[str, str] = {}
    if has_container_movies:
        for row in rows(
            f"""
            SELECT c.id AS container_id,
                   c.title AS title,
                   COALESCE(NULLIF(TRIM(UPPER(m.estimated_value_currency)), ''), '') AS currency,
                   SUM(m.estimated_value) AS amount
            FROM containers c
            JOIN container_movies cm ON cm.container_id = c.id
            JOIN movies m ON m.id = cm.movie_id
            WHERE ({container_where})
              AND c.container_type = 'vault'
              AND c.deleted_at IS NULL
              AND {movie_where}
              AND m.estimated_value IS NOT NULL
              AND NOT {locked_sql}
            GROUP BY 1, 2, 3
            """,
            list(container_params) + list(movie_params),
        ):
            container_id = str(row.get("container_id"))
            vault_titles[container_id] = row.get("title")
            bucket = vault_buckets.setdefault(container_id, {})
            bucket[_currency_key(row.get("currency"))] = (
                bucket.get(_currency_key(row.get("currency")), Decimal("0")) + _decimal(row.get("amount"))
            )
        # Vaults with nothing priced in them still belong in the series.
        for row in rows(
            f"""
            SELECT c.id AS container_id, c.title AS title
            FROM containers c
            WHERE ({container_where})
              AND c.container_type = 'vault'
              AND c.deleted_at IS NULL
            """,
            list(container_params),
        ):
            container_id = str(row.get("container_id"))
            vault_titles.setdefault(container_id, row.get("title"))
            vault_buckets.setdefault(container_id, {})

    # --- per-collection (nested, cycle-guarded) -------------------------------
    collection_buckets: dict[str, dict[str, Decimal]] = {}
    collection_titles: dict[str, str] = {}
    if has_collection_items:
        for row in rows(
            f"""
            SELECT c.id AS container_id, c.title AS title
            FROM containers c
            WHERE ({container_where})
              AND c.container_type = 'collection'
              AND c.deleted_at IS NULL
            """,
            list(container_params),
        ):
            collection_titles[str(row.get("container_id"))] = row.get("title")

        direct_movie_buckets: dict[str, dict[str, Decimal]] = {}
        for row in rows(
            f"""
            SELECT ci.collection_id AS container_id,
                   COALESCE(NULLIF(TRIM(UPPER(m.estimated_value_currency)), ''), '') AS currency,
                   SUM(m.estimated_value) AS amount
            FROM collection_items ci
            JOIN movies m ON m.id = ci.item_id
            WHERE ci.item_type = 'movie'
              AND {movie_where}
              AND m.estimated_value IS NOT NULL
              AND NOT {locked_sql}
            GROUP BY 1, 2
            """,
            list(movie_params),
        ):
            bucket = direct_movie_buckets.setdefault(str(row.get("container_id")), {})
            bucket[_currency_key(row.get("currency"))] = (
                bucket.get(_currency_key(row.get("currency")), Decimal("0")) + _decimal(row.get("amount"))
            )

        children: dict[str, list[tuple[str, str]]] = {}
        for row in rows(
            """
            SELECT collection_id, item_type, item_id
            FROM collection_items
            WHERE item_type IN ('box_set', 'vault', 'collection')
            """,
            [],
        ):
            children.setdefault(str(row.get("collection_id")), []).append(
                (str(row.get("item_type")), str(row.get("item_id")))
            )

        def resolve(collection_id: str, seen: frozenset[str], depth: int) -> dict[str, Decimal]:
            if collection_id in seen or depth > COLLECTION_MAX_DEPTH:
                return {}
            bucket = dict(direct_movie_buckets.get(collection_id) or {})
            next_seen = seen | {collection_id}
            for item_type, item_id in children.get(collection_id, []):
                if item_type == "box_set":
                    _add_bucket(bucket, box_set_values.get(item_id) or {})
                elif item_type == "vault":
                    _add_bucket(bucket, vault_buckets.get(item_id) or {})
                elif item_type == "collection":
                    _add_bucket(bucket, resolve(item_id, next_seen, depth + 1))
            return bucket

        for collection_id in collection_titles:
            collection_buckets[collection_id] = resolve(collection_id, frozenset(), 1)

    def scope_payload(
        buckets: dict[str, dict[str, Decimal]], titles: dict[str, str]
    ) -> list[dict[str, Any]]:
        payload = []
        for scope_id, bucket in buckets.items():
            converted, unconvertible = convert_bucket(
                bucket, base_currency=base_currency, exchange_rates=exchange_rates
            )
            payload.append(
                {
                    "id": scope_id,
                    "title": titles.get(scope_id),
                    "total": str(converted),
                    "byCurrency": _bucket_payload(bucket),
                    "unconvertible": str(unconvertible),
                }
            )
        payload.sort(key=lambda item: (str(item.get("title") or "").lower(), item["id"]))
        return payload

    total_converted, total_unconvertible = convert_bucket(
        total_bucket, base_currency=base_currency, exchange_rates=exchange_rates
    )
    return {
        "baseCurrency": base_currency,
        "total": str(total_converted),
        "byCurrency": _bucket_payload(total_bucket),
        "unconvertible": str(total_unconvertible),
        "pricedCount": priced_count,
        "unpricedCount": unpriced_count,
        "scopes": {
            "vaults": scope_payload(vault_buckets, vault_titles),
            "collections": scope_payload(collection_buckets, collection_titles),
        },
    }


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def _upsert_snapshot(
    conn,
    *,
    user_id: UUID | str,
    scope_type: str,
    scope_id: str | None,
    captured_on: date,
    base_currency: str,
    total_value: str,
    by_currency: dict[str, str],
    priced_count: int,
    unpriced_count: int,
) -> None:
    conflict = (
        "(user_id, captured_on) WHERE scope_id IS NULL"
        if scope_id is None
        else "(user_id, scope_type, scope_id, captured_on) WHERE scope_id IS NOT NULL"
    )
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO collection_value_snapshots (
                user_id, scope_type, scope_id, captured_on, base_currency,
                total_value, by_currency, priced_count, unpriced_count, captured_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT {conflict} DO UPDATE SET
                scope_type=EXCLUDED.scope_type,
                base_currency=EXCLUDED.base_currency,
                total_value=EXCLUDED.total_value,
                by_currency=EXCLUDED.by_currency,
                priced_count=EXCLUDED.priced_count,
                unpriced_count=EXCLUDED.unpriced_count,
                captured_at=EXCLUDED.captured_at
            """,
            (
                user_id,
                scope_type,
                scope_id,
                captured_on,
                base_currency,
                total_value,
                Jsonb(by_currency),
                priced_count,
                unpriced_count,
            ),
        )


def record_collection_value_snapshot(
    conn,
    *,
    user_id: UUID | str | None,
    movie_where: str,
    movie_params: list[Any],
    container_where: str,
    container_params: list[Any],
    captured_on: date | None = None,
) -> dict[str, Any] | None:
    """Capture today's value for every scope. Idempotent within a calendar day.

    Callers treat this as best effort: a snapshot that cannot be written must
    never fail the edit that triggered it.
    """
    if not user_id or not table_exists(conn, "collection_value_snapshots"):
        return None
    rates = price_display_exchange_rates()
    summary = compute_collection_value(
        conn,
        movie_where=movie_where,
        movie_params=movie_params,
        container_where=container_where,
        container_params=container_params,
        base_currency=SNAPSHOT_BASE_CURRENCY,
        exchange_rates=dict(rates.get("exchangeRates") or {}),
    )
    day = captured_on or date.today()
    _upsert_snapshot(
        conn,
        user_id=user_id,
        scope_type="total",
        scope_id=None,
        captured_on=day,
        base_currency=summary["baseCurrency"],
        total_value=summary["total"],
        by_currency=summary["byCurrency"],
        priced_count=summary["pricedCount"],
        unpriced_count=summary["unpricedCount"],
    )
    for scope_type, key in (("vault", "vaults"), ("collection", "collections")):
        for scope in summary["scopes"][key]:
            _upsert_snapshot(
                conn,
                user_id=user_id,
                scope_type=scope_type,
                scope_id=scope["id"],
                captured_on=day,
                base_currency=summary["baseCurrency"],
                total_value=scope["total"],
                by_currency=scope["byCurrency"],
                priced_count=0,
                unpriced_count=0,
            )
    return summary


def collection_value_history(
    conn,
    *,
    user_id: UUID | str | None,
    scope_type: str = "total",
    scope_id: str | None = None,
    since: date | None = None,
    until: date | None = None,
    limit: int = 730,
) -> list[dict[str, Any]]:
    """The stored series for one scope, oldest first — the chart's data source."""
    if not user_id or not table_exists(conn, "collection_value_snapshots"):
        return []
    clauses = ["user_id=%s", "scope_type=%s"]
    params: list[Any] = [user_id, scope_type]
    if scope_id:
        clauses.append("scope_id=%s")
        params.append(scope_id)
    else:
        clauses.append("scope_id IS NULL")
    if since:
        clauses.append("captured_on >= %s")
        params.append(since)
    if until:
        clauses.append("captured_on <= %s")
        params.append(until)
    params.append(limit)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT captured_on, base_currency, total_value, by_currency,
                   priced_count, unpriced_count, captured_at
            FROM collection_value_snapshots
            WHERE {' AND '.join(clauses)}
            ORDER BY captured_on ASC
            LIMIT %s
            """,
            params,
        )
        rows = cur.fetchall() or []
    return [
        {
            "capturedOn": row.get("captured_on").isoformat() if row.get("captured_on") else None,
            "baseCurrency": row.get("base_currency"),
            "total": str(_decimal(row.get("total_value")).quantize(Decimal("0.01"))),
            "byCurrency": row.get("by_currency") or {},
            "pricedCount": int(row.get("priced_count") or 0),
            "unpricedCount": int(row.get("unpriced_count") or 0),
        }
        for row in rows
    ]
