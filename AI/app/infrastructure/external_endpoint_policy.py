"""Fail-closed URL policy for every external AI network boundary.

Provider credentials and user consent do not make an arbitrary URL safe. This
module canonicalizes configured HTTPS endpoints and admits only the OpenAI API
host, or an operator-maintained exact allowlist for an explicit OpenAI gateway.
It deliberately performs no DNS lookup so readiness does not depend on the
network and never returns configured URLs from its public status helper.
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from dataclasses import dataclass
from typing import Optional
from urllib.parse import SplitResult, urlsplit, urlunsplit


ALLOWED_EXTERNAL_AI_HOSTS_ENV = "FOOD_CHATBOT_ALLOWED_EXTERNAL_AI_HOSTS"

OFFICIAL_EXTERNAL_AI_HOSTS = frozenset(
    {
        "api.openai.com",
    }
)

MAX_EXTERNAL_AI_URL_LENGTH = 4096
_DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


@dataclass(frozen=True, slots=True)
class ExternalEndpointDecision:
    """Internal validation result; callers must not log ``canonical_url``."""

    allowed: bool
    reason: str
    canonical_url: Optional[str] = None
    canonical_host: Optional[str] = None


@dataclass(frozen=True, slots=True)
class _ConfiguredHostPolicy:
    hosts: frozenset[str]
    invalid_entry_count: int

    @property
    def valid(self) -> bool:
        return self.invalid_entry_count == 0


class _HostnamePolicyError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _forbidden_ip_literal(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False

    # Some Python versions classify multicast addresses as globally scoped.
    # Check every forbidden category explicitly, then use ``is_global`` as the
    # conservative catch-all for future non-routable ranges.
    return bool(
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or not address.is_global
    )


def normalize_external_hostname(value: str) -> str:
    """Return an IDNA/lowercase hostname or raise a non-sensitive reason code."""

    if not isinstance(value, str):
        raise _HostnamePolicyError("hostname_invalid")
    candidate = value.strip().rstrip(".")
    if not candidate or any(ord(character) <= 0x20 for character in candidate):
        raise _HostnamePolicyError("hostname_invalid")
    if any(character in candidate for character in ("/", "\\", "?", "#", "@")):
        raise _HostnamePolicyError("hostname_invalid")

    unbracketed = candidate[1:-1] if candidate.startswith("[") and candidate.endswith("]") else candidate
    if "%" in unbracketed:
        # IPv6 zone identifiers are local-interface selectors; percent-encoded
        # DNS labels also create parser disagreements between clients.
        raise _HostnamePolicyError("hostname_invalid")

    try:
        address = ipaddress.ip_address(unbracketed)
    except ValueError:
        address = None

    if address is not None:
        canonical_ip = address.compressed.lower()
        if _forbidden_ip_literal(canonical_ip):
            raise _HostnamePolicyError("ip_literal_forbidden")
        return canonical_ip

    # libc/urllib can accept legacy hexadecimal, octal, shortened, or integer
    # IPv4 spellings that ipaddress intentionally rejects. Never treat those as
    # DNS hostnames because their actual destination differs by client/parser.
    try:
        socket.inet_aton(unbracketed)
    except OSError:
        pass
    else:
        raise _HostnamePolicyError("ip_literal_ambiguous")

    try:
        canonical = unbracketed.encode("idna").decode("ascii").lower().rstrip(".")
    except (UnicodeError, ValueError):
        raise _HostnamePolicyError("hostname_invalid") from None

    if not canonical or len(canonical) > 253:
        raise _HostnamePolicyError("hostname_invalid")
    if canonical == "localhost" or canonical.endswith(".localhost"):
        raise _HostnamePolicyError("localhost_forbidden")
    if canonical.endswith(".local"):
        raise _HostnamePolicyError("local_domain_forbidden")
    # Dotless names can resolve through an OS search suffix into private
    # infrastructure. Numeric-only forms can be interpreted as legacy IPv4.
    if "." not in canonical or canonical.replace(".", "").isdigit():
        raise _HostnamePolicyError("hostname_ambiguous")
    labels = canonical.split(".")
    if any(not _DNS_LABEL_RE.fullmatch(label) for label in labels):
        raise _HostnamePolicyError("hostname_invalid")
    return canonical


def _configured_host_policy() -> _ConfiguredHostPolicy:
    raw_value = os.getenv(ALLOWED_EXTERNAL_AI_HOSTS_ENV, "")
    hosts: set[str] = set()
    invalid_count = 0
    for raw_entry in raw_value.split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        if "*" in entry or "://" in entry:
            invalid_count += 1
            continue
        # A colon in a non-IP entry is a port or malformed hostname. Bracketed
        # IPv6 and bare IPv6 are accepted only when ipaddress validates them.
        unbracketed = entry[1:-1] if entry.startswith("[") and entry.endswith("]") else entry
        try:
            ipaddress.ip_address(unbracketed.rstrip("."))
            is_ip = True
        except ValueError:
            is_ip = False
        if ":" in entry and not is_ip:
            invalid_count += 1
            continue
        try:
            hosts.add(normalize_external_hostname(entry))
        except _HostnamePolicyError:
            invalid_count += 1
    return _ConfiguredHostPolicy(
        hosts=frozenset(hosts),
        invalid_entry_count=invalid_count,
    )


def external_endpoint_policy_status() -> dict[str, object]:
    """Return URL-free configuration facts for readiness/diagnostics."""

    configured = _configured_host_policy()
    return {
        "configured_allowlist_valid": configured.valid,
        "configured_allowlist_host_count": len(configured.hosts),
        "invalid_allowlist_entry_count": configured.invalid_entry_count,
        "official_host_count": len(OFFICIAL_EXTERNAL_AI_HOSTS),
    }


def _canonical_netloc(host: str, port: Optional[int]) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    rendered_host = f"[{host}]" if address is not None and address.version == 6 else host
    # An explicit standard port is safe but unnecessary in the canonical form.
    return rendered_host if port in (None, 443) else f"{rendered_host}:{port}"


def validate_external_ai_url(
    value: object,
    *,
    require_configured_allowlist: bool = False,
) -> ExternalEndpointDecision:
    """Validate and canonicalize one network URL without exposing it in errors.

    ``require_configured_allowlist`` is used for every explicit base-URL
    override, including an override that happens to name an official host.  The
    official hosts are automatically trusted only for built-in defaults.
    """

    if not isinstance(value, str) or not value or len(value) > MAX_EXTERNAL_AI_URL_LENGTH:
        return ExternalEndpointDecision(False, "url_invalid")
    if value != value.strip():
        return ExternalEndpointDecision(False, "url_whitespace_forbidden")
    if "\\" in value or any(ord(character) <= 0x20 or ord(character) == 0x7F for character in value):
        return ExternalEndpointDecision(False, "url_invalid")

    try:
        parsed: SplitResult = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError, UnicodeError):
        return ExternalEndpointDecision(False, "url_invalid")

    if parsed.scheme.lower() != "https":
        return ExternalEndpointDecision(False, "https_required")
    if not parsed.netloc or parsed.hostname is None:
        return ExternalEndpointDecision(False, "hostname_missing")
    if parsed.username is not None or parsed.password is not None:
        return ExternalEndpointDecision(False, "userinfo_forbidden")
    if parsed.fragment:
        return ExternalEndpointDecision(False, "fragment_forbidden")
    # Provider keys and user context must never be placed in query strings.  A
    # blanket ban also avoids custom endpoint ambiguity and query-based routing.
    if parsed.query:
        return ExternalEndpointDecision(False, "query_forbidden")
    if port not in (None, 443):
        return ExternalEndpointDecision(False, "nonstandard_port_forbidden")

    try:
        host = normalize_external_hostname(parsed.hostname)
    except _HostnamePolicyError as exc:
        return ExternalEndpointDecision(False, exc.reason)

    configured = _configured_host_policy()
    if require_configured_allowlist:
        if not configured.valid:
            return ExternalEndpointDecision(False, "configured_allowlist_invalid")
        if host not in configured.hosts:
            return ExternalEndpointDecision(False, "override_host_not_allowed")
    elif host not in OFFICIAL_EXTERNAL_AI_HOSTS:
        if not configured.valid:
            return ExternalEndpointDecision(False, "configured_allowlist_invalid")
        if host not in configured.hosts:
            return ExternalEndpointDecision(False, "host_not_allowed")

    canonical_url = urlunsplit(
        (
            "https",
            _canonical_netloc(host, port),
            parsed.path,
            "",
            "",
        )
    )
    return ExternalEndpointDecision(
        True,
        "allowed",
        canonical_url=canonical_url,
        canonical_host=host,
    )


__all__ = [
    "ALLOWED_EXTERNAL_AI_HOSTS_ENV",
    "ExternalEndpointDecision",
    "OFFICIAL_EXTERNAL_AI_HOSTS",
    "external_endpoint_policy_status",
    "normalize_external_hostname",
    "validate_external_ai_url",
]
