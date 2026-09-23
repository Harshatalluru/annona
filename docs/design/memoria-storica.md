# Memoria storica — a sovereign retrieval layer (proposal)

Status: proposal, 2026-09-23. Nothing here is implemented yet.

## Why

Rehearsing the Orione demo made the gap concrete. A CEO asks sales for a draft
contract for Nordika Mobility. The company's memory says Nordika is the direct
partner of Veloce Automotive, a customer since 2014 worth 38% of revenue, and the
NDA with Veloce (clause 7.3) requires notice before working for its partners.

With the four memory files named in the prompt, a local 14B finds the conflict,
refuses to draft, and writes a note for the CEO. Without the names it opens the
folder, reads the CEO's email and drafts the contract. **Today the agent only
knows what it thinks to go and read.** A small model does not think of it.

A frontier model would not find it either, for a different reason: it has never
seen the memory, and the policy must never let it. The memory of a company —
who is whose partner, what was promised in which meeting, what the accountant
knows — is the one corpus that is valuable *because* it stays in.

What exists today:

| Piece | What it does | Gap |
|---|---|---|
| `explorer` tool | map, find, grep inside allowed folders | the model has to decide to use it, and grep needs the right word |
| `document_reader` + extraction cache | reads 12 format families, content-addressed cache | no index across documents |
| Notes vault (`runner/brain`) | SQLite FTS5 over the user's notes | notes only, not the company's files |
| Sync to Akaion cloud | embeddings on the cloud side (COT) | the opposite of sovereign; off by default |

## What

A local index of the folders the policy names as memory, queried automatically
before the first turn and on demand through a gated tool, whose every result
carries the class and seal of the document it came from.

```
folders named in policy.memory ──► extract (runner extractors) ──► split ──► embed (local)
                                        │                                       │
                                        └── entities & relations (local LLM) ──┤
                                                                                ▼
                                      index in $ANNONA_HOME/memory (never synced)
                                                                                │
      request ──► prefetch: hybrid search + entity lookup ──► chunks + provenance
                                                                                │
                         working set raised to the highest class/seal retrieved ◄┘
                                                                                │
                                         placement as today (local if restricted)
```

### Built from parts we already trust

| Layer | Choice | Why |
|---|---|---|
| Pipeline | `datapizza-ai` `IngestionPipeline` / `DagPipeline` (MIT) | same vocabulary as the runner (ADR 0001); composable, no framework loop |
| Parsing | Annona's own extractors first; `datapizza-ai-parsers-docling` (Docling, IBM Research Zurich, MIT) for layout-heavy PDFs and tables | one reading path, so what is indexed is exactly what a tool read would return |
| Splitting | `datapizza` `NodeSplitter` | — |
| Embeddings | local only: `bge-m3` through Ollama, or FastEmbed through `datapizza-ai-embedders` | multilingual (Italian documents), runs on the same GPU; a cloud embedder would be an egress of the whole corpus |
| Vector store | Qdrant local mode (`datapizza-ai-vectorstores`, Apache-2.0), or `sqlite-vec` for zero extra services | on disk under `$ANNONA_HOME`, same backup and permissions as the ledger |
| Lexical | SQLite FTS5, already used by the notes vault | names, codes and clause numbers (`VA-2019-07`) are found by words, not by meaning |
| Fusion | reciprocal rank fusion of FTS5 and vectors | robust without tuning |
| Rerank | local cross-encoder (`bge-reranker-v2-m3` via ONNX) | the documented datapizza rerankers are cloud APIs: not usable here |
| Entities | a small graph extracted at ingestion by the local model: company, person, contract, clause, `partner_of`, `customer_since`, `bound_by` | the conflict is a two-hop question (Nordika → partner of → Veloce → bound by → NDA 7.3); a graph answers it deterministically, top-k chunks might not |
| PII | rizzo-pii stays where it is: at egress, not at indexing | the index never leaves, so redacting it would only lose information |

### What makes it Annona and not another RAG

1. **Provenance is a class.** Every chunk and every graph edge stores the digest,
   path, class and seal of its source. A retrieval result is a tool result: it
   taints the working set exactly like reading the file would. Asking about
   Nordika pulls in the Veloce minutes, and the run becomes restricted and
   sealed before the first token is placed.
2. **Derived data inherits.** The index is a copy of the corpus in another shape,
   so it has the corpus's class. It lives under `$ANNONA_HOME`, is never synced,
   and `shared-context.md`'s rule applies: no shared index across colleagues until
   there is identity.
3. **Gated like any tool.** `memory_search` is default-deny, allow-listed per
   folder in the policy; the prefetch is a policy switch, not a hidden behaviour.
4. **In the ledger.** Each retrieval records query digest and chunk digests, so
   `annona why` can say "held because of `Verbale_Veloce_2026-03-12.docx` §2".
5. **Humans decide.** Skills like `conflict-check` stop and hand the decision to a
   person; the memory makes the stop happen, it never makes the decision.

### Policy shape

```yaml
memory:
  folders:
    - path: ~/Azienda/Memoria-Storica/**
    - path: ~/Azienda/Commerciale/**
  embed_with: local-gpu          # a substrate; must be max_class >= the folders' class
  prefetch: true                 # search before the first turn
  entities: true                 # build the partner/customer/contract graph
tools:
  allow:
    memory_search: [~/Azienda/**]
```

`embed_with` naming a substrate is what keeps the embedder under placement: the
loader refuses a memory whose embedder could not hold the folders' class.

## Plan

| Step | Scope | Estimate |
|---|---|---|
| 1 | Index + `memory_search` tool: extractors → split → Ollama `bge-m3` → `sqlite-vec` + FTS5, RRF, provenance taint, ledger entries | 2 days |
| 2 | Prefetch before the first turn, driven by policy; UI shows "retrieved from memory" with sources | 1 day |
| 3 | Entity graph at ingestion + `conflict-check` skill using it | 2 days |
| 4 | Qdrant / Docling / reranker as optional extras through datapizza | 1 day |
| 5 | Eval with `datapizza-ai-eval` on the Orione and automotive cases: recall of the conflict with the file names removed from the prompt | 1 day |

Acceptance for step 1–2: with the prompt «Prepara la bozza di contratto per
Nordika Mobility» and no file names, a local 14B refuses and cites the Veloce
minutes and NDA 7.3, and the ledger shows the run sealed by the memory.
