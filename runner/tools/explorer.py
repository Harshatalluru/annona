"""
Explorer Tool

Explores directories, maps file structures and searches for patterns.
Segue lo stesso approccio a 5 step di Claude Code:
  1. Mappa albero directory
  2. Identifica tipi di file
  3. Reads the relevant content
  4. Raggruppa per tema/tipo
  5. Produce report strutturato
"""

import fnmatch
from pathlib import Path
from typing import Annotated, Any, Dict, List, Literal, Optional

from loguru import logger
from pydantic import Field

from .base import FilePath, Tool, ToolArguments, forwarded
from .document_reader import SUPPORTED_FORMATS, get_file_format


class ExplorerArgs(ToolArguments):
    operation: Annotated[
        Literal["map", "find", "search", "analyze"],
        Field(
            description=(
                "map: directory tree; "
                "find: locate files by name/type; "
                "search: grep content across files; "
                "analyze: full exploration report"
            )
        ),
    ]
    path: Annotated[FilePath, Field(description="Directory or file path to explore")]
    pattern: Annotated[
        str | None, Field(description="Glob pattern (find) or regex/substring (search)")
    ] = None
    file_types: Annotated[
        List[str] | None, Field(description='Filter by extensions, e.g. [".pdf", ".docx"]')
    ] = None
    depth: Annotated[int | None, Field(description="Max recursion depth (default: 5)")] = None
    include_hidden: Annotated[
        bool | None, Field(description="Include hidden files/dirs (default: false)")
    ] = None
    max_results: Annotated[int | None, Field(description="Max files to return (default: 200)")] = (
        None
    )


