# Decision Log

These decisions were confirmed during planning. Changing one requires an
explicit new entry with date, rationale, compatibility effect, and migration
plan where applicable.

| ID | Decision | Status |
|---|---|---|
| D-001 | Capture every item on selected publication feeds; prioritize rather than discard routine material. | Accepted |
| D-002 | Use GitHub Actions with Monday 08:00 IST as the nominal weekly schedule. | Accepted |
| D-003 | Deliver HTML/plain email through generic SMTP and archive Markdown, CSV, and JSON in a private repository. | Accepted |
| D-004 | Use deterministic rule-based classification; no AI API dependency. | Accepted |
| D-005 | Track English and bilingual content. Preserve English listing metadata when the linked document is not usable in English. | Accepted |
| D-006 | Establish a complete available-history baseline without sending publication alerts. Baseline status is per feed. | Accepted |
| D-007 | Deadline emails are changes-only. Do not repeat approaching deadlines; maintain `open_deadlines.csv` instead. | Accepted |
| D-008 | After the initial byte hash, use ETag/Last-Modified and related metadata for PDF replacement checks. Accept and disclose the silent-replacement detection gap. | Accepted |
| D-009 | Retain generated state, events, digests, and health history indefinitely; do not retain source PDFs. | Accepted |
| D-010 | Use `main` for code and an orphan `tracker-data` branch for generated records. | Accepted |
| D-011 | SMTP delivery is at-least-once using an immutable pending-outbox protocol. | Accepted |
| D-012 | Production collection uses direct HTTP/API adapters; Playwright is limited to research and diagnostics. | Accepted |
| D-013 | Ministry of Power Press Release and Parliament Question taxonomy terms are excluded as independent feeds. | Accepted |
| D-014 | Ministry of Coal's own press-release table is excluded, consistent with D-013. | Accepted |
| D-015 | Ministry of Coal Nominated Authority auction/allotment documents and meeting minutes (general, production-review, and operationalisation-review) are tracked feeds; Scrutiny Committee, Single Window System, and Energy Cell pages are not independent feeds. | Accepted |
| D-016 | Ministry of Coal "Production and Supplies" narrative statistics page receives indirect coverage only. | Accepted |
| D-017 | coal.nic.in requests are limited to 0.25 requests/second with backoff, because faster research traffic triggered an HTTP 200 CAPTCHA block page. | Accepted |
| D-018 | CERC meeting minutes (Commission, Central Advisory Committee, other meetings, Coordination Forum) and Commission-notified tariff inputs (coal price indices/CIL notifications and escalation rates) are tracked feeds. | Accepted |
| D-019 | CERC repealed regulations, homepage annual archives, technical-validation letters, and dormant regulatory-affairs correspondence are excluded as independent feeds. | Accepted |
| D-020 | CERC fetching uses a 120 s timeout, apex-to-www host fallback on connection-level failures, bounded retries, a run-level circuit breaker, and canonical-host identity. | Accepted |

### D-013 — Exclude Ministry of Power press releases and Parliament questions

- Date: 2026-10-22
- Status: Accepted (confirmed by the user)
- Decision: Do not configure the Press Release or Parliament Question
  `documents_category` taxonomy terms as tracked feeds. Any such item that
  appears in the What's New feed (`mop_whats_new`) is still captured under D-001.
- Rationale: Neither family is among the approved Ministry of Power publication
  families in `docs/PROJECT_PLAN.md`. Press releases duplicate PIB communication,
  and Parliament answers are high-volume Q&A rather than policy publications.
- Compatibility or migration impact: None; no state exists yet. Adding either
  family later is a coverage expansion that increments `coverage_revision` and
  silently baselines the new feed.
- Supersedes: None.

### D-014 to D-017 — Ministry of Coal scope and request rate

- Date: 2026-10-22
- Status: Accepted (confirmed by the user)
- Decision: Exclude Coal press releases (D-014). Add tracked feeds for
  Nominated Authority auction/allotment documents and for meeting minutes,
  including the production-review and operationalisation-review minutes
  tables. Scrutiny Committee, Single Window System, and Energy Cell pages are
  excluded as independent feeds (D-015). Production and Supplies is indirect
  because the statistics feeds carry the same data and narrative-page
  fingerprints would be noisy (D-016). Limit coal.nic.in to 0.25 requests per
  second with backoff (D-017).
- Rationale: Auctions and minutes record policy implementation and decisions;
  press releases duplicate PIB communication. At about 0.8 requests/second the
  site began serving a ~1 KB HTTP 200 CAPTCHA "Validation request" page, which
  must be quarantined as a block page and never parsed as an empty table.
- Compatibility or migration impact: None; no state exists yet. Items from
  excluded sections that appear in Coal What's New remain captured (D-001).
- Supersedes: None.

### D-018 and D-019 — CERC scope additions and exclusions

- Date: 2026-10-22
- Status: Accepted (confirmed by the user)
- Decision: Track CERC meeting minutes across all four minutes pages and the
  Commission-notified tariff inputs (coal price index/CIL notifications and
  escalation rates) (D-018). Exclude repealed regulations (`Repeal_Reg.html`),
  the homepage annual archives (`Archive.html` and its year pages), technical-
  validation letters, and dormant regulatory-affairs correspondence (D-019).
- Rationale: Minutes record Commission decisions and advisory deliberation;
  tariff inputs change current tariff outcomes. Repealed regulations are
  historical, the homepage archives hold expired bulletin items, validation
  letters are high-volume operational records, and the correspondence pages
  are dormant.
- Compatibility or migration impact: None; no state exists yet. Excluded
  material that appears on the tracked homepage/view-all bulletin remains
  captured (D-001). Known limit: older consultations that survive only in the
  homepage archives are not baselined.
- Supersedes: None.

### D-020 — CERC host fallback, timeouts, and bounded retries

- Date: 2026-10-23
- Status: Accepted (engineering decision adopted at Step 1 merge; revise via a new entry if needed)
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

## Decision template

```markdown
### D-XXX — Short title

- Date:
- Status: Proposed / Accepted / Superseded
- Decision:
- Rationale:
- Compatibility or migration impact:
- Supersedes:
```
