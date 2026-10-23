"""SMTP delivery over verified TLS (D-003, D-011).

Only ``SMTP_SECURITY=starttls`` or ``ssl`` is accepted; plaintext is refused, and
certificates are verified with ``ssl.create_default_context()``. Missing or
invalid configuration raises ``SmtpConfigError`` (the run is blocked; there is no
insecure fallback). Log lines and error messages carry counts and error classes
only: never credentials, recipient addresses, or the server's authentication text.
"""

from __future__ import annotations

import contextlib
import logging
import os
import smtplib
import socket
import ssl
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, getaddresses
from pathlib import PurePosixPath
from typing import Protocol

from policy_tracker.reporting.types import RenderedEmail

log = logging.getLogger("policy_tracker.smtp")

SECURITY_MODES = ("starttls", "ssl")
DEFAULT_TIMEOUT_SECONDS = 120.0
REQUIRED_ENV = ("SMTP_HOST", "SMTP_PORT", "SMTP_SECURITY", "SMTP_FROM", "SMTP_TO")


class SmtpConfigError(RuntimeError):
    """SMTP delivery is blocked: configuration is missing or insecure."""


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    security: str
    sender: str
    username: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)
    timeout: float = DEFAULT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if self.security not in SECURITY_MODES:
            raise SmtpConfigError(
                f"SMTP_SECURITY must be one of {SECURITY_MODES}; plaintext SMTP is refused"
            )
        if not self.host or not self.sender:
            raise SmtpConfigError("SMTP_HOST and SMTP_FROM are required")
        if not 0 < self.port < 65536:
            raise SmtpConfigError("SMTP_PORT must be a TCP port number")
        if bool(self.username) != bool(self.password):
            raise SmtpConfigError("SMTP_USERNAME and SMTP_PASSWORD must be set together")

    def secrets(self) -> tuple[str, ...]:
        return tuple(s for s in (self.username, self.password) if s)


@dataclass(frozen=True)
class SendResult:
    """Outcome of one SMTP transaction. Addresses stay in memory; log ``summary()`` only."""

    accepted: tuple[str, ...]
    refused: Mapping[str, str]  # address -> SMTP reply code (no server text)
    ambiguous: bool  # the transaction may or may not have been accepted (e.g. timeout)
    error: str | None = None  # masked error class, e.g. "authentication_failed"

    def delivered_to_all(self, expected: int) -> bool:
        return not self.ambiguous and not self.refused and len(self.accepted) == expected > 0

    def summary(self) -> str:
        text = f"accepted {len(self.accepted)}, refused {len(self.refused)}"
        if self.ambiguous:
            text += ", outcome ambiguous"
        if self.error:
            text += f", error {self.error}"
        return text


class SmtpClient(Protocol):
    def ehlo(self, name: str = "") -> object: ...
    def starttls(self, *, context: ssl.SSLContext) -> object: ...
    def login(self, user: str, password: str) -> object: ...
    def send_message(
        self, msg: EmailMessage, from_addr: str | None = None, to_addrs: Sequence[str] | None = None
    ) -> Mapping[str, tuple[int, bytes]]: ...
    def quit(self) -> object: ...
    def close(self) -> None: ...


# (settings, tls context) -> connected client; tests inject a fake.
SmtpFactory = Callable[[SmtpSettings, ssl.SSLContext], SmtpClient]


def _parse_recipients(raw: str) -> tuple[str, ...]:
    addresses = tuple(a.strip() for _, a in getaddresses([raw]) if a.strip())
    if not addresses or any("@" not in a for a in addresses):
        raise SmtpConfigError("SMTP_TO must be a comma-separated list of addresses")
    return addresses


def settings_from_env(env: Mapping[str, str] | None = None) -> SmtpSettings:
    """Build settings from ``SMTP_*``; raise ``SmtpConfigError`` naming missing variables."""

    env = os.environ if env is None else env
    missing = [name for name in REQUIRED_ENV if not env.get(name, "").strip()]
    if missing:
        raise SmtpConfigError(f"SMTP delivery blocked: missing {', '.join(missing)}")
    try:
        port = int(env["SMTP_PORT"].strip())
    except ValueError:
        raise SmtpConfigError("SMTP_PORT must be an integer") from None
    timeout = DEFAULT_TIMEOUT_SECONDS
    if env.get("SMTP_TIMEOUT", "").strip():
        try:
            timeout = float(env["SMTP_TIMEOUT"])
        except ValueError:
            raise SmtpConfigError("SMTP_TIMEOUT must be a number of seconds") from None
    return SmtpSettings(
        host=env["SMTP_HOST"].strip(),
        port=port,
        security=env["SMTP_SECURITY"].strip().lower(),
        sender=env["SMTP_FROM"].strip(),
        username=env.get("SMTP_USERNAME", "").strip() or None,
        password=env.get("SMTP_PASSWORD") or None,
        timeout=timeout,
    )


