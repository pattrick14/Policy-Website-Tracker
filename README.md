# Policy Website Tracker

This repository tracks policy-relevant publications from the Ministry of Power,
Ministry of Coal, and Central Electricity Regulatory Commission (CERC). It
identifies new and changed drafts, regulations, orders, reports, consultations,
and stakeholder-submission opportunities, and publishes a weekly email digest
plus durable Markdown, CSV, and JSON archives.

The tracker is implemented and tested offline. Deployment and live acceptance
are the next step; see [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md)
and [`docs/OPERATIONS.md`](docs/OPERATIONS.md).

## Start here

New contributors and agents should read these files in order:

1. [`AGENTS.md`](AGENTS.md) — working rules and handoff protocol.
2. [`CONTEXT.md`](CONTEXT.md) — product context, scope, and success criteria.
3. [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) — approved construction plan.
4. [`docs/DECISIONS.md`](docs/DECISIONS.md) — decisions that must not be silently changed.
5. [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md) — current state and next task.
6. [`docs/SOURCE_INVENTORY.md`](docs/SOURCE_INVENTORY.md) — the approved source inventory.
7. [`docs/OPERATIONS.md`](docs/OPERATIONS.md) — commands, workflows, and recovery runbook.

## Layout

```text
config/                 Source and classification configuration
docs/                   Product, architecture, operations, and handoff documentation
src/policy_tracker/     Python package: adapters, core, documents, reporting, operations
tests/                  Unit, adapter, digest, operations, and acceptance tests with fixtures
scripts/                Repository-maintenance helpers
.github/workflows/      Weekly tracker, data-branch initialization, and CI workflows
```

The original input list remains in [`websites.txt`](websites.txt).

## Development

```text
pip install -e '.[dev]'
python -m pytest -q
ruff check .
policy-tracker validate-config --config config/sources.yaml
policy-tracker run --config config/sources.yaml --data-dir <path> --dry-run
```

`docs/OPERATIONS.md` lists every command, exit code, and workflow input.
