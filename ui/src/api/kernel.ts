// Client for the kernel's HTTP surface (runner/kernel_api.py).
//
// Kept apart from `runner.ts`, which talks to the vault and the sync engine:
// these are the routes that answer "where did this run, and why", and mixing
// them with note CRUD would bury the one part of the app that is the product.

import { API_ORIGIN } from "./base"
import { fbAuth } from "../lib/firebase"

const BASE = `${API_ORIGIN}/api/kernel`

export class KernelError extends Error {
  constructor(readonly status: number, readonly detail: string) {
    super(detail)
  }
}

/**
 * Who is asking, when someone signed in in this browser.
 *
 * The same sign-in as cloud sync (Firebase, `akaion-prod-eu`): its ID token is
 * what a policy's `identity: {preset: akaion}` verifies, so the ledger records
 * the person instead of "anonymous". Firebase refreshes the token itself. Not
 * signed in — or the desktop app, which signs in through the browser — sends
 * nothing, and the policy decides whether anonymous is allowed. A refresh that
 * fails throws rather than quietly downgrading to anonymous.
 */
async function identity(): Promise<Record<string, string>> {
  await fbAuth.authStateReady()
  const user = fbAuth.currentUser
  return user ? { Authorization: `Bearer ${await user.getIdToken()}` } : {}
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const { headers, ...rest } = init ?? {}
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(await identity()), ...(headers as Record<string, string>) },
    ...rest,
  })
  if (!res.ok) {
    // FastAPI puts the useful sentence in `detail`; surfacing "422" alone would
    // hide the loader telling you exactly which line of the policy is wrong.
    let detail = `${res.status}`
    try {
      const body = await res.json()
      if (typeof body?.detail === "string") detail = body.detail
      else if (Array.isArray(body?.detail)) detail = body.detail.map((d: any) => d.msg).join("; ")
    } catch { /* body was not JSON */ }
    throw new KernelError(res.status, detail)
  }
  return res.json()
}

// ── Shapes ────────────────────────────────────────────────────────────────────

export interface KernelStatus {
  enforcing: boolean
  policy: string
  reason: string
  substrates?: number
  rules?: number
  default_class?: string
  decisions?: number
}

export interface PolicyClass {
  label: string
  paths: string[]
  patterns: string[]
  default: boolean
}

export interface PolicySubstrate {
  id: string
  kind: string
  jurisdiction: string
  max_class: string
  endpoint: string
  model: string
  tools: boolean
  vision: boolean
  distance: number
}

export interface PolicyRule {
  id: string
  class: string
  allow: string[]
  on_unavailable: string
  prefer: string
}

export interface PolicyDoc {
  source: string
  version: number
  default_class: string
  classes: PolicyClass[]
  substrates: PolicySubstrate[]
  rules: PolicyRule[]
  tools: { allow: Record<string, string[]>; deny_paths: string[] }
  skills: string[]
  redaction: { enabled: boolean; provider: string; endpoint: string }
}

export interface SubstrateHealth extends PolicySubstrate {
  up: boolean
  reason: string
  latency_ms: number | null
}

export interface Decision {
  seq: number
  ts: string
  run_id: string
  step_id: string
  kind: string
  outcome: string
  class: string
  substrate: string
  rule_id: string
  payload_digest: string
  detail: Record<string, any>
  hash: string
  /** Who asked, as the identity provider proved it. Empty: anonymous. */
  subject?: string
  groups?: string[]
}

/** One series of the metrics registry: a gauge/counter value, or a histogram summary. */
export interface MetricRow {
  labels: Record<string, string>
  value?: number
  count?: number
  sum?: number
  mean?: number | null
  p50?: number | null
  p95?: number | null
}

/** `GET /api/kernel/metrics`: every family, since the daemon started. */
export type Metrics = Record<string, MetricRow[]>

