# Ministry of Coal — Step 1 re-verification (2026-10-23)

This pass followed the CAPTCHA block recorded on 2026-10-22 (IMPLEMENTATION_STATUS
blocker 2). It ran under D-017, with a hard budget of 45 requests, at least 4.5 s
between the end of one response and the start of the next request, and a stop on the
first CAPTCHA page. **No request returned the CAPTCHA page.** All 45 returned HTTP 200 with
real content. I used the whole budget.

Main finding: the 2026-10-22 contracts missed pagers or archives on **eight
feeds already marked verified**. These are What's New (41 pages), compensation
orders, National Coal Index, Monthly Statistics, Statistical Report, Monthly Summary
for Cabinet, Monthly Reports for Cabinet, and termination orders. I fixed seven of them
in `config/sources.yaml`. I set `coal_termination_orders` back to unverified because its
archive was found but not fetched.

## Request log

Times are local request start times (IST), truncated to the second. The user agent was
`Mozilla/5.0 (Windows NT 10.0; Win64; x64) PolicyTracker-Step1-research`. CAPTCHA
detection looked for `Validation request` or `captcha_resp` in the body. Raw HTML stays in
the session scratchpad and is not in the repository.

| # | Time | URL (https://www.coal.nic.in) | Status | Bytes | CAPTCHA |
|---|---|---|---|---|---|
| 1 | 11:17:52 | `/nominated-authority/auction-allotment` | 200 | 93,216 | no |
| 2 | 11:17:57 | `/nominated-authority/auction-allotment?page=1` | 200 | 94,148 | no |
| 3 | 11:18:02 | `/nominated-authority/auction-allotment?page=2` | 200 | 92,961 | no |
| 4 | 11:18:07 | `/nominated-authority/auction-allotment?page=3` | 200 | 94,722 | no |
| 5 | 11:18:12 | `/nominated-authority/auction-allotment?page=4` | 200 | 94,578 | no |
| 6 | 11:18:17 | `/nominated-authority/auction-allotment?page=5` | 200 | 93,550 | no |
| 7 | 11:18:22 | `/nominated-authority/auction-allotment?page=6` | 200 | 94,815 | no |
| 8 | 11:18:27 | `/nominated-authority/auction-allotment?page=7` | 200 | 96,028 | no |
| 9 | 11:18:32 | `/nominated-authority/auction-allotment?page=8` | 200 | 72,445 | no |
| 10 | 11:18:36 | `/nominated-authority/auction-allotment?page=9` | 200 | 56,243 | no |
| 11 | 11:18:41 | `/nominated-authority/archive-auction-allotment` | 200 | 75,457 | no |
| 12 | 11:19:48 | `/public-information/minutes-metings/archive-minutes-metings` | 200 | 86,944 | no |
| 13 | 11:19:52 | `/whats-new` | 200 | 90,382 | no |
| 14 | 11:19:57 | `/acts-rules-policies` | 200 | 188,067 | no |
| 15 | 11:20:02 | `/nominated-authority/sanction-order-notice` | 200 | 94,345 | no |
| 16 | 11:20:07 | `/nominated-authority/national-coal-index` | 200 | 92,344 | no |
| 17 | 11:20:11 | `/public-information/monthly-statistics-at-glance` | 200 | 75,397 | no |
| 18 | 11:20:16 | `/public-information/statistical-report` | 200 | 75,918 | no |
| 19 | 11:20:21 | `/public-information/monthly-summary-cabinets` | 200 | 74,655 | no |
| 20 | 11:20:26 | `/nominated-authority/auction-allotment?page=2` (determinism re-fetch) | 200 | 92,961 | no |
| 21 | 11:20:30 | `/nominated-authority/auction-allotment?page=3` (determinism re-fetch) | 200 | 94,722 | no |
| 22 | 11:21:58 | `/public-information/minutes-metings/archive-minutes-metings?page=1` | 200 | 82,053 | no |
| 23 | 11:22:03 | `/whats-new?page=40` | 200 | 81,318 | no |
| 24 | 11:22:08 | `/nominated-authority/sanction-order-notice?page=4` | 200 | 87,263 | no |
| 25 | 11:22:13 | `/nominated-authority/archive-sanction-order-notice` | 200 | 82,926 | no |
| 26 | 11:22:18 | `/nominated-authority/national-coal-index?page=1` | 200 | 79,512 | no |
| 27 | 11:22:22 | `/nominated-authority/archive-national-coal-index` | 200 | 56,542 | no |
| 28 | 11:22:27 | `/public-information/monthly-statistics-at-glance?page=1` | 200 | 60,443 | no |
| 29 | 11:22:32 | `/public-information/statistical-report?page=1` | 200 | 57,005 | no |
| 30 | 11:22:37 | `/public-information/monthly-summary-cabinets/archive-monthly-summary-cabinets` | 200 | 75,218 | no |
| 31 | 11:22:42 | `/public-information/monthly-report-cabinet/archive-monthly-report-cabinet` | 200 | 75,316 | no |
| 32 | 11:22:46 | `/public-information/monthly-report-cabinet` | 200 | 81,771 | no |
| 33 | 11:23:07 | `/nominated-authority/archive-sanction-order-notice?page=1` | 200 | 61,191 | no |
| 34 | 11:23:11 | `/public-information/monthly-summary-cabinets/archive-monthly-summary-cabinets?page=1` | 200 | 65,758 | no |
| 35 | 11:23:16 | `/public-information/monthly-report-cabinet?page=1` | 200 | 57,927 | no |
| 36 | 11:23:21 | `/public-information/monthly-report-cabinet/archive-monthly-report-cabinet?page=1` | 200 | 61,256 | no |
| 37 | 11:23:27 | `/nominated-authority/national-lignite` | 200 | 61,437 | no |
| 38 | 11:23:32 | `/major-statistics/coal-statistics` | 200 | 58,851 | no |
| 39 | 11:23:37 | `/nominated-authority/termination-order` | 200 | 83,625 | no |
| 40 | 11:23:42 | `/public-information/standing-linkage-committee1` | 200 | 90,843 | no |
| 41 | 11:23:46 | `/public-information/paper-laid-table` | 200 | 60,828 | no |
| 42 | 11:23:51 | `/hi` | 200 | 110,813 | no |
| 43 | 11:23:56 | `/index.php/search/node?keys=auction` | 200 | 62,743 | no |
| 44 | 11:24:01 | `/major-statistics/coal-reserves` | 200 | 79,204 | no |
| 45 | 11:24:06 | `/sustainable-development-cell` | 200 | 55,978 | no |

