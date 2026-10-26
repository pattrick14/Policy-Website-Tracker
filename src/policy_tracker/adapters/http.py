"""Policy-enforcing HTTP fetcher (Step 3).

``HttpFetcher`` implements the frozen ``Fetcher`` protocol over ``httpx`` and
applies one site's ``FetchPolicy``: host allowlist, manual redirects with
per-hop host, port, scheme-downgrade, and public-address checks, a streamed
size cap on decoded bytes with bounded decompression, a per-exchange
wall-clock deadline, a per-host (or
per-site) rate limit, D-020 connection-failure classes, host fallback with
spaced retry rounds, and a run-level circuit breaker. It never raises for
network failures; it returns ``FetchResponse(error=...)`` instead.

See ``docs/FETCHING.md`` for the error vocabulary and the adapter contract.
"""

from __future__ import annotations

import ipaddress
import socket
import ssl
import time
import urllib.request
import zlib
from collections.abc import Callable, Iterable, Mapping
from typing import Self
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from policy_tracker.adapters.base import FetchResponse
from policy_tracker.core.contracts import FeedContract, FetchPolicy

DEFAULT_USER_AGENT = "policy-website-tracker/0 (weekly policy publication tracker; httpx)"

# Whole-exchange wall-clock bound, as a multiple of ``request_timeout_seconds``.
# Per-phase timeouts reset on every received chunk, so without it a slow-drip
# server could hold one request open indefinitely.
TOTAL_TIMEOUT_FACTOR = 3.0
# Responses are requested uncompressed; a caller may override Accept-Encoding.
DEFAULT_ACCEPT_ENCODING = "identity"
_DEFAULT_PORTS = {"http": 80, "https": 443}

# Connection-level failure classes. The first five are the D-020 vocabulary used
# by ``FetchPolicy.host_fallback_on``; the rest are finer splits that never
# trigger a fallback unless a site lists them.
CONNECT_TIMEOUT = "connect_timeout"
TLS_HANDSHAKE_TIMEOUT = "tls_handshake_timeout"
READ_TIMEOUT = "read_timeout"
CONNECTION_RESET = "connection_reset"
EMPTY_REPLY = "empty_reply"
D020_FAILURE_CLASSES = frozenset(
    {CONNECT_TIMEOUT, TLS_HANDSHAKE_TIMEOUT, READ_TIMEOUT, CONNECTION_RESET, EMPTY_REPLY}
)
NETWORK_ERRORS = D020_FAILURE_CLASSES | {
    "write_timeout",
    "pool_timeout",
    "connection_refused",
    "dns_error",
    "tls_certificate_error",
    "connect_error",
    "read_error",
    "protocol_error",
    "incomplete_body",
    "proxy_error",
    "transport_error",
    "total_timeout",
}
# Refusals decided locally by policy; they never count toward the circuit breaker.
POLICY_ERRORS = frozenset(
    {
        "invalid_url",
        "host_not_allowed",
        "port_not_allowed",
        "private_address",
        "insecure_redirect",
        "unsupported_encoding",
        "too_many_redirects",
        "too_large",
        "circuit_open",
    }
)

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_RESET_MARKERS = ("reset", "errno 104", "econnreset", "broken pipe", "aborted", "errno 32")
_DNS_MARKERS = (
    "name or service not known",
    "nodename nor servname",
    "temporary failure in name resolution",
    "getaddrinfo",
    "no address associated",
)

Resolver = Callable[[str], Iterable[str]]


def system_resolver(host: str) -> list[str]:
    """Resolve ``host`` to IP strings with the system resolver; raises ``OSError``."""

    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({str(info[4][0]) for info in infos})


