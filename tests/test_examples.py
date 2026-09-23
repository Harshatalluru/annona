"""The examples do what their READMEs say, with their real policies and documents.

Each example's ``policy.template.yaml`` is rendered the way ``setup.sh`` renders
it, and each outcome its README promises becomes a test. Substrates and the
redactor are doubles — no model, no network — so this runs in CI on every push:
an example that stops behaving as documented turns the build red.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from runner.agent.loop import AgentLoop
from runner.kernel.types import Completion, SensitivityClass, ToolCall, ToolResult, ToolSpec
from runner.memory import MemoryIndex
from runner.policy.loader import parse_policy
from runner.policy.redaction import Redaction
from runner.services.enforcement import Enforcement
from runner.tools.extractors.registry import extract
from tests.test_enforcement import ScriptedSubstrate

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
CF = "VRDPLA72D15F704Y"
IBAN = "IT60X0542811101000000123456"


def render(example: str, tmp_path: Path) -> dict:
    """The policy exactly as setup.sh writes it, with the direct-API frontier."""
    text = (EXAMPLES / example / "policy.template.yaml").read_text(encoding="utf-8")
    text = re.sub(r"#VERTEX\n.*?#/VERTEX\n", "", text, flags=re.S)
    text = text.replace("#ANTHROPIC\n", "").replace("#/ANTHROPIC\n", "")
    for key, value in {
        "__PRATICHE__": str(EXAMPLES / example / "Pratiche"),
        "__INBOX__": str(tmp_path / "inbox"),
        "__MODEL__": "qwen2.5:14b",
        "__PUBLIC__": "frontier, local-gpu",
    }.items():
        text = text.replace(key, value)
    return yaml.safe_load(text)


class Tools:
    """document_reader over the example's real files, and memory_search over an index."""

    def __init__(self, index: MemoryIndex | None = None, embed=None):
        self._index, self._embed = index, embed

    def specs(self):
        return (
            ToolSpec(name="document_reader", description="-", schema={"type": "object"}),
            ToolSpec(name="memory_search", description="-", schema={"type": "object"}),
        )

    def invoke(self, call: ToolCall) -> ToolResult:
        if call.name == "document_reader":
            return ToolResult(call.id, call.name, extract(call.arguments["path"]).text)
        hits = self._index.search(call.arguments["query"], self._embed, strict=True)
        facts = self._index.facts_about(call.arguments["query"])
        return ToolResult(
            call.id,
            call.name,
            {
                "facts": [{"fact": f.line(), "source": f.path} for f in facts],
                "results": [{"source": h.path, "text": h.text} for h in hits],
                "sources": sorted({h.path for h in hits} | {f.path for f in facts}),
            },
        )


def perimeter(tmp_path, example, *, local, frontier, tools, redactor=None):
    enforcement = Enforcement.for_run(
        policy=parse_policy(render(example, tmp_path)),
        ledger_path=tmp_path / "ledger.jsonl",
        backends={"local-gpu": local, "frontier": frontier},
        redactor=redactor,
        probe=False,
        fsync=False,
        run_id="example",
    )
    loop = AgentLoop(enforcement.backend(), enforcement.executor(tools), enforcement.gate())
    return enforcement, loop


def held(tmp_path) -> list[str]:
    """Why the run was held, from the ledger. The loop does not raise on a hold:
    it records it and returns an empty answer, and the ledger is the proof."""
    entries = [json.loads(line) for line in (tmp_path / "ledger.jsonl").read_text().splitlines()]
    return [e["detail"].get("reason", "") for e in entries if e["outcome"] == "held"]


def answers(text="ok"):
    return [Completion(text_parts=(text,), stop_reason="end_turn")]


# ── rfq-orione ───────────────────────────────────────────────────────────────

ORIONE = EXAMPLES / "rfq-orione" / "Pratiche" / "Progetto-Orione"


