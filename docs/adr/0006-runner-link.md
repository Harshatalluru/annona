# 0006 — Link a runner to Agents Studio: outbound only, its own credential, the policy decides what comes back

- **Status:** Accepted
- **Date:** 2026-09-21
- **Deciders:** Akaion AI Lab

## Context

Someone using Agents Studio wants to pick a machine they own — a laptop, a rack
server, a DGX called `dgx1` — send it a task, close the browser, and find the
result later. The machine holds the material; Studio holds the people, the roles
and the departments.

Two earlier attempts at this exist and neither is usable:

- **The backend calls the runner** (`RunnerAgent`, `POST {callback_url}/execute`).
  It needs an inbound port on the machine, which on a DGX behind a corporate
  firewall means a hole nobody will approve, and it authenticates nothing: the
  runner route it calls does not exist any more.
- **The browser calls the runner** (`src/api/annona.ts`, used by Kai). Secure and
  shipped, but the browser *is* the transport: close the tab and the run has no
  one to report to. It cannot detach.

And the runner half of the old registration contract authenticated with the
user's Firebase ID token — a one-hour credential belonging to a person, used by a
machine, with every permission that person has.

## Decision

A **link**: the runner dials out to Studio, holds a credential of its own, pulls
work addressed to it, and reports back what its own policy allows it to report.

```
Studio (browser)         backend-ai (control plane)              runner (dgx1)
  │ POST /jobs ─────────▶ job queued, requested_by = person         │
  │                       │                ◀──── POST /claim ───────┤ outbound HTTPS only
  │ (close the tab)       │ job, lease ─────────────────────────────▶│
  │                       │                                          │ policy.yaml classifies,
  │                       │                                          │ places, records (ledger)
  │                       │                ◀──── POST /result ──────┤ only what `link.release` permits
  │ GET /jobs/{id} ─────▶ │ result or "withheld on dgx1"             │
```

### 1. Outbound only

The runner opens every connection. Nothing listens on the machine for this
feature, so there is no port to expose, no tunnel, no firewall rule — the same
posture as a self-hosted CI runner. The runner refuses an endpoint that is not
`https://`, except loopback for development.

### 2. The runner has its own identity

- An org **owner or admin** creates an **enrollment code** in Studio: single use,
  15 minutes, bound to their organisation, optionally to a set of teams.
- `annona link enroll <code> --name dgx1 --endpoint https://…` trades it for a
  **runner secret** (256 bits), returned exactly once. The runner stores it in
  `$ANNONA_HOME/link.json`, mode `0600`. The backend stores only its SHA-256.
- Every runner request carries `Authorization: Runner <runner_id>.<secret>`,
  compared in constant time.
- **Revocation** in Studio clears the hash. The next request fails; there is no
  cache and no grace period.

The credential can do four things and nothing else: heartbeat, claim a job
addressed to this runner, report the result of a job it holds, and read which of
its jobs were cancelled. It cannot read notes, agents, users or other runners.

### 3. Who may send work where (RBAC)

| Action | Who |
|---|---|
| Create an enrollment code, revoke a runner, restrict it to teams | org `owner` / `admin` |
| See a runner | active org members; if the runner is restricted to teams, members of those teams and admins |
| Send a job to a runner | anyone who can see it, except `guest` |
| See a job's result | the person who sent it, and org admins |
| Cancel a job | the person who sent it, and org admins |

Checked in the backend on every call, from the organisation membership table —
not from anything the browser sends.

### 4. The runner's policy is final

Studio sends an **instruction**, never tools, paths to widen, or policy. The run
goes through the same perimeter as one typed at the machine: classification,
placement, the tool gate, the ledger. A job can be held; Studio cannot un-hold it.

### 5. What comes back is a policy decision

Studio is a destination outside the machine, so the result is egress and is
treated as such. The policy states the ceiling explicitly:

```yaml
link:
  release: internal     # the highest class whose answer may go back to Studio
```

After the run, the answer is released only if **all** hold:

