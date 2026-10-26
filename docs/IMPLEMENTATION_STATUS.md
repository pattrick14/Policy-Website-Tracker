# Implementation Status

Last updated: 2026-10-23

## Current state

- Planning: complete.
- Repository documentation scaffold: complete.
- Runtime implementation: Steps 2–7 complete; Step 8 offline acceptance complete. Live acceptance (L1–L4) needs network access to the sites and SMTP credentials.
- Source inventory: all three sites `research_status: complete`; the strict gate passes; reviewed and approved by the user on 2026-10-23.
- Tests: 896 pass and 4 live-only tests are skipped (`python -m pytest -q`); sanitized fixtures are listed in `tests/fixtures/README.md`.
- Git repository: work is on branch `claude/keen-faraday-trctv1` of `pattrick14/policy-website-tracker`, not yet merged to `main`.

## Step status

| Step | Status | Notes |
|---|---|---|
| 1. Website research and repository bootstrap | Complete | Inventory approved by the user on 2026-10-23. |
| 2. Core contracts and state engine | Complete | Contracts frozen; see the Step 2 handoff at the end. |
| 3. Ministry of Power adapter | Complete (offline) | Fixture-tested; live page shapes not rechecked (sandbox proxy refused the sites). |
| 4. Ministry of Coal adapter | Complete (offline) | Fixture-tested; live page shapes not rechecked (sandbox proxy refused the sites). |
| 5. CERC adapter | Complete (offline) | Fixture-tested; live page shapes not rechecked (sandbox proxy refused the sites). |
| 6. Enrichment and digest generation | Complete | Bounded enrichment, classification, deadlines, renderers. |
| 7. GitHub and SMTP operations | Complete (offline) | Outbox, SMTP, tracker-data publishing, workflows; not yet run on GitHub. |
| 8. Acceptance and adversarial review | Offline complete; live pending | 14 drills pass; review findings fixed; live steps L1–L4 in `docs/ACCEPTANCE.md`. |

## Exact next task

Deployment and live acceptance by an operator (`docs/ACCEPTANCE.md` section 4,
`docs/OPERATIONS.md`): merge the branch to `main`, set the SMTP variables and
secrets, run `init-data-branch.yml`, run `tracker.yml` with `mode=baseline`
until every feed's baseline is complete (L1), compare live totals with
`docs/SOURCE_INVENTORY.md` (L2), and send one controlled email (L3). Fix any
adapter whose live page shape differs from its fixtures, bumping that feed's
`parser_version`.

## Step 1 exit evidence

- Every sitemap/navigation section has a recorded decision and evidence URL.
- Pagination and available-history boundaries are verified.
- Parent/attachment evolution and removal authority are documented.
- Every configured feed has representative success and failure fixtures.
- The configuration validator passes.
- This file is updated with results, commands run, known gaps, and the next task.

## Known setup prerequisites

- A GitHub account or organization in which to create the private repository.
- SMTP provider details are not needed until Step 7.
- Live-site research requires network access and must be polite and rate-limited.

## Handoff notes

Earlier exploratory research generated `.playwright-cli/`, `document.title`, and
`({text` scratch artifacts in the workspace. They are ignored by `.gitignore`
and are not part of the project. The Playwright snapshots contain useful CERC
and Coal research evidence, so they were retained for later fixture extraction.

### Work completed in the 2026-10-22 Step 1 increment

- Initialized a local Git repository with `main` as the initial branch.
- Added `policy_tracker.inventory`, the `policy-tracker-inventory` entry point,
  and structural/strict completion-gate validation.
- Expanded every existing preliminary feed entry with the required contract
  fields: purpose, discovery, enumeration, identity, dates, MIME/language,
  refresh, completeness, history, evidence, fixtures, and verification date.
- Verified the Coal sitemap and What's New shapes and the CERC sitemap and draft-
  regulation shapes against live official pages.
