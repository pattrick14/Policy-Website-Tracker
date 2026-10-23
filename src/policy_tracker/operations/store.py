"""On-disk ``tracker-data`` layout, candidate staging, and compare-and-swap promotion.

See ``docs/DATA_LAYOUT.md`` for the file formats and the promotion protocol.
Every write goes through ``_atomic_write`` (temporary file, fsync, ``os.replace``).
The store never receives or writes recipient addresses.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from policy_tracker.core import serialize
from policy_tracker.core.migrations import migrate
from policy_tracker.core.models import (
    SCHEMA_VERSION,
    CandidateState,
    CatalogState,
    Event,
    FeedRun,
)

LAYOUT_VERSION = 1
CATALOG_PARTS = ("matters", "documents", "appearances", "versions", "feeds")
TOP_LEVEL_DIRS = ("catalog", "events", "runs", "health", "outbox", "digests", "candidates")
ARTIFACT_DIRS = ("digests",)  # immutable per-run renderer output roots
ROLLING_FILES = ("open_deadlines.csv",)  # current views at the data root, promoted with state
DEADLINE_INDEX = "deadlines.json"  # catalog/deadlines.json, promoted with the catalog
RUN_ID_PATTERN = re.compile(r"^(?P<year>\d{4})(?P<month>\d{2})\d{2}T\d{6}Z-[0-9a-f]{8}$")


class StoreError(RuntimeError):
    """The data directory is missing, malformed, or would be overwritten."""


class PromotionConflict(StoreError):
    """The catalog advanced since the candidate's base state (compare-and-swap failed)."""


