# Source Inventory

> Status: **Step 1 research complete for all three sites (2026-10-23); awaiting
> user review before Step 2.** Every endpoint, pagination boundary, archive/year
> selector, document relationship, and exclusion recorded below has been
> verified or recorded as an accepted coverage limit. The inventory is not
> approved until the user completes the final checklist item.

## Verification log

### 2026-10-22 — first Step 1 implementation increment

- The Ministry of Coal sitemap was reachable as a single English HTML navigation
  tree. It exposes publication-oriented branches for major statistics,
  sustainability reports, Nominated Authority material, public-information
  reports and minutes, Acts & Policies, CSR guidelines, and central-sector
  schemes. Generic organization, contact, gallery, vacancy, tender, RTI, and
  external Parliament/PIB branches still need explicit final decisions below.
- The Ministry of Coal `whats-new` page was reachable as a four-column table
  (`S.N`, title, download/link, date) with at least 30 current entries. It
  contained consultations, requests for stakeholder suggestions, indices,
  schemes, Gazette material, and meeting records. It is confirmed rolling and
  is not removal authority.
- Coal's `monthly-statistics-at-glance` page exposed 50 rows from June 2022
  through September 2026, with gaps in the monthly sequence. The displayed
  boundary is therefore not evidence that every calendar month exists, but all
  rows exposed by the authoritative collection are enumerated.
- Coal's `statistical-report` page exposed 50 monthly rows from July 2021
  through August 2025. It is distinct from the more current statistics-at-a-
  glance collection and is tracked as an independent feed.
- The CERC sitemap was reachable as a single English HTML navigation tree. It
  confirmed separate branches for current and prior-year orders/ROPs, current
  and repealed regulations, draft regulations, papers/studies, publications,
  regulatory-affairs minutes/advice, hearings, and public/statutory notices.
- CERC `Draft_reg.html` was reachable as a year-grouped table. A single matter
  row can contain the initial notice/draft plus later deadline extensions,
  comments, hearings, and stakeholder presentations. The page currently shows
  serial 120 for 2026 and historical rows below it; its oldest boundary still
  needs verification.
- The Ministry of Power JSON endpoints could not be fetched through the
  available web research proxy in this increment. Existing direct-API findings
  remain preliminary until independently rechecked and captured as fixtures.

In `config/sources.yaml`, `verified: true` means only that the stated endpoint
shape and behavior were observed. It does not mean the owning site's full
inventory is complete; `research_status` remains `preliminary` until the entire
site passes the checklist.

### 2026-10-22 — CERC completion and paused site review

- All nineteen required CERC feed IDs now have verified contracts and sanitized
  representative fixtures. The strict gate had narrowed to the three site-level
  `research_status` flags before coverage-decision enforcement was added.
- The Ministry of Power homepage is a client-rendered Next.js shell with no
  ordinary links in the fetched HTML. Its official `/sitemap.xml` endpoint was
  reachable but incorrectly listed `meity.gov.in` URLs, so it cannot establish
  Ministry of Power navigation completeness. The taxonomy API remains the
  verified discovery source for selected document collections.
- Work paused at the final site-level track/indirect/exclude review. A frontend-
  bundle probe intended to locate additional Ministry of Power navigation/search
  APIs was stopped when the pause was requested; no result from that probe was
  used.

### 2026-10-23 — Coal re-verification, CERC merge, and Step 1 close

- **Ministry of Coal re-verification.** Two passes at D-017 pacing (75 requests
  in total, at least 4.5 s apart) received no CAPTCHA page. They verified
  `coal_auction_allotment` (414 rows over nine pages plus a 40-row archive),
  `coal_minutes_of_meetings` (93-row archive), and the termination-order
  archive. They also found pagers or archives missing from eight contracts
  marked verified on 2026-10-22, all of which are now modelled. Every other
  tracked feed was re-checked for a pager or archive link. The Hindi interface,
  site search, and every sitemap-only narrative page except seven were fetched.
  Details: Ministry of Coal, "Re-verification (2026-10-23)".
- **Coal Gasification (D-021).** The pass found that
  `/major-statistics/national-coal-gasification-mission` is an active scheme
  dossier (59 dated rows). The user decided it stays **indirect**, not a
  tracked feed. What's New still captures its announcements; files posted only
  on that page are a known limit.
- **Accepted coverage limit.** Seven safety and Sustainable Development Cell
  narrative pages were never fetched in any session. They stay `indirect`.
- **CERC merge.** The six D-018 feeds, their `required_feed_ids` (25 in all),
  the 21-entry `coverage_decisions` list, and the D-020 site-level host-fallback
  keys are merged into `config/sources.yaml`. CERC is `research_status:
  complete`.
- **Ministry of Power** was already complete after the Citizen Engagement
  resolution.
- A live probe of `www.coal.nic.in` at 12:25 IST failed because the cloud
  environment's egress proxy refused the tunnel (HTTP 403). That is the
  environment's network policy, not a site block.

## Required inventory fields

Every discovered website section must eventually record:

- Stable feed ID and owner website
- Human-readable purpose
- Index and detail endpoints
- Discovery method (navigation, sitemap, search, API, or linked detail)
- Rolling, authoritative snapshot, rotating archive, or detail-parent behavior
- Pagination/enumeration termination rule
- Native identifiers and parent/document relationships
- Published, posted, modified, effective, and deadline date semantics
- Expected MIME and language variants
- Weekly and historical refresh policies
- Known block-page or malformed-response indicators
- Available-history boundary
- Track, indirect coverage, or exclude decision and reason
- Representative fixtures and last verification date

## Ministry of Power

Base: <https://www.powermin.gov.in/>

### Preliminary findings

- The frontend is Next.js backed by public WordPress-style JSON APIs.
- Headless Chromium received HTTP 403 during initial research; direct HTTP/API
  requests worked. Production must not depend on browser rendering.
- Category records can be parent collection posts whose nested attachment IDs
  change over time. Watching only newly created parents is insufficient.
- Displayed publication dates and ordering are not reliable cursors.

### Candidate feeds

| Feed | Endpoint | Preliminary role |
|---|---|---|
| What’s New | `https://www.powermin.gov.in/cms/wp-json/post-page/whats_new` | Rolling cross-site bulletin; discovery only, never removal authority |
| Taxonomy | `https://www.powermin.gov.in/cms/wp-json/taxonomy/documents_category` | Category discovery and contract validation |
| Documents | `https://www.powermin.gov.in/cms/wp-json/document/documents?document_category=<slug>&limit=<n>&page=<n>` | Paginated category enumeration |
| Parent detail | `https://www.powermin.gov.in/cms/wp-json/post-page/documents?slug_name=<slug>` | Parent/container metadata |
| Attachment detail | `https://www.powermin.gov.in/cms/wp-json/post-page/post?id=<id>` | File URL, title, date, size, and modified metadata |

Candidate document categories: `reports`, `acts-and-policy`,
`orders-and-notices`, `gazette-notifications`, `guidelines`, and `publications`.
Verify every current taxonomy slug rather than assuming this list is exhaustive.

### Verified API behavior (2026-10-22)

- The taxonomy endpoint returned 16 terms with stable integer `term_id`, `slug`,
  `parent`, and `count` fields. In addition to the six selected root categories,
  it exposes child terms and Parliament-question and press-release terms. Those
  non-selected terms still require explicit track/indirect/exclude decisions.
