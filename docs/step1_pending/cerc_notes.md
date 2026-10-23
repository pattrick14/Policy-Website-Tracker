# CERC Step 1 notes (updated 2026-10-23)

This note supersedes the 2026-10-22 version, which was written before D-018 and
D-019. All probes were read-only GETs or HEADs, spaced at about one request
every 1.5 s, and no PDFs were downloaded. The raw HTML is kept outside the
repository in the session scratchpad.

## Files in this directory

| File | State | Merge target |
|---|---|---|
| `cerc_new_feeds.yaml` | Six verified contracts at 6-space list indentation | Append to the `cerc` block's `feeds` list in `config/sources.yaml` |
| `cerc_coverage_decisions.yaml` | 21 entries (8 track, 1 indirect, 12 exclude), revised for D-018/D-019 | Insert as the `cerc` block's `coverage_decisions` (4-space key indentation) |
| `cerc_host_rule.md` | Observed host behaviour, proposed site-level keys, and proposed D-020 text | Site-level keys go in the `cerc` block; D-020 goes in `docs/DECISIONS.md` once the user accepts it |
| `cerc_inventory.md` | Replacement for the CERC "Step 1 research still required" subsection | `docs/SOURCE_INVENTORY.md` |
| `cerc_fixture_rows.md` | Eight provenance rows | `tests/fixtures/README.md` |

## Exact `required_feed_ids` to add to the `cerc` block

```yaml
      - cerc_commission_minutes
      - cerc_cac_minutes
      - cerc_other_meeting_minutes
      - cerc_coordination_forum_minutes
      - cerc_price_indices
      - cerc_escalation_rates
```

These are appended after `cerc_statutory_advice`, which brings the list to 25
IDs.

## Validation performed (2026-10-23)

- Each of the six contracts was parsed after dedenting and passed
  `_validate_feed(..., require_complete=True)` with no errors.
- All 21 coverage entries passed
  `_validate_coverage_decision(..., require_complete=True)` with no errors.
- Every fixture path referenced by the feeds and the coverage entries exists.
- No `GAP` text remains in the coverage list.
- A dry run in memory, with nothing written to disk, merged the fragment, the
  six feeds, and the six IDs into the loaded `config/sources.yaml`. It also set
  `research_status: complete`. `validate_inventory` reported zero CERC errors,
  both without and with `require_complete=True`.
- Fetched again on 2026-10-23, the drafted contracts' boundaries mostly matched:
  - `Minutes_comm.html`: 53 ✓.
  - `AD_minutes.html`: 14 ✓.
  - `Minutes_Coord.html`: 6 ✓.
  - `coal_price.html`: 4 ✓. `coal_price_index_base.html`: 5 ✓.
  - **Correction:** `other_minutes.html` has **106 attachment links (101 unique
    hrefs)**, not the drafted 102 (98). The page is byte-identical to the
    2026-10-22 download, so the draft had miscounted. The contract is updated.
- None of these pages carries an absolute `http://` document link. The
  escalation tree has one, and it is recorded in that contract.

## Changes made this session

1. **`cerc_escalation_rates` written and verified live.**
   - Fetched the index, all 22 year pages, and all 32 generation and
     transmission leaf pages.
   - Found 319 PDF links (318 unique hrefs):
     - generation 2011–2026: 211;
     - transmission 2011–2026: 72;
     - combined 2005–2010 year pages: 36.
   - Notification dates run from 20-09-2005 to 01-10-2026.
   - Two layouts:
     - 2011–2026 year pages are hubs, named `escaltion_YYYY.html` in the site's
       own spelling;
     - 2005–2010 year pages (`escalation_rates_YYYY.html`) list documents
       directly;
     - generation pages from 2022 use collapsed Annual, Six-Monthly, and Monthly
       accordion panels.
   - Parser hazards:
     - filenames with spaces;
     - one absolute `http://cercind.gov.in/` link (HTTPS rewrite tested, HTTP
       200 `application/pdf`);
     - one href repeated with different text;
     - same-date transmission variants (current and "Old SBDs");
     - commented-out stale hrefs, ignored (2 each on gen 2015, tran 2015, tran 2017, and
       tran 2018).
   - Fixture: `tests/fixtures/cerc/escalation_rates.html` (sanitized,
     structure only).
2. **Coverage list revised for D-018 and D-019.**
   - Repealed regulations and the homepage annual archives are now `exclude`.
   - The home bulletin and the archives are split into two entries.
   - The minutes entry points at the four minutes feeds, and the merged
     tariff-input entry points at `cerc_price_indices` and
     `cerc_escalation_rates`.
   - Every excluded entry states that bulletin appearances remain captured.
   - The archives entry states the known limit that older consultations
     surviving only there are not baselined.
   - The press-release entry states that comparing the sitemap's links would
     not notice a revived series.
   - The Acts and Policies entry records that the Ministry of Power feeds are
     relied on for the frozen copies.
   - The consolidated-regulations entry no longer claims `Repeal_Reg.html` is
     tracked.
3. **`last_verified` updated.** The five drafted contracts now carry
   `2026-10-23`, after the recount.
4. **Host-fallback rule drafted** in `cerc_host_rule.md`, as proposed decision
   D-020.

## Still unverified or open

- **D-020 is proposed, not accepted.** The host rule needs user confirmation
  before it goes into `docs/DECISIONS.md`. Its site-level keys
  (`request_timeout_seconds`, `canonical_host`, `fallback_hosts`,
  `host_fallback_on`, `max_attempts_per_url`, `retry_backoff_seconds`,
  `abort_after_consecutive_failed_urls`, `rate_limit_shared_across_hosts`) are
  accepted by the validator but have no schema yet. Step 2 should formalize them
  together with the other unschematized keys listed in the handoff.
- **Fallback triggers lack fault-injection tests.** The trigger list rests on
  observed failures: TLS-handshake timeouts and connection resets. An empty
  reply was observed on 2026-10-22 only. Step 3 fetcher tests should cover each
  trigger with recorded fault cases, not live calls.
- **The Hindi mirror is checked from one sample.** The "no Hindi-only document"
  finding rests on `Draft_reg_hin.html` alone. The coverage entry's quarterly
  comparison is the safeguard.
- **`non_feed_pages.html` holds pre-decision excerpts.** It still contains
  escalation and coal-price excerpts from before D-018. They are harmless
  because both families now have dedicated feed fixtures. Whoever merges may
  trim them from that fixture.
- Nothing about the escalation contract is unverified. It was fetched live and
  is marked `verified: true`; it was not written from cached evidence.

## Recommendation on `research_status`

**CERC can be marked `research_status: complete`** once the following merges
land in one change:

- the 21-entry coverage list;
- the six feeds;
- the six `required_feed_ids`;
- the fixture README rows;
- the inventory subsection.

Every sitemap and homepage branch has an explicit decision. Every tracked
family has a verified contract with a fixture and a verified history boundary.
The strict dry-run gate passes.

The host rule (D-020) is an operational requirement for Step 3 fetchers, not a
coverage gap. It should be accepted and recorded at the same time, but it does
not block the research gate. If the user prefers to keep CERC `preliminary`
until D-020 is accepted, that is the only remaining reason to wait.

After merging, run:

- `python -m pytest`;
- `PYTHONPATH=src python -m policy_tracker.inventory config/sources.yaml`;
- the same command with `--require-complete`.

The two current CERC test failures (KeyError on the missing
`coverage_decisions`) should clear. The strict gate will still fail on the
Ministry of Coal and Ministry of Power blockers, which are separate from CERC.
