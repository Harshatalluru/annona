/**
 * What the company's memory contributed to this answer.
 *
 * Shown for every `memory_search` call — the automatic lookup before the first
 * turn and any the model made itself. Relations first, because they are the
 * reason a run became sealed or a draft was refused ("Nordika — partner_of →
 * Veloce"); the files after, because they are what a person checks. The
 * passages stay one click away: they are evidence, not the answer.
 */
import { useState } from "react"

type Fact = { fact: string; evidence: string; source: string }
type Passage = { source: string; text: string; score: number }
type MemoryResult = { success: boolean; facts?: Fact[]; results?: Passage[]; sources?: string[] }
type Call = { tool: string; result: unknown; error: boolean }

const base = (path: string) => path.split("/").pop() || path

export function MemoryPanel({ calls }: { calls: Call[] }) {
  const [open, setOpen] = useState(false)
  const results = calls
    .filter((c) => c.tool === "memory_search" && !c.error)
    .map((c) => c.result as MemoryResult)
    .filter((r) => r && r.success)

  const facts = results.flatMap((r) => r.facts ?? [])
  const passages = results.flatMap((r) => r.results ?? [])
  const sources = [...new Set(results.flatMap((r) => r.sources ?? []))]
  if (!sources.length) return null

  return (
    <div className="an-memory">
      <div className="an-memory__head">
        <span className="an-memory__title">Retrieved from memory</span>
        <span className="an-memory__count">
          {sources.length} file{sources.length > 1 ? "s" : ""}
          {facts.length > 0 && ` · ${facts.length} relation${facts.length > 1 ? "s" : ""}`}
        </span>
        {passages.length > 0 && (
          <button className="an-link" onClick={() => setOpen(!open)}>
            {open ? "hide passages" : "show passages"}
          </button>
        )}
      </div>

      {facts.length > 0 && (
        <ul className="an-memory__facts">
          {facts.map((f, i) => (
            <li key={i} title={f.evidence}>
              <span className="an-memory__fact">{f.fact}</span>
              <span className="an-memory__src">{base(f.source)}</span>
            </li>
          ))}
        </ul>
      )}

      <div className="an-memory__files">
        {sources.map((s) => (
          <code key={s} title={s}>{base(s)}</code>
        ))}
      </div>

      {open && (
        <div className="an-memory__passages">
          {passages.map((p, i) => (
            <div key={i} className="an-memory__passage">
              <div className="an-memory__src">{base(p.source)}</div>
              <div>{p.text}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