def is_public_address(address: str) -> bool:
    """True when ``address`` is a globally routable unicast IP."""

    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or not ip.is_global
    )


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in chain and len(chain) < 10:
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def classify_error(exc: BaseException, *, response_started: bool = False) -> str:
    """Map an ``httpx``/socket exception to a failure class (see ``NETWORK_ERRORS``).

    ``response_started`` is True once a status line and headers were received,
    so a disconnect mid-body is ``incomplete_body`` rather than ``empty_reply``.
    TLS handshake classification is best effort: a connect timeout whose
    message mentions the handshake, or a non-certificate SSL error while
    connecting, is reported as ``tls_handshake_timeout``.
    """

    chain = _exception_chain(exc)
    text = " ".join(str(item) for item in chain).lower()
    has_ssl = any(isinstance(item, ssl.SSLError) for item in chain) or "ssl" in text
    cert_error = any(isinstance(item, ssl.SSLCertVerificationError) for item in chain) or (
        "certificate verify failed" in text
    )
    reset = any(isinstance(item, ConnectionResetError | BrokenPipeError) for item in chain) or any(
        marker in text for marker in _RESET_MARKERS
    )

    if isinstance(exc, httpx.ConnectTimeout):
        return TLS_HANDSHAKE_TIMEOUT if "handshake" in text or has_ssl else CONNECT_TIMEOUT
    if isinstance(exc, httpx.ReadTimeout):
        return READ_TIMEOUT
    if isinstance(exc, httpx.WriteTimeout):
        return "write_timeout"
    if isinstance(exc, httpx.PoolTimeout):
        return "pool_timeout"
    if isinstance(exc, httpx.ProxyError):
        return "proxy_error"
    if isinstance(exc, httpx.RemoteProtocolError):
        if response_started:
            return "incomplete_body"
        if "without sending" in text or "disconnected" in text or "empty" in text:
            return EMPTY_REPLY
        return "protocol_error"
    if isinstance(exc, httpx.UnsupportedProtocol | httpx.InvalidURL):
        return "invalid_url"
    if isinstance(exc, httpx.ConnectError):
        if cert_error:
            return "tls_certificate_error"
        if reset:
            return CONNECTION_RESET
        if any(isinstance(item, socket.gaierror) for item in chain) or any(
            marker in text for marker in _DNS_MARKERS
        ):
            return "dns_error"
        if "refused" in text or any(isinstance(i, ConnectionRefusedError) for i in chain):
            return "connection_refused"
        if has_ssl:
            return TLS_HANDSHAKE_TIMEOUT
        return "connect_error"
    if isinstance(exc, httpx.ReadError | httpx.WriteError):
        return CONNECTION_RESET if reset else "read_error"
    if isinstance(exc, TimeoutError | socket.timeout):
        return READ_TIMEOUT if response_started else CONNECT_TIMEOUT
    if isinstance(exc, ConnectionResetError | BrokenPipeError):
        return CONNECTION_RESET
    return "transport_error"


class _RateLimiter:
    """Minimum spacing between request starts, keyed by host or shared."""

    def __init__(
        self,
        rate_per_second: float,
        *,
        shared: bool,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        self.interval = 1.0 / rate_per_second if rate_per_second > 0 else 0.0
        self.shared = shared
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, float] = {}

    def wait(self, host: str) -> None:
        key = "*" if self.shared else host
        last = self._last.get(key)
        if last is not None and self.interval > 0:
            delay = last + self.interval - self._clock()
            if delay > 0:
                self._sleep(delay)
        self._last[key] = self._clock()


class _Refused(Exception):
    def __init__(self, error: str, url: str, host: str | None) -> None:
        super().__init__(error)
        self.error = error
        self.url = url
        self.host = host


def _failure(url: str, error: str, host: str | None) -> FetchResponse:
    return FetchResponse(
        url=url, status_code=0, headers={}, body=b"", served_by_host=host, error=error
    )


def _with_host(url: str, host: str) -> str:
    parts = urlsplit(url)
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _env_proxy_applies(url: str) -> bool:
    parts = urlsplit(url)
    proxies = urllib.request.getproxies_environment()
    if not (proxies.get(parts.scheme) or proxies.get("all")):
        return False
    return not urllib.request.proxy_bypass_environment(parts.hostname or "")


