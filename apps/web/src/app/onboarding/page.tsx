import type { Metadata } from "next";
import { Check } from "lucide-react";
import { Wordmark } from "@/components/logo";
import { deployerAccount, supports } from "@/lib/api";
import { requireSession } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { OnboardingForm } from "./onboarding-form";

export const metadata: Metadata = { title: "Set up" };

const STEPS = ["Account", "Where apps run", "Connect your agent"];

export default async function OnboardingPage() {
  const session = await requireSession();
  const [deployer, canLink] = await Promise.all([deployerAccount(), supports("gcp_link")]);
  return (
    <div className="flex min-h-dvh flex-col px-4">
      <header className="mx-auto flex h-14 w-full max-w-[1120px] items-center justify-between">
        <Wordmark />
        <span className="text-fg-muted">{session.user.email}</span>
      </header>
      <main className="mx-auto w-full max-w-[640px] pt-10 pb-16">
        <ol className="mb-8 flex flex-wrap items-center gap-x-6 gap-y-2 text-[12px]">
          {STEPS.map((s, i) => (
            <li key={s} className={cn("flex items-center gap-2", i === 1 ? "font-medium text-fg" : "text-fg-muted")}>
              <span
                className={cn(
                  "num grid size-5 place-items-center rounded-full border text-[11px]",
                  i === 0 && "border-good bg-good-soft text-good",
                  i === 1 && "border-accent text-accent",
                  i === 2 && "border-border-strong",
                )}
              >
                {i === 0 ? <Check className="size-3" aria-label="Done" /> : i + 1}
              </span>
              {s}
            </li>
          ))}
        </ol>
        <h1 className="text-[20px] font-semibold leading-7 tracking-[-0.01em]">Where should your apps run?</h1>
        <p className="mt-1.5 max-w-[62ch] text-fg-muted">
          ShipCube deploys to Google Cloud. Use ShipCube&apos;s project to start right away, or link your own so apps bill to
          your Google Cloud account. This step is optional and you can change it later.
        </p>
        <div className="mt-6">
          <OnboardingForm deployer={deployer} canLink={canLink} />
        </div>
      </main>
    </div>
  );
}