I fetched the archive URLs as `https://www.coal.nic.in/<path>`, rewritten from the emitted
`http://www.coal.nic.in//<path>`. Every rewritten URL returned HTTP 200 with the archive
heading, so the rewrite rule is tested live.

Other observations:
- Pages now include an obfuscated anti-bot `<script src="/bnith__…">` tag whose token changes on
  each request. Re-fetches of auction `?page=2` and `?page=3` were byte-identical apart
  from that tag, so page content and ordering are deterministic per URL. Content
  fingerprints must exclude this script.
- I also used saved 2026-10-22 pages in the scratchpad, with zero new requests, to confirm that
  `/nominated-authority/production-reviews` and `/operationalisation-reviews` have no pager
  and no archive link. The same saved pages supplied the current general-minutes table
  for the overlap check.

## Boundaries found

### Priority 1 — `coal_auction_allotment` (now verified)

- Current view: the first table holds state statistics (11 rows) and is ignored. The second table is
  the document table.
- Pages `?page=0` to `?page=8` hold 8 x 50 + 14 = **414 rows**. S.N runs 1 to 414 with no gaps, every
  row has a DD/MM/YYYY date, and dates never increase down the list.
- **Newest:** 07/10/2026. **Oldest:** 19/04/2017 (S.N 414).
- **Pager terminus:** every page's "Last page" link points to `?page=8`. `?page=8` itself shows "Current page 9"
  with no Next or Last link. `?page=9` returns HTTP 200 with the heading and pager but **no tables at
  all**: it is past the terminus, not an empty collection.
- **Repeated rows:** there are 406 distinct hrefs, so 8 hrefs appear twice. Seven of the repeats sit at page
  boundaries where rows share a date: S.N 99/101, 148-150/151-153, 299/301, 350/351, and 400/401.
  This is offset paging over a non-unique sort key. Other rows with the same date may be
  silently skipped. One repeat falls inside page 1 (S.N 94/96). Contract consequence: deduplicate by
  href, keep `removal_authority: false`, and treat one pass as not proof of completeness.
- **Archive boundary:** `/nominated-authority/archive-auction-allotment` (heading typo
  "Archive Auction /Allotmnet") has **40 rows on one page**. The pager `<ul>` is empty and there is no
  further archive link. It has **no Date column**: three columns S.N/Title/Attachment. All files
  sit in 2021 upload folders. Titles run from December 2014 CMSP Ordinance earmarking
  orders to June 2021 tranche notices. No href overlaps the current view.

### Priority 2 — `coal_minutes_of_meetings` (now verified)

- The current table was observed on 2026-10-22 (23 rows, 2021-01-29 to 2025-10-07, no pager) and
  was not re-fetched today.
- **Archive boundary:** `/public-information/minutes-metings/archive-minutes-metings` holds
  **93 rows across `?page=0..1`** (50 + 43). S.N runs 1 to 93 and there are 93 distinct hrefs. The table has
  four columns with YYYY-MM-DD dates from **2005-06-02 to 2019-12-13**. Rows are **not in date order**: page 0 starts with
  2013-11-25 and then 2019-12-13. Page 1 shows "Current page 2" with no Next or Last link. No href overlaps
  the current table.

### Priority 3 — archive and pager check on existing feeds