/** Who this window is, as the perimeter verified it — `whoami`, not a guess. */
export interface Identity {
  /** The policy refuses anonymous requests. */
  required: boolean
  providers: { kind: string; label: string; signin: string }[]
  you: { id: string; groups: string[]; via: string; verified_by: string } | null
  /** Why the credential this window sent was refused, if it was. */
  problem: string
}

/** One payload that left this machine. Held in memory by the daemon, never on disk. */
export interface Egress {
  kind: "redacted" | "briefed" | "verbatim"
  substrate: string
  jurisdiction: string
  class: string
  text: string
  redactor?: string
  replaced?: number
  labels?: Record<string, number>
  written_by?: string
}

export interface AskResult {
  response: string
  iterations: number
  tool_calls: { tool: string; input: Record<string, any>; result: any; error: boolean }[]
  placement: { class: string; outcome: string; substrate: string; reason: string } | null
  enforced: boolean
  decisions: Decision[]
  // named: files put in front of the run. shown: the subset a substrate was
  // actually allowed to look at. The two differ, and saying so is the point.
  attachments?: { named: number; shown: number }
  // What crossed, verbatim, and why nothing could.
  egress?: Egress[]
  sealed?: string
  // True when the operator stopped this run before it finished. The turns that
  // did complete are still present above.
  cancelled?: boolean
}

export interface AttachmentInfo {
  id: string
  name: string
  path: string
  bytes: number
  family: string
  format: string
  class: string
  readable: boolean
  reason?: string
  fix?: string
  preview: string
  // One line of fact from the reader that parsed it: pages, duration, sheets,
  // invoice number, who signed it.
  headline?: string
  // Whether the daemon can render a picture of this file. Known at intake so
  // the card can reserve the space instead of reflowing when one arrives.
  thumbnail?: boolean
  warnings: string[]
  media: { path: string; media_type: string; label: string; derived: boolean }[]
  metadata?: Record<string, any>
}

/** Where the window fetches the picture of an attachment. Loopback only. */
export const THUMBNAIL_URL = (id: string) => `${BASE}/attachments/${id}/thumbnail`

export interface FormatSupport {
  inbox: string
  inbox_short: string
  max_upload_mb: number
  extensions: string[]
  vision: boolean
  families: Record<string, { ready: boolean; extensions: string[]; install: string }>
  extras: Record<string, boolean>
}

// ── Calls ─────────────────────────────────────────────────────────────────────

export interface PolicySource {
  path: string
  /** The file, byte for byte. What the text editor edits. */
  text: string
  /** The same file as data, parsed by the daemon. What the fields edit. */
  document: any
  digest: string
  /** True when a structured save would drop a comment somebody wrote in the
   *  body. The editor steers to the text mode instead of deleting it. */
  body_has_comments: boolean
}

export interface ReplacePolicyBody {
  document?: any
  yaml?: string
  expected_digest?: string
}

export interface PolicyProfile {
  id: string
  title: string
  summary: string
  /** What this choice means for material leaving the machine. The sentence the
   *  person is agreeing to — shown in full, never truncated into a tooltip. */
  consequence: string
  needs_frontier: boolean
  recommended: boolean
}

export interface FrontierProviderInfo {
  id: string
  title: string
  model: string
  endpoint: string
  api_key_env: string
  jurisdiction: string
}

export interface SetupOptions {
  configured: boolean
  policy_path: string
  runtime: { endpoint: string; reachable: boolean; models: string[]; detail: string }
  suggested_model: string
  suggested_reason: string
  profiles: PolicyProfile[]
  providers: FrontierProviderInfo[]
}

export interface CreatePolicyBody {
  profile: string
  model?: string
  readable_paths?: string[]
  provider?: string
  provider_model?: string
  provider_endpoint?: string
  /** The NAME of the environment variable holding the key. Never the key: the
   *  daemon has no field that would accept one. */
  api_key_env?: string
}

