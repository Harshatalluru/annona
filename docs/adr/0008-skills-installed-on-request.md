# 0008 — Skills installed on request: the policy pre-approves, Studio only says when

- **Status:** Accepted
- **Date:** 2026-09-21
- **Deciders:** Akaion AI Lab

## Context

ADR 0006 lets a job name a skill, and the runner loads it only if the policy
enables it and it is installed. Installing is still something a person does at
the machine: `annona skills-install`, then an edit to `skills:`.

That is right for a skill someone found on the internet. It is friction for the
ones a team has already agreed on. The quality engineer who wants an 8D draft
from Studio has to find whoever administers the DGX, who copies a folder, edits
the policy, and goes back to what they were doing. Multiply by every machine and
every new skill, and people stop asking.

The obvious fix is wrong: let Studio push a skill to the machine. A skill is an
instruction the agent will follow with the machine's material. Whoever can put
one there decides what the agent does — so a Studio that can install skills is a
Studio that can widen what a machine does, which ADR 0006 exists to prevent.

## Decision

The policy names **catalogs**, and in each catalog the skills it **pre-approves**.
Studio may ask for a pre-approved skill to be fetched; it cannot ask for anything
else.

```yaml
skill_catalogs:
  - name: akaion
    url: https://akaion-ai.github.io/annona/catalog/index.json
    enable: [rfq-triage, eight-d]   # pre-approved: installable on request, enabled once installed
    # trust: false                  # default: installed skills are pinned local
```

The decision about *what* stays in the file an auditor reads. Studio decides
*when*.

### 1. `enable` is a decision about each skill, and it is also enablement

A name in `enable` means two things at once: the skill may be installed when
Studio asks, and once installed it is enabled exactly as if it were under
`skills:`. The effective set is `skills:` ∪ every catalog's `enable`, read
through one property (`Policy.enabled_skills`) by every consumer — the kernel's
registry, the heartbeat, `annona skills`. Splitting "may install" from "may use"
would give the operator two lists that must agree and no reason to want them
not to.

Enabled is still not usable: the `skill` tool must be in `tools.allow`, and the
skill's own tools must be allowed. Nothing about that rule changes.

### 2. No wildcards

`enable: ["*"]` pre-approves whatever the catalog publishes next — a promise
about instructions nobody here has read. Same argument as ADR 0007's endpoints:
each entry in the policy should be something someone has looked at. Names must
match `^[a-z0-9][a-z0-9._-]{0,63}$`, which also keeps a name from the network
from being a path. A skill enabled by two catalogs is an error, not first-match:
which publisher it comes from must not depend on file order.

### 3. The archive is verified before it is opened

A catalog is a JSON index:

```json
{"version": 1, "skills": [
  {"name": "rfq-triage", "version": 1, "description": "…", "pins": "local",
   "sha256": "36c3…", "archive": "rfq-triage-36c3beb35aee.tar.gz"}
]}
```

`archive` is relative to the index. The machine fetches the index (HTTPS, the
same rule as link endpoints), downloads the archive, and compares its SHA-256
with the index **before unpacking a byte**. Unpacking then refuses the whole
archive on any absolute name, any `..`, any symlink, hardlink or device, or more
than a size and count cap; files are written by the runner, never by `tarfile`,
so no mode or owner from the archive is applied. The skill inside must carry
the name that was approved.

### 4. It installs like any other foreign skill

Through the existing install path: validated before it lands, `imported_from`
(the archive URL), `catalog` and `sha256` written into the front matter, the
body byte for byte. **Pinned local unless the catalog entry says `trust: true`**
— a publisher you have not reviewed writes instructions that run inside your
walls. `trust` is per catalog, not per skill, because what it states is trust in
a publisher; a skill that declares `pins: local` stays pinned either way.

### 5. Studio adds, never replaces

An install job for a skill already present — shipped, hand-written or installed
earlier — fails. A newer version, or replacing the operator's own house version
of a skill, is `annona skills-install <name> --from <catalog> --force` at the
machine. At the machine the operator may also install any entry of a catalog the
policy names; it stays disabled until the policy enables it, and the CLI says so.

### 6. No model runs, and the ledger says who asked

An install job reads no material and runs no model, so the release decision of
ADR 0006 does not apply: the response is built from the catalog entry alone. The
ledger records `kind: skill_install`, `installed` or `refused`, with the catalog,
the version, the SHA-256 and `requested_by`.

## Wire contract

Additions to ADR 0006; everything else is unchanged.

**Heartbeat** adds pre-approved skills a catalog publishes and this machine does
not have (at most 200; a catalog that cannot be read is skipped, the heartbeat
is not):

```json
"installable": [{"name": "eight-d", "version": 1, "description": "…≤300 chars",
                 "pins": "local", "catalog": "akaion"}]
```

`pins` is what the skill will be once installed: `local` unless the catalog is
trusted.

**Claim** may return a job with `"kind": "install_skill"` and `"skill": "<name>"`.
A job without `kind`, or with `"kind": "run"`, is a normal run; any other kind
fails.

**Result** of an install job:

```json
{"lease_id": "…", "status": "completed", "skill": "eight-d",
 "response": "Installed eight-d 1 from akaion (sha256 bbbfff78e08c…); pinned local; enabled by skill_catalogs.enable"}
{"lease_id": "…", "status": "failed",
 "error": "skill 'eight-d' is not pre-approved by this machine's policy"}
```

## Non-goals

- **Signatures and publisher keys.** In v1 the trust anchor is the index, served
  over HTTPS from a URL the policy names, carrying the archive's digest — the
  same stance ADR 0007 takes on TLS for endpoints. A signed index is the next
  step if catalogs are ever mirrored or served by someone other than the
  publisher.
- **Auto-update.** A new version of an installed skill is an operator's
  `--force`, never a heartbeat side effect: an instruction that changes under a
  running deployment is a change nobody reviewed.

## Rejected

- **Studio sends the skill.** The destination the perimeter protects would be
  writing the instructions the perimeter follows.
- **Studio may install anything from a named catalog; the policy only enables.**
  Installed is not enabled, but it is still a publisher's file landing on the
  machine because someone outside asked — the whole catalog becomes writable
  from Studio. And an installed-but-disabled skill is useless until someone adds
  it to `skills:`, which trains people to enable whatever Studio put there.
- **`enable: ["*"]`.** See §2.

## Consequences

- A team can roll a skill out to every machine by publishing it and adding one
  name to the policy they already deploy; the first job that needs it installs
  it.
- Anthropic's format is kept: a catalog archive is an ordinary skill folder, and
  one published here installs by hand anywhere.
- This project's own catalog is built from `catalog/skills/` by
  `scripts/build_catalog.py` into `docs/catalog/`, reproducibly, and published
  with the docs site. A test rebuilds it and compares, so the index and the
  sources cannot drift.