| Feed | 2026-10-22 contract | Observed 2026-10-23 | Change made |
|---|---|---|---|
| `coal_whats_new` | single table, 50 rows | **41-page pager** (`?page=0..40`, Last page `?page=40`). **2042 rows** (page 40 holds S.N 2001-2042). Newest 16 Oct 2026, oldest displayed date 26 Apr 2000. Malformed tail rows: one dated `08 Oct 0013`, four undated, one titled `test` with no link. No archive link | Paged enumeration. Weekly pages until all hrefs are known, monthly full enumeration. Failure indicators and notes updated |
| `coal_acts_rules_policies` | five tables, 172 HTML rows | Same: 20+116+9+12+10 = 167 data rows, 26/11/1960 to 29/06/2026. **No pager or archive** | Boundary text only |
| `coal_compensation_orders` (`/sanction-order-notice`) | single table, 50 rows | **Pager `?page=0..4`, 233 rows** (page 4 holds S.N 201-233), 06/10/2026 back to 28/10/2016. **Archive** `/archive-sanction-order-notice`: **63 undated rows across `?page=0..1`** (50 + 13), 2014-2015 Ordinance-era material | `rotating_archive`, `archive_url`, rewrite rule, removal rule |
| `coal_national_coal_index` | single table, 50 rows | **Pager `?page=0..1`, 83 rows** (82 distinct hrefs), 29/09/2026 back to 05/06/2020. **Archive** `/archive-national-coal-index`: **4 undated rows**, no pager (NCI April, July, and August 2021, and November 2022 provisional) | same as above |
| `coal_monthly_statistics` | 50 rows | **Pager `?page=0..1`, 64 rows** (Sept 2026 back to April 2021). No archive | paged enumeration |
| `coal_statistical_reports` | 50 rows | **Pager `?page=0..1`, 56 rows** (March 2025 back to April 2020). No archive | paged enumeration |
| `coal_cabinet_summaries` | 48 rows | 48 rows (Feb 2025 back to Aug 2020), no pager, plus an **archive** `/monthly-summary-cabinets/archive-monthly-summary-cabinets` with **77 rows across `?page=0..1`** (Dec 2022 back to Sept 2014). The period ranges overlap but no href is shared | `rotating_archive`, archive, removal rule |
| `coal_monthly_cabinet_reports` | 50 current + 50 archive | **Current pager `?page=0..1`, 56 rows** (Oct 2025 back to Nov 2018). **Archive pager `?page=0..1`, 66 rows** (Sept 2021 back to April 2015). One archive row (September 2016) has no link | paged current + paged archive |
| `coal_termination_orders` | two tables | Current: 32 attachment rows (29/06/2026 back to 13/01/2017), no pager, **plus an "Archive Termination Order" link** (`/nominated-authority/archive-termination-order`) that was **not fetched** because the budget ran out | **`verified: false`, `boundary_verified: false`**, archive URL recorded, `removal_authority: false` until modelled |
| `coal_national_lignite_index` | 11 rows | 11 rows (16-10-2026 back to 07-12-2023), DD-MM-YYYY dates, no pager or archive | date format and boundary text |
| `coal_coal_statistics` | 17 rows | 17 rows, no pager or archive | boundary text |
| `coal_papers_laid` | 11 rows | 11 rows, no pager or archive | boundary text |
| `coal_standing_linkage_committee` | pages 0-1, 89 rows | Page 0 has 50 rows from 2026-06-15. Last page is still `?page=1`. No archive | boundary text |
| `coal_production_reviews`, `coal_operationalisation_reviews` | no pager/archive | Confirmed from saved 2026-10-22 pages (0 requests) | none |

**Not checked for archive links (budget exhausted):** `coal_quarterly_reports` (10 rows),
`coal_rsr_reports` (1), `coal_sustainability_reports` (3), `coal_annual_reports`,
`coal_budget_reports`, `coal_other_reports`, `coal_action_plans`, `coal_sop`,
`coal_csr_guidelines`, and `coal_central_schemes`. None of them can hide a pager, since each has
fewer than 50 rows, but any could have an archive footer link. Monthly Summary for Cabinet
(48 rows) shows that a short table can still have one.

### Priority 4 — sitemap-only pages, Hindi, and search

- `/major-statistics/coal-reserves`: a narrative page with nine inline resource tables (as on
  1 April 2021-2025, from the National Coal Inventory 2025) and **no linked files**. **Confirms `indirect`.**
- `/sustainable-development-cell`: a hub link list with no files. Three "Vrikshropan Abhiyan" entries
  render as escaped HTML text rather than live links. **Confirms `indirect` (hub).**
- `/hi`: Hindi interface (`lang="hi"`, `/hi/...` paths, and a language-dropdown POST form). The Hindi
  homepage What's New block (21 items, excluding its press-release block) mirrors the English What's New.
  9 items link the same file as an English row. About 12 link a **distinct Hindi-language PDF** (for example
  `…-wnhi.pdf` or `…-hi-wn.pdf`) that the English listing never links. I matched the items to English rows
  by filename date and title. The two items with no filename match (the Schedule I compensation-claims
  notice and the Mineral Concession Rules draft consultation) were confirmed by title on English
  What's New. The matching was a spot check, not an exhaustive translation, and no Hindi-only
  publication was found. **Confirms `exclude`**, with a
  sharper coverage limit: Hindi translation files reachable only from `/hi` are not collected.
  The Hindi block also shows a bracketed date, for example `(30/09/2026)`.
- `/index.php/search/node?keys=auction` returns 10 results per page, with Last page `?page=148`. Results link
  files directly on `http://www.coal.nic.in`, the same file repeats within a page, and the
  snippet date is an index timestamp. **Confirms `exclude`.**
- **Still decided from sitemap evidence only:** `coal-grades`, `mission-coking-coal`,
  `grading-coal`, `coal-evacuation-plan`, `Clearances`, `national-coal-gasification-mission`,
  all `/sustainable-development-cell/*` topic pages and `sdc-videoes`, all Safety pages,
  `/techno-roadmap-coal-sector`, `/achievements-flipbook`, `/CSR`, `/CSR/about-csr`,
  `/chintan-shivir`, and `/procurement-projection`.

## Config changes made (`config/sources.yaml`, `ministry_of_coal` block only)

`research_status` is unchanged (`preliminary`). No other site block was touched.