- The What's New endpoint returned 26 posts and `total_items: 26` with no page
  parameter or total-pages field. Each post has a native ID, slug, created and
  modified timestamps, menu order, and one or more attachment IDs under
  `acf_data.file`. It is a rolling feed and is not removal authority.
- Category enumeration returns `total_items`, `total_pages`, and `current_page`.
  With a limit of two, `acts-and-policy` returned four pages; page five returned
  a valid empty `posts` array while retaining the totals. Pagination terminates
  at `total_pages`, and the following empty page is a useful boundary fixture.
- Current selected-category counts were: Reports 9 parents, Acts and Policy 7,
  Orders and Notices 4, and zero parents for Gazette Notifications, Guidelines,
  and Publications. Empty selected categories must remain configured and an
  unexpected later collapse from nonzero to zero must be quarantined.
- Parent records contain attachment-record IDs in `acf_data.file`. The annual-
  report parent was created in June 2025, modified in May 2026, and currently
  contains 35 attachment groups. This verifies that old parents evolve and must
  be revisited.
- Attachment detail records contain a second media ID plus URL, byte size, MIME
  type, upload/modified timestamps, and a `pdf_type` language/layout label. One
  observed record used `Single Bilingual`.
- Sanitized fixtures now cover taxonomy, rolling discovery, pagination and its
  empty terminator, an evolved old parent, attachment detail, and the browser
  access-denied failure shape.

### Step 1 site-level review (2026-10-22)

All probes were read-only plain-HTTP GETs at about one request per second.
Headless Chromium was not used.

#### Navigation and route discovery

- The homepage is a client-rendered Next.js shell (`__NEXT_DATA__` with empty
  `pageProps`, build ID `CFzOpdsh5dnqm8hvr-SKu`) and contains no navigation
  links.
- `/sitemap.xml` returns valid XML, but all 50 `<loc>` entries point at
  `www.meity.gov.in`. It cannot establish Ministry of Power coverage.
- The WordPress REST route index `/cms/wp-json/` returns HTTP 403
  `rest_root_disabled`.
- The frontend bundle (`pages/_app`) references three public menu APIs. Each
  returns a JSON tree of `ID`, `title`, `url`, `menu_item_parent`, and `children`:
  - `/cms/wp-json/custom/v1/menus/website-menu` is the primary header menu, with
    7 top-level sections.
  - `/cms/wp-json/custom/v1/menus/global-information-architecture` is the fuller
    information architecture, also with 7 top-level sections plus Departments,
    Competition and Awards, International Collaboration, Initiative, Publications,
    Gazettes Notifications, Guidelines, Related Documents, Brochures,
    Presentations, Visitors Pass, the extra policies, and an expanded Archives
    branch.
  - `/cms/wp-json/custom/v1/menus/footer-menu` lists Archives, Website Policies,
    Related Links, Sitemap, Help, and Contact Us.
- Top-level sections are Ministry, Offerings, Documents, Media, Connect, Website
  Policies, and Archives.
- `_buildManifest.js` lists 42 page routes. Besides the menu sections it exposes
  `/whats-new`, `/search`, `/aisearch`, `/archives`, `/important-links`,
  `/RelatedLinks`, `/persona/[slug]`, `/dashboard`, `/profile`, `/feedback`,
  `/socialFeed/socialFeeds`, `/new-template`, and `/search-copy`. The route list
  depends on the build ID, so recheck it after a redeploy.

#### Hidden publication paths

- The bundle passes `post_status=archive` to the content APIs. Archived posts
  are absent from the default responses and from taxonomy counts.
- `/cms/wp-json/count-posts/archive` lists archived counts by post type. The
  non-zero counts are `whats-new` 637, `careers-post` 127, `tenders-post` 110,
  `acts-and-policy` 2, `central-documents` 2, `press-release` 1, and
  `top-banner` 1.
- `/cms/wp-json/post-page/whats_new?post_status=archive` returned all 637
  archived bulletin posts in one body. It has `total_items` and no pagination
  fields. Sending `limit` and `page` switches it to a paginated shape with the
  same ordering (`limit=5` gives `total_pages: 128`). Every post is a PDF item
  with attachment IDs. `post_date` values cluster between 21 June and 3 July
  2025, during the CMS migration, and are not publication dates. This archive is
  now covered by feed `mop_whats_new_archive`: a rotating archive with no removal
  authority, enumerated as one response checked against `total_items`.
- `document/documents?document_category=acts-and-policy&post_status=archive`
  returned 2 archived parents, one an archived Electricity Act version. The same
  query returned 0 for reports, orders-and-notices, gazette-notifications,
  guidelines, and publications. With `limit=1` the archive pass paginates like
  the published pass: `total_pages: 2`, and page 3 is an empty `posts` array with
  the totals kept. `mop_documents` now has an archive-status pass per root slug
  (`status_passes` in `config/sources.yaml`). Removal requires absence from both
  passes in two consecutive complete enumerations; a publish-to-archive move is
  a status change.
- The alternative bundle path `document/documents?type=Report` returned a
  WordPress HTML error page. The live frontend uses `document_category`.

#### Site search

- `/cms/wp-json/custom/api/search?s=<query>` returns `{data, total_items}` with
  mixed post types and statuses. For "electricity act" it returned 650 rows:
  298 `central_documents`, 187 archived `whats_new`, and also tenders, careers,
  organisation, division, scheme, and page records.
- Result `link` values point at the CMS backend host
  `cms-powermin.digifootprint.gov.in`, which is not in `allowed_hosts`.
- Search is a research cross-check, not a feed. Here it confirmed the archive
  path and showed `central_documents` as the attachment-record type.

#### Taxonomy terms and child-term behavior

The live taxonomy returned 16 terms, matching the prior snapshot.

- **Roots:** acts-and-policy 7, reports 9, orders-and-notices 4,
  gazette-notifications 0, guidelines 0, publications 0, press-release 51.
- **Children:** general_acts 6, general-report 7, general_orders 1,
  general_gazettes 0, general_guidelines 0, general_publications 0,
  general_press_release 2.
- **Parliament-question children of reports:**
  lok-sabha-parliament-questions-and-answer 1 and
  rajya-sabha-parliament-questions-and-answer 1.

Child-term behavior:

- Querying a child slug directly with `document/documents?document_category=<child>`
  returns `total_items: 0` even when the child count is non-zero. The API does
  not enumerate child terms on their own.
- Every child-tagged post observed is also tagged with its root term, so the
  root enumeration covers it. Reports 9 equals general-report 7 plus the two
  Parliament-question posts. This makes child terms indirect coverage, with one
  limit: a post tagged only with a child term would be missed. The
  `mop_documents` contract now includes a weekly child-term reconciliation
  failure indicator that compares child counts against root-enumerated posts
  carrying that child term.
- Under D-013, Press Release and Parliament Question terms are excluded as
  independent feeds and What's New appearances are still captured. The two
  current Parliament-question posts are also tagged `reports`, so `mop_documents`
  still captures them as Reports items.
- The Related Documents menu entry has no taxonomy term, and its slug returns
  an empty documents result.

#### Site-level coverage decisions

