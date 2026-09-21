import { API_ORIGIN } from "./base"

const BASE = `${API_ORIGIN}/api`

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  })
  if (!res.ok) throw new Error(`${res.status} ${path}`)
  if (res.status === 204) return undefined as T
  return res.json()
}

// ── Brain ─────────────────────────────────────────────────────────────────────

export interface BrainNote {
  id: string
  title: string
  content: string
  tags: string[]
  sync_status: "local_only" | "pending_sync" | "synced" | "sync_error"
  cot_message_id: string | null
  cot_cluster_id: string | null
  cot_cluster_name: string | null
  created_at: string
  updated_at: string
  synced_at: string | null
  sync_error: string | null
}

export const brain = {
  list:     (params?: { sync_status?: string; tag?: string }) => {
    const qs = params ? "?" + new URLSearchParams(params as Record<string, string>).toString() : ""
    return req<BrainNote[]>(`/brain/notes${qs}`)
  },
  get:      (id: string) => req<BrainNote>(`/brain/notes/${id}`),
  create:   (data: Pick<BrainNote, "title" | "content" | "tags">) =>
    req<BrainNote>("/brain/notes", { method: "POST", body: JSON.stringify(data) }),
  update:   (id: string, data: Partial<Pick<BrainNote, "title" | "content" | "tags">>) =>
    req<BrainNote>(`/brain/notes/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  delete:   (id: string) => req<void>(`/brain/notes/${id}`, { method: "DELETE" }),
  markSync: (id: string) => req<BrainNote>(`/brain/notes/${id}/mark-sync`, { method: "POST" }),
  search:   (q: string) => req<BrainNote[]>(`/brain/search?q=${encodeURIComponent(q)}`),
}

// ── Sync ──────────────────────────────────────────────────────────────────────

export interface SyncStatus {
  pending: number
  synced: number
  local_only: number
  errors: number
  last_push: string | null
}

export const sync = {
  status: () => req<SyncStatus>("/sync/status"),
  push:   () => req<{ synced: number; errors: number }>("/sync/push", { method: "POST" }),
  pushOne: (id: string) => req<BrainNote>(`/sync/push/${id}`, { method: "POST" }),
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export interface AuthStatus {
  authenticated: boolean
  email: string | null
  runner_id: string | null
  mode?: "local" | "cloud"
}

export const auth = {
  status: () => req<AuthStatus>("/auth/status"),
  save: (data: { firebase_token: string; refresh_token: string; expires_in?: number; email?: string }) =>
    req<AuthStatus>("/auth/save", { method: "POST", body: JSON.stringify(data) }),
  logout: () => req<{ authenticated: false }>("/auth/logout", { method: "POST" }),
}

// ── Runner mode ───────────────────────────────────────────────────────────────

export interface RunnerMode {
  mode: "local" | "cloud"
  cloud_enabled: boolean
  authenticated: boolean
  vault_path: string
}

export const runner = {
  mode: () => req<RunnerMode>("/runner/mode"),
}

// ── Link inbox ────────────────────────────────────────────────────────────────
// Answers Agents Studio asked for that this machine's policy kept here. The list
// says who asked and why the answer stayed; only the detail carries the answer.

export interface InboxItem {
  job_id: string
  title: string
  requested_by: string
  skill: string | null
  release: string
  placement_class: string
  received: number  // Unix seconds
}

export interface InboxRecord {
  job_id: string
  title: string
  instruction: string
  requested_by: { email?: string; role?: string; organization_id?: string }
  skill?: string | null
  response: string
  release: string
  placement: { class: string; outcome: string; substrate: string }
  ledger_head: string
  received: number
}

export const link = {
  inbox: () => req<InboxItem[]>("/link/inbox"),
  get:   (jobId: string) => req<InboxRecord>(`/link/inbox/${encodeURIComponent(jobId)}`),
}