Feeds:
1. `coal_auction_allotment`: `verified: true`, `boundary_verified: true`, `last_verified:
   '2026-10-23'`. Added `archive_url` and `archive_href_rewrite`. Rewrote `enumeration` (terminus read
   from page=0's Last link, plus a single-page archive), `identity` (repeated href = one document),
   `date_semantics` (archive undated), `failure_indicators` (missing Archive link, no tables before
   the terminus), and `completeness.notes` (tie repeats, out-of-range page, anti-bot script). Set the exact
   `history.boundary`, added the `?page=8`, `?page=9`, and archive evidence URLs, and added 4 new fixtures.
2. `coal_minutes_of_meetings`: `verified: true`, `boundary_verified: true`. Added `archive_url`
   and `archive_href_rewrite`. Set the archive pager terminus in `enumeration`, the unordered archive dates in
   `date_semantics`, and the exact boundary. Added the archive `?page=1` evidence and the `minutes_archive.html` fixture.
3. `coal_whats_new`: paged `enumeration` and `refresh_policy`. `identity` now covers linkless rows, `date_semantics`
   covers malformed or empty dates, plus failure indicators, `completeness.notes`, the boundary, the `?page=40`
   evidence, and 3 fixtures. `kind` stays `rolling` and `removal_authority` stays `false`. See the recommendation.
4. `coal_compensation_orders`, `coal_national_coal_index`, `coal_cabinet_summaries`:
   `kind: rotating_archive`, plus `archive_url`, `archive_href_rewrite`, `enumeration`, `refresh_policy`,
   `completeness` (with `removal_rule`: removal requires absence from every current and
   archive page in two consecutive complete enumerations, and a current-to-archive move is a status change),
   the boundary, evidence, and fixtures.
5. `coal_monthly_cabinet_reports`: added `archive_url`, `archive_href_rewrite`, a paged
   current and archive `enumeration`, `refresh_policy`, `date_semantics`, `completeness` with
   `removal_rule`, the corrected boundary, evidence, and fixtures.
6. `coal_monthly_statistics`, `coal_statistical_reports`: paged `enumeration`,
   `refresh_policy`, `completeness` with `removal_rule` and pager failure indicator, the corrected
   boundary, the `?page=1` evidence, and a fixture.
7. `coal_termination_orders`: **`verified: false`, `boundary_verified: false`**, `kind:
   rotating_archive`, `archive_url`, `archive_href_rewrite`, and `removal_authority: false` with a note.
   The boundary records the unfetched archive.
8. `coal_acts_rules_policies`, `coal_coal_statistics`, `coal_national_lignite_index`
   (DD-MM-YYYY `date_semantics`), `coal_papers_laid`, `coal_standing_linkage_committee`:
   boundary text records the no-pager/no-archive re-check, with `last_verified: '2026-10-23'`.

Coverage decisions, with no decision value changed:
- What's New: reason, evidence, coverage limit, refresh policy, and fixtures (41-page pager).
- Major Statistics tables: pager note, fixture.
- Public Information collections: archive and pager notes, fixture.
- NA orders and indices: pager and archive notes, the termination archive unverified, fixtures.
- Auction/Allotment: reason, evidence, coverage limit (tie repeats), fixtures.
- Meeting minutes: reason (archive boundary), evidence, coverage limit, refresh, fixture.
- Sitemap/hub pages: SDC hub confirmed.
- Narrative/thematic pages: coal-reserves confirmed. The remaining sitemap-only list is restated.
- Site search and Hindi: fetched. Reason, evidence, the Hindi translation-file limit, and fixtures.

New config keys, accepted by the validator but in no schema yet (add to the Step 2 list next to
`archive_url_template`/`status_passes`): `archive_url`, `archive_href_rewrite`
(`applies_to`, `rewrite_to`, `rule`). `completeness.removal_rule` and `completeness.notes` are now
also used on Coal.

## New fixture provenance rows for `tests/fixtures/README.md`

```markdown
| `ministry_of_coal/auction_allotment_last_page.html` | `/nominated-authority/auction-allotment?page=8` | 2026-10-23 | Final pager page: 14-row document table (S.N 401-414), pager without Next/Last links, double-slash HTTP archive link |
| `ministry_of_coal/auction_allotment_page_overlap.html` | `/nominated-authority/auction-allotment?page=2` and `?page=3` | 2026-10-23 | Two page excerpts showing same-date rows repeated across an offset-pager boundary (identity by href; one pass is not completeness proof) |
| `ministry_of_coal/paged_view_out_of_range.html` | `/nominated-authority/auction-allotment?page=9` | 2026-10-23 | Page past the pager terminus: HTTP 200, heading and pager render, no tables; not an empty collection or block page |
| `ministry_of_coal/auction_allotment_archive.html` | `/nominated-authority/archive-auction-allotment` (also the shape of `/archive-national-coal-index`) | 2026-10-23 | Single-page three-column archive without a Date column, empty pager list, verbatim heading typo, reached via the HTTPS single-slash rewrite |
| `ministry_of_coal/minutes_archive.html` | `/public-information/minutes-metings/archive-minutes-metings` | 2026-10-23 | Dated four-column archive with rows out of date order and a two-page pager |
| `ministry_of_coal/archive_table_paged.html` | `/nominated-authority/archive-sanction-order-notice`, Monthly Summary for Cabinet and Monthly Reports for Cabinet archives | 2026-10-23 | Undated three-column archive with a two-page pager |
| `ministry_of_coal/paged_table_with_archive.html` | `/nominated-authority/national-coal-index`, `/sanction-order-notice`, `/public-information/monthly-report-cabinet` | 2026-10-23 | Current table with both a pager Last page link and a double-slash HTTP archive footer link |
| `ministry_of_coal/whats_new_paged.html` | `/whats-new` | 2026-10-23 | What's New page 0 with an ellipsis pager whose Last page link is `?page=40` (41 pages) |
| `ministry_of_coal/whats_new_last_page.html` | `/whats-new?page=40` | 2026-10-23 | Last What's New page with malformed source rows: year 0013, empty dates, and a linkless "test" row |
| `ministry_of_coal/hindi_whats_new_block.html` | `/hi` | 2026-10-23 | Excluded Hindi interface What's New block mirroring English rows, some linking distinct Hindi-language PDFs |
| `ministry_of_coal/site_search_results.html` | `/index.php/search/node?keys=auction` | 2026-10-23 | Excluded Drupal search results: direct file links, repeated results, index timestamps, 149-page pager |
```

All new fixtures are hand-trimmed structural excerpts with example titles and filenames. They
contain no personal data and no PDFs.

## Replacement text for the Coal "Open verification" subsection of `docs/SOURCE_INVENTORY.md`

```markdown
#### Re-verification (2026-10-23) and remaining open items

A 45-request pass at D-017 pacing (at least 4.5 s apart) received no CAPTCHA. Full log:
`docs/step1_pending/coal_reverification.md` (merge, then delete with the directory).

Verified:
- `coal_auction_allotment`: 414 rows across `?page=0..8` (07/10/2026 back to 19/04/2017);
  `?page=9` renders the pager with no tables; the archive has 40 undated rows on one page.
  Offset paging over tied dates repeats 7 hrefs at page boundaries (406 distinct hrefs
  in 414 rows), so identity is by href and one pass never proves completeness.
- `coal_minutes_of_meetings`: the archive has 93 rows across `?page=0..1`, dated
  2005-06-02 to 2019-12-13 and not in date order.
- The archive URL rewrite (`http://www.coal.nic.in//<path>` to
  `https://www.coal.nic.in/<path>`) was exercised live on six archive pages.
