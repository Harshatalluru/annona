"""Skills fetched from a catalog (ADR 0008), against a fake publisher.

What these pin down is the order of refusals: an archive that does not match its
digest, or that would unpack outside its folder, never lands — and the catalog
committed with the docs is exactly what a fresh build of its sources produces.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path

import httpx
import pytest

from runner.kernel.errors import ConfigurationError
from runner.policy.models import SkillCatalog
from runner.skills.catalog import CatalogError, fetch_index, install_from_catalog
from runner.skills.loader import load_skill

ROOT = Path(__file__).resolve().parent.parent
URL = "https://catalog.test/annona/catalog/index.json"
CATALOG = SkillCatalog(name="akaion", url=URL, enable=("rfq-triage",))


def _build_catalog():
    spec = importlib.util.spec_from_file_location(
        "build_catalog", ROOT / "scripts/build_catalog.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_catalog = _build_catalog()


def skill_folder(tmp_path: Path, name: str = "rfq-triage", front_name: str | None = None) -> Path:
    folder = tmp_path / "src" / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {front_name or name}\ndescription: Triage RFQs.\npins: none\n---\n\n"
        "# Triage\n\nOne row per RFQ.\n",
        encoding="utf-8",
    )
    return folder


def publisher(archive: bytes, *, sha256: str | None = None, seen: list | None = None):
    """A catalog serving one entry, ``rfq-triage``, whose archive is ``archive``."""
    index = {
        "version": 1,
        "skills": [
            {
                "name": "rfq-triage",
                "version": 2,
                "description": "Triage RFQs.",
                "pins": "none",
                "sha256": sha256 or hashlib.sha256(archive).hexdigest(),
                "archive": "rfq-triage.tar.gz",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request.url.path)
        if request.url.path == "/annona/catalog/index.json":
            return httpx.Response(200, json=index)
        if request.url.path == "/annona/catalog/rfq-triage.tar.gz":
            return httpx.Response(200, content=archive)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def hostile(member: tarfile.TarInfo, data: bytes = b"") -> bytes:
    """A gzipped tar holding a valid skill plus ``member``."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tar:
        body = b"---\nname: rfq-triage\ndescription: d\n---\n\nbody\n"
        good = tarfile.TarInfo("rfq-triage/SKILL.md")
        good.size = len(body)
        tar.addfile(good, io.BytesIO(body))
        if member.isreg():
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
        else:
            tar.addfile(member)
    return raw.getvalue()


def test_a_verified_archive_installs_pinned_with_its_provenance(tmp_path: Path):
    archive = build_catalog.tarball(skill_folder(tmp_path))
    installed, entry = install_from_catalog(
        CATALOG, "rfq-triage", tmp_path / "skills", client=publisher(archive)
    )

    skill = load_skill(tmp_path / "skills" / "rfq-triage")
    assert installed.pinned and skill.pins_local, "untrusted catalog: pinned local on the way in"
    assert (
        skill.metadata["imported_from"] == "https://catalog.test/annona/catalog/rfq-triage.tar.gz"
    )
    assert skill.metadata["catalog"] == "akaion"
    assert skill.metadata["sha256"] == entry.sha256 == hashlib.sha256(archive).hexdigest()
    assert entry.version == 2


def test_a_trusted_catalog_keeps_the_skills_own_pins(tmp_path: Path):
    archive = build_catalog.tarball(skill_folder(tmp_path))
    trusted = SkillCatalog(name="akaion", url=URL, trust=True)
    installed, _ = install_from_catalog(
        trusted, "rfq-triage", tmp_path / "skills", client=publisher(archive)
    )
    assert not installed.pinned


def test_an_archive_that_does_not_match_its_digest_is_refused(tmp_path: Path):
    archive = build_catalog.tarball(skill_folder(tmp_path))
    with pytest.raises(CatalogError, match="does not match the index"):
        install_from_catalog(
            CATALOG, "rfq-triage", tmp_path / "skills", client=publisher(archive, sha256="0" * 64)
        )
    assert not (tmp_path / "skills").exists()


def _symlink() -> tarfile.TarInfo:
    info = tarfile.TarInfo("rfq-triage/escape")
    info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
    return info


def _hardlink() -> tarfile.TarInfo:
    info = tarfile.TarInfo("rfq-triage/escape")
    info.type, info.linkname = tarfile.LNKTYPE, "rfq-triage/SKILL.md"
    return info


@pytest.mark.parametrize(
    "member",
    [
        lambda: tarfile.TarInfo("rfq-triage/../../evil.sh"),
        lambda: tarfile.TarInfo("/tmp/evil.sh"),
        _symlink,
        _hardlink,
    ],
    ids=["traversal", "absolute", "symlink", "hardlink"],
)
def test_an_archive_that_reaches_outside_its_folder_is_refused_whole(tmp_path: Path, member):
    archive = hostile(member(), b"#!/bin/sh\n")
    with pytest.raises(CatalogError, match="refused"):
        install_from_catalog(CATALOG, "rfq-triage", tmp_path / "skills", client=publisher(archive))
    assert not (tmp_path / "skills").exists()
    assert not (tmp_path / "evil.sh").exists()


def test_an_archive_whose_skill_has_another_name_is_refused(tmp_path: Path):
    archive = build_catalog.tarball(skill_folder(tmp_path, front_name="something-else"))
    with pytest.raises(CatalogError, match="another name"):
        install_from_catalog(CATALOG, "rfq-triage", tmp_path / "skills", client=publisher(archive))


def test_an_existing_install_is_not_replaced_without_force(tmp_path: Path):
    client = publisher(build_catalog.tarball(skill_folder(tmp_path)))
    install_from_catalog(CATALOG, "rfq-triage", tmp_path / "skills", client=client)
    with pytest.raises(ConfigurationError, match="already exists"):
        install_from_catalog(CATALOG, "rfq-triage", tmp_path / "skills", client=client)
    install_from_catalog(CATALOG, "rfq-triage", tmp_path / "skills", client=client, force=True)


def test_the_index_is_reused_for_heartbeats_and_refetched_for_installs(tmp_path: Path):
    seen: list[str] = []
    client = publisher(build_catalog.tarball(skill_folder(tmp_path)), seen=seen)
    fetch_index(CATALOG, client=client)
    fetch_index(CATALOG, client=client)
    assert seen == ["/annona/catalog/index.json"]
    install_from_catalog(CATALOG, "rfq-triage", tmp_path / "skills", client=client)
    assert seen.count("/annona/catalog/index.json") == 2


@pytest.mark.parametrize(
    "body",
    [b"not json", b'{"version": 2, "skills": []}', b'{"version": 1, "skills": [{"name": "x"}]}'],
)
def test_a_malformed_index_is_refused(body: bytes):
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))
    )
    with pytest.raises(CatalogError):
        fetch_index(CATALOG, client=client)


def test_the_published_catalog_is_what_its_sources_build(tmp_path: Path):
    """Edit a skill under catalog/skills, then run scripts/build_catalog.py."""
    index = build_catalog.build(out=tmp_path)
    published = ROOT / "docs" / "catalog"
    assert json.loads((published / "index.json").read_text(encoding="utf-8")) == index
    assert sorted(p.name for p in published.iterdir()) == sorted(p.name for p in tmp_path.iterdir())
    for built in tmp_path.iterdir():
        assert (published / built.name).read_bytes() == built.read_bytes(), built.name
    assert {e["name"] for e in index["skills"]} == {"rfq-triage", "eight-d"}
    assert all(e["pins"] == "local" for e in index["skills"])
