import Link from "next/link";
import { signInWithGoogle } from "./actions";
import { GoogleButton } from "./google-button";

export function AuthCard({ intent, returnTo }: { intent: "sign-in" | "sign-up"; returnTo?: string }) {
  const signUp = intent === "sign-up";
  return (
    <div className="w-full max-w-[360px]">
      <h1 className="text-[20px] font-semibold leading-7 tracking-[-0.01em]">
        {signUp ? "Create your ShipCube account" : "Sign in to ShipCube"}
      </h1>
      <p className="mt-1.5 text-fg-muted">
        {signUp
          ? "ShipCube deploys the apps you build in Claude Code, Cursor or Muse Code, and keeps them private to the people you choose."
          : "Use the Google account your apps are shared with."}
      </p>

      <form action={signInWithGoogle} className="mt-6">
        <input type="hidden" name="intent" value={intent} />
        {returnTo && <input type="hidden" name="returnTo" value={returnTo} />}
        <GoogleButton label={signUp ? "Sign up with Google" : "Continue with Google"} />
      </form>

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
