# Agent Working Agreement

## Required reading

Before changing this repository, read in this order:

1. `CONTEXT.md`
2. `docs/PROJECT_PLAN.md`
3. `docs/DECISIONS.md`
4. `docs/IMPLEMENTATION_STATUS.md`
5. `docs/SOURCE_INVENTORY.md`
6. The subsystem document relevant to the task (`ARCHITECTURE.md` or `OPERATIONS.md`)

Do not infer project state from chat history. The repository documents are the
handoff record.

## Current phase

Steps 1–7 are implemented and Step 8's offline acceptance is complete. The next
work is deployment and live acceptance (`docs/IMPLEMENTATION_STATUS.md`, "Exact
next task"). Changes to frozen contracts (`core/models.py`, `core/observations.py`,
`core/contracts.py`, `adapters/base.py`, `documents/types.py`,
`reporting/types.py`) need a decision entry and, for serialized records, a
migration.

## Non-negotiable product rules

- Capture every item on selected publication feeds; use prioritization only to
  organize the digest.
- Establish the initial historical baseline without sending publication alerts.
- Baseline and coverage status are tracked per feed, not only per run.
- Preserve old state whenever collection completeness is uncertain.
- Rolling/recent lists never establish removal.
- Distinguish publication date from first discovery date.
- Do not merge records solely by title, date, or PDF hash.
- Keep English listing metadata even if a linked file is Hindi-only, scanned, or
  cannot be extracted.
- Do not store source PDFs in Git.
- Deadline email notifications are changes-only; do not add approaching-deadline
  reminders without a recorded product decision.
- PDF replacement checks use server validators/metadata after initial hashing;
  document the known inability to catch every silent replacement.
- SMTP delivery is at-least-once and must use a durable pending-outbox protocol.

## Engineering workflow

- Follow the dependency order in `docs/PROJECT_PLAN.md`.
- Each step must include fixtures, tests, documentation, and migration notes.
- Steps 3–5 may run in parallel only after Step 2 contracts are merged.
- Update `docs/IMPLEMENTATION_STATUS.md` in every implementation PR.
- Add architectural or product decisions to `docs/DECISIONS.md`; do not bury
  decisions only in code or pull-request discussion.
- Keep live-source tests out of the deterministic default test suite.
- Never commit credentials, recipient addresses, downloaded PDFs, or local data.

## Completion and handoff format

Before ending a session:

1. Run the validation commands appropriate to the current step.
2. Record completed work, test results, blockers, and the exact next action in
   `docs/IMPLEMENTATION_STATUS.md`.
3. Update the source inventory or operational documentation if observed website
   behavior changed.
4. Leave the worktree in a state another agent can understand without the chat.
