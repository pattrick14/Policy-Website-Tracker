# Operations Context

Step 7 implements this operating model: `policy_tracker.operations.pipeline`
(durable sequence), `outbox` (pending bundles), `smtp` (delivery), `publish`
(the `tracker-data` branch), and `.github/workflows/` (see its `README.md`).

## Commands

```text
policy-tracker validate-config --config config/sources.yaml
policy-tracker init-data --data-dir <dir>                      # local, no git
policy-tracker init-data-branch --data-dir <worktree> [--repo .] [--remote origin]
policy-tracker baseline --config <cfg> --data-dir <dir> --no-email [--publish] [--dry-run]
policy-tracker run      --config <cfg> --data-dir <dir> [--publish] [--dry-run] [--no-email]
policy-tracker rebaseline-feed <feed-id> --reason <text> --data-dir <dir> [--publish]
policy-tracker outbox-status --data-dir <dir> [--publish]
policy-tracker abandon-delivery <delivery-id> --reason <text> --data-dir <dir> [--publish]
```

With `--publish`, `--data-dir` is a `git worktree` of `--repo` (default `.`) that
the command checks out at the remote `tracker-data` head before it starts, and
every durable step is committed and pushed with a compare-and-swap. Without
`--publish` the data directory is used in place and nothing is committed (local
development). `--max-documents N` (default `TRACKER_MAX_DOCUMENTS`, else 200)
caps how many documents enrichment downloads or revalidates per run, so the
silent baseline hashes history in batches over several runs.

Exit codes: `0` success; `1` every feed failed (diagnostics recorded); `2`
configuration, data, or blocked delivery (for example missing SMTP settings, a
recipient-set mismatch, or a pending delivery blocking a silent command); `3`
delivery still pending (SMTP refused, partial acceptance, or ambiguous timeout);
`4` data-branch race.

## Schedule and triggers

- Weekly cron: `30 2 * * 1` (Monday 02:30 UTC / nominal 08:00 IST).
- GitHub may delay scheduled jobs; exact start time is not guaranteed.
- `workflow_dispatch` inputs: `mode` (`run` or `baseline`), `dry_run`, `no_email`.
- Workflow-level concurrency (`policy-tracker-data`) allows one run and does not
  cancel an active run. `init-data-branch.yml` shares the group.
- No workflow triggers on pushes to `tracker-data`. `ci.yml` runs on pull
  requests and pushes to `main` with a read-only token and no secrets.
- Permissions are `contents: write` only, for pushing `tracker-data`.

## Branches

- `main`: code, configuration, tests, and documentation.
- `tracker-data`: orphan branch for generated state, events, health, outbox, and
  digest artifacts (`docs/DATA_LAYOUT.md`).

`init-data-branch.yml` (manual, one-time) creates the branch and refuses if it
already exists. Normal runs refuse a missing branch. Every push fetches first,
checks that the remote head is still the commit the run started from (or last
pushed), and pushes a fast-forward without `--force`. If the head moved, the run
raises a branch race and stops without overwriting anything.

## SMTP configuration

GitHub Actions variables:

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_SECURITY` (`starttls` or `ssl`; plaintext is prohibited)
- optional `TRACKER_MAX_DOCUMENTS` (enrichment cap per run, default 200)
- optional `TRACKER_ARTIFACT_BASE_URL` (base for archive links in the email;
  default `https://github.com/<repo>/blob/tracker-data`)

GitHub Actions secrets:

- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_FROM`
- `SMTP_TO` (comma-separated)

Certificates are verified (`ssl.create_default_context()`, TLS 1.2 minimum). A
server without STARTTLS fails the attempt; there is no plaintext fallback.
Missing or invalid settings block a `run` before anything is crawled. Logs and
messages carry counts and error classes only, never credentials, server
authentication text, or recipient addresses. Recipients go in the SMTP envelope
only; the `To` header is `undisclosed-recipients:;`.

## Durable outbox

`policy-tracker run` follows this sequence:

1. Retry each pending outbox entry before crawling, sending the same stored
   bundle with the same Message-ID. If it still fails, stop with exit code 3:
   no new crawl starts while a delivery is pending.
2. Crawl (live feeds first), enrich with the same shared per-site fetchers
   (`adapters.http.fetcher_for`), build the digest, and render the Markdown,
   JSON, and CSV digests, `open_deadlines.csv`, and the email.
3. Stage the candidate (including its deadline index and `open_deadlines.csv`),
   write the digest files and the immutable pending bundle, then commit and push
   *before* contacting SMTP.
4. Send to the recipients in `SMTP_TO`. They must hash to the bundle's stored
   `recipient_set_sha256`, or the send is refused.
5. When every recipient is accepted: promote the candidate (catalog, deadline
   index, and `open_deadlines.csv` together), mark the entry delivered, and
   commit and push again.
6. Partial acceptance, refusal, or an ambiguous timeout records the attempt
   (counts only), keeps the entry pending, pushes, and exits 3.
7. `baseline` promotes silently: no digest, no outbox entry, no email.

Delivery is at-least-once. If the runner dies after SMTP accepted the message
but before step 5 is pushed, the next run sends the same bundle again with the
same Message-ID, which lets most mail clients collapse the duplicate.

`--dry-run` performs no email, commit, push, or durable change. It renders into
a temporary directory and prints its path (the workflow uploads it as an
artifact). With a pending delivery it only reports it. `run --no-email`
archives the digest and promotes without an outbox entry. `baseline`,
`run --no-email`, and `rebaseline-feed` refuse while a delivery is pending.

Each run manifest records `base_data_commit`, `config_hash`, `code_revision`,
the digest `generated_at`, and each site fetcher's circuit-breaker state
(`context.fetchers`).

## Failure behavior

- Failed feed: retain that feed’s last state, advance healthy feeds, mark digest partial.
- All feeds failed: preserve catalog, persist diagnostics (committed), exit 1, no email.
- Enrichment failure: keep listing data and continue; the run notes it.
- SMTP failed: leave a recoverable pending delivery and do not begin a later crawl.
- Data branch race: abort (exit 4) and require a retry; never overwrite concurrent history.
- New/recovered feed: silently establish its own baseline and report coverage initialization.
- Gap over ten days: flag it in the next successful digest (from the previous
  promoted run's `generated_at`).

Enable GitHub workflow-failure notifications. Operational health is also
persisted in the data branch because email cannot report its own outage.

## Recovery runbook

**Manual run.** Actions → Weekly tracker → Run workflow. Use `dry_run` first to
preview the digest and email (artifact `dry-run-<id>`). Use `mode=baseline` for
the initial silent baseline. A run after more than ten days reports the gap.

**First deployment.** Set the variables and secrets, run "Initialize
tracker-data" once, then run Weekly tracker with `mode=baseline` until every
feed's baseline is complete (enrichment hashes at most `TRACKER_MAX_DOCUMENTS`
documents per run), then let the schedule take over.

**Stuck pending delivery** (exit 3 with "still pending"). Read
`outbox/<id>/attempts.json` on `tracker-data` for the error class. Fix the cause
(credentials, provider outage, a refused recipient) and re-run the workflow. The
retry sends the identical bundle. If `SMTP_TO` changed since the bundle was
written, the send is refused: restore the old value for one run, or abandon the
delivery. To give up on a bundle, run locally with push access:

```text
git worktree add --detach ../tracker-data origin/tracker-data   # or let --publish do it
policy-tracker outbox-status --data-dir ../tracker-data --publish
policy-tracker abandon-delivery <id> --reason "<why>" --data-dir ../tracker-data --publish
```

Abandoning moves the bundle to `outbox/abandoned/<id>/` and marks the run
`not_promoted`. The catalog stays at the old state, so the next run detects the
same changes again and sends a fresh digest.

**Branch race** (exit 4). Another writer advanced `tracker-data`. Nothing was
overwritten and, if the race hit the pre-send commit, nothing was sent. Find out
who wrote (the concurrency group should prevent it), then re-run. A race on the
post-send commit means the email went out but was not recorded, so the next
run re-sends it once (at-least-once).

**Torn or hand-edited data.** Load fails on a catalog hash mismatch. Every
`--publish` command starts from a clean checkout of the remote head, so local
damage is discarded. If a bad commit was pushed, revert it on `tracker-data`
with a normal (non-force) commit.

**Rebaseline a feed** after a site redesign:
`policy-tracker rebaseline-feed <feed-id> --reason "<text>" --data-dir <dir> --publish`.
The next run re-baselines that feed silently.

## Per-site fetching behavior

- **Ministry of Coal (D-017):** at most 0.25 requests/second with backoff.
  Faster traffic gets an HTTP 200 CAPTCHA page
  (`tests/fixtures/ministry_of_coal/captcha_block.html`). Detect it by content
  and treat it as a feed failure, never as an empty list. Pages also carry an
  anti-bot `<script src="/bnith__…">` whose token changes on every request;
  exclude it from page fingerprints. A full Coal enumeration costs about 85–90
  requests, roughly 6 minutes at that rate.
- **CERC (D-020):** 120 s timeout, apex-to-`www` host fallback on
  connection-level failures, at most six attempts per URL with 20 s and 40 s
  backoff, and a circuit breaker after three consecutive failed URLs. Both
  hosts share one rate budget of 1 request/second.
- **Ministry of Power:** a browser-style 403 block page can arrive with HTTP 200
  and must be detected by content.
- **Network policy:** the GitHub Actions runner must be able to reach all three
  sites. Cloud research sessions may need the hosts added to their allowed
  domains; on 2026-10-23 one session's proxy refused `www.coal.nic.in`.

## Retention

Retain state, events, digests, and run-health records indefinitely. Do not commit
downloaded PDFs. Review repository growth annually; any pruning requires a
documented migration and an auditable maintenance run.
Delivered and abandoned outbox bundles are retained like digests (they contain
no addresses). Extracted document text is never persisted; it exists only in
memory for classification during a run.
