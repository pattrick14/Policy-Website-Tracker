# Step 8 Acceptance and Failure Drills

PROJECT_PLAN Step 8 asks for a silent live baseline, a comparison of feed totals
with the inventory, a controlled email delivery, and failure drills. This file
records how each item is verified. Items that can run offline are deterministic
tests in `tests/acceptance/`. Items that need the live sites or a real mailbox
are operator steps (section 4), and each one is also a visible `skip` in the test
report.

```text
python -m pytest -q tests/acceptance      # about 35 s; no network, no SMTP server
ruff check tests/acceptance
```

## 1. The offline harness

`tests/acceptance/acceptance_harness.py` runs the production code path end to end.
Only the network and the SMTP server are replaced.

| Production part | In the harness |
|---|---|
| `config/sources.yaml` contracts | Real file. `conftest.py` caches the parsed YAML per file version, which saves about 0.5 s per run. |
| Adapter registry | `adapters.registry.default_registry()` (real MoP, Coal, and CERC adapters). |
| Site fetchers (`http.fetcher_for`) | `WorldFetcher`, one per run, built on `ReplayFetcher`. |
| Enrichment | Real `documents.enrich.enrich` and `Extractor`. PDF parsing runs in the real bounded subprocess. OCR binaries are reported missing, so results do not depend on the machine. |
| Digest and renderers | Real `build_digest` and `cli.default_renderer()` (Markdown, JSON, CSV, `open_deadlines.csv`, email). |
| Data branch | Real `DataStore`. Publish-mode drills use `GitPublisher` with a local bare remote (`tests/operations/ops_helpers.py`). |
| SMTP | `FakeSender` (scripted accept, partial, refuse, or timeout), or a patched `smtp.send` for CLI drills. |
| Clock | Injected `Clock`. Each call returns the next second, and a weekly run is a clock jump. Nothing sleeps. |

`World` describes what the three sites serve. Each **replayed** feed's URLs map to
the existing sanitized fixtures. The URL→fixture maps are imported from the adapter
tests (`DOCUMENT_ROUTES`, `default_pages()`, the Coal route tables), so the two
suites cannot drift apart. Replayed feeds:

- **Full world:** 36 feeds. These are all 24 CERC feeds, 9 Coal feeds
  (`coal_quarterly_reports`, `coal_acts_rules_policies`, `coal_production_reviews`,
  `coal_operationalisation_reviews`, `coal_national_coal_index`,
  `coal_compensation_orders`, `coal_minutes_of_meetings`,
  `coal_termination_orders`, `coal_whats_new` with its 41-page pager), and 3 MoP
  feeds (`mop_documents`, `mop_whats_new`, `mop_whats_new_archive`).
- **Core world:** 10 feeds from all three sites (`CORE_FEEDS`). Multi-run drills
  use it to stay fast.

Every other tracked feed gets a fetcher that fails with `connection_refused`, the
same as an unreachable site. **So every offline digest is `partial`.** The drills
check the specific health row they care about, never only the partial flag.

Enrichment downloads get tiny PDFs generated in memory. A drill can override one
URL's bytes and validators. MoP attachment-record lookups get a synthetic record
that points at such a PDF. No source document is written to the repository.

## 2. Drill results

Status as of 2026-10-23: 34 tests pass (the two bugs in section 3 are fixed),
and 4 are skipped (live-only, section 4).

