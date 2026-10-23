"""Deterministic JSON serialization for the Step 2 records.

``to_data`` turns any record from ``policy_tracker.core.models`` (or
``observations``) into plain JSON data; ``from_data`` rebuilds it from its type
hints. ``encode_document``/``decode_document`` add the top-level
``schema_version`` and ``kind`` envelope and run migrations on read. ``dumps``
is the only JSON writer: sorted keys, UTF-8, two-space indent, trailing newline.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import types
import typing
from collections.abc import Mapping
from enum import Enum
from typing import Any

from policy_tracker.core import models
from policy_tracker.core.migrations import migrate
from policy_tracker.core.models import SCHEMA_VERSION


class SerializationError(ValueError):
    """Raised when data does not match the record schema."""


# Stable document kinds; changing one is a schema change.
DOCUMENT_KINDS: dict[type, str] = {
    models.CatalogState: "catalog_state",
    models.CandidateState: "candidate_state",
    models.PendingDelivery: "pending_delivery",
    models.FeedRun: "feed_run",
    models.Event: "event",
    models.Matter: "matter",
    models.Document: "document",
    models.Appearance: "appearance",
    models.DocumentVersion: "document_version",
    models.FeedState: "feed_state",
}


def dumps(document: Any) -> str:
    """Canonical JSON text: sorted keys, non-ASCII kept, 2-space indent, newline."""

    return json.dumps(document, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def dumps_bytes(document: Any) -> bytes:
    return dumps(document).encode("utf-8")


def loads(text: str | bytes) -> Any:
    return json.loads(text)


def to_data(value: Any) -> Any:
    """Convert a record (recursively) into JSON-compatible data."""

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, float):
        raise SerializationError("floats are not part of the record schema")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_data(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise SerializationError(f"mapping keys must be strings, got {key!r}")
            out[key] = to_data(item)
        return out
    if isinstance(value, (tuple, list)):
        return [to_data(item) for item in value]
    raise SerializationError(f"cannot serialize {type(value).__name__}")


def from_data[T](cls: type[T], data: Any) -> T:
    """Rebuild a record of type ``cls`` from ``to_data`` output."""

    return _convert(cls, data, cls.__name__)


def _hints(cls: type) -> dict[str, Any]:
    return typing.get_type_hints(cls)


def _convert(tp: Any, data: Any, path: str) -> Any:
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if tp is Any:
        return data
    if origin in (typing.Union, types.UnionType):
        if data is None and type(None) in args:
            return None
        options = [arg for arg in args if arg is not type(None)]
        if len(options) != 1:
            raise SerializationError(f"{path}: unsupported union {tp!r}")
        return _convert(options[0], data, path)
    if tp is type(None):
        if data is not None:
            raise SerializationError(f"{path}: expected null")
        return None
    if origin is tuple:
        if not isinstance(data, list):
            raise SerializationError(f"{path}: expected a list")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_convert(args[0], item, f"{path}[{i}]") for i, item in enumerate(data))
        if len(args) != len(data):
            raise SerializationError(f"{path}: expected {len(args)} items")
        return tuple(_convert(a, item, f"{path}[{i}]") for i, (a, item) in enumerate(zip(args, data)))
    if origin in (Mapping, dict, typing.Mapping) or tp in (Mapping, dict):
        if not isinstance(data, dict):
            raise SerializationError(f"{path}: expected an object")
        value_type = args[1] if len(args) == 2 else Any
        return {key: _convert(value_type, item, f"{path}.{key}") for key, item in data.items()}
    if isinstance(tp, type) and issubclass(tp, Enum):
        try:
            return tp(data)
        except ValueError as exc:
            raise SerializationError(f"{path}: {exc}") from None
    if isinstance(tp, type) and dataclasses.is_dataclass(tp):
        return _convert_dataclass(tp, data, path)
    if tp is bool:
        if not isinstance(data, bool):
            raise SerializationError(f"{path}: expected a boolean")
        return data
    if tp is int:
        if isinstance(data, bool) or not isinstance(data, int):
            raise SerializationError(f"{path}: expected an integer")
        return data
    if tp is str:
        if not isinstance(data, str):
            raise SerializationError(f"{path}: expected a string")
        return data
    raise SerializationError(f"{path}: unsupported type {tp!r}")


def _convert_dataclass(cls: type, data: Any, path: str) -> Any:
    if not isinstance(data, dict):
        raise SerializationError(f"{path}: expected an object for {cls.__name__}")
    hints = _hints(cls)
    fields = {f.name: f for f in dataclasses.fields(cls)}
    unknown = sorted(set(data) - set(fields))
    if unknown:
        raise SerializationError(f"{path}: unknown fields {unknown}")
    kwargs: dict[str, Any] = {}
    for name, f in fields.items():
        if name not in data:
            if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
                raise SerializationError(f"{path}: missing field {name!r}")
            continue
        kwargs[name] = _convert(hints[name], data[name], f"{path}.{name}")
    return cls(**kwargs)


def kind_of(cls: type) -> str:
    try:
        return DOCUMENT_KINDS[cls]
    except KeyError:
        raise SerializationError(f"{cls.__name__} is not a top-level document type") from None


def encode_document(record: Any) -> dict[str, Any]:
    """Top-level document: the record's fields plus ``schema_version`` and ``kind``."""

    data = to_data(record)
    data["schema_version"] = SCHEMA_VERSION  # CatalogState already carries it
    data["kind"] = kind_of(type(record))
    return data


def decode_document[T](cls: type[T], document: Mapping[str, Any]) -> T:
    """Migrate ``document`` to the current schema and rebuild a ``cls`` record."""

    upgraded = migrate(dict(document))
    expected = kind_of(cls)
    kind = upgraded.pop("kind", None)
    if kind != expected:
        raise SerializationError(f"expected a {expected!r} document, got {kind!r}")
    if not any(f.name == "schema_version" for f in dataclasses.fields(cls)):
        upgraded.pop("schema_version", None)
    return from_data(cls, upgraded)


def document_text(record: Any) -> str:
    return dumps(encode_document(record))


def parse_document[T](cls: type[T], text: str | bytes) -> T:
    document = loads(text)
    if not isinstance(document, dict):
        raise SerializationError("a document must be a JSON object")
    return decode_document(cls, document)


def state_hash(state: models.CatalogState) -> str:
    """SHA-256 of the canonical current-schema encoding of a catalog state."""

    return hashlib.sha256(dumps_bytes(encode_document(state))).hexdigest()
