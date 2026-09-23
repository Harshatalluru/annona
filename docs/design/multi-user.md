# More than one person — identity, a subject in every decision, rules per group

Status: U1–U3 built 2026-09-24 (`runner/services/identity.py`, `Policy.for_subject`,
`tests/test_identity.py`); U4–U7 open. Builds on the order already written in
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

Annona stores no users and no passwords, and is tied to no identity provider —
Akaion's included. A request carries a **subject** from one of three kinds of source,
and a policy can list several:

| Kind | How it is proven | Covers |
|---|---|---|
| `jwt` | a bearer token (`Authorization: Bearer …`) verified against the issuer's public keys (JWKS), `iss` and `aud` checked, expiry enforced; subject and groups read from named claims | **any OIDC provider** — Google Workspace, Entra ID, Okta, Keycloak, Auth0 — and the **Akaion platform**, whose tokens are Firebase ID tokens: standard JWTs signed by Google |
| `proxy` | an authenticating proxy (oauth2-proxy, Pomerium, Google IAP, Cloudflare Access) sets `X-Forwarded-Email`/`-Groups`; trusted **only** with the proxy's shared secret header or mTLS | a server or DGX on the LAN/VPC, zero login code in Annona |
| `os` | the CLI over a unix socket, `SO_PEERCRED` gives the uid | one machine, several accounts |

The `jwt` kind is one verifier for every provider: PyJWT and `cryptography` are already
dependencies, and `PyJWKClient` caches the keys. The Akaion platform is a **preset** of it,
not a special case in the code:

```yaml
identity:
  required: true
  providers:
    - kind: jwt                      # the company's own IdP
      issuer: https://login.microsoftonline.com/<tenant>/v2.0
      audience: api://annona
      jwks_url: https://login.microsoftonline.com/<tenant>/discovery/v2.0/keys
      subject_claim: email
      groups_claim: groups
    - kind: jwt                      # optional: people who sign in with Akaion
      preset: akaion                 # = issuer https://securetoken.google.com/<project>,
      project: akaion-prod-eu        #   audience <project>, Google's securetoken JWKS
    - kind: proxy
      email_header: X-Forwarded-Email
      groups_header: X-Forwarded-Groups
      secret_env: ANNONA_PROXY_SECRET  # the proxy sends it as X-Annona-Proxy-Secret
```

Only asymmetric tokens (RS256, ES256) are accepted, `exp`/`iss`/`aud` are required, and
with `subject_claim: email` a token saying `email_verified: false` is refused. A present
but wrong credential is refused even when identity is optional — never downgraded to
anonymous.

The first provider that accepts the credential wins; a credential every provider rejects
is refused and recorded. Nothing is sent to Akaion to verify an Akaion token — the keys
are public and cached, so verification works offline between key rotations. A company
that never uses Akaion lists no Akaion preset and nothing about Akaion is ever contacted.

A request with no credential is **anonymous**: allowed only when `identity.required` is
false (today's single-user laptop). On a server it is true and anonymous requests are
refused.

## What the subject changes

```yaml
identity:
  required: true
  providers: [...]                    # as above
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
| U1 | `Subject` on every request from `jwt` (any OIDC issuer, Akaion preset) and `proxy` providers; `subject`/`groups` in the ledger and in the hash chain; `annona audit --subject` | 2 days |
| U2 | `groups:` in the policy; `match.group` in rules; `${subject}` in allow-lists | 2 days |
| U3 | memory folders with `groups`, filtered before ranking | 1 day |
| U4 | per-subject concurrency limits | 1 day |
| U5 | Sign-in flow in the UI (authorization code + PKCE) for sites without a proxy; the token it obtains is verified by U1's `jwt` provider | 3 days |
| U6 | UI: who am I, admin ledger filter by subject and group | 2 days |
| U7 | the `dgx-condivisa` example and its tests | 1 day |
