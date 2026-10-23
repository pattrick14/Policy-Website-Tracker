# Fetching

Production retrieval goes through `policy_tracker.adapters.http.HttpFetcher`,
which implements the frozen `Fetcher` protocol (`adapters/base.py`) and applies
one site's `FetchPolicy` (`core/contracts.py`). Adapters never import `httpx`
directly; tests replay responses with `policy_tracker.adapters.replay.ReplayFetcher`.

Related records: D-017 (Coal rate and CAPTCHA), D-020 (CERC fallback, retries,
breaker), D-022 (host rules), PROJECT_PLAN "Retrieval, documents, and language",
and OPERATIONS "Per-site fetching behavior".

## Modules

| Module | Purpose |
|---|---|
| `adapters/http.py` | `HttpFetcher`, `SiteFetcherPool`, `fetcher_for`, `close_fetchers`, `classify_error`, `is_public_address` |
| `adapters/blocks.py` | `detect_block_page(body, content_type)`, `detect_block_response(response)` |
| `adapters/replay.py` | `ReplayFetcher`, `ReplayEntry` for fixture tests |

## One fetcher per site

`fetcher_for(contract)` matches the runner's `FetcherFactory`. It returns one
shared `HttpFetcher` per `site_id` for the life of the process, so every feed
of a site shares the rate limit, the consecutive-failure counter, and the
circuit breaker. Feeds of one site must have identical `FetchPolicy` and
`allowed_hosts`; a mismatch raises `ValueError` (site fetch keys are site-level
in `config/sources.yaml`). Call `close_fetchers()` at the end of a run. Tests
construct their own `SiteFetcherPool` or `HttpFetcher`.

## Request rules

Every `get(url, headers=...)` call:

1. Returns `circuit_open` at once, with no network traffic, if the site's
   breaker is open.
2. Accepts only `http`/`https` URLs whose host is in the site's
   `allowed_hosts` (`invalid_url`, `host_not_allowed`).
3. Resolves each hop's host and refuses it unless every address is a public,
   globally routable unicast IP (`private_address`): private, loopback,
   link-local (including 169.254.169.254), CGNAT, reserved, multicast,
   unspecified, and IPv4-mapped forms of these are refused. If local
   resolution fails and an environment proxy (`HTTPS_PROXY`/`HTTP_PROXY`,
   honouring `NO_PROXY`) will carry the request, the proxy resolves the name
   and the check is skipped; without a proxy the result is `dns_error`.
4. Waits for the rate limit: request starts are spaced at least
   `1 / request_rate_per_host_per_second` seconds apart per host, or across
   all hosts of the site when `rate_limit_shared_across_hosts` is true. Every
   redirect hop and every retry is a request.
5. Sends `GET` with TLS verification always on (the CA bundle is certifi, or
   `SSL_CERT_FILE`/`SSL_CERT_DIR` when set), the policy timeout for each of
   connect, read, write, and pool, and the caller's headers unchanged.
   Conditional headers (`If-None-Match`, `If-Modified-Since`) pass through.
6. Follows redirects (301, 302, 303, 307, 308 with a `Location`) itself, up to
   `max_redirects` hops; each hop repeats steps 2–4. One more redirect returns
   `too_many_redirects`. A 3xx without `Location` is returned as is.
7. Streams the body and stops at `max_document_bytes` of decoded content
   (`too_large`); a larger declared `Content-Length` is refused before reading.

A response with any HTTP status, including 304, 403, 404, and 5xx, is returned
as a normal `FetchResponse` with `error=None`. `FetchResponse.ok` is true only
for 2xx, so callers handling conditional requests check `status_code == 304`
explicitly. `url` is the final URL after redirects and fallback, and
`served_by_host` is the host that answered. Adapters canonicalize identity with
`HostRules`, never with `served_by_host`.

`HttpFetcher` never raises for network or policy failures. It returns
`FetchResponse(status_code=0, headers={}, body=b"", error=<class>)`.

## Error vocabulary

D-020 connection-level classes (valid in `host_fallback_on`):

| Error | Meaning |
|---|---|
| `connect_timeout` | TCP connect timed out |
| `tls_handshake_timeout` | Best effort: a connect timeout during the TLS handshake, or a non-certificate SSL failure (for example EOF) while connecting |
| `read_timeout` | No bytes within the timeout while waiting for headers or body |
| `connection_reset` | Connection reset or aborted (ECONNRESET, broken pipe) while connecting or reading |
| `empty_reply` | Server closed the connection before sending any response |

Other network classes (count toward the breaker, never fall back unless a
site lists them): `write_timeout`, `pool_timeout`, `connection_refused`,
`dns_error`, `tls_certificate_error`, `connect_error`, `read_error`,
`protocol_error` (malformed response head), `incomplete_body` (disconnect after
headers), `proxy_error`, `transport_error`.

Policy refusals (never retried, never counted toward the breaker):
`invalid_url`, `host_not_allowed`, `private_address`, `too_many_redirects`,
`too_large`, `circuit_open`.

## Host fallback, retries, and the circuit breaker (D-020)

