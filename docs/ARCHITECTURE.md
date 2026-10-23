# Architecture Context

> This document records the approved target architecture. Step 2 implemented the
> `core/` contracts, state engine, store, and CLI; adapters, documents,
> reporting, and delivery follow in Steps 3–7.

## Component flow

```text
Configured feeds
    → source adapters
    → normalized records + completeness evidence
    → identity/state/diff engine
    → bounded document enrichment
    → deterministic classification and deadlines
    → immutable digest + candidate state
    → tracker-data pending outbox
    → SMTP delivery
    → promoted state and archived artifacts
```

## Package boundaries

- `adapters/`: source-specific discovery and normalization. An adapter must also
  report fetch, parse, pagination, and coverage evidence.
- `core/`: schemas, identity, state, migrations, diff, event generation, and
  per-feed baseline semantics.
- `documents/`: allowlisted retrieval, validators, byte/text fingerprints,
  bounded extraction, OCR, and language/extraction status.
- `reporting/`: classification, deadline extraction/state, digest construction,
  and Markdown/CSV/JSON/email renderers.
- `operations/`: CLI orchestration, branch publication, durable outbox, SMTP,
  run health, and recovery.

Adapters must not send email or mutate durable state. Renderers must operate on
normalized events rather than scraping output. Operations must not promote a
candidate state before the associated email is accepted, except for silent
baseline runs.

## Identity invariants

- Matter, document, appearance, and version are separate concepts.
- Native IDs take precedence; source-scoped canonical URL is the next fallback.
- Preserve source provenance and original URL even after canonicalization.
- Multiple appearances can reference one document.
- A shared file hash does not prove documents or matters are identical.
- Metadata fingerprints and byte/text fingerprints serve different purposes.
- Parent-child relationships require explicit source evidence; unresolved links
  are stored without speculative merging.

## Completeness invariants

- An HTTP response can succeed while parsing or enumeration fails.
- State advances per successfully and completely evaluated feed.
- Removal counters advance only for complete authoritative collections.
- Suspiciously incomplete results retain prior state and generate health alerts.
- Baseline state is per feed and per coverage revision.

## Planned schemas

Version the serialized forms of `Matter`, `Document`, `Appearance`,
`DocumentVersion`, `Event`, `FeedRun`, `PendingDelivery`, and `CandidateState`.
Step 2 owns the exact field definitions and migration protocol; adapters must not
be developed until those contracts are frozen and fixture-tested.

## Safety boundaries

- Allowlist source/download hosts and reject private-address redirects.
- Bound redirect count, response size, time, extraction pages, and OCR duration.
- Treat PDFs and all source strings as untrusted input.
- Escape rendered output and defend CSV consumers from formula execution.
- Keep credentials unavailable to untrusted pull-request workflows.
