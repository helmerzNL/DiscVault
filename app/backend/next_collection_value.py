"""Daily capture of what each user's collection is worth.

`movies.estimated_value` is one current number per disc. Editing it overwrites
the previous value and leaves no trace, so the history of a collection's worth
cannot be reconstructed after the fact — it can only be recorded as it happens.
That is what this module does, and it is why the chart it feeds necessarily
starts empty on the day it ships.

Three decisions worth knowing before reading the code:

**Scope is ownership, not visibility.** The rest of the statistics surface is
scoped with `visible_movie_where_sql`, which answers "what may this user look
at". A value chart asks something else — what is *mine* worth — and the two
diverge as soon as an instance has more than one user or a shared group. Owning
is also stable: a permission change should not rewrite last month's total.
`movie_count` here therefore counts owned discs and can legitimately differ from
`totalMovies` elsewhere on the same page.

**A total is a claim about completeness**, so the rows it could not include are
recorded next to it rather than dropped. `estimated_value_currency` is nullable
by deliberate design — an amount whose currency was never recorded is a real
state, and assuming EUR would invent a fact (see App-Guidance
`estimated-value-currency.md` §1). Such an amount cannot be converted, so it is
counted in `unconvertible_count` and left out of the sum. A chart that silently
swallowed it would draw a line that means less than it appears to.

**The currency is stored per row.** Snapshots only compare within one currency,
and the user's display preference can change. Reading yesterday's number through
today's preference would retroactively change what it said.
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

try:  # pragma: no cover - import shape mirrors the rest of the backend
    from .next_preferences import PRICE_DISPLAY_SUPPORTED_CURRENCIES
    from .next_preferences import app_effective_preferences
    from .next_preferences import price_display_exchange_rates
except ImportError:  # pragma: no cover
    from next_preferences import PRICE_DISPLAY_SUPPORTED_CURRENCIES
    from next_preferences import app_effective_preferences
    from next_preferences import price_display_exchange_rates


COLLECTION_VALUE_JOB_TYPE = "collection_value.snapshot"

#: Currency a snapshot falls back to when the user expressed no preference.
#: Not a guess about their money — it is the base the exchange rates are quoted
#: in, so it is the one currency that needs no conversion at all.
DEFAULT_SNAPSHOT_CURRENCY = "EUR"


def _table_exists(conn, name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) AS reg", (f"public.{name}",))
        row = cur.fetchone()
    return bool(row and row.get("reg"))


def snapshot_interval_hours() -> float:
    """How stale a snapshot may get before another is enqueued."""
    try:
        hours = float(os.environ.get("DISCVAULT_VALUE_SNAPSHOT_INTERVAL_HOURS", "24"))
    except ValueError:
        return 24.0
    return min(max(hours, 1.0), 24.0 * 30)


def convert_amount(
    amount: Decimal,
    *,
    from_currency: str,
    to_currency: str,
    rates: dict[str, float],
) -> Decimal | None:
    """Convert between two currencies, or return ``None`` when it cannot be done.

    Returning ``None`` rather than the unconverted amount is the point: a number
    in the wrong currency added to a total is worse than a number left out,
    because the total then looks complete and is wrong.
    """
    source = (from_currency or "").strip().upper()
    target = (to_currency or "").strip().upper()
    if not source or not target:
        return None
    if source == target:
        return amount
    try:
        source_rate = float(rates[source])
        target_rate = float(rates[target])
    except (KeyError, TypeError, ValueError):
        return None
    if source_rate <= 0:
        return None
    # Rates are quoted against EUR, so cross-convert through the base.
    return (amount / Decimal(str(source_rate))) * Decimal(str(target_rate))


def preferred_snapshot_currency(conn, user_id) -> str:
    preferences = app_effective_preferences(conn, user_id) or {}
    preferred = str(preferences.get("preferred_price_currency") or "").strip().upper()
    if preferred in PRICE_DISPLAY_SUPPORTED_CURRENCIES:
        return preferred
    return DEFAULT_SNAPSHOT_CURRENCY


def compute_collection_value(conn, user_id, *, currency: str, rates: dict[str, float]) -> dict[str, Any]:
    """Total the discs ``user_id`` owns, expressed in ``currency``."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT estimated_value, estimated_value_currency
            FROM movies
            WHERE owner_id = %s AND deleted_at IS NULL
            """,
            (user_id,),
        )
        rows = cur.fetchall()

    total = Decimal("0")
    valued = 0
    unpriced = 0
    unconvertible = 0
    for row in rows:
        raw_amount = row.get("estimated_value")
        if raw_amount is None:
            unpriced += 1
            continue
        try:
            amount = Decimal(str(raw_amount))
        except (InvalidOperation, TypeError, ValueError):
            unpriced += 1
            continue
        # No recorded currency means exactly that. It is not EUR, and it is not
        # the display preference either — both would be inventions.
        row_currency = (row.get("estimated_value_currency") or "").strip().upper()
        if not row_currency:
            unconvertible += 1
            continue
        converted = convert_amount(amount, from_currency=row_currency, to_currency=currency, rates=rates)
        if converted is None:
            unconvertible += 1
            continue
        total += converted
        valued += 1

    return {
        "currency": currency,
        "totalValue": total.quantize(Decimal("0.01")),
        "valuedCount": valued,
        "movieCount": len(rows),
        "unpricedCount": unpriced,
        "unconvertibleCount": unconvertible,
    }


def capture_collection_value_snapshots(conn, *, captured_on: date | None = None) -> dict[str, Any]:
    """Record one snapshot per user for ``captured_on`` (today by default).

    Idempotent: the unique index on (user_id, captured_on) turns a second run on
    the same day into an overwrite, so a restarted worker cannot put two points
    on one date.
    """
    if not _table_exists(conn, "collection_value_snapshots") or not _table_exists(conn, "movies"):
        return {"status": "skipped", "reason": "tables_missing", "captured": 0}
    captured_on = captured_on or date.today()

    # Fetched once for the whole run: it is a network call, and every user in
    # the same run should be priced against the same rates.
    rates = dict((price_display_exchange_rates().get("exchangeRates") or {}))
    if not rates:
        return {"status": "skipped", "reason": "no_exchange_rates", "captured": 0}

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT owner_id AS id
            FROM movies
            WHERE owner_id IS NOT NULL AND deleted_at IS NULL
            """
        )
        owner_ids = [row["id"] for row in cur.fetchall()]

    captured = 0
    for owner_id in owner_ids:
        totals = compute_collection_value(
            conn, owner_id, currency=preferred_snapshot_currency(conn, owner_id), rates=rates
        )
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO collection_value_snapshots (
                    user_id, captured_on, currency, total_value,
                    valued_count, movie_count, unpriced_count, unconvertible_count
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, captured_on) DO UPDATE SET
                    currency=EXCLUDED.currency,
                    total_value=EXCLUDED.total_value,
                    valued_count=EXCLUDED.valued_count,
                    movie_count=EXCLUDED.movie_count,
                    unpriced_count=EXCLUDED.unpriced_count,
                    unconvertible_count=EXCLUDED.unconvertible_count
                """,
                (
                    owner_id,
                    captured_on,
                    totals["currency"],
                    totals["totalValue"],
                    totals["valuedCount"],
                    totals["movieCount"],
                    totals["unpricedCount"],
                    totals["unconvertibleCount"],
                ),
            )
        captured += 1

    return {"status": "ok", "captured": captured, "capturedOn": captured_on.isoformat()}


def collection_value_trend(conn, user_id, *, days: int = 365) -> dict[str, Any]:
    """The recorded snapshots for one user, oldest first.

    Returns an empty series rather than an error when nothing has been captured
    yet — which is the normal state on the day this ships, and the UI has to say
    so rather than draw a flat line through one point.
    """
    empty = {"points": [], "currency": None, "hasHistory": False}
    if not _table_exists(conn, "collection_value_snapshots"):
        return empty
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT captured_on, currency, total_value, valued_count,
                   movie_count, unpriced_count, unconvertible_count
            FROM collection_value_snapshots
            WHERE user_id = %s
              AND captured_on >= (CURRENT_DATE - %s::int)
            ORDER BY captured_on ASC
            """,
            (user_id, max(1, min(int(days or 365), 3650))),
        )
        rows = cur.fetchall()
    if not rows:
        return empty

    points = []
    for row in rows:
        captured = row.get("captured_on")
        points.append(
            {
                "at": captured.isoformat() if hasattr(captured, "isoformat") else str(captured),
                "value": float(row.get("total_value") or 0),
                "valuedCount": int(row.get("valued_count") or 0),
                "movieCount": int(row.get("movie_count") or 0),
                "unpricedCount": int(row.get("unpriced_count") or 0),
                "unconvertibleCount": int(row.get("unconvertible_count") or 0),
            }
        )

    # The series is only comparable within one currency. If the user changed
    # their preference partway through, older points are quoted in the old one;
    # say so rather than drawing a step that looks like a value change.
    currencies = {str(row.get("currency") or "").upper() for row in rows}
    latest = points[-1]
    first = points[0]
    return {
        "points": points,
        "currency": str(rows[-1].get("currency") or "").upper() or None,
        "hasHistory": len(points) > 1,
        "mixedCurrency": len(currencies) > 1,
        "currentValue": latest["value"],
        "changeFromStart": round(latest["value"] - first["value"], 2),
        "firstCapturedAt": first["at"],
        "lastCapturedAt": latest["at"],
        "unpricedCount": latest["unpricedCount"],
        "unconvertibleCount": latest["unconvertibleCount"],
    }


