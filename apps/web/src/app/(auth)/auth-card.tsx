import Link from "next/link";
import { webAuthConfig } from "@/lib/auth";
import { GoogleSignIn } from "./google-sign-in";

export function AuthCard({ intent, returnTo }: { intent: "sign-in" | "sign-up"; returnTo?: string }) {
  const signUp = intent === "sign-up";
  return (
    <div className="w-full max-w-[360px]">
      <h1 className="text-[20px] font-semibold leading-7 tracking-[-0.01em]">
        {signUp ? "Create your ShipCube account" : "Sign in to ShipCube"}
      </h1>
      <p className="mt-1.5 text-fg-muted">
        {signUp
          ? "ShipCube deploys the apps you build in Claude Code, Cursor or Muse Code, and keeps them private to the people you choose. ShipCube is in early access: request access first, and once you're approved, sign up with the same Google account."
          : "Use the Google account your apps are shared with."}
      </p>

      <div className="mt-6">
        <GoogleSignIn
          label={signUp ? "Sign up with Google" : "Continue with Google"}
          intent={intent}
          returnTo={returnTo}
          webConfig={webAuthConfig()}
        />
      </div>

      <p className="mt-4 text-[12px] text-fg-muted">
        Your Google identity decides who can open your apps. ShipCube reads your name and email address only;
        linking a GCP project is a separate, optional step. See the{" "}
        <Link href="/terms" className="underline underline-offset-2 hover:text-fg">
          Terms
        </Link>{" "}
        and{" "}
        <Link href="/privacy" className="underline underline-offset-2 hover:text-fg">
          Privacy notice
        </Link>
        .
      </p>

      <div className="mt-8 border-t border-border pt-4 text-fg-muted">
        {signUp ? (
          <>
            Already have an account?{" "}
            <Link href="/sign-in" className="text-accent underline underline-offset-2">
              Sign in
            </Link>
          </>
        ) : (
          <>
            New to ShipCube?{" "}
            <Link href="/#request-access" className="text-accent underline underline-offset-2">
              Request access
            </Link>
          </>
        )}
      </div>
    </div>
  );
}
