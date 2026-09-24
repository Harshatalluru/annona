import type { Metrics, MetricRow } from "../api/kernel"

/** Reading the registry snapshot: small helpers, no state. */

export const rows = (m: Metrics | null, name: string): MetricRow[] => m?.[`annona_${name}`] ?? []

export const sum = (m: Metrics | null, name: string, where: Record<string, string> = {}): number =>
  rows(m, name)
    .filter((r) => Object.entries(where).every(([k, v]) => r.labels[k] === v))
    .reduce((acc, r) => acc + (r.value ?? 0), 0)

export const one = (m: Metrics | null, name: string, where: Record<string, string> = {}): MetricRow | undefined =>
  rows(m, name).find((r) => Object.entries(where).every(([k, v]) => r.labels[k] === v))

export interface SubstrateStats {
  id: string
  up: boolean | null
  model: string
  lastTps: number | null
  tps: MetricRow | undefined
  seconds: MetricRow | undefined
  tokensIn: number
  tokensOut: number
  inFlight: number
  failures: number
}

/** Everything the registry knows, folded per substrate. */
export function substrates(m: Metrics | null): SubstrateStats[] {
  const ids = new Set<string>()
  for (const name of ["substrate_up", "inference_seconds", "requests_in_flight", "inference_failures_total"])
    rows(m, name).forEach((r) => r.labels.substrate && ids.add(r.labels.substrate))
  return [...ids].sort().map((id) => {
    const up = one(m, "substrate_up", { substrate: id })
    const last = one(m, "last_output_tokens_per_second", { substrate: id })
    return {
      id,
      up: up ? up.value === 1 : null,
      model: last?.labels.model ?? one(m, "inference_seconds", { substrate: id })?.labels.model ?? "",
      lastTps: last?.value ?? null,
      tps: one(m, "output_tokens_per_second", { substrate: id }),
      seconds: one(m, "inference_seconds", { substrate: id }),
      tokensIn: sum(m, "tokens_total", { substrate: id, direction: "in" }),
      tokensOut: sum(m, "tokens_total", { substrate: id, direction: "out" }),
      inFlight: sum(m, "requests_in_flight", { substrate: id }),
      failures: sum(m, "inference_failures_total", { substrate: id }),
    }
  })
}

export function host(m: Metrics | null) {
  const used = sum(m, "host_memory_bytes", { kind: "used" })
  const total = sum(m, "host_memory_bytes", { kind: "total" })
  return {
    cpu: one(m, "host_cpu_ratio")?.value ?? null,
    memory: total ? used / total : null,
    used,
    total,
    daemon: one(m, "process_resident_bytes")?.value ?? null,
    info: one(m, "host_info")?.labels ?? null,
  }
}

export const pct = (x: number | null | undefined) => (x == null ? "—" : `${Math.round(x * 100)}%`)
export const num = (x: number | null | undefined, digits = 1) =>
  x == null ? "—" : x >= 1000 ? `${(x / 1000).toFixed(1)}k` : x.toFixed(digits).replace(/\.0$/, "")
export const bytes = (x: number | null | undefined) =>
  x == null ? "—" : x >= 1e9 ? `${(x / 1e9).toFixed(1)} GB` : `${Math.round(x / 1e6)} MB`
