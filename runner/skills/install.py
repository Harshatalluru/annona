"""Installing a skill somebody else wrote (layer L2).

Claude's skills work here unchanged — the format is the same, `name` and
`description` are the only required fields, and a folder that ships `scripts/`
and `references/` is copied whole. That is deliberate: an ecosystem is worth
more than a format.

What is *not* the same is the trust assumption, and this module exists to make
that explicit rather than to make it convenient.

A skill is an instruction your agent will follow. Whoever wrote the file decides
what your agent does with your material — it is a supply-chain dependency that
happens to be prose. So an imported skill is **pinned to the perimeter by
default**: it may run, and everything it touches stays inside. Removing that pin
is one flag, `--trust`, and the point of the flag is that somebody has to type
it after reading the file.

Two more things are stated at install time rather than discovered later:

- **installed is not enabled.** The policy still has to name the skill. Copying
  a file into a directory is not a decision about what your agents may do.
- **bundled scripts will not run** unless the policy allows the `shell` tool,
  which it does not by default. The instructions still work; the automation in
  them does not. Better said now than debugged at a customer site.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

import yaml

from runner.kernel.errors import ConfigurationError
from runner.skills.loader import load_skill
from runner.skills.models import Skill

__all__ = ["CLAUDE_SKILLS_DIR", "InstalledSkill", "install_skill", "resolve_source"]

CLAUDE_SKILLS_DIR = Path.home() / ".claude" / "skills"
"""Where Claude Code keeps its skills. Consulted when a source is given by name."""


class InstalledSkill:
    """What happened during an install, in a form the CLI can report."""

    def __init__(
        self,
        skill: Skill,
        *,
        destination: Path,
        pinned: bool,
        has_scripts: bool,
        source: Path,
    ) -> None:
        self.skill = skill
        self.destination = destination
        self.pinned = pinned
        self.has_scripts = has_scripts
        self.source = source


def resolve_source(source: str | Path) -> Path:
    """Find the skill folder a user meant.

    A path if it is one; otherwise a name looked up in Claude's skills
    directory, so ``annona skills install pdf`` does the obvious thing on a
    machine that already has Claude Code.
    """
    candidate = Path(source).expanduser()
    if candidate.exists():
        return candidate.parent if candidate.name == "SKILL.md" else candidate

    from_claude = CLAUDE_SKILLS_DIR / str(source)
    if (from_claude / "SKILL.md").exists():
        return from_claude

    raise ConfigurationError(
        f"no skill at {candidate}, and none named {source!r} in {CLAUDE_SKILLS_DIR}"
    )


def _restamp(
    path: Path, *, source: Path, trust: bool, provenance: Mapping[str, str] | None = None
) -> None:
    """Rewrite the front matter with provenance, and pin unless trusted.

    The body is copied byte for byte. Only the front matter is touched, and the
    CLI prints exactly which keys were added — an import that silently edited
    somebody's instruction would be its own kind of supply-chain problem.
    """
    raw = path.read_text(encoding="utf-8")
    _, front, body = raw.split("---", 2)
    meta = yaml.safe_load(front) or {}

    meta["imported_from"] = str(source)
    meta["imported_at"] = datetime.now(timezone.utc).date().isoformat()
    meta.update(provenance or {})

    if not trust and str(meta.get("pins", "none")).lower() != "local":
        meta["pins"] = "local"
        meta["pinned_reason"] = "imported, not written here"

    path.write_text(
        "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + "---" + body,
        encoding="utf-8",
    )


def install_skill(
    source: str | Path,
    destination_dir: str | Path,
    *,
    name: str | None = None,
    trust: bool = False,
    force: bool = False,
    provenance: Mapping[str, str] | None = None,
) -> InstalledSkill:
    """Copy a skill folder into the operator's skills directory.

    Args:
        source: a folder, a ``SKILL.md``, or the name of a skill in
            ``~/.claude/skills``.
        destination_dir: usually ``~/.annona/skills``.
        name: override the installed directory name.
        trust: keep the skill's own ``pins`` value instead of pinning it local.
        force: replace an existing installation of the same name.
        provenance: front matter keys that say where the skill came from better
            than the folder does — a catalog install unpacks into a temporary
            directory, and ``imported_from`` should name the archive instead.

    Raises:
        ConfigurationError: the source does not exist, does not validate, or
            would overwrite something without ``force``. Validation happens
            *before* anything is written, so a broken skill never lands.
    """
    folder = resolve_source(source)
    manifest = folder / "SKILL.md"
    if not manifest.exists():
        raise ConfigurationError(f"{folder} contains no SKILL.md")

    # Validate first: nothing is copied until it is known to load.
    candidate = load_skill(manifest)
    target_name = name or candidate.name
    destination = Path(destination_dir).expanduser() / target_name

    if destination.exists() and not force:
        raise ConfigurationError(
            f"{destination} already exists; pass --force to replace it, or --name to install "
            "under a different name"
        )

    if destination.exists():
        shutil.rmtree(destination)

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(folder, destination)

    _restamp(destination / "SKILL.md", source=folder, trust=trust, provenance=provenance)
    installed = load_skill(destination / "SKILL.md")

    has_scripts = (
        any((destination / sub).is_dir() for sub in ("scripts", "bin", "tools"))
        or any(destination.glob("*.py"))
        or any(destination.glob("*.sh"))
    )

    return InstalledSkill(
        installed,
        destination=destination,
        pinned=installed.pins_local,
        has_scripts=has_scripts,
        source=folder,
    )
