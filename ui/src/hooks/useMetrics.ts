import { useEffect, useState } from "react"
import { kernel, type Metrics } from "../api/kernel"

/** The registry snapshot, refreshed every `everyMs`. `null` until the first answer. */
export function useMetrics(everyMs = 3000): Metrics | null {
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const m = await kernel.metrics()
        if (alive) setMetrics(m)
      } catch { /* daemon restarting: keep the last numbers on screen */ }
    }
    void tick()
    const id = setInterval(tick, everyMs)
    return () => { alive = false; clearInterval(id) }
  }, [everyMs])
  return metrics
}