- Pagers or archives missing from the 2026-10-22 contracts are now modelled for
  What's New (41 pages, 2042 rows back to 2000, with malformed tail rows),
  compensation orders (5 pages and a 2-page undated archive), National Coal Index (2 pages
  and a 4-row undated archive), Monthly Statistics (2 pages), Statistical Report
  (2 pages), Monthly Summary for Cabinet (2-page archive), and Monthly Reports for
  Cabinet (2 current pages and a 2-page archive).
- No pager or archive: Acts & Policies, Coal Statistics, National Lignite Index,
  Papers Laid, Standing Linkage Committee (pager ends at page=1), production reviews,
  and operationalisation reviews.
- `/major-statistics/coal-reserves` (inline tables, no files) and the
  `/sustainable-development-cell` hub confirm their indirect decisions. The Hindi
  interface mirrors English What's New rows (spot-checked; no Hindi-only item found), often linking distinct Hindi PDFs that
  are not collected. Site search (149 pages, repeated results) is not enumerable. Both
  stay excluded.
- Pages carry an injected anti-bot `<script src="/bnith__…">` whose token changes on
  every request; exclude it from page fingerprints.

Still open (about 25 requests at D-017 pacing):
- `coal_termination_orders` links `/nominated-authority/archive-termination-order`,
  which was not fetched. The feed is `verified: false` until its archive shape and boundary are
  recorded.
- Archive-link check not yet done for quarterly booklet, RSR, sustainability report,
  annual/budget/other report indexes, action plan, SOP, CSR guidelines, and Central Sector
  Schemes.
- Narrative pages still decided from sitemap evidence only: `coal-grades`,
  `mission-coking-coal`, `grading-coal`, `coal-evacuation-plan`, `Clearances`,
  `national-coal-gasification-mission`, the `/sustainable-development-cell/*` topic pages
  and `sdc-videoes`, the Safety pages, `/techno-roadmap-coal-sector`,
  `/achievements-flipbook`, `/CSR`, `/CSR/about-csr`, `/chintan-shivir`, and
  `/procurement-projection`.
```

### Other inventory text the coordinator should correct (not edited by me)

- In "Verified endpoint shapes": the rows for `/whats-new`, `/monthly-statistics-at-glance`,
  `/statistical-report`, `/sanction-order-notice`, `/national-coal-index`,
  `/monthly-summary-cabinets`, `/monthly-report-cabinet`, and `/termination-order` should
  take the boundaries in the Priority 3 table above.
- In "New feed boundaries": mark `coal_auction_allotment` and `coal_minutes_of_meetings` as
  verified, using the boundaries above.
- Under "Observed behaviour", add to the archive list: `archive-sanction-order-notice`,
  `archive-national-coal-index`, `archive-monthly-summary-cabinets`, and
  `archive-termination-order`. Note that archive tables usually lack a Date column.
- OPERATIONS.md: note the anti-bot script token and the per-page cost of the deeper
  enumerations. A full Coal enumeration is now about 41 (What's New) + 10 (auction) + 7
  (compensation) + about 25 other pages, roughly 85 requests or about 6 minutes at 0.25 req/s.
  The weekly What's New refresh should stop early once a page holds only known hrefs.

## Recommendation on `research_status`

**Do not mark Coal `complete` yet. Keep `preliminary`.**

Strict-gate status after this pass: `--require-complete` reports exactly three Coal errors.
They are `research_status`, plus `verified` and `history.boundary_verified` on `coal_termination_orders`
(`sites[1].feeds[10]`). The two D-015 feeds that blocked the gate now pass.

The research is not complete for three reasons:
1. `coal_termination_orders` has a confirmed but unfetched archive.
2. Today showed that "verified" on 2026-10-22 did not include pager or archive checks.
   Eight feeds were wrong. Ten more feeds (listed above) have not had that check.
3. About 20 narrative pages are still decided from sitemap evidence only. They are low risk,
   since they are indirect or excluded and What's New catches new material, but they are unconfirmed.

One more pass of about 25 requests at D-017 pacing closes the gap. That pass should fetch the termination archive
(and any archive pager), then load each of the ten unchecked feed pages once for footer
archive links, then spend any budget left on the narrative pages. If the narrative pages
stay unfetched, the user could accept them as a recorded coverage limit. Coal can be marked
`complete` when the termination archive is modelled and the ten feeds show no unmodelled
archive.

A product question for the coordinator, not decided here: What's New is a 2042-row
history back to 2000, not a short rolling list. Its contract keeps `kind: rolling` and
`removal_authority: false`, which is safe. Step 2 may want to reclassify it, and a baseline
without alerts must cover all 41 pages.

> **Superseded in part by Pass 2 below.** The termination-archive and unchecked-feed items above
> are closed. The final recommendation is at the end of Pass 2. The Pass 1 "Open verification"
> replacement text is replaced by the Pass 2 version.

---

# Pass 2 — remaining Coal checks (2026-10-23, coordinator follow-up)

Same rules as Pass 1: a budget of 30 requests, at least 4.5 s from the end of one response to the
next request, and a stop on the first CAPTCHA. **All 30 requests returned HTTP 200 real pages, with no
CAPTCHA.** The budget is fully used. This pass used a fresh request counter, so `#` restarts at 1.

