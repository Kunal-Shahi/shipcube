"use client";

import { useTransition } from "react";
import { GcpLinker } from "@/components/gcp-linker";
import { finishOnboarding } from "./actions";

export function OnboardingForm({ deployer, canLink }: { deployer: string; canLink: boolean }) {
  const [pending, start] = useTransition();
  return (
    <div className={pending ? "pointer-events-none opacity-60" : undefined} aria-busy={pending}>
      <GcpLinker deployer={deployer} canLink={canLink} doneLabel="Continue to agent setup" onDone={() => start(() => finishOnboarding())} />
      <form action={finishOnboarding} className="mt-6 border-t border-border pt-4">
        <button type="submit" className="text-fg-muted underline-offset-2 hover:text-fg hover:underline">
          Skip for now. Apps use ShipCube&apos;s project until you link one in Settings.
        </button>
      </form>
    </div>
  );
}
