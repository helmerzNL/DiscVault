"""Search-engine and AI-crawler exclusion for the DiscVault PWA.

A DiscVault instance is a private collection manager, not a public website.
Nothing in it is meant to be findable: the library, the shelves, the borrow
history and the people pages all describe one household. Two self-hosted
instances nevertheless turned up in Google — which is what happens as soon as an
instance is reachable over HTTPS and *anything* links to it or resolves through
a public DNS record. No sign-in wall is involved: the SPA shell, the deep-link
pages and the public app routes all answer ``200`` to an anonymous request, so a
crawler has a document to index even when the collection data behind it is
gated.

Three layers, because each one covers what the others cannot:

1. ``/robots.txt`` — the polite layer. Every well-behaved crawler reads it
   before fetching anything, and the disallow costs one request. It is not a
   removal tool: a URL already in an index can survive a ``Disallow``, because
   the crawler stops fetching and therefore never sees a ``noindex``.
2. ``X-Robots-Tag`` on every response plus ``<meta name="robots">`` in every
   rendered document — the removal layer, for anything that gets fetched anyway
   (a crawler that ignores ``robots.txt``, a link shared into an indexer, an
   instance that was already indexed before this shipped).
3. A hard ``403`` for known crawler and AI-scraper user agents — the layer that
   does not depend on the crawler's cooperation, and the one that actually
   clears an existing index entry, since a URL answering ``4xx`` is dropped over
   time. Gated by ``DISCVAULT_BLOCK_CRAWLERS`` (default on).

None of this is a security control: a user agent is self-reported, so a scraper
that wants in only has to claim to be a browser. The layers keep DiscVault out
of search results and out of training corpora — an instance that must not be
*reachable* still has to be kept off the public internet or behind
authentication.
"""

from __future__ import annotations

import os
import re

from flask import Flask, Response, request


# Crawlers that index for a search engine. Blocking these is what keeps an
# instance out of search results.
SEARCH_ENGINE_USER_AGENTS: tuple[str, ...] = (
    "AdsBot-Google",
    "AdsBot-Google-Mobile",
    "APIs-Google",
    "Applebot",
    "Baiduspider",
    "bingbot",
    "BingPreview",
    "coccocbot",
    "DuckDuckBot",
    "DuckDuckGo-Favicons-Bot",
    "Exabot",
    "Gigabot",
    "Googlebot",
    "Googlebot-Image",
    "Googlebot-News",
    "Googlebot-Video",
    "Mediapartners-Google",
    "MojeekBot",
    "msnbot",
    "Naverbot",
    "PetalBot",
    "Qwantbot",
    "Qwantify",
    "Seekport",
    "SeznamBot",
    "Slurp",
    "Sogou web spider",
    "Storebot-Google",
    "Teoma",
    "Yandex",
    "YandexBot",
    "YandexImages",
    "Yeti",
)

# Crawlers that collect text for a large language model — for training, for a
# retrieval index, or to answer a single user's prompt live. Several of these
# ignore robots.txt outright, which is why layer 3 exists.
AI_CRAWLER_USER_AGENTS: tuple[str, ...] = (
    "AI2Bot",
    "Ai2Bot-Dolma",
    "aiHitBot",
    "Amazonbot",
    "Andibot",
    "anthropic-ai",
    "Applebot-Extended",
    "Brightbot",
    "Bytespider",
    "CCBot",
    "ChatGPT-User",
    "Claude-SearchBot",
    "Claude-User",
    "Claude-Web",
    "ClaudeBot",
    "cohere-ai",
    "cohere-training-data-crawler",
    "Cotoyogi",
    "Crawlspace",
    "Diffbot",
    "DeepSeekBot",
    "FacebookBot",
    "Factset_spyderbot",
    "FirecrawlAgent",
    "FriendlyCrawler",
    "Google-CloudVertexBot",
    "Google-Extended",
    "GoogleOther",
    "GPTBot",
    "iaskspider/2.0",
    "ICC-Crawler",
    "ImagesiftBot",
    "img2dataset",
    "ISSCyberRiskCrawler",
    "Kangaroo Bot",
    "meta-externalagent",
    "Meta-ExternalAgent",
    "meta-externalfetcher",
    "Meta-ExternalFetcher",
    "MistralAI-User",
    "MyCentralAIScraperBot",
    "NovaAct",
    "OAI-SearchBot",
    "omgili",
    "omgilibot",
    "PanguBot",
    "Perplexity-User",
    "PerplexityBot",
    "PhindBot",
    "Poseidon Research Crawler",
    "QualifiedBot",
    "SBIntuitionsBot",
    "Scrapy",
    "SemrushBot-OCOB",
    "Sidetrade indexer bot",
    "TikTokSpider",
    "Timpibot",
    "VelenPublicWebCrawler",
    "Webzio-Extended",
    "wpbot",
    "YouBot",
)

# SEO and lead-generation crawlers. Not search engines and not AI, but they
# fetch every page they can reach and republish what they find.
SEO_CRAWLER_USER_AGENTS: tuple[str, ...] = (
    "AhrefsBot",
    "Barkrowler",
    "BLEXBot",
    "DataForSeoBot",
    "dotbot",
    "Linguee Bot",
    "MJ12bot",
    "SemrushBot",
    "SeekportBot",
    "serpstatbot",
    "SiteAuditBot",
    "ZoominfoBot",
)

