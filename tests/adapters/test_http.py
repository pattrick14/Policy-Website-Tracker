"""HttpFetcher and ReplayFetcher tests. No real network: httpx.MockTransport only."""

from __future__ import annotations

import dataclasses
import ssl
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

from policy_tracker.adapters import http as http_module
from policy_tracker.adapters.base import Fetcher
from policy_tracker.adapters.http import (
    D020_FAILURE_CLASSES,
    DEFAULT_USER_AGENT,
    HttpFetcher,
    SiteFetcherPool,
    classify_error,
    is_public_address,
)
from policy_tracker.adapters.replay import ReplayEntry, ReplayFetcher
from policy_tracker.core.config import load_config, load_contracts
from policy_tracker.core.contracts import FetchPolicy

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"

APEX = "cercind.gov.in"
WWW = "www.cercind.gov.in"
CERC_HOSTS = frozenset({APEX, WWW})
CERC_POLICY = FetchPolicy(
    request_rate_per_host_per_second=1,
    request_timeout_seconds=120,
    max_redirects=5,
    max_document_bytes=1_000,
    rate_limit_shared_across_hosts=True,
    canonical_host=APEX,
    fallback_hosts=(WWW,),
    host_fallback_on=tuple(sorted(D020_FAILURE_CLASSES)),
    max_attempts_per_url=6,
    retry_backoff_seconds=(20, 40),
    abort_after_consecutive_failed_urls=3,
)
COAL_HOSTS = frozenset({"www.coal.nic.in", "coal.nic.in", "www.coal.gov.in", "coal.gov.in"})
COAL_POLICY = FetchPolicy(
    request_rate_per_host_per_second=0.25,
    request_timeout_seconds=30,
    max_redirects=5,
    max_document_bytes=1_000,
)

Handler = Callable[[httpx.Request], httpx.Response]


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 6))
        self.now += seconds


def public_resolver(host: str) -> list[str]:
    return ["93.184.215.14"]


class Recorder:
    """MockTransport handler that records requests and delegates to ``respond``."""

    def __init__(self, respond: Handler) -> None:
        self.respond = respond
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)

    @property
    def hosts(self) -> list[str]:
        return [request.url.host for request in self.requests]


def make(
    respond: Handler,
    policy: FetchPolicy = CERC_POLICY,
    hosts: frozenset[str] = CERC_HOSTS,
    resolver: Callable[[str], list[str]] = public_resolver,
) -> tuple[HttpFetcher, Recorder, FakeClock]:
    recorder = Recorder(respond)
    clock = FakeClock()
    fetcher = HttpFetcher(
        policy,
        hosts,
        transport=httpx.MockTransport(recorder),
        clock=clock,
        sleep=clock.sleep,
        resolver=resolver,
    )
    return fetcher, recorder, clock


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"<html>ok</html>")


def raiser(exc: Exception) -> Handler:
    def respond(request: httpx.Request) -> httpx.Response:
        raise exc

    return respond


class BrokenStream(httpx.SyncByteStream):
    def __init__(self, exc: Exception, chunks: tuple[bytes, ...] = (b"<html>",)) -> None:
        self.exc = exc
        self.chunks = chunks

    def __iter__(self) -> Iterator[bytes]:
        yield from self.chunks
        raise self.exc


