# State and Diff Engine

`policy_tracker.core.engine` is the pure Step 2 engine. It folds one adapter
observation for one feed into a `CatalogState` and returns the new state, the
semantic events, and the feed's `FeedRun`. It performs no I/O, never mutates
its inputs, and is deterministic.

## API

```python
@dataclass(frozen=True)
class FeedOutcome:
    state: CatalogState
    events: tuple[Event, ...]
    feed_run: FeedRun

apply_observation(
    state, observation | None, contract, run,
    *, authoritative_feeds: frozenset[str] | None = None,
) -> FeedOutcome
ensure_feed_state(state, contract) -> CatalogState
rebaseline_feed(state, feed_id, *, reason) -> CatalogState
owner_rank(appearance, is_authoritative=None) -> tuple   # owner sort key
owning_appearance(appearances, is_authoritative=None) -> Appearance | None
```

Operations call `apply_observation` once per feed per run and chain the
returned states. Feeds should be applied in a fixed order (for example, sorted
by feed id) so that whole-run output is reproducible.

- `ensure_feed_state` creates a `NOT_STARTED` `FeedState` for a new feed. It
  uses the contract's parser version and syncs `coverage_revision` from the
  contract.
- `rebaseline_feed` resets one feed to `NOT_STARTED`. It clears
  `baselined_revision` and `last_item_count`, so the next complete accepted run
  is silent and is not quarantined as a collapse. Records are kept. `reason`
  must not be empty. `FeedState` has no field for it, so the caller must log
  it, for example in the run's health record.
- `authoritative_feeds` is the set of feed ids whose contracts have
  `removal_authority=True`. Operations pass the same set on every call of a
  run. It lets the owner rule (below) rank authoritative feeds above
  rolling/recent feeds, which the engine cannot know from the current contract
  alone. The current contract's own `removal_authority` is always used for the
  current feed. With `None` the authority term is ignored for every feed (the
  legacy rule), which stays consistent across feeds but lets a rolling feed
  that saw a record first own it.

## Acceptance and quarantine

A feed's state advances only when the run is **accepted**. A run is
quarantined, with the reasons listed in `FeedRun.quarantine_reasons`, when any
of these holds:

| Reason | Condition |
|---|---|
| `adapter_failed` | `observation is None` (the adapter could not run) |
| `fetch_failed` | `evidence.fetch_ok` is false |
| `parse_invalid` | `evidence.parse_valid` is false |
| `block_page` | `evidence.block_page_detected` (HTTP 200 block or CAPTCHA page) |
| `unexpected_mime_type` | `evidence.unexpected_mime_types` is not empty |
| `unexpected_empty` | authoritative feed, zero items, `last_item_count > 0` |
| `row_count_collapse` | complete enumeration with fewer than `row_count_collapse_threshold × last_item_count` items |
| `stale_observation` | `observed_at` is earlier than the feed's latest recorded sighting |

A quarantined run keeps every record and appearance. It changes only
`FeedState.consecutive_failures`, which goes up by 1. An accepted run resets the
counter to 0. The item count is the number of distinct matters plus documents
after duplicates collapse. `last_item_count` changes only on complete accepted
runs.

## Identity

Keys come from `identity.canonical_url(base_url=contract.base_url,
rules=contract.host_rules)` and `identity.entity_key`. A document is keyed by
its native id first and its canonical URL second. Titles, dates, and hashes
never merge records. Every distinct original href is appended to
`Document.original_urls` in first-seen order.

`Document.matter_key` is set only from `matter_native_id`. The id must match an
`ObservedMatter` in the same observation or an existing matter of the same site
with that native id. Otherwise the field is `None` (unresolved). Any feed may
set a missing `matter_key`. Only the owning feed (see below) may change it to a
different matter. A sighting without parent evidence never clears it.

## Appearances and metadata ownership

There is one `Appearance` per (feed, entity). Duplicate sightings in one
observation collapse to one, and the first sighting's provenance and metadata
win. Collapsing creates no events; a diagnostic records the count. An
appearance's provenance is updated to the latest sighting, so its locator
follows ordering changes without creating events.