def triage_prompt():
    return f"Nella cartella {ORIONE} leggi la mail e la specifica e confronta i requisiti."


def test_orione_the_rfq_stays_on_the_local_gpu(tmp_path):
    local, frontier = (
        ScriptedSubstrate("local-gpu", answers(), local=True),
        ScriptedSubstrate("frontier"),
    )
    enforcement, loop = perimeter(
        tmp_path, "rfq-orione", local=local, frontier=frontier, tools=Tools()
    )

    loop.run(triage_prompt())

    assert local.calls == 1 and frontier.calls == 0
    assert enforcement.klass is SensitivityClass.RESTRICTED
    assert "Orione" in enforcement.working_set.sealed


def test_orione_with_the_gpu_down_the_rfq_is_held_not_sent(tmp_path):
    local = ScriptedSubstrate("local-gpu", local=True, fail=True)
    frontier = ScriptedSubstrate("frontier", answers("from the cloud"))
    _, loop = perimeter(tmp_path, "rfq-orione", local=local, frontier=frontier, tools=Tools())

    result = loop.run(triage_prompt())

    assert result.response == ""
    assert any("sealed" in reason for reason in held(tmp_path))
    assert frontier.calls == 0, "the cloud was up and received nothing"


class Redactor:
    name = "fake-rizzo"

    def analyse(self, text: str) -> Redaction:
        return Redaction(
            text=text.replace(CF, "[CF_1]")
            .replace(IBAN, "[IBAN_1]")
            .replace("Paolo Verdi", "[FULLNAME_1]"),
            mapping={"[CF_1]": CF, "[IBAN_1]": IBAN, "[FULLNAME_1]": "Paolo Verdi"},
            labels={"CF": 1, "IBAN": 1, "FULLNAME": 1},
        )


def test_orione_the_supplier_email_leaves_only_redacted(tmp_path):
    email = extract(
        EXAMPLES
        / "rfq-orione"
        / "Pratiche"
        / "Accrediti"
        / "Accredito_fornitore_Brianza_Tecnica.eml"
    ).text
    assert CF in email
    local = ScriptedSubstrate("local-gpu", local=True, fail=True)
    frontier = ScriptedSubstrate("frontier", answers("visura e DURC"))
    _, loop = perimeter(
        tmp_path, "rfq-orione", local=local, frontier=frontier, tools=Tools(), redactor=Redactor()
    )

    loop.run(f"Che documenti servono per l'accredito?\n\n{email}")

    assert frontier.calls == 1
    assert "[CF_1]" in frontier.received[0]
    for identifier in (CF, IBAN, "Paolo Verdi"):
        assert identifier not in frontier.received[0]


def test_orione_a_public_question_goes_to_the_best_model(tmp_path):
    local, frontier = (
        ScriptedSubstrate("local-gpu", local=True),
        ScriptedSubstrate("frontier", answers()),
    )
    _, loop = perimeter(tmp_path, "rfq-orione", local=local, frontier=frontier, tools=Tools())

    loop.run("Spiegami in tre righe cos'è una probe card MEMS.")

    assert frontier.calls == 1 and local.calls == 0


# ── memoria-storica ──────────────────────────────────────────────────────────

MEMORY = EXAMPLES / "memoria-storica" / "Pratiche"
VOCAB = ["nordika", "veloce", "partner", "contratto", "nda", "kappa", "clausola", "bms"]


def embed(texts):
    """Bag of words over VOCAB, plus one axis of its own for text that shares none
    of it — so an unrelated question points away from every memory passage."""
    out = []
    for text in texts:
        words = re.findall(r"\w+", text.lower())
        counts = [float(words.count(v)) for v in VOCAB]
        out.append([*counts, 0.0 if any(counts) else 1.0])
    return out