class ChunkStream(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    def __iter__(self) -> Iterator[bytes]:
        yield from self.chunks


def test_satisfies_fetcher_protocol_and_sends_user_agent() -> None:
    http_fetcher, recorder, _ = make(ok)
    fetcher: Fetcher = http_fetcher  # structural check of the frozen protocol
    response = fetcher.get("https://cercind.gov.in/Draft_reg.html")
    assert response.ok and response.body == b"<html>ok</html>"
    assert response.served_by_host == APEX
    assert response.url == "https://cercind.gov.in/Draft_reg.html"
    assert response.headers["content-type"] == "text/html"
    assert recorder.requests[0].headers["user-agent"] == DEFAULT_USER_AGENT


# -- allowlist, DNS, and redirects ---------------------------------------------------


@pytest.mark.parametrize(
    ("url", "error"),
    [
        ("https://evil.example/x", "host_not_allowed"),
        ("https://cercind.gov.in.evil.example/x", "host_not_allowed"),
        ("ftp://cercind.gov.in/x", "invalid_url"),
        ("/relative/path", "invalid_url"),
    ],
)
def test_refuses_disallowed_or_invalid_urls_without_network(url: str, error: str) -> None:
    fetcher, recorder, _ = make(ok)
    response = fetcher.get(url)
    assert response.error == error and response.status_code == 0 and not response.ok
    assert recorder.requests == []


def test_host_resolving_to_private_address_is_refused() -> None:
    fetcher, recorder, _ = make(ok, resolver=lambda host: ["127.0.0.1"])
    assert fetcher.get("https://cercind.gov.in/").error == "private_address"
    assert recorder.requests == []


def test_any_private_address_among_several_is_refused() -> None:
    fetcher, _, _ = make(ok, resolver=lambda host: ["93.184.215.14", "10.1.2.3"])
    assert fetcher.get("https://cercind.gov.in/").error == "private_address"


def test_dns_failure_without_proxy_is_dns_error() -> None:
    def fail(host: str) -> list[str]:
        raise OSError("Name or service not known")

    fetcher, recorder, _ = make(ok, resolver=fail)
    assert fetcher.get("https://cercind.gov.in/").error == "dns_error"
    assert recorder.requests == []


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("93.184.215.14", True),
        ("2606:4700::1111", True),
        ("10.0.0.1", False),
        ("172.16.5.4", False),
        ("192.168.1.1", False),
        ("127.0.0.1", False),
        ("169.254.169.254", False),
        ("100.64.0.1", False),
        ("0.0.0.0", False),
        ("240.0.0.1", False),
        ("224.0.0.1", False),
        ("::1", False),
        ("fe80::1", False),
        ("fc00::1", False),
        ("::ffff:127.0.0.1", False),
        ("not-an-ip", False),
    ],
)
def test_is_public_address(address: str, public: bool) -> None:
    assert is_public_address(address) is public


def redirect_chain(length: int, final: Handler = ok) -> Handler:
    def respond(request: httpx.Request) -> httpx.Response:
        step = int(request.url.params.get("n", "0"))
        if step < length:
            return httpx.Response(302, headers={"Location": f"/hop?n={step + 1}"})
        return final(request)

    return respond


def test_follows_redirects_up_to_the_limit() -> None:
    fetcher, recorder, _ = make(redirect_chain(5))
    response = fetcher.get("https://cercind.gov.in/hop?n=0")
    assert response.ok and response.url == "https://cercind.gov.in/hop?n=5"
    assert len(recorder.requests) == 6


def test_redirect_limit_exceeded() -> None:
    fetcher, recorder, _ = make(redirect_chain(6))
    response = fetcher.get("https://cercind.gov.in/hop?n=0")
    assert response.error == "too_many_redirects"
    assert len(recorder.requests) == 6  # never asks for the 7th URL


def test_redirect_to_disallowed_host_is_refused() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(301, headers={"Location": "https://evil.example/steal"})

    fetcher, recorder, _ = make(respond)
    response = fetcher.get("https://cercind.gov.in/a")
    assert response.error == "host_not_allowed"
    assert response.url == "https://evil.example/steal"
    assert recorder.hosts == [APEX]


def test_redirect_to_allowed_host_with_private_address_is_refused() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://www.cercind.gov.in/a"})

    def resolver(host: str) -> list[str]:
        return ["192.168.0.10"] if host == WWW else ["93.184.215.14"]

    fetcher, recorder, _ = make(respond, resolver=resolver)
    assert fetcher.get("https://cercind.gov.in/a").error == "private_address"
    assert recorder.hosts == [APEX]


def test_redirect_to_other_allowed_host_records_served_by_host() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == APEX:
            return httpx.Response(301, headers={"Location": "https://www.cercind.gov.in/a"})
        return ok(request)

    fetcher, _, _ = make(respond)
    response = fetcher.get("https://cercind.gov.in/a")
    assert response.ok and response.served_by_host == WWW
    assert response.url == "https://www.cercind.gov.in/a"


def test_redirect_without_location_is_returned_as_is() -> None:
    fetcher, _, _ = make(lambda request: httpx.Response(302))
    response = fetcher.get("https://cercind.gov.in/a")
    assert response.status_code == 302 and response.error is None


# -- size cap ------------------------------------------------------------------------


def test_declared_content_length_over_cap_is_too_large() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 1_001)

    fetcher, _, _ = make(respond)
    response = fetcher.get("https://cercind.gov.in/big.pdf")
    assert response.error == "too_large" and response.body == b""


