"""Substrate health (layer L2).

Placement is the intersection of three questions: does the policy permit this
substrate, can it do what the step needs, and is it up. This module owns the
third one.

Health is *observed*, never assumed. A substrate is healthy because a probe
succeeded or because a real call succeeded recently — not because it is in the
configuration file. The distinction matters at exactly the moment the product is
judged: when the local GPU dies mid-run, a perimeter that believes its own
config file reroutes restricted material to a frontier API and calls it
resilience.

Two mechanisms, because the two failure modes are different:

**Active probe.** Cheap HTTP GET against an OpenAI-compatible endpoint, cached
for a few seconds. Used for substrates the operator marked ``probe: true``,
which in practice means anything self-hosted.

**Circuit breaker.** A backend that raises during a real call is marked down for
a cool-off period. This catches the substrates nobody wants to probe — a
metered frontier API — and it is why a hold is fast the second time.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from urllib.parse import urlparse

from loguru import logger

from runner.policy.models import Substrate

__all__ = ["Health", "SubstrateRegistry", "http_prober"]

DEFAULT_TTL_SECONDS = 10.0
"""How long a probe result is trusted. Short: this is a liveness answer."""

DEFAULT_COOLOFF_SECONDS = 60.0
"""How long a substrate stays down after a real call failed against it."""


@dataclass(frozen=True, slots=True)
class Health:
    """The answer to "can this substrate take work right now?"."""

    up: bool
    reason: str = ""
    latency_ms: float = 0.0

    @classmethod
    def ok(cls, latency_ms: float = 0.0) -> Health:
        return cls(True, "", latency_ms)

    @classmethod
    def down(cls, reason: str) -> Health:
        return cls(False, reason, 0.0)


MANAGED_KINDS = frozenset({"anthropic", "vertex", "bedrock", "azure"})
"""Kinds whose model listing needs the same credential as a real call.

An anonymous ``GET /models`` answers 401 there, which a probe would read as
"down" while the substrate is perfectly healthy. Their liveness is learned from
real calls instead, through :meth:`SubstrateRegistry.mark_down`.
"""

Prober = Callable[[Substrate], Health]
"""Probes one substrate. Injected, so tests never touch the network."""


def http_prober(timeout: float = 2.0) -> Prober:
    """Liveness probe for OpenAI-compatible endpoints.

    ``GET {endpoint}/models`` is the one call every OpenAI-compatible server
    answers — Ollama, vLLM, llama.cpp and LM Studio all do — and it neither
    loads a model nor costs a token.
    """

    def probe(substrate: Substrate) -> Health:
        if not substrate.endpoint or substrate.kind.lower() in MANAGED_KINDS:
            return Health.ok()

        import httpx  # imported here: L2 must not require an HTTP client to be importable

        parsed = urlparse(substrate.endpoint)
        if parsed.scheme not in ("http", "https"):
            return Health.down(f"endpoint is not http(s): {substrate.endpoint}")

        # Ollama's native API answers /api/tags; every OpenAI-compatible
        # server answers /models. Both are free and neither loads a model.
        base = substrate.endpoint.rstrip("/")
        url = f"{base}/api/tags" if substrate.kind.lower() == "ollama" else f"{base}/models"
        started = time.monotonic()
        try:
            response = httpx.get(url, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - any transport failure is "down"
            return Health.down(f"{type(exc).__name__}: {exc}")

        elapsed = (time.monotonic() - started) * 1000.0
        if response.status_code >= 400:
            return Health.down(f"HTTP {response.status_code} from {url}")
        return Health.ok(elapsed)

    return probe


@dataclass
class _Cached:
    health: Health
    at: float
    sticky_until: float = 0.0


@dataclass
class SubstrateRegistry:
    """Substrates plus their observed health.

    The registry answers questions about liveness only. It has no opinion about
    classes or jurisdictions — those belong to the policy, and keeping them out
    of here is what stops a "temporarily allow the frontier because local is
    down" shortcut from ever being expressible.
    """

    substrates: Mapping[str, Substrate]
    prober: Prober | None = None
    ttl: float = DEFAULT_TTL_SECONDS
    cooloff: float = DEFAULT_COOLOFF_SECONDS
    clock: Callable[[], float] = time.monotonic
    _cache: dict[str, _Cached] = field(default_factory=dict, repr=False)
    _broken: dict[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def from_substrates(
        cls,
        substrates: Iterable[Substrate],
        *,
        prober: Prober | None = None,
        **kwargs: object,
    ) -> SubstrateRegistry:
        return cls(
            substrates={s.id: s for s in substrates},
            prober=prober,
            **kwargs,  # type: ignore[arg-type]
        )

    def __contains__(self, substrate_id: object) -> bool:
        return substrate_id in self.substrates

    def get(self, substrate_id: str) -> Substrate | None:
        return self.substrates.get(substrate_id)

    # ── Liveness ──────────────────────────────────────────────────────────────

    def health(self, substrate_id: str) -> Health:
        """Current health, from cache, probe, or the circuit breaker."""
        substrate = self.substrates.get(substrate_id)
        if substrate is None:
            return Health.down(f"substrate {substrate_id!r} is not registered")

        if substrate_id in self._broken:
            return Health.down(self._broken[substrate_id])

        now = self.clock()
        cached = self._cache.get(substrate_id)

        if cached is not None and cached.sticky_until > now:
            return cached.health
        if cached is not None and (now - cached.at) < self.ttl:
            return cached.health

        if self.prober is None or not substrate.probe:
            health = Health.ok()
        else:
            health = self.prober(substrate)
            if not health.up:
                logger.warning(f"substrate {substrate_id} is down: {health.reason}")

        self._cache[substrate_id] = _Cached(health=health, at=now)
        return health

    def is_up(self, substrate_id: str) -> bool:
        return self.health(substrate_id).up

    def mark_down(self, substrate_id: str, reason: str) -> None:
        """Trip the breaker after a real call failed.

        Sticky for :attr:`cooloff` seconds so a run does not retry a dead
        substrate on every turn, and so the *reason* an operator sees in the
        ledger is the transport error rather than a later, vaguer probe failure.
        """
        now = self.clock()
        self._cache[substrate_id] = _Cached(
            health=Health.down(reason),
            at=now,
            sticky_until=now + self.cooloff,
        )
        logger.warning(f"substrate {substrate_id} marked down for {self.cooloff:.0f}s: {reason}")

    def mark_broken(self, substrate_id: str, reason: str) -> None:
        """Take a substrate out for the whole run: its adapter could not be built.

        Unlike :meth:`mark_down` this never expires and no probe or successful
        call clears it, because there is no adapter to call — a later "up" would
        route work to a substrate that cannot receive it.
        """
        self._broken[substrate_id] = reason
        logger.warning(f"substrate {substrate_id} unavailable for this run: {reason}")

    def mark_up(self, substrate_id: str, latency_ms: float = 0.0) -> None:
        """Record a successful real call, clearing any breaker."""
        self._cache[substrate_id] = _Cached(health=Health.ok(latency_ms), at=self.clock())

    def snapshot(self) -> dict[str, Health]:
        """Health of every registered substrate, for ``annona status``."""
        return {sid: self.health(sid) for sid in self.substrates}
