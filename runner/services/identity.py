"""Who is asking: a subject proven by the company's identity provider.

Annona stores no users and no passwords. A request carries a credential, and
one of the policy's providers proves it — or the request is refused:

- ``jwt``: a bearer token checked against the issuer's published keys (JWKS),
  with ``iss``, ``aud`` and ``exp`` enforced. Any OIDC provider; the Akaion
  platform is the same verifier with a preset (Firebase ID tokens are JWTs
  signed by Google). Nothing is sent to the issuer: the keys are public and
  cached.
- ``proxy``: an authenticating proxy (oauth2-proxy, IAP, Pomerium) sets the
  email and groups headers, and its shared secret beside them. Without the
  secret the headers are what anyone on the network path can type.

No credential is the anonymous subject, allowed only when the policy does not
require identity. See ``docs/design/multi-user.md``.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable, Mapping
from functools import cache
from typing import Any

import jwt

from runner.kernel.types import Subject
from runner.policy.models import IdentityPolicy, IdentityProvider

__all__ = ["PROXY_SECRET_HEADER", "IdentityError", "authenticate"]

PROXY_SECRET_HEADER = "X-Annona-Proxy-Secret"
ALGORITHMS = ["RS256", "ES256"]
"""Asymmetric only: a shared-secret HS256 token would let anyone holding the
audience's secret mint subjects, and the issuer's keys are public by design."""

KeyFor = Callable[[str, str], Any]
"""(jwks_url, token) → the public key that signed the token."""


class IdentityError(Exception):
    """The request could not be tied to a subject the policy accepts."""


@cache
def _jwks(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)


def _published_key(jwks_url: str, token: str) -> Any:
    return _jwks(jwks_url).get_signing_key_from_jwt(token).key


def _groups(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list | tuple):
        return ()
    return tuple(g.strip() for g in map(str, value) if g.strip())


def _from_jwt(provider: IdentityProvider, token: str, key_for: KeyFor) -> Subject:
    claims = jwt.decode(
        token,
        key_for(provider.jwks_url, token),
        algorithms=ALGORITHMS,
        audience=provider.audience,
        issuer=provider.issuer,
        options={"require": ["exp", "iss", "aud"]},
        leeway=30,
    )
    who = claims.get(provider.subject_claim)
    if not isinstance(who, str) or not who:
        raise IdentityError(f"the token has no {provider.subject_claim!r} claim")
    # An unverified email is a string the user typed at sign-up.
    if provider.subject_claim == "email" and claims.get("email_verified") is False:
        raise IdentityError("the token's email is not verified")
    return Subject(who, _groups(claims.get(provider.groups_claim)), f"jwt:{provider.issuer}")


def _from_proxy(provider: IdentityProvider, headers: Mapping[str, str]) -> Subject | None:
    who = headers.get(provider.email_header.lower(), "")
    if not who:
        return None
    expected = os.environ.get(provider.secret_env, "")
    given = headers.get(PROXY_SECRET_HEADER.lower(), "")
    if not expected or not secrets.compare_digest(given.encode(), expected.encode()):
        raise IdentityError(f"{provider.email_header} without the proxy's secret")
    return Subject(who, _groups(headers.get(provider.groups_header.lower(), "")), "proxy")


def authenticate(
    identity: IdentityPolicy,
    headers: Mapping[str, str],
    *,
    key_for: KeyFor = _published_key,
) -> Subject:
    """The subject behind a request, or :class:`IdentityError`.

    ``headers`` must be keyed in lower case (Starlette's are). A credential that
    is present and wrong is refused even when identity is optional: a forged
    header is never quietly downgraded to "anonymous".
    """
    auth = headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    jwt_providers = [p for p in identity.providers if p.kind == "jwt"]

    if token and jwt_providers:
        problems = []
        for provider in jwt_providers:
            try:
                return _from_jwt(provider, token, key_for)
            except (jwt.PyJWTError, IdentityError) as exc:
                problems.append(f"{provider.issuer}: {exc}")
        raise IdentityError("no provider accepted the token (" + "; ".join(problems) + ")")

    for provider in identity.providers:
        if provider.kind == "proxy":
            subject = _from_proxy(provider, headers)
            if subject is not None:
                return subject

    if identity.required:
        raise IdentityError("this perimeter requires identity, and the request carries none")
    return Subject()
