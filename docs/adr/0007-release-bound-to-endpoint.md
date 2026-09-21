# 0007 — Bind a higher release ceiling to the Studio it is for

- **Status:** Accepted
- **Date:** 2026-09-21
- **Deciders:** Akaion AI Lab

## Context

ADR 0006 puts one number in the policy: `link.release`, the highest class whose
answer may go back to Studio. It says nothing about *which* Studio.

That was enough while there was one. It is not once a company runs Agents Studio
inside its own network: the Studio on `studio.intranet.example.com` sits behind
the same firewall as the material, and the people using it are the ones the NDA
already covers. They want `restricted` answers back. `studio.akaion.com` should
still get `internal` at most.

With one number the operator has two bad choices. Keep `internal`, and the Studio
in the building cannot do the work it was installed for. Raise it to
`restricted`, and the policy now promises that about whatever endpoint the
machine is enrolled to — today the one in the building, tomorrow the public one,
after the laptop goes home and someone runs `annona link enroll` with a new code.
Nothing in the policy would notice. The ceiling would be a promise about a
destination it never names.

## Decision

A ceiling above the default names the endpoint it is for.

```yaml
link:
  release: internal            # ceiling for any endpoint
  endpoints:                   # optional: a ceiling for named control planes
    - url: https://studio.intranet.example.com
      release: restricted
```

The effective ceiling is the `endpoints` entry whose `url` matches the endpoint
in `link.json` — the one this machine enrolled to — and `link.release` for every
other endpoint. The runner reads both at decision time, so **the machine decides,
not Studio**: a Studio cannot claim to be the one in the building, and a policy
copied to another machine, or a machine re-enrolled elsewhere, falls back to
`link.release` without anyone editing anything.

### 1. Matching is exact

Two URLs are the same endpoint when scheme, host, port and path are equal, with
the host compared case-insensitively and a trailing slash ignored. Nothing else
is equated: not `:443` with no port, not `www.` with none, not one path with
another. A near miss falls back to `link.release` — the lower ceiling, and so the
safe direction to be wrong in. The operator finds out from `annona link status`,
which prints the ceiling in force for the enrolled endpoint and the rule that set
it.

### 2. No wildcards

`*.intranet.example.com` reads like a description of the company network. It is
a promise about every machine that will ever answer under that name, including
the ones nobody has set up yet — a test instance, a contractor's preview, a
forgotten subdomain pointed at a SaaS. The policy is the document an auditor
reads; each entry in it should be a Studio someone has looked at.

### 3. HTTPS only

An `endpoints` URL must be `https://` (plain `http` on loopback, for
development) — the same rule as enrollment, applied when the policy loads. A
ceiling bound to a plain-HTTP endpoint is bound to whoever answers on the network
path. An entry listed twice after normalising is an error, not first-match: two
ceilings for one Studio mean the author believes something false about one of
them.

### 4. An entry may raise or lower

Any class is accepted, including one below `link.release`. Raising is the reason
this exists; lowering — `public` only, to a staging Studio — is never less safe
than the default and forbidding it would be a rule with nothing to protect. An
`endpoints` list with no `release` is valid too: answers go to the Studios named,
and every other one gets metadata only.

### 5. What does not change

- **Sealed material never leaves**, whatever the endpoint. A seal is not a class
  and no ceiling reaches it.
- The three checks of ADR 0006 — working set, seal, the answer's own text — are
  the same checks, against the ceiling for this endpoint.
- The `release` reason sent to Studio and kept in the ledger names the rule
  that applied: `within link.release for https://studio.intranet.example.com
  (restricted)`, or `within link.release (internal)`.

## Non-goals

- **Proving the endpoint is on-prem.** The runner does not look at IP ranges,
  routes or VPN state. DNS and the TLS certificate are the trust anchor: the
  name in the policy is the name the certificate must prove. An operator running
  Studio internally should issue its certificate from their own CA and trust
  that CA on the machine, so a public certificate for a look-alike name cannot
  stand in for it.
- **Studio telling the runner what it is.** Anything Studio says about itself is
  a claim from the destination the ceiling is meant to judge.

## Rejected

- **One `link.release`, raised by hand when needed.** The status quo: a blind
  promise about the next enrollment, as above.
- **Wildcards, CIDRs, "private network" detection.** A promise about machines
  nobody has seen, or a check that DNS rebinding and a split-horizon resolver
  both defeat.
- **The ceiling in `link.json`, written at enrollment.** `link.json` is the
  credential, rewritten by every `enroll`; the ceiling is policy, and belongs in
  the file an auditor reads and a digest covers.

## Consequences

- One policy can be deployed to every machine in a company: the ones enrolled to
  the Studio in the building send `restricted` back, the rest send `internal`.
- Moving a Studio to a new hostname silently lowers the ceiling until the policy
  names it. That is the intended failure: fewer answers released, not more.
- The heartbeat still names no endpoints, and the policy digest changes when the
  list does, so Studio can see that the policy changed without seeing the list.