## Pass 2 request log

| # | Time | URL (https://www.coal.nic.in) | Status | Bytes | CAPTCHA |
|---|---|---|---|---|---|
| 1 | 11:36:29 | `/nominated-authority/archive-termination-order` | 200 | 54,264 | no |
| 2 | 11:36:35 | `/major-statistics/quarterly-booklet` | 200 | 58,720 | no |
| 3 | 11:36:40 | `/major-statistics/rsr-report` | 200 | 54,209 | no |
| 4 | 11:36:45 | `/sustainable-development-cell/report` | 200 | 55,275 | no |
| 5 | 11:36:50 | `/public-information/reports/annual-reports` | 200 | 58,972 | no |
| 6 | 11:36:54 | `/public-information/reports/budget-reports` | 200 | 60,058 | no |
| 7 | 11:36:59 | `/public-information/reports/other-reports` | 200 | 56,148 | no |
| 8 | 11:37:04 | `/public-information/action-plan` | 200 | 54,287 | no |
| 9 | 11:37:09 | `/public-information/sop` | 200 | 54,224 | no |
| 10 | 11:37:13 | `/CSR/guidelines` | 200 | 56,849 | no |
| 11 | 11:37:18 | `/central-sector-schemes` | 200 | 58,914 | no |
| 12 | 11:37:53 | `/major-statistics/coal-grades` | 200 | 57,050 | no |
| 13 | 11:37:57 | `/major-statistics/mission-coking-coal` | 200 | 56,984 | no |
| 14 | 11:38:02 | `/major-statistics/grading-coal` | 200 | 54,708 | no |
| 15 | 11:38:07 | `/major-statistics/coal-evacuation-plan` | 200 | 56,280 | no |
| 16 | 11:38:12 | `/major-statistics/Clearances` | 200 | 56,367 | no |
| 17 | 11:38:17 | `/major-statistics/national-coal-gasification-mission` | 200 | 96,132 | no |
| 18 | 11:38:22 | `/techno-roadmap-coal-sector` | 200 | 55,428 | no |
| 19 | 11:38:27 | `/achievements-flipbook` | 200 | 54,287 | no |
| 20 | 11:38:32 | `/CSR` | 200 | 53,744 | no |
| 21 | 11:38:37 | `/CSR/about-csr` | 200 | 59,718 | no |
| 22 | 11:38:42 | `/chintan-shivir` | 200 | 54,382 | no |
| 23 | 11:38:47 | `/procurement-projection` | 200 | 54,224 | no |
| 24 | 11:38:52 | `/about-safety-coal-mines` | 200 | 53,826 | no |
| 25 | 11:38:57 | `/safety-coal-mines/safety-staistics` | 200 | 55,267 | no |
| 26 | 11:39:02 | `/safety-coal-mines/safety-initiatives` | 200 | 55,691 | no |
| 27 | 11:39:07 | `/sustainable-development-cell/about-sdc` | 200 | 65,753 | no |
| 28 | 11:39:12 | `/sustainable-development-cell/greening-initiatives` | 200 | 65,652 | no |
| 29 | 11:39:16 | `/sustainable-development-cell/mine-water-utilization` | 200 | 65,356 | no |
| 30 | 11:39:21 | `/sustainable-development-cell/promoting-renewable` | 200 | 60,813 | no |

Combined across both passes: 75 requests, 0 CAPTCHA pages.

## Pass 2 findings

### 1. Termination-order archive: verified

`/nominated-authority/archive-termination-order` was fetched via the HTTPS single-slash rewrite.
It has **1 row** (Kasta East allotment termination, file in the `2021-05` upload folder) on a single
page. The pager `<ul>` is empty, there is no Date column, and the href does not appear in the current
table. Together with the current table (32 rows, 29/06/2026 back to 13/01/2017), the feed is fully
observed. `coal_termination_orders` is back to `verified: true` / `boundary_verified: true`. Its
removal authority is restored with the current-plus-archive two-run removal rule.

### 2. Ten unchecked feeds: none has a pager or archive

| Feed | Observed | Contract change |
|---|---|---|
| `coal_quarterly_reports` | 10 rows, no S.N column, DD-MM-YYYY, 17-09-2026 back to 22-05-2024 | date format and boundary |
| `coal_rsr_reports` | 1 undated row | boundary |
| `coal_sustainability_reports` | 3 rows, no S.N column, 24-10-2024 back to 01-08-2024 | date format and boundary |
| `coal_annual_reports` | parent index 18 rows (2025-26 back to 2009-10). Children not re-fetched | boundary note |
| `coal_budget_reports` | parent index 18 rows (2026-27 back to 2009-10). Children not re-fetched | boundary note |
| `coal_other_reports` | parent index 3 rows | boundary note |
| `coal_action_plans` | 3 list links: two coal.gov.in PDFs plus a coal.gov.in/actionplan HTML page for FY 2024 | boundary note |
| `coal_sop` | 1 row dated 08-08-2024 | boundary |
| `coal_csr_guidelines` | 8 rows, all dated 03-04-2024 (a posting date) | boundary |
| `coal_central_schemes` | narrative page and a 6-row budget table (BE/RE 2022-23), no files | boundary |

No contract was missing an archive or pager. Every Coal feed has now had a pager and archive check
on 2026-10-23. The one exception is the annual/budget/other report detail pages, which were last
enumerated on 2026-10-22; I re-checked only their parent indexes.