def purchase_trend(conn, user_id, *, currency: str, rates: dict[str, float]) -> dict[str, Any]:
    """Cumulative spend and collection growth, per month, from purchase history.

    Unlike the value trend this needs no capture: `purchase_date` and
    `purchase_price` have been on `movies` since the core schema, so the series
    runs back to the first disc the user dated. It answers a different question —
    what a collection cost to build, not what it is worth now — and the two are
    shown side by side rather than merged, because a disc bought for 5 and worth
    50 is a fact about both and an average of neither.
    """
    empty = {"points": [], "currency": currency, "hasHistory": False}
    if not _table_exists(conn, "movies"):
        return empty
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT purchase_date, purchase_price, estimated_value_currency
            FROM movies
            WHERE owner_id = %s
              AND deleted_at IS NULL
              AND purchase_date IS NOT NULL
            ORDER BY purchase_date ASC
            """,
            (user_id,),
        )
        rows = cur.fetchall()
    if not rows:
        return empty

    monthly: dict[str, dict[str, Any]] = {}
    unpriced = 0
    unconvertible = 0
    for row in rows:
        purchased = row.get("purchase_date")
        if purchased is None:
            continue
        month = f"{purchased.year:04d}-{purchased.month:02d}"
        bucket = monthly.setdefault(month, {"at": f"{month}-01", "spend": Decimal("0"), "count": 0})
        bucket["count"] += 1
        raw_amount = row.get("purchase_price")
        if raw_amount is None:
            unpriced += 1
            continue
        try:
            amount = Decimal(str(raw_amount))
        except (InvalidOperation, TypeError, ValueError):
            unpriced += 1
            continue
        # `purchase_price` predates the currency column and has none of its own.
        # The per-movie currency is the closest recorded fact; without one the
        # amount is not convertible and is counted, not guessed.
        row_currency = (row.get("estimated_value_currency") or "").strip().upper()
        converted = (
            amount
            if row_currency == currency.upper()
            else convert_amount(amount, from_currency=row_currency, to_currency=currency, rates=rates)
            if row_currency
            else None
        )
        if converted is None:
            unconvertible += 1
            continue
        bucket["spend"] += converted

    points = []
    running_spend = Decimal("0")
    running_count = 0
    for month in sorted(monthly):
        bucket = monthly[month]
        running_spend += bucket["spend"]
        running_count += bucket["count"]
        points.append(
            {
                "at": bucket["at"],
                "spend": float(bucket["spend"].quantize(Decimal("0.01"))),
                "cumulativeSpend": float(running_spend.quantize(Decimal("0.01"))),
                "count": bucket["count"],
                "cumulativeCount": running_count,
            }
        )

    return {
        "points": points,
        "currency": currency,
        "hasHistory": len(points) > 1,
        "totalSpend": float(running_spend.quantize(Decimal("0.01"))),
        "totalCount": running_count,
        "unpricedCount": unpriced,
        "unconvertibleCount": unconvertible,
        "firstPurchaseAt": points[0]["at"] if points else None,
        "lastPurchaseAt": points[-1]["at"] if points else None,
    }
