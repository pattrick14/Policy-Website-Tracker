# Operations Context

> Target operating model; implementation is scheduled for Step 7.

## Schedule and triggers

- Weekly cron: `30 2 * * 1` (Monday 02:30 UTC / nominal 08:00 IST).
- GitHub may delay scheduled jobs; exact start time is not guaranteed.
- Support manual `workflow_dispatch` with `dry_run` and `no_email` inputs.
- Workflow-level concurrency allows one run and does not cancel an active run.
- Do not trigger on tracker-generated branch pushes.

## Branches

- `main`: code, configuration, tests, and documentation.
- `tracker-data`: orphan branch for generated state, events, health, outbox, and
  digest artifacts.

Normal runs must compare the expected data-branch commit before pushing and stop
on unexpected advancement instead of force-pushing. A separate one-time workflow
will initialize the data branch and schema manifest.

## SMTP configuration

GitHub Actions variables:

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_SECURITY` (`starttls` or `ssl`; plaintext is prohibited)

GitHub Actions secrets:

- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_FROM`
- `SMTP_TO`

Require certificate verification. Do not log credentials, full authentication
errors, or recipient addresses.

## Durable outbox

Before a new crawl, retry any pending delivery. A pending bundle contains the
immutable digest, candidate state or delta, base data commit, code/config hashes,
recipient set, and delivery status. It is pushed before SMTP begins.

Promote candidate state only after all recipients are accepted. Partial
acceptance or ambiguous timeouts remain pending and retry the same immutable
bundle and recipient set. Delivery is at-least-once; duplicates remain possible
after an SMTP success followed by a crash before the success marker is pushed.

## Failure behavior

- Failed feed: retain that feed’s last state, advance healthy feeds, mark digest partial.
- All feeds failed: preserve catalog, persist diagnostics, fail the workflow.
- SMTP failed: leave recoverable pending delivery and do not begin a later crawl.
- Data branch race: abort and require retry; never overwrite concurrent history.
- New/recovered feed: silently establish its own baseline and report coverage initialization.
- Gap over ten days: flag it in the next successful digest and run manifest.

Enable GitHub workflow-failure notifications. Operational health must also be
persisted in the data branch because email cannot report its own outage reliably.

## Retention

Retain state, events, digests, and run-health records indefinitely. Do not commit
downloaded PDFs. Review repository growth annually; any pruning requires a
documented migration and an auditable maintenance run.
