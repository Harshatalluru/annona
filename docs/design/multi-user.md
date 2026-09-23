# More than one person — identity, a subject in every decision, rules per group

Status: proposal, 2026-09-24. Builds on the order already written in
[`shared-context.md`](shared-context.md) and makes it concrete.

## Why now

A DGX Spark or an office server is shared by design: five people in a studio, a sales
team, a lab. Today Annona cannot tell them apart:

- no identity anywhere — `LedgerEntry` (`audit/ledger.py:72`) records *what* was decided,
  never *who* asked;
- the API binds to loopback (`local_api.py:359`); `PairedOriginMiddleware`
  (`pairing.py:203`) authorises an **app** by origin and token, not a person;
- the policy has no subject: every rule, allow-list and memory applies to everyone.

The documented workaround — one instance per person — does not scale past a handful and
cannot share a memory safely. This closes that gap without an Annona user database: the
company already has one.

## Where identity comes from

Annona does not store users or passwords. It accepts a **subject** from the identity the
company already runs, in this order of preference:

| Source | How | Fits |
|---|---|---|
| **Authenticating proxy** | oauth2-proxy, Pomerium, **Google IAP**, Cloudflare Access, Entra App Proxy in front of Annona; Annona trusts `X-Forwarded-Email` / `X-Forwarded-Groups` (or the IAP signed JWT) **only** from the proxy, proven by a shared secret header or mTLS | a server or DGX on the LAN/VPC; zero code in Annona for login |
| **OIDC in the daemon** | Google Workspace, Entra ID, Okta, Keycloak; authorization-code flow in the UI, ID token verified by JWKS; groups from the token claim | no proxy available |
| **Local OS user** | the CLI over a unix socket, `SO_PEERCRED` gives the uid | one machine, several accounts |

A request with no subject is **anonymous**: allowed only when the policy says
`identity: optional` (today's single-user behaviour, the default for a laptop). On a
server the policy sets `identity: required` and anonymous requests are refused.

## What the subject changes

```yaml
identity:
  required: true
  source: proxy                       # proxy | oidc | os
  proxy:
    email_header: X-Forwarded-Email
    groups_header: X-Forwarded-Groups
    secret_env: ANNONA_PROXY_SECRET   # the proxy sends it; anything else is refused
groups:
  sales:  [anna@acme.it, marco@acme.it]   # or from the IdP's groups claim
  legal:  ["*@legal.acme.it"]
  admins: [ceo@acme.it]

rules:
  - id: R-memoria-sales
    match: {class: restricted, group: sales}   # one more dimension on today's match
    allow: [local-gpu]
    on_unavailable: hold

tools:
  allow:
    document_reader: [~/Azienda/Condivisa/**, "~/Azienda/Personale/${subject}/**"]

memory:
  folders:
    - path: ~/Azienda/Memoria-Storica/**
      groups: [sales, admins]                  # who may retrieve from it
```

1. **Every ledger entry carries `subject`** (and `groups`), inside the hash chain, so a
   rewritten author breaks verification. `annona audit --subject anna@acme.it`.
2. **Rules match on `group`** — one more dimension, not a new subsystem.
3. **Allow-lists expand `${subject}`** — a personal folder per person without a rule each.
4. **Memory folders carry `groups`** — the index keeps the folder on every passage and the
   search filters by the caller's groups *before* ranking, so a passage a person may not
   see is never retrieved, scored or counted.
5. **Seals stay global** — a seal is about the matter, not the person.
6. **Per-subject limits** — `max_concurrency` per subject (see observability.md), so one
   person's batch job does not starve the office's interactive use.
7. **The UI shows who you are**, and an admin view filters the ledger by person and group.

Pairing stays what it is — authorisation of an app — and composes with identity: a
paired Studio acts *on behalf of* a subject it must name.

## Acceptance

- Two subjects on one daemon: Anna (sales) retrieves the Veloce minutes; Luca (legal) asks
  the same question and gets no passage from Memoria-Storica, and the ledger shows both
  subjects.
- A request forged with `X-Forwarded-Email` but without the proxy secret is refused and
  recorded.
- `annona verify` fails after a subject is edited in the ledger.
- Anonymous requests are refused when `identity.required: true`.

## Plan

| Step | Scope | Estimate |
|---|---|---|
| U1 | `Subject` on every request (proxy headers + secret); `subject`/`groups` in the ledger and in the hash chain; `annona audit --subject` | 2 days |
| U2 | `groups:` in the policy; `match.group` in rules; `${subject}` in allow-lists | 2 days |
| U3 | memory folders with `groups`, filtered before ranking | 1 day |
| U4 | per-subject concurrency limits | 1 day |
| U5 | OIDC in the daemon (Workspace, Entra, Okta, Keycloak) for sites without a proxy | 3 days |
| U6 | UI: who am I, admin ledger filter by subject and group | 2 days |
| U7 | the `dgx-condivisa` example and its tests | 1 day |
