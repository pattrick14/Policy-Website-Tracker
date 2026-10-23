# Source Fixtures

Fixtures are sanitized, deterministic excerpts used to define source contracts.
They must not contain credentials, personal recipient data, or downloaded source
PDFs. Each fixture should preserve the structural behavior relevant to parsing
while replacing document titles and file paths when the exact text is not needed.

Current provenance:

| Fixture | Source observed | Observation date | Behavior preserved |
|---|---|---|---|
| `ministry_of_coal/whats_new.html` | `https://www.coal.nic.in/whats-new` | 2026-10-22 | Four-column rolling table, direct PDF link, file metadata, and displayed date |
| `ministry_of_coal/acts_rules_policies.html` | `https://www.coal.nic.in/acts-rules-policies` | 2026-10-22 | Five distinct authoritative tables and attachment URLs whose upload folders are not publication dates |
| `ministry_of_coal/sitemap.html` | `https://www.coal.nic.in/sitemap` | 2026-10-22 | Nested navigation branches plus internal and external links |
| `ministry_of_coal/direct_attachment_table.html` | Multiple Ministry collection pages | 2026-10-22 | Shared direct-attachment table contract, including displayed dates and backdated records |
| `ministry_of_coal/termination_orders.html` | `/nominated-authority/termination-order` | 2026-10-22 | Explanatory allocation table followed by the authoritative attachment table |
| `ministry_of_coal/nested_report_index.html` | Annual, budget, and other-report indexes | 2026-10-22 | Parent index whose rows link to nested detail pages rather than files |
| `ministry_of_coal/nested_report_detail.html` | Annual-report detail page | 2026-10-22 | Multiple child attachments, including a separate Hindi file |
| `ministry_of_coal/direct_link_list.html` | `/public-information/action-plan` | 2026-10-22 | Main-content link list without a publication table |
| `ministry_of_coal/central_sector_schemes.html` | `/central-sector-schemes` | 2026-10-22 | Canonical narrative matter page with three schemes and an embedded budget table |
| `cerc/draft_regulations.html` | `https://cercind.gov.in/Draft_reg.html` | 2026-10-22 | Year grouping and later-added extension, comments, hearing, and presentation links on one matter row |
| `cerc/current_regulations.html` | `https://cercind.gov.in/Current_reg.html` | 2026-10-22 | Gazette/notification/reasons links plus nested principal and amendment history |
| `cerc/recent_orders.html` | `https://cercind.gov.in/recent_orders.html` | 2026-10-22 | Current-year serial boundary, petition number, order/posting dates, category, and prior-year order date |
| `cerc/recent_rops.html` | `https://cercind.gov.in/recent_rops.html` | 2026-10-22 | Current-year serial boundary, petition number, hearing/posting dates, and category |
| `cerc/discussion_papers.html` | `https://cercind.gov.in/Disc_Paper.html` | 2026-10-22 | Evolving paper row with deadline, notice, paper, and comments links |
| `cerc/study_reports.html` | `https://cercind.gov.in/Study_Rep.html` | 2026-10-22 | Complete report table with document links and posting dates |
| `cerc/public_notices.html` | `https://cercind.gov.in/public-notice.html` | 2026-10-22 | General notice table and publication dates |
| `cerc/statutory_public_notices.html` | `https://cercind.gov.in/SPN.html` | 2026-10-22 | Petition matter with paired English/Hindi newspaper attachments |
| `cerc/statutory_advice.html` | `https://cercind.gov.in/advice_gov.html` | 2026-10-22 | Statutory-advice link list with dates embedded in link text |
| `cerc/site_map.html` | `https://cercind.gov.in/site_map.html` | 2026-10-22 | Navigation branches for regulations, papers, order/ROP archives, publications, and hearings |
| `cerc/home_bulletin.html` | `https://cercind.gov.in/index-en.html` and `viewall.html` | 2026-10-22 | Rolling bulletin with a current consultation, persistent older resource, and view-all link |
| `cerc/working_papers.html` | `https://cercind.gov.in/Work_Paper.html` | 2026-10-22 | Complete two-document working-paper table |
| `cerc/order_archives.html` | `https://cercind.gov.in/orders.html` and linked annual pages | 2026-10-22 | Prior-year index boundaries plus representative archived order metadata |
| `cerc/rop_archives.html` | `https://cercind.gov.in/record_proceeding.html` and linked annual pages | 2026-10-22 | Prior-year index boundaries plus representative archived ROP metadata |
| `cerc/market_monitoring.html` | `https://cercind.gov.in/report_MM.html` | 2026-10-22 | Annual Market Monitoring Cell report list and reporting-period boundaries |
| `cerc/annual_reports.html` | `https://cercind.gov.in/annual_report.html` | 2026-10-22 | Annual-report link list, historical boundary, and gaps in the displayed year sequence |
| `cerc/other_reports.html` | `https://cercind.gov.in/Other_Report.html` | 2026-10-22 | Single-report table distinct from the other report collections |
| `cerc/public_hearings.html` | `https://cercind.gov.in/public.html` | 2026-10-22 | Year-grouped hearing matters with notice and later supporting-document links |
| `cerc/hearing_schedule.html` | `https://cercind.gov.in/Sched_hear.html` | 2026-10-22 | Rolling monthly advance/weekly cause lists and a hearing-mode change notice |
| `ministry_of_power/taxonomy.json` | Ministry document taxonomy API | 2026-10-22 | Root/child terms, stable term IDs and slugs, and zero-count selected categories |
| `ministry_of_power/whats_new.json` | Ministry What's New API | 2026-10-22 | Rolling posts, native IDs, menu ordering, attachment IDs, and response total |
| `ministry_of_power/documents_page.json` | Ministry category-document API | 2026-10-22 | Parent metadata, nested attachment IDs, category terms, and pagination totals |
| `ministry_of_power/documents_after_end.json` | Ministry category-document API | 2026-10-22 | Empty page immediately after `total_pages` |
| `ministry_of_power/old_parent.json` | Ministry parent-detail API | 2026-10-22 | Old parent modified later and containing multiple attachment groups |
| `ministry_of_power/attachment_detail.json` | Ministry attachment-detail API | 2026-10-22 | Bilingual PDF metadata, file URL, byte size, MIME type, and timestamps |
| `ministry_of_power/browser_block.html` | Sanitized browser failure shape from initial research | 2026-10-22 | HTML access-denied response that must not be parsed as valid source content |
| `ministry_of_power/taxonomy_full.json` | `https://www.powermin.gov.in/cms/wp-json/taxonomy/documents_category` | 2026-10-22 | All 16 terms with IDs, slugs, parents, and counts, including press-release and Parliament-question terms |
| `ministry_of_power/documents_root_child_terms.json` | `/cms/wp-json/document/documents?document_category=reports&limit=50&page=1` | 2026-10-22 | Root enumeration returning posts tagged with both a child term and the root term (3 of 9; titles replaced) |
| `ministry_of_power/documents_child_slug_empty.json` | `/cms/wp-json/document/documents?document_category=general-report&limit=50&page=1` | 2026-10-22 | Child-slug query returns zero items although the taxonomy count is 7 |
| `ministry_of_power/documents_archive_status.json` | `/cms/wp-json/document/documents?document_category=acts-and-policy&limit=50&page=1&post_status=archive` | 2026-10-22 | Archived parents absent from the default enumeration; `post_status: archive` (titles replaced) |
| `ministry_of_power/whats_new_archive.json` | `/cms/wp-json/post-page/whats_new?post_status=archive` | 2026-10-22 | Archived bulletin posts with `total_items: 637` and no pagination fields (2 posts kept; titles and attachment IDs replaced) |
| `ministry_of_power/whats_new_archive_page.json` | `/cms/wp-json/post-page/whats_new?post_status=archive&limit=5&page=2` | 2026-10-22 | Paginated fallback shape (`total_pages: 128`, `current_page: 2`) produced by sending limit/page (2 of 5 posts kept; titles and attachment IDs replaced) |
| `ministry_of_power/documents_archive_page.json` | `/cms/wp-json/document/documents?document_category=acts-and-policy&limit=1&page=2&post_status=archive` | 2026-10-22 | Archive-pass pagination: last page (`current_page` equals `total_pages: 2`); title replaced |
| `ministry_of_power/documents_archive_after_end.json` | `/cms/wp-json/document/documents?document_category=acts-and-policy&limit=1&page=3&post_status=archive` | 2026-10-22 | Empty archive-pass page immediately after `total_pages` |
| `ministry_of_power/archive_counts.json` | `/cms/wp-json/count-posts/archive` | 2026-10-22 | Archived-post counts for every post type |
| `ministry_of_power/search_results.json` | `/cms/wp-json/custom/api/search?s=electricity%20act` | 2026-10-22 | Mixed-type, mixed-status search result shape with CMS-host links (one row per type and status; titles and slugs replaced) |
| `ministry_of_power/navigation_menus.json` | `/cms/wp-json/custom/v1/menus/{website-menu,global-information-architecture,footer-menu}` | 2026-10-22 | Complete header, information-architecture, and footer menu trees (IDs, titles, URLs, parents) |
| `ministry_of_power/frontend_routes.json` | `/_next/static/CFzOpdsh5dnqm8hvr-SKu/_buildManifest.js` and page chunks | 2026-10-22 | Derived list of Next.js page routes, bundle-referenced API paths, and the `post_status=archive` flag |
| `ministry_of_power/rest_root_disabled.json` | `https://www.powermin.gov.in/cms/wp-json/` | 2026-10-22 | HTTP 403 `rest_root_disabled` JSON body for the REST route index |
| `ministry_of_power/sitemap_misconfigured.xml` | `https://www.powermin.gov.in/sitemap.xml` | 2026-10-22 | Valid sitemap XML whose entries all point at `www.meity.gov.in` (4 of 50 kept) |
| `ministry_of_power/home_shell.html` | `https://www.powermin.gov.in/` | 2026-10-22 | Client-rendered Next.js shell with empty `__NEXT_DATA__` page props and no navigation links (beacon and chatbot scripts removed) |
| `ministry_of_power/citizen_engagement_page.json` | `/cms/wp-json/wp/v2/pages?slug=public-consultation&acf_format=standard` | 2026-10-23 | Citizen Engagement backing page: Citizen Charter title and a one-entry PDF repeat list (no consultations) |
| `ministry_of_power/adapter_taxonomy.json` | Synthetic adapter fixture (`/cms/wp-json/...` shapes) | 2026-10-23 | Taxonomy (`taxonomy_full.json` shape) with counts reduced to match the adapter_* pages |
| `ministry_of_power/adapter_reports_page1.json` | Synthetic adapter fixture (`/cms/wp-json/...` shapes) | 2026-10-23 | Reports page 1 (limit 50): three posts carrying child terms with synthetic ACF file groups |
| `ministry_of_power/adapter_reports_page2_end.json` | Synthetic adapter fixture (`/cms/wp-json/...` shapes) | 2026-10-23 | Reports empty terminus page after `total_pages` |
| `ministry_of_power/adapter_acts_page1.json` | Synthetic adapter fixture (`/cms/wp-json/...` shapes) | 2026-10-23 | Acts and Policy page 1: a single-file parent and a multi-group parent with an external link |
| `ministry_of_power/adapter_acts_page1_new_attachment.json` | Synthetic adapter fixture (`/cms/wp-json/...` shapes) | 2026-10-23 | Variant of `adapter_acts_page1.json` where parent 7002 gains attachment 6013 (live new-document test) |
| `ministry_of_power/adapter_acts_page2_end.json` | Synthetic adapter fixture (`/cms/wp-json/...` shapes) | 2026-10-23 | Acts and Policy empty terminus page |
| `ministry_of_power/adapter_acts_archive_limit1_page1.json` | Synthetic adapter fixture (`/cms/wp-json/...` shapes) | 2026-10-23 | Archive pass at limit 1, page 1 (precedes `documents_archive_page.json`) |
| `ministry_of_power/adapter_acts_archive_page2_end.json` | Synthetic adapter fixture (`/cms/wp-json/...` shapes) | 2026-10-23 | Archive pass empty terminus page |
| `ministry_of_power/adapter_orders_page1.json` | Synthetic adapter fixture (`/cms/wp-json/...` shapes) | 2026-10-23 | Orders and Notices page 1 |
| `ministry_of_power/adapter_orders_page2_end.json` | Synthetic adapter fixture (`/cms/wp-json/...` shapes) | 2026-10-23 | Orders and Notices empty terminus page |
| `ministry_of_power/adapter_whats_new.json` | Synthetic adapter fixture (`/cms/wp-json/...` shapes) | 2026-10-23 | What's New response whose `total_items` equals the post count (complete enumeration) |
| `ministry_of_power/adapter_whats_new_archive.json` | Synthetic adapter fixture (`/cms/wp-json/...` shapes) | 2026-10-23 | What's New archive response whose `total_items` equals the post count |
| `ministry_of_power/adapter_whats_new_added.json` | Synthetic adapter fixture (`/cms/wp-json/...` shapes) | 2026-10-23 | What's New with a new post 9003 (live new-matter test) |
| `ministry_of_coal/minutes_of_meetings.html` | `/public-information/minutes-metings` | 2026-10-22 | General minutes table distinct from SLC, overlapping SLC notice row, and an archive link absent from the sitemap |
| `ministry_of_coal/auction_allotment.html` | `/nominated-authority/auction-allotment` | 2026-10-22 | Leading statistics table before the document table, nine-page pager whose Last page link is `?page=8`, and a double-slash HTTP archive link |
| `ministry_of_coal/review_minutes_table.html` | `/nominated-authority/production-reviews` and `/operationalisation-reviews` | 2026-10-22 | Narrative page plus a review-minutes table whose `http://coal.gov.in` upload folder does not match the meeting date |
| `ministry_of_coal/press_releases.html` | `/media/press-release` | 2026-10-22 | Excluded (D-014) internal press-release table with DD/MM/YYYY dates, two-page pager, and archive link |
| `ministry_of_coal/production_and_supplies.html` | `/major-statistics/production-and-supplies` | 2026-10-22 | Indirect (D-016) narrative page with inline current data tables, linked ten-year datasets, and a site-wide Last Updated label |
| `ministry_of_coal/external_link_pages.html` | `/public-information/critical-and-Super-critical-coal-stocks`, `/parliament-qa`, `/nominated-authority/coal-block-information` | 2026-10-22 | Ministry pages whose only content is outbound links to Coal India, Sansad, and coal-block portals |
| `ministry_of_coal/excluded_operational_tables.html` | `/tenders/past-tender` and `/nominated-authority/vacancy` | 2026-10-22 | Excluded procurement and recruitment tables, with pager and archive link |
| `ministry_of_coal/excluded_generic_pages.html` | `/contact-us`, `/public-information/citizen-charters`, `/rti/proactive-disclosures-rti-act-ministry-of-coal` | 2026-10-22 | Excluded generic and administrative pages that still contain document links (personal details removed) |
| `ministry_of_coal/captcha_block.html` | Any Ministry of Coal URL after about 70 requests at 1.2 s spacing | 2026-10-22 | HTTP 200 CAPTCHA "Validation request" block page (error code 421) that must be quarantined, not parsed |
| `ministry_of_coal/auction_allotment_last_page.html` | `/nominated-authority/auction-allotment?page=8` | 2026-10-23 | Final pager page: 14-row document table (S.N 401-414), pager without Next/Last links, double-slash HTTP archive link |
| `ministry_of_coal/auction_allotment_page_overlap.html` | `/nominated-authority/auction-allotment?page=2` and `?page=3` | 2026-10-23 | Two page excerpts showing same-date rows repeated across an offset-pager boundary (identity by href; one pass is not completeness proof) |
| `ministry_of_coal/paged_view_out_of_range.html` | `/nominated-authority/auction-allotment?page=9` | 2026-10-23 | Page past the pager terminus: HTTP 200, heading and pager render, no tables; not an empty collection or block page |
| `ministry_of_coal/auction_allotment_archive.html` | `/nominated-authority/archive-auction-allotment` (also the shape of `/archive-national-coal-index` and `/archive-termination-order`) | 2026-10-23 | Single-page three-column archive without a Date column, empty pager list, verbatim heading typo, reached via the HTTPS single-slash rewrite |
| `ministry_of_coal/minutes_archive.html` | `/public-information/minutes-metings/archive-minutes-metings` | 2026-10-23 | Dated four-column archive with rows out of date order and a two-page pager |
| `ministry_of_coal/archive_table_paged.html` | `/nominated-authority/archive-sanction-order-notice`, Monthly Summary for Cabinet and Monthly Reports for Cabinet archives | 2026-10-23 | Undated three-column archive with a two-page pager |
| `ministry_of_coal/paged_table_with_archive.html` | `/nominated-authority/national-coal-index`, `/sanction-order-notice`, `/public-information/monthly-report-cabinet` | 2026-10-23 | Current table with both a pager Last page link and a double-slash HTTP archive footer link |
| `ministry_of_coal/whats_new_paged.html` | `/whats-new` | 2026-10-23 | What's New page 0 with an ellipsis pager whose Last page link is `?page=40` (41 pages) |
| `ministry_of_coal/whats_new_last_page.html` | `/whats-new?page=40` | 2026-10-23 | Last What's New page with malformed source rows: year 0013, empty dates, and a linkless "test" row |
| `ministry_of_coal/hindi_whats_new_block.html` | `/hi` | 2026-10-23 | Excluded Hindi interface What's New block mirroring English rows, some linking distinct Hindi-language PDFs |
| `ministry_of_coal/site_search_results.html` | `/index.php/search/node?keys=auction` | 2026-10-23 | Excluded Drupal search results: direct file links, repeated results, index timestamps, 149-page pager |
| `ministry_of_coal/coal_gasification.html` | `/major-statistics/national-coal-gasification-mission` | 2026-10-23 | Evidence for the indirect decision D-021 (not a tracked feed): multi-section scheme page with DD/MM/YYYY section tables, a nested table, an undated row, and absolute coal.gov.in hrefs with mixed schemes and doubled path slashes |
| `ministry_of_coal/narrative_static_tables.html` | `/major-statistics/coal-evacuation-plan` (shape shared by Clearances, Grading of Coal, Mission Coking Coal, Technology Roadmap, Procurement Projection) | 2026-10-23 | Indirect narrative page with a small static file table plus escaped HTML rows rendered as text that must not be parsed as rows |
| `cerc/repealed_regulations.html` | `https://cercind.gov.in/Repeal_Reg.html` | 2026-10-22 | Excluded page (D-019): serial-numbered repealed-regulation rows with Gazette/notification/reasons/corrigendum children, legacy `.htm` notifications, Gazette number/date columns, and a trailing HTML comment of stale links to ignore |
| `cerc/meeting_minutes.html` | `/Minutes_comm.html`, `/AD_minutes.html`, `/other_minutes.html`, `/Minutes_Coord.html` | 2026-10-22 | Four minutes feeds (D-018): dated link lists, and committee rows with multiple presentation/projection attachments |
| `cerc/home_archives.html` | `https://cercind.gov.in/Archive.html`, `/Archive26.html`, `/Archive2009.html` | 2026-10-22 | Excluded homepage annual archives (D-019): year index plus archived matters with nested hearing/comment/presentation children and an external link; evidence for the known baseline limit |
| `cerc/hindi_mirror.html` | `https://cercind.gov.in/index_hin.html`, `/Draft_reg_hin.html` | 2026-10-22 | Hindi navigation and a mirror row reusing English attachment hrefs while lagging the English page |
| `cerc/site_map_branches.html` | `https://cercind.gov.in/site_map.html` and `/index-en.html` | 2026-10-22 | Non-feed sitemap branches (organisation, acts, regulatory affairs, RTI, tenders, Hindi, petitions, external) used for coverage decisions |
| `cerc/non_feed_pages.html` | Escalation-rate, coal-price, technical-validation, petition-status, compliance, correspondence, search, and links pages | 2026-10-22 | Representative structures of excluded pages, including the Google search widget and external links (escalation and coal-price excerpts predate D-018; their feed fixtures are `escalation_rates.html` and `price_indices.html`) |
| `cerc/price_indices.html` | `https://cercind.gov.in/coal_price.html`, `/coal_price_index_base.html` | 2026-10-22 | Coal price index link boxes including the collapsed base 2022-23 accordion panel, date-stamped workbook filenames, and the CIL price-notification list |
| `cerc/escalation_rates.html` | `https://cercind.gov.in/escalation_rates.html`, `/escaltion_2026.html`, `/escalation_rates_gen_pro_2026.html`, `/escalation_rates_tran_ser_2023.html`, `/escalation_rates_2010.html` | 2026-10-23 | Year index with both naming schemes and a coal-price cross-link, a 2011+ hub page, collapsed Annual/Six-Monthly/Monthly panels, notification/explanation pairs with restarting numbering, a notification dated after its covered month, same-date transmission variants, filenames with spaces, a cross-year path, a repeated href with different text, an absolute `http://` link, and a commented-out stale href |

These fixtures are contract examples, not complete live snapshots. Together
with the site-level coverage decisions in `config/sources.yaml`, they are the
Step 1 evidence for all three sites; the inventory still awaits user review
before Step 2.
