"""``policy-tracker`` command-line interface."""

from __future__ import annotations

import argparse
import functools
import logging
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from policy_tracker import inventory
from policy_tracker.adapters import http
from policy_tracker.adapters.base import Fetcher
from policy_tracker.adapters.registry import default_registry
from policy_tracker.core.contracts import FeedContract
from policy_tracker.documents.enrich import enrich
from policy_tracker.operations import pipeline, runner, smtp
from policy_tracker.operations.outbox import sender_domain
from policy_tracker.operations.publish import BranchRace, GitError, GitPublisher
from policy_tracker.operations.store import DataStore, StoreError
from policy_tracker.reporting import render
from policy_tracker.reporting.digest import build_digest

DEFAULT_CONFIG = Path("config/sources.yaml")
EXIT_ERROR = 2


def default_fetcher_factory(contract: FeedContract) -> Fetcher:
    """The shared, policy-enforcing per-site ``HttpFetcher`` (also used for enrichment)."""

    return http.fetcher_for(contract)


def close_fetchers() -> None:
    http.close_fetchers()


def fetcher_state(site_id: str) -> Mapping[str, Any] | None:
    """Breaker state of a site's shared fetcher, for the run manifest."""

    pool = getattr(http, "_DEFAULT_POOL", None)
    fetcher = pool.get(site_id) if pool is not None else None
    if fetcher is None:
        return None
    return {
        "circuit_open": bool(fetcher.circuit_open),
        "consecutive_failed_urls": int(fetcher.consecutive_failed_urls),
    }


