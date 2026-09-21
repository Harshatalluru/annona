# Link a machine to Agents Studio

Run work from Agents Studio on a machine you own — a laptop, a rack server, a
DGX — and close the browser while it runs. The machine dials out; nothing on it
listens. The design, and what it refuses to do, is [ADR 0006](../adr/0006-runner-link.md).

## 1. Get a code (org owner or admin)

Studio → **Machines** → **Add machine**. The code is single use and expires in
15 minutes.

## 2. Enroll

```bash
annona link enroll ann_enr_… --name dgx1
```

This writes the machine's own credential to `~/.annona/link.json` (mode `0600`)
and, if the policy has none, adds:

```yaml
link:
  release: internal   # the highest class whose ANSWER may go back to Studio
```

A run that reads anything above it — or sealed material, or whose answer itself
classifies above it — is reported **withheld**: Studio sees that it finished and
where it ran; the answer stays here.

## 3. Serve, detached

Foreground: `annona link serve`. On a server, as a service:

```ini
# /etc/systemd/system/annona-link.service
[Unit]
Description=Annona link to Agents Studio
After=network-online.target ollama.service
Wants=network-online.target

[Service]
User=annona
Environment=ANNONA_HOME=/srv/annona
ExecStart=/usr/local/bin/annona link serve
Restart=on-failure
# Exit code 3 means the credential was revoked in Studio: do not loop on it.
RestartPreventExitStatus=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now annona-link
journalctl -u annona-link -f
```

Only outbound HTTPS to the Studio endpoint is needed. No inbound port, no tunnel.

## 4. Stop

- **From Studio:** revoke the machine. The next request fails and `serve` exits 3.
- **From the machine:** `annona link forget` deletes the credential — revoke it in
  Studio too, so the old secret is dead on both sides.

Every job is in the local ledger: `received` with who asked, then `released` or
`withheld`. `annona audit` and `annona verify` read it like any other decision.