def test_streamed_body_over_cap_is_too_large() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=ChunkStream([b"x" * 600, b"x" * 600]))

    fetcher, _, _ = make(respond)
    assert fetcher.get("https://cercind.gov.in/big.pdf").error == "too_large"


def test_body_exactly_at_cap_is_accepted() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=ChunkStream([b"x" * 500, b"x" * 500]))

    fetcher, _, _ = make(respond)
    response = fetcher.get("https://cercind.gov.in/ok.pdf")
    assert response.ok and len(response.body) == 1_000


def test_size_error_does_not_trigger_fallback_or_breaker() -> None:
    fetcher, recorder, _ = make(lambda request: httpx.Response(200, content=b"x" * 2_000))
    for _ in range(4):
        assert fetcher.get("https://cercind.gov.in/big.pdf").error == "too_large"
    assert recorder.hosts == [APEX] * 4 and not fetcher.circuit_open


# -- rate limiting -------------------------------------------------------------------


def test_coal_rate_limit_spaces_requests_four_seconds_apart() -> None:
    fetcher, _, clock = make(ok, policy=COAL_POLICY, hosts=COAL_HOSTS)
    for path in ("a", "b", "c"):
        assert fetcher.get(f"https://www.coal.nic.in/{path}").ok
    assert clock.sleeps == [4.0, 4.0]


def test_rate_limit_is_per_host_unless_shared() -> None:
    fetcher, _, clock = make(ok, policy=COAL_POLICY, hosts=COAL_HOSTS)
    fetcher.get("https://www.coal.nic.in/a")
    fetcher.get("https://coal.gov.in/b.pdf")
    assert clock.sleeps == []
    fetcher.get("https://www.coal.nic.in/c")
    assert clock.sleeps == [4.0]


def test_shared_rate_limit_spans_hosts() -> None:
    fetcher, _, clock = make(ok)
    fetcher.get("https://cercind.gov.in/a")
    fetcher.get("https://www.cercind.gov.in/b")
    assert clock.sleeps == [1.0]


def test_elapsed_time_counts_toward_rate_limit() -> None:
    fetcher, _, clock = make(ok, policy=COAL_POLICY, hosts=COAL_HOSTS)
    fetcher.get("https://www.coal.nic.in/a")
    clock.now += 3.0
    fetcher.get("https://www.coal.nic.in/b")
    assert clock.sleeps == [1.0]


def test_each_redirect_hop_is_rate_limited() -> None:
    fetcher, _, clock = make(redirect_chain(2), policy=COAL_POLICY, hosts=COAL_HOSTS)
    assert fetcher.get("https://www.coal.nic.in/hop?n=0").ok
    assert clock.sleeps == [4.0, 4.0]


# -- failure classes -----------------------------------------------------------------


def _with_cause(exc: Exception, cause: BaseException) -> Exception:
    exc.__cause__ = cause
    return exc


FAILURES = [
    (httpx.ConnectTimeout("timed out"), "connect_timeout"),
    (
        httpx.ConnectTimeout("_ssl.c:989: The handshake operation timed out"),
        "tls_handshake_timeout",
    ),
    (
        _with_cause(httpx.ConnectError("EOF occurred"), ssl.SSLEOFError(8, "EOF occurred")),
        "tls_handshake_timeout",
    ),
    (
        _with_cause(
            httpx.ConnectError("verify failed"),
            ssl.SSLCertVerificationError(1, "certificate verify failed"),
        ),
        "tls_certificate_error",
    ),
    (httpx.ReadTimeout("timed out"), "read_timeout"),
    (httpx.ReadError("[Errno 104] Connection reset by peer"), "connection_reset"),
    (httpx.ConnectError("[Errno 104] Connection reset by peer"), "connection_reset"),
    (httpx.ConnectError("[Errno 111] Connection refused"), "connection_refused"),
    (httpx.ConnectError("[Errno -2] Name or service not known"), "dns_error"),
    (httpx.RemoteProtocolError("Server disconnected without sending a response."), "empty_reply"),
    (httpx.RemoteProtocolError("illegal status line: bytearray(b'xx')"), "protocol_error"),
    (httpx.WriteTimeout("timed out"), "write_timeout"),
    (httpx.PoolTimeout("timed out"), "pool_timeout"),
    (httpx.ProxyError("502 Bad Gateway"), "proxy_error"),
    (httpx.ConnectError("something else"), "connect_error"),
]


