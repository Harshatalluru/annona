"""The perimeter, end to end, through the real agent loop.

These are the acceptance tests from the HLD, run against scripted substrates so
they are deterministic and need no network: T3 placement conformance, T4 the
leak canary, T6 failover, T7 tamper evidence, T8 the air gap.

Everything here goes through :class:`~runner.agent.loop.AgentLoop` — the same
loop production uses, unmodified. If the perimeter needed a special loop, it
would not be a perimeter; it would be a mode.
"""

from __future__ import annotations

import json

import pytest

from runner.agent.loop import AgentLoop
from runner.audit.ledger import verify_file
from runner.kernel.blocks import block_text
from runner.kernel.errors import BackendUnavailableError, PlacementHeldError
from runner.kernel.types import (
    Capabilities,
    Completion,
    CompletionRequest,
    SensitivityClass,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from runner.policy.loader import parse_policy
from runner.services.enforcement import Enforcement

pytestmark = [pytest.mark.integration, pytest.mark.e2e]

CANARY = "ANNONA-CANARY-7f3a91"


# ── Doubles ───────────────────────────────────────────────────────────────────


class ScriptedSubstrate:
    """A substrate that answers from a script and counts what it was sent.

    It records every payload it received, which is how the leak test works: the
    frontier substrate is a wiretap, and the assertion is that nothing sensitive
    ever reached it.
    """

    def __init__(self, name: str, script=(), *, local: bool = False, fail: bool = False):
        self._name = name
        self._script = list(script)
        self._local = local
        self._fail = fail
        self.received: list[str] = []
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(native_tools=True, is_local=self._local, context_window=32_000)

    def complete(self, request: CompletionRequest) -> Completion:
        self.calls += 1
        # Every block, tool calls and results included: a wiretap that only
        # read text blocks could not see a secret that arrived in a tool result.
        rendered = "\n".join(
            block_text(block) for turn in request.transcript for block in turn.blocks
        )
        self.received.append(request.system + "\n" + rendered)

        if self._fail:
            raise BackendUnavailableError(f"{self._name} is unreachable")

        if self._script:
            return self._script.pop(0)
        return Completion(text_parts=(f"{self._name} answered",), stop_reason="end_turn")


class FileTools:
    """A tool registry with one tool that returns file contents verbatim."""

    def __init__(self, files: dict[str, str]):
        self._files = files
        self.executed: list[str] = []

    def specs(self):
        return (
            ToolSpec(
                name="document_reader",
                description="Read a document",
                schema={"type": "object", "properties": {"path": {"type": "string"}}},
            ),
        )

    def invoke(self, call: ToolCall) -> ToolResult:
        path = str(call.arguments.get("path", ""))
        self.executed.append(path)
        if path not in self._files:
            return ToolResult(call_id=call.id, name=call.name, content="not found", is_error=True)
        return ToolResult(call_id=call.id, name=call.name, content=self._files[path])


def policy_document(tmp_path, *, on_unavailable="hold", brief=False):
    return {
        "version": 1,
        "default": "deny",
        "classes": {
            "restricted": {
                "paths": [f"{tmp_path}/clients/**"],
                "patterns": [r"[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]"],
            },
            "internal": {"paths": [f"{tmp_path}/work/**"]},
            "public": {"default": True},
        },
        "substrates": [
            {
                "id": "local-gpu",
                "kind": "echo",
                "jurisdiction": "on-prem",
                "max_class": "restricted",
                "quality": 60,
            },
            {
                "id": "eu-cluster",
                "kind": "echo",
                "jurisdiction": "eu",
                "max_class": "internal",
                "quality": 70,
            },
            {
                "id": "frontier",
                "kind": "echo",
                "jurisdiction": "us",
                "max_class": "public",
                "quality": 95,
            },
        ],
        "rules": [
            {
                "id": "R-restricted",
                "match": {"class": "restricted"},
                "allow": ["local-gpu"],
                "on_unavailable": on_unavailable,
            },
            {
                "id": "R-internal",
                "match": {"class": "internal"},
                "allow": ["local-gpu", "eu-cluster"],
                "on_unavailable": "hold",
            },
            {
                "id": "R-public",
                "match": {"class": "public"},
                "allow": ["frontier", "local-gpu"],
                "prefer": "quality",
                "on_unavailable": "hold",
            },
        ],
        "egress": {
            "canaries": [CANARY],
            "brief": {"produced_by": "local-gpu", "max_tokens": 256, "must_clear": True}
            if brief
            else {},
        },
        "tools": {
            "allow": {"document_reader": [f"{tmp_path}/**"]},
            "deny_paths": [f"{tmp_path}/secrets/**"],
        },
    }


def build(tmp_path, *, substrates, files=None, **policy_kwargs):
    """A perimeter wired to scripted substrates, with a real ledger on disk."""
    enforcement = Enforcement.for_run(
        policy=parse_policy(policy_document(tmp_path, **policy_kwargs)),
        ledger_path=tmp_path / "ledger.jsonl",
        backends=substrates,
        probe=False,
        fsync=False,
        run_id="test",
    )
    tools = FileTools(files or {})
    loop = AgentLoop(enforcement.backend(), enforcement.executor(tools), enforcement.gate())
    return enforcement, loop, tools


def read_file_then_answer(path: str) -> list[Completion]:
    return [
        Completion(
            tool_calls=(ToolCall(id="c1", name="document_reader", arguments={"path": path}),),
            stop_reason="tool_use",
        ),
        Completion(text_parts=("done",), stop_reason="end_turn"),
    ]


# ── T3 · placement conformance through the loop ───────────────────────────────


def test_a_public_task_is_placed_on_the_best_permitted_substrate(tmp_path):
    frontier = ScriptedSubstrate("frontier")
    local = ScriptedSubstrate("local-gpu", local=True)

    enforcement, loop, _ = build(
        tmp_path,
        substrates={
            "local-gpu": local,
            "eu-cluster": ScriptedSubstrate("eu"),
            "frontier": frontier,
        },
    )
    result = loop.run("summarise the public tender")

    assert frontier.calls == 1
    assert local.calls == 0
    assert result.response == "frontier answered"
    assert enforcement.klass is SensitivityClass.PUBLIC


def test_reading_a_client_file_moves_the_whole_run_on_prem(tmp_path):
    """The transcript is the leak: one restricted read redirects everything after it."""
    client_file = tmp_path / "clients" / "BG-114.pdf"
    client_file.parent.mkdir(parents=True)
    client_file.write_text("cliente RSSMRA85T10A562S")

    frontier = ScriptedSubstrate("frontier", read_file_then_answer(str(client_file)))
    local = ScriptedSubstrate("local-gpu", [Completion(text_parts=("local answer",))], local=True)

    enforcement, loop, tools = build(
        tmp_path,
        substrates={
            "local-gpu": local,
            "eu-cluster": ScriptedSubstrate("eu"),
            "frontier": frontier,
        },
        files={str(client_file): "cliente RSSMRA85T10A562S, pratica 2026/114"},
    )
    result = loop.run("compare this client file with our case law")

    assert tools.executed == [str(client_file)], "the read itself was permitted"
    assert enforcement.klass is SensitivityClass.RESTRICTED
    assert local.calls == 1, "the turn after the read went on-prem"
    assert result.response == "local answer"

    # The wiretap: whatever the frontier saw, it was never the file.
    assert not any("RSSMRA85T10A562S" in seen for seen in frontier.received)


# ── T4 · the leak canary ──────────────────────────────────────────────────────


def test_a_canary_never_reaches_a_substrate_that_may_not_hold_it(tmp_path):
    """The measurement behind "your data does not leave".

    A canary is planted in a file the agent is told to read. The frontier
    substrate records everything it receives. The assertion is a number: zero.
    """
    seeded = tmp_path / "work" / "notes.md"
    seeded.parent.mkdir(parents=True)
    seeded.write_text(f"internal note {CANARY}")

    frontier = ScriptedSubstrate("frontier", read_file_then_answer(str(seeded)))
    local = ScriptedSubstrate(
        "local-gpu", [Completion(text_parts=("handled locally",))], local=True
    )

    _, loop, _ = build(
        tmp_path,
        substrates={
            "local-gpu": local,
            "eu-cluster": ScriptedSubstrate("eu"),
            "frontier": frontier,
        },
        files={str(seeded): f"internal note {CANARY}"},
    )
    loop.run("read the note and summarise it")

    leaked = [seen for seen in frontier.received if CANARY in seen]
    assert leaked == [], f"leak rate is not zero: {len(leaked)} payload(s) carried the canary"


def test_a_canary_in_the_prompt_itself_is_treated_as_restricted(tmp_path):
    """Classification is of the bytes about to be sent, not of their provenance."""
    frontier = ScriptedSubstrate("frontier")
    local = ScriptedSubstrate("local-gpu", [Completion(text_parts=("local",))], local=True)

    _, loop, _ = build(
        tmp_path,
        substrates={
            "local-gpu": local,
            "eu-cluster": ScriptedSubstrate("eu"),
            "frontier": frontier,
        },
    )
    loop.run(f"what does {CANARY} refer to?")

    assert frontier.calls == 0
    assert local.calls == 1


# ── T6 · failover ─────────────────────────────────────────────────────────────


def test_when_the_local_gpu_dies_restricted_work_is_held_not_rerouted(tmp_path):
    """The test that matters commercially.

    The frontier substrate is up and would answer. It is not called, the run
    stops, and the ledger says why.
    """
    client_file = tmp_path / "clients" / "BG-114.pdf"
    client_file.parent.mkdir(parents=True)
    client_file.write_text("cliente RSSMRA85T10A562S")

    frontier = ScriptedSubstrate("frontier", read_file_then_answer(str(client_file)))
    local = ScriptedSubstrate("local-gpu", local=True, fail=True)

    enforcement, loop, _ = build(
        tmp_path,
        substrates={
            "local-gpu": local,
            "eu-cluster": ScriptedSubstrate("eu"),
            "frontier": frontier,
        },
        files={str(client_file): "cliente RSSMRA85T10A562S"},
    )
    result = loop.run("summarise the client file")

    assert enforcement.klass is SensitivityClass.RESTRICTED
    assert frontier.calls == 1, "only the first, still-public turn reached the frontier"
    assert not any("RSSMRA85T10A562S" in seen for seen in frontier.received)
    assert result.response == "", "the run stopped rather than answering from elsewhere"

    held = [e for e in enforcement.ledger.entries() if e.outcome == "held"]
    assert held, "a hold must be recorded"
    assert any("holds rather than downgrades" in str(e.detail.get("reason", "")) for e in held)


def test_a_public_task_does_fail_over_within_the_permitted_set(tmp_path):
    """Failover is not disabled — it is bounded. Cost may degrade; jurisdiction may not."""
    frontier = ScriptedSubstrate("frontier", fail=True)
    local = ScriptedSubstrate(
        "local-gpu", [Completion(text_parts=("local took over",))], local=True
    )

    enforcement, loop, _ = build(
        tmp_path,
        substrates={
            "local-gpu": local,
            "eu-cluster": ScriptedSubstrate("eu"),
            "frontier": frontier,
        },
    )
    result = loop.run("summarise the public tender")

    assert frontier.calls == 1
    assert local.calls == 1
    assert result.response == "local took over"
    assert not enforcement.registry.is_up("frontier"), "the dead substrate was marked down"


def test_the_router_raises_rather_than_inventing_an_answer(tmp_path):
    """A held step must not be papered over with fabricated model text."""
    enforcement = Enforcement.for_run(
        policy=parse_policy(policy_document(tmp_path)),
        ledger_path=tmp_path / "ledger.jsonl",
        backends={
            "local-gpu": ScriptedSubstrate("local-gpu", local=True, fail=True),
            "eu-cluster": ScriptedSubstrate("eu", fail=True),
            "frontier": ScriptedSubstrate("frontier", fail=True),
        },
        probe=False,
        fsync=False,
    )
    enforcement.working_set.observe("seed", SensitivityClass.RESTRICTED)

    with pytest.raises(PlacementHeldError):
        enforcement.backend().complete(CompletionRequest(system="", transcript=()))


# ── Briefs ────────────────────────────────────────────────────────────────────


def test_a_brief_is_produced_locally_reclassified_and_only_then_crosses(tmp_path):
    """Two-tier reasoning, with the second tier gated on a fresh classification."""
    frontier = ScriptedSubstrate("frontier", [Completion(text_parts=("frontier reasoned",))])
    local = ScriptedSubstrate(
        "local-gpu",
        [Completion(text_parts=("A client asks about a deadline. No identifiers.",))],
        local=True,
    )

    enforcement = Enforcement.for_run(
        policy=parse_policy(
            {
                **policy_document(tmp_path, brief=True),
                "rules": [
                    {
                        "id": "R-restricted",
                        "match": {"class": "restricted"},
                        "allow": ["local-gpu"],
                        "on_unavailable": "hold",
                    },
                    {
                        "id": "R-internal",
                        "match": {"class": "internal"},
                        "allow": ["eu-cluster"],
                        "on_unavailable": "brief",
                    },
                    {
                        "id": "R-public",
                        "match": {"class": "public"},
                        "allow": ["frontier", "local-gpu"],
                        "prefer": "quality",
                    },
                ],
            }
        ),
        ledger_path=tmp_path / "ledger.jsonl",
        backends={
            "local-gpu": local,
            "eu-cluster": ScriptedSubstrate("eu", fail=True),
            "frontier": frontier,
        },
        probe=False,
        fsync=False,
    )
    enforcement.registry.mark_down("eu-cluster", "outage")
    enforcement.working_set.observe(f"{tmp_path}/work/x.md", SensitivityClass.INTERNAL)

    completion = enforcement.backend().complete(
        CompletionRequest(system="answer the question", transcript=())
    )

    assert completion.text == "frontier reasoned"
    assert local.calls == 1, "the brief was written on-prem"
    assert "BRIEF" in local.received[0], "the local model was told what it was producing"
    assert frontier.received[0].endswith("A client asks about a deadline. No identifiers.")


def test_a_brief_that_still_carries_identifiers_is_held(tmp_path):
    """The instruction is the cheap layer; the reclassification is the control."""
    frontier = ScriptedSubstrate("frontier")
    local = ScriptedSubstrate(
        "local-gpu",
        [Completion(text_parts=("Il cliente RSSMRA85T10A562S chiede una proroga",))],
        local=True,
    )

    enforcement = Enforcement.for_run(
        policy=parse_policy(
            {
                **policy_document(tmp_path, brief=True),
                "rules": [
                    {
                        "id": "R-restricted",
                        "match": {"class": "restricted"},
                        "allow": ["local-gpu"],
                        "on_unavailable": "hold",
                    },
                    {
                        "id": "R-internal",
                        "match": {"class": "internal"},
                        "allow": ["eu-cluster"],
                        "on_unavailable": "brief",
                    },
                    {"id": "R-public", "match": {"class": "public"}, "allow": ["frontier"]},
                ],
            }
        ),
        ledger_path=tmp_path / "ledger.jsonl",
        backends={
            "local-gpu": local,
            "eu-cluster": ScriptedSubstrate("eu", fail=True),
            "frontier": frontier,
        },
        probe=False,
        fsync=False,
    )
    enforcement.registry.mark_down("eu-cluster", "outage")
    enforcement.working_set.observe(f"{tmp_path}/work/x.md", SensitivityClass.INTERNAL)

    with pytest.raises(PlacementHeldError, match="still restricted"):
        enforcement.backend().complete(CompletionRequest(system="", transcript=()))

    assert frontier.calls == 0


# ── Tools ─────────────────────────────────────────────────────────────────────


def test_an_unknown_tool_is_refused_because_the_policy_never_named_it(tmp_path):
    class OneMoreTool(FileTools):
        def specs(self):
            return super().specs() + (
                ToolSpec(name="shell", description="run a command", schema={"type": "object"}),
            )

        def invoke(self, call):
            self.executed.append(call.name)
            return ToolResult(call_id=call.id, name=call.name, content="ran")

    frontier = ScriptedSubstrate(
        "frontier",
        [
            Completion(
                tool_calls=(ToolCall(id="c1", name="shell", arguments={"command": "ls"}),),
                stop_reason="tool_use",
            ),
            Completion(text_parts=("done",)),
        ],
    )
    enforcement = Enforcement.for_run(
        policy=parse_policy(policy_document(tmp_path)),
        ledger_path=tmp_path / "ledger.jsonl",
        backends={
            "local-gpu": ScriptedSubstrate("l", local=True),
            "eu-cluster": ScriptedSubstrate("e"),
            "frontier": frontier,
        },
        probe=False,
        fsync=False,
    )
    tools = OneMoreTool({})
    loop = AgentLoop(enforcement.backend(), enforcement.executor(tools), enforcement.gate())
    result = loop.run("list the directory")

    assert tools.executed == [], "the tool never ran"
    assert "Permission denied" in str(result.tool_calls[0].result)


def test_a_denied_path_does_not_taint_the_run(tmp_path):
    """A hostile plan must not be able to escalate a run by asking for files."""
    secret = tmp_path / "secrets" / "id_rsa"
    secret.parent.mkdir(parents=True)
    secret.write_text("PRIVATE KEY")

    frontier = ScriptedSubstrate(
        "frontier", read_file_then_answer(str(secret)) + [Completion(text_parts=("ok",))]
    )
    enforcement, loop, tools = build(
        tmp_path,
        substrates={
            "local-gpu": ScriptedSubstrate("l", local=True),
            "eu-cluster": ScriptedSubstrate("e"),
            "frontier": frontier,
        },
        files={str(secret): "PRIVATE KEY"},
    )
    loop.run("read the key")

    assert tools.executed == []
    assert (
        enforcement.klass is SensitivityClass.PUBLIC
    ), "a refused read must not raise the class, or refusals become an escalation"


# ── T7 · the record ───────────────────────────────────────────────────────────


def test_every_decision_of_a_run_is_in_the_ledger_and_the_chain_verifies(tmp_path):
    client_file = tmp_path / "clients" / "x.pdf"
    client_file.parent.mkdir(parents=True)
    client_file.write_text("cliente RSSMRA85T10A562S")

    frontier = ScriptedSubstrate("frontier", read_file_then_answer(str(client_file)))
    local = ScriptedSubstrate("local-gpu", [Completion(text_parts=("done",))], local=True)

    enforcement, loop, _ = build(
        tmp_path,
        substrates={"local-gpu": local, "eu-cluster": ScriptedSubstrate("e"), "frontier": frontier},
        files={str(client_file): "cliente RSSMRA85T10A562S"},
    )
    loop.run("summarise it")

    entries = enforcement.ledger.entries()
    kinds = {e.kind for e in entries}
    assert {"inference", "tool_call"} <= kinds
    assert verify_file(enforcement.ledger.path).ok

    inferences = [e for e in entries if e.kind == "inference"]
    assert inferences[0].substrate == "frontier"
    assert inferences[-1].substrate == "local-gpu"
    assert inferences[-1].klass == "restricted"


def test_tampering_with_the_record_of_a_real_run_is_detected(tmp_path):
    enforcement, loop, _ = build(
        tmp_path,
        substrates={
            "local-gpu": ScriptedSubstrate("l", local=True),
            "eu-cluster": ScriptedSubstrate("e"),
            "frontier": ScriptedSubstrate("frontier"),
        },
    )
    loop.run("a public question")

    path = enforcement.ledger.path
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    rows[0]["substrate"] = "local-gpu"
    path.write_text(
        "\n".join(json.dumps(r, sort_keys=True, separators=(",", ":")) for r in rows) + "\n"
    )

    result = verify_file(path)
    assert not result.ok
    assert result.at_seq == 1


# ── T8 · the air gap ──────────────────────────────────────────────────────────


def test_with_every_remote_substrate_down_local_work_still_runs(tmp_path):
    """Pull the uplink: the machine keeps working, on-prem only."""
    local = ScriptedSubstrate(
        "local-gpu", [Completion(text_parts=("answered offline",))], local=True
    )

    enforcement = Enforcement.for_run(
        policy=parse_policy(
            {
                **policy_document(tmp_path),
                "rules": [
                    {
                        "id": "R-restricted",
                        "match": {"class": "restricted"},
                        "allow": ["local-gpu"],
                    },
                    {"id": "R-internal", "match": {"class": "internal"}, "allow": ["local-gpu"]},
                    {
                        "id": "R-public",
                        "match": {"class": "public"},
                        "allow": ["frontier", "local-gpu"],
                        "prefer": "privacy",
                    },
                ],
            }
        ),
        ledger_path=tmp_path / "ledger.jsonl",
        backends={
            "local-gpu": local,
            "eu-cluster": ScriptedSubstrate("eu", fail=True),
            "frontier": ScriptedSubstrate("frontier", fail=True),
        },
        probe=False,
        fsync=False,
    )
    loop = AgentLoop(enforcement.backend(), enforcement.executor(FileTools({})), enforcement.gate())

    assert loop.run("what is the deadline?").response == "answered offline"
