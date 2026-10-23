# Weekly Policy Website Tracker — Approved Blueprint

## 1. Outcome and tracking boundary

Build a private GitHub repository that researches every navigable section of the
three listed websites before tracker development begins, establishes a silent
full-history baseline for every selected publication feed, and then runs weekly.

The scheduled GitHub Actions expression will be `30 2 * * 1` (Monday 02:30 UTC,
nominally 08:00 IST). GitHub does not guarantee execution at an exact minute.

The tracker will capture all items on selected feeds while prioritizing policy
changes, consultations, drafts, regulations, orders, reports, and stakeholder
submissions. It will send HTML and plain-text email and archive Markdown, CSV,
and JSON. It will not require an AI API.

### Initial source families

| Website | Publication families to inventory and track |
|---|---|
| Ministry of Power | What’s New API; document taxonomy; Reports, Acts & Policy, Orders and Notices, Gazette Notifications, Guidelines, and Publications; every nested attachment belonging to those collections. Browser automation is diagnostic only because the site currently blocks headless Chromium while public data APIs remain accessible. |
| Ministry of Coal | Sitemap, What’s New, Acts/Rules/Policies, reports, statistics, cabinet summaries, sustainable-development reports, committee material, schemes, sanction/termination notices, national indices, and other publication-oriented pages found during inventory. |
| CERC | Sitemap, homepage/view-all bulletin, draft and current regulations, discussion/working/study papers, orders and records of proceedings, reports, public notices, hearings, statutory notices, and statutory advice. Existing matters must be revisited because later comments, extensions, hearings, Gazette files, and statements of reasons may be appended. |

Generic organisational pages, staff directories, galleries, jobs, and tenders do
not become independent feeds. If an item from such a section appears in a tracked
bulletin and is relevant to policy, research, or consultation, it remains
captured. External websites are recorded as outbound links but are outside v1
crawling.

## 2. Tracker behavior and interfaces

### Data and identity model

Use versioned schemas for:

- **Matter:** a policy, consultation, proceeding, regulation, report series, or
  other parent subject.
- **Document:** an attachment or publication, identified first by source-native
  ID, then source-scoped canonical URL, then stable provenance hash.
- **Appearance:** where a matter or document appeared, allowing one document to
  occur in several feeds without duplicate publication alerts.
- **DocumentVersion:** validators, byte hash, text fingerprint, extraction status,
  and verification time.
- **Event:** new matter, new document, new appearance, metadata/status change,
  deadline change, content replacement, withdrawal, or reappearance.
- **FeedRun:** fetch status, parse validity, pagination completeness, coverage
  boundary, configuration hash, code revision, and diagnostics.

URLs retain query parameters unless an explicitly tested source-specific rule
removes them. Never merge records solely by title, date, or PDF hash. Preserve
uncertain relationships as unresolved. Record publication date separately from
`first_seen_at` so backfilled documents are not represented as newly published.

### Feed completeness and baseline rules

- Every feed contract defines endpoint, feed type, pagination, authoritative or
  rolling status, expected markup/JSON shape, language behavior, document types,
  and refresh policy.
- Fetch success, parse validity, and complete enumeration are separate states.
- Quarantine HTTP 200 block pages, broken pagination, unexpected MIME types,
  unexpected empty authoritative feeds, and row-count collapses over 50%.
- Require two consecutive complete authoritative enumerations before emitting a
  removal. Rolling and recent-item lists never imply removal.
- Preserve the prior state of failed feeds while allowing healthy feeds to
  advance; mark the digest partial.
- Track baseline completion per feed. A recovered or newly added feed is silently
  baselined rather than flooding the digest with old history.
- Coverage expansions increment `coverage_revision`; newly exposed records are
  backfill. Schema and parser migrations are explicit and versioned.

### Retrieval, documents, and language

- Use `httpx` plus HTML/JSON parsers in production. Keep Playwright for research,
  visual inspection, and fixture refresh only.
- Fully enumerate configured indexes weekly. Revisit active matters and current
  or prior-year parents weekly; rescan all historical parent pages quarterly.
- Store a byte hash and extracted-text fingerprint on first acquisition. Later,
  use ETag/Last-Modified and conditional requests. This metadata-only policy may
  miss silent byte replacement when a server does not update validators.
- A failed or unsupported `HEAD` never proves that a file is unchanged.
- Restrict downloads to allowlisted official hosts, five redirects, 100 MiB,
  public IP destinations, and verified file signatures.
- Run PDF parsing in a bounded subprocess. Extract at most 200 pages; OCR may
  inspect the first 50 pages within a 60-second document budget.
- Process English and bilingual material. Preserve discoveries and mark warnings
  for Hindi-only, scanned, or unextractable files.
- Escape HTML and Markdown and neutralize spreadsheet-formula-leading CSV cells.

### Classification and weekly presentation

Classify items deterministically into:

1. Action required: comments, submissions, suggestions, consultations,
   stakeholder input, and hearings.
2. Rules and regulation: Acts, rules, regulations, amendments, Gazette notices,
   corrigenda, and statements of reasons.
3. Policy and official direction: policies, guidelines, procedures, statutory
   advice, directions, and significant orders.
4. Research and reporting: reports, studies, working/discussion papers, annual,
   monthly, and quarterly statistics, and indices.
5. Routine records: high-volume case orders, records of proceedings, and other
   captured publications.

All selected-feed items enter the archive. Routine records remain in a compact
appendix unless policy-significance rules promote them.

Deadlines are arrays with source evidence and extraction method. Dates with no
stated time display “time not stated” and use 23:59 IST only for sorting.
Ambiguous dates are flagged. Email shows deadlines only when discovered or
changed; it sends no approaching-deadline reminders. Regenerate
`open_deadlines.csv` weekly for the complete current view.

