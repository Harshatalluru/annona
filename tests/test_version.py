"""One version, everywhere it is written — the window used to say v0.1.0 at 0.1.3."""

import json
from pathlib import Path

import tomllib
from fastapi.testclient import TestClient

from runner import __version__
from tests.test_local_api_ui_mount import _build_app

ROOT = Path(__file__).resolve().parent.parent


def test_every_place_that_names_the_version_agrees():
    assert tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"] == __version__
    for path in ("ui/package.json", "ui/src-tauri/tauri.conf.json"):
        assert json.loads((ROOT / path).read_text())["version"] == __version__, path


def test_health_says_which_daemon_is_answering(tmp_path):
    assert TestClient(_build_app(tmp_path, None)).get("/health").json()["version"] == __version__