@pytest.mark.parametrize(("exc", "expected"), FAILURES)
def test_connection_failures_are_classified(exc: Exception, expected: str) -> None:
    fetcher, _, _ = make(raiser(exc), policy=COAL_POLICY, hosts=COAL_HOSTS)
    response = fetcher.get("https://www.coal.nic.in/a")
    assert response.error == expected
    assert response.status_code == 0 and response.body == b""
    assert response.served_by_host == "www.coal.nic.in"


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (
            httpx.RemoteProtocolError("peer closed connection without sending body"),
            "incomplete_body",
        ),
        (httpx.ReadTimeout("timed out"), "read_timeout"),
        (httpx.ReadError("[Errno 104] Connection reset by peer"), "connection_reset"),
    ],
)
def test_failures_while_reading_body(exc: Exception, expected: str) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=BrokenStream(exc))

    fetcher, _, _ = make(respond, policy=COAL_POLICY, hosts=COAL_HOSTS)
    assert fetcher.get("https://www.coal.nic.in/a").error == expected


def test_classify_plain_socket_errors() -> None:
    assert classify_error(ConnectionResetError()) == "connection_reset"
    assert classify_error(TimeoutError(), response_started=True) == "read_timeout"
    assert classify_error(TimeoutError()) == "connect_timeout"


# -- D-020 host fallback, retries, and circuit breaker -------------------------------


def test_cerc_fallback_rounds_order_attempts_and_backoff() -> None:
    fetcher, recorder, clock = make(raiser(httpx.ConnectTimeout("timed out")))
    response = fetcher.get("https://cercind.gov.in/Draft_reg.html")
    assert response.error == "connect_timeout"
    assert recorder.hosts == [APEX, WWW] * 3
    assert {request.url.path for request in recorder.requests} == {"/Draft_reg.html"}
    # Shared 1 req/s spacing inside each round, then the 20 s and 40 s backoffs.
    assert clock.sleeps == [1.0, 20.0, 1.0, 40.0, 1.0]
    assert fetcher.attempts == 6


def test_fallback_starts_on_canonical_host_even_for_www_url() -> None:
    fetcher, recorder, _ = make(raiser(httpx.ReadTimeout("timed out")))
    fetcher.get("https://www.cercind.gov.in/x.html?a=1")
    assert recorder.hosts[:2] == [APEX, WWW]
    assert all(request.url.query == b"a=1" for request in recorder.requests)


def test_fallback_host_answers_and_is_recorded() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == APEX:
            raise httpx.ReadError("[Errno 104] Connection reset by peer")
        return ok(request)

    fetcher, recorder, clock = make(respond)
    response = fetcher.get("https://cercind.gov.in/Draft_reg.html")
    assert response.ok
    assert response.served_by_host == WWW
    assert response.url == "https://www.cercind.gov.in/Draft_reg.html"
    assert recorder.hosts == [APEX, WWW] and clock.sleeps == [1.0]


