# Tracker Data Layout

`policy_tracker.operations.store.DataStore` owns the on-disk layout of the
`tracker-data` branch (or any directory with that layout). Step 2 writes it
locally; Step 7 commits and pushes it. Recipient addresses are never written:
a `PendingDelivery` stores only `recipient_set_sha256` and `recipient_count`.

## Serialization

`policy_tracker.core.serialize` is the only JSON writer:

- sorted keys, UTF-8 without `\u` escapes, 2-space indent, one trailing newline;
- enums are stored as their string values, tuples as lists, `Mapping` as objects;
- no floats appear in the schema; booleans are never accepted as integers;
- unknown fields, missing required fields and wrong types are rejected on read.

Every top-level file carries `schema_version` and a `kind`. Kinds in use:
`tracker_data`, `catalog_matters`, `catalog_documents`, `catalog_appearances`,
`catalog_versions`, `catalog_feeds`, `catalog_state` (the assembled form passed
to migrations), `candidate_header`, `run_events`, `run_manifest`, `feed_health`,
plus one kind per record (`catalog_state`, `candidate_state`, `pending_delivery`,
`feed_run`, `event`, `matter`, `document`, `appearance`, `document_version`,
`feed_state`).

The **state hash** is the SHA-256 of the canonical current-schema encoding of the
whole `CatalogState`. It does not depend on how the catalog is split into files.

## Files

```text
schema.json                       layout manifest: schema_version, layout_version, catalog parts
catalog/matters.json              {"records": {matter_key: Matter}}
catalog/documents.json            {"records": {document_key: Document}}
catalog/appearances.json          {"records": {appearance_key: Appearance}}
catalog/versions.json             {"records": {document_key: [DocumentVersion, ...]}}
catalog/feeds.json                {"records": {feed_id: FeedState}}
candidates/<run_id>/candidate.json  staged candidate header (base_state_hash, state_hash)
candidates/<run_id>/catalog/*.json  staged candidate catalog, same format as catalog/
events/YYYY/MM/<run_id>.json      {"run_id", "events": [Event, ...]} (omitted when empty)
runs/<run_id>.json                run manifest (below)
health/feeds.json                 latest health summary per feed
outbox/                           Step 7 pending deliveries (empty in Step 2)
digests/YYYY/MM/                  Step 6 digests (empty in Step 2)
```

Each catalog file also records the `state_hash` of the state it belongs to. On
load, all five files must agree on `schema_version` and `state_hash`, and (at
the current schema version) the recomputed hash must match. A mismatch means a
torn or hand-edited catalog; recover by checking out the last committed
`tracker-data` revision. The catalog is one file per collection; sharding (for
example by site) is a layout change that needs a `layout_version` bump.

Run ids are `YYYYMMDDTHHMMSSZ-<8 hex>`: the UTC start time plus
`stable_hash(started_at, config_hash)[:8]`. The year and month of the events
path come from the run id. A recorded run id is never overwritten. Event ids are
unique within a run; archive consumers key events on `(run_id, event_id)`.

### Run manifest

`runs/<run_id>.json` holds `status` (`candidate`, `promoted` or
`not_promoted`), `config_hash`, `code_revision`, `base_state_hash`,
`candidate_state_hash`, `promoted_state_hash` (after promotion), `event_count`,
`events_path`, every `FeedRun`, a free-form `context` (mode, started_at,
dry_run) and `rebaselines` (`[{feed_id, reason, at}]`; the frozen `FeedState`
has no field for a rebaseline reason, so the audit record lives here and as
`last_rebaseline` in `health/feeds.json`).

### Health

`health/feeds.json` maps each feed id to its latest fetch/parse/enumeration
status, acceptance, item counts, quarantine reasons, diagnostics, and the
baseline status and `consecutive_failures` of the run's candidate state. Feeds
absent from a run keep their previous entry. Health is written for every
recorded run, including runs that are not promoted.

## Writes and promotion protocol

All writes are atomic per file: write a temporary file in the same directory,
fsync, `os.replace`, fsync the directory. A run proceeds as follows:

1. `load_state()` reads and migrates the promoted catalog; `base_state_hash` is
   its state hash.
2. The runner applies every tracked feed (live feeds first, then baselining and
   backfill feeds, each group in feed-id order) and builds a `CandidateState`.
3. `write_candidate(candidate)` stages `candidates/<run_id>/`, then writes the
   events and health, then writes `runs/<run_id>.json` last with status
   `candidate`. Nothing in `catalog/` changes.
4. *(Step 7 seam, `runner._stage_delivery`)*: post-baseline runs render the
   digest, write a pending outbox entry, push, and send before step 5.
5. `promote(candidate)` is a compare-and-swap. It refuses (`PromotionConflict`)
   unless the current catalog's state hash equals `candidate.base_state_hash`.
   It also refuses unless the run is recorded and not yet promoted. It then
   rewrites `catalog/` and `schema.json`, marks the manifest `promoted`, and
   deletes the staged candidate.

If every feed fails, the runner calls `write_run(..., status="not_promoted")`
instead. That records the manifest, events and health without staging or
promoting. `--dry-run` reads only and writes nothing at all. `rebaseline-feed`
goes through the same `write_candidate`/`promote` path with no feed runs.

Promotion rewrites five catalog files one at a time, so a crash in the middle
can leave a torn catalog. Load detects this (see above). In Step 7 the Git
commit is the atomic unit, and a torn working tree is never committed.

## Migrations

`policy_tracker.core.migrations.MIGRATIONS[n]` upgrades a document from version
`n` to `n + 1`. `migrate()` refuses documents with no `schema_version` and
documents newer than the code. It also refuses when a step is missing. Catalog
migrations receive the assembled `catalog_state` document (all five parts).
Other files are migrated one by one when read. Migrated data is written back at
the new version on the next promotion. Schema version 1 is the first frozen
version, and no migrations are registered yet.
