import { cn } from "@/lib/cn";

// "How it fits together", drawn as a blueprint: the agent on the left, ShipCube's four parts in the
// middle, Google Cloud on the right, and the person who approves underneath. Connectors are labelled
// with what travels along them. On narrow screens it stacks top to bottom.

const PARTS = [
  { name: "Load estimator", does: "Profiles the code, picks where it runs, prices it" },
  { name: "Approval gate", does: "Holds first deploys, cap raises, sharing and deletes" },
  { name: "AI gateway", does: "Caps each app's tokens and spend" },
  { name: "Data layer", does: "Named measures over your warehouse, not raw SQL" },
];

export function FlowDiagram() {
  return (
    <figure className="tone-blue blueprint relative overflow-hidden rounded-2xl border border-border p-5 sm:p-8 lg:p-10">
      <figcaption className="sr-only">
        Your agent calls ShipCube&apos;s MCP tools. ShipCube estimates, gates, caps and queries, then builds and runs the
        work on Google Cloud. Risky steps send you an approval link; you decide in the console.
      </figcaption>
      <div className="grid items-stretch gap-0 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,auto)_minmax(0,1.5fr)_minmax(0,auto)_minmax(0,0.9fr)]">
        <Node label="Your agent" note="Any MCP client">
          <ul className="flex flex-col gap-1 text-[15px]">
            {["Muse", "Claude Code", "Cursor"].map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </Node>

        <Wire label="MCP tool calls" back="results, operations" />

        <Node label="ShipCube" note="Your workspace" strong>
          <ul className="grid gap-2 sm:grid-cols-2">
            {PARTS.map((p) => (
              <li key={p.name} className="rounded-lg border border-border bg-surface/70 p-3">
                <p className="font-display text-[16px] font-semibold">{p.name}</p>
                <p className="mt-0.5 text-[13px] leading-5 text-fg-muted">{p.does}</p>
              </li>
            ))}
          </ul>
        </Node>

        <Wire label="builds and runs" back="usage, logs" className="order-5 lg:order-none" />

        <Node label="Google Cloud" note="Ours, or your project" className="order-6 lg:order-none">
          <ul className="flex flex-col gap-1 text-[15px]">
            {["Cloud Run services", "Cloud Run jobs", "NVIDIA L4 GPUs", "BigQuery, your warehouse"].map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </Node>

        {/* The person: under ShipCube, on every screen size. */}
        <div className="order-4 flex flex-col items-center pb-2 lg:order-none lg:col-start-3 lg:pb-0">
          <div className="flex h-12 flex-col items-center" aria-hidden>
            <span className="w-px flex-1 border-l border-dashed border-[var(--approve)]" />
          </div>
          <p className="-mt-1 mb-2 text-[12px] text-[var(--approve)]">approval link, for risky steps only</p>
          <div className="w-full max-w-[420px] rounded-xl border border-[var(--approve)] bg-surface/70 p-4 text-center">
            <p className="font-display text-[16px] font-semibold">You, in the console</p>
            <p className="mt-0.5 text-[13px] leading-5 text-fg-muted">
              Approve or decline with one click. The agent can&apos;t approve its own requests.
            </p>
          </div>
        </div>
      </div>
    </figure>
  );
}

function Node({
  label,
  note,
  strong,
  className,
  children,
}: {
  label: string;
  note: string;
  strong?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "relative rounded-xl border p-4 sm:p-5",
        strong ? "border-accent bg-surface-2/80" : "border-input-border bg-surface/50",
        className,
      )}
    >
      {/* Drafting marks on the corners. */}
      <span className="absolute -top-px -left-px size-2.5 border-t-2 border-l-2 border-accent" aria-hidden />
      <span className="absolute -right-px -bottom-px size-2.5 border-r-2 border-b-2 border-accent" aria-hidden />
      <div className="mb-3">
        <h3 className="font-display text-[19px] font-semibold">{label}</h3>
        <p className="text-[12px] text-fg-subtle">{note}</p>
      </div>
      {children}
    </div>
  );
}

/** A connector between two nodes: across on wide screens, down on narrow ones, labelled both ways. */
function Wire({ label, back, className }: { label: string; back: string; className?: string }) {
  return (
    <div className={cn("flex items-center justify-center py-2 lg:px-1 lg:py-0", className)} aria-hidden>
      <div className="flex flex-row items-center gap-3 lg:w-[120px] lg:flex-col lg:gap-1.5">
        <span className="text-[12px] whitespace-nowrap text-accent lg:order-1">
          {label} <span className="lg:hidden">&darr;</span>
          <span className="hidden lg:inline">&rarr;</span>
        </span>
        <span className="h-10 w-px bg-accent lg:order-2 lg:h-px lg:w-full" />
        <span className="text-[12px] whitespace-nowrap text-fg-subtle lg:order-3">
          <span className="lg:hidden">&uarr;</span>
          <span className="hidden lg:inline">&larr;</span> {back}
        </span>
      </div>
    </div>
  );
}