| Scope | Decision | Basis / feed | Coverage limit |
|---|---|---|---|
| Selected document collections (six roots, published) | track | `mop_documents`, `mop_document_taxonomy` | Published status only; three roots currently empty |
| What's New (published) | track | `mop_whats_new` | Rolling; never removal authority |
| What's New archive (`post_status=archive`, 637 posts) | track | `mop_whats_new_archive` | Rotating archive; no removal authority; migration post dates are never publication dates |
| Archived document parents (Archives menu; `post_status=archive`) | track | `mop_documents` (archive-status pass) | Archiving is a status change, not removal |
| Child terms of selected roots | indirect | Root enumeration (dual tagging) | Child-only tagging would be missed; weekly reconciliation indicator in `mop_documents` |
| Press Release root and child | exclude | D-013 | What's New appearances still captured |
| Parliament Question terms and `/connect/parliament-questions` | exclude | D-013 | What's New appearances still captured; dual-tagged posts remain in Reports |
| Related Documents menu entry | exclude | No backing term; empty API result | Review if a term appears |
| Attachment records (`central_documents`) | indirect | Parent and What's New attachment IDs | Unlinked attachment records not enumerated |
| Site search (`/search`, `/aisearch`, `custom/api/search`) | exclude | Diagnostic only | Not an enumeration source; CMS-host links |
| Ministry organisation pages and directories | exclude | Generic (plan section 1) | — |
| Attached organisations, PSUs, related and important links | exclude | External to the three-site boundary | Outbound links only |
| Offerings programme pages (schemes, awards, collaboration, initiatives) | exclude | Not an approved MoP family | What's New appearances captured |
| Tenders and vacancies, including archives | exclude | Plan section 1 | What's New appearances captured |
| Media (photos, videos, brochures, presentations), including archives | exclude | Generic (plan section 1) | — |
| Connect pages (contact, directory, RTI, grievance, citizen engagement, visitors pass) | exclude | Generic administrative; Citizen Engagement holds only the Citizen's Charter (verified 2026-10-23) | Weekly growth check on the `public-consultation` page's document list |
| Website policies and utility routes | exclude | No publications | Route list tied to build ID |
| Homepage widgets and auxiliary APIs | exclude | Presentation widgets | In-spotlight-only items would be missed |
| Navigation sources: homepage HTML, `/sitemap.xml`, REST index | exclude as coverage sources | Replaced by the menu APIs, taxonomy, and build manifest | Menu changes detected only at inventory refresh |

#### Citizen Engagement resolution (2026-10-23)

The `/connect/[slug]` page lazy-loads one component per Connect menu child. The
Citizen Engagement component (webpack module 48622) reads
`/cms/wp-json/wp/v2/pages?slug=public-consultation&acf_format=standard`. That
page (ID 3467, modified 2025-10-14) is titled "Citizen Charter". Its
`public_consultation_repeat` list holds one PDF attachment (ID 12224,
"Citizen's/Client's Charter of MoP", `Single Bilingual`). It contains no
consultation notices, so the generic-exclusion decision stands.

Because the backing page slug is `public-consultation`, the decision adds a
weekly health check: if the repeat list grows beyond its one charter entry, the
digest must warn so the page can be reconsidered as a feed. Fixture:
`ministry_of_power/citizen_engagement_page.json`.

#### Remaining before `research_status: complete`

All Ministry of Power feeds and site-level decisions are merged and verified,
and the site is `research_status: complete`.

#### Open items for user review (not blocking)

- **Schemes and Services.** Offerings programme pages, including schemes, are
  excluded because the plan's Ministry of Power families do not list schemes
  (the Coal families do). Confirm whether Ministry of Power schemes should be
  tracked.
- **Menu drift.** The three menu APIs are not configured feeds, so a new
  navigation section surfaces only at an inventory refresh. A cheap weekly
  menu-tree diff could be added.
- **CMS backend host.** Search results link to
  `cms-powermin.digifootprint.gov.in`. Keep it out of `allowed_hosts` unless a
  feed needs it.
- **Archive move between runs.** A What's New post that moves to the archive
  between the two fetches of one run may appear in both responses or in
  neither. Identity by post ID across `mop_whats_new` and
  `mop_whats_new_archive` handles the duplicate. The "neither" case is caught
  on the next run.

## Ministry of Coal

Base: <https://www.coal.nic.in/>  
Sitemap: <https://www.coal.nic.in/sitemap>  
What’s New: <https://www.coal.nic.in/whats-new>

### Preliminary findings

- What’s New is a broad dated table and has included draft-rule consultation,
  stakeholder suggestions, and public consultation material.
- Report collections vary widely in freshness; URL folder years and displayed
  dates are not always reliable cursors.
- Some annual/report lists link to nested detail pages rather than directly to
  files.

### Verified endpoint shapes (2026-10-22; boundaries corrected 2026-10-23)

Rows marked *(2026-10-23)* take the boundaries found by the re-verification
passes. Archive pages are reached through the archive URL rewrite rule below.

