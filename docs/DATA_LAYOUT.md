# Tracker Data Layout

`policy_tracker.operations.store.DataStore` owns the on-disk layout of the
`tracker-data` branch (or any directory with that layout).
`policy_tracker.operations.publish.GitPublisher` commits and pushes it, and
`policy_tracker.operations.outbox.Outbox` owns `outbox/`. Recipient addresses are never written:
a `PendingDelivery` stores only `recipient_set_sha256` and `recipient_count`.

## Serialization

`policy_tracker.core.serialize` is the only JSON writer:

- sorted keys, UTF-8 without `\u` escapes, 2-space indent, one trailing newline;
- enums are stored as their string values, tuples as lists, `Mapping` as objects;
- no floats appear in the schema; booleans are never accepted as integers;
- unknown fields, missing required fields and wrong types are rejected on read.

Every top-level file carries `schema_version` and a `kind`. Kinds in use:
`tracker_data`, `deadline_index`, `outbox_email`, `delivery_attempts`,
`delivery_abandoned`, `catalog_matters`, `catalog_documents`, `catalog_appearances`,
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
catalog/deadlines.json            deadline index (reporting.deadlines.index_to_data; absent before the first digest)
open_deadlines.csv                complete current view of open deadlines (rolling; promoted with the catalog)
candidates/<run_id>/candidate.json  staged candidate header (base_state_hash, state_hash)
candidates/<run_id>/catalog/*.json  staged candidate catalog (and deadlines.json), same format as catalog/
candidates/<run_id>/rolling/open_deadlines.csv  staged rolling view
events/YYYY/MM/<run_id>.json      {"run_id", "events": [Event, ...]} (omitted when empty)
runs/<run_id>.json                run manifest (below)
health/feeds.json                 latest health summary per feed
outbox/<run_id>/                  pending or delivered email bundle (below)
outbox/abandoned/<run_id>/        bundles an operator abandoned, plus abandoned.json
digests/YYYY/MM/<run_id>.{md,json,csv}  immutable per-run digests (written with the pending bundle)
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
4. Weekly runs (`pipeline.run_pipeline`) also stage `catalog/deadlines.json`
   and `rolling/open_deadlines.csv` in the candidate directory, write the
   per-run digests under `digests/`, create the outbox bundle, and commit and
   push all of it before SMTP. Promotion (step 5) happens only after every
   recipient accepted; baselines skip the digest and the outbox.
5. `promote(candidate)` is a compare-and-swap. It refuses (`PromotionConflict`)
   unless the current catalog's state hash equals `candidate.base_state_hash`.
   It also refuses unless the run is recorded and not yet promoted. It then
   rewrites `catalog/` (including a staged `deadlines.json`), copies a staged
   `open_deadlines.csv` to the root, rewrites `schema.json`, marks the manifest
   `promoted`, and deletes the staged candidate. A failed send therefore never
   advances the deadline index or the open-deadlines view.

If every feed fails, the runner calls `write_run(..., status="not_promoted")`
instead. That records the manifest, events and health without staging or
promoting. `--dry-run` reads only and writes nothing at all. `rebaseline-feed`
goes through the same `write_candidate`/`promote` path with no feed runs.

Promotion rewrites five catalog files one at a time, so a crash in the middle
can leave a torn catalog. Load detects this (see above). With `--publish` the
Git commit is the atomic unit: every command starts from a clean checkout of the
remote head, so a torn working tree is discarded, never committed.

A run that is abandoned (`abandon-delivery`) gets status `not_promoted` plus
`discarded: {reason, at}` in its manifest, and its staged candidate is deleted.

## Outbox

`outbox/<delivery_id>/` (the delivery id is the run id) holds one email:

```text
delivery.json     PendingDelivery (kind pending_delivery): message_id, base_data_commit,
                  code_revision, config_hash, digest_paths, recipient_set_sha256,
                  recipient_count, status (pending|delivered), attempts
email.json        kind outbox_email: subject, text_body, html_body, size_bytes,
                  attachments [{path, file, media_type, sha256}], content_sha256
attachments/<name>  attachment bytes (the digest CSVs when they fit under 5 MiB)
attempts.json     kind delivery_attempts: [{at, accepted, refused, ambiguous, error}]
```

`email.json` and `attachments/` are written once; `content_sha256` and each
attachment digest are verified on every load, so a modified bundle is refused.
Only `delivery.json` (status, attempts) and `attempts.json` change. Attempts
record counts and a masked error class, never addresses.

The Message-ID is deterministic: `<{run_id}.digest.policy-tracker@{domain}>`,
with the domain of `SMTP_FROM` at bundle time (else `policy-tracker.invalid`).
Recipient addresses are never stored. `recipient_set_sha256` is the SHA-256 of
the sorted, de-duplicated, lower-cased bare addresses joined by `\n`. At send
time the `SMTP_TO` secret must hash to it, or the send is refused.

`delivery.json` is written last, so a crash while a bundle is being created
leaves no visible entry. Entries are retained after delivery as the send record.

## Migrations

`policy_tracker.core.migrations.MIGRATIONS[n]` upgrades a document from version
`n` to `n + 1`. `migrate()` refuses documents with no `schema_version` and
documents newer than the code. It also refuses when a step is missing. Catalog
migrations receive the assembled `catalog_state` document (all five parts).
Other files are migrated one by one when read. Migrated data is written back at
the new version on the next promotion. Schema version 1 is the first frozen
version, and no migrations are registered yet.
