"""HTTPS for skill catalogs — the adapter half of ``runner.skills.catalog``.

The catalog logic (index validation, SHA-256 check, paranoid unpacking) is a
decision layer and may not open sockets; this is the one function it is handed
to fetch bytes with. Kept here so the architectural contract "L2 skills cannot
reach an L1 adapter" holds by import, not by review.
"""

from __future__ import annotations

import httpx

from runner.policy.models import normalise_endpoint
from runner.skills.catalog import FETCH_TIMEOUT, CatalogError, Fetch

__all__ = ["http_fetch"]


def http_fetch(client: httpx.Client | None = None) -> Fetch:
    """A fetch that GETs over HTTPS and refuses a body larger than ``limit``."""

    def fetch(url: str, limit: int) -> bytes:
        try:
            normalise_endpoint(url)
        except ValueError as exc:
            raise CatalogError(str(exc)) from None
        http = client or httpx.Client(timeout=FETCH_TIMEOUT)
        try:
            with http.stream("GET", url) as response:
                if response.status_code != 200:
                    raise CatalogError(f"GET {url}: {response.status_code}")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > limit:
                        raise CatalogError(f"{url} is larger than {limit} bytes")
                return bytes(body)
        except httpx.HTTPError as exc:
            raise CatalogError(f"cannot fetch {url}: {exc}") from exc
        finally:
            if client is None:
                http.close()

    return fetch
