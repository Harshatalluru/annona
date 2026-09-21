import { useEffect, useState } from "react"
import { link, InboxItem, InboxRecord } from "../../api/runner"
import { InboxIcon } from "../ui/Icons"
import { API_ORIGIN } from "../../api/base"

/**
 * Inbox — the answers Agents Studio asked for and this machine kept.
 *
 * When a linked run reads something above `link.release`, Studio is told the
 * job finished and where it ran, and the answer is written here instead. Studio
 * points people at this machine to read it; until now that meant a terminal and
 * `annona link show`. The list says who asked and why the answer stayed; the
 * answer itself is fetched only when one is opened.
 */

function fmt(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString("it-IT", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  })
}

function Chip({ label, value }: { label: string; value: string }) {
  return (
    <span className="an-chip">
      <span className="an-chip__k">{label}</span>
      <span className="an-chip__v">{value}</span>
    </span>
  )
}

function Label({ children }: { children: string }) {
  return <div className="text-sub" style={{ fontSize: 11, margin: "16px 0 6px" }}>{children}</div>
}

function Detail({ r }: { r: InboxRecord }) {
  return (
    <>
      <div style={{ fontSize: 18, fontWeight: 600, color: "var(--text)", marginBottom: 4 }}>
        {r.title || r.job_id}
      </div>
      <div className="text-muted" style={{ fontSize: 12 }}>
        {r.requested_by?.email || "unknown"} · {fmt(r.received)} · <code>{r.job_id.slice(0, 8)}</code>
      </div>

      <Label>Why it stayed here</Label>
      <div className="an-held">{r.release || "the policy withheld it"}</div>
      <div className="an-answer__meta" style={{ marginTop: 8 }}>
        <Chip label="class" value={r.placement?.class || "—"} />
        <Chip label="ran on" value={r.placement?.substrate || "—"} />
        {r.skill && <Chip label="skill" value={r.skill} />}
      </div>

      <Label>Instruction</Label>
      <div className="card an-answer__text">{r.instruction}</div>

      <Label>Answer</Label>
      <div className="card an-answer__text">{r.response || "(no answer)"}</div>
    </>
  )
}

export default function InboxView() {
  const [items, setItems]       = useState<InboxItem[] | null>(null)
  const [runnerDown, setRunnerDown] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const [record, setRecord]     = useState<InboxRecord | null>(null)

  useEffect(() => {
    link.inbox().then(setItems).catch(() => setRunnerDown(true))
  }, [])

  useEffect(() => {
    let current = true
    setRecord(null)
    if (selected) {
      link.get(selected)
        .then((r) => { if (current) setRecord(r) })
        .catch(() => { /* gone since the list was read */ })
    }
    return () => { current = false }
  }, [selected])

  return (
    <>
      <div className="ak-view-header">
        <div>
          <div className="ak-view-title">Inbox</div>
          <div className="ak-view-sub">
            Answers Agents Studio asked for but this machine's policy kept here
          </div>
        </div>
      </div>

      {runnerDown ? (
        <div className="view-body">
          <div className="card">
            <p className="text-muted">Daemon not reachable on <code>{API_ORIGIN.replace(/^https?:\/\//, "")}</code>.</p>
          </div>
        </div>
      ) : items === null ? (
        <div className="view-body"><p className="text-sub">Loading…</p></div>
      ) : items.length === 0 ? (
        <div className="ak-empty">
          <span className="ak-empty__icon"><InboxIcon size={64} /></span>
          <h2 className="ak-empty__title">Nothing kept here</h2>
          <p className="ak-empty__subtitle">
            When Agents Studio runs a job on this machine and the answer draws on material
            above what the policy lets go back, Studio is told the job finished and the
            answer stays here, for you to read.
          </p>
        </div>
      ) : (
        <div className="brain-split" style={{ flex: 1, overflow: "hidden" }}>
          <div className="brain-list-panel">
            <div className="ak-note-list">
              {items.map((it) => (
                <div
                  key={it.job_id}
                  className={`ak-note-card ${selected === it.job_id ? "selected" : ""}`}
                  onClick={() => setSelected(it.job_id)}
                >
                  <div className="ak-note-card__title">{it.title || it.job_id.slice(0, 8)}</div>
                  <div className="ak-note-card__preview">{it.release}</div>
                  <div className="ak-note-card__footer">
                    <span>{it.requested_by}</span>
                    <span className="ak-note-card__time">{fmt(it.received)}</span>
                  </div>
                  {it.skill && (
                    <div className="ak-note-card__tags">
                      <span className="ak-note-tag">{it.skill}</span>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>

          <div className="brain-editor-panel">
            {record ? (
              <Detail r={record} />
            ) : (
              <div className="ak-empty">
                <p className="ak-empty__subtitle">Pick an answer from the list.</p>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  )
}
