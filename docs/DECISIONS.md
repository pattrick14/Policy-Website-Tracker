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
| D-021 | Ministry of Coal "Coal Gasification" mission page receives indirect coverage only; What's New captures its announcements. | Accepted |
| D-022 | Feed configuration is a closed schema; tracked feeds load into typed contracts whose host rules alias only tested host mirrors to the site's canonical host, and Coal collapses duplicated path slashes on those hosts. | Accepted |
| D-023 | State-engine semantics: one owning feed per record sets its metadata, removal counters ignore replays and stale observations, parser migrations apply silently, and event ids identify semantic changes. Feeds run live-first in feed-id order. | Accepted |
| D-024 | Production fetching uses one policy-enforcing HttpFetcher per site (allowlist, public-IP and per-hop redirect checks, streamed size cap, rate limit, D-020 fallback and breaker); adapters detect block pages by content. | Accepted |
| D-025 | Adapter identity rules: CERC documents are keyed by canonical URL with row-level matter ids; Coal linkless rows use a content hash; MoP documents are attachment ids resolved to files at enrichment; an undated sighting never clears a known date. | Accepted |
| D-026 | Deadlines read numeric dates as DD/MM, flag ambiguous dates without a date, let a stated extension replace earlier dates, and report a change only for records and feeds that existed before the run; classification is rule-based from config/taxonomy.yaml. | Accepted |

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

### D-021 — Coal Gasification mission page is indirect

- Date: 2026-10-23
- Status: Accepted (confirmed by the user)
- Decision: The Ministry of Coal Coal Gasification page
  (`/major-statistics/national-coal-gasification-mission`) is not a tracked
  feed. It receives indirect coverage in the narrative and thematic pages group
  (sitemap link-set comparison). Its announcements are captured through the
  tracked What's New feed (`coal_whats_new`) under D-001.
- Rationale: The 2026-10-23 re-verification found that the page is an active
  scheme dossier, not a static narrative page, and proposed tracking it. The
  user chose indirect coverage because the scheme's new material (approvals,
  RFPs, pre-application notices, query responses) is announced in What's New,
  which is already tracked with full history.
- Compatibility or migration impact: None; no state exists yet. Known limit:
  files posted only on this page are not baselined or tracked independently.
  At verification the page held 59 rows with 57 distinct hrefs in nine section
  tables (one nested), dated 16/09/2026 back to 21/09/2021, including RFPs
  dated 13.08.2026. Unannounced additions or edits to the page are not
  detected. The page shape is kept as the fixture
  `tests/fixtures/ministry_of_coal/coal_gasification.html`. Promoting the page
  to a tracked feed later is a coverage expansion that increments
  `coverage_revision` and silently baselines the new feed.
- Supersedes: None. Consistent with D-015 and D-016, which leave other
  Ministry of Coal pages without independent feeds when What's New or another
  tracked feed carries their material.

### D-022 — Closed contract schema and site host rules

- Date: 2026-10-23
- Status: Accepted (Step 2 engineering decision)
- Decision: `policy_tracker.inventory` rejects unknown top-level, `defaults`,
  site, feed, `completeness`, `archive_href_rewrite`, and coverage-decision
  keys, and types every key formerly accepted without a schema (Step 1 notes).
  `policy_tracker.core.config.load_contracts` validates first, then builds one
  `FeedContract` per `decision: track` feed. Site fetch keys override
  `defaults`. `HostRules.host_aliases` maps only tested mirrors to the site's canonical
  host (`canonical_host` if set, else the `base_url` host): the www/apex pair
  and any D-020 fallback host. HTTPS upgrade and slash collapsing apply only
  to aliased hosts.
  `collapse_duplicate_slashes` is an explicit site key, set only on
  `ministry_of_coal`, citing the tested 2026-10-23 archive-href rewrite.
  Rolling feeds must declare `removal_authority: false`.
  `coverage_revision` and `parser_version` default to 1 when absent.
- Rationale: A typo in a contract key would otherwise be ignored silently and
  change collection or removal behavior. One canonical host per site keeps
  identity stable across host fallback (D-020) and mixed-scheme hrefs.
- Compatibility or migration impact: None; no state exists yet. Coal's
  `coal.gov.in` and `www.coal.gov.in` file hosts are allowed but not aliased,
  so their mixed-scheme and doubled-slash hrefs keep their raw identity, as
  SOURCE_INVENTORY requires, until a tested rule exists. Adding such a rule
  later changes document keys and needs a migration.
- Supersedes: None.

### D-023 — State-engine ownership, replay, and ordering rules

- Date: 2026-10-23
- Status: Accepted (Step 2 engineering decision)
- Decision: When a record appears on several feeds, only its owning feed (the
  present appearance seen first, ties broken by feed id) may change its title,
  dates, canonical URL, or metadata; other feeds may only add a missing
  `matter_key`. Removal counters advance only when the run id differs from the
  feed's last complete run and the observation is newer than the feed's latest
  sighting; an older observation is quarantined as `stale_observation`. A
  parser-version change applies metadata and section changes silently for
  that feed until a complete accepted run. Event ids hash the event type,
  feed, entity, and changed values, not the run id, so archives key history on
  `(run_id, event_id)`. The runner applies live feeds first, then baselining
  and backfill feeds, each sorted by feed id, so a new publication is reported
  as `new_document` even when a baselining feed also sees it.
