"""The company's memory: a local retrieval index over the folders the policy names.

See ``docs/design/memoria-storica.md``. The index lives beside the policy and the
ledger, is built by a local embedder, and is never synced: it is a copy of the
corpus in another shape, so it has the corpus's class.
"""

from runner.memory.index import Hit, MemoryIndex, default_index_path

__all__ = ["Hit", "MemoryIndex", "default_index_path"]
