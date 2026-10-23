"""``policy-tracker`` command-line interface."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import yaml

from policy_tracker import inventory
from policy_tracker.adapters.base import FetchResponse
from policy_tracker.adapters.registry import default_registry
from policy_tracker.core.contracts import FeedContract
from policy_tracker.operations import runner
from policy_tracker.operations.store import DataStore, StoreError

DEFAULT_CONFIG = Path("config/sources.yaml")


class _UnavailableFetcher:
    """Step 2 placeholder; Step 3 supplies the policy-enforcing HTTP fetcher."""

    def get(self, url: str, *, headers: Mapping[str, str] | None = None) -> FetchResponse:
        del headers
        return FetchResponse(url=url, status_code=0, headers={}, body=b"", error="no_fetcher")


def default_fetcher_factory(contract: FeedContract) -> _UnavailableFetcher:
    del contract
    return _UnavailableFetcher()


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

    p = sub.add_parser("validate-config", help="strictly validate the source configuration")
    config_arg(p)

    p = sub.add_parser("init-data", help="create an empty tracker-data layout")
    data_arg(p)

    p = sub.add_parser("baseline", help="silent baseline: record and promote, never email")
    config_arg(p)
    data_arg(p)
    p.add_argument("--no-email", action="store_true", help="explicit; baselines never email")

    p = sub.add_parser("run", help="weekly run")
    config_arg(p)
    data_arg(p)
    p.add_argument("--dry-run", action="store_true", help="write nothing, send nothing")

    p = sub.add_parser("rebaseline-feed", help="silently re-baseline one feed")
    p.add_argument("feed_id")
    p.add_argument("--reason", required=True)
    config_arg(p)
    data_arg(p)
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
    for message in report.messages:
        print(f"  {message}")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate-config":
            return _validate_config(args.config)
        if args.command == "init-data":
            DataStore(args.data_dir).initialize()
            print(f"initialized {args.data_dir}")
            return 0
        if args.command in ("baseline", "run"):
            report = runner.run_feeds(
                args.config,
                args.data_dir,
                registry=default_registry(),
                fetcher_factory=default_fetcher_factory,
                mode=args.command,
                dry_run=getattr(args, "dry_run", False),
                code_revision=code_revision(),
                now=runner.utc_now,
            )
            _print_report(report)
            return report.exit_code
        if args.command == "rebaseline-feed":
            run_id = runner.rebaseline(
                args.config,
                args.data_dir,
                args.feed_id,
                reason=args.reason,
                code_revision=code_revision(),
                now=runner.utc_now,
            )
            print(f"rebaselined {args.feed_id} in run {run_id}")
            return 0
    except (StoreError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    raise AssertionError(f"unhandled command {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