def facts(text):
    """What the local model extracts from the example's minutes (checked live)."""
    if "partner commerciale diretto" not in text:
        return []
    return [
        {
            "subject": "Veloce Automotive",
            "relation": "partner_of",
            "object": "Nordika Mobility GmbH",
            "evidence": "partner commerciale diretto",
        },
        {
            "subject": "Veloce Automotive",
            "relation": "bound_by",
            "object": "clausola 7.3 dell'NDA VA-2019-07",
            "evidence": "clausola 7.3",
        },
    ]


@pytest.fixture
def memory(tmp_path):
    index = MemoryIndex(tmp_path / "memory.sqlite")
    index.build(
        [f"{MEMORY}/Memoria-Storica/**", f"{MEMORY}/Commerciale/**"],
        embed,
        lambda p: extract(p).text,
        model="fake",
        endpoint="-",
        facts=facts,
    )
    return index


def ask(loop, prompt):
    """As the kernel API does it: a strict memory lookup before the first turn."""
    return loop.run(
        prompt,
        prefetch=(ToolCall(id="memory_0", name="memory_search", arguments={"query": prompt}),),
    )


NORDIKA = "Prepara la bozza di contratto per Nordika Mobility, come chiesto dal CEO."


def test_memoria_the_memory_seals_a_request_that_names_no_file(tmp_path, memory):
    local, frontier = (
        ScriptedSubstrate("local-gpu", answers(), local=True),
        ScriptedSubstrate("frontier"),
    )
    enforcement, loop = perimeter(
        tmp_path, "memoria-storica", local=local, frontier=frontier, tools=Tools(memory, embed)
    )

    ask(loop, NORDIKA)

    assert enforcement.klass is SensitivityClass.RESTRICTED
    assert "Veloce" in enforcement.working_set.sealed
    assert local.calls == 1 and frontier.calls == 0
    assert "partner_of" in local.received[0], "the relation reached the local model"


def test_memoria_with_the_gpu_down_the_request_is_held(tmp_path, memory):
    local = ScriptedSubstrate("local-gpu", local=True, fail=True)
    frontier = ScriptedSubstrate("frontier", answers("ecco la bozza"))
    _, loop = perimeter(
        tmp_path, "memoria-storica", local=local, frontier=frontier, tools=Tools(memory, embed)
    )

    result = ask(loop, NORDIKA)

    assert result.response == ""
    assert any("sealed" in reason for reason in held(tmp_path))
    assert frontier.calls == 0


def test_memoria_an_unrelated_question_does_not_touch_the_memory(tmp_path, memory):
    local, frontier = (
        ScriptedSubstrate("local-gpu", local=True),
        ScriptedSubstrate("frontier", answers()),
    )
    enforcement, loop = perimeter(
        tmp_path, "memoria-storica", local=local, frontier=frontier, tools=Tools(memory, embed)
    )

    ask(loop, "Spiegami cos'è un banco di test per batterie.")

    assert enforcement.working_set.sealed == ""
    assert frontier.calls == 1


def test_memoria_the_ledger_names_the_files_never_the_passages(tmp_path, memory):
    local, frontier = (
        ScriptedSubstrate("local-gpu", answers(), local=True),
        ScriptedSubstrate("frontier"),
    )
    _, loop = perimeter(
        tmp_path, "memoria-storica", local=local, frontier=frontier, tools=Tools(memory, embed)
    )

    ask(loop, NORDIKA)

    ledger = (tmp_path / "ledger.jsonl").read_text()
    assert "Verbale_Veloce_2026-03-12.docx" in ledger
    assert "partner commerciale diretto" not in ledger


# ── The scripts themselves ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "script", sorted(str(p.relative_to(EXAMPLES)) for p in EXAMPLES.glob("*/*.sh"))
)
def test_every_example_script_parses(script):
    """`bash -n` on every setup/run/stop. An apostrophe inside ${1:?…} once broke
    setup.sh for every example, and nothing else would have noticed before a
    person ran it on a new machine."""
    result = subprocess.run(["bash", "-n", str(EXAMPLES / script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
