"""Keeping the working set honest (layer L2).

A decorator around any :class:`~runner.kernel.ports.ToolExecutor` that
classifies what each call *brought back* and folds it into the working set.

The gate classifies what a call is about to touch; this classifies what it
actually returned, and the two are not the same. A shell command with no path
argument can print a private key. A document reader pointed at an innocuous file
can return a page of identifiers. Classifying only the request is how a
perimeter ends up with a clean record and a leak.

Placed between the loop and the real executor rather than inside it, because
every tool would otherwise have to remember to do this, and one of them would
not.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from runner.audit.ledger import Ledger
from runner.kernel.types import SensitivityClass, ToolCall, ToolResult, ToolSpec
from runner.policy.classifier import PolicyClassifier, WorkingSet

__all__ = ["TrackingExecutor"]


class TrackingExecutor:
    """Wraps an executor and taints the run with what it returns."""

    def __init__(
        self,
        inner: object,
        classifier: PolicyClassifier,
        working_set: WorkingSet,
        ledger: Ledger | None = None,
    ) -> None:
        self._inner = inner
        self._classifier = classifier
        self._working_set = working_set
        self._ledger = ledger

    def specs(self) -> tuple[ToolSpec, ...]:
        return self._inner.specs()  # type: ignore[attr-defined]

    def invoke(self, call: ToolCall) -> ToolResult:
        result: ToolResult = self._inner.invoke(call)  # type: ignore[attr-defined]

        klass = self._classifier.classify_result(result)
        if klass > SensitivityClass.PUBLIC:
            before = self._working_set.klass
            after = self._working_set.observe(f"{call.name} result", klass)
            if after > before and self._ledger is not None:
                # Worth a line of its own: this is the moment a run stops being
                # placeable on a frontier model, and the reason is not visible
                # from the call that caused it.
                self._ledger.record(
                    "taint",
                    outcome="cleared",
                    klass=after,
                    substrate="local",
                    detail={
                        "tool": call.name,
                        "reason": f"result raised the working set to {after.label}",
                    },
                )

        # A tool that answers from a corpus (the company's memory) declares which
        # files it drew on. Recorded, not the passages: the ledger stores what was
        # decided and why, and a second copy of the memory would outlive it.
        sources = result.content.get("sources") if isinstance(result.content, Mapping) else None
        if sources and self._ledger is not None:
            query = str(call.arguments.get("query", ""))
            self._ledger.record(
                "retrieval",
                outcome="cleared",
                klass=self._working_set.klass,
                substrate="local",
                detail={
                    "tool": call.name,
                    "paths": [str(s) for s in list(sources)[:50]],
                    "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
                },
            )

        return result
