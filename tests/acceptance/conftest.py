"""Fixtures for the Step 8 offline acceptance drills (see docs/ACCEPTANCE.md)."""

from __future__ import annotations

import copy
import functools
import sys
from pathlib import Path
from typing import Any

import pytest

from policy_tracker.core import config as config_module
from policy_tracker.core import serialize

sys.path.insert(0, str(Path(__file__).resolve().parent))

from acceptance_harness import Harness, World, make_harness


@pytest.fixture(autouse=True)
def _offline_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ambient SMTP settings, artifact URLs, or caps leak into a drill."""

    for name in (
        "SMTP_HOST", "SMTP_PORT", "SMTP_SECURITY", "SMTP_USERNAME", "SMTP_PASSWORD",
        "SMTP_FROM", "SMTP_TO", "TRACKER_MAX_DOCUMENTS", "TRACKER_ARTIFACT_BASE_URL",
        "GITHUB_REPOSITORY", "GITHUB_SHA",
    ):  # fmt: skip
        monkeypatch.delenv(name, raising=False)


_REAL_LOAD_CONFIG = config_module.load_config
_CONFIG_CACHE: dict[tuple[str, int, int], Any] = {}


def _cached_load_config(path: Path) -> Any:
    """``load_config`` memoized on (path, mtime, size): parsing the YAML is ~0.5 s per run.

    The parsed, validated document is the real one; each caller gets a deep copy.
    """

    resolved = Path(path).resolve()
    stat = resolved.stat()
    key = (str(resolved), stat.st_mtime_ns, stat.st_size)
    if key not in _CONFIG_CACHE:
        _CONFIG_CACHE[key] = _REAL_LOAD_CONFIG(resolved)
    return copy.deepcopy(_CONFIG_CACHE[key])


@pytest.fixture(autouse=True)
def _fast_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module, "load_config", _cached_load_config)
    # typing.get_type_hints per decoded record is about half of every catalog load;
    # the hints of a class never change, so memoize them (behavior is identical).
    hints = getattr(serialize, "_hints", None)
    if hints is not None and not hasattr(hints, "cache_info"):
        monkeypatch.setattr(serialize, "_hints", functools.cache(hints))


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    """The full replayed world (36 feeds), enrichment capped at zero documents."""

    return make_harness(tmp_path)


@pytest.fixture
def core(tmp_path: Path) -> Harness:
    """A smaller world (``CORE_FEEDS``, all three sites) for the multi-run drills."""

    return make_harness(tmp_path, World.core())
