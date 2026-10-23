"""Migration registry tests, using a synthetic v0 -> v1 migration."""

from __future__ import annotations

import pytest

from policy_tracker.core import migrations, serialize
from policy_tracker.core.models import SCHEMA_VERSION, FeedState


def _v0_to_v1(document: dict) -> dict:
    """Synthetic: v0 called the field ``failures`` and had no parser version."""

    if document.get("kind") == "feed_state":
        document["consecutive_failures"] = document.pop("failures")
        document.setdefault("parser_version", 1)
    return document


@pytest.fixture
def v0_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(migrations, "MIGRATIONS", {0: _v0_to_v1})


V0_FEED_STATE = {
    "schema_version": 0,
    "kind": "feed_state",
    "feed_id": "f",
    "baseline_status": "complete",
    "coverage_revision": 1,
    "baselined_revision": 1,
    "failures": 2,
}


def test_current_version_has_no_registered_steps() -> None:
    assert SCHEMA_VERSION == 1
    assert all(version < SCHEMA_VERSION for version in migrations.MIGRATIONS)


def test_current_document_passes_through_unchanged_copy() -> None:
    document = {"schema_version": SCHEMA_VERSION, "kind": "x", "nested": {"a": 1}}
    result = migrations.migrate(document)
    assert result == document
    result["nested"]["a"] = 2
    assert document["nested"]["a"] == 1


def test_synthetic_v0_document_upgrades_and_decodes(v0_registry: None) -> None:
    original = dict(V0_FEED_STATE)
    upgraded = migrations.migrate(original)
    assert upgraded["schema_version"] == 1
    assert upgraded["consecutive_failures"] == 2
    assert original == V0_FEED_STATE  # input untouched
    state = serialize.decode_document(FeedState, V0_FEED_STATE)
    assert state.consecutive_failures == 2 and state.parser_version == 1


def test_missing_step_is_an_error() -> None:
    with pytest.raises(migrations.MigrationError, match="no migration registered from"):
        migrations.migrate(dict(V0_FEED_STATE))


def test_newer_version_is_refused() -> None:
    with pytest.raises(migrations.MigrationError, match="newer than supported"):
        migrations.migrate({"schema_version": SCHEMA_VERSION + 1})


@pytest.mark.parametrize("bad", [{}, {"schema_version": "1"}, {"schema_version": True}, {"schema_version": -1}])
def test_missing_or_invalid_version_is_refused(bad: dict) -> None:
    with pytest.raises(migrations.MigrationError):
        migrations.migrate(bad)


def test_chain_applies_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []

    def step(n: int):
        def apply(document: dict) -> dict:
            seen.append(n)
            assert document["schema_version"] == n
            return document

        return apply

    monkeypatch.setattr(migrations, "MIGRATIONS", {0: step(0), 1: step(1), 2: step(2)})
    result = migrations.migrate({"schema_version": 0}, target=3)
    assert seen == [0, 1, 2]
    assert result["schema_version"] == 3


def test_migration_must_return_object(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(migrations, "MIGRATIONS", {0: lambda document: None})
    with pytest.raises(migrations.MigrationError, match="did not return an object"):
        migrations.migrate({"schema_version": 0})