| Endpoint | Observed shape | Current contract consequence |
|---|---|---|
| `/sitemap` | Single HTML navigation tree with publication and generic branches | Discovery-only; compare link set but never infer publication removal |
| `/whats-new` | *(2026-10-23)* Four-column table with a **41-page pager** (`?page=0..40`; page 0's Last page link is `?page=40`). 2042 rows, newest 16 Oct 2026, oldest displayed date 26 Apr 2000. Malformed tail rows: one dated `08 Oct 0013`, four undated, one titled `test` with no link. No archive link | Track every row with paged enumeration; weekly pages until a page holds only known hrefs, monthly full enumeration. The silent baseline must enumerate every page. `kind: rolling`, never removal authority (reclassification deferred to Step 2) |
| `/public-information/monthly-statistics-at-glance` | *(2026-10-23)* Attachment table with a pager `?page=0..1`: 64 rows, September 2026 back to April 2021, with gaps. No archive | Paged enumeration of an authoritative collection; do not manufacture missing months |
| `/public-information/statistical-report` | *(2026-10-23)* Attachment table with a pager `?page=0..1`: 56 monthly rows, March 2025 back to April 2020. No archive | Paged enumeration; a distinct authoritative historical collection |
| `/major-statistics/production-and-supplies` | Narrative/data page with current tables and linked ten-year datasets | Indirect (D-016); the statistics feeds carry the same data |
| `/acts-rules-policies` | Five HTML tables, 172 HTML rows (20+116+9+12+10 = 167 data rows, 26/11/1960 to 29/06/2026), covering Acts, rules, policies, methodologies, and notifications. No pager or archive (2026-10-23) | Track as one authoritative collection; do not use upload-folder years as publication dates |
| `/major-statistics/quarterly-booklet` | One table with 10 data rows, no S.N column, DD-MM-YYYY dates (17-09-2026 back to 22-05-2024). No pager or archive (2026-10-23) | Track as authoritative collection |
| `/major-statistics/rsr-report` | One table with 1 undated data row. No pager or archive (2026-10-23) | Track; a later unexpected empty table is suspicious |
| `/major-statistics/coal-statistics` | One table with 17 data rows. No pager or archive (2026-10-23) | Track as authoritative collection |
| `/sustainable-development-cell/report` | One table with 3 data rows, no S.N column, 24-10-2024 back to 01-08-2024. No pager or archive (2026-10-23) | Track sustainability guidelines and reports |
| `/nominated-authority/sanction-order-notice` | *(2026-10-23)* Pager `?page=0..4`: 233 rows, 06/10/2026 back to 28/10/2016. **Archive** `/nominated-authority/archive-sanction-order-notice`: 63 undated rows over `?page=0..1` (2014–2015 Ordinance-era material) | `rotating_archive`; removal requires absence from every current and archive page in two consecutive complete enumerations, and a current-to-archive move is a status change |
| `/nominated-authority/termination-order` | *(2026-10-23)* Explanatory 32-block table plus an attachment table of 32 rows (29/06/2026 back to 13/01/2017), no pager. **Archive** `/nominated-authority/archive-termination-order`: 1 undated row on one page, href not in the current table | Enumerate attachments from the second table plus the archive; preserve explanatory context separately; `rotating_archive` with the current-plus-archive two-run removal rule |
| `/nominated-authority/national-coal-index` | *(2026-10-23)* Pager `?page=0..1`: 83 rows (82 distinct hrefs), 29/09/2026 back to 05/06/2020. **Archive** `/nominated-authority/archive-national-coal-index`: 4 undated rows, no pager | `rotating_archive` with the same removal rule as compensation orders |
| `/nominated-authority/national-lignite` | One table with 11 data rows, DD-MM-YYYY dates (16-10-2026 back to 07-12-2023). No pager or archive (2026-10-23) | Track as authoritative monthly index collection |
| `/public-information/monthly-summary-cabinets` | *(2026-10-23)* 48 rows (February 2025 back to August 2020), no pager. **Archive** `/public-information/monthly-summary-cabinets/archive-monthly-summary-cabinets`: 77 rows over `?page=0..1` (December 2022 back to September 2014). Period ranges overlap but no href is shared | `rotating_archive` with the current-plus-archive removal rule |
| `/public-information/standing-linkage-committee1` | 89 rows across pager pages 0 and 1; oldest displayed date 16 August 2017. Re-checked 2026-10-23: page 0 starts 2026-06-15, Last page still `?page=1`, no archive | Track all rows; page 1 is the verified pager terminus |
| `/public-information/reports/annual-reports` | 18 parent rows (2009-10–2025-26) and 355 child attachments across all detail pages (children enumerated 2026-10-22; parent index re-checked 2026-10-23, no pager or archive) | Track every parent and nested child; periodically revisit historical children because old parents evolve |
| `/public-information/reports/budget-reports` | 18 parent rows (2009-10–2026-27) and 38 child attachments across all detail pages (children enumerated 2026-10-22; parent index re-checked 2026-10-23, no pager or archive) | Track every parent and nested child |
| `/public-information/reports/other-reports` | 3 parent rows, each with one child attachment (parent index re-checked 2026-10-23, no pager or archive) | Track every parent and nested child |
| `/public-information/action-plan` | Direct list of 3 plans (two coal.gov.in PDFs and a coal.gov.in/actionplan HTML page for FY 2024). No archive (2026-10-23) | Track list; it is not a table |
| `/public-information/monthly-report-cabinet` | *(2026-10-23)* Current pager `?page=0..1`: 56 rows (October 2025 back to November 2018). Archive `/public-information/monthly-report-cabinet/archive-monthly-report-cabinet` pager `?page=0..1`: 66 rows (September 2021 back to April 2015); one archive row (September 2016) has no link | Paged current plus paged archive as one rotating-archive feed, with the current-plus-archive removal rule |
| `/public-information/paper-laid-table` | One table with 11 data rows. No pager or archive (2026-10-23) | Track all rows even when the title embeds the only publication date |
| `/public-information/sop` | One table with 1 data row (08-08-2024). No archive (2026-10-23) | Track as authoritative collection |
| `/CSR/guidelines` | One table with 8 data rows, all dated 03-04-2024 (a posting date). No archive (2026-10-23) | Track CSR policies, guidelines, FAQs, and clarifications |
| `/central-sector-schemes` | One canonical narrative page describing 3 schemes and an embedded 6-row budget table (BE/RE 2022-23), no files, no archive (2026-10-23) | Track page-level content changes; it currently has no publication attachments |

The Acts/Rules/Policies collection currently exposes 169 document-like links
across the whole rendered page, including unrelated navigation/footer files.
Adapters must scope enumeration to the five content tables. Numerous historical
rules appear under a `2026-06` upload path, proving that URL folder dates are not
publication dates or reliable incremental cursors.

The two Cabinet collections are not interchangeable. “Monthly Summary for
Cabinet” and “Monthly Reports for Cabinet” are separate pages, and the latter
has an explicit archive. A visible typo labels a September 2024 row as the
second item among 2025 entries; displayed period labels and posting dates must be
preserved independently rather than silently corrected.

### Candidate publication families

- Acts, rules, and policies
- Monthly statistics and statistical reports
- Quarterly booklets, RSR reports, and coal statistics
- Monthly cabinet summaries/reports
- Meeting minutes and Standing Linkage Committee material
- Annual, budget, other, action-plan, SOP, and sustainability reports
- Critical/super-critical coal stock material
- Papers laid on the table
- Nominated Authority sanction and termination notices
- National Coal and National Lignite indices
- CSR guidelines and central-sector schemes

### Site-level coverage review (2026-10-22)

The live sitemap (`/sitemap`, Drupal) was enumerated in full: 279 anchors,
including header, primary-menu, sitemap-tree, and footer duplicates. Every
internal branch was mapped either to a configured feed or to a group decision.
Ambiguous branches were probed once at about one request per second. After
roughly 70 requests, the server began returning an **HTTP 200 CAPTCHA
"Validation request" page** (error code 421) for every URL. Probing stopped and
the remaining pages were decided from sitemap evidence. The 2026-10-23
re-verification passes later fetched them; see "Re-verification (2026-10-23)".

#### Observed behaviour relevant to contracts

- **What's New is not in the sitemap tree.** It is reached from the homepage
  What's New block (`/whats-new`). The homepage also carries a rolling Press
  Release block and outbound portal links (SWCS, CIMS imports, star rating,
  CMPDI dashboard, MSTC auctions).
- **Archive pages are a hidden publication path.** Many Drupal views end with an
  "Archive ..." link to a page that is absent from the sitemap, for example
  `/public-information/minutes-metings/archive-minutes-metings`,
  `/media/press-release/archive-press-release`,
  `/nominated-authority/archive-auction-allotment`, and
  `archive-review-meeting-scrutiny-committee`, `archive-single-window-system`,
  `archive-vacancy`, `archive-tender-na`,
  `/public-information/advertisements/archive-advertisements`, and
  `/public-information/grant-in-add/archive-grant-in-add`. The 2026-10-23
  passes found more on tracked feeds:
  `/nominated-authority/archive-sanction-order-notice`,
  `/nominated-authority/archive-national-coal-index`,
  `/public-information/monthly-summary-cabinets/archive-monthly-summary-cabinets`,
  and `/nominated-authority/archive-termination-order` (plus the already
  modelled `monthly-report-cabinet` archive). Archive tables usually lack a
  Date column (S.N/Title/Attachment only); the minutes archive is the dated
  exception. Archive links are emitted as `http://www.coal.nic.in//...`, with
  an HTTP scheme and a double slash, so adapters must normalise them before
  dereferencing.
- **Anti-bot script.** Since 2026-10-23 every page carries an injected
  `<script src="/bnith__…">` tag whose token changes on each request. Re-fetches
  were otherwise byte-identical, so content and ordering are deterministic per
  URL, but page fingerprints must exclude this script.
- **Offset pagers over tied dates.** Drupal pagers page by offset over a
  non-unique date sort. Rows sharing a date can repeat across a page boundary
  (seen on Auction/Allotment) and others may be silently skipped. Identity is
  by href, and one enumeration pass never proves completeness.
- **Out-of-range page.** A `?page=N` past the pager terminus returns HTTP 200
  with the heading and pager but no tables. It is not an empty collection.
- **Block page.** The anti-automation response is HTTP 200 `text/html`, about
  1 KB, with `<title>Validation request</title>` and a `captcha_resp` form. It
  must be quarantined as a block page and never parsed as an empty collection.
  Fixture: `ministry_of_coal/captcha_block.html`. Production should stay at or
  below the configured rate, and should probably add backoff and a per-run
  request budget, because 1.2 s spacing triggered the block within about 70
  requests.
- The footer `Last Updated` label (21 Oct 2026 on every page observed) is
  site-wide. It is a health signal only, never a page modification date.
- Several tables render duplicate rows (Energy Cell, Grant-in-Aid). Collapse
  them to one document with two appearances.
- Files often live on `http://coal.gov.in/sites/default/files/<folder>/` with
  upload folders unrelated to the meeting or publication date (for example the
  `2023-06/` folder holds 2021–2025 minutes).
- `/major-statistics/generation-of-thermal-power-from-raw-coal` (in the sitemap)
  returned **404**.
- **Critical and Super Critical Coal Stocks** is only an outbound link to
  `coalindia.in/categorization-tpp-coal-stocks/`, so it is excluded as external.
  The candidate family is resolved as "not Ministry-hosted".

#### Site-level coverage decisions

| Scope | Decision | Feed(s) / reason |
|---|---|---|
| What's New (homepage block + `/whats-new`) | track | `coal_whats_new`; 41-page pager, 2042 rows back to 2000; rolling, never removal authority |
| Acts & Policies | track | `coal_acts_rules_policies` |
| Major Statistics tables | track | `coal_monthly_statistics`, `coal_statistical_reports`, `coal_quarterly_reports`, `coal_rsr_reports`, `coal_coal_statistics` |
| Public Information collections | track | `coal_cabinet_summaries`, `coal_monthly_cabinet_reports`, `coal_standing_linkage_committee`, `coal_action_plans`, `coal_papers_laid`, `coal_sop` |
| Reports (annual/budget/other) | track | `coal_annual_reports`, `coal_budget_reports`, `coal_other_reports` |
| NA orders and indices | track | `coal_compensation_orders`, `coal_termination_orders`, `coal_national_coal_index`, `coal_national_lignite_index` |
| Sustainability report, CSR guidelines, Central Sector Schemes | track | `coal_sustainability_reports`, `coal_csr_guidelines`, `coal_central_schemes` |
| NA Auction/Allotment (pages 0–8) + archive | track (D-015) | `coal_auction_allotment`; verified 2026-10-23 |
| Meeting minutes: general (+ archive), NA Production Reviews, NA Operationalisation Reviews | track (D-015) | `coal_minutes_of_meetings` (archive verified 2026-10-23), `coal_production_reviews`, `coal_operationalisation_reviews` |
| NA Scrutiny Committee, Single Window System, Energy Cell | exclude (D-015) | Not independent feeds; What's New appearances still captured |
| Ministry Press Releases + archive + homepage block | exclude (D-014) | Duplicates PIB communication; What's New appearances still captured |
| Production and Supplies | indirect (D-016) | Statistics feeds carry the same data; narrative fingerprint would be noisy |
| Sitemap and hub pages | indirect | `coal_sitemap` link-set comparison |
| Narrative/thematic pages (statistics narratives, SDC topics, safety, roadmap, flipbook, Chintan Shivir, procurement projection, About CSR/NA, tranche-wise allocation) | indirect | Static descriptive pages; new material is expected in What's New. Seven pages never fetched (accepted limit, below) |
| Coal Gasification (`/major-statistics/national-coal-gasification-mission`) | indirect (D-021) | Active scheme dossier, not a tracked feed. What's New captures its announcements; files posted only on this page are a known limit |
| Generic organisation/site pages | exclude | About Us, Who's Who, ministers, Organisations, Contact, Help, Screen Reader, policies, Feedback, Technical Help Desk |
| Administrative compliance | exclude | Citizen Charter, Grievance Cell, RTI hub/PIO/proactive disclosures, Grant-in-Aid |
| Operational procurement/recruitment | exclude | Tenders (open/past), NA Tender, NA Vacancy, Advertisements (+ archives) |
| Galleries and legacy showcases | exclude | Photo Gallery, SDC Videos, CSR gallery PDF, Success Stories (2019), Mines Summary (2019) |
| External outbound links | exclude | PIB, Sansad (Parliament Q&A), rti.gov.in, Coal India critical-stock page, coal-block portals, SWCS/CIMS/CPPP/MSTC, india.gov.in footer, social media |
| Site search and Hindi interface | exclude | Drupal keyword search is not enumerable (149 pages, repeated results, index timestamps). Hindi re-presents the same nodes; English metadata is authoritative. Hindi translation files reachable only from `/hi` are not collected |

The four D-015 feeds and the coverage list are merged into `config/sources.yaml`
and `required_feed_ids`.

#### New feed boundaries (observed 2026-10-22, completed 2026-10-23)

| Feed | Observed boundary | Status |
|---|---|---|
| `coal_auction_allotment` | Each page holds a state statistics table (ignored) and the document table. `?page=0..8` hold 8 × 50 + 14 = 414 rows (S.N 1–414, no gaps), 07/10/2026 back to 19/04/2017, dates never increasing. Every page's Last page link is `?page=8`; `?page=9` renders the pager with no tables. 406 distinct hrefs: 7 repeats at page boundaries where rows share a date, 1 within page 1. Archive `/nominated-authority/archive-auction-allotment` (heading typo "Archive Auction /Allotmnet"): 40 undated rows on one page, three columns, files in 2021 upload folders, December 2014 to June 2021 titles, no href shared with the current view | Verified 2026-10-23 |
| `coal_minutes_of_meetings` | 23 current rows, 2021-01-29 through 2025-10-07, no pager (observed 2026-10-22). Archive `/public-information/minutes-metings/archive-minutes-metings`: 93 rows over `?page=0..1` (50 + 43), 93 distinct hrefs, four columns with YYYY-MM-DD dates from 2005-06-02 to 2019-12-13, **not in date order**; no href shared with the current table | Verified 2026-10-23 |
| `coal_production_reviews` | 25 rows (24 distinct hrefs), 19-10-2022 through 24-07-2025, no pager, no archive | Verified |
| `coal_operationalisation_reviews` | Summary table plus 26 rows (24 distinct hrefs), 15-09-2021 through 23-05-2025, no pager, no archive | Verified |

**Archive URL rewrite rule:** archive hrefs are emitted as
`http://www.coal.nic.in//<path>`. Rewrite them to `https://www.coal.nic.in/<path>`
(HTTPS scheme, collapse the leading double slash) before fetching. This is a
coal-specific, explicitly tested rule; other query parameters are retained.

**Request rate (D-017):** at most 0.25 requests/second with backoff. On a
CAPTCHA "Validation request" page, stop the run for the host, quarantine it,
and preserve prior state. Do not retry in a loop.

#### Language policy resolution

- The site exposes a language dropdown (`en`/`hi`, a POST form to the current
  path). The Hindi interface is not crawled independently. English pages and
  English listing metadata are authoritative (D-005).
- Where a tracked English page links **distinct** English and Hindi files for
  the same item (for example annual-report chapters; fixture
  `nested_report_detail.html`), preserve both link variants as **separate
  documents under one row/matter**, record each file's language, and keep the
  English listing metadata even when the Hindi file is the only usable one.
  Identical hrefs collapse to one document.
- The `/hi` homepage What's New block was spot-checked on 2026-10-23. Its 21
  items mirror English What's New rows; 9 link the same file and about 12 link
  a distinct Hindi-language PDF (for example `…-wnhi.pdf`) that the English
  listing never links. No Hindi-only publication was found. The Hindi block
  shows a bracketed date, for example `(30/09/2026)`.
- Known limitations: Hindi-only nodes that never appear on English listings
  would not be detected, and Hindi translation files reachable only from `/hi`
  are not collected.

#### Re-verification (2026-10-23)

Two passes at D-017 pacing (75 requests, at least 4.5 s apart) received no
CAPTCHA. The full request logs were merged here from the Step 1 working notes.

Verified:
- `coal_auction_allotment`: 414 rows across `?page=0..8` (07/10/2026 back to
  19/04/2017); `?page=9` renders the pager with no tables; the archive has 40
  undated rows on one page. Offset paging over tied dates repeats 7 hrefs at
  page boundaries (406 distinct hrefs in 414 rows), so identity is by href and
  one pass never proves completeness.
- `coal_minutes_of_meetings`: the archive has 93 rows across `?page=0..1`,
  dated 2005-06-02 to 2019-12-13 and not in date order.
- `coal_termination_orders`: the archive has one undated row on a single page.
- The archive URL rewrite (`http://www.coal.nic.in//<path>` to
  `https://www.coal.nic.in/<path>`) was exercised live on seven archive pages.
- Pagers or archives missing from the 2026-10-22 contracts are now modelled
  for What's New (41 pages, 2042 rows back to 2000, with malformed tail rows;
  the silent baseline must enumerate every page), compensation orders (5 pages
  and a 2-page undated archive), National Coal Index (2 pages and a 4-row
  archive), Monthly Statistics (2 pages), Statistical Report (2 pages), Monthly
  Summary for Cabinet (2-page archive), Monthly Reports for Cabinet (2 current
  pages and a 2-page archive), and termination orders (1-row archive).
