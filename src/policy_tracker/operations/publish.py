"""Publish the orphan ``tracker-data`` branch with compare-and-swap pushes (D-010).

``GitPublisher`` drives ``git`` through ``subprocess`` in a dedicated worktree of a
repository whose remote already carries credentials (in GitHub Actions, the
``actions/checkout`` clone of ``main``). It never force-pushes:

- ``init_data_branch`` creates the branch once and refuses if it already exists;
- ``checkout_data`` resets the worktree to the remote head (optionally asserting
  it equals an expected commit) and refuses a missing branch;
- ``commit_and_push`` fetches, checks the remote head still equals the expected
  commit, and pushes a fast-forward; any mismatch or rejection raises
  ``BranchRace`` and leaves remote history untouched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

DATA_BRANCH = "tracker-data"
DEFAULT_AUTHOR = ("policy-tracker[bot]", "policy-tracker@users.noreply.github.com")


class GitError(RuntimeError):
    """A git command failed."""


class BranchRace(GitError):
    """The remote data branch is not at the expected commit; nothing was pushed."""


class DataBranchMissing(GitError):
    """The remote data branch does not exist; run ``init-data-branch`` first."""


class DataBranchExists(GitError):
    """``init-data-branch`` refused: the data branch already exists."""


@dataclass
class GitPublisher:
    """Publish ``worktree`` (a tracker-data checkout) to ``remote``'s ``branch``."""

    repo: Path  # a git repository with ``remote`` configured (credentials included)
    worktree: Path  # the tracker-data checkout; a ``git worktree`` of ``repo``
    remote: str = "origin"
    branch: str = DATA_BRANCH
    author: tuple[str, str] = DEFAULT_AUTHOR
    timeout: float = 300.0

    def __post_init__(self) -> None:
        self.repo = Path(self.repo).resolve()
        self.worktree = Path(self.worktree).resolve()
        self.head: str | None = None

    # -- plumbing ----------------------------------------------------------------------
    @property
    def _tracking_ref(self) -> str:
        return f"refs/remotes/{self.remote}/{self.branch}"

    def _run(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        name, email = self.author
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
            "GIT_TERMINAL_PROMPT": "0",
        }
        command = ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args]
        try:
            return subprocess.run(
                command,
                cwd=cwd or self.repo,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise GitError(f"git {args[0]} could not run: {type(exc).__name__}") from None

    def _git(self, *args: str, cwd: Path | None = None, check: bool = True) -> str:
        result = self._run(*args, cwd=cwd)
        if check and result.returncode != 0:
            # stderr can echo a remote URL; keep only the first line, never the URL.
            first = (result.stderr.strip().splitlines() or [""])[0]
            raise GitError(f"git {args[0]} failed ({result.returncode}): {_scrub(first)}")
        return result.stdout.strip()

    def remote_head(self) -> str | None:
        """The remote branch's commit, fetched into the tracking ref; ``None`` if absent."""

        listing = self._git("ls-remote", "--heads", self.remote, f"refs/heads/{self.branch}")
        if not listing:
            return None
        self._git(
            "fetch", "--no-tags", "--quiet", self.remote,
            f"+refs/heads/{self.branch}:{self._tracking_ref}",
        )  # fmt: skip
        return self._git("rev-parse", "--verify", f"{self._tracking_ref}^{{commit}}")

    def _is_worktree(self) -> bool:
        if not (self.worktree / ".git").exists():
            return False
        top = self._git("rev-parse", "--show-toplevel", cwd=self.worktree, check=False)
        common = self._git("rev-parse", "--git-common-dir", cwd=self.worktree, check=False)
        repo_common = self._git("rev-parse", "--git-common-dir")
        return (
            Path(top).resolve() == self.worktree
            and (self.worktree / common).resolve() == (self.repo / repo_common).resolve()
        )

    def _prepare_worktree(self, commit: str | None) -> None:
        """Attach ``worktree`` to ``commit`` (detached), discarding local changes."""

        if self._is_worktree():
            if commit is not None:
                self._git("checkout", "--quiet", "--detach", "--force", commit, cwd=self.worktree)
            self._git("reset", "--quiet", "--hard", cwd=self.worktree)
            self._git("clean", "--quiet", "-ffdx", cwd=self.worktree)
            return
        if self.worktree.exists() and any(self.worktree.iterdir()):
            raise GitError(f"{self.worktree} exists and is not a worktree of {self.repo}")
        self._git("worktree", "prune")
        if commit is None:
            self._git("worktree", "add", "--quiet", "--detach", str(self.worktree))
        else:
            self._git("worktree", "add", "--quiet", "--detach", str(self.worktree), commit)

    # -- public API --------------------------------------------------------------------
    def init_data_branch(self, initialize: Callable[[Path], None], message: str) -> str:
        """One-time creation of the orphan branch; refuse if it already exists."""

        if self.remote_head() is not None:
            raise DataBranchExists(f"{self.remote}/{self.branch} already exists; refusing")
        if self._is_worktree():
            self._git("worktree", "remove", "--force", str(self.worktree))
        elif self.worktree.exists() and not any(self.worktree.iterdir()):
            self.worktree.rmdir()
        self._prepare_worktree(None)
        orphan = f"{self.branch}-init"
        self._git("checkout", "--quiet", "--orphan", orphan, cwd=self.worktree)
        self._git(
            "rm", "-r", "-f", "--quiet", "--cached", "--ignore-unmatch", ".", cwd=self.worktree
        )
        for entry in self.worktree.iterdir():
            if entry.name == ".git":
                continue
            shutil.rmtree(entry) if entry.is_dir() and not entry.is_symlink() else entry.unlink()
        initialize(self.worktree)
        self._git("add", "--all", cwd=self.worktree)
        self._git("commit", "--quiet", "--no-verify", "-m", message, cwd=self.worktree)
        commit = self._git("rev-parse", "HEAD", cwd=self.worktree)
        self._push(commit)
        self._git("checkout", "--quiet", "--detach", cwd=self.worktree)
        self._git("branch", "-D", orphan)
        self.head = commit
        return commit

    def checkout_data(self, expected_base: str | None = None) -> str:
        """Reset the worktree to the remote head and return it.

        Raise ``DataBranchMissing`` if the branch does not exist and ``BranchRace``
        if ``expected_base`` is given and differs from the remote head.
        """

        head = self.remote_head()
        if head is None:
            raise DataBranchMissing(
                f"{self.remote}/{self.branch} does not exist; run the init-data-branch workflow"
            )
        if expected_base is not None and head != expected_base:
            raise BranchRace(f"{self.branch} is at {head[:12]}, expected {expected_base[:12]}")
        self._prepare_worktree(head)
        self.head = head
        return head

    def has_changes(self) -> bool:
        return bool(self._git("status", "--porcelain", "--untracked-files=all", cwd=self.worktree))

    def commit_and_push(self, message: str, expected_remote_head: str) -> str:
        """Commit everything in the worktree and fast-forward the remote branch.

        Raises ``BranchRace`` when the remote head is not ``expected_remote_head``
        (checked after a fresh fetch, and again by git's fast-forward-only push).
        Returns the new head (or ``expected_remote_head`` when nothing changed).
        """

        local = self._git("rev-parse", "HEAD", cwd=self.worktree)
        if local != expected_remote_head:
            raise BranchRace(
                f"worktree is at {local[:12]}, expected {expected_remote_head[:12]}; "
                "check out the data branch again"
            )
        if not self.has_changes():
            self.head = expected_remote_head
            return expected_remote_head
        self._git("add", "--all", cwd=self.worktree)
        self._git("commit", "--quiet", "--no-verify", "-m", message, cwd=self.worktree)
        commit = self._git("rev-parse", "HEAD", cwd=self.worktree)
        remote = self.remote_head()
        if remote != expected_remote_head:
            raise BranchRace(
                f"{self.remote}/{self.branch} advanced to {str(remote)[:12]} "
                f"(expected {expected_remote_head[:12]}); refusing to overwrite it"
            )
        self._push(commit)
        self.head = commit
        return commit

    def _push(self, commit: str) -> None:
        # No --force and no lease override: git rejects anything but a fast-forward.
        refspec = f"{commit}:refs/heads/{self.branch}"
        result = self._run("push", "--porcelain", self.remote, refspec, cwd=self.worktree)
        if result.returncode == 0:
            return
        report = f"{result.stdout}\n{result.stderr}"
        if any(word in report for word in ("[rejected]", "non-fast-forward", "fetch first")):
            raise BranchRace(f"push to {self.branch} rejected as non-fast-forward; nothing pushed")
        first = (result.stderr.strip().splitlines() or [""])[0]
        raise GitError(f"git push failed ({result.returncode}): {_scrub(first)}")


def _scrub(text: str) -> str:
    """Drop anything that looks like a URL (which may embed a token)."""

    return " ".join("<url>" if "://" in word or "@" in word else word for word in text.split())
