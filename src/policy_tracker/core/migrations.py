"""Explicit, versioned schema migrations for serialized documents.

``MIGRATIONS[n]`` upgrades a document from schema version ``n`` to ``n + 1``.
``migrate`` applies the chain up to ``SCHEMA_VERSION``. Migrations receive the
whole top-level document (including ``kind``) so they can dispatch on it, must
not mutate their input, and never guess: unknown or newer versions fail loudly.

Schema version 1 is the first frozen version, so the registry is empty. To add
version 2: bump ``models.SCHEMA_VERSION``, register ``MIGRATIONS[1]``, add a
fixture of a v1 document plus a test, and record the change in
``docs/DATA_LAYOUT.md``.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from policy_tracker.core.models import SCHEMA_VERSION

Migration = Callable[[dict[str, Any]], dict[str, Any]]

MIGRATIONS: dict[int, Migration] = {}


class MigrationError(ValueError):
    """A document cannot be upgraded to the current schema version."""


def document_version(document: dict[str, Any]) -> int:
    if "schema_version" not in document:
        raise MigrationError("document has no schema_version; refusing to guess its version")
    version = document["schema_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise MigrationError(f"invalid schema_version {version!r}")
    return version


def migrate(document: dict[str, Any], *, target: int = SCHEMA_VERSION) -> dict[str, Any]:
    """Return a copy of ``document`` upgraded to ``target`` (default: current)."""

    version = document_version(document)
    if version > target:
        raise MigrationError(
            f"document schema_version {version} is newer than supported version {target}; "
            "upgrade the tracker code before reading this data"
        )
    current = copy.deepcopy(document)
    while version < target:
        step = MIGRATIONS.get(version)
        if step is None:
            raise MigrationError(f"no migration registered from schema_version {version}")
        current = step(copy.deepcopy(current))
        if not isinstance(current, dict):
            raise MigrationError(f"migration from {version} did not return an object")
        version += 1
        current["schema_version"] = version
    return current