- Every other tracked feed was re-checked and has no pager or archive. The
  annual, budget, and other report detail pages were last enumerated on
  2026-10-22; only their parent indexes were re-checked.
- Coal Gasification (`/major-statistics/national-coal-gasification-mission`)
  is an active scheme publication collection: nine Title/Attachment/Date
  section tables (one nested), 59 rows with 57 distinct hrefs, 16/09/2026 back
  to 21/09/2021, one undated row, absolute coal.gov.in hrefs with mixed schemes
  and 7 doubled path slashes, no pager or archive. It includes the gasification
  scheme approval, RFPs dated 13.08.2026, pre-application notices, and query
  responses. By user decision **D-021 it stays `indirect`**, not a tracked feed:
  What's New captures its announcements, and many rows already appear there.
  Fixture `coal_gasification.html` is the evidence for that decision.
- Narrative pages confirmed `indirect`: Coal Grades, Coal Reserves, Mission
  Coking Coal, Grading of Coal, Coal Evacuation Plan, Clearances, Technology
  Roadmap, Achievements Flipbook, Chintan Shivir, Procurement Projection, About
  CSR, Safety Statistics, Safety Initiatives, About SDC, Greening Initiatives,
  Mine Water Utilization, and Promoting Renewable. The ones with files hold
  small static sets unchanged since 2021–2024. Hubs confirmed: `/CSR`,
  `/about-safety-coal-mines`, `/sustainable-development-cell`. Coal Evacuation
  Plan, Clearances, and the SDC hub render escaped HTML rows as text, which
  must not be parsed as rows (fixture `narrative_static_tables.html`).