| # | Drill | Test (`tests/acceptance/…`) | What is asserted | Status |
|---|---|---|---|---|
| 1 | Silent baseline | `test_baseline_and_delivery.py::test_drill1_…` | With SMTP configured: no send, outbox entry, digest, or event. All 36 replayed feeds are `COMPLETE`. For each feed, `last_item_count` equals the number of its appearances and the distinct-record count recomputed from a direct adapter observation. CERC document totals equal the adapter tests' `EXPECTED_COUNTS`. The 18 unreachable feeds stay `NOT_STARTED` with one failure. | pass |
| 2 | First live item | `…::test_drill2_…` | A Coal row is added and its PDF says "comments … on or before 30.11.2026". Result: exactly one live `NEW_DOCUMENT`, with deadline `2026-11-30` (`text_regex`). The archive files are written, the outbox is `DELIVERED`, the catalog is promoted, `open_deadlines.csv` has the row, and the email has the item with the deterministic Message-ID. No recipient address appears in any data file. | pass |
| 3 | Identical rerun | `…::test_drill3_…` | Three reruns plus a dry run produce no events. The email is a heartbeat. The only event in the whole archive is the drill-2 `NEW_DOCUMENT`. | pass |
| 3b | Rolling view stable on rerun | `…::test_open_deadlines_view_is_stable_…` | `open_deadlines.csv` is unchanged by an identical rerun. | pass (bug 1 fixed) |
| CLI | Production entry point | `…::test_cli_publish_mode_…` | `init-data-branch`, `baseline --publish --no-email`, `run --publish` (new item), `run --publish` (heartbeat). Checks the exact commit sequence on the remote, and that no secret or recipient appears in stdout or stderr. | pass |
| 4 | Malformed 200 | `test_feed_failures.py::test_drill4_…` (3 cases) | Cases: an MoP 200 block page, a Coal 200 CAPTCHA, and a block page in the middle of MoP pagination. Each time, that feed is quarantined `block_page`. Its appearances, `last_complete_run_id`, and `last_item_count` are unchanged, and `consecutive_failures` is 1. A real change on another feed in the same run is reported and that feed advances. The digest is partial, the health row says `block_page`, and the email shows the problem line. | pass |
| 5 | Partial pagination | `…::test_drill5_…` (3 cases) | Cases: a Coal pager page returns 404, an MoP API page has its connection reset, and a Coal archive pager loses its Last link (the run is accepted but partial, and the archive's page 2 is never seen). After three runs: no `WITHDRAWN`, every appearance is present with `missing_complete_runs == 0`, and the feed's last complete run is still the baseline. | pass |
| 6 | Recovered baseline | `…::test_drill6_feed_failing_…` | A feed is down during the baseline and the first weekly run. It then recovers with a row that was published while it was down. Result: no events for that feed, a heartbeat with "Coverage initialized for …", and every one of its records has `BASELINE` discovery. A later addition is then `LIVE`. | pass |
| 6b | Operator rebaseline | `…::test_drill6_operator_rebaseline_…` | After `policy-tracker rebaseline-feed … --reason "site redesign"`, the next run absorbs a new draft file silently. The reason is recorded in `health/feeds.json`. | pass |
| 7 | Old-parent attachment | `test_matters_and_removals.py::test_drill7_…` (CERC draft, MoP parent) | One live `NEW_DOCUMENT` whose `matter_key` is the existing baseline matter, with no `NEW_MATTER`. The parent keeps its first sighting, and its older files stay linked. | pass |
| 8a | Rolling disappearance | `…::test_drill8_rolling_…` | A CERC home item and an MoP What's New post are missing for three complete runs. No events, the records stay `ACTIVE`, and the counters stay 0. | pass |
| 8b | Authoritative disappearance | `…::test_drill8_authoritative_…` | Run sequence: missing (counter 1), CAPTCHA run (counter still 1), missing again (exactly one `WITHDRAWN`, shown as "Removed from source"), missing again (no event), back (`REAPPEARED`, record `ACTIVE`), then a quiet rerun. | pass |
| 9a | Validator limitation (D-008) | `test_documents.py::test_drill9_same_validators_…` | The bytes are replaced but the server keeps its ETag and Last-Modified and answers 304. The change is **not detected**: the old hash stays, a `conditional_get_304` confirmation is recorded, and the D-008 note appears in the digest and email. This documents the limitation; it is not a bug. | pass |
| 9b | New validators and new bytes | `…::test_drill9_new_validators_and_new_bytes_…` | With `select="all"` revalidation, one `CONTENT_REPLACED` ("File replaced (server validators changed)") is reported, once only. | pass |
| 9c | Revalidation through a listing change | `…::test_drill9_listing_change_…` | With production services, a title edit makes enrichment revalidate the file, which reports `METADATA_CHANGED` and `CONTENT_REPLACED`. | pass |
| 9d | Revalidation without a listing change | `…::test_drill9_new_validators_detected_in_production_…` | With production services, a file replaced with new validators under an unchanged listing is detected. | pass (bug 2 fixed) |
| 10 | OCR failure | `…::test_drill10_…` (scanned, corrupt) | The record is kept with its English listing metadata. The digest warnings are `scanned` + `ocr_unavailable`, or `extraction_failed`, and the email shows "Warning: …". | pass |
| 11a | SMTP refused, partial, or timeout | `test_delivery_failures.py::test_drill11_failed_send_…` (3 cases) | Exit 3. The delivery stays pending and the catalog is not promoted. The attempt is recorded as counts only. A second failing run retries the same bundle and Message-ID and makes **no** request to any site. A successful run sends the stored bundle first, then crawls and sends a heartbeat. There is one `NEW_DOCUMENT` overall. | pass |
| 11b | Crash after SMTP accepted | `…::test_drill11_crash_…` (local, publish) | The runner is killed before the success marker is written or pushed. The next run sends the identical bundle again with the same Message-ID (at-least-once), then marks it delivered and promotes. | pass |
| 12a | Schema migration | `test_migrations_and_gaps.py::test_drill12_v0_catalog_…` | A synthetic v0 catalog is refused when no migration step exists. With `MIGRATIONS[0]` registered (the monkeypatch pattern from `tests/core/test_migrations.py`), it loads equal to the original state. The run emits no events, the counters survive, and the files are written back at the current version with a verified hash. | pass |
| 12b | Parser-version bump | `…::test_drill12_parser_version_…` | Coal `PARSER_VERSION` goes to 2 while a title also changes. There is no `METADATA_CHANGED` or `STATUS_CHANGED`, the new title is applied, the new row is still alerted, and the diagnostic reads "parser migration 1 -> 2". The next run reports real edits again. | pass |
| 13 | Branch race | `…::test_drill13_branch_race_…` | Through the CLI with `--publish`, another writer pushes mid-run. Exit 4, no SMTP call, the remote head is the other writer's commit (whose parent is the old head), and no outbox bundle is on the remote. The next run starts from the new head and delivers. | pass |
| 14 | Gap over ten days | `…::test_drill14_gap_…` | A weekly run has no gap. An outage week (all feeds failed, exit 1, no email) is followed by a run that flags the 14-day gap in the JSON, Markdown, text, and HTML. The next weekly run has no gap. A pending delivery retried two weeks later also flags its gap. | pass |
| — | Cosmetic noise | `test_noise.py` | One run combines whitespace in text nodes, reordered rows, reordered MoP posts with new `menu_order`, Coal anti-bot `bnith__` tokens, CERC served from the `www` fallback host, and a row repeated on a page. It produces no events and a heartbeat. | pass |

## 3. Bugs found and fixed

1. **The open-deadlines view changes on an identical rerun.** The bug is in
   `reporting/digest.py`, `_open_deadlines`. In the week a record is reported, its
   open-deadline row reuses the digest item, whose category `_item` promoted to
   `action_required` (`deadline:found`). In later weeks the record is not an item,
   so `_open_deadlines` calls `classify()` directly without that promotion, and
   the category becomes, for example, `research_and_reporting`. As a result,
   `open_deadlines.csv` changes between two runs of identical source content.
   Test: `test_open_deadlines_view_is_stable_across_identical_reruns`.
2. **Production never revalidates an already-hashed document under an unchanged
   listing.** The cause is in `operations/cli.py`, `default_services`, which sets
   `enrich_options = {"max_documents": …}` only. `documents.enrich.enrich`
   therefore runs with `select="changed"` and selects only documents touched by a
   `NEW_*`, `METADATA_CHANGED`, or `REAPPEARED` event, plus unversioned documents.
   A file replaced with new ETag, Last-Modified, and bytes is never requested
   again, so the D-008 validator check never runs. This contradicts OPERATIONS
   ("caps how many documents enrichment downloads or revalidates per run") and
   PROJECT_PLAN ("Later, use ETag/Last-Modified and conditional requests").
   A fix also needs a rotation: with `select="all"` the cap always takes the
   same first N sorted keys, so documents later in the sort order would never be
   revalidated. Test:
   `test_drill9_new_validators_detected_in_production_without_listing_change`.

Fixes (2026-10-23): `_open_deadlines` now classifies every row from the record
alone and applies the same `deadline:found` promotion, so the view is stable.
Enrichment takes `revalidate=N` and adds the N least recently verified versioned
documents to each run (`TRACKER_REVALIDATE_DOCUMENTS`, default 150), so every
file is rechecked on a rotating schedule.

## 4. Live-only acceptance (operator steps)

These steps need GitHub Actions or a machine that can reach `www.powermin.gov.in`,
`www.coal.nic.in`, and `cercind.gov.in`/`www.cercind.gov.in`, plus SMTP
credentials. `tests/acceptance/test_live_only.py` lists each one as a skip.

**L1. Silent live baseline.** In GitHub, run Actions → Weekly tracker → Run
workflow with `mode=baseline`, or use
`gh workflow run tracker.yml -f mode=baseline`. Repeat until every feed is
complete. Each run hashes at most `TRACKER_MAX_DOCUMENTS` documents. Locally:

```text
policy-tracker validate-config --config config/sources.yaml
policy-tracker init-data --data-dir /tmp/tracker-live
policy-tracker baseline --config config/sources.yaml --data-dir /tmp/tracker-live --no-email
```

Pass criteria: exit 0, no `outbox/<run_id>/` directory, no `digests/` files, no
email, and every feed `complete` in `health/feeds.json`. Coal needs about 6
minutes at 0.25 requests per second.

**L2. Live totals against the inventory.** Print each feed's baseline status and
distinct-record count:

```text
python -c "import json; r=json.load(open('/tmp/tracker-live/catalog/feeds.json'))['records'];
[print(k, v['baseline_status'], v['last_item_count']) for k, v in sorted(r.items())]"
```

Compare each count with `docs/SOURCE_INVENTORY.md`. The count is distinct
matters plus documents, so compare it with distinct hrefs, not raw rows. For
example: `coal_auction_allotment` has 406 distinct current hrefs plus 40 archive
rows, `coal_whats_new` has 2042 rows, `coal_minutes_of_meetings` has 23 + 93,
`coal_national_coal_index` has 82 + 4, `mop_whats_new` has 26 posts plus their
files, and `mop_whats_new_archive` has 637 posts. The MoP category parents are
Reports 9, Acts and Policy 7, and Orders and Notices 4. Record any difference and
its explanation in `docs/SOURCE_INVENTORY.md`.

**L3. Controlled real email.** Point the SMTP settings at a test mailbox. Use a
`SMTP_TO` that holds only that mailbox, then make a live run after the baseline:

```text
export SMTP_HOST=… SMTP_PORT=465 SMTP_SECURITY=ssl SMTP_USERNAME=… SMTP_PASSWORD=…
export SMTP_FROM=tracker@<domain> SMTP_TO=<test mailbox>
policy-tracker run --config config/sources.yaml --data-dir /tmp/tracker-live
policy-tracker outbox-status --data-dir /tmp/tracker-live
```

Check that the message arrives with HTML and text parts, `To:
undisclosed-recipients:;`, `Message-ID: <<run_id>.digest.policy-tracker@<domain>>`,
the attached CSVs (under 5 MiB), and the heartbeat wording. Then set a wrong
`SMTP_PASSWORD` and rerun after a source change. Expect exit 3 and a pending
entry in `outbox-status`. Restore the password and rerun. The same Message-ID
must arrive once more, followed by that week's heartbeat.

**L4. Live block pages and rate limits.** After L1 and the first weekly runs,
check `health/feeds.json` and the run manifests (`runs/*.json`,
`context.fetchers`). Any `block_page` quarantine must leave that feed's records
unchanged, and none may appear as an empty list.

Operators must also enable GitHub workflow-failure notifications. Email cannot
report its own outage (PROJECT_PLAN acceptance criteria).

## 5. Other observations (not failing tests)

- **Resolved after review (2026-10-23):** the MoP `post_modified` timestamp is
  no longer matter metadata, so editing a parent post no longer adds a "Listing
  details changed" item; MoP digest links use the file URL that enrichment
  resolved; and `core/serialize._hints` is cached in production.
- **Offline digests are always partial.** Feeds without fixtures are unreachable
  offline, so every offline digest is `partial`, as section 1 explains.