def recipients_from_env(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    env = os.environ if env is None else env
    raw = env.get("SMTP_TO", "")
    if not raw.strip():
        raise SmtpConfigError("SMTP delivery blocked: missing SMTP_TO")
    return _parse_recipients(raw)


def tls_context() -> ssl.SSLContext:
    """Verified TLS: hostname checks and CA verification on, TLS 1.2 minimum."""

    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if context.verify_mode is not ssl.CERT_REQUIRED or not context.check_hostname:
        raise SmtpConfigError("certificate verification is unavailable; refusing to send")
    return context


def default_factory(settings: SmtpSettings, context: ssl.SSLContext) -> SmtpClient:
    if settings.security == "ssl":
        return smtplib.SMTP_SSL(
            settings.host, settings.port, timeout=settings.timeout, context=context
        )
    return smtplib.SMTP(settings.host, settings.port, timeout=settings.timeout)


def build_message(
    email: RenderedEmail, *, message_id: str, sender: str, date: str | None = None
) -> EmailMessage:
    """multipart/alternative text + HTML, wrapped in multipart/mixed when attachments exist.

    Recipients go only in the SMTP envelope; the ``To`` header is undisclosed.
    """

    msg = EmailMessage()
    msg["Subject"] = email.subject
    msg["From"] = sender
    msg["To"] = "undisclosed-recipients:;"
    msg["Message-ID"] = message_id
    msg["Date"] = date or formatdate(usegmt=True)
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(email.text_body, subtype="plain", charset="utf-8")
    msg.add_alternative(email.html_body, subtype="html", charset="utf-8")
    for attachment in email.attachments:
        maintype, _, subtype = attachment.media_type.partition("/")
        msg.add_attachment(
            attachment.content,
            maintype=maintype or "application",
            subtype=(subtype or "octet-stream").split(";")[0].strip(),
            filename=PurePosixPath(attachment.path).name,
        )
    return msg


def _masked(exc: BaseException) -> str:
    """An error class with no server text, credentials, or addresses."""

    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return f"authentication_failed ({exc.smtp_code})"
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return "starttls_unsupported"
    if isinstance(exc, ssl.SSLCertVerificationError):
        return "certificate_verification_failed"
    if isinstance(exc, ssl.SSLError):
        return "tls_error"
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return f"sender_refused ({exc.smtp_code})"
    if isinstance(exc, smtplib.SMTPDataError):
        return f"data_refused ({exc.smtp_code})"
    if isinstance(exc, smtplib.SMTPResponseException):
        return f"smtp_error ({exc.smtp_code})"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return "server_disconnected"
    if isinstance(exc, OSError):
        return f"connection_error ({type(exc).__name__})"
    return type(exc).__name__


def send(
    email: RenderedEmail,
    *,
    message_id: str,
    settings: SmtpSettings,
    recipients: Sequence[str],
    smtp_factory: SmtpFactory | None = None,
) -> SendResult:
    """Send one message to ``recipients`` with a fixed Message-ID; never raises for SMTP errors.

    Failures before the message is handed over are definite (nothing accepted).
    A timeout or disconnect while the message is being transferred is ambiguous:
    the server may have accepted it, so the caller must keep the delivery pending.
    """

    if settings.security not in SECURITY_MODES:  # defensive: settings validate this too
        raise SmtpConfigError("plaintext SMTP is refused")
    recipients = tuple(recipients)
    if not recipients:
        raise SmtpConfigError("no recipients configured")
    message = build_message(email, message_id=message_id, sender=settings.sender)
    context = tls_context()
    factory = smtp_factory or default_factory
    client: SmtpClient | None = None
    transferring = False
    try:
        client = factory(settings, context)
        if settings.security == "starttls":
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        if settings.username and settings.password:
            client.login(settings.username, settings.password)
        transferring = True
        refused_raw = client.send_message(
            message, from_addr=settings.sender, to_addrs=list(recipients)
        )
        transferring = False
    except smtplib.SMTPRecipientsRefused as exc:
        result = SendResult(
            accepted=(),
            refused={addr: str(code) for addr, (code, _msg) in exc.recipients.items()},
            ambiguous=False,
            error="all_recipients_refused",
        )
    except smtplib.SMTPResponseException as exc:  # a definite reply code: nothing accepted
        result = SendResult(accepted=(), refused={}, ambiguous=False, error=_masked(exc))
    except smtplib.SMTPNotSupportedError as exc:  # e.g. no STARTTLS: never fall back
        result = SendResult(accepted=(), refused={}, ambiguous=False, error=_masked(exc))
    except OSError as exc:  # includes SMTPServerDisconnected, timeouts and TLS errors
        # Before the transfer this is a definite failure; during it, the outcome is unknown.
        result = SendResult(accepted=(), refused={}, ambiguous=transferring, error=_masked(exc))
    else:
        refused = {addr: str(code) for addr, (code, _msg) in dict(refused_raw or {}).items()}
        accepted = tuple(addr for addr in recipients if addr not in refused)
        result = SendResult(accepted=accepted, refused=refused, ambiguous=False)
    finally:
        if client is not None:
            try:
                client.quit()
            except Exception:  # noqa: BLE001 - the transaction outcome is already known
                with contextlib.suppress(Exception):
                    client.close()
    log.info("smtp delivery %s: %s", message_id, result.summary())
    return result
