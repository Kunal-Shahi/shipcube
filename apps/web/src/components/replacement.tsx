import { ArrowRight } from "lucide-react";
import type { Monitoring } from "@/lib/types";
import { TIERS } from "@/lib/types";
import { money } from "@/lib/format";
import { Button, TierBadge } from "./ui";

export function ReplacementSuggestion({ r }: { r: NonNullable<Monitoring["replacement"]> }) {
  const saving = r.currentMonthly - r.proposedMonthly;
  return (
    <section className="rounded-lg border border-accent/40 bg-accent-soft px-4 py-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 max-w-[80ch]">
          <h2 className="flex flex-wrap items-center gap-2 font-semibold">
            Suggested move
            <TierBadge tier={r.fromTier} />
            <ArrowRight className="size-3.5 text-fg-muted" aria-hidden />
            <TierBadge tier={r.toTier} />
            <span className="font-normal text-fg-muted">{TIERS[r.toTier].name}</span>
          </h2>
          <p className="mt-1 text-fg-muted">{r.reason}</p>
          <p className="num mt-1.5">
            {money(r.currentMonthly)}/month now → {money(r.proposedMonthly)}/month after the move
            {saving > 0 && <span className="text-good"> · saves {money(saving)}</span>}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="primary" size="sm">Move to {r.toTier}</Button>
          <Button variant="ghost" size="sm">Dismiss</Button>
        </div>
      </div>
    </section>
  );
}
