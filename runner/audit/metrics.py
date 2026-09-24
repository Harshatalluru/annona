"""What the kernel is doing, as numbers — Prometheus text and JSON from one registry.

Every family is declared here, with its label *names* fixed. Label *values* come
from the code (a substrate id, a model, a class, an outcome) and never from
material: no path, no prompt, no person, no file name. That is a privacy rule
and a cardinality rule at the same time, and declaring the families in one
place is what makes it reviewable.

Pure and in memory (layer L2, like the ledger it sits beside): no I/O, no HTTP.
The kernel API renders it; the router, the gate and the tracking executor feed
it. The Prometheus text format is written by hand — it is a few lines, and a
client library would be the only new dependency in the perimeter.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

__all__ = ["METRICS", "Metrics", "hold_reason"]

Kind = Literal["counter", "gauge", "histogram"]

SECONDS = (0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300)
TOKENS_PER_SECOND = (1, 2, 5, 10, 20, 30, 50, 75, 100, 150, 200, 400)


@dataclass(frozen=True)
class Family:
    name: str
    kind: Kind
    help: str
    labels: tuple[str, ...] = ()
    buckets: tuple[float, ...] = ()


FAMILIES = {
    f.name: f
    for f in (
        Family(
            "annona_inference_seconds",
            "histogram",
            "Wall time of one inference.",
            ("substrate", "model"),
            SECONDS,
        ),
        Family(
            "annona_inference_failures_total",
            "counter",
            "Inferences that failed on a substrate.",
            ("substrate",),
        ),
        Family(
            "annona_tokens_total",
            "counter",
            "Tokens read and written by models.",
            ("substrate", "model", "direction"),
        ),
        Family(
            "annona_output_tokens_per_second",
            "histogram",
            "Generation speed of one inference.",
            ("substrate", "model"),
            TOKENS_PER_SECOND,
        ),
        Family(
            "annona_last_output_tokens_per_second",
            "gauge",
            "Generation speed of the latest inference.",
            ("substrate", "model"),
        ),
        Family("annona_requests_in_flight", "gauge", "Inferences running now.", ("substrate",)),
        Family(
            "annona_decisions_total",
            "counter",
            "Placement and gate decisions, as in the ledger.",
            ("kind", "outcome", "class"),
        ),
        Family("annona_holds_total", "counter", "Steps held, by reason.", ("reason",)),
        Family(
            "annona_egress_total",
            "counter",
            "Payloads that left the machine.",
            ("kind", "substrate"),
        ),
        Family(
            "annona_redacted_identifiers_total",
            "counter",
            "Identifiers replaced before egress.",
            ("label",),
        ),
        Family("annona_host_cpu_ratio", "gauge", "Machine CPU in use, 0 to 1."),
        Family("annona_host_memory_bytes", "gauge", "Machine memory.", ("kind",)),
        Family("annona_process_resident_bytes", "gauge", "Memory held by the daemon."),
        Family(
            "annona_host_info",
            "gauge",
            "Always 1: the chip, and what a local model runs on.",
            ("os", "arch", "chip", "accelerator"),
        ),
        Family(
            "annona_ollama_loaded_bytes",
            "gauge",
            "Models Ollama holds in memory, and how much of each is on the GPU.",
            ("model", "kind"),
        ),
        Family(
            "annona_substrate_up",
            "gauge",
            "1 if the substrate answered last time, else 0.",
            ("substrate",),
        ),
    )
}


@dataclass
class _Histogram:
    buckets: tuple[float, ...]
    counts: list[int] = field(default_factory=list)
    total: float = 0.0
    count: int = 0

    def __post_init__(self) -> None:
        self.counts = [0] * len(self.buckets)

    def observe(self, value: float) -> None:
        self.total += value
        self.count += 1
        for i, bound in enumerate(self.buckets):
            if value <= bound:
                self.counts[i] += 1

    def quantile(self, q: float) -> float | None:
        """Upper bound of the bucket holding the q-th observation (Prometheus-style)."""
        if not self.count:
            return None
        rank = q * self.count
        for bound, cumulative in zip(self.buckets, self.counts, strict=True):
            if cumulative >= rank:
                return bound
        return math.inf


LabelKey = tuple[str, ...]


class Metrics:
    """The registry. One per process; thread-safe; resettable for tests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, dict[LabelKey, float]] = {}
        self._histograms: dict[str, dict[LabelKey, _Histogram]] = {}

    def reset(self) -> None:
        with self._lock:
            self._values.clear()
            self._histograms.clear()

    def _key(self, name: str, labels: Mapping[str, str]) -> tuple[Family, LabelKey]:
        family = FAMILIES[name]
        if set(labels) != set(family.labels):
            raise ValueError(f"{name} takes labels {family.labels}, got {tuple(labels)}")
        return family, tuple(str(labels[n]) for n in family.labels)

    def inc(self, name: str, value: float = 1.0, /, **labels: str) -> None:
        _, key = self._key(name, labels)
        with self._lock:
            series = self._values.setdefault(name, {})
            series[key] = series.get(key, 0.0) + value

    def set(self, name: str, value: float, /, **labels: str) -> None:
        _, key = self._key(name, labels)
        with self._lock:
            self._values.setdefault(name, {})[key] = value

    def clear(self, name: str) -> None:
        """Drop every series of one gauge: for sets whose members come and go."""
        with self._lock:
            self._values.pop(name, None)

    def observe(self, name: str, value: float, /, **labels: str) -> None:
        family, key = self._key(name, labels)
        with self._lock:
            series = self._histograms.setdefault(name, {})
            series.setdefault(key, _Histogram(family.buckets)).observe(value)

    # ── Exposition ────────────────────────────────────────────────────────────

    def prometheus(self) -> str:
        """The Prometheus text exposition format, version 0.0.4."""
        lines: list[str] = []
        with self._lock:
            for family in FAMILIES.values():
                lines.append(f"# HELP {family.name} {family.help}")
                lines.append(f"# TYPE {family.name} {family.kind}")
                if family.kind == "histogram":
                    for key, h in sorted(self._histograms.get(family.name, {}).items()):
                        base = _labels(family.labels, key)
                        for bound, cumulative in zip(h.buckets, h.counts, strict=True):
                            le = _labels((*family.labels, "le"), (*key, _num(bound)))
                            lines.append(f"{family.name}_bucket{le} {cumulative}")
                        inf = _labels((*family.labels, "le"), (*key, "+Inf"))
                        lines.append(f"{family.name}_bucket{inf} {h.count}")
                        lines.append(f"{family.name}_sum{base} {_num(h.total)}")
                        lines.append(f"{family.name}_count{base} {h.count}")
                else:
                    for key, value in sorted(self._values.get(family.name, {}).items()):
                        lines.append(f"{family.name}{_labels(family.labels, key)} {_num(value)}")
        return "\n".join(lines) + "\n"

    def snapshot(self) -> dict[str, list[dict[str, object]]]:
        """The same numbers as JSON, with p50/p95 for histograms, for the UI."""
        out: dict[str, list[dict[str, object]]] = {}
        with self._lock:
            for family in FAMILIES.values():
                rows: list[dict[str, object]] = []
                if family.kind == "histogram":
                    for key, h in sorted(self._histograms.get(family.name, {}).items()):
                        rows.append(
                            {
                                "labels": dict(zip(family.labels, key, strict=True)),
                                "count": h.count,
                                "sum": round(h.total, 3),
                                "mean": round(h.total / h.count, 3) if h.count else None,
                                "p50": h.quantile(0.5),
                                "p95": h.quantile(0.95),
                            }
                        )
                else:
                    for key, value in sorted(self._values.get(family.name, {}).items()):
                        rows.append(
                            {"labels": dict(zip(family.labels, key, strict=True)), "value": value}
                        )
                out[family.name] = rows
        return out


def _labels(names: Sequence[str], values: Sequence[str]) -> str:
    if not names:
        return ""
    body = ",".join(f'{n}="{_escape(v)}"' for n, v in zip(names, values, strict=True))
    return "{" + body + "}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _num(value: float) -> str:
    if value == math.inf:
        return "+Inf"
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def hold_reason(reason: str) -> str:
    """A closed set of hold reasons from the free-text reason in the ledger."""
    text = reason.lower()
    if "sealed" in text:
        return "sealed"
    if "redact" in text:
        return "redactor"
    if "unavailable" in text or "failed" in text or "unreachable" in text:
        return "unavailable"
    return "no_rule"


METRICS = Metrics()
"""The process's registry. Enforcement is built per run; the numbers outlive it."""
