# Policy Website Tracker

This repository will track policy-relevant publications from the Ministry of
Power, Ministry of Coal, and Central Electricity Regulatory Commission (CERC).
It will identify new and changed drafts, regulations, orders, reports,
consultations, and stakeholder-submission opportunities and publish a weekly
email digest plus durable Markdown, CSV, and JSON archives.

The project is currently scaffolded but not implemented. The next task is the
website-research and source-inventory gate described in
[`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md).

## Start here

New contributors and agents should read these files in order:

1. [`AGENTS.md`](AGENTS.md) — working rules and handoff protocol.
2. [`CONTEXT.md`](CONTEXT.md) — product context, scope, and success criteria.
3. [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) — approved construction plan.
4. [`docs/DECISIONS.md`](docs/DECISIONS.md) — decisions that must not be silently changed.
5. [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md) — current state and next task.
6. [`docs/SOURCE_INVENTORY.md`](docs/SOURCE_INVENTORY.md) — preliminary source research and the required research deliverable.

## Planned layout

```text
config/                 Source and classification configuration
docs/                   Product, architecture, operations, and handoff documentation
src/policy_tracker/     Python package (currently empty scaffolding)
tests/                  Test and fixture areas (currently empty scaffolding)
scripts/                Repository-maintenance helpers
.github/workflows/      GitHub Actions workflows, added during operations work
```

The original input list remains in [`websites.txt`](websites.txt).

## Step 1 validation

Install the package in a development environment and run:

```text
policy-tracker-inventory config/sources.yaml
policy-tracker-inventory config/sources.yaml --require-complete
```

The first command checks contract structure. The second is the research exit
gate and intentionally fails while any site, history boundary, or fixture set
is still preliminary.