- Added sanitized fixtures for Coal What's New and an evolving CERC draft row.
- Added six deterministic inventory tests.

Validation results:

- `python -m pytest`: 6 passed.
- Structural inventory validation: passed.
- Strict Step 1 completion gate: failed as expected, listing remaining
  preliminary sites, unverified feeds, fixtures, and history boundaries.
- Ruff was not run because the optional `ruff` dependency is not installed in
  the current environment.

Known setup issue: after `git init`, the sandbox command runner failed its own
workspace setup. Validation commands were run through the approved external
runner. The repository path was added to Git's global `safe.directory` list to
handle the shared-workspace ownership mismatch.

### Work completed in the 2026-10-22 continuation

- Reverified all three Ministry of Power API shapes directly against the live
  official host: taxonomy, What's New, category enumeration, parent detail, and
  attachment detail.
- Verified pagination totals and the empty page after `total_pages`.
- Recorded current selected-category boundaries: Reports 9, Acts and Policy 7,
  Orders and Notices 4, and three configured zero-item categories.
- Verified an old annual-report parent with 35 attachment groups and a modified
  timestamp later than its creation timestamp.
- Added seven sanitized Ministry of Power success/failure fixtures and marked
  the three existing Ministry contracts verified. The site remains preliminary
  until navigation and non-selected taxonomy terms have explicit decisions.
- Added a reusable read-only `scripts/inventory_probe.py` research helper and a
  deterministic parser test.
- Enumerated the complete Ministry of Coal sitemap and verified the five-table
  Acts/Rules/Policies collection, including the unreliability of upload-folder
  years. Added its contract and sanitized fixture.

Latest validation results:

- `python -m pytest`: 7 passed.
- Structural inventory validation: passed.
- The strict Step 1 gate remains intentionally failing because Coal/CERC feed
  families, exclusions, history boundaries, and fixtures are incomplete.

### Work completed in the 2026-10-22 CERC continuation

- Verified and recorded full visible boundaries for CERC drafts (serials 1–120,
  2013–2026) and current regulations (serials 1–208, 1999–2026).
- Verified current-year Orders (serials 1–526) and ROPs (serials 1–1826),
  including records with prior-year decision/hearing dates posted in 2026.
- Added verified contracts and sanitized fixtures for current regulations,
  orders, ROPs, discussion papers, study reports, general public notices,
  statutory public notices, and statutory advice.
- Confirmed that statutory-notice matters commonly contain distinct paired
  English and Hindi newspaper files.
- Added explicit `required_feed_ids` manifests for all three sites. The strict
  gate now reports missing feed families instead of silently passing an
  incomplete configured subset.

Latest validation results:

- `python -m pytest`: 8 passed.
- Structural inventory validation: passed.
- Strict Step 1 gate: fails with an explicit list of 21 missing Coal contracts,
  8 missing CERC contracts, incomplete sitemap/home fixtures, and preliminary
  site statuses.

Exact next action: verify and configure the missing Coal contracts listed by
the strict gate, then finish CERC market monitoring, annual/other reports,
hearing pages, and order/ROP archives. Do not begin Step 2 until the strict gate
passes and the inventory is reviewed.

### Work completed in the 2026-10-22 Coal continuation

- Probed all 21 previously missing Ministry of Coal publication pages and
  recorded table counts, row counts, document-link shapes, and nested-detail
  behavior.
- Added every required Coal feed ID to `config/sources.yaml`. Sixteen of the 21
  new contracts have verified live boundaries; five remain intentionally
  unverified pending historical child/archive enumeration.
- Added contracts for statistics, quarterly/RSR/coal reports, sustainability,
  compensation and termination orders, national indices, Cabinet material,
  Standing Linkage Committee material, annual/budget/other nested reports,
  action plans, papers laid, SOPs, CSR guidance, and Central Sector Schemes.
- Added reusable sanitized fixtures for direct tables, two-table termination
  pages, nested report indexes/details, direct link lists, narrative scheme
  pages, and the Coal sitemap.
