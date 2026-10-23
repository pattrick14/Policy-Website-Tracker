# Ministry of Power: notes, blockers, and recommendation (2026-10-22, revised)

## Recommendation

**The site can be marked `research_status: complete` once the coordinator has
merged the outputs below and the strict gate passes.** No research blocker
remains. Both hidden archive paths now have tracked contracts under
D-001/D-006. A merged dry run of all four MoP changes passes the validator: with
`require_complete=True`, the only remaining MoP error is `research_status`
itself.

## Merge steps (coordinator)

1. `mop_new_feeds.yaml`: add the `mop_whats_new_archive` item under the
   `ministry_of_power` `feeds:` list (6-space list indentation).
2. `mop_documents_patch.md`:
   - Replace the `mop_documents` item with the full replacement block.
   - Set `required_feed_ids: [mop_whats_new, mop_whats_new_archive,
     mop_document_taxonomy, mop_documents]`.
3. `mop_coverage_decisions.yaml`: insert it into the site block (4-space key).
4. `mop_inventory.md`: replace the "Step 1 research still required"
   subsection.
5. `mop_fixture_rows.md`: add the rows to the `tests/fixtures/README.md` table.

The two current test failures (`test_repository_inventory_is_structurally_valid`
and `test_declared_repository_fixtures_exist`) predate this work and come from
the missing `coverage_decisions` lists. They should clear once all three sites
are merged.

## Contract choices made (review welcome, not blocking)

### `mop_whats_new_archive`

- Kind is `rotating_archive` with `removal_authority: false`.
- Primary enumeration is the single unparameterized response (about 660 KB, 637
  posts), complete when the `posts` length equals `total_items`.
- `limit`/`page` switch the endpoint to a paginated shape with identical
  ordering (`limit=5` gives 128 pages). This is documented as a fallback only.
  A single atomic body avoids items shifting between pages mid-enumeration.
- Dates: `post_date` is a June–July 2025 migration artifact. Publication date
  comes from attachment or document metadata, or stays unknown. It is never
  `post_date`.
- A post moving from `mop_whats_new` to the archive is a status or appearance
  change for the same post ID, not a new publication.

### `mop_documents`

- The archive pass adds `post_status=archive` per root slug, with the same
  `total_pages` termination.
- Verified with `limit=1` on acts-and-policy: 2 pages, then an empty page 3.
- Removal requires absence from **both** passes in two consecutive complete
  enumerations. A publish/archive move is a status change.
- The weekly child-term reconciliation mismatch is now a completeness failure
  indicator.

### Implementer notes

- Two keys are new to this file's contracts: `archive_url_template` and
  `status_passes` on `mop_documents`, and `completeness.notes`,
  `removal_rule`, and `child_term_reconciliation` on the two feeds. The
  validator accepts them. The Step 2 contract schema should formalize them.

## Smaller open items (not blocking; user may confirm)

- **Citizen Engagement** (`/connect/citizen-engagement`) is excluded as generic.
  Its content could not be resolved through a public data API in the bounded
  probe, so the review does not rule out consultation material hosted there.
  Consultations seen so far arrive through What's New. A manual browser look
  during a later refresh would close this.
- **Schemes and Services** and other Offerings pages are excluded because the
  plan's MoP families do not list schemes (the Coal plan does). Confirm if
  schemes should be tracked for MoP too.
- **Menu drift detection.** The menu APIs are not configured feeds, so new
  navigation sections surface only at inventory refresh. Optionally add a cheap
  weekly menu-tree diff.
- **CMS backend host.** Search results link to `cms-powermin.digifootprint.gov.in`.
  It is not in `allowed_hosts` and should stay out unless a feed needs it.
- **Build-ID dependence.** `frontend_routes.json` reflects build
  `CFzOpdsh5dnqm8hvr-SKu`, and chunk URLs change on redeploy.
- **Archive move between runs.** If a What's New post moves to the archive
  between the two weekly fetches, it may be seen in both responses or in
  neither. Identity by post ID across both feeds handles the duplicate case. The
  "neither" case is caught on the next run.

## Not an open question

- Press Release and Parliament Question terms are excluded as independent feeds
  per D-013, and What's New appearances are still captured. The two current
  Parliament-question posts are also tagged `reports`, so `mop_documents`
  Reports enumeration still captures them under the capture-every-item rule.
  Implementers should not filter them out.

## Research footprint

About 45 read-only GETs, spaced about 1 second apart, all plain HTTP. No PDFs
were downloaded. Fixtures were created only under
`tests/fixtures/ministry_of_power/`, 15 in total:

- `taxonomy_full.json`
- `documents_root_child_terms.json`
- `documents_child_slug_empty.json`
- `documents_archive_status.json`
- `documents_archive_page.json`
- `documents_archive_after_end.json`
- `whats_new_archive.json`
- `whats_new_archive_page.json`
- `archive_counts.json`
- `search_results.json`
- `navigation_menus.json`
- `frontend_routes.json`
- `rest_root_disabled.json`
- `sitemap_misconfigured.xml`
- `home_shell.html`

Config, docs, and tests were not modified.