- The Hindi interface mirrors English What's New rows (spot-checked; no
  Hindi-only item found), often linking distinct Hindi PDFs that are not
  collected. Site search (149 pages, repeated results) is not enumerable. Both
  stay excluded.
- Pages carry an injected anti-bot `<script src="/bnith__…">` whose token
  changes on every request; exclude it from page fingerprints.

Accepted coverage limits:
- **Never fetched (7 pages):** `/major-statistics/safety-coal-mines` (About
  Safety), `/safety-coal-mines/accident-portal`, and the SDC pages
  `air-quality-management`, `eco-tourism-in-mining-area`, `energy-efficiency`,
  `gainful-utilization`, and `sdc-videoes`. Every fetched sibling was
  text-only; they remain `indirect`. About 7 requests would confirm them later.
- **Coal Gasification (D-021):** files posted only on the gasification page and
  never announced in What's New are not captured.
- Unannounced edits to indirect narrative pages, including a new file added to
  one of their small static tables, are not detected.

Notes for Step 2 and operations:
- What's New is a 2042-row history, not a short rolling list. It stays
  `kind: rolling` with `removal_authority: false`; Step 2 may reclassify it.
  The weekly refresh should stop early once a page holds only known hrefs.
- New contract keys used on Coal and accepted by the validator but not yet in
  a schema: `archive_url`, `archive_href_rewrite` (`applies_to`, `rewrite_to`,
  `rule`), `completeness.removal_rule`, and `completeness.notes`.
- A tested identity rule is needed before merging coal.gov.in hrefs that
  differ only in scheme or doubled slashes; until then raw hrefs are kept.
- A full Coal enumeration costs roughly 85–90 requests, about 6 minutes at
  0.25 req/s. `docs/OPERATIONS.md` should record this, the anti-bot script, and
  the CAPTCHA block signature (`<title>Validation request</title>` or a
  `captcha_resp` form).

## CERC

Base: <https://cercind.gov.in/>  
Sitemap: <https://cercind.gov.in/site_map.html>

### Candidate feeds

The initial candidate list below is kept for history. The verified contracts,
including the D-018 additions, are in the tables that follow.

| Family | Preliminary endpoint |
|---|---|
| Home/current bulletin | `index-en.html`, `viewall.html` |
| Draft regulations | `Draft_reg.html` |
| Current/final regulations | `Current_reg.html` |
| Discussion/working/study material | `Disc_Paper.html`, `Work_Paper.html`, `Study_Rep.html` |
| Current orders | `recent_orders.html` |
| Current records of proceedings | `recent_rops.html` |
| Publications | `report_MM.html`, `annual_report.html`, `Other_Report.html` |
| Hearings/schedules | `public.html`, `Sched_hear.html` |
| Notices/advice | `public-notice.html`, `SPN.html`, `advice_gov.html` |

### Preliminary findings

- Draft rows evolve after initial publication by adding extensions, comments,
  public hearings, and presentations.
- Regulation rows can later gain Gazette files, notifications, statements of
  reasons, consolidated files, corrigenda, procedures, and addenda.
- Current orders are high-volume and include petition number, order date, posted
  date, and category.
- Homepage notices may expose stakeholder consultations before a dedicated list.
- Site-level “last updated” dates are health signals, not cursors.

### Step 1 research status (updated 2026-10-23)

The four original open items are resolved:

- ~~Enumerate archived years and confirm whether current pages link to the complete
  history.~~ **Resolved.** `cerc_order_archives` covers 27 annual pages
  (1999–2025) and `cerc_rop_archives` covers 18 (2008–2025). The homepage
  bulletin's annual archives (`Archive.html`, 25 pages covering 1999–2026) are
  **excluded** by D-019 (see the known limit below).
- ~~Identify stable native matter/document keys across evolving rows.~~ **Resolved
  for inventory purposes.** Every verified contract records native IDs, such as
  displayed serial plus child href, petition number plus href, leaf page plus
  href, or document href. Final identity rules remain a Step 2 decision.
- ~~Determine the row-to-child-link relationship for each page family.~~
  **Resolved** by the 25 verified contracts and their fixtures. Draft and
  discussion rows also link HTML child pages (`Comment-N-SM-YYYY.html`,
  `Presentation-N-SM-YYYY.html`), and these belong to the parent row contract.
- ~~Capture fixtures for an evolving draft, deadline extension, final regulation
  with later files, routine order, public notice, and malformed/empty table.~~
  **Resolved** by `draft_regulations.html`, `current_regulations.html`,
  `recent_orders.html`, `public_notices.html`, and the other CERC fixtures. A
  malformed or empty-table fixture remains a Step 3 parser-test item, not an
  inventory gap.

#### New feeds from the site-level review (D-018)

| Feed ID | Endpoint | Verified boundary (2026-10-23) |
|---|---|---|
| `cerc_commission_minutes` | `/Minutes_comm.html` | 53 minutes, 11 August 2009 to 17 April 2026 |
| `cerc_cac_minutes` | `/AD_minutes.html` | 14 Central Advisory Committee minutes, 18 March 2009 to 26 September 2023 |
| `cerc_other_meeting_minutes` | `/other_minutes.html` | 106 attachment links (101 unique hrefs) in an 82-row table, 2010–2020; Validation Committee and task-force meetings |
| `cerc_coordination_forum_minutes` | `/Minutes_Coord.html` | 6 minutes, 12 August 2008 to 30 March 2010 (dormant, still enumerated) |
| `cerc_price_indices` | `/coal_price.html`, `/coal_price_index_base.html` | 4 coal-price-index documents, including the collapsed base 2022-23 accordion (workbook updated 13.03.2026), and 5 CIL price notifications from 29 May 2016 to 4 April 2025 |
| `cerc_escalation_rates` | `/escalation_rates.html` | 22 year pages (2026–2005), 38 leaf pages, 319 PDF links (318 unique) dated 20-09-2005 to 01-10-2026 |

The escalation-rates tree has two layouts:

- **2011–2026.** Year pages (`escaltion_YYYY.html`, which is the site's own
  spelling) are hubs. Each links `escalation_rates_gen_pro_YYYY.html` and
  `escalation_rates_tran_ser_YYYY.html`.
- **2005–2010.** Year pages (`escalation_rates_YYYY.html`) list the documents
  directly.
- **Generation pages from 2022.** Documents sit in collapsed Annual, Six-Monthly,
  and Monthly accordion panels, and all panels must be parsed.
- **Row shape.** Rows are notification-and-explanation pairs, sometimes followed
  by an addendum, amendment, corrigendum, or validation order. Numbering restarts
  at 1 for each notification.
- **Dates.** A monthly notification is issued after the month it covers; for
  example, the September 2026 rates were notified on 01-10-2026.
- **Hazards.**
  - Filenames contain spaces.
  - One 2010 href is an absolute `http://cercind.gov.in/` URL. The HTTPS rewrite
    was tested and returned 200 `application/pdf`.
  - One 2010 href appears twice with different link text.
  - Several leaf pages hold commented-out stale hrefs.

#### Additional observations (2026-10-22 to 2026-10-23)

- **Host behaviour:** see decision D-020 and the site-level host keys in
  `config/sources.yaml`. Both
  hosts had outage windows of several minutes in which the TLS handshake hung.
  Outside those windows, each host in turn occasionally reset single connections
  while the other answered at once. Production fetching needs a 120 s timeout,
  apex-to-`www` fallback on connection-level failures, bounded spaced retries, a
  run-level circuit breaker that preserves state, and identity based on the
  canonical host.
- **Search:** `search.html` is an embedded Google Custom Search widget. It is not
  an enumerable endpoint and exposes no hidden publication path.
- **Hidden publication paths:** the four minutes pages, the coal price index
  pages, and the escalation-rates tree were reachable from no existing feed.
  They are now tracked (D-018). `Repeal_Reg.html` and the homepage annual
  archives were also hidden; they are excluded (D-019).
- **Language:** `index.html` and `index_hin.html` are the Hindi homepage; the
  English homepage is `index-en.html`. Hindi mirror pages (`*_hin.html`) reuse
  the English attachment hrefs. `Draft_reg_hin.html` shared 532 document links
  with `Draft_reg.html`, lagged the English page, and held no document missing
  from the English pages. Paired English/Hindi SPN newspaper files remain
  distinct documents.
- **Dead navigation:** `recent_orders_rops.html` (linked from the sitemap and
  the homepage "Recent Order & ROP" item) and `Current_petition_status.html`
  return HTTP 404. These are dead links, not feed failures.
- **Parser hazard:** `Repeal_Reg.html`, the escalation leaf pages, and other
  pages contain HTML comments holding stale links. Parsers must ignore comment
  content.
- **Replacement detection:** PDFs return `Last-Modified` and `ETag`. After the
  initial hash, these validators are the replacement signal, and a silent
  replacement that does not change them cannot be caught.

#### Site-level coverage decisions

| Scope | Decision | Feed IDs | Fixture evidence |
|---|---|---|---|
| Current and draft regulations | track | `cerc_current_regulations`, `cerc_draft_regulations` | `current_regulations.html`, `draft_regulations.html` |
| Repealed regulations (`Repeal_Reg.html`) | exclude (D-019); bulletin appearances remain captured | none | `repealed_regulations.html` |
| Discussion/working/study papers and `Paper.html` landing | track | `cerc_discussion_papers`, `cerc_working_papers`, `cerc_study_reports` | three paper fixtures |
| Orders, ROPs (current and archives), hearing schedule | track | `cerc_recent_orders`, `cerc_order_archives`, `cerc_recent_rops`, `cerc_rop_archives`, `cerc_hearing_schedule` | order/ROP/schedule fixtures |
| Market Monitoring, annual, and other reports | track | `cerc_market_monitoring`, `cerc_annual_reports`, `cerc_other_reports` | report fixtures |
| Public hearings, public notices, SPN, statutory advice | track | `cerc_public_hearings`, `cerc_public_notices`, `cerc_statutory_public_notices`, `cerc_statutory_advice` | notice/advice fixtures |
| Meeting minutes (Commission, CAC, other, Coordination Forum) | track (D-018) | `cerc_commission_minutes`, `cerc_cac_minutes`, `cerc_other_meeting_minutes`, `cerc_coordination_forum_minutes` | `meeting_minutes.html` |
| Home bulletin and view-all | track | `cerc_home` | `home_bulletin.html` |
| Homepage annual archives (`Archive.html` and year pages) | exclude (D-019); see the known limit below | none | `home_archives.html` |
| Coal price index, CIL notifications, escalation rates | track (D-018) | `cerc_price_indices`, `cerc_escalation_rates` | `price_indices.html`, `escalation_rates.html` |
| Sitemap and navigation-only landing pages | indirect | `cerc_sitemap` | `site_map.html`, `site_map_branches.html` |
| Site search | exclude | none (third-party widget) | `non_feed_pages.html` |
| Consolidated regulation compilations (2010, 2016) | exclude (frozen) | none | `site_map_branches.html` |
| Acts, MoP rules, national policies, bidding guidelines | exclude (frozen copies; Ministry of Power feeds relied on) | none | `site_map_branches.html` |
| Section 62(5) data, technical validation (D-019), compliance status, petition status/data, licensee lists | exclude (operational data) | none | `non_feed_pages.html` |
| Dormant regulatory affairs and correspondence | exclude (D-019) | none | `site_map_branches.html`, `non_feed_pages.html` |
| Press releases, Hindi newsletter, compendium, media, gallery | exclude (dormant/outreach) | none | `site_map_branches.html` |
| Generic organisation and accessibility pages | exclude (generic) | none | `site_map_branches.html` |
| Tenders, vacancies, RTI, medical empanelment, forms, e-filing message | exclude (operational) | none; bulletin items still captured by `cerc_home` | `site_map_branches.html`, `home_bulletin.html` |
| Hindi site variant and `*_hin.html` mirrors | exclude (duplicate) | none | `hindi_mirror.html` |
| External systems and sites (e-filing portal, IP-address systems, APTEL, Forum of Regulators, POSOCO/Grid-India, MoP) | exclude (external) | none | `non_feed_pages.html`, `site_map_branches.html` |

