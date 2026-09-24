import { useEffect, useState } from "react"
import { API_ORIGIN } from "../../api/base"
import { kernel } from "../../api/kernel"
import { useMetrics } from "../../hooks/useMetrics"
import { bytes, host, num, pct, rows, substrates, sum } from "../../lib/metrics"
import type { MetricRow } from "../../api/kernel"

/**
 * Everything the kernel counts, since the daemon started — the same registry
 * `/metrics` exposes to Prometheus, so what is on screen is what a dashboard
 * would show. Numbers only: no path, prompt or person is ever a label.
 */
export default function MonitorView() {
  return (
    <>
      <div className="view-header">
        <div className="view-header-left">
          <div className="ak-view-title">Monitor</div>
          <div className="ak-view-sub">Speed, load, the machine and the perimeter — live, since the daemon started</div>
        </div>
      </div>
      <div className="view-body"><MonitorBody /></div>
    </>
  )
}

/** The Monitor without its page chrome: the full view, and the drawer the pulse opens. */
export function MonitorBody() {
  const m = useMetrics(2000)
  const subs = substrates(m)
  const h = host(m)
  // Why a substrate is down is text, so it is not a metric: ask the kernel.
  const [why, setWhy] = useState<Record<string, string>>({})
  const downIds = subs.filter((s) => s.up === false).map((s) => s.id).join(",")
  useEffect(() => {
    if (!downIds) return
    kernel.substrates(false)
      .then((r) => setWhy(Object.fromEntries(r.substrates.map((s) => [s.id, s.reason]))))
      .catch(() => { /* the red dot still says it */ })
  }, [downIds])

  return (
    <>
        {!m && <div className="an-dim">Reading the registry…</div>}

        <div className="an-h4">Models <span>per substrate: generation speed, seconds per inference, tokens</span></div>
        <div className="an-mon-grid">
          {subs.length === 0 && <div className="an-dim">No substrate has answered yet. Ask something.</div>}
          {subs.map((s) => (
            <div key={s.id} className="an-mon-card">
              <div className="an-mon-card__head">
                <span className={`an-pulse__dot ${s.up === false ? "is-down" : s.up ? "is-up" : ""}`} />
                <b>{s.id}</b>
                <span className="an-dim">{s.model}</span>
                {s.inFlight > 0 && <span className="an-mon-live">{s.inFlight} running</span>}
              </div>
              {s.up === false && why[s.id] && <div className="an-mon-why">{why[s.id]}</div>}
              <div className="an-mon-big">
                {s.lastTps == null ? "—" : num(s.lastTps)}<small> tok/s last</small>
              </div>
              <Quantiles label="tok/s" row={s.tps} />
              <Quantiles label="seconds" row={s.seconds} />
              <div className="an-mon-kv">
                <span>tokens in</span><b>{num(s.tokensIn, 0)}</b>
                <span>tokens out</span><b>{num(s.tokensOut, 0)}</b>
                <span>inferences</span><b>{s.seconds?.count ?? 0}</b>
                <span>failures</span><b style={s.failures ? { color: "var(--red)" } : undefined}>{s.failures}</b>
              </div>
            </div>
          ))}
        </div>

        <div className="an-h4">This machine <span>CPU and memory; what Ollama holds, and how much of it on the GPU</span></div>
        <div className="an-mon-grid">
          <div className="an-mon-card">
            <Bar label="CPU" ratio={h.cpu} />
            <Bar label="Memory" ratio={h.memory} note={`${bytes(h.used)} of ${bytes(h.total)}`} />
            <div className="an-mon-kv"><span>Annona daemon</span><b>{bytes(h.daemon)}</b></div>
          </div>
          <div className="an-mon-card">
            <div className="an-mon-card__head"><b>Loaded in Ollama</b></div>
            {rows(m, "ollama_loaded_bytes").filter((r) => r.labels.kind === "total").length === 0 && (
              <div className="an-dim">No model loaded — the next request pays the load time.</div>
            )}
            {rows(m, "ollama_loaded_bytes").filter((r) => r.labels.kind === "total").map((r) => {
              const gpu = sum(m, "ollama_loaded_bytes", { model: r.labels.model, kind: "gpu" })
              return (
                <Bar key={r.labels.model} label={r.labels.model} ratio={r.value ? gpu / r.value : null}
                     note={`${bytes(r.value)} · ${pct(r.value ? gpu / r.value : null)} on GPU`} />
              )
            })}
          </div>
        </div>

        <div className="an-h4">Perimeter <span>decisions, refusals, what left, what was redacted</span></div>
        <div className="an-mon-grid">
          <Counts title="Decisions" rows={rows(m, "decisions_total")} label={(l) => `${l.kind} · ${l.outcome} · ${l.class}`} />
          <Counts title="Held, by reason" rows={rows(m, "holds_total")} label={(l) => l.reason} tone="var(--red)" />
          <Counts title="Left this machine" rows={rows(m, "egress_total")} label={(l) => `${l.kind} → ${l.substrate}`} />
          <Counts title="Identifiers redacted" rows={rows(m, "redacted_identifiers_total")} label={(l) => l.label} />
        </div>

        <Integrations />
    </>
  )
}

