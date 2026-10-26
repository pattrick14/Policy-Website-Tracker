"""Shared offline helpers for Step 7 tests: a local bare git remote and fakes."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from policy_tracker.operations.smtp import SendResult
from policy_tracker.reporting.types import RenderedEmail


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@dataclass
class GitRemote:
    bare: Path
    main: Path  # a clone of ``bare`` with one commit on main (like actions/checkout)
    tmp: Path

    def remote_head(self, branch: str = "tracker-data") -> str | None:
        out = git("ls-remote", "--heads", str(self.bare), f"refs/heads/{branch}", cwd=self.tmp)
        return out.split()[0] if out else None

    def race(self, filename: str = "race.txt") -> str:
        """Push an unrelated commit to tracker-data from another clone; return its sha."""

        other = self.tmp / f"racer-{filename}"
        git("clone", "--quiet", "-b", "tracker-data", str(self.bare), str(other), cwd=self.tmp)
        (other / filename).write_text("concurrent writer\n")
        git("add", "-A", cwd=other)
        git("commit", "--quiet", "-m", "concurrent", cwd=other)
        git("push", "--quiet", "origin", "tracker-data", cwd=other)
        return git("rev-parse", "HEAD", cwd=other)


def make_git_remote(tmp_path: Path) -> GitRemote:
    bare = tmp_path / "remote.git"
    git("init", "--quiet", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    main = tmp_path / "main"
    git("clone", "--quiet", str(bare), str(main), cwd=tmp_path)
    git("commit", "--quiet", "--allow-empty", "-m", "main", cwd=main)
    git("push", "--quiet", "origin", "HEAD:main", cwd=main)
    return GitRemote(bare=bare, main=main, tmp=tmp_path)


@dataclass
class FakeSender:
    """Scripted SMTP outcomes; records every send (message id, email, recipients)."""

    results: list[str] = field(default_factory=lambda: ["ok"])
    calls: list[tuple[str, RenderedEmail, tuple[str, ...]]] = field(default_factory=list)

    def __call__(self, email: RenderedEmail, *, message_id: str, recipients) -> SendResult:
        recipients = tuple(recipients)
        self.calls.append((message_id, email, recipients))
        outcome = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if outcome == "ok":
            return SendResult(accepted=recipients, refused={}, ambiguous=False)
        if outcome == "partial":
            return SendResult(
                accepted=recipients[:1], refused={r: "550" for r in recipients[1:]}, ambiguous=False
            )
        if outcome == "refused":
            return SendResult(
                accepted=(),
                refused={r: "550" for r in recipients},
                ambiguous=False,
                error="all_recipients_refused",
            )
        if outcome == "timeout":
            return SendResult(accepted=(), refused={}, ambiguous=True, error="timeout")
        raise AssertionError(outcome)
