<!-- Replacement for the "### Step 1 research still required" subsection under "## CERC" in docs/SOURCE_INVENTORY.md. Paste from the next heading down. -->

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

- **Host behaviour:** see `cerc_host_rule.md` and proposed decision D-020. Both
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

CERC can be set to `research_status: complete` once the following are merged
into the repository:

- the 21-entry `coverage_decisions` list;
- the six D-018 feeds and their `required_feed_ids`;
- the D-020 host rule.