class HttpFetcher:
    """``Fetcher`` that enforces one site's ``FetchPolicy``.

    One instance per site: it owns the site's rate-limit state, failure
    counter, and circuit breaker. Not thread-safe; feeds run sequentially.
    """

    def __init__(
        self,
        policy: FetchPolicy,
        allowed_hosts: frozenset[str],
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        user_agent: str = DEFAULT_USER_AGENT,
        resolver: Resolver | None = None,
    ) -> None:
        self.policy = policy
        self.allowed_hosts = frozenset(host.lower() for host in allowed_hosts)
        self._sleep = sleep
        self._clock = clock
        self._resolver = resolver or system_resolver
        # A local resolution failure is tolerated only when an environment proxy
        # performs the connection (the proxy then resolves the name).
        self._may_use_env_proxy = transport is None
        self._limiter = _RateLimiter(
            policy.request_rate_per_host_per_second,
            shared=policy.rate_limit_shared_across_hosts,
            clock=clock,
            sleep=sleep,
        )
        self._client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(policy.request_timeout_seconds),
            follow_redirects=False,
            verify=True,
            trust_env=True,
            headers={"User-Agent": user_agent, "Accept-Encoding": DEFAULT_ACCEPT_ENCODING},
        )
        self._consecutive_failed_urls = 0
        self._circuit_open = False
        self.attempts = 0  # network attempts made, for run health

    # -- public -----------------------------------------------------------------

    @property
    def circuit_open(self) -> bool:
        return self._circuit_open

    @property
    def consecutive_failed_urls(self) -> int:
        return self._consecutive_failed_urls

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def get(self, url: str, *, headers: Mapping[str, str] | None = None) -> FetchResponse:
        """GET ``url`` under the policy. Returns, never raises, on network failure."""

        if self._circuit_open:
            return _failure(url, "circuit_open", None)
        try:
            parts = urlsplit(url)
            parts.port  # noqa: B018 - raises ValueError for a malformed port
        except ValueError:
            return _failure(url, "invalid_url", None)
        host = (parts.hostname or "").lower() if parts.netloc else ""
        if parts.scheme not in ("http", "https") or not host:
            return _failure(url, "invalid_url", None)
        if host not in self.allowed_hosts:
            return _failure(url, "host_not_allowed", host)
        if not _default_port(parts.scheme, parts.port):
            return _failure(url, "port_not_allowed", host)

        hosts = self._host_order(host)
        max_attempts = max(1, self.policy.max_attempts_per_url)
        fallback_on = set(self.policy.host_fallback_on)
        attempts = 0
        round_index = 0
        last: FetchResponse | None = None
        while attempts < max_attempts:
            for candidate in hosts:
                if attempts >= max_attempts:
                    break
                last = self._fetch_once(_with_host(url, candidate), headers)
                attempts += 1
                if last.error is None:
                    self._consecutive_failed_urls = 0
                    return last
                if last.error not in fallback_on:
                    return self._finish_failure(last)
            if attempts >= max_attempts:
                break
            self._sleep(self._backoff(round_index))
            round_index += 1
        assert last is not None
        return self._finish_failure(last)

    # -- internals --------------------------------------------------------------

    def _host_order(self, host: str) -> list[str]:
        policy = self.policy
        if not policy.fallback_hosts:
            return [host]
        group = [policy.canonical_host or host, *policy.fallback_hosts]
        ordered = list(dict.fromkeys(h.lower() for h in group))
        if host not in ordered:
            return [host]
        return [h for h in ordered if h in self.allowed_hosts]

    def _backoff(self, round_index: int) -> float:
        backoff = self.policy.retry_backoff_seconds
        if not backoff:
            return 0.0
        return backoff[min(round_index, len(backoff) - 1)]

    def _finish_failure(self, response: FetchResponse) -> FetchResponse:
        if response.error in NETWORK_ERRORS:
            self._consecutive_failed_urls += 1
            limit = self.policy.abort_after_consecutive_failed_urls
            if limit is not None and self._consecutive_failed_urls >= limit:
                self._circuit_open = True
        return response

    def _check_host(self, url: str) -> str:
        try:
            parts = urlsplit(url)
            parts.port  # noqa: B018 - raises ValueError for a malformed port
        except ValueError:
            raise _Refused("invalid_url", url, None) from None
        host = (parts.hostname or "").lower()
        if parts.scheme not in ("http", "https") or not host:
            raise _Refused("invalid_url", url, host or None)
        if host not in self.allowed_hosts:
            raise _Refused("host_not_allowed", url, host)
        if not _default_port(parts.scheme, parts.port):
            raise _Refused("port_not_allowed", url, host)
        try:
            addresses = list(self._resolver(host))
        except OSError:
            if self._may_use_env_proxy and _env_proxy_applies(url):
                return host
            raise _Refused("dns_error", url, host) from None
        if not addresses or not all(is_public_address(a) for a in addresses):
            raise _Refused("private_address", url, host)
        return host

    def _fetch_once(self, url: str, headers: Mapping[str, str] | None) -> FetchResponse:
        current = url
        previous = url
        host: str | None = None
        try:
            for hop in range(self.policy.max_redirects + 1):
                if hop and _downgrades(previous, current):
                    raise _Refused("insecure_redirect", current, host)
                host = self._check_host(current)
                self._limiter.wait(host)
                self.attempts += 1
                result = self._send(current, host, headers)
                if isinstance(result, FetchResponse):
                    return result
                previous, current = current, result  # redirect target
            return _failure(current, "too_many_redirects", host)
        except _Refused as refused:
            return _failure(refused.url, refused.error, refused.host)

    def _send(self, url: str, host: str, headers: Mapping[str, str] | None) -> FetchResponse | str:
        """One HTTP exchange. Returns a response, or the next URL for a redirect."""

        started = False
        deadline = self._clock() + TOTAL_TIMEOUT_FACTOR * self.policy.request_timeout_seconds
        try:
            request = self._client.build_request("GET", url, headers=dict(headers or {}))
            response = self._client.send(request, stream=True)
            started = True
            if self._clock() > deadline:
                response.close()
                return _failure(url, "total_timeout", host)
            try:
                location = response.headers.get("location")
                if response.status_code in _REDIRECT_STATUSES and location:
                    return urljoin(url, location.strip())
                body = self._read_capped(response, deadline)
                if isinstance(body, str):
                    return _failure(url, body, host)
                return FetchResponse(
                    url=url,
                    status_code=response.status_code,
                    headers=httpx.Headers(response.headers),
                    body=body,
                    served_by_host=host,
                )
            finally:
                response.close()
        except httpx.InvalidURL:
            return _failure(url, "invalid_url", host)
        except (httpx.HTTPError, httpx.StreamError, OSError) as exc:
            return _failure(url, classify_error(exc, response_started=started), host)

    def _read_capped(self, response: httpx.Response, deadline: float) -> bytes | str:
        """Body decoded under the size cap and deadline, or an error class.

        Raw bytes are read with ``iter_raw`` and decoded here with a bounded
        incremental decompressor, so the cap applies to decoded bytes and no
        single chunk can expand past it (a gzip bomb stops at ``cap + 1``).
        """

        cap = self.policy.max_document_bytes
        declared = response.headers.get("content-length")
        if declared and declared.strip().isdigit() and int(declared) > cap:
            return "too_large"
        if response.is_stream_consumed:
            # The transport already buffered and decoded the body (httpx.MockTransport
            # in tests); only the cap can still apply.
            content = response.content
            return "too_large" if len(content) > cap else content
        decoder = _BoundedDecoder.for_encoding(response.headers.get("content-encoding"))
        if decoder is None:
            return "unsupported_encoding"
        chunks: list[bytes] = []
        total = 0
        raw_total = 0
        for raw in response.iter_raw():
            if self._clock() > deadline:
                return "total_timeout"
            raw_total += len(raw)
            if raw_total > cap:  # compressed bytes alone exceed the cap
                return "too_large"
            for piece in decoder.feed(raw, cap - total + 1):
                total += len(piece)
                if total > cap:
                    return "too_large"
                chunks.append(piece)
        for piece in decoder.flush(cap - total + 1):
            total += len(piece)
            if total > cap:
                return "too_large"
            chunks.append(piece)
        return b"".join(chunks)