- Verified that annual-report index rows are parents: the 2025-26 detail page
  contains 22 child chapter attachments.
- Tightened the Coal What's New boundary to its observed 50-row table.

Latest validation results:

- `python -m pytest`: 8 passed.
- Structural inventory validation: passed.
- Strict Step 1 gate now reports only five unfinished Coal contracts: Standing
  Linkage Committee, annual reports, budget reports, other reports, and the
  archived Monthly Reports for Cabinet collection. Eight required CERC
  contracts and site-level review/exclusion work also remain.

Exact next action: enumerate the five remaining Coal historical child/archive
boundaries, then complete the eight missing CERC contracts. Step 2 remains
blocked.

### Work completed in the 2026-10-22 CERC and coverage-gate continuation

- Added and verified all eight previously missing CERC contracts: working
  papers, prior-year Orders, prior-year ROPs, market-monitoring reports, annual
  reports, other reports, public hearings, and hearing schedules.
- Recorded archive boundaries of 27 annual Order pages (1999-2025) and 18 annual
  ROP pages (2008-2025).
- Recorded two working papers, 14 Market Monitoring Cell reports, 18 annual
  reports, one Other Report, 78 public-hearing document links reaching 2015,
  and 21 current hearing-schedule documents.
- Completed CERC sitemap and homepage/view-all evidence and added ten sanitized
  fixtures, bringing the repository fixture count to 35.
- After those changes, `python -m pytest` passed all 8 tests and the strict gate
  reported only the three preliminary site statuses.
- Extended the inventory validator and tests so every site must also provide
  structured `coverage_decisions` with a scope, decision, reason, evidence,
  coverage limit, refresh policy, fixture, and verification date. This prevents
  a site from being marked complete merely because its selected feeds validate.
- Confirmed that the Ministry of Power homepage fetch is an empty client-rendered
  app shell and that its official `/sitemap.xml` is misconfigured with MeitY
  URLs. A subsequent frontend-bundle endpoint probe was stopped when work was
  paused and produced no accepted research result.

Current validation state at pause:

- Last successful pre-coverage-decision run: `python -m pytest` — 8 passed.
- Current configuration is intentionally not gate-ready: the new validator
  requires `coverage_decisions`, and none of the three site blocks has that list
  yet. Tests/strict validation must be rerun after those lists are added.
- No Step 2 code has been started.

Exact resume action: add and review the three site-level `coverage_decisions`
lists, capture any missing representative navigation evidence (especially the
misconfigured Ministry of Power sitemap), reconcile the remaining candidate
and exclusion notes in `docs/SOURCE_INVENTORY.md`, then set a site to
`research_status: complete` only when its decisions are defensible. Run
`python -m pytest`, structural validation, and `--require-complete` afterward.
Step 2 remains blocked until that strict gate passes.

### Work completed in the 2026-10-22 Coal boundary continuation

- Enumerated both Standing Linkage Committee pages: 89 rows total, with
  `page=1` as the terminus and 16 August 2017 as the oldest displayed date.
- Followed all 18 annual-report parents and counted 355 child attachments across
  the 2009-10 through 2025-26 collection.
- Followed all 18 budget-report parents and counted 38 child attachments across
  the 2009-10 through 2026-27 collection.
- Followed all three Other Reports parents and verified one child attachment on
  each detail page.
- Enumerated Monthly Reports for Cabinet as 50 current rows plus 50 archive rows,
  with the exposed history extending from November 2016 through October 2025.
- Marked all five remaining Coal contracts verified. The strict gate now has no
  Coal feed-level errors; Coal remains site-preliminary only until the final
  navigation/exclusion and language-policy review is recorded.

Latest validation results:

- `python -m pytest`: 8 passed.
- Structural inventory validation: passed.
- Strict Step 1 gate: fails only for the three preliminary site statuses, the
  incomplete CERC sitemap/home evidence records, and eight missing required
  CERC contracts.

