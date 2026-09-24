"""The machine under the perimeter, sampled when someone asks for the numbers.

No sampler thread: /metrics and the Monitor view are the only readers, and each
read refreshes the gauges first. CPU is the share used since the previous read,
which is what a scraper polling every few seconds wants.

GPU utilisation is not here yet (NVML on NVIDIA, ioreg on Apple Silicon — see
docs/design/observability.md, O3). What Ollama holds on the GPU is: /api/ps.
"""

from __future__ import annotations

from collections.abc import Iterable

import httpx
import psutil

from runner.audit.metrics import METRICS
from runner.kernel.errors import PolicyError
from runner.policy.loader import load_policy
from runner.services.enforcement import policy_path

__all__ = ["sample"]


def _ollama_endpoints() -> list[str]:
    """The Ollama substrates the policy declares — the ones worth asking."""
    path = policy_path()
    try:
        policy = load_policy(path) if path.exists() else None
    except PolicyError:
        return []
    return (
        [s.endpoint for s in policy.substrates if s.kind == "ollama" and s.endpoint]
        if policy
        else []
    )


def sample(ollama_endpoints: Iterable[str] | None = None) -> None:
    """Refresh the host and Ollama gauges in the process registry."""
    if ollama_endpoints is None:
        ollama_endpoints = _ollama_endpoints()
    METRICS.set("annona_host_cpu_ratio", psutil.cpu_percent(interval=None) / 100)
    memory = psutil.virtual_memory()
    METRICS.set("annona_host_memory_bytes", memory.total - memory.available, kind="used")
    METRICS.set("annona_host_memory_bytes", memory.total, kind="total")
    METRICS.set("annona_process_resident_bytes", psutil.Process().memory_info().rss)

    # A model Ollama evicted must disappear, not linger at its last size.
    METRICS.clear("annona_ollama_loaded_bytes")
    for endpoint in dict.fromkeys(e.rstrip("/").removesuffix("/v1") for e in ollama_endpoints):
        try:
            models = httpx.get(f"{endpoint}/api/ps", timeout=1.0).json().get("models", [])
        except (httpx.HTTPError, ValueError):
            continue  # Ollama down: substrate_up already says so.
        for m in models:
            name = str(m.get("name", ""))
            METRICS.set(
                "annona_ollama_loaded_bytes", float(m.get("size", 0)), model=name, kind="total"
            )
            METRICS.set(
                "annona_ollama_loaded_bytes", float(m.get("size_vram", 0)), model=name, kind="gpu"
            )
