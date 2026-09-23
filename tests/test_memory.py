"""The company's memory, and what a tool result may carry into a run.

Two groups of promises:

- the index (``runner.memory``): paragraphs stay whole, unchanged files are not
  re-embedded, removed files leave the index, and the automatic lookup matches
  names and codes, not ordinary words — so a public question is not sealed just
  because it shares "contratto" with the memory;
- provenance: whatever arrives in a tool result — a canary, the path of a
  restricted file — classifies the run exactly as if it were in the prompt.
  Found while wiring the memory: the router rendered tool results as object
  reprs, so a secret that appeared only in a result was invisible to it.
"""

from __future__ import annotations

import re

import numpy as np
from datapizza.tools import Tool

from runner.audit.ledger import Ledger, read_entries
from runner.kernel.blocks import ToolResultBlock, block_text
from runner.kernel.types import Completion, SensitivityClass, ToolCall, ToolResult
from runner.memory import MemoryIndex
from runner.memory.index import chunk
from runner.policy.classifier import PolicyClassifier, WorkingSet
from runner.policy.loader import parse_policy
from runner.policy.tracking import TrackingExecutor
from tests.test_enforcement import CANARY, ScriptedSubstrate, build, policy_document

# ── A deterministic embedder: bag of words over a fixed vocabulary ───────────

VOCAB = ["nordika", "veloce", "partner", "contratto", "nda", "probe", "card", "mems", "clausola"]


def fake_embed(texts):
    out = []
    for text in texts:
        words = re.findall(r"\w+", text.lower())
        vec = np.array([words.count(v) for v in VOCAB], dtype=np.float32) + 1e-3
        out.append(vec.tolist())
    return out


def read_text(path):
    return path.read_text(encoding="utf-8")


def memory(tmp_path):
    folder = tmp_path / "Memoria"
    folder.mkdir()
    (folder / "verbale.md").write_text(
        "Veloce comunica che Nordika è partner diretto sulla piattaforma.\n\n"
        "Clausola 7.3 dell'NDA: avvisare prima.",
        encoding="utf-8",
    )
    (folder / "anagrafica.md").write_text("Veloce Automotive, cliente dal 2014.", encoding="utf-8")
    (folder / ".annona-cache").mkdir()
    (folder / ".annona-cache" / "copy.json").write_text("Nordika Nordika Nordika", encoding="utf-8")
    index = MemoryIndex(tmp_path / "index.sqlite")
    index.build([f"{folder}/**"], fake_embed, read_text, model="fake", endpoint="-")
    return index, folder


# ── The index ────────────────────────────────────────────────────────────────


def test_paragraphs_are_kept_whole_where_they_fit():
    text = "Primo paragrafo breve.\n\nSecondo paragrafo breve."
    assert chunk(text, size=200) == ["Primo paragrafo breve.\n\nSecondo paragrafo breve."]
    long = " ".join(["parola"] * 200)
    assert all(len(piece) <= 250 for piece in chunk(long, size=250, overlap=20))


def test_hidden_caches_are_not_indexed(tmp_path):
    index, _ = memory(tmp_path)
    assert index.stats()["documents"] == 2


def test_unchanged_files_are_skipped_and_removed_files_forgotten(tmp_path):
    index, folder = memory(tmp_path)
    again = index.build([f"{folder}/**"], fake_embed, read_text, model="fake", endpoint="-")
    assert again == {"indexed": 0, "unchanged": 2, "removed": 0}

    (folder / "anagrafica.md").unlink()
    after = index.build([f"{folder}/**"], fake_embed, read_text, model="fake", endpoint="-")
    assert after["removed"] == 1
    assert index.stats()["documents"] == 1


def test_a_name_finds_the_memory_that_mentions_it(tmp_path):
    index, _ = memory(tmp_path)
    hits = index.search("Prepara la bozza di contratto per Nordika", fake_embed, strict=True)
    assert any(h.path.endswith("verbale.md") for h in hits)
    assert all("/.annona-cache/" not in h.path for h in hits)


def test_an_ordinary_question_does_not_pull_the_memory(tmp_path):
    """A common word ("cliente") shared with the memory must not retrieve — and so
    seal — a public question. Names and codes only, and a similarity floor."""
    index, _ = memory(tmp_path)
    assert (
        index.search("Spiegami cos'è una probe card, per un cliente", fake_embed, strict=True) == []
    )


# ── Provenance: what a tool result carries ───────────────────────────────────


