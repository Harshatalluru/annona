/**
 * Welcome / first-launch view.
 *
 * Local-first philosophy: NO login required to start using the app.
 * Primary CTA = "Open my vault" (skips cloud sync).
 * Secondary CTA = optional cloud sign-in, which is exactly that: optional.
 *
 * Once dismissed (either path), `localStorage.akaion_onboarding_done = "true"`,
 * so the next launch skips this view altogether.
 */
import { useState } from "react";
import { signIn, isSigninHandoff, signInWithGoogle } from "../../lib/signin";
import { AuthStatus } from "../../api/runner";
import type { Identity } from "../../api/kernel";
import AuthLogo from "../auth/AuthLogo";
import SocialButton from "../auth/SocialButton";

interface Props {
  /** What the perimeter says about identity: required, by whom, and who we are. */
  identity?: Identity | null;
  /** Path to the local vault (footer info, e.g. ~/akaion-brain) */
  vaultPath?: string;
  /** Called when the user picks local-only ("Open my vault") */
  onSkip: () => void;
  /** Called after a successful Google login */
  onLogin: (status: AuthStatus) => void;
}

export const ONBOARDING_FLAG = "akaion_onboarding_done";

export default function WelcomeView({ identity, vaultPath = "~/akaion-brain", onSkip, onLogin }: Props) {
  // Managed mode, like a work profile: the organisation's policy says who may
  // use this perimeter, so there is no "continue without an account".
  const required = !!identity?.required && !identity.you;
  const provider = identity?.providers.find((p) => p.signin) ?? null;
  const signInLabel = required
    ? `Sign in with ${provider?.label ?? "your account"}`
    : "Sign in (optional)";
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState<string | null>(null);
  const [handoffDone, setHandoffDone] = useState(false);

  const handleSkip = () => {
    try { localStorage.setItem(ONBOARDING_FLAG, "true"); } catch { /* private mode */ }
    onSkip();
  };

  const [waiting, setWaiting] = useState(false);

  const finish = (status: AuthStatus) => {
    try { localStorage.setItem(ONBOARDING_FLAG, "true"); } catch { /* */ }
    onLogin(status);
  };

  const handleGoogle = async () => {
    setLoading(true);
    setError(null);
    setWaiting(false);
    try {
      finish(await signIn(() => setWaiting(true)));
    } catch (e: any) {
      // Silently swallow user-driven cancellations
      if (e?.code !== "auth/popup-closed-by-user" && e?.code !== "auth/cancelled-popup-request") {
        setError(e?.message ?? "Google sign-in failed");
      }
    } finally {
      setLoading(false);
      setWaiting(false);
    }
  };

  // This tab was opened by the desktop app to perform the exchange. It must
  // NOT start on its own: `signInWithPopup` calls `window.open`, and a browser
  // blocks that when it is not the direct result of a click — which would end
  // in the same `auth/popup-blocked` the desktop window failed with, one layer
  // further out. So the click stays, and the page is about nothing else.
  const handleHandoff = async () => {
    setLoading(true);
    setError(null);
    try {
      await signInWithGoogle();
      setHandoffDone(true);
    } catch (e: any) {
      if (e?.code !== "auth/popup-closed-by-user" && e?.code !== "auth/cancelled-popup-request") {
        setError(e?.message ?? "Google sign-in failed");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="ak-welcome-shell">
      <div className="ak-welcome-orb ak-welcome-orb--violet" />
      <div className="ak-welcome-orb ak-welcome-orb--blue" />

      <div className="ak-welcome-card animate-fade-in-up">
        <AuthLogo
          title="Annona"
          subtitle="Where it runs is a decision"
        />

        {required && (
          <div className="ak-auth-note">
            <strong>This perimeter is managed by your organisation.</strong><br />
            Every decision it takes is recorded under the name of whoever asked,
            so it needs to know who you are. Your sign-in stays in this browser;
            the ledger keeps only your email and groups — never the token.
            {!provider && <><br />Sign in through your organisation&apos;s portal, then reload this page.</>}
          </div>
        )}

        {!isSigninHandoff() && !required && (
        <button
          className="ak-btn-primary"
          onClick={handleSkip}
          disabled={loading}
          aria-label="Open my vault — local mode"
        >
          <span aria-hidden="true">✦</span>
          Open my vault
        </button>
        )}

        {!isSigninHandoff() && !required && <div className="ak-or-divider">or</div>}

        {isSigninHandoff() && !handoffDone && (
          <div className="ak-auth-note">
            Annona opened this tab to sign you in — its own window cannot show a
            Google popup. Finish here and go back to the app.
          </div>
        )}

        {!handoffDone && (!required || provider) && (
          <SocialButton
            provider="google"
            variant="full"
            isLoading={loading}
            onClick={isSigninHandoff() ? handleHandoff : handleGoogle}
            label={isSigninHandoff() ? "Continue with Google" : signInLabel}
          />
        )}

        {waiting && (
          <div className="ak-auth-note">
            Finish signing in in your browser — this window will pick it up.
          </div>
        )}

        {handoffDone && (
          <div className="ak-auth-note">
            Signed in. You can close this tab and go back to Annona.
          </div>
        )}

        {error && <div className="ak-auth-error">{error}</div>}

        {identity?.problem && (
          <div className="ak-auth-error">This perimeter did not accept your sign-in: {identity.problem}</div>
        )}

        <p className="ak-welcome-footer">
          Local vault: <code>{vaultPath}</code><br />
          {required
            ? "Your work runs where the policy says. Signing in names you; it does not send your files anywhere."
            : "Signing in puts your name on the decisions you cause and syncs your notes. Without it, everything still works — anonymously."}
        </p>
      </div>
    </div>
  );
}
