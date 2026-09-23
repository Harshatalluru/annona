"""Managed clouds as substrates: Vertex, Bedrock, Azure, and keyed APIs.

Four promises, each with the test that keeps it:

- an EU substrate cannot point at a non-EU region where the URL says the region;
- one substrate that cannot be built (expired login, missing key) takes itself
  out of the run instead of the whole perimeter, and says why;
- managed endpoints are not probed anonymously (a 401 is not "down");
- Bedrock and Azure build from the machine's identity or a named key, never a
  secret in the policy.
"""

from __future__ import annotations

import sys
import types

import pytest

from runner.capability.backends import AnthropicBackend, OpenAICompatibleBackend
from runner.kernel.errors import ConfigurationError, PolicyError
from runner.placement.registry import SubstrateRegistry, http_prober
from runner.policy.loader import parse_policy
from runner.services.enforcement import Enforcement, build_backend

VERTEX_EU = "https://europe-west1-aiplatform.googleapis.com/v1/projects/p/locations/europe-west1"
VERTEX_US = "https://us-central1-aiplatform.googleapis.com/v1/projects/p/locations/us-central1"
BEDROCK_EU = "https://bedrock-runtime.eu-central-1.amazonaws.com"
BEDROCK_US = "https://bedrock-runtime.us-east-1.amazonaws.com"


def policy(*substrates: dict, allow: list[str] | None = None):
    ids = [s["id"] for s in substrates]
    return parse_policy(
        {
            "version": 1,
            "default": "deny",
            "classes": {"public": {"default": True}},
            "substrates": list(substrates),
            "rules": [{"match": {"class": "public"}, "allow": allow or ids}],
        }
    )


def sub(sid: str, kind: str, endpoint: str = "", model: str = "m", **extra) -> dict:
    return {
        "id": sid,
        "kind": kind,
        "endpoint": endpoint,
        "model": model,
        "max_class": "public",
        **extra,
    }


# ── Region against jurisdiction ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("kind", "endpoint"),
    [
        ("vertex", VERTEX_US),
        ("bedrock", BEDROCK_US),
        ("vertex", VERTEX_EU.replace("europe-west1", "global")),
    ],
)
def test_an_eu_substrate_pointing_outside_the_eu_is_refused(kind, endpoint):
    with pytest.raises(PolicyError, match="processes data in"):
        policy(sub("frontier", kind, endpoint, jurisdiction="eu"))


@pytest.mark.parametrize(("kind", "endpoint"), [("vertex", VERTEX_EU), ("bedrock", BEDROCK_EU)])
def test_an_eu_substrate_in_an_eu_region_is_accepted(kind, endpoint):
    assert policy(sub("frontier", kind, endpoint, jurisdiction="eu")).substrates[0].kind == kind


def test_a_non_eu_jurisdiction_is_not_second_guessed():
    assert policy(sub("frontier", "vertex", VERTEX_US, jurisdiction="us"))


# ── One broken substrate does not take the perimeter down ─────────────────────