Exact next action: verify and configure CERC working papers, order archives,
ROP archives, market-monitoring reports, annual reports, other reports, public
hearings, and hearing schedules; add CERC sitemap/home fixtures; then complete
the three site-level navigation/exclusion reviews. Do not begin Step 2 until
the strict inventory gate passes and its evidence is reviewed.

## Earlier pause handoff — 2026-10-22 (superseded)

This section supersedes earlier incremental “exact next action” notes above.

- All required Ministry of Power, Ministry of Coal, and CERC feed IDs are now
  configured with verified history boundaries and representative fixtures.
- The last successful validation before strengthening the site-level gate was
  `python -m pytest` with 8 passing tests; strict validation then reported only
  the three preliminary site statuses.
- The validator now additionally requires a non-empty `coverage_decisions` list
  for every site. Tests were updated for that rule, but the three configuration
  lists have not been written yet, so the current tree is intentionally not
  structurally gate-ready and has not been revalidated after that schema change.
- The Ministry of Power `/sitemap.xml` is unusable for coverage because it lists
  MeitY URLs. Its empty Next.js app shell and the aborted frontend-bundle probe
  are documented in `docs/SOURCE_INVENTORY.md`.
- No Step 2 implementation has started.

Resume with documentation/configuration only: add and review site-level
track/indirect/exclude decisions and evidence for Ministry of Power, Ministry of
Coal, and CERC; capture any missing navigation fixture; reconcile the remaining
preliminary/candidate notes; then rerun tests plus structural and strict
inventory validation. Do not set `research_status: complete` or begin Step 2
until that review is defensible and `--require-complete` passes.

## Pause handoff — 2026-10-22 (site-level coverage review, superseded)

Superseded by the 2026-10-23 handoff below; kept for history.

### Decisions made this session (all user-confirmed, see `docs/DECISIONS.md`)

- D-013: Ministry of Power press releases and Parliament questions are excluded.
- D-014 to D-017: Coal press releases are excluded. Auction/allotment documents
  and the general, production-review, and operationalisation-review minutes are
  tracked. Scrutiny Committee, Single Window System, and Energy Cell pages are
  excluded. Production and Supplies is indirect. coal.nic.in is limited to
  0.25 requests/second with backoff.
- D-018/D-019: CERC tracks its four meeting-minutes pages, the coal price
  indices, and the escalation rates. It excludes repealed regulations, the
  homepage annual archives, technical-validation letters, and the dormant
  correspondence pages.
- GitHub destination: `pattrick14/policy-website-tracker`, configured locally as
  `origin`. Nothing has been committed or pushed. This machine has no GitHub CLI
  or credential, so the repository's existence and privacy are unconfirmed.

### State of the worktree

- `config/sources.yaml`: the Ministry of Power and Ministry of Coal
  `coverage_decisions`, new feeds, and `required_feed_ids` are **merged**.
  - Ministry of Power adds `mop_whats_new_archive` (637 archived posts whose post
    dates are migration artifacts). `mop_documents` gains an archive-status pass
    and a weekly child-term reconciliation.
  - Ministry of Coal adds `coal_auction_allotment`, `coal_minutes_of_meetings`,
    `coal_production_reviews`, and `coal_operationalisation_reviews`, plus a
    site-level `request_rate_per_host_per_second: 0.25`.
- CERC is **not merged**. Its block still has no `coverage_decisions`, so
  `python -m pytest` currently reports 2 failures (7 pass). Both failures are
  `test_repository_inventory_is_structurally_valid` and
  `test_declared_repository_fixtures_exist` (KeyError on CERC), and they clear
  once the CERC list is merged.
- `docs/SOURCE_INVENTORY.md`: the Ministry of Power and Coal site-level review
  sections are merged. The CERC "Step 1 research still required" subsection is
  stale and still needs replacing.