Every post-baseline run sends a heartbeat digest containing action/deadline
changes, law and policy items, reports/research, other updates, routine appendix,
and source health. Cap email at 5 MiB. Attach CSV if the message remains below
that limit; otherwise link to private-repository artifacts.

### Repository, automation, and delivery

- `main` holds code, configuration, tests, and documentation.
- Orphan `tracker-data` holds catalogs, versions, events, manifests, health,
  pending outbox, deadline index, and digests.
- Data layout: `catalog/`, `events/YYYY/MM/`, `digests/YYYY/MM/`, `runs/`,
  `health/`, `outbox/`, and `schema.json`.
- A one-time initialization workflow creates `tracker-data`; normal runs refuse
  to overwrite a missing or unexpectedly advanced branch.
- Allow only one run at a time and never cancel an active send.
- Trigger only on `schedule` and `workflow_dispatch`, avoiding recursive runs.
- `dry_run` performs no email, commit, push, or durable state mutation.
- Pin code revision, configuration hash, and base data commit into each run.

Public CLI:

```text
policy-tracker validate-config --config config/sources.yaml
policy-tracker baseline --config config/sources.yaml --data-dir <path> --no-email
policy-tracker run --config config/sources.yaml --data-dir <path>
policy-tracker run --config config/sources.yaml --data-dir <path> --dry-run
policy-tracker rebaseline-feed <feed-id> --reason <text>
```

Durable delivery sequence:

1. Retry an existing pending outbox entry before crawling.
2. Crawl, diff, and render an immutable digest plus candidate state.
3. Commit and push the pending bundle before contacting SMTP.
4. Send to the immutable stored recipient set using a deterministic Message-ID
   and verified TLS.
5. After every recipient is accepted, promote candidate state and mark the entry
   delivered in a second commit.
6. Ambiguous timeout or partial acceptance remains pending and retries the same
   recipient set. Delivery is at-least-once; a crash after SMTP acceptance but
   before recording success can cause a duplicate.
7. The first baseline promotes state without an email outbox entry.

GitHub variables: `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY` (`starttls` or `ssl`).
GitHub secrets: `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TO`.

## 3. Construction blueprint

Dependency graph: `1 → 2 → (3 ∥ 4 ∥ 5) → 6 → 7 → 8`

### Step 1 — Website research and repository bootstrap

Inventory every sitemap, navigation tree, search/listing page, API, pagination
mechanism, archive, nested-detail pattern, and exclusion. Capture representative
sanitized fixtures including block pages, old-parent attachments, pagination,
and evolving consultation rows. Complete `docs/SOURCE_INVENTORY.md` and feed
contracts. Exit only when every discovered section has a track/exclude decision,
evidence URL, feed type, refresh policy, coverage limit, and fixture.

### Step 2 — Core contracts and state engine

Freeze schemas, adapter interface, identities, provenance, completeness,
per-feed baseline, migration, diff, removal, and backfill semantics. Build the
CLI and synthetic tests before adapter work. Tests must cover ordering changes,
duplicate appearances, backfills, removals, reappearances, partial baselines,
parser migrations, and incomplete enumeration.

### Steps 3–5 — Source adapters (parallel after Step 2)

- **Ministry of Power:** APIs, taxonomy, parent collections, nested attachments,
  pagination, and browser-block fallback.
- **Ministry of Coal:** What’s New, policy/legal pages, reports/statistics,
  committee/nominated-authority material, nested detail, and inconsistent dates.
- **CERC:** bulletins, evolving drafts/regulations, papers/reports, orders/ROPs,
  hearings, notices, advice, extensions, and later-added documents.

Each adapter returns collection-completeness evidence alongside normalized
records and has deterministic fixture tests.

### Step 6 — Enrichment and digest generation

Implement bounded download/extraction, OCR fallback, byte/text versions, English
handling, classification, evidence-backed deadlines, deadline state, safe
Markdown/CSV/JSON renderers, and HTML/plain email. Snapshot tests cover ordering,
highlighting, discovery labels, deadline changes, escaping, and size overflow.

### Step 7 — GitHub and SMTP operations

Implement data-branch initialization/publishing, immutable pending bundles,
retry-before-crawl, compare-and-swap pushes, SMTP delivery, concurrency, schedule,
manual dry-run, retention, and recovery documentation. Test rejection, partial
acceptance, ambiguous timeout, retry, post-send crash, branch race, and masking.

### Step 8 — Acceptance and adversarial review

Perform a silent live baseline, compare feed totals with the inventory, run a
controlled email delivery, and execute failure drills for malformed 200s,
partial pagination, recovered baselines, validator limitations, old-parent
attachments, rolling disappearance, OCR failure, SMTP failures, and migrations.
Rerunning the same snapshot must create no duplicate semantic events.

Each step is a separate pull request with fixtures, tests, documentation,
migration notes, and an updated implementation-status handoff.

## 4. Acceptance criteria and assumptions

- Baseline all history exposed by selected feeds without publication alerts.
- Report new publications, attachments, comments, extensions, hearings, and
  changed regulations once.
- Do not alert on ordering, whitespace, navigation, duplicate appearances, or
  normal rolling-list expiry.
- Retain last good state for failed/suspicious feeds and expose the problem.
- Do not claim universal detection of silent same-URL replacements.
- Never discard discovery because language detection or extraction failed.
- Commit no source PDFs; preserve provenance, validators, hashes, metadata,
  events, and manifests instead.
- Retain generated history indefinitely and review repository growth annually.
- Enable GitHub failure notifications. A later successful run flags any gap over
  ten days; operators can trigger a manual recovery run.
- Deployment requires a private GitHub destination and SMTP credentials. Block
  rather than fall back to insecure delivery.
