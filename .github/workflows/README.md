# Workflows

| File | Trigger | Purpose |
|---|---|---|
| `ci.yml` | `pull_request`, push to `main` | `ruff check`, `validate-config`, `pytest`. No secrets, read-only token. |
| `init-data-branch.yml` | `workflow_dispatch` only | One-time creation of the orphan `tracker-data` branch. Refuses if it exists. |
| `tracker.yml` | `schedule` (`30 2 * * 1`), `workflow_dispatch` | Weekly run: retry pending delivery, crawl, digest, push, email, promote. |

`tracker.yml` and `init-data-branch.yml` share the concurrency group
`policy-tracker-data` with `cancel-in-progress: false`, so only one writer runs and
an active send is never cancelled. Neither triggers on pushes, so the tracker's
own `tracker-data` commits cannot start runs. Both need `contents: write` and
nothing else.

Manual dispatch inputs for `tracker.yml`:

- `mode`: `run` (default) or `baseline` (silent; never emails).
- `dry_run`: no email, commit, push, or durable change. The rendered digest and
  email are uploaded as a short-lived workflow artifact.
- `no_email`: archive the digest and promote without an outbox entry or email.

Configuration (repository settings; see `docs/OPERATIONS.md`):

- Variables: `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY` (`starttls` or `ssl`),
  optional `TRACKER_MAX_DOCUMENTS` and `TRACKER_ARTIFACT_BASE_URL`.
- Secrets: `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TO`.

Actions are pinned to major versions (`actions/checkout@v4`,
`actions/setup-python@v5`, `actions/upload-artifact@v4`).
