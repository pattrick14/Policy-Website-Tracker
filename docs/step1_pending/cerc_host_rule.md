# CERC host-fallback rule (proposed, 2026-10-23)

This note holds research output waiting to be merged. It records how the two
CERC hosts behaved and proposes the site-level configuration and a decision
entry (D-020). Nothing here has been merged into `config/sources.yaml` or
`docs/DECISIONS.md`.

## Observed host behaviour

Both `cercind.gov.in` and `www.cercind.gov.in` are already in `allowed_hosts`.
They serve identical content: `site_map.html` was 18,242 bytes from each. All
probes were read-only GETs or HEADs, spaced at about one request every 1.5 s.

| When (IST) | Timeout | Apex `cercind.gov.in` | `www.cercind.gov.in` |
|---|---|---|---|
| 2026-10-22, all session (previous agent) | 60–90 s | Every attempt timed out (connect or TLS handshake) or returned an empty reply | Served every page, with intermittent timeouts of 90 s or more that succeeded on retry |
| 2026-10-23, morning (reported by the coordinating agent) | 60 s | Failed | Failed |
| 2026-10-23 11:17:46–11:26:11 | 120 s | Two TLS-handshake timeouts at the full 120 s | Two TLS-handshake timeouts at the full 120 s |
| 2026-10-23 11:27:14–11:27:16 | 120 s | Succeeded in 0.2 s, immediately after the `www` failure | Connection reset (WinError 10054) after 21.7 s |
| 2026-10-23 11:27:18–11:29:24 | 120 s | Not tried | 54 consecutive GETs succeeded in 0.1–1.4 s |
| 2026-10-23 11:30 | 120 s | `site_map.html` in 0.26 s | `site_map.html` in 0.19 s; two PDF HEADs also succeeded |
| 2026-10-23 11:33:25–11:33:27 | 120 s | Succeeded in 0.2 s, immediately after the `www` failure | Connection reset after 19.3 s, then 5 consecutive GETs succeeded |

In total, 61 page GETs succeeded on 2026-10-23 after the outage window cleared.
The 11:17–11:26 attempts are logged with 20 s and 40 s backoff between rounds.

There are two separate failure modes:

1. **Outage windows.** Both hosts hang at the TLS handshake for minutes. When
   this happens, a longer timeout never turned a failure into a success; the
   requests that succeeded completed in under 2 s. Only spaced retries helped,
   once the window ended.
2. **Sporadic single-host failures.** One host resets the connection or times
   out while the other answers at once. Falling back to the other host for the
   same path fixed every such failure observed on 2026-10-23.

Neither host is reliably better. On 2026-10-22 only `www` worked, but both
resets on 2026-10-23 were on `www`, and both times the apex host answered.

Other relevant behaviour:

- HTTP 404 responses (`recent_orders_rops.html`, `Current_petition_status.html`)
  are real dead links on both hosts. They must not trigger a fallback or retry.
- PDFs return `Last-Modified` and `ETag` validators. For example,
  `2026/escalation/Monthly%20Notification%20Sep%202026.pdf` returned
  `Last-Modified: Thu, 01 Oct 2026 07:23:42 GMT`.
- Page markup mixes host forms. The navigation links to
  `http://www.cercind.gov.in/index_hin.html`, and one escalation page links to
  `http://cercind.gov.in/2010/...`. Both resolve over HTTPS on either host.

## Proposed site-level configuration

These keys sit at site level, beside the existing `allowed_hosts`, in the same
style as the Coal block's `request_rate_per_host_per_second`. The validator
accepts unknown keys, and Step 2 should formalize them in the schema.

```yaml
    request_rate_per_host_per_second: 1
    rate_limit_shared_across_hosts: true
    request_timeout_seconds: 120
    canonical_host: cercind.gov.in
    fallback_hosts: [www.cercind.gov.in]
    host_fallback_on: [connect_timeout, tls_handshake_timeout, read_timeout, connection_reset, empty_reply]
    max_attempts_per_url: 6
    retry_backoff_seconds: [20, 40]
    abort_after_consecutive_failed_urls: 3
```

How the keys work together:

- **Attempt rounds.** Each round tries the configured (apex) host first and then
  each fallback host. Up to three rounds give `max_attempts_per_url: 6`. Rounds
  are separated by 20 s, then 40 s.
- **Fallback triggers.** Only connection-level failures trigger a fallback.
  HTTP 404, block pages, and other complete responses are returned as they are.
- **Circuit breaker.** After three consecutive URLs exhaust their attempts, the
  CERC run stops. Every feed not completely collected is marked incomplete, and
  its previous state is preserved. No removals, baselines, or alerts come from
  an incomplete feed.
- **Canonical identity.** Document identity always uses `canonical_host`. A
  fetch served by `www.cercind.gov.in`, and an `http://` or `www.` href in page
  markup, must not create a new source-scoped canonical URL.
- **Shared rate limit.** Both hostnames point to the same server, so they share
  one rate budget of at most one request per second.

The worst case for a single URL is six attempts at 120 s plus 60 s of backoff,
about 13 minutes. The circuit breaker therefore caps a full outage at roughly 40
minutes before the run stops.

## Proposed decision entry

Add to the summary table:

```markdown
| D-020 | CERC fetching uses a 120 s timeout, apex-to-www host fallback on connection-level failures, bounded retries, a run-level circuit breaker, and canonical-host identity. | Proposed |
```

Add to the detailed section:

```markdown
### D-020 — CERC host fallback, timeouts, and bounded retries

- Date: 2026-10-23
- Status: Proposed
- Decision: CERC requests use a 120-second timeout. On a connection-level
  failure (connect or TLS-handshake timeout, read timeout, connection reset,
  empty reply) the same path is retried on the other allowed host
  (`cercind.gov.in` first, then `www.cercind.gov.in`). Up to three such rounds
  run per URL (six attempts), separated by 20 s and 40 s backoff. HTTP error
  statuses and block pages never trigger a fallback. After three consecutive
  URLs exhaust their attempts, the CERC run stops, and every feed not completely
  collected is marked incomplete with its previous state preserved. Both hosts
  share one rate limit of at most one request per second. Document identity
  always uses the canonical host `cercind.gov.in`, whichever host served the
  response or appeared in the href.
- Rationale: On 2026-10-22 the apex host failed every request while `www`
  mostly worked. On 2026-10-23 both hosts hung for several minutes, then each
  host in turn reset single connections while the other answered at once.
  Successful requests completed in under 2 s. Host fallback fixes the
  single-host failures, and spaced bounded retries ride out short outages. The
  circuit breaker keeps a long outage from causing false removals or a stalled
  run.
- Compatibility or migration impact: None; no state exists yet. Step 2 must
  add the site-level keys (`request_timeout_seconds`, `canonical_host`,
  `fallback_hosts`, `host_fallback_on`, `max_attempts_per_url`,
  `retry_backoff_seconds`, `abort_after_consecutive_failed_urls`,
  `rate_limit_shared_across_hosts`) to the contract schema. Feed URLs stay on
  the apex host.
- Supersedes: None.
```
