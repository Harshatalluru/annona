"""Skills fetched from a catalog the policy names (layer L2). ADR 0008 is the argument.

A catalog is a JSON index served over HTTPS: per skill, a name, a version, a
tarball of its folder and the tarball's SHA-256. The policy names the catalogs
this machine may fetch from and, per catalog, which skills are pre-approved::

    skill_catalogs:
      - name: akaion
        url: https://akaion-ai.github.io/annona/catalog/index.json
        enable: [rfq-triage, eight-d]

Studio may then ask for a pre-approved skill to be installed. It decides *when*;
the policy already decided *what*. Nothing Studio sends can widen the list.

Three things happen before a skill lands, in this order, and each one refuses
rather than repairs:

- **The archive matches the index.** Its SHA-256 is compared with the one the
  index published before a byte of it is unpacked. The index, fetched over
  HTTPS from a URL the policy names, is the trust anchor — in v1 there are no
  signatures, the same stance ADR 0007 takes on TLS.
- **Unpacking is paranoid.** Regular files and directories only, relative names,
  no ``..``, a cap on size and count. ``tarfile`` will write wherever an archive
  tells it to; this writes only under a temporary directory it made.
- **It installs like any other foreign skill**, through
  :func:`~runner.skills.install.install_skill`: validated before it lands,
  provenance in the front matter, pinned local unless the catalog is
  ``trust: true``.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
import tempfile
import time
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urljoin

import httpx
from loguru import logger

from runner.kernel.errors import ConfigurationError
from runner.policy.models import SKILL_NAME, Policy, SkillCatalog, normalise_endpoint
from runner.skills.install import InstalledSkill, install_skill
from runner.skills.loader import load_skill

__all__ = [
    "CatalogEntry",
    "CatalogError",
    "fetch_index",
    "install_from_catalog",
    "installable",
]

FETCH_TIMEOUT = 15.0
INDEX_TTL_SECONDS = 600.0
"""How long an index is reused. Heartbeats come every 15 s; a catalog changes
when somebody publishes, and ten minutes late is fine for that."""
MAX_INDEX_BYTES = 1 << 20
MAX_ARCHIVE_BYTES = 5 << 20
MAX_UNPACKED_BYTES = 20 << 20
MAX_MEMBERS = 500
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CatalogError(ConfigurationError):
    """A catalog could not be read, or what it served was refused."""


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """One skill as the index describes it. ``archive`` is already absolute."""

    name: str
    version: int
    description: str
    pins: str
    sha256: str
    archive: str


_indexes: dict[str, tuple[float, dict[str, CatalogEntry]]] = {}


def _get(url: str, limit: int, client: httpx.Client | None) -> bytes:
    """GET ``url`` over HTTPS, refusing a body larger than ``limit``."""
    try:
        normalise_endpoint(url)
    except ValueError as exc:
        raise CatalogError(str(exc)) from None
    http = client or httpx.Client(timeout=FETCH_TIMEOUT)
    try:
        with http.stream("GET", url) as response:
            if response.status_code != 200:
                raise CatalogError(f"GET {url}: {response.status_code}")
            body = bytearray()
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > limit:
                    raise CatalogError(f"{url} is larger than {limit} bytes")
            return bytes(body)
    except httpx.HTTPError as exc:
        raise CatalogError(f"cannot fetch {url}: {exc}") from exc
    finally:
        if client is None:
            http.close()


def _parse_index(raw: bytes, url: str) -> dict[str, CatalogEntry]:
    """Validate an index in full. One malformed entry refuses the whole index."""
    try:
        doc: Any = json.loads(raw)
    except ValueError:
        raise CatalogError(f"{url} is not JSON") from None
    if (
        not isinstance(doc, dict)
        or doc.get("version") != 1
        or not isinstance(doc.get("skills"), list)
    ):
        raise CatalogError(f"{url} is not a version 1 catalog index")

    entries: dict[str, CatalogEntry] = {}
    for index, item in enumerate(doc["skills"]):
        try:
            entry = CatalogEntry(
                name=str(item["name"]),
                version=int(item.get("version", 1)),
                description=str(item.get("description", "")),
                pins=str(item.get("pins", "none")),
                sha256=str(item["sha256"]).lower(),
                archive=urljoin(url, str(item["archive"])),
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            raise CatalogError(f"{url}: skills[{index}] is malformed") from None
        if not SKILL_NAME.match(entry.name) or not _SHA256.match(entry.sha256):
            raise CatalogError(f"{url}: skills[{index}] has an invalid name or sha256")
        entries[entry.name] = entry
    return entries


def fetch_index(
    catalog: SkillCatalog,
    *,
    client: httpx.Client | None = None,
    max_age: float = INDEX_TTL_SECONDS,
) -> dict[str, CatalogEntry]:
    """The catalog's entries by name, reused for ``max_age`` seconds."""
    cached = _indexes.get(catalog.url)
    if cached and time.monotonic() - cached[0] < max_age:
        return cached[1]
    entries = _parse_index(_get(catalog.url, MAX_INDEX_BYTES, client), catalog.url)
    _indexes[catalog.url] = (time.monotonic(), entries)
    return entries