- Rationale: Without ownership, two feeds showing different titles for one
  document would emit `metadata_changed` every week. The replay guard keeps
  reruns of the same snapshot from withdrawing records (PROJECT_PLAN Step 8).
  Feed order otherwise changes event types within a run.
- Compatibility or migration impact: None; no state exists yet. A rebaseline
  reason has no state field and is recorded in the run manifest instead.
  See `docs/STATE_ENGINE.md`.
- Supersedes: None.

### D-024 — Policy-enforcing HTTP fetcher and block-page detection

- Date: 2026-10-23
- Status: Accepted (Step 3 engineering decision)
- Decision: `adapters/http.HttpFetcher` is the only production fetcher.
  `fetcher_for` shares one instance per site, so the rate limit, failure count,
  and circuit breaker are site-level for a whole run. Redirects are followed
  manually, and every hop must be an allowed host resolving only to globally
  routable addresses. Any HTTP status (304, 404, 5xx) is returned unchanged and
  resets the failure count; only network-class errors count toward the
  breaker, and policy refusals never retry or count. When local DNS fails and
  an environment proxy carries the request, the proxy's resolution is trusted.
  Adapters call `adapters/blocks.detect_block_page` on listing responses and
  report hits as block-page evidence.
- Rationale: PROJECT_PLAN retrieval limits, D-017, and D-020. Keeping body
  inspection out of the fetcher keeps "block pages never trigger fallback"
  explicit.
- Compatibility or migration impact: None. Error strings are listed in
  `docs/FETCHING.md` and appear in run-health output.
- Supersedes: None.

### D-025 — Adapter identity rules and undated sightings

- Date: 2026-10-23
- Status: Accepted (Steps 3–5 engineering decision)
- Decision:
  - **CERC.** Documents are keyed by canonical URL, so one file listed on
    several feeds (home bulletin and a draft row, recent orders and an annual
    archive) is one document. Row identifiers are matter native ids:
    `draft_reg:N`, `current_reg:N`, `disc_paper:N`, `spn:<petition>`, and
    `public_hearing:<hash of year and matter text>`. Serials, petition numbers,
    archive years, and leaf pages are metadata. When one file appears under
    two matters, the first parent seen wins.
  - **Ministry of Coal.** Documents are keyed by the attachment href in
    canonical form. A row with no link at all uses `linkless:` plus a hash of
    feed, section, title, raw date, and occurrence order, so a shifting row
    position does not create a weekly new document. Linked rows never use it.
  - **Ministry of Power.** Documents are keyed by WordPress attachment id. The
    document URL is the attachment-record API, which enrichment resolves to the
    file (English first, then bilingual, then Hindi). When separate English
    and Hindi files exist, only the English file is hashed, so a change to the
    Hindi file alone is not detected (D-005, D-008).
  - **Engine.** A sighting with no date at all (such as a Coal archive table
    without a date column) never clears a document's known date; a different
    date still emits `metadata_changed`.
- Rationale: keying CERC documents by serial plus href would raise duplicate
  new-document alerts across feeds. Without the linkless rule, the Coal "test"
  row would alert weekly. Without the date rule, every current-to-archive move
  would also report a lost date.
- Compatibility or migration impact: None; no state exists yet. Known risks:
  if CERC renumbers serials, its matters get new keys, and editing a public
  hearing's text gives it a new key. Live page shapes could not be rechecked
  from this environment (proxy refusal), so adapters rest on the Step 1
  fixtures.
- Supersedes: None.

### D-026 — Deadline extraction and digest classification rules

- Date: 2026-10-23
- Status: Accepted (Step 6 engineering decision)
- Decision: Numeric dates are read as DD/MM (Indian convention). A date counts
  as a deadline only in a sentence with a deadline topic and cue; ambiguous
  dates (two-digit year, impossible, MM/DD-only, or no year) are flagged with
  no date and left out of `open_deadlines.csv`. A stated extension replaces
  earlier non-extension dates in the same record. A deadline change is
  reported only for records that existed before the run, on feeds that were
  live before it; the first deadline index is built silently, and an
  evidence-only change is not a deadline change. Classification is
  deterministic and driven by `config/taxonomy.yaml` (schema version 2): a
  routine gate with promotion terms, then title rules, feed defaults, and text
  terms, falling back to "Other updates".
- Rationale: D-004 and D-007; avoids re-alerting on wording-only changes and
  keeps ambiguous dates visible without guessing.
- Compatibility or migration impact: None. The rules are first-pass
  heuristics, not yet tuned against live listings, and change only by editing
  the taxonomy file.
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
