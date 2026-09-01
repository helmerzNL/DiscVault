"""The statistics endpoint valued the whole collection twice per request.

`/api/next/stats/personal` calls `compute_collection_value` for its own
response, and then `capture_collection_value_snapshot` so a day on which the
user only *looked* still gets a point on the value chart. That second call
recomputed the identical figure: the same scan of every movie, container and
price, and another exchange-rate lookup - which on a deployment that cannot
reach the rates provider is a second network timeout inside the same request.

Doubling the most expensive thing the endpoint does is what pushed it past the
MCP server's fifteen-second read timeout, so `get_top_actors` and
`get_top_directors` reported

    HTTPConnectionPool(host='next-api', port=5000): Read timed out

on the calls that followed the first failure. The snapshot is still captured -
the chart keeps its point - it is just written from the summary already in hand.

The guard matters as much as the reuse: a summary computed in some other base
currency must not be stored as if it were in the snapshot's, because nothing
downstream would ever reveal the mix-up. That case recomputes.
"""

import os
import sys
import unittest
from datetime import date


backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_root not in sys.path:
    sys.path.insert(0, backend_root)

import next_collection_value  # noqa: E402
from next_collection_value import SNAPSHOT_BASE_CURRENCY  # noqa: E402


def _summary(base_currency=SNAPSHOT_BASE_CURRENCY, total="42.00"):
    return {
        "baseCurrency": base_currency,
        "total": total,
        "byCurrency": {"EUR": total},
        "unconvertible": "0.00",
        "pricedCount": 3,
        "unpricedCount": 1,
        "scopes": {"vaults": [], "collections": []},
    }


class SnapshotSummaryReuseTests(unittest.TestCase):
    def setUp(self):
        self.computed = 0
        self.rate_lookups = 0
        self.written = []

        def fake_compute(conn, **kwargs):
            self.computed += 1
            return _summary(total="99.00")

        def fake_rates(*args, **kwargs):
            self.rate_lookups += 1
            return {"exchangeRates": {"EUR": 1.0}}

        def fake_upsert(conn, **kwargs):
            self.written.append(kwargs)

        patches = {
            "table_exists": lambda conn, table: True,
            "compute_collection_value": fake_compute,
            "price_display_exchange_rates": fake_rates,
            "_upsert_snapshot": fake_upsert,
        }
        self.originals = {name: getattr(next_collection_value, name) for name in patches}
        for name, value in patches.items():
            setattr(next_collection_value, name, value)
        self.addCleanup(self._restore)

    def _restore(self):
        for name, value in self.originals.items():
            setattr(next_collection_value, name, value)

    def _record(self, summary=None):
        return next_collection_value.record_collection_value_snapshot(
            object(),
            user_id="11111111-1111-1111-1111-111111111111",
            movie_where="TRUE",
            movie_params=[],
            container_where="TRUE",
            container_params=[],
            captured_on=date(2026, 9, 1),
            summary=summary,
        )

    def test_a_supplied_summary_is_written_without_recomputing(self):
        handed_over = _summary(total="42.00")

        result = self._record(handed_over)

        self.assertEqual(self.computed, 0, "the valuation was computed a second time")
        self.assertEqual(self.rate_lookups, 0, "the rates were looked up a second time")
        self.assertEqual(result, handed_over)
        self.assertEqual(len(self.written), 1)
        self.assertEqual(self.written[0]["total_value"], "42.00")

    def test_without_a_summary_it_still_computes_one(self):
        # Every other caller - the write paths - has nothing in hand and must
        # keep working exactly as before.
        result = self._record(None)

        self.assertEqual(self.computed, 1)
        self.assertEqual(result["total"], "99.00")
        self.assertEqual(self.written[0]["total_value"], "99.00")

    def test_a_summary_in_another_base_currency_is_recomputed_not_stored(self):
        # Storing USD figures under the snapshot's EUR base would corrupt the
        # value history in a way no later reader could detect.
        self._record(_summary(base_currency="USD", total="42.00"))

        self.assertEqual(self.computed, 1)
        self.assertEqual(self.written[0]["base_currency"], SNAPSHOT_BASE_CURRENCY)
        self.assertEqual(self.written[0]["total_value"], "99.00")

    def test_a_malformed_summary_is_recomputed_rather_than_trusted(self):
        for bogus in ({}, {"baseCurrency": SNAPSHOT_BASE_CURRENCY}, "not a summary", []):
            with self.subTest(summary=bogus):
                self.computed = 0
                self.written.clear()
                self._record(bogus)
                self.assertEqual(self.computed, 1)


if __name__ == "__main__":
    unittest.main()
