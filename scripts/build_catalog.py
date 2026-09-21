"""Build the skill catalog published with the docs site (ADR 0008).

    python scripts/build_catalog.py

Reads ``catalog/skills/<name>/`` and writes ``docs/catalog/index.json`` plus one
``<name>-<sha12>.tar.gz`` per skill, which GitHub Pages serves at
https://akaion-ai.github.io/annona/catalog/.

The tarballs are deterministic — sorted entries, no timestamps, no owners, fixed
modes, gzip header without a name or mtime — so the SHA-256 in the index is a
function of the sources alone. ``tests/test_skill_catalog.py`` rebuilds and
compares, so the index and the sources cannot be edited one without the other.
The archive name carries the digest, so a CDN serving yesterday's file under
today's index is a 404, not a checksum mismatch.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "catalog" / "skills"
OUT = ROOT / "docs" / "catalog"

sys.path.insert(0, str(ROOT))
from runner.skills.loader import load_skill  # noqa: E402


def tarball(folder: Path) -> bytes:
    """The skill folder as a reproducible ``.tar.gz``, rooted at ``<name>/``."""
    # Dotfiles (.DS_Store, editor droppings) would make the digest depend on
    # whose machine ran the build.
    members = sorted(
        rel.as_posix()
        for rel in (p.relative_to(folder) for p in folder.rglob("*"))
        if not any(part.startswith(".") for part in rel.parts)
    )
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for rel in ["", *members]:
            path = folder / rel
            info = tarfile.TarInfo(f"{folder.name}/{rel}" if rel else folder.name)
            if path.is_dir():
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                tar.addfile(info)
            else:
                data = path.read_bytes()
                info.size, info.mode = len(data), 0o644
                tar.addfile(info, io.BytesIO(data))
    packed = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=packed, mtime=0, compresslevel=9) as gz:
        gz.write(raw.getvalue())
    return packed.getvalue()


def build(source: Path = SOURCE, out: Path = OUT) -> dict:
    """Write the catalog under ``out`` and return the index."""
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("*.tar.gz"):
        stale.unlink()

    skills = []
    for folder in sorted(p for p in source.iterdir() if (p / "SKILL.md").is_file()):
        skill = load_skill(folder)  # an invalid skill is not published
        if skill.name != folder.name:
            raise SystemExit(f"{folder}: the folder must be named after the skill ({skill.name})")
        archive = tarball(folder)
        digest = hashlib.sha256(archive).hexdigest()
        filename = f"{skill.name}-{digest[:12]}.tar.gz"
        (out / filename).write_bytes(archive)
        skills.append(
            {
                "name": skill.name,
                "version": skill.version,
                "description": skill.description,
                "pins": skill.pins,
                "sha256": digest,
                "archive": filename,
            }
        )

    index = {"version": 1, "skills": skills}
    text = json.dumps(index, indent=2, ensure_ascii=False) + "\n"
    (out / "index.json").write_text(text, encoding="utf-8")
    return index


if __name__ == "__main__":
    for entry in build()["skills"]:
        print(f"{entry['name']} v{entry['version']}  {entry['sha256'][:12]}  {entry['archive']}")