When `fallback_hosts` is set and the URL host is the canonical host or a
fallback host, each round tries the same path and query on the canonical host
first, then on each fallback host. A failure whose class is in
`host_fallback_on` moves to the next host; any other outcome (success, an HTTP
status, a block page, a policy refusal, or an unlisted error) ends the URL at
once. Rounds repeat until `max_attempts_per_url` attempts are spent, sleeping
`retry_backoff_seconds[i]` between round `i` and `i + 1` (the last value
repeats). For CERC that is six attempts in the order apex, www, apex, www,
apex, www with 20 s and 40 s sleeps, plus the shared 1 s rate spacing.

Sites without fallback hosts retry the same host when `max_attempts_per_url`
is above 1 and the class is listed; with the defaults they make one attempt.

A URL that ends in a network-class error increments the site's consecutive
failure count; any HTTP response resets it. When the count reaches
`abort_after_consecutive_failed_urls`, the breaker opens for the rest of the
process and every later `get()` returns `circuit_open`. Adapters report that
as fetch-failure evidence, so the engine marks the feed incomplete and keeps
its previous state. `HttpFetcher.circuit_open`, `consecutive_failed_urls`,
and `attempts` are available for run health.

## Block pages

Some sites return HTTP 200 block pages. Adapters must call
`detect_block_response(response)` (or `detect_block_page(body, content_type)`)
on every listing response before parsing, and report a non-`None` reason as
`block_page_detected` evidence, never as an empty list. The fetcher does not
inspect bodies and never falls back on a block page.

| Reason | Signature |
|---|---|
| `captcha` | coal.nic.in "Validation request" form (`action="/captcha_resp"` or `name="captcha_resp_txt"`), or a small page titled "Validation request" mentioning a CAPTCHA (`tests/fixtures/ministry_of_coal/captcha_block.html`) |
| `browser_block` | "The requested browser client was blocked" text (`tests/fixtures/ministry_of_power/browser_block.html`), or a small page (16 KiB or less) titled "Access Denied", "403 Forbidden", or "Forbidden" |
| `waf_rejected` | Small F5 BIG-IP "The requested URL was rejected. Please consult with your administrator" page |
| `challenge` | Small CDN bot-challenge interstitial ("Just a moment...", `challenge-platform`, `cf-chl-`, `_Incapsula_Resource`) |

Only markup bodies are inspected (at most the first 64 KiB). A body declared
as PDF, image, archive, audio, or video is never flagged; an HTML block page
served as `application/json` is. Every non-block fixture in `tests/fixtures/`
is checked for false positives, as are a normal page that contains a CAPTCHA
feedback form, the Coal anti-bot `<script src="/bnith__...">` tag, and the
words "Validation request" and "Access Denied" in content.

## ReplayFetcher for adapter tests

```python
from policy_tracker.adapters.replay import ReplayEntry, ReplayFetcher

fetcher = ReplayFetcher(fixture_root="tests/fixtures/cerc")
fetcher.add("https://cercind.gov.in/Draft_reg.html", fixture="draft_regulations.html")
fetcher.add("https://cercind.gov.in/x.pdf", body=b"%PDF-1.7", headers={"ETag": '"a"'})
fetcher.add_sequence("https://cercind.gov.in/flaky.html", [
    ReplayEntry(error="connection_reset"),
    ReplayEntry(fixture="public_notices.html", served_by_host="www.cercind.gov.in"),
])
observation = adapter.collect(contract, fetcher, observed_at="2026-10-23T00:00:00Z")
assert fetcher.unmatched == [] and fetcher.unused() == []
```

- URLs match exactly, ignoring the fragment. Unknown URLs return 404 with an
  empty body and are listed in `unmatched`.
- `fixture` paths resolve against `fixture_root`; a missing file fails when
  the route is added. The content type is inferred from the suffix (`.html`,
  `.json`, `.xml`, `.pdf`, `.txt`, `.csv`) unless given.
- Repeated `add` calls or `add_sequence` return successive entries; the last
  one repeats. `ReplayEntry` headers are used as given (no content-type inference).
- `error=` simulates a connection failure (`status_code` 0); `final_url=` and
  `served_by_host=` simulate redirects and host fallback.
- With `conditional=True` (default), a request whose `If-None-Match` matches the
  entry's `ETag`, or whose `If-Modified-Since` equals its `Last-Modified`, gets
  a 304 with an empty body.
- `requests` records each URL with its headers; `requested_urls` lists URLs.

Replay does not apply the fetch policy; fetch-policy behavior is tested in
`tests/adapters/test_http.py` with `httpx.MockTransport`.

## Known limits

- The private-address check resolves names before connecting; a DNS answer
  that changes between the check and the connection (rebinding) is not
  detected, and through a proxy the proxy's own resolution is trusted.
- Timeouts apply to each phase and each read, not to a whole transfer, so a
  slow but steady download can exceed `request_timeout_seconds` in total;
  the size cap bounds it.
- TLS-handshake classification depends on exception text and is best effort.
- The breaker is per process and per site; a new process starts closed.
- The fetcher is not thread-safe; feeds of a site run sequentially.