Items from every excluded section that appear on the tracked homepage or
view-all bulletin remain captured by `cerc_home` (D-001).

**Known limits:**

- **Homepage archives.** Older consultations that survive only in the homepage
  annual archives are not baselined (D-019). One example is the 2009
  transmission-lines cost-model consultation on `Archive2009.html`.
- **Repealed regulations.** A regulation leaving `Current_reg.html` is seen by
  `cerc_current_regulations`, but its arrival on `Repeal_Reg.html` is not.
- **Press releases.** Comparing the sitemap's links would not notice a revived
  press-release series, because new year links are added on
  `press_releases.html`, not in the sitemap.
- **Ministry rules.** A Ministry rule or policy posted only on CERC is missed
  unless the CERC bulletin or a Ministry of Power feed carries it.

The 21-entry `coverage_decisions` list, the six D-018 feeds and their
`required_feed_ids`, and the D-020 host keys were merged into
`config/sources.yaml` on 2026-10-23. CERC is `research_status: complete`.

### Verified CERC endpoint shapes (2026-10-22)

| Endpoint | Observed shape | Current contract consequence |
|---|---|---|
| `/site_map.html` | Single HTML navigation tree including current/prior orders and ROPs, regulation families, publications, hearings, notices, advice, and minutes | Discovery-only; every branch still needs an explicit decision |
| `/Draft_reg.html` | One year-grouped table, serials 1–120 (2013–2026), with multiple child links per matter | Track as evolving matters; revisit old rows and compare child-link sets |
| `/Current_reg.html` | One table, serials 1–208 (1999–2026), with Gazette, notification, reasons, principal-regulation, and amendment links | Track as evolving matters; nested amendment panels are part of the row contract |
| `/recent_orders.html` | Current-year six-column table, serials 1–526 | Rolling current-year feed; capture all rows and never infer removal from rollover |
| `/recent_rops.html` | Current-year six-column table, serials 1–1826 | Rolling current-year feed; capture all rows and never infer removal from rollover |
| `/Disc_Paper.html` | Serial-numbered evolving table, serials 1–32, with notices, papers, extensions, comments, hearings, and presentations | Track as evolving consultation matters |
| `/Study_Rep.html` | Seven-row report table with posting dates, oldest 09.04.2019 | Track as authoritative collection |
| `/public-notice.html` | Eleven visible general notice matters from 2021–2026 | Track as authoritative collection; one row exposes more than one date |
| `/SPN.html` | Petition-keyed table reaching August 2019, normally with paired English/Hindi newspaper files | Track each row as a matter and each language file as a document |
| `/advice_gov.html` | Statutory-advice link list reaching May 2010 | Track as authoritative collection; dates are embedded in link text |

The current draft table demonstrates why a row cannot be treated as a single
document: a matter may later acquire an extension, comments, a hearing notice,
and presentation files. Displayed serial plus child URL is useful provenance,
but final identity rules remain a Step 2 decision after the inventory gate.

Current-year order and ROP pages include records whose hearing/order date falls
in the prior calendar year but whose posting date is in the current year. Feed
membership follows the site's current-year page and posting behavior; it must
not be reconstructed from the order or hearing date alone.

### Additional verified CERC contracts (2026-10-22)

| Endpoint | Verified boundary | Contract consequence |
|---|---|---|
| `/Work_Paper.html` | Two January 2021 working papers | Authoritative collection distinct from discussion and study material |
| `/orders.html` | 27 prior-year pages covering 1999–2025 | Follow every annual page; current-year rollover never establishes removal |
| `/record_proceeding.html` | 18 prior-year pages covering 2008–2025 | Follow every annual page; current-year rollover never establishes removal |
| `/report_MM.html` | 14 annual reports covering 2008-09–2021-22 | Track the complete reporting-period list |
| `/annual_report.html` | 18 reports covering 1998-99–2024-25, with gaps | Track every exposed report without manufacturing missing years |
| `/Other_Report.html` | One expert-group report | Track as a distinct authoritative collection |
| `/public.html` | 78 main-content document links; oldest visible hearing is in 2015 | Track each matter and every later notice, comment, or supporting link |
| `/Sched_hear.html` | 21 documents: one hearing-mode notice plus January–October 2026 advance and weekly lists | Rolling schedule; preserve prior observations and never infer removal |

The homepage exposes 18 document links and `viewall.html` exposes 17. Both mix
current notices with persistent older resources, so the home contract is rolling
discovery and never removal authority. Sanitized fixtures now cover the sitemap,
home/view-all behavior, all eight contracts above, and the previously verified
CERC families.

## Exclusion inventory

The final inventory must explicitly list all sitemap/navigation sections not
tracked and explain whether they are generic content, independently irrelevant,
duplicated by another feed, or external to the three-site boundary. Do not treat
the examples in this document as a complete exclusion decision.

Status (2026-10-23): all three sites carry structured `coverage_decisions` in
`config/sources.yaml`, and each site section above lists them with evidence and
coverage limits. Step 1 research is complete for Ministry of Power, Ministry of
Coal, and CERC. Ministry of Coal's last open items were closed by the
2026-10-23 re-verification, decision D-021 (Coal Gasification indirect), and
the accepted limit for seven never-fetched narrative pages.

## Completion checklist

- [x] Every sitemap and primary navigation branch has been enumerated.
- [x] Site search and What’s New feeds have been checked for hidden publication paths.
- [x] Every candidate feed has an explicit contract and fixture set.
- [x] Pagination and history boundaries are documented.
- [x] Parent/attachment evolution is documented.
- [x] Language and date behavior is documented.
- [x] Every discovered section has a track/indirect/exclude decision.
- [x] `config/sources.yaml` contains only verified contracts.
- [x] Inventory reviewed before Step 2 begins (user approval 2026-10-23).