### 3. Narrative pages: one correction, the rest confirmed

**Correction: Coal Gasification (`/major-statistics/national-coal-gasification-mission`) is an active
publication collection, not a narrative page.**
- It has nine Title/Attachment/Date section tables, plus one table nested inside the investors'-meet section.
- It holds **59 rows with 57 distinct hrefs**, dated **16/09/2026 back to 21/09/2021**, with one undated row.
- Content: the Scheme for promotion of Coal/Lignite Gasification approval, RFPs dated 13.08.2026,
  pre-application conference notices, query responses, the coal-to-hydrogen roadmap, and seminar and
  investor-meet material.
- Files are absolute coal.gov.in links in mixed http/https, and 7 have a doubled path slash
  (`https://coal.gov.in//sites/...`).
- There is no pager and no archive.
- Many rows also appear in What's New, but the page is the authoritative scheme dossier.

On that evidence I added a tracked feed **`coal_gasification`** (verified, with its boundary and fixture),
a coverage decision `track`, and an entry in `required_feed_ids`. I removed gasification from the
narrative group.
**This needs a recorded product decision** (proposed D-0xx: "Coal Gasification scheme publications are a
tracked feed, consistent with D-015"). If the user prefers `indirect`, revert three things: the feed,
its `required_feed_ids` entry, and the coverage entry. Then restore "Coal Gasification" to the narrative-group
scope. What's New would still capture the announcements.

**Confirmed `indirect` (narrative):**
- No files: Coal Grades, Safety Statistics (NLCIL tables to 2022), Safety Initiatives
  (one line), About CSR, About SDC, Greening Initiatives, Mine Water Utilization, Promoting Renewable.
- Small static file sets, unchanged since 2021-2024:
  - Mission Coking Coal: 2 files (2021)
  - Grading of Coal: 2 files (2022-2023 lignite-grading consultation)
  - Coal Evacuation Plan: 2 files (Coal Logistics Policy, 2024)
  - Clearances: 1 file (2024 compendium)
  - Technology Roadmap: 4 files (2022)
  - Procurement Projection: 2 files (2021)
  - Chintan Shivir: 3 files (2021)
  - Achievements Flipbook: 1 PDF plus four coal.gov.in flipbook HTML pages
- Coal Evacuation Plan and Clearances also render **escaped HTML table rows as text**. They are not live
  links and must never be parsed as rows. Fixture: `narrative_static_tables.html`.

**Confirmed `indirect` (hubs):** `/CSR` (links to About CSR and Guidelines, plus the CSR photo-gallery PDF)
and `/about-safety-coal-mines` (four safety links).

**Never fetched in any session (7 pages, out of budget):**
- `/major-statistics/safety-coal-mines` (About Safety) and `/safety-coal-mines/accident-portal`
- SDC `air-quality-management`, `eco-tourism-in-mining-area`, `energy-efficiency`,
  `gainful-utilization`, and `sdc-videoes`

Every sibling page fetched was text-only, so these are low risk. They are recorded as a coverage limit
on the narrative decision.

## Pass 2 config changes (`ministry_of_coal` block only)

I re-read `config/sources.yaml` before editing and applied the changes with a script that edits only
the text between `- id: ministry_of_coal` and `- id: cerc`, asserting each target matched exactly
once. A comparison against a pre-edit copy confirmed that everything outside the Coal block, including
the coordinator's new Ministry of Power edits, is byte-identical. `research_status` is unchanged (`preliminary`).

Changes:
- `required_feed_ids`: added `coal_gasification`.
- New coverage decision: Coal Gasification → `track` (feed `coal_gasification`).
- Narrative coverage decision: scope drops Coal Gasification. Reason, evidence, coverage limit
  (escaped rows, the 7 never-fetched pages), and fixture updated.
- Coverage decisions refreshed with the re-check results: Major Statistics tables (quarterly and RSR),
  Public Information (action plan and SOP), Reports branch, NA orders and indices (termination archive),
  Sustainability/CSR/Schemes, and Sitemap/hubs (CSR and safety hubs).
- `coal_whats_new`: `completeness.notes` now states that the silent initial baseline must enumerate all
  41 pages (page=0 through page=40, or whatever Last link page=0 shows), and that a partial baseline is not
  complete. `kind: rolling` and `removal_authority: false` are kept, and reclassification is deferred to Step 2.
- `coal_termination_orders`: `verified: true`, `boundary_verified: true`. Set the single-page archive in
  `enumeration`, added `date_semantics`, `refresh_policy`, `removal_authority: true` with `removal_rule`, and a
  `missing archive table` failure indicator, plus the exact boundary and the `auction_allotment_archive.html`
  fixture, which shares its shape.
- Boundary and `last_verified` updates for `coal_quarterly_reports` (and DD-MM-YYYY `date_semantics`),
  `coal_rsr_reports`, `coal_sustainability_reports` (and `date_semantics`), `coal_annual_reports`,
  `coal_budget_reports`, `coal_other_reports`, `coal_action_plans`, `coal_sop`,
  `coal_csr_guidelines`, and `coal_central_schemes`.
- New feed `coal_gasification`. It inherits `coal_direct_table`. Its contract sets a multi-section `enumeration`
  and an `identity` that keeps raw hrefs with no normalised merge without a tested rule. It also sets
  `date_semantics`, `removal_authority: false` with notes, the boundary, and the fixture.

## Pass 2 fixture provenance rows for `tests/fixtures/README.md`

```markdown
| `ministry_of_coal/coal_gasification.html` | `/major-statistics/national-coal-gasification-mission` | 2026-10-23 | Multi-section publication page: section tables with DD/MM/YYYY dates, a nested table, an undated row, and absolute coal.gov.in hrefs with mixed schemes and doubled path slashes |
| `ministry_of_coal/narrative_static_tables.html` | `/major-statistics/coal-evacuation-plan` (shape shared by Clearances, Grading of Coal, Mission Coking Coal, Technology Roadmap, Procurement Projection) | 2026-10-23 | Indirect narrative page with a small static file table plus escaped HTML rows rendered as text that must not be parsed as rows |
```

The Pass 1 row for `ministry_of_coal/auction_allotment_archive.html` should also name
`/nominated-authority/archive-termination-order` among its sources. The fixture comment is updated.

## Updated replacement text for the Coal "Open verification" subsection of `docs/SOURCE_INVENTORY.md`

This supersedes the Pass 1 replacement text.

```markdown
#### Re-verification (2026-10-23)

Two passes at D-017 pacing (75 requests, at least 4.5 s apart) received no CAPTCHA. Full logs:
`docs/step1_pending/coal_reverification.md` (merge, then delete with the directory).

Verified:
- `coal_auction_allotment`: 414 rows across `?page=0..8` (07/10/2026 back to 19/04/2017);
  `?page=9` renders the pager with no tables; the archive has 40 undated rows on one page.
  Offset paging over tied dates repeats 7 hrefs at page boundaries (406 distinct hrefs
  in 414 rows), so identity is by href and one pass never proves completeness.
- `coal_minutes_of_meetings`: the archive has 93 rows across `?page=0..1`, dated
  2005-06-02 to 2019-12-13 and not in date order.
- `coal_termination_orders`: the archive has one undated row on a single page.
- The archive URL rewrite (`http://www.coal.nic.in//<path>` to
  `https://www.coal.nic.in/<path>`) was exercised live on seven archive pages.