When the same record appears on several feeds, the **owning feed** controls
its listing metadata. The owner is the appearance with the smallest
`owner_rank`: present appearances first, then appearances on feeds with
removal authority before rolling/recent feeds (when `authoritative_feeds` is
given), then the earliest `first_seen_at`, then feed id. So a home bulletin or
What's New sighting that precedes the authoritative listing does not own the
record; the authoritative feed's title, dates, metadata, and later corrections
apply. If the owner's appearance is withdrawn, ownership moves to the next
present appearance. Enrichment (`documents.enrich.owning_feed`, using each
contract's `removal_authority`) and the digest (`build_digest(...,
authoritative_feeds=...)`) use the same rank.

Other feeds never overwrite a value. They may fill gaps:

- a missing `matter_key`;
- `published_date` and `published_raw`, when the record has no date evidence at
  all (both empty) and the sighting is dated;
- metadata keys the record does not have (non-empty values only).

The owner's sighting sets the metadata keys it carries and keeps keys it omits
(an omitted key is absence of evidence, like an undated sighting), so a key
filled by another feed is not removed and refilled every week. A parser
migration replaces the owner's metadata wholesale (silently), so keys a parser
no longer emits do not linger. These rules stop two feeds that render one
record differently from producing `METADATA_CHANGED` events every week.

Migration note: once operations start passing `authoritative_feeds`, records
first seen on a rolling feed move to their authoritative feed. On that feed's
next live run, any title, date, or metadata difference it already listed is
applied and reported once as `METADATA_CHANGED`.

## Run modes per feed

| Feed state before the run | Mode | New records get | Events |
|---|---|---|---|
| `baseline_status != COMPLETE` | baseline | `Discovery.BASELINE` | none for this feed |
| complete, `coverage_revision > baselined_revision` | backfill | `Discovery.BACKFILL` | emitted; new-record events carry `BACKFILL` |
| complete and current | live | `Discovery.LIVE` | emitted |

- **Baseline (D-006).** State is absorbed silently, including metadata,
  section, removal, and reappearance changes. The first accepted complete
  enumeration sets `COMPLETE` and `baselined_revision = coverage_revision`.
  Accepted partial runs set `PARTIAL` and still absorb records silently, so a
  partial baseline recovers silently. Newly added feeds and feeds reset by
  `rebaseline_feed` start here.
- **Backfill.** `NEW_DOCUMENT`, `NEW_MATTER`, and `NEW_APPEARANCE` carry
  `discovery=BACKFILL`. The digest labels them as newly covered history, not as
  new publications. Changes to records that were already known are reported as
  `LIVE`. A complete run sets `baselined_revision` to the new revision. A
  partial run stays in backfill mode.
- **Live.** A new record emits `NEW_DOCUMENT` or `NEW_MATTER`. A known record
  first seen on this feed emits `NEW_APPEARANCE`.

## Change events (live and backfill modes)

- `METADATA_CHANGED` is emitted for a change, by the owning feed, to the title,
  `canonical_url`, `published_raw`, `published_date`, or a metadata key, for a
  gap filled by another feed, and for a gained or changed `matter_key`. `details` has `changed_fields` (a
  comma-separated, sorted list) plus `<field>.old` and `<field>.new`. Values
  are compared after whitespace normalization, so whitespace-only edits emit
  nothing. Titles and metadata values are stored with whitespace normalized.
  `published_raw` is stored verbatim.
- `STATUS_CHANGED` is emitted when an appearance's `section` changes, for
  example `current` → `archive`. A section change is never a removal.
- Position and ordering changes emit nothing.

`metadata_fingerprint` is `stable_hash` over the normalized title,
`published_raw`, `published_date`, the sorted metadata, and `matter_key`
(`engine.document_fingerprint`).

## Removal and reappearance

- Only `removal_authority` feeds count removals, and only on accepted
  **complete** enumerations. A missing appearance's `missing_complete_runs`
  goes up by 1. When it reaches 2, the appearance becomes `present=False` and a
  `WITHDRAWN` event is emitted for it. `details.record_status` gives the
  record's resulting status.
- A Document or Matter becomes `WITHDRAWN` only when no appearance of it is
  present on any feed.
- Rolling feeds (`removal_authority=False`) never advance counters and never
  withdraw.
- Partial enumerations never advance counters.
- Seeing an item again resets its counter to 0. Seeing a withdrawn appearance
  again emits `REAPPEARED` and restores the record to `ACTIVE`.
- A replay does not count as another complete enumeration. A replay is a run
  whose `run_id` matches `last_complete_run_id`, or an observation that is not
  newer than the feed's latest sighting. This keeps a re-applied observation
  from withdrawing items.

## Parser migration

When `observation.parser_version != FeedState.parser_version`, the run is
accepted as usual, but it is treated like a baseline for existing records.
Metadata and section changes are applied **silently**, with no
`METADATA_CHANGED` or `STATUS_CHANGED`, because they may only reflect the new
parser's reading of unchanged pages. New records, new appearances, removals,
and reappearances still emit events normally. `FeedState.parser_version`
changes only after a complete accepted run. Until then, partial runs under the
new parser stay silent for metadata, so records that the parser has not yet
re-read cannot produce changes that are really parser artifacts. A diagnostic
on the `FeedRun` records the migration.

If a parser change also changes record identity (keys), migrate the state
explicitly (`core/migrations.py`) or call `rebaseline_feed`. Otherwise the
re-keyed records appear as new records.

## Determinism and idempotency

- The event id is `stable_hash(event_type, feed_id, entity_kind, entity_key,
  canonical JSON of details)`. `details` holds the changed values, for example
  old and new fields, `last_seen_at` for `WITHDRAWN`, and
  `previous_last_seen_at` for `REAPPEARED`. `run_id` is never part of the
  hash, so replaying a snapshot over the same base state gives identical event
  ids.
- Applying an observation to the state it produced emits no events.
- Events are sorted by (entity kind, entity key, event-type order, event id).
- Record and appearance `first_seen_at` and `last_seen_at` come from
  `observation.observed_at`. `last_seen_at` never moves backwards.
  `FeedRun.started_at` is `run.started_at`. `FeedRun.finished_at` is
  `observation.observed_at`, or `run.started_at` when there is no observation.
  Timestamps are compared as strings, so adapters must emit one ISO 8601 UTC
  format (`YYYY-MM-DDTHH:MM:SSZ`).

## Known limits

- An event id identifies a semantic change, not one occurrence of it. If a
  value goes A→B→A→B, the second A→B has the same id as the first. Archive
  consumers should key history on (`run_id`, `event_id`).
- Results within one run depend on feed order. Suppose a baselining feed sees a
  record before a live feed does in the same run. The live feed then reports
  `NEW_APPEARANCE` instead of `NEW_DOCUMENT`. Apply feeds in a fixed order.
- The `rebaseline_feed` reason is not persisted in `CatalogState`.
