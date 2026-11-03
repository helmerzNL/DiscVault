# Search engines and AI crawlers

The detail behind the *Privacy and security* section of the
[README](../README.md#privacy-and-security).

A DiscVault instance is a private collection manager, and it keeps itself out of
search results and out of AI training corpora without any configuration:

- `/robots.txt` disallows every crawler on every path, and names the search
  engines, AI crawlers, SEO bots and link-preview fetchers individually.
- Every response carries `X-Robots-Tag: noindex, nofollow, noarchive, nosnippet,
  noimageindex`, and every rendered page repeats it as a `<meta name="robots">`
  tag — this is the part that clears an instance already in an index, since a
  `Disallow` alone stops a crawler from ever seeing the `noindex`.
- Known crawler user agents are refused with `403`. `/robots.txt` and
  `/api/next/health` always answer, so container health checks and uptime
  monitors are unaffected.

Set `DISCVAULT_BLOCK_CRAWLERS=false` only if you deliberately want an instance to
be findable. Nothing has to be misconfigured for the opposite to happen: a
reachable host with a public DNS record gets crawled on its own, which is how two
self-hosted instances ended up in Google.

All three layers are served by DiscVault itself, so a reverse proxy in front of it
must pass them through: a proxy that answers `/robots.txt` from its own
configuration, or strips response headers, replaces DiscVault's answer with its
own. Cloudflare's own "Block AI bots" setting is complementary, not a substitute —
it never sees a request that does not go through Cloudflare.

None of this is a security control — a user agent is self-reported, so a scraper
that wants in can simply claim to be a browser. An instance that must not be
*reachable* still belongs behind authentication, a VPN, or off the public
internet.