def test_a_substrate_that_cannot_be_built_is_unavailable_not_fatal(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    p = policy(
        sub("local", "echo", jurisdiction="on-prem"),
        sub("frontier", "anthropic", model="claude-opus-5", jurisdiction="us"),
    )

    enforcement = Enforcement.for_run(
        policy=p, ledger_path=tmp_path / "ledger.jsonl", probe=False, fsync=False, secrets={}
    )

    assert enforcement.registry.is_up("local")
    health = enforcement.registry.health("frontier")
    assert not health.up
    assert "ANTHROPIC_API_KEY" in health.reason
    assert "frontier" not in enforcement.backends


def test_a_broken_substrate_stays_broken_for_the_whole_run():
    p = policy(sub("frontier", "anthropic"))
    registry = SubstrateRegistry.from_substrates(p.substrates)

    registry.mark_broken("frontier", "no credential")
    registry.mark_up("frontier")

    assert not registry.is_up("frontier"), "no adapter exists, so nothing may route to it"


def test_an_expired_google_login_makes_vertex_unavailable(monkeypatch):
    import google.auth
    import google.auth.exceptions

    def expired(*_, **__):
        raise google.auth.exceptions.RefreshError("Reauthentication is needed.")

    monkeypatch.setattr(google.auth, "default", expired)
    p = policy(
        sub("frontier", "vertex", VERTEX_EU, model="google/gemini-2.5-flash", jurisdiction="eu")
    )

    with pytest.raises(ConfigurationError, match="gcloud auth application-default login"):
        build_backend(p.substrates[0])


# ── Managed endpoints are not probed anonymously ──────────────────────────────


@pytest.mark.parametrize(
    ("kind", "endpoint"),
    [
        ("vertex", VERTEX_EU),
        ("bedrock", BEDROCK_EU),
        ("azure", "https://r.openai.azure.com/openai/v1"),
        ("anthropic", "https://api.anthropic.com"),
    ],
)
def test_managed_kinds_are_not_probed_without_credentials(kind, endpoint, monkeypatch):
    import httpx

    def no_network(*_, **__):
        raise AssertionError("an anonymous probe would read a 401 as down")

    monkeypatch.setattr(httpx, "get", no_network)
    substrate = policy(sub("frontier", kind, endpoint, jurisdiction="us")).substrates[0]

    assert http_prober()(substrate).up


# ── Bedrock ───────────────────────────────────────────────────────────────────


def test_bedrock_builds_claude_in_the_endpoint_region(monkeypatch):
    seen = {}

    class FakeBedrock:
        def __init__(self, *, aws_region):
            seen["region"] = aws_region

    monkeypatch.setattr("anthropic.AnthropicBedrock", FakeBedrock)
    p = policy(
        sub(
            "frontier",
            "bedrock",
            BEDROCK_EU,
            model="eu.anthropic.claude-sonnet-5",
            jurisdiction="eu",
        )
    )

    backend = build_backend(p.substrates[0])

    assert isinstance(backend, AnthropicBackend)
    assert seen["region"] == "eu-central-1"


@pytest.mark.parametrize(
    ("endpoint", "model", "match"),
    [
        ("https://example.com", "anthropic.claude-sonnet-5", "needs a model and an endpoint"),
        (BEDROCK_EU, "meta.llama4-maverick", "Claude models only"),
    ],
)
def test_bedrock_refuses_what_it_cannot_serve(endpoint, model, match):
    p = policy(sub("frontier", "bedrock", endpoint, model=model))
    with pytest.raises(ConfigurationError, match=match):
        build_backend(p.substrates[0])


# ── Azure ─────────────────────────────────────────────────────────────────────

AZURE = "https://acme.openai.azure.com/openai/v1"


def test_azure_uses_the_key_the_policy_names():
    p = policy(sub("frontier", "azure", AZURE, model="gpt-5", api_key_env="ACME_AZURE_KEY"))

    backend = build_backend(p.substrates[0], secrets={"ACME_AZURE_KEY": "k"})

    assert isinstance(backend, OpenAICompatibleBackend)
    assert backend._api_key == "k"


def test_azure_without_a_key_uses_the_machine_identity(monkeypatch):
    class Token:
        token = "entra-token"

    class FakeCredential:
        def get_token(self, scope):
            assert scope == "https://cognitiveservices.azure.com/.default"
            return Token()

    module = types.ModuleType("azure.identity")
    module.DefaultAzureCredential = FakeCredential
    monkeypatch.setitem(sys.modules, "azure", types.ModuleType("azure"))
    monkeypatch.setitem(sys.modules, "azure.identity", module)
    p = policy(sub("frontier", "azure", AZURE, model="gpt-5"))

    assert build_backend(p.substrates[0], secrets={})._api_key == "entra-token"


def test_azure_with_neither_key_nor_identity_says_how_to_fix_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "azure.identity", None)
    p = policy(sub("frontier", "azure", AZURE, model="gpt-5"))

    with pytest.raises(ConfigurationError, match="AZURE_OPENAI_API_KEY"):
        build_backend(p.substrates[0], secrets={})


# ── A folder named at the end of a sentence ───────────────────────────────────


def test_a_restricted_folder_named_before_a_full_stop_is_still_restricted(tmp_path):
    """Found rehearsing the Orione demo: with the local GPU down, "…sulla
    cartella /…/Progetto-Orione." was classified public and the prompt, with
    the project's name in the path, went to the frontier. The full stop had
    become part of the path, and no glob matched it."""
    from runner.kernel.types import SensitivityClass
    from runner.policy.classifier import PolicyClassifier

    folder = tmp_path / "Pratiche" / "Progetto-Orione"
    p = parse_policy(
        {
            "version": 1,
            "default": "deny",
            "classes": {"restricted": {"paths": [f"{folder}/**"]}, "public": {"default": True}},
            "substrates": [sub("local", "echo", max_class="restricted", jurisdiction="on-prem")],
            "rules": [{"match": {"class": "restricted"}, "allow": ["local"]}],
        }
    )

    klass = PolicyClassifier(p).classify_text(f"Usa la skill rfq-triage sulla cartella {folder}.")

    assert klass == SensitivityClass.RESTRICTED