- the run's working set is at or below `link.release`;
- the working set carries no seal;
- the answer text itself classifies at or below `link.release`.

Otherwise the job is reported **`withheld`**: Studio receives the placement, the
decisions and the ledger head, and the sentence "the result stays on dgx1". The
answer is read at the machine (`annona why`, the window).

**No `link` section, no release.** A policy that never mentions the link releases
metadata only. `annona link enroll` writes the section, prints what it means, and
the operator can lower it. The ceiling lives in the policy because the policy is
the document an auditor reads.

### 6. Both sides keep a record

- The backend stores who sent what, to which runner, when, and the outcome.
- The runner's ledger records `kind: link` entries — `received` (with
  `requested_by`: email, role, organisation), then `released` or `withheld` —
  in the same hash chain as every placement. The ledger now answers *who asked*,
  not only *what ran where*.

### 7. Leases, not retries

A claim takes a 30-minute lease. A result must present the lease id. A lease that
expires marks the job `expired`; it is **not** handed to another runner. Rerunning
restricted work somewhere the requester did not choose is a decision a person
makes, not a retry loop.

## Wire contract

Base: `{backend-ai}/api/v1/runner/link`. JSON over HTTPS.

**Person (Firebase bearer, current organisation):**

| | |
|---|---|
| `POST /enrollments` `{name?, allowed_team_ids?}` | → `{code, expires_at}` — admin only |
| `GET /runners` | → runners visible to the caller, with `online`, reported substrates and policy digest |
| `PATCH /runners/{id}` `{allowed_team_ids}` | admin only |
| `DELETE /runners/{id}` | revoke — admin only |
| `POST /jobs` `{runner_id, instruction, title?, agent_id?}` | → job, `status: queued` |
| `GET /jobs?runner_id=&limit=` | → jobs visible to the caller |
| `GET /jobs/{id}` | → one job |
| `POST /jobs/{id}/cancel` | → job |

**Runner (`Authorization: Runner <id>.<secret>`):**

| | |
|---|---|
| `POST /enroll` `{code, name, machine_id, version}` | no credential yet; → `{runner_id, secret, organization}` |
| `POST /heartbeat` `{version, policy_digest, substrates[], busy}` | → `{cancel: [job_id]}` |
| `POST /claim` | → `{job: null}` or `{job: {id, lease_id, instruction, title, requested_by: {email, role, organization_id}}}` |
| `POST /jobs/{id}/result` `{lease_id, status, response?, placement, decisions[], ledger_head, release, error?}` | `status` ∈ `completed`, `withheld`, `failed`, `cancelled`. `release` states why, never material |

A result is **idempotent**: the same runner, lease and status sent twice (a lost
response, a retry) is acknowledged unchanged. A different status on a closed job
is `409`, so `withheld` can never be turned into `completed`. The backend drops
any `response` that arrives with a status other than `completed`.

Job states: `queued → running → completed | withheld | failed | cancelled | expired`.

## Rejected

- **Inbound calls to the runner**, with or without a tunnel. A listening port on
  the machine that holds the material is the one thing this design exists to
  avoid.
- **Reusing the person's Firebase token on the runner.** Wrong lifetime, wrong
  principal, and revoking the machine would mean revoking the person.
- **WebSockets.** A held connection from Cloud Run is fragile and buys a few
  seconds of latency on work that takes tens of seconds. Polling every few
  seconds is boring and survives every proxy.
- **Sending the answer and letting Studio decide what to show.** Once it has left
  the machine, "not shown" is a UI choice, not a perimeter.

## Consequences

- A runner can run detached as a service (`annona link serve`, systemd on a
  DGX) with no browser anywhere.
- One job at a time per runner. A GPU box runs one model at a time anyway; a
  second concurrent slot is a later change to the claim loop, not to the contract.
- Latency to start is up to one poll interval (3 s).
- Studio sees the *shape* of a withheld run — where it was placed, what was held
  — which is itself metadata. It is the minimum that lets a person know the job
  finished, and it is stated here so nobody discovers it.