def test_a_tool_result_renders_as_what_it_returned():
    block = ToolResultBlock(
        id="r1", tool=Tool(name="memory_search", description="-"), result="Veloce /a/b.docx"
    )
    assert block_text(block) == "Veloce /a/b.docx"


def ask_then_answer(result_path: str) -> list[Completion]:
    return [
        Completion(
            tool_calls=(
                ToolCall(id="c1", name="document_reader", arguments={"path": result_path}),
            ),
            stop_reason="tool_use",
        ),
        Completion(text_parts=("done",), stop_reason="end_turn"),
    ]


def run_with_result(tmp_path, content: str):
    """A public file (no class by path) whose *content* is the only signal."""
    note = tmp_path / "notes" / "memo.txt"
    note.parent.mkdir(parents=True)
    note.write_text(content)
    frontier = ScriptedSubstrate("frontier", ask_then_answer(str(note)))
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
        files={str(note): content},
    )
    loop.run("riassumi la nota")
    return frontier, local


def test_a_canary_that_arrives_only_in_a_tool_result_never_reaches_the_frontier(tmp_path):
    frontier, local = run_with_result(tmp_path, f"verbale interno {CANARY}")
    assert not any(CANARY in payload for payload in frontier.received)
    assert local.calls == 1


def test_a_restricted_path_named_in_a_tool_result_keeps_the_next_turn_local(tmp_path):
    """What `memory_search` does: a retrieved passage names the file it came from."""
    source = tmp_path / "clients" / "verbale.docx"
    frontier, local = run_with_result(tmp_path, f"fonte: {source}\nNordika è partner di Veloce.")
    assert frontier.calls == 1, "the first turn was public; the second must not be"
    assert local.calls == 1


# ── The graph: who is whose partner ──────────────────────────────────────────


def fake_facts(text):
    """What a local model would extract from the two memory files."""
    facts = []
    if "Nordika" in text:
        facts.append(
            {
                "subject": "Nordika Mobility GmbH",
                "relation": "partner_of",
                "object": "Veloce Automotive S.p.A.",
                "evidence": "Nordika è partner diretto sulla piattaforma.",
            }
        )
    if "Clausola 7.3" in text:
        facts.append(
            {
                "subject": "Veloce Automotive",
                "relation": "bound_by",
                "object": "NDA VA-2019-07 clausola 7.3",
                "evidence": "Clausola 7.3 dell'NDA: avvisare prima.",
            }
        )
    return facts


def test_the_conflict_is_two_hops_from_the_name_in_the_request(tmp_path):
    folder = tmp_path / "Memoria"
    folder.mkdir()
    (folder / "verbale.md").write_text(
        "Nordika è partner diretto sulla piattaforma.\n\nClausola 7.3 dell'NDA: avvisare prima.",
        encoding="utf-8",
    )
    index = MemoryIndex(tmp_path / "index.sqlite")
    index.build(
        [f"{folder}/**"], fake_embed, read_text, model="fake", endpoint="-", facts=fake_facts
    )

    lines = [f.line() for f in index.facts_about("Bozza di contratto per Nordika Mobility")]

    assert "Nordika Mobility GmbH — partner_of → Veloce Automotive S.p.A." in lines
    # Second hop: the request never names Veloce or the NDA.
    assert "Veloce Automotive — bound_by → NDA VA-2019-07 clausola 7.3" in lines
    assert index.facts_about("cos'è una probe card") == []


# ── The ledger records what the memory contributed ───────────────────────────


def test_a_retrieval_is_recorded_with_its_files_not_its_passages(tmp_path):
    class Memory:
        def specs(self):
            return ()

        def invoke(self, call):
            return ToolResult(
                call_id=call.id,
                name=call.name,
                content={"results": [{"text": "segreto"}], "sources": ["/m/verbale.docx"]},
            )

    ledger = Ledger(tmp_path / "ledger.jsonl", run_id="t", fsync=False)
    policy = parse_policy(policy_document(tmp_path))
    tracking = TrackingExecutor(
        Memory(), PolicyClassifier(policy), WorkingSet(SensitivityClass.PUBLIC), ledger
    )

    tracking.invoke(ToolCall(id="m1", name="memory_search", arguments={"query": "Nordika"}))

    entries = [e for e in read_entries(tmp_path / "ledger.jsonl") if e.kind == "retrieval"]
    assert len(entries) == 1
    assert entries[0].detail["paths"] == ["/m/verbale.docx"]
    assert "segreto" not in (tmp_path / "ledger.jsonl").read_text()
