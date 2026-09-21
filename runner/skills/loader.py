"""Reading skills off disk (layer L2).

A skill is a directory containing ``SKILL.md``: YAML front matter, then prose.
Same shape as Anthropic's Agent Skills, deliberately, so a skill written for one
runtime is not thrown away by the other — the front matter this project adds
(``pins``, ``requires``) is ignored by a runtime that does not understand it,
and the instruction still works.

Two directories are searched, in order, and later wins:

``runner/skills/shipped/`` in the installation
    What ships with the release.
``$ANNONA_HOME/skills/``
    What the operator wrote. Their version of ``image-report`` overrides ours,
    which is how a practice encodes its own house style without forking.

Everything is validated at load time and a malformed skill is an error rather
than a skipped file, for the same reason a malformed policy is: silence is how
you end up believing a capability is present when it is not.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from runner.kernel.errors import ConfigurationError
from runner.skills.models import Skill, SkillRequirements

__all__ = ["BUNDLED_SKILLS_DIR", "discover_skills", "load_skill", "skills_dirs"]

BUNDLED_SKILLS_DIR = Path(__file__).resolve().parent / "shipped"
"""Where the skills that ship with the release live: inside the package, so the
wheel, the desktop sidecar and the container all carry them."""


def skills_dirs(home: str | Path | None = None) -> tuple[Path, ...]:
    """Directories searched for skills, in precedence order.

    ``ANNONA_SKILLS_DIR`` overrides where the shipped set lives.
    """
    bundled = Path(os.getenv("ANNONA_SKILLS_DIR", str(BUNDLED_SKILLS_DIR))).expanduser()

    if home:
        return (bundled, Path(home).expanduser() / "skills")

    explicit = os.getenv("ANNONA_HOME")
    base = Path(explicit).expanduser() if explicit else Path.home() / ".annona"
    return (bundled, base / "skills")


def _split_front_matter(text: str, source: Path) -> tuple[dict[str, Any], str]:
    """Split ``---`` front matter from the instruction body."""
    if not text.startswith("---"):
        raise ConfigurationError(f"{source}: a skill must start with YAML front matter")

    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ConfigurationError(f"{source}: front matter is not closed with '---'")

    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"{source}: front matter is not valid YAML: {exc}") from exc

    if not isinstance(meta, dict):
        raise ConfigurationError(f"{source}: front matter must be a mapping")

    return meta, parts[2].strip()


def load_skill(path: str | Path) -> Skill:
    """Load one ``SKILL.md``.

    Raises:
        ConfigurationError: the file is missing, malformed, or declares
            something this runtime does not understand.
    """
    source = Path(path).expanduser()
    if source.is_dir():
        source = source / "SKILL.md"

    try:
        raw = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(f"cannot read skill at {source}: {exc}") from exc

    meta, body = _split_front_matter(raw, source)

    name = str(meta.get("name", "")).strip()
    if not name:
        raise ConfigurationError(f"{source}: a skill must declare a name")

    description = str(meta.get("description", "")).strip()
    if not description:
        raise ConfigurationError(f"{source}: skill {name!r} must declare a description")

    if not body:
        raise ConfigurationError(f"{source}: skill {name!r} has no instruction body")

    pins = str(meta.get("pins", "none")).strip().lower()
    if pins not in ("none", "local"):
        raise ConfigurationError(
            f"{source}: skill {name!r} declares pins: {pins!r}; only 'local' and 'none' exist"
        )

    requires_raw = meta.get("requires") or []
    if isinstance(requires_raw, str):
        requires_raw = [requires_raw]
    requires_set = {str(r).strip().lower() for r in requires_raw}
    unknown = requires_set - {"vision"}
    if unknown:
        raise ConfigurationError(
            f"{source}: skill {name!r} requires unknown capabilities: {', '.join(sorted(unknown))}"
        )

    tools_raw = meta.get("tools") or []
    if isinstance(tools_raw, str):
        tools_raw = [tools_raw]

    return Skill(
        name=name,
        description=description,
        body=body,
        version=int(meta.get("version", 1)),
        requires=SkillRequirements(
            vision="vision" in requires_set,
            tools=tuple(str(t).strip() for t in tools_raw),
            min_context=int(meta.get("min_context", 0)),
        ),
        pins=pins,
        source=source,
        metadata={
            str(k): str(v)
            for k, v in meta.items()
            if k
            not in {"name", "description", "version", "requires", "tools", "pins", "min_context"}
        },
    )


def discover_skills(dirs: Iterable[str | Path] | None = None) -> dict[str, Skill]:
    """Load every skill found, later directories overriding earlier ones."""
    found: dict[str, Skill] = {}

    for directory in dirs if dirs is not None else skills_dirs():
        base = Path(directory).expanduser()
        if not base.is_dir():
            continue

        for candidate in sorted(base.glob("*/SKILL.md")):
            skill = load_skill(candidate)
            if skill.name in found:
                logger.debug(f"skill {skill.name} overridden by {candidate}")
            found[skill.name] = skill

    return found