class _BoundedDecoder:
    """Incremental Content-Encoding decoder whose output per call is bounded."""

    def __init__(self, encoding: str) -> None:
        self.encoding = encoding
        self._zlib: zlib._Decompress | None = None
        self._buffered = b""  # deflate: wait for enough bytes to pick zlib vs raw
        if encoding in ("gzip", "x-gzip"):
            self._zlib = zlib.decompressobj(zlib.MAX_WBITS | 16)

    @classmethod
    def for_encoding(cls, header: str | None) -> _BoundedDecoder | None:
        codings = [c.strip().lower() for c in (header or "").split(",") if c.strip()]
        codings = [c for c in codings if c != "identity"]
        if not codings:
            return cls("identity")
        if len(codings) == 1 and codings[0] in ("gzip", "x-gzip", "deflate"):
            return cls(codings[0])
        return None  # brotli, zstd, stacked codings: not requested, not decoded

    def _drain(self, data: bytes, limit: int) -> list[bytes]:
        assert self._zlib is not None
        out = [self._zlib.decompress(data, max(limit, 1))]
        produced = len(out[0])
        while self._zlib.unconsumed_tail and produced <= limit - 1:
            piece = self._zlib.decompress(self._zlib.unconsumed_tail, max(limit - produced, 1))
            if not piece:
                break
            out.append(piece)
            produced += len(piece)
        return out

    def feed(self, data: bytes, limit: int) -> list[bytes]:
        """Decode ``data``; return at most about ``limit`` bytes (more means over the cap)."""

        if self.encoding == "identity":
            return [data]
        try:
            if self._zlib is None:  # deflate: zlib-wrapped or raw, decided on first bytes
                self._buffered += data
                if len(self._buffered) < 2:
                    return []
                header = self._buffered[:2]
                wrapped = header[0] & 0x0F == 8 and int.from_bytes(header, "big") % 31 == 0
                self._zlib = zlib.decompressobj(zlib.MAX_WBITS if wrapped else -zlib.MAX_WBITS)
                data, self._buffered = self._buffered, b""
            return self._drain(data, limit)
        except zlib.error as exc:
            raise httpx.DecodingError(str(exc)) from exc

    def flush(self, limit: int) -> list[bytes]:
        if self.encoding == "identity":
            return []
        out: list[bytes] = []
        if self._zlib is None and self._buffered:
            out.extend(self.feed(b"", limit))
        if self._zlib is not None:
            try:
                out.append(self._zlib.flush())
            except zlib.error as exc:
                raise httpx.DecodingError(str(exc)) from exc
        return out


