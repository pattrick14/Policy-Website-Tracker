"""Immutable pending-delivery bundles in ``tracker-data/outbox/<delivery_id>/`` (D-011).

A bundle is written once, committed and pushed before SMTP is contacted, and then
re-sent unchanged until every recipient accepts it. Only ``delivery.json``
(status and attempt count) and ``attempts.json`` (an audit log of counts) change
after creation; ``email.json`` and ``attachments/`` are immutable and carry
SHA-256 digests that are checked on every load.

Recipient addresses are never written. The bundle stores the SHA-256 of the
normalized recipient set and its size; at send time the addresses come from the
``SMTP_TO`` secret and must hash to the stored digest or the send is refused.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
import shutil
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from email.utils import getaddresses
from pathlib import Path, PurePosixPath
from typing import Any

from policy_tracker.core import serialize
from policy_tracker.core.migrations import migrate
from policy_tracker.core.models import SCHEMA_VERSION, DeliveryStatus, PendingDelivery
from policy_tracker.operations.store import (
    StoreError,
    _atomic_write,
    _atomic_write_bytes,
    _read_json,
    _write_json,
    run_month,
)
from policy_tracker.reporting.types import RenderedEmail, RenderedFile

OUTBOX_DIR = "outbox"
DEFAULT_MESSAGE_ID_DOMAIN = "policy-tracker.invalid"
_DOMAIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class OutboxError(StoreError):
    """An outbox bundle is missing, malformed, tampered with, or would be overwritten."""


class RecipientMismatch(OutboxError):
    """The configured recipients do not hash to the bundle's stored recipient set."""


def normalize_recipients(addresses: Iterable[str]) -> tuple[str, ...]:
    """Sorted, de-duplicated, lower-cased bare addresses; rejects anything without ``@``."""

    out: set[str] = set()
    for raw in addresses:
        for _name, address in getaddresses([raw]):
            address = address.strip().lower()
            if not address:
                continue
            local, _, domain = address.rpartition("@")
            if not local or not domain or any(ch.isspace() for ch in address):
                raise OutboxError("a recipient address is malformed (value not shown)")
            out.add(address)
    if not out:
        raise OutboxError("the recipient set is empty")
    return tuple(sorted(out))


def recipient_set_sha256(addresses: Iterable[str]) -> str:
    """SHA-256 of the normalized recipient set, one address per line."""

    return hashlib.sha256("\n".join(normalize_recipients(addresses)).encode("utf-8")).hexdigest()


def make_message_id(run_id: str, domain: str | None = None) -> str:
    """Deterministic RFC 5322 Message-ID for a run, e.g. ``<20261026T...@example.org>``."""

    run_month(run_id)  # validates the run id shape
    domain = (domain or DEFAULT_MESSAGE_ID_DOMAIN).strip().lower()
    if not _DOMAIN.match(domain) or ".." in domain:
        domain = DEFAULT_MESSAGE_ID_DOMAIN
    return f"<{run_id}.digest.policy-tracker@{domain}>"


def sender_domain(address: str | None) -> str | None:
    """The domain of ``SMTP_FROM`` (for the Message-ID), or ``None``."""

    if not address:
        return None
    pairs = getaddresses([address])
    bare = pairs[0][1] if pairs else ""
    _, _, domain = bare.rpartition("@")
    return domain or None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _content_sha256(email: RenderedEmail) -> str:
    parts = {
        "subject": email.subject,
        "text_body": email.text_body,
        "html_body": email.html_body,
        "attachments": [
            {"path": a.path, "media_type": a.media_type, "sha256": _sha256(a.content)}
            for a in email.attachments
        ],
    }
    return _sha256(serialize.dumps_bytes(parts))


@dataclass(frozen=True)
class Bundle:
    """A loaded, integrity-checked outbox entry."""

    header: PendingDelivery
    email: RenderedEmail
    content_sha256: str


@dataclass(frozen=True)
class Attempt:
    """One delivery attempt, as counts only (never addresses)."""

    at: str
    accepted: int
    refused: int
    ambiguous: bool
    error: str | None = None