- `tests/fixtures/README.md`: provenance rows for 15 new Ministry of Power and
  9 new Coal fixtures are added. The new CERC fixtures (`repealed_regulations`,
  `meeting_minutes`, `home_archives`, `hindi_mirror`, `site_map_branches`,
  `non_feed_pages`, `price_indices`) exist on disk, but their rows are not yet
  in the README.
- `docs/step1_pending/` holds unmerged research outputs preserved from the
  session. Delete the directory once its contents are merged.
  - `cerc_coverage_decisions.yaml`: a 20-entry fragment from **before** D-018 and
    D-019. It must be revised: remove the three `GAP` coverage limits, mark
    repealed regulations and homepage archives `exclude` (D-019), point the
    minutes entry at the four new feed ids and the tariff entries at the two
    tariff feeds (D-018), and cite the decision ids.
  - `cerc_new_feeds.yaml`: verified contracts for `cerc_commission_minutes`
    (53), `cerc_cac_minutes` (14), `cerc_other_meeting_minutes` (82 rows / 102
    links), `cerc_coordination_forum_minutes` (6), and `cerc_price_indices`.
    The work stopped before `cerc_escalation_rates` was written.
  - `cerc_inventory.md` and `cerc_fixture_rows.md`: pre-decision drafts for the
    inventory and fixture README.
  - `cerc_notes.md`, `coal_notes.md`, `mop_notes.md`: the research agents' notes
    and open items.

### Strict-gate blockers (`--require-complete`)

1. **CERC:** finish the `cerc_escalation_rates` contract and fixture, revise and
   merge the coverage list plus the six new feeds and their `required_feed_ids`,
   then replace the CERC inventory subsection.
   - Host behavior: `cercind.gov.in` timed out or returned empty replies all
     session, while `www.cercind.gov.in` (already allowed) mostly worked, with
     intermittent timeouts of 90 s or more. Record a host-fallback, long-timeout
     (≥120 s), and bounded-retry rule for CERC as a decision and site-level
     config.
2. **Ministry of Coal:** every request, including one at 5 s spacing, received
   the HTTP 200 CAPTCHA page (`tests/fixtures/ministry_of_coal/captcha_block.html`).
   Once the block clears, make about 40 requests at 4 s or longer spacing:
   - verify auction pages 1–8 and the auction archive;
   - verify the minutes archive;
   - check whether the existing feeds (`whats-new`, `acts-rules-policies`,
     `sanction-order-notice`, `national-coal-index`) have archive pages their
     contracts miss;
   - look at the roughly 30 sitemap pages decided from sitemap evidence alone,
     the Hindi variant, and search.
   Then set `coal_auction_allotment` and `coal_minutes_of_meetings` to verified.
   Archive links are `http://www.coal.nic.in//<path>` and need the tested
   HTTPS single-slash rewrite recorded in their contracts.
3. **Ministry of Power:** `/connect/citizen-engagement` is client-rendered, and
   no public data API returned its content (`post-page/pages|page|connect?
   slug_name=citizen-engagement` all returned empty). A person must view it in
   a normal browser to confirm it holds no consultations before the current
   `exclude` decision is final. Everything else on the site passes.
4. Set each site's `research_status: complete` only after its blockers clear.
   Then run `python -m pytest`, `PYTHONPATH=src python -m
   policy_tracker.inventory config/sources.yaml`, and the same command with
   `--require-complete`, and tick the checklist in `docs/SOURCE_INVENTORY.md`.

### Notes for Step 2

- Contracts now use keys that the validator accepts but no schema defines yet:
  `status_passes`, `archive_url_template`, `completeness.removal_rule`,
  `completeness.child_term_reconciliation`, `completeness.notes`, and
  site-level `request_rate_per_host_per_second`. Formalize them when contracts
  are frozen.
- HTTP 200 block pages exist on Coal (CAPTCHA) and Ministry of Power (browser
  403). Dead links on CERC (`recent_orders_rops.html`,
  `Current_petition_status.html` returning 404) are dead links, not feed
  failures.

