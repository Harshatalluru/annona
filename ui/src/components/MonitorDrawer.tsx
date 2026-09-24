import { useEffect } from "react"
import { MonitorBody } from "./views/MonitorView"

/**
 * The Monitor over whatever you were doing, not instead of it: the pulse opens
 * this, and closing it leaves the conversation exactly where it was.
 */
export default function MonitorDrawer({ onClose, onFullPage }: { onClose: () => void; onFullPage: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose() }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  return (
    <div className="an-drawer-backdrop" onClick={onClose}>
      <aside className="an-drawer" role="dialog" aria-label="Monitor" onClick={(e) => e.stopPropagation()}>
        <div className="an-drawer__head">
          <div>
            <div className="ak-view-title">Monitor</div>
            <div className="ak-view-sub">Live, since the daemon started</div>
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            <button className="btn" onClick={onFullPage}>Full page</button>
            <button className="btn" onClick={onClose} aria-label="Close">✕</button>
          </div>
        </div>
        <div className="an-drawer__body"><MonitorBody /></div>
      </aside>
    </div>
  )
}