def _unpack(archive: bytes, into: Path) -> None:
    """Unpack a skill tarball under ``into``, or refuse it whole.

    Every member is checked before anything is written, so a refused archive
    leaves nothing behind. Files are written by this code, not by ``tarfile``:
    modes, owners and link targets in the archive are never applied.
    """
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            members = tar.getmembers()
            if len(members) > MAX_MEMBERS:
                raise CatalogError(f"the archive has more than {MAX_MEMBERS} entries")
            total = 0
            for member in members:
                parts = PurePosixPath(member.name).parts
                if (
                    member.name.startswith("/")
                    or ".." in parts
                    or "\\" in member.name
                    or ":" in member.name
                    or not (member.isfile() or member.isdir())
                ):
                    raise CatalogError(
                        f"the archive entry {member.name!r} is refused: only relative, "
                        "regular files and directories are unpacked"
                    )
                total += member.size
                if total > MAX_UNPACKED_BYTES:
                    raise CatalogError(
                        f"the archive unpacks to more than {MAX_UNPACKED_BYTES} bytes"
                    )

            for member in members:
                target = into.joinpath(*PurePosixPath(member.name).parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                target.write_bytes(source.read() if source else b"")
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise CatalogError(f"the archive cannot be unpacked: {exc}") from exc


def install_from_catalog(
    catalog: SkillCatalog,
    name: str,
    destination_dir: str | Path,
    *,
    client: httpx.Client | None = None,
    force: bool = False,
) -> tuple[InstalledSkill, CatalogEntry]:
    """Fetch ``name`` from ``catalog``, verify it, and install it.

    Whether the skill is *enabled* afterwards is not decided here: that is the
    policy's ``enable`` (or ``skills:``), read at the moment a run asks.

    Raises:
        CatalogError: the index has no such skill, the archive does not match
            its SHA-256, or it would unpack outside its folder.
        ConfigurationError: the skill inside does not load, or would overwrite
            an installed one without ``force``.
    """
    # Never the cached index: a digest ten minutes old against an archive
    # published a minute ago would refuse a good install.
    entry = fetch_index(catalog, client=client, max_age=0).get(name)
    if entry is None:
        raise CatalogError(f"catalog {catalog.name!r} has no skill named {name!r}")

    archive = _get(entry.archive, MAX_ARCHIVE_BYTES, client)
    digest = hashlib.sha256(archive).hexdigest()
    if digest != entry.sha256:
        raise CatalogError(
            f"{entry.archive}: sha256 {digest[:12]}… does not match the index "
            f"({entry.sha256[:12]}…); nothing was installed"
        )

    with tempfile.TemporaryDirectory(prefix="annona-skill-") as tmp:
        _unpack(archive, Path(tmp))
        folder = Path(tmp) / name
        # The policy enabled a name; the registry keys skills by the name in
        # SKILL.md. If the two differ, what gets installed is not what was approved.
        if load_skill(folder).name != name:
            raise CatalogError(f"the archive for {name!r} contains a skill with another name")
        installed = install_skill(
            folder,
            destination_dir,
            trust=catalog.trust,
            force=force,
            provenance={
                "imported_from": entry.archive,
                "catalog": catalog.name,
                "sha256": entry.sha256,
            },
        )
    return installed, entry


def installable(
    policy: Policy, installed: Collection[str], *, client: httpx.Client | None = None
) -> list[dict[str, Any]]:
    """Pre-approved skills a catalog publishes that this machine does not have yet.

    What the heartbeat offers Studio to install. A catalog that cannot be read
    is skipped with a warning: a publisher being offline must not take the
    machine off Studio's list. ``pins`` is what the skill will be *once
    installed* — local, unless the catalog is trusted.
    """
    offered: list[dict[str, Any]] = []
    for catalog in policy.skill_catalogs:
        try:
            index = fetch_index(catalog, client=client)
        except Exception as exc:  # noqa: BLE001 — never break the heartbeat
            logger.warning(f"skill catalog {catalog.name} unavailable: {exc}")
            continue
        for name in catalog.enable:
            entry = index.get(name)
            if entry is None or name in installed:
                continue
            offered.append(
                {
                    "name": entry.name,
                    "version": entry.version,
                    "description": entry.description[:300],
                    "pins": entry.pins if catalog.trust else "local",
                    "catalog": catalog.name,
                }
            )
    return offered[:200]