Step 2 remains blocked until the strict gate passes and the inventory is
reviewed.

## Handoff — 2026-10-23 (CERC merged, superseded)

Superseded by the Step 1 completion handoff below; kept for history.

### Completed

- CERC merged into `config/sources.yaml`: six D-018 feeds
  (`cerc_commission_minutes`, `cerc_cac_minutes`, `cerc_other_meeting_minutes`,
  `cerc_coordination_forum_minutes`, `cerc_price_indices`,
  `cerc_escalation_rates`), their `required_feed_ids` (25 in total), the
  21-entry `coverage_decisions` list revised for D-018/D-019, and the D-020
  site-level host-fallback keys. CERC is now `research_status: complete`.
- The CERC inventory section, fixture README rows, and D-020 were already in
  the docs. The merged `docs/step1_pending/cerc_*` fragments are deleted.
- The Ministry of Power Citizen Engagement check was resolved earlier
  (`docs/SOURCE_INVENTORY.md`, "Citizen Engagement resolution"), and Ministry
  of Power is `research_status: complete`.

### Validation results

- `python -m pytest`: 9 passed (the two CERC KeyError failures are cleared).
- `PYTHONPATH=src python -m policy_tracker.inventory config/sources.yaml`: valid.
- The same command with `--require-complete` fails only with
  `sites[1].research_status: research gate requires 'complete'` (Ministry of
  Coal).

### Remaining blocker: Ministry of Coal

Blocker 2 in the 2026-10-22 handoff is out of date. Two re-verification passes
earlier on 2026-10-23 (75 requests, no CAPTCHA) verified `coal_auction_allotment`
and `coal_minutes_of_meetings`, along with archives, pagers, Hindi, and search.
Those results are already in the `ministry_of_coal` block; see
`docs/step1_pending/coal_reverification.md`. What remains:

1. **A product decision on `coal_gasification`.** The pass found that
   `/major-statistics/national-coal-gasification-mission` is an active scheme
   dossier (59 rows, RFPs dated 13.08.2026), and added it as a tracked,
   verified feed. Either record it as a decision (track, consistent with D-015)
   or revert it to `indirect`; the revert steps are in `coal_reverification.md`.
2. Record the seven never-fetched safety and SDC narrative pages as an accepted
   coverage limit. They are already listed as such in `coal_reverification.md`.
3. Merge that file's SOURCE_INVENTORY replacement text and fixture README rows.
   Then set Coal `research_status: complete`, delete `docs/step1_pending/`,
   rerun the three validation commands, and tick the checklist in
   `docs/SOURCE_INVENTORY.md`.

A live probe on 2026-10-23 at 12:25 IST could not reach `www.coal.nic.in`: the
cloud environment's egress proxy refused the tunnel (HTTP 403). That is the
environment's network policy, not the site's CAPTCHA. Any further live Coal
check needs that host added to the environment's allowed domains.

Step 2 stays blocked until Coal is complete and the inventory is reviewed.

### Notes for Step 2 (additional)

- Formalize the D-020 site-level keys (`rate_limit_shared_across_hosts`,
  `canonical_host`, `fallback_hosts`, `host_fallback_on`,
  `max_attempts_per_url`, `retry_backoff_seconds`,
  `abort_after_consecutive_failed_urls`, and the site-level
  `request_timeout_seconds`) alongside the keys already listed above.
- Step 3 fetcher tests need recorded fault cases for each D-020 fallback
  trigger. The empty-reply trigger was observed only on 2026-10-22.
- `tests/fixtures/cerc/non_feed_pages.html` still holds escalation and
  coal-price excerpts from before D-018. They are harmless and can be trimmed.

## Handoff — 2026-10-23 (Step 1 complete; superseded)

Superseded by the Step 2 handoff below; kept for history.

