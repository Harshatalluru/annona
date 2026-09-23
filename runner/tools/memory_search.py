"""memory_search — ask the company's memory before answering.

The index is built by ``annona memory index`` from the folders the policy names
(see ``runner.memory``). This tool only reads it, with the same local embedder
the index was built with: the model name and endpoint are stored in the index,
so a search can never be answered by a different embedder than the one the
policy approved.

Each result carries the absolute path of its source. The router classifies every
path in an outbound payload, so a passage from a sealed folder seals the run —
the provenance of a retrieved passage is enforced by the same code as a file read.
"""

from typing import Any, Dict, List, Optional

from runner.memory import MemoryIndex, default_index_path
from runner.memory.index import ollama_embedder

from .base import Tool


class MemorySearchTool(Tool):
    """Hybrid search over the company's memory, on this machine."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(
            name="memory_search",
            description=(
                "Search the company's historical memory (customers, partners, contracts, NDAs, "
                "meeting minutes, management notes) for passages relevant to a question. "
                "Use it before drafting a contract or an offer, or whenever the answer depends "
                "on who the company already works with. Returns passages with their source file."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to look for: names of companies or people, topics, codes",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Passages to return (default 6, max 20)",
                    },
                    "strict": {
                        "type": "boolean",
                        "description": "Match words only as names and codes (used by the automatic lookup)",
                    },
                },
                "required": ["query"],
            },
        )

    def execute(
        self,
        query: str,
        top_k: int = 6,
        strict: bool = False,
        within: Optional[List[str]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """``within`` is not advertised to the model: the perimeter sets it to the
        folders this run's subject may retrieve from, overwriting anything else."""
        path = default_index_path()
        if not path.exists():
            return {
                "success": False,
                "error": "no memory index on this machine: run `annona memory index`",
            }
        index = MemoryIndex(path)
        try:
            embed = ollama_embedder(index.meta("endpoint"), index.meta("model"))
            hits = index.search(
                query, embed, k=max(1, min(int(top_k), 20)), strict=bool(strict), within=within
            )
            facts = index.facts_about(query, within=within)
        finally:
            index.close()
        return {
            "success": True,
            "query": query,
            # Relations first: they are the answer to "is there a conflict?", the
            # passages are the evidence to read.
            "facts": [{"fact": f.line(), "evidence": f.evidence, "source": f.path} for f in facts],
            "results": [{"source": h.path, "text": h.text, "score": h.score} for h in hits],
            # Declared for the ledger: which files this answer drew on.
            "sources": sorted({h.path for h in hits} | {f.path for f in facts}),
        }