def _atomic_write(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    _fsync_dir(path.parent)


def _fsync_dir(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_json(path: Path, document: Any) -> None:
    _atomic_write(path, serialize.dumps(document))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        document = serialize.loads(path.read_bytes())
    except FileNotFoundError:
        raise StoreError(f"missing data file {path}") from None
    except ValueError as exc:
        raise StoreError(f"malformed JSON in {path}: {exc}") from None
    if not isinstance(document, dict):
        raise StoreError(f"{path} must contain a JSON object")
    return document


def run_month(run_id: str) -> tuple[str, str]:
    """``(YYYY, MM)`` of a run id such as ``20261026T023000Z-1a2b3c4d``."""

    match = RUN_ID_PATTERN.match(run_id)
    if match is None:
        raise StoreError(f"run id {run_id!r} does not match YYYYMMDDTHHMMSSZ-<8 hex>")
    return match["year"], match["month"]


def catalog_documents(state: CatalogState) -> dict[str, dict[str, Any]]:
    """Split a state into its ``catalog/<part>.json`` documents."""

    encoded = serialize.encode_document(state)
    digest = serialize.state_hash(state)
    return {
        part: {
            "schema_version": SCHEMA_VERSION,
            "kind": f"catalog_{part}",
            "state_hash": digest,
            "records": encoded[part],
        }
        for part in CATALOG_PARTS
    }


def _assemble_catalog(directory: Path) -> tuple[dict[str, Any], str]:
    """Read split catalog files back into one ``catalog_state`` document."""

    versions: set[int] = set()
    hashes: set[str] = set()
    assembled: dict[str, Any] = {"kind": "catalog_state"}
    for part in CATALOG_PARTS:
        document = _read_json(directory / f"{part}.json")
        if document.get("kind") != f"catalog_{part}":
            raise StoreError(f"{directory / part}.json has kind {document.get('kind')!r}")
        versions.add(document.get("schema_version"))  # type: ignore[arg-type]
        hashes.add(document.get("state_hash"))  # type: ignore[arg-type]
        assembled[part] = document.get("records", {})
    if len(versions) != 1 or len(hashes) != 1:
        raise StoreError(
            f"catalog files in {directory} disagree on schema_version/state_hash "
            "(torn write?); restore the last committed tracker-data revision"
        )
    assembled["schema_version"] = versions.pop()
    return assembled, hashes.pop()


def _decode_catalog(directory: Path) -> CatalogState:
    assembled, stored_hash = _assemble_catalog(directory)
    was_current = assembled["schema_version"] == SCHEMA_VERSION
    try:
        state = serialize.decode_document(CatalogState, assembled)
    except ValueError as exc:
        raise StoreError(f"cannot load catalog from {directory}: {exc}") from exc
    if was_current and serialize.state_hash(state) != stored_hash:
        raise StoreError(f"catalog in {directory} does not match its recorded state_hash")
    return state


def _feed_health(feed_run: FeedRun, state: CatalogState) -> dict[str, Any]:
    feed_state = state.feeds.get(feed_run.feed_id)
    return {
        "run_id": feed_run.run_id,
        "finished_at": feed_run.finished_at,
        "fetch_status": feed_run.fetch_status.value,
        "parse_status": feed_run.parse_status.value,
        "enumeration_status": feed_run.enumeration_status.value,
        "accepted": feed_run.accepted,
        "item_count": feed_run.item_count,
        "previous_item_count": feed_run.previous_item_count,
        "quarantine_reasons": list(feed_run.quarantine_reasons),
        "diagnostics": list(feed_run.diagnostics),
        "baseline_status": feed_state.baseline_status.value if feed_state else None,
        "consecutive_failures": feed_state.consecutive_failures if feed_state else None,
    }


class DataStore:
    """A ``tracker-data`` checkout (or any directory with that layout)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # -- paths -------------------------------------------------------------------------
    @property
    def schema_path(self) -> Path:
        return self.root / "schema.json"

    @property
    def catalog_dir(self) -> Path:
        return self.root / "catalog"

    @property
    def health_path(self) -> Path:
        return self.root / "health" / "feeds.json"

    def run_path(self, run_id: str) -> Path:
        run_month(run_id)
        return self.root / "runs" / f"{run_id}.json"

    def events_path(self, run_id: str) -> Path:
        year, month = run_month(run_id)
        return self.root / "events" / year / month / f"{run_id}.json"

    def candidate_dir(self, run_id: str) -> Path:
        run_month(run_id)
        return self.root / "candidates" / run_id

    # -- lifecycle ---------------------------------------------------------------------
    def is_initialized(self) -> bool:
        return self.schema_path.is_file()

    def initialize(self) -> None:
        """Create an empty layout; refuse if the directory already has any content."""

        if self.root.exists():
            if not self.root.is_dir():
                raise StoreError(f"{self.root} exists and is not a directory")
            if any(entry.name != ".git" for entry in self.root.iterdir()):
                raise StoreError(f"refusing to initialize non-empty directory {self.root}")
        self.root.mkdir(parents=True, exist_ok=True)
        for name in TOP_LEVEL_DIRS:
            (self.root / name).mkdir(exist_ok=True)
        for name in ("events", "runs", "health", "outbox", "digests", "candidates"):
            _atomic_write(self.root / name / ".gitkeep", "")
        self._write_catalog(self.catalog_dir, CatalogState())
        self._write_schema()

    def _write_schema(self) -> None:
        _write_json(
            self.schema_path,
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "tracker_data",
                "layout_version": LAYOUT_VERSION,
                "catalog_parts": list(CATALOG_PARTS),
            },
        )

    def _require_initialized(self) -> None:
        if not self.is_initialized():
            raise StoreError(f"{self.root} is not an initialized tracker-data directory")
        schema = migrate(_read_json(self.schema_path))
        if schema.get("kind") != "tracker_data":
            raise StoreError(f"{self.schema_path} is not a tracker-data manifest")
        if schema.get("layout_version") != LAYOUT_VERSION:
            raise StoreError(f"unsupported layout_version {schema.get('layout_version')!r}")

    # -- catalog -----------------------------------------------------------------------
    def load_state(self) -> CatalogState:
        """Load (and migrate in memory) the promoted catalog."""

        self._require_initialized()
        return _decode_catalog(self.catalog_dir)

    def state_hash(self) -> str:
        return serialize.state_hash(self.load_state())

    def _write_catalog(self, directory: Path, state: CatalogState) -> None:
        for part, document in catalog_documents(state).items():
            _write_json(directory / f"{part}.json", document)

    # -- candidates and runs -----------------------------------------------------------
    def write_run(
        self,
        candidate: CandidateState,
        *,
        context: Mapping[str, Any] | None = None,
        status: str = "recorded",
        rebaselines: Sequence[Mapping[str, str]] = (),
    ) -> None:
        """Record a run's events, feed runs, manifest, and health without staging state.

        ``rebaselines`` holds ``{feed_id, reason, at}`` audit records; the frozen
        ``FeedState`` has no field for the reason, so the manifest and health keep it.
        """

        self._require_initialized()
        run_path = self.run_path(candidate.run_id)
        if run_path.exists():
            raise StoreError(f"run {candidate.run_id} is already recorded")
        seen: set[str] = set()
        for event in candidate.events:
            if event.run_id != candidate.run_id:
                raise StoreError(f"event {event.event_id} belongs to run {event.run_id}")
            if event.event_id in seen:  # archive consumers key on (run_id, event_id)
                raise StoreError(f"duplicate event id {event.event_id} in run {candidate.run_id}")
            seen.add(event.event_id)
        if candidate.events:
            _write_json(
                self.events_path(candidate.run_id),
                {
                    "schema_version": SCHEMA_VERSION,
                    "kind": "run_events",
                    "run_id": candidate.run_id,
                    "events": [serialize.to_data(event) for event in candidate.events],
                },
            )
        self._write_health(candidate, rebaselines)
        manifest = self._manifest(candidate, context=context, status=status)
        manifest["rebaselines"] = [dict(item) for item in rebaselines]
        _write_json(run_path, manifest)

    def write_candidate(
        self,
        candidate: CandidateState,
        *,
        context: Mapping[str, Any] | None = None,
        rebaselines: Sequence[Mapping[str, str]] = (),
    ) -> None:
        """Record the run and stage its candidate catalog under ``candidates/<run_id>/``."""

        self._require_initialized()
        if self.run_path(candidate.run_id).exists():
            raise StoreError(f"run {candidate.run_id} is already recorded")
        directory = self.candidate_dir(candidate.run_id)
        self._write_catalog(directory / "catalog", candidate.state)
        _write_json(
            directory / "candidate.json",
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "candidate_header",
                "run_id": candidate.run_id,
                "base_state_hash": candidate.base_state_hash,
                "state_hash": serialize.state_hash(candidate.state),
            },
        )
        self.write_run(candidate, context=context, status="candidate", rebaselines=rebaselines)

    def promote(self, candidate: CandidateState) -> str:
        """Compare-and-swap the catalog to ``candidate.state``; return the new state hash."""

        self._require_initialized()
        run_path = self.run_path(candidate.run_id)
        if not run_path.exists():
            raise StoreError(f"run {candidate.run_id} must be recorded before promotion")
        manifest = _read_json(run_path)
        if manifest.get("status") == "promoted":
            raise StoreError(f"run {candidate.run_id} is already promoted")
        current = self.state_hash()
        if current != candidate.base_state_hash:
            raise PromotionConflict(
                f"catalog state {current[:12]} is not candidate base "
                f"{candidate.base_state_hash[:12]}; refusing to overwrite newer state"
            )
        new_hash = serialize.state_hash(candidate.state)
        staged = self.candidate_dir(candidate.run_id)
        self._write_catalog(self.catalog_dir, candidate.state)
        index = staged / "catalog" / DEADLINE_INDEX
        if index.is_file():
            _atomic_write_bytes(self.catalog_dir / DEADLINE_INDEX, index.read_bytes())
        for name in ROLLING_FILES:
            rolling = staged / "rolling" / name
            if rolling.is_file():
                _atomic_write_bytes(self.root / name, rolling.read_bytes())
        self._write_schema()
        manifest["status"] = "promoted"
        manifest["promoted_state_hash"] = new_hash
        _write_json(run_path, manifest)
        shutil.rmtree(self.candidate_dir(candidate.run_id), ignore_errors=True)
        return new_hash

    def load_candidate(self, run_id: str) -> CandidateState:
        """Rebuild a staged, not yet promoted candidate (Step 7 retries use this)."""

        directory = self.candidate_dir(run_id)
        header = migrate(_read_json(directory / "candidate.json"))
        state = _decode_catalog(directory / "catalog")
        if serialize.state_hash(state) != header["state_hash"] and (
            header["schema_version"] == SCHEMA_VERSION
        ):
            raise StoreError(f"candidate {run_id} catalog does not match its header")
        return CandidateState(
            run_id=run_id,
            base_state_hash=header["base_state_hash"],
            state=state,
            events=self.load_events(run_id),
            feed_runs=self.load_feed_runs(run_id),
        )

    def _manifest(
        self, candidate: CandidateState, *, context: Mapping[str, Any] | None, status: str
    ) -> dict[str, Any]:
        runs = candidate.feed_runs
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "run_manifest",
            "run_id": candidate.run_id,
            "status": status,
            "config_hash": runs[0].config_hash if runs else None,
            "code_revision": runs[0].code_revision if runs else None,
            "base_state_hash": candidate.base_state_hash,
            "candidate_state_hash": serialize.state_hash(candidate.state),
            "event_count": len(candidate.events),
            "events_path": (
                self.events_path(candidate.run_id).relative_to(self.root).as_posix()
                if candidate.events
                else None
            ),
            "feed_runs": [serialize.to_data(run) for run in runs],
            "context": serialize.to_data(dict(context or {})),
        }

    def _write_health(
        self, candidate: CandidateState, rebaselines: Sequence[Mapping[str, str]]
    ) -> None:
        feeds: dict[str, Any] = {}
        if self.health_path.exists():
            feeds = dict(migrate(_read_json(self.health_path)).get("feeds", {}))
        for feed_run in candidate.feed_runs:
            entry = _feed_health(feed_run, candidate.state)
            previous = feeds.get(feed_run.feed_id, {})
            if "last_rebaseline" in previous:
                entry["last_rebaseline"] = previous["last_rebaseline"]
            feeds[feed_run.feed_id] = entry
        for item in rebaselines:
            entry = dict(feeds.get(item["feed_id"], {}))
            entry["last_rebaseline"] = {
                "run_id": candidate.run_id,
                "reason": item["reason"],
                "at": item["at"],
            }
            feeds[item["feed_id"]] = entry
        _write_json(
            self.health_path,
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "feed_health",
                "run_id": candidate.run_id,
                "feeds": feeds,
            },
        )

    # -- readers -----------------------------------------------------------------------
    def load_run(self, run_id: str) -> dict[str, Any]:
        return migrate(_read_json(self.run_path(run_id)))

    def load_feed_runs(self, run_id: str) -> tuple[FeedRun, ...]:
        manifest = self.load_run(run_id)
        return tuple(serialize.from_data(FeedRun, item) for item in manifest["feed_runs"])

    def load_events(self, run_id: str) -> tuple[Event, ...]:
        path = self.events_path(run_id)
        if not path.exists():
            return ()
        document = migrate(_read_json(path))
        return tuple(serialize.from_data(Event, item) for item in document["events"])

    def run_ids(self) -> list[str]:
        directory = self.root / "runs"
        if not directory.is_dir():
            return []
        return sorted(p.stem for p in directory.glob("*.json") if RUN_ID_PATTERN.match(p.stem))

    def iter_events(self) -> Iterator[Event]:
        for run_id in self.run_ids():
            yield from self.load_events(run_id)

    # -- Step 7: delivery support ------------------------------------------------------
    def is_promoted(self, run_id: str) -> bool:
        return self.run_path(run_id).exists() and self.load_run(run_id).get("status") == "promoted"

    def last_promoted_run_at(self) -> str | None:
        """Digest ``generated_at`` (else ``started_at``) of the newest promoted run, if any."""

        for run_id in reversed(self.run_ids()):
            manifest = self.load_run(run_id)
            context = manifest.get("context") or {}
            if manifest.get("status") == "promoted" and context.get("mode") in ("run", "baseline"):
                return context.get("generated_at") or context.get("started_at")
        return None

    def artifact_path(self, relative: str) -> Path:
        """Resolve a renderer path such as ``digests/2026/10/<run_id>.md`` safely."""

        pure = PurePosixPath(relative)
        parts = pure.parts
        if (
            pure.is_absolute()
            or len(parts) < 2
            or any(part in ("", ".", "..") for part in parts)
            or parts[0] not in ARTIFACT_DIRS
        ):
            raise StoreError(f"refusing artifact path {relative!r} (must be under {ARTIFACT_DIRS})")
        return self.root.joinpath(*parts)

    def write_artifacts(self, files: Sequence[tuple[str, bytes]]) -> None:
        """Write per-run digest files; an existing file is never changed (digests are immutable)."""

        self._require_initialized()
        targets = [(self.artifact_path(path), path, content) for path, content in files]
        for target, path, content in targets:
            if target.exists() and target.read_bytes() != content:
                raise StoreError(f"refusing to overwrite immutable artifact {path}")
        for target, _path, content in targets:
            _atomic_write_bytes(target, content)

    def stage_rolling(self, run_id: str, files: Sequence[tuple[str, bytes]]) -> None:
        """Stage current-view files (``open_deadlines.csv``); ``promote`` publishes them."""

        for path, content in files:
            if path not in ROLLING_FILES:
                raise StoreError(f"{path!r} is not a known rolling file {ROLLING_FILES}")
            _atomic_write_bytes(self.candidate_dir(run_id) / "rolling" / path, content)

    def stage_deadline_index(self, run_id: str, document: Mapping[str, Any]) -> None:
        """Stage ``catalog/deadlines.json``; ``promote`` publishes it with the catalog."""

        _write_json(self.candidate_dir(run_id) / "catalog" / DEADLINE_INDEX, dict(document))

    def load_deadline_index(self) -> dict[str, Any] | None:
        """The promoted ``catalog/deadlines.json`` document, or ``None`` before the first."""

        path = self.catalog_dir / DEADLINE_INDEX
        return _read_json(path) if path.exists() else None

    def discard_candidate(self, run_id: str, *, reason: str, at: str) -> None:
        """Mark a staged, unpromoted run ``not_promoted`` and delete its candidate catalog."""

        manifest = self.load_run(run_id)
        if manifest.get("status") != "candidate":
            raise StoreError(f"run {run_id} is {manifest.get('status')!r}, not a staged candidate")
        manifest["status"] = "not_promoted"
        manifest["discarded"] = {"reason": reason, "at": at}
        _write_json(self.run_path(run_id), manifest)
        shutil.rmtree(self.candidate_dir(run_id), ignore_errors=True)
