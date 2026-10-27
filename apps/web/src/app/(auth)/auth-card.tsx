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
          ? "ShipCube deploys the apps you build in Claude Code, Cursor or Muse Code, and keeps them private to the people you choose. Sign-up is by invite for now."
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
        linking a GCP project is a separate, optional step.
      </p>

      <div className="mt-8 border-t border-border pt-4 text-fg-muted">
        {signUp ? (
          <>
            Already have an account?{" "}
            <Link href="/sign-in" className="text-accent hover:underline">
              Sign in
            </Link>
          </>
        ) : (
          <>
            New to ShipCube?{" "}
            <Link href="/sign-up" className="text-accent hover:underline">
              Create an account
            </Link>
          </>
        )}
      </div>
    </div>
  );
}
