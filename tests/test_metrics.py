"""Tokens, seconds and decisions as numbers — and never a path, a prompt or a person.

- each adapter reads the usage its provider already returns;
- the router turns it into metrics and a `usage` ledger entry of numbers only;
- /metrics is valid Prometheus text, its label sets are closed, and after a run
  over restricted material it names none of it;
- an optional token guards /metrics for scrapers on another host.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from runner.audit.metrics import METRICS
from runner.capability.backends.anthropic import AnthropicBackend
from runner.capability.backends.ollama import _usage as ollama_usage
from runner.capability.backends.openai_compatible import _usage as openai_usage
from runner.kernel.types import Completion, CompletionRequest, Usage
from tests.test_enforcement import ScriptedSubstrate
from tests.test_examples import ORIONE, Tools, perimeter
from tests.test_local_api_ui_mount import _build_app


@pytest.fixture(autouse=True)
def fresh_metrics():
    METRICS.reset()
    yield
    METRICS.reset()


# ── Usage from what each provider already returns ────────────────────────────


def test_ollama_usage_uses_generation_time_for_tokens_per_second():
    usage = ollama_usage(
        {"prompt_eval_count": 1200, "eval_count": 300, "eval_duration": 10_000_000_000}, 14.0
    )
    assert (usage.input_tokens, usage.output_tokens) == (1200, 300)
    assert usage.tokens_per_second == 30.0, "300 tokens in 10 s of generation, not 14 s of wall"


def test_openai_compatible_usage_block():
    usage = openai_usage({"usage": {"prompt_tokens": 50, "completion_tokens": 20}}, 2.0)
    assert (usage.input_tokens, usage.output_tokens, usage.tokens_per_second) == (50, 20, 10.0)


def test_anthropic_usage_from_the_sdk_response():
    response = SimpleNamespace(
        content=[{"type": "text", "text": "ok"}],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=40, output_tokens=8),
    )
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **_: response))
    completion = AnthropicBackend(client=client, model="m").complete(
        CompletionRequest(system="s", transcript=())
    )
    assert completion.usage.input_tokens == 40 and completion.usage.output_tokens == 8


# ── The router: metrics and a ledger entry of numbers ────────────────────────


def test_an_inference_becomes_metrics_and_a_usage_entry(tmp_path):
    answer = Completion(
        text_parts=("ok",),
        usage=Usage(input_tokens=900, output_tokens=120, seconds=6.0, generation_seconds=4.0),
    )
    local = ScriptedSubstrate("local-gpu", [answer], local=True)
    _, loop = perimeter(
        tmp_path, "rfq-orione", local=local, frontier=ScriptedSubstrate("f"), tools=Tools()
    )

    loop.run(f"Nella cartella {ORIONE} confronta mail e specifica.")

    text = METRICS.prometheus()
    assert (
        'annona_tokens_total{substrate="local-gpu",model="qwen2.5:14b",direction="out"} 120' in text
    )
    assert (
        'annona_last_output_tokens_per_second{substrate="local-gpu",model="qwen2.5:14b"} 30' in text
    )
    assert 'annona_substrate_up{substrate="local-gpu"} 1' in text
    entries = [json.loads(line) for line in (tmp_path / "ledger.jsonl").read_text().splitlines()]
    usage = next(e for e in entries if e["kind"] == "usage")
    assert usage["detail"]["tokens_out"] == 120 and usage["detail"]["tokens_per_second"] == 30.0
    assert set(usage["detail"]) == {
        "model",
        "tokens_in",
        "tokens_out",
        "seconds",
        "tokens_per_second",
    }


def test_after_a_run_over_restricted_material_metrics_name_none_of_it(tmp_path):
    local = ScriptedSubstrate(
        "local-gpu", [Completion(text_parts=("ok",), usage=Usage(1, 1, 1.0))], local=True
    )
    _, loop = perimeter(
        tmp_path, "rfq-orione", local=local, frontier=ScriptedSubstrate("f"), tools=Tools()
    )

    loop.run(f"Nella cartella {ORIONE} confronta mail e specifica.")

    for exposition in (METRICS.prometheus(), str(METRICS.snapshot())):
        assert "/" not in exposition.replace("# HELP", "").split("annona_")[0]
        for leak in ("Pratiche", "Orione", "/Users", "nella cartella"):
            assert leak.lower() not in exposition.lower()


# ── The exposition ───────────────────────────────────────────────────────────


def test_histograms_are_cumulative_and_labels_are_closed():
    METRICS.observe("annona_inference_seconds", 0.3, substrate="s", model="m")
    METRICS.observe("annona_inference_seconds", 7.0, substrate="s", model="m")
    text = METRICS.prometheus()

    assert 'annona_inference_seconds_bucket{substrate="s",model="m",le="0.5"} 1' in text
    assert 'annona_inference_seconds_bucket{substrate="s",model="m",le="10"} 2' in text
    assert 'annona_inference_seconds_count{substrate="s",model="m"} 2' in text
    with pytest.raises(ValueError, match="takes labels"):
        METRICS.inc("annona_holds_total", reason="sealed", path="/Users/x")


def test_metrics_endpoint_and_its_optional_token(tmp_path, monkeypatch):
    METRICS.inc("annona_holds_total", reason="sealed")
    client = TestClient(_build_app(tmp_path, ui_dist=None))

    open_ = client.get("/metrics")
    assert open_.status_code == 200
    assert open_.headers["content-type"].startswith("text/plain")
    assert 'annona_holds_total{reason="sealed"} 1' in open_.text

    monkeypatch.setenv("ANNONA_METRICS_TOKEN", "s3cret")
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer s3cret"}).status_code == 200
