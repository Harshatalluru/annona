# Observability — tokens/s, concurrency, the machine, and a /metrics your stack already reads

Status: proposal, 2026-09-24. Tracked in the issues linked at the end.

## What exists today

| Signal | Where | Gap |
|---|---|---|
| `GET /health` | `local_api.py:129` | liveness only |
| `GET /api/kernel/status` | `kernel_api.py:538` | `enforcing`, `held`, `last_decision_at`; re-reads the whole ledger per call (O(n)) |
| substrate `latency_ms` | `placement/registry.py:51` | measured by the probe only; real inference calls `mark_up()` with 0 (`router.py:312`) |
| tokens, timings | — | `Completion` (`kernel/types.py:210`) has no usage; Ollama's `eval_count`/`eval_duration`, OpenAI's and Anthropic's `usage` are dropped in the adapters |
| concurrency | — | `/ask` runs on the threadpool (~40 threads), no queue, no per-substrate limit, no in-flight count |
| CPU / GPU / memory | — | `psutil` is a dependency (`requirements.txt:70`) and nothing imports it |
| Prometheus / OTel | — | none; `running-on-a-server.md:56` names `/metrics` as a good first contribution |
| logs | `main.py:136` | loguru, plain text, stdout + `logs/annona.log` |

## What an operator should see

One question per signal, in the order they get asked:

1. **Is it enforcing, and is it deciding?** (already: `enforcing`, `last_decision_at`)
2. **How fast is each model, right now?** tokens/s out, time to first token, seconds per inference — per substrate and model.
3. **How loaded is it?** requests in flight, waiting, and for how long; per substrate.
4. **Is the machine the bottleneck?** CPU, RAM, GPU utilisation, VRAM, temperature; which models Ollama holds in memory.
5. **What crossed?** egress by kind (verbatim, redacted), identifiers redacted by label, holds by reason.
6. **Is the record whole?** ledger entries, last verify result.

## Design

### 1 · Usage on every completion

`Completion` gains `usage: Usage | None` — `input_tokens`, `output_tokens`, `seconds`,
`first_token_seconds` (when streaming). Each adapter fills it from what the provider
already returns:

| Adapter | Source |
|---|---|
| Ollama | `prompt_eval_count`, `eval_count`, `eval_duration` (ns) → tokens/s = `eval_count / eval_duration` |
| OpenAI-compatible (vLLM, Vertex Gemini, Azure) | `usage.prompt_tokens`, `usage.completion_tokens`; wall time measured |
| Anthropic / Bedrock / Vertex Claude | `usage.input_tokens`, `usage.output_tokens`; wall time measured |

The router records `tokens_in`, `tokens_out`, `ms` in the `inference` ledger entry's
`detail` — numbers only, never text — and calls `mark_up(substrate, latency_ms)` with the
real latency.

### 2 · A metrics registry, two expositions

One in-process registry (`runner/audit/metrics.py`, L2: counters and histograms, no I/O),
fed by the router, the gate, the tracking executor and a resource sampler. Exposed as:

- **`GET /metrics`** — Prometheus text format, via `prometheus_client`. What Prometheus,
  Grafana Agent/Alloy, VictoriaMetrics, Datadog's OpenMetrics check, and **Google Cloud
  Managed Service for Prometheus** all scrape without an adapter.
- **`GET /api/kernel/metrics`** — the same numbers as JSON, for the UI.
- **OTLP (optional extra `annona[otel]`)** — when `OTEL_EXPORTER_OTLP_ENDPOINT` is set,
  metrics and one trace per run (spans: classify → place → inference → tool call). Reaches
  Grafana Tempo, Datadog, Honeycomb, and GCP Cloud Trace/Monitoring through an OTel
  Collector with the `googlecloud` exporter.

Metric names (labels are closed sets — never a path, a prompt, a user or a file name:
cardinality and privacy are the same rule):

```
annona_inference_seconds{substrate,model,outcome}          histogram
annona_tokens_total{substrate,model,direction="in|out"}    counter
annona_output_tokens_per_second{substrate,model}           histogram
annona_first_token_seconds{substrate,model}                histogram
annona_requests_in_flight{substrate}                       gauge
annona_queue_wait_seconds{substrate}                       histogram
annona_decisions_total{kind,outcome,class}                 counter
annona_holds_total{reason="sealed|unavailable|no_rule|redactor"}  counter
annona_egress_total{kind="verbatim|redacted",substrate}    counter
annona_redacted_identifiers_total{label}                   counter
annona_substrate_up{substrate}                             gauge
annona_substrate_probe_seconds{substrate}                  histogram
annona_memory_retrievals_total                             counter
annona_ledger_entries                                      gauge
annona_ledger_verified                                     gauge (1 ok, 0 broken)
annona_host_cpu_ratio / annona_host_memory_bytes{kind}     gauge
annona_gpu_utilization_ratio{gpu} / annona_gpu_memory_bytes{gpu,kind="used|total"}
annona_gpu_temperature_celsius{gpu} / annona_gpu_power_watts{gpu}
annona_ollama_loaded_bytes{model,kind="ram|vram"}          gauge
```