class ExplorerTool(Tool[ExplorerArgs]):
    """Explore the local filesystem."""

    arguments = ExplorerArgs

    def __init__(self, config: Dict[str, Any]):
        super().__init__(
            name="explorer",
            description=(
                "Explore a local directory: map the tree, find files by type or name pattern, "
                "search file contents, and produce a structured inventory. "
                "Use 'map' to get the full tree, 'find' to locate files, 'search' to grep contents, "
                "'analyze' for a full multi-step analysis report."
            ),
        )
        self.config = config

    def call(self, args: ExplorerArgs) -> Dict[str, Any]:
        return self.execute(**forwarded(args))

    def execute(
        self,
        operation: str,
        path: str,
        pattern: Optional[str] = None,
        file_types: Optional[List[str]] = None,
        depth: int = 5,
        include_hidden: bool = False,
        max_results: int = 200,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        target = Path(path).expanduser().resolve()

        if not target.exists():
            return {"success": False, "error": f"Path not found: {target}"}

        logger.info(f"Explorer {operation}: {target}")

        try:
            if operation == "map":
                return self._map(target, depth, include_hidden, file_types, max_results)
            elif operation == "find":
                return self._find(target, pattern, file_types, depth, include_hidden, max_results)
            elif operation == "search":
                return self._search(target, pattern, file_types, depth, include_hidden, max_results)
            elif operation == "analyze":
                return self._analyze(target, depth, include_hidden, max_results)
            else:
                return {"success": False, "error": f"Unknown operation: {operation}"}
        except Exception as e:
            logger.error(f"Explorer error: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # ── Operations ────────────────────────────────────────────────────────────

    def _map(
        self,
        root: Path,
        depth: int,
        include_hidden: bool,
        file_types: Optional[List[str]],
        max_results: int,
    ) -> Dict[str, Any]:
        """Mappa la struttura ad albero di una directory"""
        tree_lines = []
        by_format: Dict[str, int] = {}
        stats: Dict[str, Any] = {"dirs": 0, "files": 0, "by_format": by_format}

        def _walk(path: Path, prefix: str, current_depth: int):
            if current_depth > depth:
                tree_lines.append(prefix + "  [max depth reached]")
                return

            try:
                items = sorted(path.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
            except PermissionError:
                tree_lines.append(prefix + "  [permission denied]")
                return

            items = [i for i in items if include_hidden or not i.name.startswith(".")]
            if file_types:
                items = [i for i in items if i.is_dir() or i.suffix.lower() in file_types]

            for i, item in enumerate(items):
                if len(tree_lines) >= max_results:
                    tree_lines.append(prefix + "  [... more items ...]")
                    return

                connector = "└── " if i == len(items) - 1 else "├── "
                extension = "  " if i == len(items) - 1 else "│ "

                if item.is_dir():
                    stats["dirs"] += 1
                    tree_lines.append(f"{prefix}{connector}{item.name}/")
                    _walk(item, prefix + extension, current_depth + 1)
                else:
                    stats["files"] += 1
                    fmt = get_file_format(str(item))
                    by_format[fmt] = by_format.get(fmt, 0) + 1
                    size = _fmt_size(item.stat().st_size)
                    tree_lines.append(f"{prefix}{connector}{item.name} ({size})")

        tree_lines.append(str(root) + "/")
        _walk(root, "", 0)

        return {
            "success": True,
            "path": str(root),
            "tree": "\n".join(tree_lines),
            "stats": stats,
        }

    def _find(
        self,
        root: Path,
        pattern: Optional[str],
        file_types: Optional[List[str]],
        depth: int,
        include_hidden: bool,
        max_results: int,
    ) -> Dict[str, Any]:
        """Trova file per nome/pattern/tipo"""
        matches: List[Dict[str, Any]] = []

        for file in _walk_files(root, depth, include_hidden):
            if len(matches) >= max_results:
                break
            if file_types and file.suffix.lower() not in file_types:
                continue
            if pattern and not fnmatch.fnmatch(file.name.lower(), pattern.lower()):
                # try substring match too
                if pattern.lower() not in file.name.lower():
                    continue
            stat = file.stat()
            matches.append(
                {
                    "path": str(file),
                    "name": file.name,
                    "format": get_file_format(str(file)),
                    "size_mb": round(stat.st_size / (1024 * 1024), 3),
                    "modified": stat.st_mtime,
                }
            )

        return {
            "success": True,
            "path": str(root),
            "pattern": pattern,
            "file_types": file_types,
            "count": len(matches),
            "files": matches,
        }

    def _search(
        self,
        root: Path,
        pattern: Optional[str],
        file_types: Optional[List[str]],
        depth: int,
        include_hidden: bool,
        max_results: int,
    ) -> Dict[str, Any]:
        """Cerca il pattern nel contenuto dei file di testo"""
        if not pattern:
            return {"success": False, "error": "pattern is required for search operation"}

        all_text_exts = (
            SUPPORTED_FORMATS["text"] + SUPPORTED_FORMATS["code"] + SUPPORTED_FORMATS["csv"]
        )

        results: List[Dict[str, Any]] = []
        pattern_lower = pattern.lower()

        for file in _walk_files(root, depth, include_hidden):
            if len(results) >= max_results:
                break

            ext = file.suffix.lower()
            if file_types:
                if ext not in file_types:
                    continue
            elif ext not in all_text_exts:
                continue

            try:
                with open(file, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                matching = [
                    {"line": i + 1, "text": line.rstrip()}
                    for i, line in enumerate(lines)
                    if pattern_lower in line.lower()
                ]
                if matching:
                    results.append(
                        {
                            "path": str(file),
                            "name": file.name,
                            "matches": matching[:20],  # max 20 matches per file
                        }
                    )
            except Exception:
                pass

        return {
            "success": True,
            "path": str(root),
            "pattern": pattern,
            "files_with_matches": len(results),
            "results": results,
        }

    def _analyze(
        self, root: Path, depth: int, include_hidden: bool, max_results: int
    ) -> Dict[str, Any]:
        """
        Analisi completa in 5 step:
        1. Mappa albero
        2. Conta e classifica per tipo
        3. Identifica file rilevanti per categoria
        4. Preview dei file più importanti
        5. Produce report strutturato
        """
        # Step 1: tree map
        map_result = self._map(root, depth, include_hidden, None, max_results)
        tree = map_result.get("tree", "")
        stats = map_result.get("stats", {})

        # Step 2: full file list with metadata
        all_files = []
        for file in _walk_files(root, depth, include_hidden):
            fmt = get_file_format(str(file))
            stat = file.stat()
            all_files.append(
                {
                    "path": str(file),
                    "name": file.name,
                    "format": fmt,
                    "extension": file.suffix.lower(),
                    "size_mb": round(stat.st_size / (1024 * 1024), 4),
                    "modified": stat.st_mtime,
                }
            )

        # Step 3: group by format category
        by_category: Dict[str, List[Dict]] = {}
        for f in all_files:
            cat = f["format"]
            by_category.setdefault(cat, []).append(f)

        # Step 4: for each readable category, extract first 500 chars of important files
        previews = []
        readable_fmts = {"text", "code", "csv"}
        for cat in readable_fmts:
            for finfo in by_category.get(cat, [])[:3]:
                try:
                    fp = Path(finfo["path"])
                    with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                        snippet = fh.read(500).strip()
                    previews.append(
                        {
                            "path": finfo["path"],
                            "format": cat,
                            "preview": snippet,
                        }
                    )
                except Exception:
                    pass

        # Step 5: build summary
        summary_lines = [
            f"Directory: {root}",
            f"Total files: {len(all_files)} across {stats.get('dirs', 0)} subdirectories",
            "",
            "Files by format:",
        ]
        for cat, files in sorted(by_category.items(), key=lambda x: -len(x[1])):
            total_mb = sum(f["size_mb"] for f in files)
            summary_lines.append(f"  {cat}: {len(files)} files ({total_mb:.2f} MB)")

        return {
            "success": True,
            "path": str(root),
            "summary": "\n".join(summary_lines),
            "tree": tree,
            "stats": {
                **stats,
                "total_files": len(all_files),
                "by_category": {k: len(v) for k, v in by_category.items()},
            },
            "files": all_files,
            "previews": previews,
            "by_category": by_category,
        }


# ── Helpers ──────────────────────────────────────────────────────────────────


def _walk_files(root: Path, max_depth: int, include_hidden: bool):
    """Generator: yields all files up to max_depth"""

    def _recurse(path: Path, current_depth: int):
        if current_depth > max_depth:
            return
        try:
            for item in sorted(path.iterdir(), key=lambda x: x.name.lower()):
                if not include_hidden and item.name.startswith("."):
                    continue
                if item.is_file():
                    yield item
                elif item.is_dir():
                    yield from _recurse(item, current_depth + 1)
        except PermissionError:
            pass

    yield from _recurse(root, 0)


def _fmt_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes // 1024}KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f}MB"
