"""A hybrid index — words and meaning — over the company's memory, all on this machine.

Two searches, fused:

- **FTS5** finds names, codes and clause numbers (``Nordika``, ``VA-2019-07``),
  which is what a conflict usually hangs on and what embeddings are worst at;
- **vectors** from a local embedder find the passage that says the same thing in
  other words ("partner commerciale diretto" for "lavorano insieme").

They are fused by reciprocal rank, which needs no tuning and no score scale.

Every chunk keeps the absolute path of the file it came from, and a search
result carries it. That is the whole provenance mechanism: the router classifies
every path named in an outbound payload, so a retrieved passage raises and seals
the run exactly as reading the file would have. The index adds no new way for
material to change class.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import numpy as np

__all__ = [
    "Fact",
    "Hit",
    "MemoryIndex",
    "chunk",
    "default_index_path",
    "ollama_embedder",
    "ollama_fact_extractor",
]

Embedder = Callable[[Sequence[str]], list[list[float]]]
Reader = Callable[[Path], str]
FactExtractor = Callable[[str], list[dict[str, str]]]
"""Relations stated in one document: ``subject``, ``relation``, ``object``, ``evidence``."""
"""Text of one file. Passed in by the caller (the CLI uses the runner's extractors),
so the index depends on no tool and can be imported by one."""

CHUNK_CHARS = 900
OVERLAP_CHARS = 150
CANDIDATES = 30
RRF_K = 60
MIN_SIMILARITY = 0.5
"""Below this cosine a passage is not about the question, it is merely the nearest.

Retrieval that always returns *something* would pull sealed memory into every
run — including a public question that has nothing to do with it — and seal it.
Calibrated on bge-m3 with the Orione fixtures; see tests/test_memory.py."""

STOPWORDS = frozenset(
    "per una uno che con del della delle dei degli nel nella nelle sul sulla sono come cosa "
    "cos questo questa quello quella più anche non tra fra dal dalla alle agli gli the and for "
    "with that this from are was what how tre due righe fammi dimmi prima dopo".split()
)


def default_index_path() -> Path:
    """Beside the policy and the ledger: ``$ANNONA_HOME/memory/index.sqlite``."""
    home = os.getenv("ANNONA_HOME")
    base = Path(home).expanduser() if home else Path.home() / ".annona"
    return base / "memory" / "index.sqlite"


def ollama_embedder(endpoint: str, model: str, timeout: float = 120.0) -> Embedder:
    """Embeddings from a local Ollama. Nothing here may call a remote service."""
    url = endpoint.rstrip("/") + "/api/embed"

    def embed(texts: Sequence[str]) -> list[list[float]]:
        response = httpx.post(url, json={"model": model, "input": list(texts)}, timeout=timeout)
        response.raise_for_status()
        return response.json()["embeddings"]

    return embed


RELATIONS = (
    "partner_of",
    "customer_of",
    "supplier_of",
    "competitor_of",
    "joint_venture_with",
    "works_for",
    "bound_by",
    "requires",
    "worth",
)
"""The vocabulary of the memory graph. Small on purpose: a conflict check needs
to follow who works with whom and what binds us, not to model the world."""

_FACT_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "relation": {"type": "string", "enum": list(RELATIONS)},
                    "object": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["subject", "relation", "object", "evidence"],
            },
        }
    },
    "required": ["facts"],
}

_FACT_PROMPT = """Extract the business relationships stated in the document below.
Only what the text states explicitly; never infer. Relations:
- partner_of: two organisations work together commercially (partnership, joint platform)
- customer_of: A buys from B ("Veloce is our customer" -> Veloce customer_of <our company>)
- supplier_of, competitor_of, joint_venture_with, works_for (person -> organisation)
- bound_by: an organisation is bound by a contract, NDA or clause (object = the contract or clause)
- requires: a contract or clause requires an action (object = the action, short)
- worth: a customer's value to us (object = the figure, e.g. "38% del fatturato 2025")
Use full names as written (e.g. "Nordika Mobility GmbH"). "Noi"/"we" is the company
writing the document. evidence = the exact sentence, at most 200 characters.

DOCUMENT:
"""


def ollama_fact_extractor(
    endpoint: str, model: str, company: str = "", timeout: float = 300.0
) -> FactExtractor:
    """Relations extracted by a local model, constrained to a JSON schema."""
    url = endpoint.rstrip("/") + "/api/chat"
    # Without the company's own name the model writes "Noi" in one document and
    # the legal name in the next, and the graph splits one node in two.
    us = (
        f"The company that owns these documents is {company}: write it by that name, "
        'never "noi", "we" or "our company".\n'
        if company
        else ""
    )

    def extract(text: str) -> list[dict[str, str]]:
        response = httpx.post(
            url,
            json={
                "model": model,
                "messages": [{"role": "user", "content": us + _FACT_PROMPT + text[:8000]}],
                "format": _FACT_SCHEMA,
                "stream": False,
                "options": {"temperature": 0},
            },
            timeout=timeout,
        )
        response.raise_for_status()
        data = json.loads(response.json()["message"]["content"])
        return [f for f in data.get("facts", []) if f.get("relation") in RELATIONS]

    return extract


_SUFFIXES = re.compile(r"\b(s\.?p\.?a\.?|s\.?r\.?l\.?|gmbh|b\.?v\.?|inc\.?|ltd\.?|ag)\s*$", re.I)


def entity_key(name: str) -> str:
    """One key per organisation however it is written: case and legal form dropped."""
    core = _SUFFIXES.sub("", name.strip()).strip(" .,")
    return re.sub(r"\s+", " ", core).lower()


def chunk(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    """Paragraph-aligned pieces of about ``size`` characters, with a little overlap.

    Paragraphs are kept whole where they fit, because a clause cut in half is a
    clause the model misreads; an oversized paragraph is cut on spaces.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    # Pieces leave room for the overlap carried into the next chunk, so no chunk
    # ever exceeds ``size``.
    limit = max(1, size - overlap - 2)
    pieces: list[str] = []
    for paragraph in paragraphs:
        while len(paragraph) > limit:
            cut = paragraph.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            pieces.append(paragraph[:cut])
            paragraph = paragraph[max(0, cut - overlap) :]
        pieces.append(paragraph)

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > size:
            chunks.append(current)
            current = current[-overlap:] + "\n\n" + piece if overlap else piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


@dataclass(frozen=True)
class Hit:
    """One retrieved passage and where it came from."""

    path: str
    text: str
    score: float


@dataclass(frozen=True)
class Fact:
    """One relation from the memory graph, with the sentence and file that state it."""

    subject: str
    relation: str
    object: str
    evidence: str
    path: str

    def line(self) -> str:
        return f"{self.subject} — {self.relation} → {self.object}"


def _files(folders: Iterable[str]) -> list[Path]:
    """Every regular file under the policy's folders, hidden ones excluded.

    Hidden directories are where readers keep their caches (``.annona-cache``):
    indexing a cache would index a second copy of the same document.
    """
    found: dict[str, Path] = {}
    for folder in folders:
        root = Path(folder.removesuffix("/**")).expanduser()
        if root.is_file():
            found[str(root.resolve())] = root.resolve()
            continue
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            if path.is_file() and not any(part.startswith(".") for part in relative.parts):
                found[str(path.resolve())] = path.resolve()
    return [found[k] for k in sorted(found)]


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class MemoryIndex:
    """The index on disk. Open it, build it, search it."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else default_index_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS docs (path TEXT PRIMARY KEY, digest TEXT, indexed_at REAL);
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY, path TEXT, ord INTEGER, text TEXT, vec BLOB);
            CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text);
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY, path TEXT, subject TEXT, subject_key TEXT,
                relation TEXT, object TEXT, object_key TEXT, evidence TEXT);
            CREATE INDEX IF NOT EXISTS facts_subject ON facts(subject_key);
            CREATE INDEX IF NOT EXISTS facts_object ON facts(object_key);
            """
        )
        # numpy's stubs do not resolve under this project's mypy target; the
        # arrays are typed Any rather than silencing each operator.
        self._matrix: tuple[Any, list[int]] | None = None

    def close(self) -> None:
        self._db.close()

    # ── Meta ─────────────────────────────────────────────────────────────────

    def meta(self, key: str, default: str = "") -> str:
        row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def _set_meta(self, key: str, value: str) -> None:
        self._db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))

    def stats(self) -> dict[str, object]:
        docs = self._db.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        chunks = self._db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        facts = self._db.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        return {
            "documents": docs,
            "chunks": chunks,
            "facts": facts,
            "model": self.meta("model"),
            "path": str(self.path),
        }

    # ── Build ────────────────────────────────────────────────────────────────

    def build(
        self,
        folders: Sequence[str],
        embed: Embedder,
        read: Reader,
        *,
        model: str,
        endpoint: str,
        facts: FactExtractor | None = None,
        on_file: Callable[[Path, int], None] | None = None,
    ) -> dict[str, int]:
        """Bring the index in line with the folders. Unchanged files are skipped.

        A different embedding model makes every stored vector meaningless, so a
        model change rebuilds from scratch rather than mixing two spaces.
        """
        if self.meta("model") and self.meta("model") != model:
            self._db.executescript(
                "DELETE FROM docs; DELETE FROM chunks; DELETE FROM chunks_fts; DELETE FROM facts;"
            )
        self._set_meta("model", model)
        self._set_meta("endpoint", endpoint)
        # Turning the graph on for an existing index must read every file once:
        # "unchanged" is about the bytes, and the relations were never extracted.
        if facts is not None and self.meta("facts") != "1":
            self._db.execute("DELETE FROM docs")
        self._set_meta("facts", "1" if facts is not None else "0")

        files = _files(folders)
        present = {str(p) for p in files}
        added = skipped = removed = 0

        for (old,) in self._db.execute("SELECT path FROM docs").fetchall():
            if old not in present:
                self._forget(old)
                removed += 1

        for path in files:
            digest = _digest(path)
            row = self._db.execute(
                "SELECT digest FROM docs WHERE path = ?", (str(path),)
            ).fetchone()
            if row and row[0] == digest:
                skipped += 1
                continue
            self._forget(str(path))
            text = read(path)
            pieces = chunk(text)
            vectors = embed(pieces) if pieces else []
            for order, (text, vector) in enumerate(zip(pieces, vectors, strict=True)):
                array: Any = np.asarray(vector, dtype=np.float32)
                blob = array.tobytes()
                cur = self._db.execute(
                    "INSERT INTO chunks (path, ord, text, vec) VALUES (?, ?, ?, ?)",
                    (str(path), order, text, blob),
                )
                self._db.execute(
                    "INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)", (cur.lastrowid, text)
                )
            for fact in facts(text) if facts and text.strip() else []:
                self._db.execute(
                    "INSERT INTO facts (path, subject, subject_key, relation, object, object_key, evidence)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(path),
                        fact["subject"],
                        entity_key(fact["subject"]),
                        fact["relation"],
                        fact["object"],
                        entity_key(fact["object"]),
                        fact.get("evidence", "")[:300],
                    ),
                )
            self._db.execute("INSERT INTO docs VALUES (?, ?, ?)", (str(path), digest, time.time()))
            self._db.commit()
            added += 1
            if on_file:
                on_file(path, len(pieces))

        self._db.commit()
        self._matrix = None
        return {"indexed": added, "unchanged": skipped, "removed": removed}

    def _forget(self, path: str) -> None:
        ids = [r[0] for r in self._db.execute("SELECT id FROM chunks WHERE path = ?", (path,))]
        self._db.executemany("DELETE FROM chunks_fts WHERE rowid = ?", [(i,) for i in ids])
        self._db.execute("DELETE FROM chunks WHERE path = ?", (path,))
        self._db.execute("DELETE FROM facts WHERE path = ?", (path,))
        self._db.execute("DELETE FROM docs WHERE path = ?", (path,))

    # ── Search ───────────────────────────────────────────────────────────────

    def _lexical(self, query: str, names_only: bool = False) -> list[int]:
        words = re.findall(r"[\w-]{3,}", query)
        if names_only:
            # Names and codes only: a capital that does not start the sentence,
            # an acronym, a digit. "cliente" or "contratto" would match half the
            # memory and seal every run that uses an ordinary word.
            words = [
                w
                for i, w in enumerate(words)
                if (w[0].isupper() and i > 0) or w.isupper() or any(c.isdigit() for c in w)
            ]
        terms = {w.lower() for w in words if w.lower() not in STOPWORDS}
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in sorted(terms))
        rows = self._db.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?",
            (match, CANDIDATES),
        ).fetchall()
        return [r[0] for r in rows]

    def _semantic(self, query: str, embed: Embedder) -> list[int]:
        if self._matrix is None:
            rows = self._db.execute("SELECT id, vec FROM chunks").fetchall()
            if not rows:
                return []
            ids = [r[0] for r in rows]
            matrix: Any = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
            matrix /= np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9
            # ponytail: brute-force cosine in memory; fine to ~100k chunks, sqlite-vec or Qdrant beyond.
            self._matrix = (matrix, ids)
        matrix, ids = self._matrix
        q: Any = np.asarray(embed([query])[0], dtype=np.float32)
        q /= np.linalg.norm(q) + 1e-9
        similarity: Any = matrix @ q
        order = np.argsort(-similarity)[:CANDIDATES]
        return [ids[i] for i in order if similarity[i] >= MIN_SIMILARITY]

    def search(self, query: str, embed: Embedder, k: int = 6, *, strict: bool = False) -> list[Hit]:
        """The ``k`` passages most relevant to ``query``, by fused rank.

        ``strict`` is for retrieval nobody asked for — the prefetch before the
        first turn: words match only as names and codes, so an ordinary question
        does not pull (and seal) the memory just by sharing a common word.
        """
        scores: dict[int, float] = {}
        try:
            semantic = self._semantic(query, embed)
        except httpx.HTTPError:
            # The embedder runs on the local GPU; when it is down the words still
            # work. A memory that fails closed on an outage would silently stop
            # warning about conflicts exactly when the model is least capable.
            semantic = []
        for ranking in (self._lexical(query, names_only=strict), semantic):
            for rank, cid in enumerate(ranking):
                scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
        best = sorted(scores, key=scores.__getitem__, reverse=True)[:k]
        hits = []
        for cid in best:
            path, text = self._db.execute(
                "SELECT path, text FROM chunks WHERE id = ?", (cid,)
            ).fetchone()
            hits.append(Hit(path=path, text=text, score=round(scores[cid], 5)))
        return hits

    # ── Graph ────────────────────────────────────────────────────────────────

    def facts_about(self, query: str, hops: int = 2, limit: int = 12) -> list[Fact]:
        """Relations reachable from the organisations the query names, ``hops`` deep.

        A conflict is usually two hops away and never in the request: Nordika →
        partner of → Veloce → bound by → NDA 7.3. Following edges answers that
        deterministically, where top-k passages might rank the NDA out.
        """
        keys = [
            r[0]
            for r in self._db.execute(
                "SELECT DISTINCT subject_key FROM facts UNION SELECT DISTINCT object_key FROM facts"
            )
        ]
        text = query.lower()
        words = set(re.findall(r"[\w-]{4,}", text))
        frontier = {k for k in keys if k and (k in text or k.split()[0] in words)}
        seen_facts: dict[int, Fact] = {}
        visited: set[str] = set()
        for _ in range(hops):
            if not frontier:
                break
            marks = ",".join("?" * len(frontier))
            rows = self._db.execute(
                f"SELECT id, subject, relation, object, evidence, path, subject_key, object_key FROM facts "  # noqa: S608 - placeholders only
                f"WHERE subject_key IN ({marks}) OR object_key IN ({marks})",
                (*frontier, *frontier),
            ).fetchall()
            visited |= frontier
            nxt: set[str] = set()
            for fid, subj, rel, obj, ev, path, sk, ok in rows:
                seen_facts.setdefault(fid, Fact(subj, rel, obj, ev, path))
                nxt |= {sk, ok}
            frontier = nxt - visited
        return list(seen_facts.values())[:limit]
