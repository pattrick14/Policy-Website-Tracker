"""SMTP delivery with an injected fake SMTP client (no network)."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email import message_from_bytes
from email.policy import default as default_policy

import pytest

from policy_tracker.operations import smtp
from policy_tracker.operations.smtp import SendResult, SmtpConfigError, SmtpSettings
from policy_tracker.reporting.types import RenderedEmail, RenderedFile

PASSWORD = "s3cret-Pa55word"
USERNAME = "tracker-user@mail.example.org"
RECIPIENTS = ("alice@example.org", "bob@example.net")
MESSAGE_ID = "<20261026T023000Z-1a2b3c4d.digest.policy-tracker@example.org>"
ENV = {
    "SMTP_HOST": "smtp.example.org",
    "SMTP_PORT": "587",
    "SMTP_SECURITY": "starttls",
    "SMTP_USERNAME": USERNAME,
    "SMTP_PASSWORD": PASSWORD,
    "SMTP_FROM": "Policy Tracker <tracker@example.org>",
    "SMTP_TO": ", ".join(RECIPIENTS),
}


def _email() -> RenderedEmail:
    csv = RenderedFile("digests/2026/10/run.csv", b"a,b\n1,2\n", "text/csv")
    return RenderedEmail("Weekly digest", "plain body", "<p>html body</p>", (csv,), 100)


class FakeSMTP:
    """Records the conversation; ``fail`` names a step that raises."""

    def __init__(self, fail: str | None = None, refused: dict | None = None) -> None:
        self.fail = fail
        self.refused = refused or {}
        self.steps: list[str] = []
        self.sent: list[tuple[bytes, str, list[str]]] = []
        self.context: ssl.SSLContext | None = None

    def factory(self, settings: SmtpSettings, context: ssl.SSLContext) -> FakeSMTP:
        self.context = context
        if self.fail == "connect":
            raise ConnectionRefusedError("connection refused")
        self.steps.append(f"connect:{settings.security}")
        return self

    def ehlo(self, name: str = "") -> None:
        self.steps.append("ehlo")

    def starttls(self, *, context: ssl.SSLContext) -> None:
        if self.fail == "starttls":
            raise smtplib.SMTPNotSupportedError("STARTTLS extension not supported by server.")
        assert context.verify_mode is ssl.CERT_REQUIRED and context.check_hostname
        self.steps.append("starttls")

    def login(self, user: str, password: str) -> None:
        if self.fail == "login":
            raise smtplib.SMTPAuthenticationError(
                535, f"5.7.8 bad credentials for {user} / {password}".encode()
            )
        self.steps.append("login")

    def send_message(self, msg, from_addr=None, to_addrs=None):
        self.steps.append("send")
        if self.fail == "timeout":
            raise TimeoutError("timed out")
        if self.fail == "disconnect":
            raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
        if self.fail == "data":
            raise smtplib.SMTPDataError(554, b"rejected")
        if self.fail == "all_refused":
            raise smtplib.SMTPRecipientsRefused({r: (550, b"no such user") for r in to_addrs})
        self.sent.append((msg.as_bytes(), from_addr, list(to_addrs)))
        return self.refused

    def quit(self) -> None:
        self.steps.append("quit")

    def close(self) -> None:
        self.steps.append("close")


def _send(fake: FakeSMTP, env: dict | None = None) -> SendResult:
    settings = smtp.settings_from_env(env or ENV)
    return smtp.send(
        _email(),
        message_id=MESSAGE_ID,
        settings=settings,
        recipients=RECIPIENTS,
        smtp_factory=fake.factory,
    )


def test_success_sends_multipart_with_fixed_message_id() -> None:
    fake = FakeSMTP()
    result = _send(fake)
    assert result.delivered_to_all(2) and result.accepted == RECIPIENTS
    assert fake.steps == ["connect:starttls", "ehlo", "starttls", "ehlo", "login", "send", "quit"]
    raw, sender, to = fake.sent[0]
    assert to == list(RECIPIENTS) and sender == ENV["SMTP_FROM"]
    msg = message_from_bytes(raw, policy=default_policy)
    assert msg["Message-ID"] == MESSAGE_ID
    assert msg.get_content_type() == "multipart/mixed"
    body, attachment = msg.iter_parts()
    assert body.get_content_type() == "multipart/alternative"
    assert [p.get_content_type() for p in body.iter_parts()] == ["text/plain", "text/html"]
    assert attachment.get_filename() == "run.csv"
    for address in RECIPIENTS:  # envelope only; never in headers
        assert address.encode() not in raw


def test_ssl_mode_skips_starttls() -> None:
    fake = FakeSMTP()
    assert _send(fake, {**ENV, "SMTP_SECURITY": "ssl", "SMTP_PORT": "465"}).delivered_to_all(2)
    assert "starttls" not in fake.steps and fake.steps[0] == "connect:ssl"


def test_partial_acceptance_is_not_delivered() -> None:
    result = _send(FakeSMTP(refused={"bob@example.net": (550, b"mailbox unavailable")}))
    assert result.accepted == ("alice@example.org",)
    assert result.refused == {"bob@example.net": "550"}
    assert not result.delivered_to_all(2) and not result.ambiguous


def test_rejection_of_every_recipient() -> None:
    result = _send(FakeSMTP(fail="all_refused"))
    assert result.accepted == () and len(result.refused) == 2 and not result.ambiguous


def test_data_rejection_is_definite() -> None:
    result = _send(FakeSMTP(fail="data"))
    assert not result.ambiguous and result.error == "data_refused (554)"


@pytest.mark.parametrize("fail", ["timeout", "disconnect"])
def test_failure_during_transfer_is_ambiguous(fail: str) -> None:
    result = _send(FakeSMTP(fail=fail))
    assert result.ambiguous and not result.delivered_to_all(2)


def test_failure_before_transfer_is_definite() -> None:
    result = _send(FakeSMTP(fail="connect"))
    assert not result.ambiguous and result.accepted == () and result.error.startswith("connection")


def test_missing_starttls_never_falls_back_to_plaintext() -> None:
    fake = FakeSMTP(fail="starttls")
    result = _send(fake)
    assert result.error == "starttls_unsupported" and "send" not in fake.steps
    assert "login" not in fake.steps


@pytest.mark.parametrize("security", ["none", "plain", "plaintext", "tls-optional", ""])
def test_plaintext_or_unknown_security_is_refused(security: str) -> None:
    with pytest.raises(SmtpConfigError):
        smtp.settings_from_env({**ENV, "SMTP_SECURITY": security})


def test_missing_configuration_blocks_with_variable_names_only() -> None:
    env = {k: v for k, v in ENV.items() if k not in ("SMTP_HOST", "SMTP_TO")}
    with pytest.raises(SmtpConfigError) as info:
        smtp.settings_from_env(env)
    assert "SMTP_HOST" in str(info.value) and "SMTP_TO" in str(info.value)
    with pytest.raises(SmtpConfigError):
        smtp.recipients_from_env(env)
    with pytest.raises(SmtpConfigError):
        smtp.settings_from_env({**ENV, "SMTP_PASSWORD": ""})  # username without password


def test_recipients_parse_comma_list() -> None:
    assert smtp.recipients_from_env(ENV) == RECIPIENTS


def test_tls_context_verifies_certificates() -> None:
    context = smtp.tls_context()
    assert context.verify_mode is ssl.CERT_REQUIRED and context.check_hostname


def test_no_secret_or_recipient_in_logs_or_errors(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    settings = smtp.settings_from_env(ENV)
    assert PASSWORD not in repr(settings) and USERNAME not in repr(settings)
    results = [_send(FakeSMTP(fail=fail)) for fail in ("login", "timeout", "all_refused", None)]
    assert results[0].error == "authentication_failed (535)"
    text = caplog.text + " ".join(r.summary() for r in results)
    assert "smtp delivery" in caplog.text
    for secret in (PASSWORD, USERNAME, *RECIPIENTS):
        assert secret not in text
