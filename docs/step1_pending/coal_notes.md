# Ministry of Coal — Step 1 site review notes (2026-10-22, revised after D-014–D-017)

## Outputs

- `coal_coverage_decisions.yaml`: 20 site-level decisions. There are no
  pending entries; D-014, D-015, D-016, and D-017 are cited where they apply.
- `coal_new_feeds.yaml`: four D-015 feed contracts, as list items under
  `feeds:`. Two are verified and two are unverified.
- Merge steps: append `coal_new_feeds.yaml` after `coal_central_schemes`,
  insert the coverage list before the coal `research_status`, and add
  `coal_auction_allotment`, `coal_minutes_of_meetings`,
  `coal_production_reviews`, and `coal_operationalisation_reviews` to
  `required_feed_ids`.
- Validation: I spliced the files in memory. Normal validation returns 0 coal
  errors. `--require-complete` reports only `research_status` plus
  `verified`/`boundary_verified` on `coal_auction_allotment` and
  `coal_minutes_of_meetings`. All referenced fixtures exist.

## Decisions applied

| Branch | Decision | Basis |
|---|---|---|
| NA Auction/Allotment + archive | track: `coal_auction_allotment` | D-015 |
| General minutes + archive, Production Reviews, Operationalisation Reviews | track: `coal_minutes_of_meetings`, `coal_production_reviews`, `coal_operationalisation_reviews` | D-015 |
| Scrutiny Committee, Single Window System, Energy Cell | exclude as independent feeds; What's New appearances still captured | D-015, D-001 |
| Ministry press releases | exclude; What's New appearances still captured | D-014, D-001 |
| Production and Supplies | indirect | D-016 |
| coal.nic.in request rate | 0.25 req/s with backoff; stop on CAPTCHA | D-017 |

## Verification attempt under D-017

- At 5 s spacing, the first request (`/nominated-authority/auction-allotment?page=1`)
  returned the HTTP 200 CAPTCHA "Validation request" page. The run stopped
  immediately with no retries. The block, first seen at about 0.8 req/s, had not
  cleared.
- All recorded boundaries therefore come from pages fetched successfully earlier
  in the session, before the block.

## Exactly what remains unverified

1. `coal_auction_allotment`: pager pages `?page=1` through `?page=8`, including
   their row counts and the oldest document date. Also the archive page
   (`/nominated-authority/archive-auction-allotment`) and any archive pager.
   What is known: page 0 has 50 document rows (07/10/2026 back to 19/11/2024)
   and the pager "Last page" link targets `?page=8`. Set to `verified: false` and
   `boundary_verified: false`.
2. `coal_minutes_of_meetings`: the archive page
   (`/public-information/minutes-metings/archive-minutes-metings`) and any archive
   pager. The current table (23 rows, 2021-01-29 to 2025-10-07, no pager) was
   observed. Set to `verified: false` and `boundary_verified: false`.
3. Archive links on already-configured feeds were not checked: `whats-new`,
   `acts-rules-policies`, `nominated-authority/sanction-order-notice`, and
   `nominated-authority/national-coal-index`, in that priority order, then the
   other direct-table feeds. Many Coal Drupal views end with an "Archive ..."
   footer link, so these contracts may be missing history.
4. Pages decided from sitemap evidence only, because they returned the CAPTCHA:
   - Major Statistics: `coal-reserves`, `coal-grades`, `mission-coking-coal`,
     `grading-coal`, `coal-evacuation-plan`, `Clearances`, and
     `national-coal-gasification-mission`.
   - All `/sustainable-development-cell/*` topic pages and `sdc-videoes`.
   - All Safety pages.
   - `/techno-roadmap-coal-sector`, `/achievements-flipbook`, `/CSR`,
     `/CSR/about-csr`, `/chintan-shivir`, and `/procurement-projection`.

   These are covered as indirect or exclude. One clean fetch each would confirm
   that none holds a dated publication table.
5. The Hindi interface variant (`/hi/...`) and one site-search results page
   (`/index.php/search/node?keys=...`) were never fetched.

## Archive URL rewrite rule

Coal archive links are emitted as `http://www.coal.nic.in//<path>`. Before
fetching, normalise them to `https://www.coal.nic.in/<path>`: force HTTPS,
collapse the duplicated slash after the host, and keep the path and query
unchanged. This needs a fixture-backed unit test (`auction_allotment.html` and
`minutes_of_meetings.html` contain the raw form). Do not apply it beyond
coal.nic.in.

## Recommendation on `research_status`

**Keep `preliminary`.** The site can move to `complete` after one more research
pass, from a fresh session or after the block clears, at 0.25 req/s or slower.
That pass should:

- enumerate auction pages 1–8 and the auction and minutes archives, then set both
  feeds to `verified: true` with observed oldest dates;
- check the four priority feeds for archive links;
- spot-check the blocked narrative pages, the Hindi variant, and search.

About 40 requests are needed, roughly 3 minutes at D-017 pacing.

## Docs follow-ups for the coordinator (not edited by me)

- Add the CAPTCHA page signature ("Validation request" title or `captcha_resp`
  form) to the Coal `failure_indicators` and to OPERATIONS.md (D-017 behaviour).
- In SOURCE_INVENTORY.md, annotate "Critical/super-critical coal stock material"
  as external (it is only an outbound link to coalindia.in), so it is not a
  missing feed.
- Record that `/major-statistics/generation-of-thermal-power-from-raw-coal`, which
  the sitemap lists, returns 404.
- Add the nine rows from `coal_fixture_rows.md` to `tests/fixtures/README.md`.
