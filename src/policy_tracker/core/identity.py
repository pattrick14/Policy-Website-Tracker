"""Source-scoped identity rules (Step 2 frozen contract).

Precedence: source-native ID, then source-scoped canonical URL, then a stable
provenance hash. Titles, dates, and file hashes never establish identity.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from policy_tracker.core.models import EntityKind, Provenance

_DEFAULT_PORTS = {"http": 80, "https": 443}
_UNSAFE_PATH_CHARS = re.compile(r"[^A-Za-z0-9\-._~!$&'()*+,;=:@/%]")


@dataclass(frozen=True)
class HostRules:
    """Per-site URL rules derived from the contract configuration.

    ``host_aliases`` maps each tested mirror of a site host (including the
    canonical host itself) to the canonical host. HTTP is upgraded to HTTPS,
    and duplicated path slashes are collapsed where the site rule says so, only
    for aliased hosts. Other allowed hosts (such as a separate file host) keep
    their scheme and path verbatim until a tested rule covers them.
    """

    allowed_hosts: frozenset[str]
    host_aliases: Mapping[str, str]
    collapse_duplicate_slashes: bool = False


def canonical_url(url: str, *, base_url: str, rules: HostRules) -> str:
    """Return the source-scoped canonical form of ``url``.

    Query parameters are kept verbatim. Fragments, default ports, and host
    case are normalized. Spaces and other unsafe path characters are
    percent-encoded without double-encoding existing escapes.
    """

    absolute = urljoin(base_url, url.strip())
    parts = urlsplit(absolute)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    aliased = host in rules.host_aliases
    if aliased:
        host = rules.host_aliases[host]
        if scheme == "http":
            scheme = "https"
            if port == 80:
                port = None
    if port is not None and _DEFAULT_PORTS.get(scheme) == port:
        port = None
    netloc = host if port is None else f"{host}:{port}"
    path = parts.path or "/"
    if aliased and rules.collapse_duplicate_slashes:
        path = re.sub(r"/{2,}", "/", path)
    path = _UNSAFE_PATH_CHARS.sub(lambda match: quote(match.group(0)), path)
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def provenance_hash(provenance: Provenance) -> str:
    """Stable hash of where a record was seen; the last-resort identity."""

    payload = f"{provenance.page_url}\n{provenance.locator}".encode()
    return hashlib.sha256(payload).hexdigest()[:24]


def entity_key(
    site_id: str,
    kind: EntityKind,
    *,
    native_id: str | None,
    canonical: str | None,
    provenance: Provenance | None,
) -> str:
    """Return the record key, preferring native ID, then canonical URL, then provenance."""

    if native_id:
        return f"{site_id}:{kind.value}:id:{native_id}"
    if canonical:
        return f"{site_id}:{kind.value}:url:{canonical}"
    if provenance is None:
        raise ValueError("an identity needs a native id, a canonical URL, or provenance")
    return f"{site_id}:{kind.value}:prov:{provenance_hash(provenance)}"


def appearance_key(feed_id: str, kind: EntityKind, key: str) -> str:
    return f"{feed_id}|{kind.value}|{key}"


def stable_hash(*parts: str) -> str:
    """Deterministic short hash for fingerprints and event ids."""

    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()
    return digest[:32]