def test_recovery_in_a_later_round() -> None:
    calls = {"n": 0}

    def respond(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 3:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return ok(request)

    fetcher, recorder, clock = make(respond)
    response = fetcher.get("https://cercind.gov.in/a")
    assert response.ok and response.served_by_host == WWW
    assert recorder.hosts == [APEX, WWW, APEX, WWW]
    assert 20.0 in clock.sleeps and 40.0 not in clock.sleeps


@pytest.mark.parametrize("status", [404, 403, 500, 503])
def test_no_fallback_on_http_status(status: int) -> None:
    fetcher, recorder, _ = make(lambda request: httpx.Response(status, content=b"err"))
    response = fetcher.get("https://cercind.gov.in/missing.html")
    assert response.status_code == status and response.error is None and response.body == b"err"
    assert recorder.hosts == [APEX]


def test_no_fallback_on_unlisted_failure_class() -> None:
    exc = _with_cause(
        httpx.ConnectError("verify failed"),
        ssl.SSLCertVerificationError(1, "certificate verify failed"),
    )
    fetcher, recorder, clock = make(raiser(exc))
    assert fetcher.get("https://cercind.gov.in/a").error == "tls_certificate_error"
    assert recorder.hosts == [APEX] and clock.sleeps == []


def test_no_fallback_for_hosts_outside_the_fallback_group() -> None:
    policy = dataclasses.replace(CERC_POLICY, max_attempts_per_url=1)
    hosts = CERC_HOSTS | {"files.cercind.gov.in"}
    fetcher, recorder, _ = make(raiser(httpx.ConnectTimeout("t")), policy=policy, hosts=hosts)
    fetcher.get("https://files.cercind.gov.in/a.pdf")
    assert recorder.hosts == ["files.cercind.gov.in"]


def test_site_without_fallback_makes_one_attempt() -> None:
    fetcher, recorder, clock = make(
        raiser(httpx.ConnectTimeout("t")), policy=COAL_POLICY, hosts=COAL_HOSTS
    )
    assert fetcher.get("https://www.coal.nic.in/a").error == "connect_timeout"
    assert len(recorder.requests) == 1 and clock.sleeps == []


def test_circuit_breaker_opens_after_three_exhausted_urls() -> None:
    fetcher, recorder, _ = make(raiser(httpx.ConnectTimeout("t")))
    for index in range(3):
        assert not fetcher.circuit_open
        assert fetcher.get(f"https://cercind.gov.in/{index}.html").error == "connect_timeout"
    assert fetcher.circuit_open
    before = len(recorder.requests)
    assert before == 18
    response = fetcher.get("https://cercind.gov.in/later.html")
    assert response.error == "circuit_open" and response.status_code == 0
    assert len(recorder.requests) == before


def test_success_resets_consecutive_failure_count() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/bad"):
            raise httpx.ConnectTimeout("t")
        return ok(request)

    fetcher, _, _ = make(respond)
    for path in ("/bad1", "/bad2", "/good", "/bad3", "/bad4"):
        fetcher.get(f"https://cercind.gov.in{path}")
    assert fetcher.consecutive_failed_urls == 2 and not fetcher.circuit_open


def test_http_errors_and_policy_refusals_do_not_trip_breaker() -> None:
    fetcher, _, _ = make(lambda request: httpx.Response(404))
    for index in range(5):
        fetcher.get(f"https://cercind.gov.in/{index}")
        fetcher.get("https://evil.example/")
    assert not fetcher.circuit_open and fetcher.consecutive_failed_urls == 0


# -- conditional requests ------------------------------------------------------------


def test_conditional_headers_pass_through_and_304_is_returned() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304, headers={"ETag": '"v1"'})
        return httpx.Response(200, headers={"ETag": '"v1"'}, content=b"%PDF-1.7")

    fetcher, recorder, _ = make(respond)
    url = "https://cercind.gov.in/doc.pdf"
    first = fetcher.get(url)
    assert first.ok and first.headers["etag"] == '"v1"'
    second = fetcher.get(
        url,
        headers={"If-None-Match": '"v1"', "If-Modified-Since": "Wed, 21 Oct 2026 07:28:00 GMT"},
    )
    assert second.status_code == 304 and second.error is None and second.body == b""
    assert not second.ok  # FetchResponse.ok is 2xx only; callers check for 304 explicitly
    sent = recorder.requests[1].headers
    assert sent["if-modified-since"] == "Wed, 21 Oct 2026 07:28:00 GMT"


