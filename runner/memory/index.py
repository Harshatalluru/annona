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

__all__ = ["Hit", "MemoryIndex", "default_index_path", "ollama_embedder", "chunk"]

Embedder = Callable[[Sequence[str]], list[list[float]]]
Reader = Callable[[Path], str]
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
        return {
            "documents": docs,
            "chunks": chunks,
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
        on_file: Callable[[Path, int], None] | None = None,
    ) -> dict[str, int]:
        """Bring the index in line with the folders. Unchanged files are skipped.

        A different embedding model makes every stored vector meaningless, so a
        model change rebuilds from scratch rather than mixing two spaces.
        """
        if self.meta("model") and self.meta("model") != model:
            self._db.executescript("DELETE FROM docs; DELETE FROM chunks; DELETE FROM chunks_fts;")
        self._set_meta("model", model)
        self._set_meta("endpoint", endpoint)

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
            pieces = chunk(read(path))
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