function Quantiles({ label, row }: { label: string; row: MetricRow | undefined }) {
  return (
    <div className="an-mon-q">
      <span className="an-dim">{label}</span>
      <span>p50 <b>{row?.p50 == null ? "—" : num(row.p50)}</b></span>
      <span>p95 <b>{row?.p95 == null ? "—" : row.p95 === Infinity ? "∞" : num(row.p95)}</b></span>
      <span>mean <b>{row?.mean == null ? "—" : num(row.mean)}</b></span>
    </div>
  )
}

function Bar({ label, ratio, note }: { label: string; ratio: number | null; note?: string }) {
  const tone = ratio == null ? "var(--text-sub)" : ratio > 0.9 ? "var(--red)" : ratio > 0.7 ? "#e0a33a" : "var(--green)"
  return (
    <div className="an-mon-bar">
      <div className="an-mon-bar__row"><span>{label}</span><b>{note ?? pct(ratio)}</b></div>
      <div className="an-mon-bar__track">
        <div style={{ width: `${Math.min(100, (ratio ?? 0) * 100)}%`, background: tone }} />
      </div>
    </div>
  )
}

function Counts({ title, rows, label, tone }: {
  title: string; rows: MetricRow[]; label: (l: Record<string, string>) => string; tone?: string
}) {
  const sorted = [...rows].sort((a, b) => (b.value ?? 0) - (a.value ?? 0))
  return (
    <div className="an-mon-card">
      <div className="an-mon-card__head"><b>{title}</b><span className="an-dim">{sorted.reduce((a, r) => a + (r.value ?? 0), 0)}</span></div>
      {sorted.length === 0 && <div className="an-dim">None yet.</div>}
      <div className="an-mon-kv">
        {sorted.map((r) => (
          <FragmentRow key={label(r.labels)} k={label(r.labels)} v={r.value ?? 0} tone={tone} />
        ))}
      </div>
    </div>
  )
}

function FragmentRow({ k, v, tone }: { k: string; v: number; tone?: string }) {
  return <><span>{k}</span><b style={tone ? { color: tone } : undefined}>{v}</b></>
}

const SCRAPE = `scrape_configs:
  - job_name: annona
    scrape_interval: 15s
    static_configs:
      - targets: ["localhost:7070"]
    # when ANNONA_METRICS_TOKEN is set on the daemon:
    # authorization: { type: Bearer, credentials: "<token>" }`

/** Attach the same numbers to the stack the company already runs. */
function Integrations() {
  const [sample, setSample] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const test = async () => {
    try {
      const r = await fetch(`${API_ORIGIN}/metrics`)
      const text = await r.text()
      setSample(r.ok
        ? text.split("\n").filter((l) => l && !l.startsWith("#")).slice(0, 12).join("\n")
        : `${r.status}: ${text}`)
    } catch (e) {
      setSample(String(e))
    }
  }

  return (
    <>
      <div className="an-h4">Integrations <span>Prometheus text at /metrics — Grafana, GCP Managed Prometheus, Datadog read it as is</span></div>
      <div className="an-mon-card">
        <pre className="an-mon-code">{SCRAPE}</pre>
        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <button className="btn" onClick={() => { void navigator.clipboard.writeText(SCRAPE); setCopied(true) }}>
            {copied ? "Copied" : "Copy Prometheus job"}
          </button>
          <button className="btn" onClick={() => void test()}>Test scrape</button>
        </div>
        {sample && <pre className="an-mon-code" style={{ marginTop: 8 }}>{sample}</pre>}
        <div className="an-dim an-note">
          GCP: a <code>PodMonitoring</code> (GKE) or the Ops Agent <code>prometheus</code> receiver
          (Compute Engine, a DGX in a VPC) pointed at the same target. Datadog: the OpenMetrics
          check. /metrics binds with the API — loopback unless you put a tunnel or proxy in front.
        </div>
      </div>
    </>
  )
}