# Link-preview fetchers. A preview card for a private collection page is the
# same disclosure as an index entry, and these are the agents that build one
# when a URL is pasted into a chat.
LINK_PREVIEW_USER_AGENTS: tuple[str, ...] = (
    "Discordbot",
    "facebookexternalhit",
    "Facebot",
    "Slackbot",
    "Slackbot-LinkExpanding",
    "TelegramBot",
    "Twitterbot",
    "WhatsApp",
)

BLOCKED_USER_AGENTS: tuple[str, ...] = tuple(
    sorted(
        {
            *SEARCH_ENGINE_USER_AGENTS,
            *AI_CRAWLER_USER_AGENTS,
            *SEO_CRAWLER_USER_AGENTS,
            *LINK_PREVIEW_USER_AGENTS,
        },
        key=str.lower,
    )
)

# One compiled alternation over every token above. Substring matching is
# deliberate: user agents carry the token inside a longer string
# ("Mozilla/5.0 (compatible; Googlebot/2.1; +http://...)"), and a crawler that
# appends a version or a URL must still match.
_BLOCKED_USER_AGENT_PATTERN = re.compile(
    "|".join(re.escape(token) for token in BLOCKED_USER_AGENTS),
    re.IGNORECASE,
)

# Applies to the whole response, HTML or not — a JSON export answered to a
# crawler is as indexable as a page. ``noarchive``/``nosnippet`` also cover the
# cached copy and the result-page excerpt, which outlive the crawl itself.
X_ROBOTS_TAG = "noindex, nofollow, noarchive, nosnippet, noimageindex, notranslate"

ROBOTS_META_TAG = f'  <meta name="robots" content="{X_ROBOTS_TAG}">'

# Served whatever the user agent claims to be. A crawler that gets a 403 for
# robots.txt concludes there are no restrictions at all, so this one path must
# always answer 200 — it is the only way to state the restriction to a crawler
# that respects it.
ROBOTS_TXT_ALWAYS_ALLOWED_PATHS = frozenset({"/robots.txt"})

# Not a crawler surface: container health checks, uptime monitors and probes hit
# these, and some of them send a user agent containing "bot". Answering 403
# there would report a healthy instance as down.
CRAWLER_BLOCK_EXEMPT_PATHS = frozenset(
    {
        "/robots.txt",
        "/api/next/health",
        "/api/health",
        "/health",
    }
)

_BLOCKED_BODY = "DiscVault is a private collection manager and is not open to crawlers.\n"


def crawler_blocking_enabled() -> bool:
    """Whether known crawler user agents are refused outright.

    Default on: an instance is private unless its operator decides otherwise,
    and the two indexed instances that prompted this had nobody switch anything
    on to end up in Google.
    """
    raw = os.environ.get("DISCVAULT_BLOCK_CRAWLERS", "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def is_blocked_user_agent(user_agent: str | None) -> bool:
    """Whether this user agent belongs to a crawler DiscVault refuses."""
    if not user_agent:
        return False
    return _BLOCKED_USER_AGENT_PATTERN.search(user_agent) is not None


def robots_txt() -> str:
    """A disallow-everything ``robots.txt``, per named crawler and as a default.

    The wildcard group is what a compliant crawler needs, and on its own it is
    enough. The named groups are there because a crawler that finds a group
    matching its own token uses *only* that group and ignores ``*`` entirely —
    so an operator who later relaxes the wildcard (to let a status page be
    found, say) does not silently re-admit every AI scraper at the same time.
    """
    lines = [
        "# DiscVault is a private collection manager, not a public website.",
        "# Nothing here is meant to be indexed, summarised or used as training",
        "# data. Every crawler is disallowed everywhere; each response also",
        "# carries an X-Robots-Tag: noindex header, and known crawler user",
        "# agents are refused with 403 unless the operator sets",
        "# DISCVAULT_BLOCK_CRAWLERS=false.",
        "",
        "User-agent: *",
        "Disallow: /",
        "",
    ]
    for token in BLOCKED_USER_AGENTS:
        lines.append(f"User-agent: {token}")
        lines.append("Disallow: /")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def apply_robots_headers(result: Response) -> Response:
    """Stamp the ``noindex`` header on a response."""
    result.headers["X-Robots-Tag"] = X_ROBOTS_TAG
    return result


def register_next_crawler_routes(flask_app: Flask, *, connect=None) -> None:  # pragma: no cover - Flask integration
    @flask_app.get("/robots.txt")
    def next_robots_txt():
        result = flask_app.response_class(robots_txt(), mimetype="text/plain")
        result.headers["Cache-Control"] = "public, max-age=3600"
        return apply_robots_headers(result)

    @flask_app.before_request
    def refuse_crawler_user_agents():
        if not crawler_blocking_enabled():
            return None
        if request.path in CRAWLER_BLOCK_EXEMPT_PATHS:
            return None
        if not is_blocked_user_agent(request.headers.get("User-Agent")):
            return None
        result = flask_app.response_class(_BLOCKED_BODY, status=403, mimetype="text/plain")
        result.headers["Cache-Control"] = "no-store"
        return apply_robots_headers(result)

    @flask_app.after_request
    def add_robots_headers(result: Response):
        return apply_robots_headers(result)
