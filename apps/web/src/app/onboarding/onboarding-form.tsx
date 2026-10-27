"use client";

import { useTransition } from "react";
import { GcpLinker } from "@/components/gcp-linker";
import { finishOnboarding } from "./actions";

export function OnboardingForm() {
  const [pending, start] = useTransition();
  return (
    <div className={pending ? "pointer-events-none opacity-60" : undefined}>
      <GcpLinker doneLabel="Continue to agent setup" onDone={() => start(() => finishOnboarding())} />
      <form action={finishOnboarding} className="mt-6 border-t border-border pt-4">
        <button type="submit" className="text-fg-muted hover:text-fg">
          Skip for now. Apps use ShipCube&apos;s project until you link one in Settings.
        </button>
      </form>
    </div>
  );
}