### 3 · The machine

A sampler thread (every 5 s, off by default in tests):

- **CPU, RAM, process RSS** — `psutil`, already installed.
- **NVIDIA (DGX Spark, servers)** — NVML via `nvidia-ml-py` (optional extra
  `annona[gpu]`): utilisation, memory, temperature, power per GPU.
- **Apple Silicon** — `ioreg -r -c IOAccelerator` exposes "Device Utilization %" without
  sudo; unified memory from `psutil`. (`powermetrics` would need root: not used.)
- **Ollama** — `GET /api/ps`: which models are loaded, and how much sits in VRAM. Works on
  every platform and answers "why is the second model slow" (it was evicted).

### 4 · Concurrency with a ceiling

`substrates[].max_concurrency` (default: unlimited for remote, `1` for a local Ollama
unless set): a per-substrate semaphore in the router. A request that waits records
`annona_queue_wait_seconds`; one that waits past `queue_timeout` is **held** with reason
`unavailable`, like any other unavailability — never spilled to another substrate, because
that would be failover by load, which the policy did not grant. The UI shows the queue.

### 5 · The Monitor view

A new view beside Ask and Perimeter, fed by `/api/kernel/metrics` every 2 s:

- one card per substrate: tokens/s (sparkline), p50/p95 seconds per inference, in flight /
  waiting, up/down;
- the machine: CPU, RAM, GPU %, VRAM, temperature; models loaded in Ollama;
- the perimeter: holds and egress in the last hour, identifiers redacted by label, ledger
  verified;
- **Integrations**: copy-paste snippets with a "test" button (it scrapes `/metrics` once
  and shows the first lines) for Prometheus, Grafana, GCP Managed Prometheus, Datadog,
  OTLP.

### 6 · Attach in a few clicks

Shipped under `deploy/`:

- `deploy/prometheus/annona.yml` — scrape job; `deploy/grafana/annona-dashboard.json` —
  the dashboard (tokens/s, latency, queue, GPU, holds, egress);
- `docker compose --profile observability up -d` — Prometheus + Grafana preloaded with
  the dashboard, bound to loopback;
- GCP: a `PodMonitoring` manifest (GKE) and an Ops Agent `prometheus` receiver snippet
  (Compute Engine / DGX in a VPC), plus the same dashboard as a Cloud Monitoring JSON;
- Datadog: `openmetrics.d/conf.yaml`.

`/metrics` binds with the API (loopback by default); a scraper on another host needs the
same tunnel or authenticating proxy the docs already require, or `ANNONA_METRICS_TOKEN`
for a bearer check on that one route.

## Acceptance

- Ten concurrent `/ask` on a local 14B: `/metrics` shows `annona_requests_in_flight` = 1,
  `annona_queue_wait_seconds` growing, tokens/s per request in a plausible band, no request
  on the frontier.
- `grep -E '/Users|Pratiche|@' /metrics` returns nothing after the Orione and Memoria runs.
- The Grafana dashboard in `docker compose --profile observability` shows data within 30 s
  of the first `/ask`.
- `make check` covers: usage parsed from each adapter's fixture, label sets closed,
  semaphore holds on timeout.

## Plan

| Step | Scope | Estimate |
|---|---|---|
| O1 | `Usage` on `Completion` from the three adapters; ledger `detail` gets tokens and ms; real latency to the registry | 1 day |
| O2 | metrics registry + `/metrics` + `/api/kernel/metrics`; status stops re-reading the whole ledger | 1 day |
| O3 | resource sampler: psutil, NVML extra, Apple `ioreg`, Ollama `/api/ps` | 1 day |
| O4 | `max_concurrency` + queue + hold on timeout | 1 day |
| O5 | Monitor view in the UI, with the Integrations panel | 2 days |
| O6 | `deploy/`: Prometheus, Grafana dashboard, compose profile, GCP and Datadog snippets | 1 day |
| O7 | OTLP extra: metrics + one trace per run | 1 day |
