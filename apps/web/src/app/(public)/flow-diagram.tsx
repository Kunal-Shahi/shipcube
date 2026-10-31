import { cn } from "@/lib/cn";

// "How it fits together", drawn on blueprint paper: the agent on the left, ShipCube's four parts in
// the middle, Google Cloud on the right, and the person who approves underneath. No boxes: open
// columns, joined by connectors labelled with what travels along them. On narrow screens it stacks,
// with the approver kept under ShipCube.

const PARTS = [
  { name: "Load estimator", does: "Profiles the code, picks where it runs, prices it" },
  { name: "Approval gate", does: "Holds first deploys, cap raises, sharing and deletes" },
  { name: "AI gateway", does: "Caps each app's tokens and spend" },
  { name: "Data layer", does: "Named measures over your warehouse, not raw SQL" },
];

const COLS = "lg:grid-cols-[minmax(0,0.75fr)_minmax(0,auto)_minmax(0,1.5fr)_minmax(0,auto)_minmax(0,0.9fr)]";

export function FlowDiagram() {
  return (
    <figure className="tone-blue blueprint overflow-hidden rounded-2xl p-6 sm:p-10 lg:p-12">
      <figcaption className="sr-only">
        Your agent calls ShipCube&apos;s MCP tools. ShipCube estimates, gates, caps and queries, then builds and runs the
        work on Google Cloud. Risky steps send you an approval link; you decide in the console.
      </figcaption>
      <div className={cn("grid gap-y-2", COLS)}>
        <Column title="Your agent" note="Any MCP client">
          <List items={["Muse", "Claude Code", "Cursor"]} />
        </Column>

        <Wire label="MCP tool calls" back="results" />

        <Column title="ShipCube" note="Your workspace" big>
          <ul className="grid gap-x-8 gap-y-5 sm:grid-cols-2">
            {PARTS.map((p) => (
              <li key={p.name}>
                <p className="font-display text-[17px] font-semibold">{p.name}</p>
                <p className="mt-1 text-[14px] leading-5 text-fg-muted">{p.does}</p>
              </li>
            ))}
          </ul>
        </Column>

        <Wire label="builds and runs" back="usage" className="order-5 lg:order-none" />

        <Column title="Google Cloud" note="Ours, or your project" className="order-6 lg:order-none">
          <List items={["Cloud Run services", "Cloud Run jobs", "NVIDIA L4 GPUs", "BigQuery, your warehouse"]} />
        </Column>

        {/* The person: under ShipCube, on every screen size. */}
        <div className="order-4 flex flex-col items-start pt-2 pb-4 lg:order-none lg:col-start-3 lg:items-center lg:pb-0 lg:text-center">
          <span className="h-10 border-l border-dashed border-[var(--approve)]" aria-hidden />
          <p className="mt-2 text-[12px] text-[var(--approve)]">approval link, for risky steps only</p>
          <p className="mt-3 flex items-center gap-2 font-display text-[19px] font-semibold">
            <span className="size-2 rounded-full bg-[var(--approve)]" aria-hidden />
            You, in the console
          </p>
          <p className="mt-1 max-w-[40ch] text-[14px] leading-5 text-fg-muted">
            Approve or decline with one click. The agent can&apos;t approve its own requests.
          </p>
        </div>
      </div>
    </figure>
  );
}

function Column({
  title,
  note,
  big,
  className,
  children,
}: {
  title: string;
  note: string;
  big?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cn("py-2", className)}>
      <h3 className={cn("font-display font-semibold tracking-[-0.01em]", big ? "text-[26px]" : "text-[21px]")}>{title}</h3>
      <p className="text-[13px] text-fg-subtle">{note}</p>
      <div className="mt-5">{children}</div>
    </div>
  );
}

function List({ items }: { items: string[] }) {
  return (
    <ul className="flex flex-col gap-1.5 text-[15px]">
      {items.map((i) => (
        <li key={i}>{i}</li>
      ))}
    </ul>
  );
}

/** A connector between two columns: across on wide screens, down on narrow ones, labelled both ways. */
function Wire({ label, back, className }: { label: string; back: string; className?: string }) {
  return (
    <div className={cn("flex items-center py-3 lg:justify-center lg:px-4 lg:py-0 lg:pt-12", className)} aria-hidden>
      <div className="flex flex-row items-center gap-3 lg:w-[110px] lg:flex-col lg:items-stretch lg:gap-1.5">
        <span className="h-8 w-px bg-accent lg:order-2 lg:h-px lg:w-full" />
        <span className="text-[12px] whitespace-nowrap text-accent lg:order-1">
          {label} <span className="lg:hidden">&darr;</span>
          <span className="hidden lg:inline">&rarr;</span>
        </span>
        <span className="text-[12px] whitespace-nowrap text-fg-subtle lg:order-3 lg:text-right">
          <span className="lg:hidden">&uarr;</span>
          <span className="hidden lg:inline">&larr;</span> {back}
        </span>
      </div>
    </div>
  );
}