def _artifact_base_url() -> str | None:
    """``TRACKER_ARTIFACT_BASE_URL``, else the tracker-data branch of ``GITHUB_REPOSITORY``."""

    explicit = os.environ.get("TRACKER_ARTIFACT_BASE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repository:
        return None
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    return f"{server}/{repository}/blob/tracker-data"


def default_renderer() -> pipeline.Renderer:
    return pipeline.Renderer(
        files=(render.render_markdown, render.render_json, render.render_csv),
        rolling=(render.render_open_deadlines_csv,),
        email=functools.partial(
            render.render_email, attach_csv=True, artifact_base_url=_artifact_base_url()
        ),
    )


def _publisher(args: argparse.Namespace) -> GitPublisher | None:
    if not getattr(args, "publish", False):
        return None
    return GitPublisher(repo=args.repo, worktree=args.data_dir, remote=args.remote)


DEFAULT_MAX_DOCUMENTS = 200


def _max_documents(args: argparse.Namespace) -> int:
    """``--max-documents``, else ``TRACKER_MAX_DOCUMENTS``, else 200 (baselines hash in batches)."""

    value = getattr(args, "max_documents", None)
    if value is None:
        raw = os.environ.get("TRACKER_MAX_DOCUMENTS", "").strip()
        value = int(raw) if raw else DEFAULT_MAX_DOCUMENTS
    if value < 0:
        raise ValueError("max documents must be zero or positive")
    return value


def default_services(args: argparse.Namespace, *, deliver: bool) -> pipeline.Services:
    """Production collaborators. SMTP settings load only when this run must email."""

    services = pipeline.Services(
        enricher=enrich,
        digest_builder=build_digest,
        renderer=default_renderer(),
        publisher=_publisher(args),
        fetcher_state=fetcher_state,
    )
    services.enrich_options = {"max_documents": _max_documents(args)}
    if deliver:
        settings = smtp.settings_from_env()
        services.recipients = smtp.recipients_from_env()
        services.sender = functools.partial(smtp.send, settings=settings)
        services.message_id_domain = sender_domain(settings.sender)
    return services


def code_revision() -> str:
    """``GITHUB_SHA``, else ``git rev-parse HEAD``, else ``"unknown"``."""

    sha = os.environ.get("GITHUB_SHA", "").strip()
    if sha:
        return sha
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="policy-tracker", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def config_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    def data_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument("--data-dir", type=Path, required=True)

    def git_args(p: argparse.ArgumentParser, *, flag: bool = True) -> None:
        if flag:
            p.add_argument(
                "--publish",
                action="store_true",
                help="treat --data-dir as a git worktree of the tracker-data branch; "
                "check it out first and commit+push (compare-and-swap) as the run proceeds",
            )
        p.add_argument("--repo", type=Path, default=Path("."), help="git repository (main)")
        p.add_argument("--remote", default="origin")

    p = sub.add_parser("validate-config", help="strictly validate the source configuration")
    config_arg(p)

    p = sub.add_parser("init-data", help="create an empty tracker-data layout")
    data_arg(p)

    p = sub.add_parser(
        "init-data-branch", help="one-time: create and push the orphan tracker-data branch"
    )
    data_arg(p)
    git_args(p, flag=False)

    for name, text in (
        ("baseline", "silent baseline: record and promote, never email"),
        ("run", "weekly run: retry pending delivery, crawl, digest, push, email, promote"),
    ):
        p = sub.add_parser(name, help=text)
        config_arg(p)
        data_arg(p)
        git_args(p)
        p.add_argument("--dry-run", action="store_true", help="no email, commit, push or writes")
        p.add_argument(
            "--no-email",
            action="store_true",
            help="baseline: explicit (never emails); run: archive the digest and promote "
            "without an outbox entry or email",
        )
        p.add_argument(
            "--max-documents",
            type=int,
            default=None,
            help="documents to download/revalidate this run "
            f"(default TRACKER_MAX_DOCUMENTS or {DEFAULT_MAX_DOCUMENTS})",
        )

    p = sub.add_parser("rebaseline-feed", help="silently re-baseline one feed")
    p.add_argument("feed_id")
    p.add_argument("--reason", required=True)
    config_arg(p)
    data_arg(p)
    git_args(p)

    p = sub.add_parser("outbox-status", help="list pending deliveries (counts only)")
    data_arg(p)
    git_args(p)

    p = sub.add_parser(
        "abandon-delivery", help="archive a stuck pending delivery and discard its candidate"
    )
    p.add_argument("delivery_id")
    p.add_argument("--reason", required=True)
    data_arg(p)
    git_args(p)
    return parser


def _validate_config(path: Path) -> int:
    from policy_tracker.core import config as config_module

    try:
        data = inventory.load_inventory(path)
    except (OSError, yaml.YAMLError) as exc:
        print(f"{path}: {exc}", file=sys.stderr)
        return 2
    errors = inventory.validate_inventory(data, require_complete=True)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    try:
        contracts = config_module.load_contracts(config_module.load_config(path))
    except config_module.ConfigError as exc:
        print(f"{path}: {exc}", file=sys.stderr)
        return 1
    print(f"{path}: valid ({len(contracts)} tracked feeds)")
    return 0


def _print_report(report: runner.RunReport) -> None:
    status = "dry run" if report.dry_run else ("promoted" if report.promoted else "not promoted")
    print(f"run {report.run_id} ({report.mode}): {status}")
    print(
        f"  feeds accepted {len(report.accepted_feeds)}/{len(report.feed_runs)}; "
        f"events {len(report.events)} (live {len(report.live_events)})"
    )
    for feed_run in report.feed_runs:
        if not feed_run.accepted:
            reasons = ", ".join((*feed_run.quarantine_reasons, *feed_run.diagnostics)) or "-"
            print(f"  failed {feed_run.feed_id}: {reasons}")
    if report.delivery:
        print(f"  delivery: {report.delivery}")
    if report.data_commit:
        print(f"  tracker-data head: {report.data_commit}")
    for message in report.messages:
        print(f"  {message}")


def _run_command(args: argparse.Namespace) -> int:
    deliver = args.command == "run" and not args.no_email and not args.dry_run
    services = default_services(args, deliver=deliver)
    report = runner.run_feeds(
        args.config,
        args.data_dir,
        registry=default_registry(),
        fetcher_factory=default_fetcher_factory,
        mode=args.command,
        dry_run=args.dry_run,
        code_revision=code_revision(),
        now=runner.utc_now,
        no_email=args.no_email,
        services=services,
    )
    _print_report(report)
    return report.exit_code


def _init_data_branch(args: argparse.Namespace) -> int:
    publisher = GitPublisher(repo=args.repo, worktree=args.data_dir, remote=args.remote)
    commit = publisher.init_data_branch(
        lambda root: DataStore(root).initialize(), "Initialize tracker-data layout"
    )
    print(f"initialized {publisher.branch} at {commit}")
    return 0


def _outbox_status(args: argparse.Namespace) -> int:
    publisher = _publisher(args)
    if publisher is not None:
        publisher.checkout_data()
    pending = pipeline.pending_deliveries(args.data_dir)
    if not pending:
        print("no pending deliveries")
    for header in pending:
        print(
            f"pending {header.delivery_id}: attempts {header.attempts}, "
            f"{header.recipient_count} recipient(s), base {header.base_data_commit[:12]}"
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate-config":
            return _validate_config(args.config)
        if args.command == "init-data":
            DataStore(args.data_dir).initialize()
            print(f"initialized {args.data_dir}")
            return 0
        if args.command == "init-data-branch":
            return _init_data_branch(args)
        if args.command in ("baseline", "run"):
            return _run_command(args)
        if args.command == "rebaseline-feed":
            run_id = runner.rebaseline(
                args.config,
                args.data_dir,
                args.feed_id,
                reason=args.reason,
                code_revision=code_revision(),
                now=runner.utc_now,
                publisher=_publisher(args),
            )
            print(f"rebaselined {args.feed_id} in run {run_id}")
            return 0
        if args.command == "outbox-status":
            return _outbox_status(args)
        if args.command == "abandon-delivery":
            pipeline.abandon_delivery(
                args.data_dir,
                args.delivery_id,
                reason=args.reason,
                now=runner.utc_now,
                publisher=_publisher(args),
            )
            print(f"abandoned delivery {args.delivery_id}; the next run re-detects its changes")
            return 0
    except BranchRace as exc:
        print(f"error: data branch race: {exc}", file=sys.stderr)
        return pipeline.EXIT_BRANCH_RACE
    except (smtp.SmtpConfigError, pipeline.DeliveryBlocked) as exc:
        print(f"blocked: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except (StoreError, GitError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        close_fetchers()
    raise AssertionError(f"unhandled command {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
