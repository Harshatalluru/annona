"""`annona memory` — build and query the company's memory index.

annona memory index            index the folders the policy names
annona memory search "Nordika" what a run would retrieve for a question
annona memory status           what is indexed, with which embedder
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from runner.memory import MemoryIndex, default_index_path
from runner.memory.index import ollama_embedder, ollama_fact_extractor
from runner.policy.loader import load_policy
from runner.services.enforcement import policy_path
from runner.tools.extractors.registry import extract

console = Console()
memory_app = typer.Typer(
    name="memory", help="The company's memory: a local retrieval index.", no_args_is_help=True
)


def _policy():
    path = policy_path()
    if not path.exists():
        console.print(f"[red]No policy at {path}[/red]: run `annona setup` first.")
        raise typer.Exit(1)
    return load_policy(path)


@memory_app.command("index")
def index_cmd() -> None:
    """Index the folders named in the policy's `memory:` section. Unchanged files are skipped."""
    policy = _policy()
    memory = policy.memory
    if not memory.active:
        console.print("[yellow]The policy has no `memory:` section[/yellow]; nothing to index.")
        raise typer.Exit(1)
    embedder = policy.substrate(memory.embed_with)
    assert embedder is not None  # the loader refuses a memory without one
    endpoint = embedder.endpoint or "http://localhost:11434"
    index = MemoryIndex(default_index_path())
    try:
        result = index.build(
            memory.folders,
            ollama_embedder(endpoint, memory.model),
            lambda path: extract(path).text,
            model=memory.model,
            endpoint=endpoint,
            # The graph is extracted by the same local substrate's chat model:
            # the one the loader already checked can hold the folders' class.
            facts=ollama_fact_extractor(endpoint, embedder.model, memory.company)
            if memory.entities
            else None,
            on_file=lambda path, n: console.print(
                f"  [green]+[/green] {path.name}  [dim]{n} passages[/dim]"
            ),
        )
        stats = index.stats()
    finally:
        index.close()
    console.print(
        f"\n✅ {result['indexed']} indexed · {result['unchanged']} unchanged · {result['removed']} removed"
        f"  —  {stats['documents']} documents, {stats['chunks']} passages, {stats['facts']} relations, embedded by "
        f"{memory.model} on {embedder.id} ({embedder.jurisdiction})"
    )
    console.print(f"   {stats['path']}")


@memory_app.command("search")
def search_cmd(query: str, k: int = typer.Option(6, "--k", help="Passages to show")) -> None:
    """Show what `memory_search` would return for a question."""
    path = default_index_path()
    if not path.exists():
        console.print("[red]No memory index[/red]: run `annona memory index`.")
        raise typer.Exit(1)
    index = MemoryIndex(path)
    try:
        hits = index.search(
            query, ollama_embedder(index.meta("endpoint"), index.meta("model")), k=k
        )
    finally:
        index.close()
    index = MemoryIndex(path)
    try:
        facts = index.facts_about(query)
    finally:
        index.close()
    for fact in facts:
        console.print(f"  [cyan]{fact.line()}[/cyan]  [dim]{fact.path.rsplit('/', 1)[-1]}[/dim]")
    table = Table(show_lines=True)
    table.add_column("#", width=3)
    table.add_column("source")
    table.add_column("passage")
    for i, hit in enumerate(hits, 1):
        table.add_row(str(i), hit.path.rsplit("/", 1)[-1], hit.text[:300])
    console.print(table)


@memory_app.command("status")
def status_cmd() -> None:
    """What is indexed, and with which embedder."""
    path = default_index_path()
    if not path.exists():
        console.print("No memory index on this machine.")
        return
    index = MemoryIndex(path)
    try:
        stats = index.stats()
    finally:
        index.close()
    console.print(
        f"{stats['documents']} documents · {stats['chunks']} passages · model {stats['model']}"
    )
    console.print(f"   {stats['path']}")


def register(app: typer.Typer) -> None:
    app.add_typer(memory_app)
