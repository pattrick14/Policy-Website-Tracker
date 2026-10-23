"""tracker-data branch publishing against a local bare repository (no network)."""

from __future__ import annotations

from pathlib import Path

import pytest
from ops_helpers import GitRemote, git

from policy_tracker.operations.publish import (
    BranchRace,
    DataBranchExists,
    DataBranchMissing,
    GitPublisher,
)
from policy_tracker.operations.store import DataStore


def _publisher(remote: GitRemote) -> GitPublisher:
    return GitPublisher(repo=remote.main, worktree=remote.tmp / "data")


def _init(remote: GitRemote) -> tuple[GitPublisher, str]:
    publisher = _publisher(remote)
    return publisher, publisher.init_data_branch(
        lambda root: DataStore(root).initialize(), "init tracker-data"
    )


def test_init_creates_orphan_branch_once(git_remote: GitRemote) -> None:
    publisher, commit = _init(git_remote)
    assert git_remote.remote_head() == commit
    # Orphan: no shared history with main.
    assert git("rev-list", "--count", commit, cwd=git_remote.bare) == "1"
    files = git("ls-tree", "-r", "--name-only", commit, cwd=git_remote.bare).splitlines()
    assert "schema.json" in files and "catalog/feeds.json" in files
    with pytest.raises(DataBranchExists):
        publisher.init_data_branch(lambda root: None, "again")
    assert git_remote.remote_head() == commit


def test_checkout_refuses_missing_branch(git_remote: GitRemote) -> None:
    with pytest.raises(DataBranchMissing):
        _publisher(git_remote).checkout_data()


def test_commit_and_push_fast_forwards(git_remote: GitRemote) -> None:
    publisher, base = _init(git_remote)
    head = publisher.checkout_data(expected_base=base)
    assert head == base
    (publisher.worktree / "runs" / "note.txt").write_text("x\n")
    new = publisher.commit_and_push("one", head)
    assert new != head and git_remote.remote_head() == new
    assert publisher.commit_and_push("no changes", new) == new  # nothing to commit


def test_branch_race_aborts_without_overwriting(git_remote: GitRemote) -> None:
    publisher, base = _init(git_remote)
    head = publisher.checkout_data()
    racer = git_remote.race()
    (publisher.worktree / "runs" / "mine.txt").write_text("mine\n")
    with pytest.raises(BranchRace):
        publisher.commit_and_push("mine", head)
    assert git_remote.remote_head() == racer
    tree = git("ls-tree", "-r", "--name-only", racer, cwd=git_remote.bare)
    assert "runs/mine.txt" not in tree
    with pytest.raises(BranchRace):
        publisher.checkout_data(expected_base=base)
    # A fresh checkout picks up the concurrent history and discards local work.
    assert publisher.checkout_data() == racer
    assert not (publisher.worktree / "runs" / "mine.txt").exists()
    assert (publisher.worktree / "race.txt").exists()


def test_push_rejection_is_a_race(git_remote: GitRemote, monkeypatch: pytest.MonkeyPatch) -> None:
    """Even if the remote advances after the pre-push fetch, git refuses a non-fast-forward."""

    publisher, _ = _init(git_remote)
    head = publisher.checkout_data()
    (publisher.worktree / "runs" / "mine.txt").write_text("mine\n")
    real_remote_head = publisher.remote_head
    racer: list[str] = []

    def advance_then_lie() -> str | None:
        observed = real_remote_head()
        if not racer:
            racer.append(git_remote.race())
        return observed

    monkeypatch.setattr(publisher, "remote_head", advance_then_lie)
    with pytest.raises(BranchRace):
        publisher.commit_and_push("mine", head)
    assert git_remote.remote_head() == racer[0]


def test_existing_non_worktree_directory_refused(git_remote: GitRemote, tmp_path: Path) -> None:
    _init(git_remote)
    data = tmp_path / "elsewhere"
    data.mkdir()
    (data / "file.txt").write_text("user data")
    with pytest.raises(Exception, match="not a worktree"):
        GitPublisher(repo=git_remote.main, worktree=data).checkout_data()
    assert (data / "file.txt").read_text() == "user data"