class Outbox:
    """The ``outbox/`` directory of a tracker-data checkout."""

    def __init__(self, data_root: Path) -> None:
        self.root = Path(data_root) / OUTBOX_DIR

    def entry_dir(self, delivery_id: str) -> Path:
        run_month(delivery_id)  # delivery ids are run ids
        return self.root / delivery_id

    def ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(
            p.name for p in self.root.iterdir() if p.is_dir() and (p / "delivery.json").is_file()
        )

    def pending_ids(self) -> list[str]:
        """Pending deliveries, oldest first."""

        return [i for i in self.ids() if self.load_header(i).status is DeliveryStatus.PENDING]

    # -- create ------------------------------------------------------------------------
    def create(self, header: PendingDelivery, email: RenderedEmail) -> Bundle:
        """Write a new immutable bundle; refuse to overwrite an existing entry."""

        if header.status is not DeliveryStatus.PENDING or header.attempts != 0:
            raise OutboxError("a new bundle must be pending with zero attempts")
        if header.delivery_id != header.run_id:
            raise OutboxError("delivery_id must equal run_id")
        directory = self.entry_dir(header.delivery_id)
        if directory.exists():
            raise OutboxError(f"outbox entry {header.delivery_id} already exists")
        names: set[str] = set()
        attachments: list[dict[str, Any]] = []
        for attachment in email.attachments:
            name = PurePosixPath(attachment.path).name
            if not _SAFE_NAME.match(name) or name in names:
                raise OutboxError(f"unsafe or duplicate attachment name {name!r}")
            names.add(name)
            attachments.append(
                {
                    "path": attachment.path,
                    "file": f"attachments/{name}",
                    "media_type": attachment.media_type,
                    "sha256": _sha256(attachment.content),
                }
            )
        content_sha = _content_sha256(email)
        try:
            for attachment, meta in zip(email.attachments, attachments, strict=True):
                _atomic_write_bytes(directory / meta["file"], attachment.content)
            _write_json(
                directory / "email.json",
                {
                    "schema_version": SCHEMA_VERSION,
                    "kind": "outbox_email",
                    "delivery_id": header.delivery_id,
                    "subject": email.subject,
                    "text_body": email.text_body,
                    "html_body": email.html_body,
                    "size_bytes": email.size_bytes,
                    "attachments": attachments,
                    "content_sha256": content_sha,
                },
            )
            _write_json(directory / "attempts.json", _attempts_document(header.delivery_id, []))
            # delivery.json last: its presence is what makes the entry visible.
            _atomic_write(directory / "delivery.json", serialize.document_text(header))
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        return Bundle(header=header, email=email, content_sha256=content_sha)

    # -- read --------------------------------------------------------------------------
    def load_header(self, delivery_id: str) -> PendingDelivery:
        path = self.entry_dir(delivery_id) / "delivery.json"
        try:
            header = serialize.decode_document(PendingDelivery, _read_json(path))
        except ValueError as exc:
            raise OutboxError(f"malformed outbox header {path}: {exc}") from None
        if header.delivery_id != delivery_id:
            raise OutboxError(f"{path} names delivery {header.delivery_id}")
        return header

    def load(self, delivery_id: str) -> Bundle:
        """Load a bundle and verify every stored digest."""

        header = self.load_header(delivery_id)
        directory = self.entry_dir(delivery_id)
        document = migrate(_read_json(directory / "email.json"))
        if document.get("kind") != "outbox_email" or document.get("delivery_id") != delivery_id:
            raise OutboxError(f"{directory / 'email.json'} is not this delivery's email")
        attachments: list[RenderedFile] = []
        for meta in document["attachments"]:
            file_path = directory / meta["file"]
            if PurePosixPath(meta["file"]).parent != PurePosixPath("attachments"):
                raise OutboxError(f"attachment path {meta['file']!r} escapes the bundle")
            try:
                content = file_path.read_bytes()
            except FileNotFoundError:
                raise OutboxError(f"missing attachment {file_path}") from None
            if _sha256(content) != meta["sha256"]:
                raise OutboxError(f"attachment {file_path} does not match its digest")
            attachments.append(
                RenderedFile(path=meta["path"], content=content, media_type=meta["media_type"])
            )
        email = RenderedEmail(
            subject=document["subject"],
            text_body=document["text_body"],
            html_body=document["html_body"],
            attachments=tuple(attachments),
            size_bytes=document["size_bytes"],
        )
        if _content_sha256(email) != document["content_sha256"]:
            raise OutboxError(f"outbox entry {delivery_id} was modified after creation")
        return Bundle(header=header, email=email, content_sha256=document["content_sha256"])

    def load_attempts(self, delivery_id: str) -> list[dict[str, Any]]:
        path = self.entry_dir(delivery_id) / "attempts.json"
        if not path.exists():
            return []
        return list(migrate(_read_json(path)).get("attempts", []))

    # -- state transitions -------------------------------------------------------------
    def verify_recipients(
        self, header: PendingDelivery, recipients: Sequence[str]
    ) -> tuple[str, ...]:
        """Return the normalized recipients, or raise if they are not the stored set."""

        normalized = normalize_recipients(recipients)
        if (
            recipient_set_sha256(normalized) != header.recipient_set_sha256
            or len(normalized) != header.recipient_count
        ):
            raise RecipientMismatch(
                f"configured recipients do not match delivery {header.delivery_id}'s stored "
                f"recipient set ({header.recipient_count} recipient(s)); refusing to send. "
                "Restore the original SMTP_TO secret or abandon the delivery."
            )
        return normalized

    def record_attempt(
        self, delivery_id: str, attempt: Attempt, *, delivered: bool
    ) -> PendingDelivery:
        """Increment ``attempts``; set DELIVERED only when every recipient accepted."""

        header = self.load_header(delivery_id)
        if header.status is DeliveryStatus.DELIVERED:
            raise OutboxError(f"delivery {delivery_id} is already delivered")
        updated = dataclasses.replace(
            header,
            attempts=header.attempts + 1,
            status=DeliveryStatus.DELIVERED if delivered else DeliveryStatus.PENDING,
        )
        directory = self.entry_dir(delivery_id)
        log = [*self.load_attempts(delivery_id), serialize.to_data(attempt)]
        _write_json(directory / "attempts.json", _attempts_document(delivery_id, log))
        _atomic_write(directory / "delivery.json", serialize.document_text(updated))
        return updated

    def abandon(self, delivery_id: str, *, reason: str, at: str) -> None:
        """Move a pending entry to ``outbox/abandoned/<id>/`` with the operator's reason."""

        if not reason.strip():
            raise OutboxError("abandoning a delivery needs a non-empty reason")
        header = self.load_header(delivery_id)
        if header.status is not DeliveryStatus.PENDING:
            raise OutboxError(f"delivery {delivery_id} is not pending")
        source = self.entry_dir(delivery_id)
        target = self.root / "abandoned" / delivery_id
        if target.exists():
            raise OutboxError(f"{target} already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        _write_json(
            target / "abandoned.json",
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "delivery_abandoned",
                "delivery_id": delivery_id,
                "reason": reason,
                "at": at,
            },
        )


def _attempts_document(delivery_id: str, attempts: list[Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "delivery_attempts",
        "delivery_id": delivery_id,
        "attempts": attempts,
    }