export const kernel = {
  status:     () => req<KernelStatus>("/status"),
  policy:     () => req<PolicyDoc>("/policy"),

  // ── Editing the policy ──────────────────────────────────────────────────────
  // `source` gives the file both ways: as text, and as the document the server
  // already parsed — so the editor can offer fields without a YAML parser in
  // the browser, and the two views cannot disagree about what the file says.
  //
  // `replacePolicy` sends `expected_digest` so an edit made against a stale
  // read is refused rather than applied: this file is editable from a terminal
  // too, and a form submitted ten minutes ago must not silently win.
  policySource: () => req<PolicySource>("/policy/source"),
  replacePolicy: (body: ReplacePolicyBody) =>
    req<{ path: string; digest: string; backup: string; step_id: string }>("/policy", {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  // ── Onboarding ──────────────────────────────────────────────────────────────
  // `createPolicy` writes the *first* policy and 409s if one exists. See
  // runner/kernel_api.py for why that asymmetry is the safety property.
  setupOptions: () => req<SetupOptions>("/profiles"),
  createPolicy: (body: CreatePolicyBody) =>
    req<{ path: string; profile: string; model: string; consequence: string }>("/policy", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  substrates: (probe = true) => req<{ probed: boolean; substrates: SubstrateHealth[] }>(
    `/substrates?probe=${probe}`,
  ),
  ledger:     (opts?: { limit?: number; held?: boolean; runId?: string }) => {
    const qs = new URLSearchParams()
    if (opts?.limit) qs.set("limit", String(opts.limit))
    if (opts?.held) qs.set("held", "true")
    if (opts?.runId) qs.set("run_id", opts.runId)
    const q = qs.toString()
    return req<{ path: string; total: number; shown?: number; entries: Decision[] }>(
      `/ledger${q ? `?${q}` : ""}`,
    )
  },
  identity:   () => req<Identity>("/identity"),
  metrics:    () => req<Metrics>("/metrics"),
  verify:     () => req<{ path: string; ok: boolean; entries: number; problem: string; empty: boolean }>(
    "/ledger/verify",
  ),
  ask:        (
    prompt: string,
    attachments: string[] = [],
    opts: { escalate?: boolean; maxIterations?: number; runId?: string; signal?: AbortSignal } = {},
  ) =>
    req<AskResult>("/ask", {
      method: "POST",
      // Drop the request when the caller aborts, so the window frees the
      // composer the instant Stop is pressed instead of waiting out the turn.
      signal: opts.signal,
      body: JSON.stringify({
        prompt,
        attachments,
        escalate: Boolean(opts.escalate),
        max_iterations: opts.maxIterations ?? 8,
        run_id: opts.runId ?? "",
      }),
    }),

  // Stop a run started with this run_id at its next turn boundary. Best-effort:
  // pairs with aborting the fetch above, which is what makes Stop feel instant.
  cancel:     (runId: string) =>
    req<{ cancelled: boolean }>(`/ask/${encodeURIComponent(runId)}/cancel`, { method: "POST" }),

  // ── Attachments ─────────────────────────────────────────────────────────────
  formats:    () => req<FormatSupport>("/formats"),
  attachments: (limit = 30) => req<{ inbox: string; attachments: AttachmentInfo[] }>(
    `/attachments?limit=${limit}`,
  ),
  detach:     (id: string) => req<{ deleted: string }>(`/attachments/${id}`, { method: "DELETE" }),

  // No Content-Type header on purpose: the browser has to set the multipart
  // boundary itself, and the JSON default in `req` would make the daemon reject
  // the body it is about to receive.
  attach:     async (file: File): Promise<AttachmentInfo> => {
    const body = new FormData()
    body.append("file", file)
    const res = await fetch(`${BASE}/attachments`, { method: "POST", body })
    if (!res.ok) {
      let detail = `${res.status}`
      try {
        const parsed = await res.json()
        if (typeof parsed?.detail === "string") detail = parsed.detail
      } catch { /* body was not JSON */ }
      throw new KernelError(res.status, detail)
    }
    return res.json()
  },
}
