"""The link to Agents Studio (ADR 0006), against a fake control plane.

The fake speaks the wire contract in the ADR through ``httpx.MockTransport``,
so these tests pin the runner's half of it: what is sent, what is refused, and
above all what never leaves — the answer to a run that read restricted material.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import httpx
import pytest
import yaml

from runner.audit.ledger import read_entries, verify_file
from runner.kernel.types import SensitivityClass
from runner.link import (
    API_PREFIX,
    LinkClient,
    LinkConfig,
    LinkError,
    LinkRevokedError,
    LinkWorker,
    check_endpoint,
    enroll,
    release_decision,
)
from runner.policy.loader import default_policy_document, parse_policy

FISCAL_CODE = "RSSMRA85T10A562S"
ENDPOINT = "https://studio.test"
JOB = {
    "id": "job-1",
    "lease_id": "lease-1",
    "title": "Offerta probe card",
    "instruction": "Riassumi la specifica",
    "requested_by": {
        "email": "ada@technoprobe.example",
        "role": "member",
        "organization": "Technoprobe",
    },
}


def policy_doc(release: str | None = "internal") -> dict:
    doc = json.loads(json.dumps(default_policy_document()))
    if release is not None:
        doc["link"] = {"release": release}
    return doc


@pytest.fixture
def policy_file(tmp_path: Path) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(policy_doc()), encoding="utf-8")
    return path


def config() -> LinkConfig:
    return LinkConfig(endpoint=ENDPOINT, runner_id="r-1", secret="s3cret", name="dgx1")


def enforced(response: str, klass: str = "internal", sealed: str = ""):
    """A stand-in for reason_and_execute on the enforced path."""

    def run(instruction, cancelled):
        return {
            "response": response,
            "placement": {
                "class": klass,
                "outcome": "placed",
                "substrate": "local-gpu",
                "reason": "r",
            },
            "sealed": sealed,
        }

    return run


def worker(policy_file: Path, run, handler=None) -> LinkWorker:
    transport = httpx.MockTransport(handler or (lambda r: httpx.Response(200, json={})))
    client = LinkClient(config(), client=httpx.Client(transport=transport))
    return LinkWorker(
        client, run, policy_file=policy_file, poll_seconds=0.01, heartbeat_seconds=0.01
    )


# ── Transport ────────────────────────────────────────────────────────────────


def test_only_https_leaves_this_machine():
    assert (
        check_endpoint("https://api.prod.akaion.com/api/service4/")
        == "https://api.prod.akaion.com/api/service4"
    )
    assert check_endpoint("http://127.0.0.1:8084") == "http://127.0.0.1:8084"
    for bad in ("http://studio.example.com", "ftp://x", "studio.example.com"):
        with pytest.raises(LinkError):
            check_endpoint(bad)


def test_the_secret_is_written_0600_and_a_readable_one_is_refused(tmp_path: Path):
    path = config().save(tmp_path / "link.json")
    assert path.stat().st_mode & 0o777 == 0o600
    assert LinkConfig.load(path) == config()

    os.chmod(path, 0o644)
    with pytest.raises(LinkError, match="0600"):
        LinkConfig.load(path)


def test_enrollment_trades_a_code_for_the_runners_own_credential():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"runner_id": "r-9", "secret": "fresh", "organization": "Technoprobe"}
        )

    cfg = enroll(
        ENDPOINT,
        " ann_enr_abc ",
        "dgx1",
        version="0.1.0",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert seen["path"] == f"{API_PREFIX}/enroll"
    assert seen["auth"] is None, "enrollment must not carry any other credential"
    assert seen["body"]["code"] == "ann_enr_abc"
    assert len(seen["body"]["machine_id"]) == 32
    assert (cfg.runner_id, cfg.secret, cfg.organization) == ("r-9", "fresh", "Technoprobe")


def test_every_runner_call_carries_the_runner_credential_and_nothing_else():
    headers = []

    def handler(request: httpx.Request) -> httpx.Response:
        headers.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"job": None, "cancel": []})

    client = LinkClient(config(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    client.claim()
    client.heartbeat({})
    assert headers == ["Runner r-1.s3cret"] * 2


def test_a_401_means_revoked():
    client = LinkClient(
        config(),
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"detail": "nope"}))
        ),
    )
    with pytest.raises(LinkRevokedError):
        client.claim()


# ── What may go back ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("release", "run_class", "sealed", "response", "released"),
    [
        ("internal", SensitivityClass.INTERNAL, "", "Pitch 40 µm, 12k pad.", True),
        ("internal", SensitivityClass.PUBLIC, "", "ok", True),
        (None, SensitivityClass.PUBLIC, "", "ok", False),  # no link section → metadata only
        ("internal", SensitivityClass.RESTRICTED, "", "ok", False),  # read restricted
        ("internal", SensitivityClass.INTERNAL, "Progetto Falcon", "ok", False),  # sealed
        (
            "internal",
            SensitivityClass.INTERNAL,
            "",
            f"Il cliente è {FISCAL_CODE}",
            False,
        ),  # answer itself
        ("public", SensitivityClass.INTERNAL, "", "ok", False),  # ceiling lowered
    ],
)
def test_release_decision(release, run_class, sealed, response, released):
    policy = parse_policy(policy_doc(release))
    ok, why = release_decision(policy, run_class=run_class, sealed=sealed, response=response)
    assert ok is released, why
    assert why


def test_a_released_answer_goes_back_with_who_asked_in_the_ledger(policy_file: Path):
    body = worker(policy_file, enforced("Pitch 40 µm.")).run_job(JOB)

    assert body["status"] == "completed"
    assert body["response"] == "Pitch 40 µm."
    assert body["lease_id"] == "lease-1"
    assert body["placement"] == {"class": "internal", "outcome": "placed", "substrate": "local-gpu"}

    ledger = policy_file.parent / "ledger.jsonl"
    link = [e for e in read_entries(ledger) if e.kind == "link"]
    assert [e.outcome for e in link] == ["received", "released"]
    assert link[0].detail["requested_by"]["email"] == "ada@technoprobe.example"
    assert verify_file(ledger).ok


def test_a_restricted_run_is_withheld_and_its_answer_never_leaves(policy_file: Path):
    secret_answer = f"La pratica di {FISCAL_CODE} scade il 15 marzo"
    body = worker(policy_file, enforced(secret_answer, klass="restricted")).run_job(JOB)

    assert body["status"] == "withheld"
    assert "response" not in body
    assert FISCAL_CODE not in json.dumps(body)
    assert all(
        "reason" not in d for d in body["decisions"]
    ), "reasons name material; withheld sends shape only"
    outcomes = [
        e.outcome for e in read_entries(policy_file.parent / "ledger.jsonl") if e.kind == "link"
    ]
    assert outcomes == ["received", "withheld"]


def test_no_policy_means_no_remote_work(tmp_path: Path):
    called = []
    body = worker(tmp_path / "missing.yaml", lambda i, c: called.append(i)).run_job(JOB)
    assert body["status"] == "failed"
    assert not called, "an instruction from the network must not run without a perimeter"


def test_an_unenforced_run_is_never_released(policy_file: Path):
    body = worker(policy_file, lambda i, c: {"response": "legacy answer"}).run_job(JOB)
    assert body["status"] == "failed"
    assert "response" not in body


def test_a_failure_reports_its_type_not_its_text(policy_file: Path):
    def boom(i, c):
        raise RuntimeError(f"could not parse {FISCAL_CODE}.pdf")

    body = worker(policy_file, boom).run_job(JOB)
    assert body["status"] == "failed"
    assert FISCAL_CODE not in json.dumps(body)


# ── The loop ─────────────────────────────────────────────────────────────────


def test_serve_claims_runs_reports_and_stops_on_revocation(policy_file: Path):
    posted = []
    claims = iter([{"job": JOB}, {"job": None}])

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/claim"):
            try:
                return httpx.Response(200, json=next(claims))
            except StopIteration:
                return httpx.Response(401, json={"detail": "revoked"})
        if path.endswith("/result"):
            posted.append(json.loads(request.content))
        return httpx.Response(200, json={"cancel": []})

    w = worker(policy_file, enforced("ok"), handler)
    with pytest.raises(LinkRevokedError):
        w.serve(threading.Event())

    assert len(posted) == 1
    assert posted[0]["status"] == "completed"


def test_a_job_cancelled_in_studio_reports_cancelled(policy_file: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"cancel": ["job-1"]})

    w = worker(policy_file, None, handler)

    def run(instruction, cancelled):
        w.beat_once()  # the heartbeat lands mid-run
        assert cancelled()
        return enforced("partial")(instruction, cancelled)

    w._run = run
    assert w.run_job(JOB)["status"] == "cancelled"


def test_the_heartbeat_names_substrates_but_never_endpoints(policy_file: Path):
    report = worker(policy_file, enforced("ok")).report()
    assert report["policy_digest"]
    assert report["substrates"][0]["id"] == "local-gpu"
    assert "endpoint" not in json.dumps(report)


def test_enroll_adds_link_release_keeping_the_operators_comments(tmp_path, monkeypatch):
    from runner.cli_link import _ensure_link_section

    path = tmp_path / "policy.yaml"
    path.write_text(
        "# mine, keep me\n" + yaml.safe_dump(policy_doc(release=None)), encoding="utf-8"
    )
    monkeypatch.setenv("ANNONA_HOME", str(tmp_path))

    with pytest.raises(ValueError):
        _ensure_link_section("top-secret")
    assert "link:" not in path.read_text(), "a bad value must not be written"

    assert _ensure_link_section("internal") == "internal"
    text = path.read_text()
    assert text.startswith("# mine, keep me")
    assert _ensure_link_section("public") == "internal", "an existing ceiling is never overwritten"