- D-021 (user-confirmed): the Coal Gasification mission page is `indirect`.
  The `coal_gasification` feed was removed; What's New captures its
  announcements, and files posted only on that page are a recorded limit.
- The Coal re-verification results (auction/allotment and minutes verified with
  archives and the HTTPS single-slash rewrite; pagers and archives added for
  What's New and seven other feeds) were already in config. Their inventory
  text and fixture README rows are now merged. The seven never-fetched safety
  and SDC narrative pages are an accepted coverage limit.
- Ministry of Coal is `research_status: complete`, so all three sites are.
- `docs/step1_pending/` is merged and deleted.
- Validation: `python -m pytest` 9 passed; structural validation valid;
  `--require-complete` valid (exit 0).
- The completion checklist in `docs/SOURCE_INVENTORY.md` is ticked except
  "Inventory reviewed before Step 2 begins", which needs the user.

Step 2 must not start until the user has reviewed the inventory. The Step 2
notes in the two earlier handoffs above (unschematized keys, fault-case tests,
Coal identity rules for mixed-scheme and doubled-slash coal.gov.in hrefs, and a
note that full Coal enumeration costs about 90 requests, roughly 6 minutes at
0.25 req/s) still apply.

## Handoff — 2026-10-23 (Step 2 complete; superseded)

Superseded by the Steps 3–8 handoff below; kept for history.
The user approved the Step 1 inventory on 2026-10-23.

### Delivered

- **Frozen contracts:** `core/models.py` (versioned records, `SCHEMA_VERSION`
  1), `core/observations.py` (adapter output plus completeness evidence),
  `core/contracts.py` (`FeedContract`, `FetchPolicy`, `RunContext`),
  `core/identity.py` (native ID, then canonical URL, then provenance hash), and
  `adapters/base.py` (`Adapter`, `Fetcher`, `AdapterRegistry`).
- **Config (D-022):** `core/config.py` loads 54 tracked feeds into typed
  contracts. The inventory schema is closed and types every formerly
  unschematized key. Only tested host mirrors alias to a canonical host, so
  coal.gov.in file hrefs keep their raw identity.
- **Engine (D-023):** `core/engine.py` is pure. It covers per-feed silent
  baselines, backfill by `coverage_revision`, appearances, two-run removal on
  authoritative feeds only, reappearance, quarantine (block page, parse, fetch,
  MIME, collapse, unexpected empty, stale), parser migrations, and
  deterministic event ids. See `docs/STATE_ENGINE.md`.
- **Persistence:** `core/serialize.py` and `core/migrations.py` provide
  deterministic JSON and a versioned migration registry. `operations/store.py`
  implements the tracker-data layout with atomic writes and compare-and-swap
  promotion. See `docs/DATA_LAYOUT.md`.
- **Operations:** `operations/runner.py` applies feeds live-first and records
  rebaseline reasons in the run manifest. `operations/cli.py` provides
  `policy-tracker validate-config | init-data | baseline | run [--dry-run] |
  rebaseline-feed`. `_stage_delivery()` is the seam for the Step 7 outbox.

### Validation

- `python -m pytest -q`: 419 passed.
- `ruff check src tests`: clean.
- `policy-tracker validate-config --config config/sources.yaml`: valid, 54
  tracked feeds. The strict inventory gate passes.
- Smoke test: `init-data`, then `run --dry-run`; every feed fails as
  `no_adapter` and nothing is promoted, as expected before Steps 3–5.

### Notes for Steps 3–5

- Adapters must emit ISO 8601 UTC `observed_at` in one format; the engine's
  stale-observation guard compares the strings.
- Report fetch, parse, block-page, MIME, and pagination evidence separately,
  and set `enumeration_complete` only when the contract's termination rule is
  met.
- Set `ObservedDocument.matter_native_id` only on explicit source evidence, and
  `section` for current/archive passes so moves are status changes.
- The fetcher must apply `FetchPolicy` (Coal 0.25 req/s and CAPTCHA detection,
  CERC D-020 fallback and circuit breaker) and needs recorded fault cases in
  its tests.
- A parser change bumps the feed's `parser_version` in config.

## Authoritative handoff — 2026-10-23 (Steps 3–8 complete offline)

This section supersedes every earlier handoff and "exact next action" above.

### Delivered

- **Fetching (D-024):** `adapters/http.py` (`HttpFetcher`, per-site
  `fetcher_for`), `adapters/blocks.py` (content-based block pages), and
  `adapters/replay.py` (`ReplayFetcher`). See `docs/FETCHING.md`.
- **Adapters (D-025):** `ministry_of_power.py` (3 feeds), `ministry_of_coal.py`
  (27), and `cerc.py` (24), all registered in `adapters/registry.py`.
- **Enrichment (Step 6):** `documents/` covers full first acquisition with
  signature checks, sandboxed pypdf extraction, OCR fallback (reports
  `ocr_unavailable` when tesseract is absent), language detection, conditional
  revalidation, and `content_replaced`. Ministry of Power attachment records
  resolve to files. Every run rechecks `TRACKER_REVALIDATE_DOCUMENTS` (150)
  documents, least recently verified first.
- **Reporting (D-026):** `reporting/` covers rule-based classification from
  `config/taxonomy.yaml`, evidence-backed deadlines, the digest, and the
  Markdown/JSON/CSV/`open_deadlines.csv`/email renderers.
- **Operations (D-027):** `operations/` covers the outbox, SMTP (STARTTLS or
  SSL only), compare-and-swap `tracker-data` publishing, the durable delivery
  sequence, and the CLI. `.github/workflows/` holds `tracker.yml`,
  `init-data-branch.yml`, and `ci.yml`. See `docs/OPERATIONS.md`.
- **Acceptance (Step 8):** `tests/acceptance/` runs 14 offline drills through
  the real code path (`docs/ACCEPTANCE.md`).

### Review and fixes

An adversarial review found 11 issues, all fixed with regression tests:

- **Ownership (D-023 revised):** authoritative feeds now own records over
  rolling bulletins.
- **Coal completeness:** empty pager pages and unpaged feeds that gain a pager
  can no longer count as complete.
- **Baseline mode:** it skips feeds that are already live.
- **Fetcher (HTTP):**
  - decompressed size is bounded;
  - each request has a total deadline;
  - https-to-http redirects and non-default ports are refused.
- **Delivery pipeline:**
  - an interrupted promotion now finishes instead of resending forever;
  - dry runs no longer reset the data checkout;
  - health is published only at promotion;
  - the push token stays out of `.git/config` and child processes.
- **Smaller fixes:** only open deadlines promote an item to Action required;
  `content_replaced` is gated on feeds live before the run; Ministry of Power
  `group_index` and `post_modified` no longer cause noise.

The acceptance suite found two further bugs, both fixed: the open-deadlines
view drifting on reruns, and files not being revalidated under an unchanged
listing.

### Validation

- `python -m pytest -q`: 896 passed, 4 skipped (live-only).
- `ruff check .`: clean.
- `policy-tracker validate-config --config config/sources.yaml`: valid, 54
  tracked feeds.
- Live dry run from this environment: every site was refused by the sandbox
  proxy, so every feed failed with recorded reasons and nothing was promoted,
  as designed.

### Known limits and open items

- **Live checks not done:** page shapes and API responses have not been
  rechecked against the live sites. Adapters rest on the Step 1 fixtures, and
  several feeds have thin fixtures (listed in the Step 4 and 5 notes in the
  commit history and `docs/ACCEPTANCE.md`).
- **Classification:** the taxonomy rules are first-pass heuristics, to be
  tuned against the first live digests.
- **Push token:** it stays in the main process's memory during a run (see
  `docs/OPERATIONS.md`).
- **D-008:** a server that replaces bytes but keeps its validators and answers
  304 is not detected.