def test_timeout_comes_from_policy() -> None:
    seen: list[dict[str, float | None]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"])
        return ok(request)

    fetcher, _, _ = make(respond)
    fetcher.get("https://cercind.gov.in/")
    assert seen[0] == {"connect": 120.0, "read": 120.0, "write": 120.0, "pool": 120.0}


# -- per-site pool -------------------------------------------------------------------


def test_pool_shares_one_fetcher_per_site() -> None:
    contracts = load_contracts(load_config(ROOT / "config" / "sources.yaml"))
    pool = SiteFetcherPool()
    try:
        cerc = [c for c in contracts.values() if c.site_id == "cerc"]
        coal = next(c for c in contracts.values() if c.site_id == "ministry_of_coal")
        first = pool.for_contract(cerc[0])
        assert all(pool.for_contract(c) is first for c in cerc)
        assert pool.for_contract(coal) is not first
        assert first.policy.fallback_hosts == (WWW,)
        assert first.policy.max_attempts_per_url == 6
        assert pool.get("cerc") is first
        changed = dataclasses.replace(
            cerc[1], fetch=dataclasses.replace(cerc[1].fetch, max_redirects=1)
        )
        with pytest.raises(ValueError, match="differs"):
            pool.for_contract(changed)
    finally:
        pool.close()
    assert pool.get("cerc") is None


# -- ReplayFetcher -------------------------------------------------------------------


def test_replay_fetcher_serves_fixtures_bodies_and_404() -> None:
    fetcher = ReplayFetcher(fixture_root=FIXTURES)
    fetcher.add("https://cercind.gov.in/Draft_reg.html", fixture="cerc/draft_regulations.html")
    fetcher.add("https://cercind.gov.in/a.pdf", body=b"%PDF-1.7", content_type="application/pdf")
    page = fetcher.get("https://cercind.gov.in/Draft_reg.html#top")
    assert page.ok and page.headers["Content-Type"].startswith("text/html")
    assert page.body == (FIXTURES / "cerc" / "draft_regulations.html").read_bytes()
    assert page.served_by_host == APEX
    missing = fetcher.get("https://cercind.gov.in/unknown.html")
    assert missing.status_code == 404 and missing.error is None
    assert fetcher.unmatched == ["https://cercind.gov.in/unknown.html"]
    assert fetcher.unused() == ["https://cercind.gov.in/a.pdf"]
    assert fetcher.requested_urls[0] == "https://cercind.gov.in/Draft_reg.html#top"


def test_replay_fetcher_sequences_errors_and_conditional() -> None:
    url = "https://cercind.gov.in/x.pdf"
    fetcher = ReplayFetcher(
        {"https://cercind.gov.in/plain": b"plain"},
    ).add_sequence(
        url,
        [
            ReplayEntry(error="connection_reset", served_by_host=APEX),
            ReplayEntry(body=b"%PDF", headers={"ETag": '"e1"'}, served_by_host=WWW),
        ],
    )
    assert fetcher.get(url).error == "connection_reset"
    second = fetcher.get(url)
    assert second.ok and second.served_by_host == WWW
    third = fetcher.get(url, headers={"If-None-Match": '"e1"'})
    assert third.status_code == 304 and third.body == b""
    assert fetcher.requests[2].headers == {"If-None-Match": '"e1"'}
    assert fetcher.get("https://cercind.gov.in/plain").body == b"plain"


def test_replay_fetcher_missing_fixture_fails_at_setup() -> None:
    with pytest.raises(FileNotFoundError):
        ReplayFetcher(fixture_root=FIXTURES).add("https://x/", fixture="nope.html")


def test_malformed_urls_and_redirect_targets_never_raise() -> None:
    fetcher, recorder, _ = make(
        lambda request: httpx.Response(302, headers={"Location": "https://cercind.gov.in:99999/"})
    )
    assert fetcher.get("https://cercind.gov.in:notaport/").error == "invalid_url"
    assert fetcher.get("https://cercind.gov.in/a").error == "invalid_url"
    assert len(recorder.requests) == 1


# -- review hardening: encoding, deadline, downgrade, ports ---------------------------


class StreamingTransport(httpx.BaseTransport):
    """Transport that streams ``chunks`` lazily (MockTransport buffers bodies)."""

    def __init__(self, chunks: Callable[[], Iterator[bytes]], headers: dict[str, str]) -> None:
        self.chunks = chunks
        self.headers = headers
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        outer = self

        class Stream(httpx.SyncByteStream):
            def __iter__(self) -> Iterator[bytes]:
                yield from outer.chunks()

        return httpx.Response(200, headers=self.headers, stream=Stream())


def streaming(
    chunks: Callable[[], Iterator[bytes]],
    headers: dict[str, str],
    policy: FetchPolicy = CERC_POLICY,
) -> tuple[HttpFetcher, StreamingTransport, FakeClock]:
    transport = StreamingTransport(chunks, headers)
    clock = FakeClock()
    fetcher = HttpFetcher(
        policy,
        CERC_HOSTS,
        transport=transport,
        clock=clock,
        sleep=clock.sleep,
        resolver=public_resolver,
    )
    return fetcher, transport, clock


def test_identity_encoding_requested_by_default_and_overridable() -> None:
    fetcher, recorder, _ = make(ok)
    fetcher.get("https://cercind.gov.in/")
    fetcher.get("https://cercind.gov.in/", headers={"Accept-Encoding": "gzip"})
    assert recorder.requests[0].headers["accept-encoding"] == "identity"
    assert recorder.requests[1].headers["accept-encoding"] == "gzip"


def test_gzip_bomb_stops_at_cap_without_full_expansion() -> None:
    import gzip

    bomb = gzip.compress(b"\0" * 20_000_000)  # about 20 KB on the wire
    policy = dataclasses.replace(CERC_POLICY, max_document_bytes=100_000)
    assert len(bomb) < policy.max_document_bytes
    fetcher, _, _ = streaming(lambda: iter([bomb]), {"Content-Encoding": "gzip"}, policy)
    response = fetcher.get("https://cercind.gov.in/bomb.pdf")
    assert response.error == "too_large" and not fetcher.circuit_open
    # The decoder itself never expands past the requested bound in one feed.
    decoder = http_module._BoundedDecoder.for_encoding("gzip")
    assert decoder is not None
    assert sum(len(piece) for piece in decoder.feed(bomb, 1_001)) <= 1_001


def test_compressed_bodies_within_cap_are_decoded() -> None:
    import gzip
    import zlib

    payload = b"<html>" + b"x" * 500 + b"</html>"
    raw_deflate = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    bodies = {
        "gzip": gzip.compress(payload),
        "deflate": zlib.compress(payload),
        "x-gzip": gzip.compress(payload),
    }
    for encoding, body in bodies.items():
        fetcher, _, _ = streaming(
            lambda body=body: iter([body[:3], body[3:]]), {"Content-Encoding": encoding}
        )
        assert fetcher.get("https://cercind.gov.in/").body == payload, encoding
    raw = raw_deflate.compress(payload) + raw_deflate.flush()
    fetcher, _, _ = streaming(lambda: iter([raw]), {"Content-Encoding": "deflate"})
    assert fetcher.get("https://cercind.gov.in/").body == payload
    fetcher, _, _ = streaming(lambda: iter([b"\x00" * 10]), {"Content-Encoding": "br"})
    assert fetcher.get("https://cercind.gov.in/").error == "unsupported_encoding"
    plain, _, _ = streaming(lambda: iter([payload]), {})
    assert plain.get("https://cercind.gov.in/").body == payload


def test_slow_drip_body_hits_total_timeout_without_fallback() -> None:
    clock_box: list[FakeClock] = []

    def drip() -> Iterator[bytes]:
        for _ in range(10):
            clock_box[0].now += 50.0  # each chunk arrives inside the 120 s read timeout
            yield b"x"

    fetcher, transport, clock = streaming(drip, {"Content-Type": "text/html"})
    clock_box.append(clock)
    response = fetcher.get("https://cercind.gov.in/slow")
    assert response.error == "total_timeout"  # deadline 3 x 120 s
    assert len(transport.requests) == 1  # its own class: not in host_fallback_on
    assert fetcher.consecutive_failed_urls == 1  # a network-class failure for the breaker
    assert clock.now - 1_000.0 < 3 * 120 + 60


def test_https_to_http_redirect_is_refused() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.scheme == "https":
            return httpx.Response(302, headers={"Location": "http://cercind.gov.in/plain"})
        return ok(request)

    fetcher, recorder, _ = make(respond)
    response = fetcher.get("https://cercind.gov.in/a")
    assert response.error == "insecure_redirect" and len(recorder.requests) == 1
    assert response.url == "http://cercind.gov.in/plain"
    upgrade, recorder, _ = make(
        lambda request: (
            httpx.Response(301, headers={"Location": "https://cercind.gov.in/a"})
            if request.url.scheme == "http"
            else ok(request)
        )
    )
    assert upgrade.get("http://cercind.gov.in/a").ok  # http -> https is allowed
    assert not upgrade.circuit_open and upgrade.consecutive_failed_urls == 0


def test_non_default_ports_are_refused() -> None:
    fetcher, recorder, _ = make(ok)
    assert fetcher.get("https://cercind.gov.in:8443/").error == "port_not_allowed"
    assert fetcher.get("http://cercind.gov.in:8080/").error == "port_not_allowed"
    assert recorder.requests == []
    assert fetcher.get("https://cercind.gov.in:443/").ok
    assert fetcher.get("http://cercind.gov.in:80/").ok
    redirecting, recorder, _ = make(
        lambda request: httpx.Response(302, headers={"Location": "https://cercind.gov.in:8443/"})
    )
    assert redirecting.get("https://cercind.gov.in/a").error == "port_not_allowed"
    assert len(recorder.requests) == 1 and redirecting.consecutive_failed_urls == 0
