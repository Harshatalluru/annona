"""Tests for the link inbox in runner/local_api.py.

The inbox holds answers this machine's policy refused to send to Agents Studio.
Reading them in the window is the point; reading them from anywhere else would
be the release the policy refused, by another door.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runner.auth import AuthManager
from runner.brain.manager import BrainManager
from runner.local_api import create_app
from runner.pairing import Pairing
from runner.sync.engine import SyncEngine

CLOUD = "https://app.akaion.com"
JOB_ID = "3f2c9a1e-0000-4000-8000-000000000001"
RECORD = {
    "job_id": JOB_ID,
    "title": "Riassumi la specifica",
    "instruction": "Riassumi la specifica NDA",
    "requested_by": {
        "email": "ada@technoprobe.example",
        "role": "member",
        "organization_id": "o-1",
    },
    "skill": "summarise",
    "response": "Pitch 40 µm.\nTolleranza 2 µm.",
    "release": "the run read restricted material, above link.release (internal)",
    "placement": {"class": "restricted", "outcome": "placed", "substrate": "local-gpu"},
    "ledger_head": "abc123",
}


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    h = tmp_path / "annona-home"
    inbox = h / "link" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / f"{JOB_ID}.json").write_text(json.dumps(RECORD), encoding="utf-8")
    (h / "secret.json").write_text('{"job_id": "secret"}', encoding="utf-8")
    monkeypatch.setenv("ANNONA_HOME", str(h))
    return h


@pytest.fixture
def client(home, tmp_path) -> TestClient:
    brain = BrainManager(tmp_path / "brain")
    auth = AuthManager(config_dir=tmp_path / ".akaion")
    sync = SyncEngine(brain=brain, cot_url="http://localhost:9999", auth=auth)
    return TestClient(create_app(brain, sync, auth))


def test_the_list_says_who_asked_and_why_but_not_what(client):
    items = client.get("/api/link/inbox").json()

    assert len(items) == 1
    item = items[0]
    assert item["job_id"] == JOB_ID
    assert item["requested_by"] == "ada@technoprobe.example"
    assert item["skill"] == "summarise"
    assert item["release"] == RECORD["release"]
    assert item["placement_class"] == "restricted"
    assert item["received"] > 0
    assert "response" not in item and "instruction" not in item


def test_the_detail_is_the_whole_record(client):
    body = client.get(f"/api/link/inbox/{JOB_ID}").json()

    assert body["response"] == RECORD["response"]
    assert body["instruction"] == RECORD["instruction"]
    assert body["placement"] == RECORD["placement"]


def test_an_unknown_id_is_404(client):
    assert client.get("/api/link/inbox/nope").status_code == 404


@pytest.mark.parametrize("job_id", ["..%2Fsecret", "..%2F..%2Fannona-home%2Fsecret", "secret"])
def test_an_id_is_looked_up_never_opened_as_a_path(client, job_id):
    response = client.get(f"/api/link/inbox/{job_id}")

    assert response.status_code == 404
    assert "secret" not in response.text


def test_an_empty_inbox_is_an_empty_list(client, home):
    (home / "link" / "inbox" / f"{JOB_ID}.json").unlink()

    assert client.get("/api/link/inbox").json() == []


def test_a_paired_app_cannot_read_what_the_policy_kept_here(client, home):
    pairing = Pairing.create(home / "pairing.json", origins=(CLOUD,))
    headers = {"Origin": CLOUD, "x-annona-token": pairing.token}

    assert client.get("/api/link/inbox", headers=headers).status_code == 403
    assert client.get(f"/api/link/inbox/{JOB_ID}", headers=headers).status_code == 403
    # Unpaired is refused earlier, by the middleware.
    assert client.get("/api/link/inbox", headers={"Origin": CLOUD}).status_code == 401


def test_the_window_on_this_machine_can_read_it(client):
    headers = {"Origin": "tauri://localhost"}

    assert client.get("/api/link/inbox", headers=headers).status_code == 200
    assert client.get(f"/api/link/inbox/{JOB_ID}", headers=headers).status_code == 200
