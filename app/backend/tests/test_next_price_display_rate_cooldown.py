"""Why a *later* call to the statistics endpoint timed out after fifteen seconds.

`/api/next/stats/personal` looks up exchange rates over the network, from
inside the request, with a database transaction open. On a deployment that can
reach `api.frankfurter.app` this is a cache hit and costs nothing. On one that
cannot - no egress, a blocking firewall, DNS that black-holes - it costs the
full HTTP timeout, and the failure was **not remembered**: the `except` branch
returned the fallback rates without writing them to the cache, so the very next
request paid the same wait again.

Worse, the endpoint paid it twice per request. It looks the rates up once for
its own response and then captured a value snapshot, which recomputed the whole
valuation and looked them up a second time. At the old ten-second timeout that
is twenty seconds of waiting for a request whose caller - the MCP server - gives
up at fifteen:

    HTTPConnectionPool(host='next-api', port=5000): Read timed out

Hence the shape of the report: the first call to `get_top_actors` returns a 500
from a different bug, and every call after it hangs. Two faults, one symptom.

Three things are pinned here, one per contributing cause:

1. a failed lookup is cached for a cool-down, so the wait is paid once per
   cool-down rather than once per request;
2. previously-fetched rates are preferred over the built-in fallback when the
   provider goes away - a cool-down must not throw away good data;
3. the per-request budget is small enough that two of them cannot reach a
   caller's timeout, which is what makes the doubled lookup survivable at all.

The doubled lookup itself is removed separately, by handing the computed
valuation to `record_collection_value_snapshot`; see `summary` there.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone


backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_root not in sys.path:
    sys.path.insert(0, backend_root)

import next_preferences  # noqa: E402


class PriceDisplayRateCooldownTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.original_get = next_preferences.requests.get
        self.original_cache = dict(next_preferences._PRICE_DISPLAY_RATE_CACHE)
        next_preferences._PRICE_DISPLAY_RATE_CACHE.clear()
        next_preferences._PRICE_DISPLAY_RATE_CACHE.update({"expires_at": None, "payload": None})
        self.addCleanup(self._restore)

    def _restore(self):
        next_preferences.requests.get = self.original_get
        next_preferences._PRICE_DISPLAY_RATE_CACHE.clear()
        next_preferences._PRICE_DISPLAY_RATE_CACHE.update(self.original_cache)

    def _fail_every_lookup(self):
        def failing_get(url, **kwargs):
            self.calls.append(kwargs)
            raise OSError("no route to host")

        next_preferences.requests.get = failing_get

    def test_a_failed_lookup_is_not_retried_on_the_very_next_request(self):
        # The regression: without a cached failure, every request repeated the
        # network wait. Ten requests meant ten timeouts.
        self._fail_every_lookup()
        now = datetime.now(timezone.utc)

        first = next_preferences.price_display_exchange_rates(now)
        second = next_preferences.price_display_exchange_rates(now + timedelta(seconds=1))
        third = next_preferences.price_display_exchange_rates(now + timedelta(minutes=1))

        self.assertEqual(len(self.calls), 1, "the failure was looked up again")
        self.assertEqual(first["source"], "fallback")
        self.assertEqual(second, first)
        self.assertEqual(third, first)

    def test_the_cool_down_expires_so_the_provider_is_tried_again(self):
        # A cool-down that never lifts would leave a deployment on fallback
        # rates forever after one bad minute.
        self._fail_every_lookup()
        now = datetime.now(timezone.utc)

        next_preferences.price_display_exchange_rates(now)
        after = now + next_preferences.PRICE_DISPLAY_RATE_RETRY_AFTER + timedelta(seconds=1)
        next_preferences.price_display_exchange_rates(after)

        self.assertEqual(len(self.calls), 2)

    def test_rates_already_fetched_survive_the_provider_going_away(self):
        # Real rates that are a few hours stale beat the hardcoded fallback, so
        # the cool-down must re-cache what was already known rather than
        # replacing it.
        known = {
            "base": "EUR",
            "exchangeRates": {"EUR": 1.0, "USD": 1.11},
            "updatedAt": "2026-08-31",
            "source": "frankfurter",
        }
        now = datetime.now(timezone.utc)
        next_preferences._PRICE_DISPLAY_RATE_CACHE["payload"] = known
        next_preferences._PRICE_DISPLAY_RATE_CACHE["expires_at"] = now - timedelta(seconds=1)
        self._fail_every_lookup()

        result = next_preferences.price_display_exchange_rates(now)

        self.assertEqual(result, known)
        self.assertEqual(
            next_preferences._PRICE_DISPLAY_RATE_CACHE["expires_at"],
            now + next_preferences.PRICE_DISPLAY_RATE_RETRY_AFTER,
        )

    def test_the_lookup_timeout_is_short_enough_to_be_paid_inside_a_request(self):
        # The MCP server reads with a 15s timeout. This runs inside the request,
        # holding a database transaction, so the budget has to leave room for
        # the rest of the endpoint even if it were paid more than once.
        self._fail_every_lookup()
        next_preferences.price_display_exchange_rates()

        self.assertEqual(len(self.calls), 1)
        self.assertLessEqual(self.calls[0].get("timeout"), 5)
        self.assertEqual(
            self.calls[0].get("timeout"), next_preferences.PRICE_DISPLAY_RATE_TIMEOUT_SECONDS
        )


if __name__ == "__main__":
    unittest.main()
