import type { Identity } from "../../api/kernel"
import type { AuthStatus } from "../../api/runner"

/**
 * Who you are here, in the sidebar — the one place people look for it.
 *
 * It shows what the perimeter verified (`/api/kernel/identity`), not what the
 * browser's own sign-in believes: "signed in" on screen has to mean "your name
 * is on your decisions in the ledger", or the badge is decoration. Sync is a
 * consequence of the same sign-in, so it is a line here, not a second account.
 */
interface Props {
  identity: Identity | null
  sync: AuthStatus | null
  busy: boolean
  onSignIn: () => void
  onSignOut: () => void
}

const muted = { fontSize: 11, color: "rgba(255,255,255,0.45)", lineHeight: 1.4, marginTop: -2 }
const quiet = { borderColor: "rgba(255,255,255,0.08)", background: "rgba(255,255,255,0.04)" }

export default function AccountBlock({ identity, sync, busy, onSignIn, onSignOut }: Props) {
  const you = identity?.you
  const synced = !!sync?.authenticated
  const canSignIn = !identity || identity.providers.length === 0 || identity.providers.some((p) => p.signin)

  if (you) {
    return (
      <div className="ak-cloud-badge" role="region" aria-label="Signed in">
        <div className="ak-cloud-badge__row">
          <span className="ak-cloud-badge__dot ak-cloud-badge__dot--online" />
          <span style={{ fontSize: 12, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {you.id}
          </span>
        </div>
        <div style={muted}>
          ✓ Verified by {you.verified_by}
          {you.groups.length > 0 && <> · {you.groups.join(", ")}</>}
        </div>
        <div style={muted}>Your decisions carry your name in the ledger.{synced && " Notes sync to the cloud."}</div>
        <button className="ak-cloud-badge__cta" onClick={onSignOut} style={quiet}>Sign out</button>
      </div>
    )
  }

  if (identity?.problem) {
    return (
      <div className="ak-cloud-badge" role="region" aria-label="Sign-in not accepted">
        <div className="ak-cloud-badge__row">
          <span className="ak-cloud-badge__dot" style={{ background: "var(--amber, #e0a33a)" }} />
          <span style={{ fontSize: 12, fontWeight: 500 }}>Sign-in not accepted</span>
        </div>
        <div style={muted} title={identity.problem}>
          This perimeter does not recognise your account. Sign in with one it trusts.
        </div>
        <button className="ak-cloud-badge__cta" onClick={onSignOut} style={quiet}>Sign out</button>
      </div>
    )
  }

  return (
    <div className="ak-cloud-badge" role="region" aria-label="Anonymous">
      <div className="ak-cloud-badge__row">
        <span className="ak-cloud-badge__dot" />
        <span style={{ fontSize: 12, fontWeight: 500 }}>Anonymous</span>
      </div>
      <div style={muted}>
        {synced
          ? `Notes sync as ${sync?.email ?? "you"}, but this window's requests are unsigned — open Annona in your browser to sign them.`
          : "Decisions are recorded without a name. Notes stay on this machine."}
      </div>
      {canSignIn && (
        <button className="ak-cloud-badge__cta" onClick={onSignIn} disabled={busy}>
          {busy ? "Connecting…" : "Sign in →"}
        </button>
      )}
    </div>
  )
}