- Pagers or archives missing from the 2026-10-22 contracts are now modelled for
  What's New (41 pages, 2042 rows back to 2000, with malformed tail rows; the silent
  baseline must enumerate every page), compensation orders (5 pages and a 2-page
  undated archive), National Coal Index (2 pages and a 4-row archive), Monthly
  Statistics (2 pages), Statistical Report (2 pages), Monthly Summary for Cabinet
  (2-page archive), Monthly Reports for Cabinet (2 current pages and a 2-page
  archive), and termination orders (1-row archive).
- Every other tracked feed was re-checked and has no pager or archive. The annual,
  budget, and other report detail pages were last enumerated on 2026-10-22; only their
  parent indexes were re-checked.
- Coal Gasification (`/major-statistics/national-coal-gasification-mission`) is an
  active scheme publication collection (59 rows, 16/09/2026 back to 21/09/2021) and is
  now the tracked feed `coal_gasification`. A product decision record is pending.
- Narrative pages confirmed `indirect`: Coal Grades, Coal Reserves, Mission Coking Coal,
  Grading of Coal, Coal Evacuation Plan, Clearances, Technology Roadmap, Achievements
  Flipbook, Chintan Shivir, Procurement Projection, About CSR, Safety Statistics, Safety
  Initiatives, About SDC, Greening Initiatives, Mine Water Utilization, and Promoting
  Renewable. Hubs confirmed: `/CSR`, `/about-safety-coal-mines`,
  `/sustainable-development-cell`. Coal Evacuation Plan and Clearances render escaped
  HTML rows as text, which must not be parsed.
- The Hindi interface mirrors English What's New rows (spot-checked; no Hindi-only item
  found), often linking distinct Hindi PDFs that are not collected. Site search (149
  pages, repeated results) is not enumerable. Both stay excluded.
- Pages carry an injected anti-bot `<script src="/bnith__…">` whose token changes on
  every request; exclude it from page fingerprints.

Accepted coverage limits:
- Never fetched: `/major-statistics/safety-coal-mines`,
  `/safety-coal-mines/accident-portal`, and the SDC pages `air-quality-management`,
  `eco-tourism-in-mining-area`, `energy-efficiency`, `gainful-utilization`, and
  `sdc-videoes`. Every fetched sibling was text-only; they remain `indirect`.
```

## Pass 2 validation

- `python -m pytest -q`: **7 passed, 2 failed.** The failures are the expected CERC `coverage_decisions`
  KeyError in `test_repository_inventory_is_structurally_valid` and
  `test_declared_repository_fixtures_exist`.
- `PYTHONPATH=src python -m policy_tracker.inventory config/sources.yaml`: the only error is
  `sites[2].coverage_decisions` (CERC).
- `--require-complete`: the **only Ministry of Coal error is `sites[1].research_status`**. The other errors
  are CERC (`sites[2].coverage_decisions` and `sites[2].research_status`). Ministry of Power passes.
- Every Coal fixture referenced in the config exists. Every `required_feed_ids` entry is configured
  and every configured feed is required. A YAML walk found no stray flow-mapping keys.

## Final recommendation

**Coal can be marked `research_status: complete` once the coordinator settles the Coal
Gasification decision.**

1. Record the decision for `coal_gasification`: either confirm tracking (proposed D-0xx) or revert it to
   `indirect` as described above. Either way the strict gate stays clean apart from `research_status`.
2. Accept the seven never-fetched narrative and safety pages as the recorded coverage limit. Optionally
   fetch them later with about 7 requests.
3. Then set `research_status: complete`, merge the SOURCE_INVENTORY replacement text and both sets of fixture
   README rows, and delete this file with `docs/step1_pending/`.

Still for Step 2:
- What's New reclassification (it stays `rolling` with `removal_authority: false` for now).
- Schema keys `archive_url` and `archive_href_rewrite`.
- A tested identity rule for coal.gov.in hrefs with mixed schemes and doubled slashes, if merging on a
  normalised URL is ever wanted.
- Full Coal enumeration now costs roughly 90 requests, about 6 minutes at 0.25 req/s. OPERATIONS.md should say so.
