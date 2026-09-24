import { useMetrics } from "../hooks/useMetrics"
import { host, num, pct, substrates, sum } from "../lib/metrics"

/**
 * The few numbers worth seeing from every view, top right: is each model up and
 * how fast was it last, is anything running, was anything refused or sent out,
 * and is the machine the bottleneck. One click opens the Monitor.
 */
export default function MetricsPulse({ onOpen }: { onOpen: () => void }) {
  const m = useMetrics(3000)
  if (!m) return null

  const subs = substrates(m)
  const running = subs.reduce((a, s) => a + s.inFlight, 0)
  const held = sum(m, "holds_total")
  const out = sum(m, "egress_total")
  const h = host(m)

  return (
    <button className="an-pulse" onClick={onOpen} title="Open the Monitor">
      {subs.map((s) => (
        <span key={s.id} className="an-pulse__item" title={`${s.id} · ${s.model || "no inference yet"}`}>
          <span className={`an-pulse__dot ${s.up === false ? "is-down" : s.up ? "is-up" : ""}`} />
          {s.id}
          <b>{s.lastTps == null ? "—" : `${num(s.lastTps)} tok/s`}</b>
        </span>
      ))}
      {running > 0 && (
        <span className="an-pulse__item an-pulse__item--live">
          <span className="an-pulse__dot is-live" />
          {running} running
        </span>
      )}
      <span className="an-pulse__item" title="Steps held since the daemon started">
        held <b style={held ? { color: "var(--red)" } : undefined}>{held}</b>
      </span>
      <span className="an-pulse__item" title="Payloads that left this machine">
        out <b>{out}</b>
      </span>
      <span className="an-pulse__item" title={`Daemon uses ${Math.round((h.daemon ?? 0) / 1e6)} MB`}>
        CPU <b>{pct(h.cpu)}</b> · RAM <b>{pct(h.memory)}</b>
      </span>
      <span className="an-pulse__more" aria-hidden="true">›</span>
    </button>
  )
}
