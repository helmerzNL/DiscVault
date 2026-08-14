"""Guards for the crawler and AI-scraper exclusion.

Two self-hosted DiscVault instances were findable in Google. Nothing had to be
misconfigured for that: the SPA shell and the deep-link pages answer ``200`` to
an anonymous request, so a crawler that reaches the host has a document to index
whether or not the collection behind it is gated.

The three layers each cover what the others cannot, and each one has a way to
regress silently:

  * ``robots.txt`` can start answering something other than ``Disallow: /``, or
    stop being reachable at all — and a crawler that gets a ``403`` for
    ``robots.txt`` concludes there are *no* restrictions;
  * the ``noindex`` header can stop being applied to some responses, which is
    exactly the layer that clears an instance already in an index;
  * the user-agent refusal can start matching a health check (reporting a live
    instance as down) or stop matching the crawlers it names.
"""

import os
import sys
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import next_crawlers  # noqa: E402

try:
    import next_push
except ModuleNotFoundError as exc:  # pragma: no cover - depends on test environment
    if exc.name not in {"flask", "psycopg", "jwt"}:
        raise
    next_push = None


class RobotsTxtTests(unittest.TestCase):
    def test_wildcard_group_disallows_everything(self):
        body = next_crawlers.robots_txt()
        self.assertIn("User-agent: *\nDisallow: /", body)

    def test_every_blocked_agent_has_its_own_group(self):
        # A crawler that finds a group naming its own token uses only that group
        # and ignores the wildcard entirely, so the named groups are what keeps
        # a later relaxation of `*` from re-admitting every AI scraper with it.
        body = next_crawlers.robots_txt()
        for token in next_crawlers.BLOCKED_USER_AGENTS:
            self.assertIn(f"User-agent: {token}\nDisallow: /", body, token)

    def test_the_crawlers_that_prompted_this_are_named(self):
        # Search engines that indexed the two instances, plus the AI crawlers
        # this is meant to keep out of a training corpus. Named individually
        # because "the list is non-empty" would pass with any of them dropped.
        body = next_crawlers.robots_txt()
        for token in (
            "Googlebot",
            "Google-Extended",
            "bingbot",
            "DuckDuckBot",
            "Applebot",
            "Applebot-Extended",
            "GPTBot",
            "ChatGPT-User",
            "OAI-SearchBot",
            "ClaudeBot",
            "anthropic-ai",
            "PerplexityBot",
            "CCBot",
            "Bytespider",
            "Amazonbot",
            "Meta-ExternalAgent",
            "cohere-ai",
            "YouBot",
        ):
            self.assertIn(f"User-agent: {token}\n", body, token)

    def test_no_sitemap_is_advertised(self):
        self.assertNotIn("Sitemap", next_crawlers.robots_txt())


class UserAgentMatchingTests(unittest.TestCase):
    def test_real_crawler_user_agent_strings_match(self):
        # Substring matching is the point: the token arrives inside a longer
        # string, usually with a version and a URL appended.
        for user_agent in (
            "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
            "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.2; +https://openai.com/gptbot)",
            "Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
            "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
            "Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)",
            "Mozilla/5.0 (compatible) meta-externalagent/1.1 (+https://developers.facebook.com/docs/sharing/webmasters/crawler)",
            "CCBot/2.0 (https://commoncrawl.org/faq/)",
            "Mozilla/5.0 (Linux; Android 5.0) AppleWebKit/537.36 (KHTML, like Gecko) Mobile Safari/537.36 (compatible; Bytespider; spider-feedback@bytedance.com)",
        ):
            self.assertTrue(next_crawlers.is_blocked_user_agent(user_agent), user_agent)

    def test_browsers_and_the_apps_own_clients_do_not_match(self):
        # A false positive here locks a real user out of their own collection,
        # which is worse than an indexed page.
        for user_agent in (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
            "DiscVault/26.9 (iOS 17.4; iPhone15,2)",
            "python-requests/2.32.3",
            "Python-urllib/3.12",
            "curl/8.6.0",
            "Uptime-Kuma/1.23.13",
            "Go-http-client/2.0",
            "",
            None,
        ):
            self.assertFalse(next_crawlers.is_blocked_user_agent(user_agent), repr(user_agent))


class BlockingSwitchTests(unittest.TestCase):
    def setUp(self):
        self._original = os.environ.get("DISCVAULT_BLOCK_CRAWLERS")
        self.addCleanup(self._restore)

    def _restore(self):
        if self._original is None:
            os.environ.pop("DISCVAULT_BLOCK_CRAWLERS", None)
        else:
            os.environ["DISCVAULT_BLOCK_CRAWLERS"] = self._original

    def test_blocking_is_on_when_nothing_is_configured(self):
        # An instance is private unless its operator says otherwise: nobody
        # switched anything on to end up in Google.
        os.environ.pop("DISCVAULT_BLOCK_CRAWLERS", None)
        self.assertTrue(next_crawlers.crawler_blocking_enabled())

    def test_operator_can_switch_it_off(self):
        for value in ("false", "0", "no", "off", "FALSE"):
            os.environ["DISCVAULT_BLOCK_CRAWLERS"] = value
            self.assertFalse(next_crawlers.crawler_blocking_enabled(), value)

    def test_anything_else_leaves_it_on(self):
        for value in ("true", "1", "yes", "", "  ", "nonsense"):
            os.environ["DISCVAULT_BLOCK_CRAWLERS"] = value
            self.assertTrue(next_crawlers.crawler_blocking_enabled(), repr(value))


class ExemptPathTests(unittest.TestCase):
    def test_robots_txt_is_never_refused(self):
        # A 4xx on robots.txt is read as "no restrictions at all", which would
        # invert the whole point of serving it.
        self.assertIn("/robots.txt", next_crawlers.CRAWLER_BLOCK_EXEMPT_PATHS)

    def test_health_checks_are_never_refused(self):
        # Container health checks and uptime monitors are not crawler surfaces,
        # and some of them send a user agent containing a crawler token.
        self.assertIn("/api/next/health", next_crawlers.CRAWLER_BLOCK_EXEMPT_PATHS)


class RobotsHeaderTests(unittest.TestCase):
    def test_the_header_forbids_indexing_and_the_derived_copies(self):
        # `noindex` alone leaves the cached copy and the result-page snippet,
        # which outlive the crawl.
        for directive in ("noindex", "nofollow", "noarchive", "nosnippet", "noimageindex"):
            self.assertIn(directive, next_crawlers.X_ROBOTS_TAG, directive)

    def test_the_meta_tag_carries_the_same_directives(self):
        self.assertIn(next_crawlers.X_ROBOTS_TAG, next_crawlers.ROBOTS_META_TAG)
        self.assertIn('name="robots"', next_crawlers.ROBOTS_META_TAG)


@unittest.skipIf(next_push is None, "Flask is not installed in this test environment")
class RenderedHeadTests(unittest.TestCase):
    def test_every_rendered_document_declares_noindex(self):
        # One shared <head> feeds the SPA shell, the collection dashboard, both
        # detail views and the migration dashboard, so asserting on it covers
        # all of them. The header covers the two documents that do not use it.
        self.assertIn(next_crawlers.ROBOTS_META_TAG.strip(), next_push.pwa_head_tags())


if __name__ == "__main__":
    unittest.main()