def _default_port(scheme: str, port: int | None) -> bool:
    return port is None or port == _DEFAULT_PORTS.get(scheme)


def _downgrades(previous: str, target: str) -> bool:
    """True for an https -> http redirect hop."""

    return urlsplit(previous).scheme == "https" and urlsplit(target).scheme != "https"


class SiteFetcherPool:
    """One ``HttpFetcher`` per site, so a site's feeds share rate limit and breaker."""

    def __init__(self, factory: Callable[[FetchPolicy, frozenset[str]], HttpFetcher] | None = None):
        self._factory = factory or (lambda policy, hosts: HttpFetcher(policy, hosts))
        self._fetchers: dict[str, tuple[FetchPolicy, frozenset[str], HttpFetcher]] = {}

    def for_contract(self, contract: FeedContract) -> HttpFetcher:
        hosts = contract.host_rules.allowed_hosts
        entry = self._fetchers.get(contract.site_id)
        if entry is not None:
            policy, allowed, fetcher = entry
            if policy != contract.fetch or allowed != hosts:
                raise ValueError(
                    f"feed {contract.feed_id!r} has a fetch policy or host allowlist that "
                    f"differs from other feeds of site {contract.site_id!r}"
                )
            return fetcher
        fetcher = self._factory(contract.fetch, hosts)
        self._fetchers[contract.site_id] = (contract.fetch, hosts, fetcher)
        return fetcher

    def get(self, site_id: str) -> HttpFetcher | None:
        entry = self._fetchers.get(site_id)
        return entry[2] if entry else None

    def close(self) -> None:
        for _, _, fetcher in self._fetchers.values():
            fetcher.close()
        self._fetchers.clear()


_DEFAULT_POOL = SiteFetcherPool()


def fetcher_for(contract: FeedContract) -> HttpFetcher:
    """Runner ``FetcherFactory``: the process-wide shared fetcher for the contract's site."""

    return _DEFAULT_POOL.for_contract(contract)


def close_fetchers() -> None:
    """Close and forget every shared fetcher (end of run, or between tests)."""

    _DEFAULT_POOL.close()
