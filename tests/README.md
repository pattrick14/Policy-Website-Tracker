# Test Layout

The runtime is not implemented yet. Planned deterministic suites:

- `core/` — schemas, identity, state, diff, completeness, and baseline behavior.
- `adapters/` — one fixture-driven suite per source website.
- `enrichment/` — document validation, extraction, OCR, language, and deadlines.
- `digests/` — classification and renderer snapshots.
- `operations/` — data-branch, outbox, SMTP, and crash-recovery behavior.
- `fixtures/` — sanitized source responses with provenance metadata.

Live-source smoke tests must be explicitly selected and must not run as part of
the deterministic default test suite.
