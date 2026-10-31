import { cn } from "@/lib/cn";

// "How it fits together", drawn on blueprint paper. Wide screens: three open columns (the agent,
// ShipCube's four parts, Google Cloud) joined by labelled connectors, with the person who approves
// under ShipCube. Narrower screens: the same story as one vertical line, step by step.

const AGENTS = ["Muse", "Claude Code", "Cursor"];
const CLOUD = ["Cloud Run services", "Cloud Run jobs", "NVIDIA L4 GPUs", "BigQuery, your warehouse"];
const PARTS = [
  { name: "Load estimator", does: "Profiles the code, picks where it runs, prices it" },
  { name: "Approval gate", does: "Holds first deploys, cap raises, sharing and deletes" },
  { name: "AI gateway", does: "Caps each app's tokens and spend" },
  { name: "Data layer", does: "Named measures over your warehouse, not raw SQL" },
];
const YOU = "Approve or decline with one click. The agent can't approve its own requests.";

export function FlowDiagram() {
  return (
    <figure className="tone-blue blueprint overflow-hidden rounded-2xl p-6 sm:p-10 lg:p-12">
      <figcaption className="sr-only">
        Your agent calls ShipCube&apos;s MCP tools. ShipCube estimates, gates, caps and queries, then builds and runs the
        work on Google Cloud. Risky steps send you an approval link; you decide in the console.
      </figcaption>
      <Columns />
      <Timeline />
    </figure>
  );
}

function Parts({ className }: { className?: string }) {
  return (
    <ul className={cn("grid gap-x-8 gap-y-5", className)}>
      {PARTS.map((p) => (
        <li key={p.name}>
          <p className="font-display text-[17px] font-semibold">{p.name}</p>
          <p className="mt-1 text-[14px] leading-5 text-fg-muted">{p.does}</p>
        </li>
      ))}
    </ul>
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

/* Wide screens. */

function Columns() {
  return (
    <div className="hidden gap-y-2 lg:grid lg:grid-cols-[minmax(0,0.75fr)_auto_minmax(0,1.5fr)_auto_minmax(0,0.9fr)]">
      <Column title="Your agent" note="Any MCP client">
        <List items={AGENTS} />
      </Column>
      <Wire label="MCP tool calls" back="results" />
      <Column title="ShipCube" note="Your workspace" big>
        <Parts className="grid-cols-2" />
      </Column>
      <Wire label="builds and runs" back="usage" />
      <Column title="Google Cloud" note="Ours, or your project">
        <List items={CLOUD} />
      </Column>
      <div className="col-start-3 flex flex-col items-center pt-2 text-center">
        <span className="h-10 border-l border-dashed border-[var(--approve)]" aria-hidden />
        <p className="mt-2 text-[12px] text-[var(--approve)]">approval link, for risky steps only</p>
        <p className="mt-3 flex items-center gap-2 font-display text-[19px] font-semibold">
          <span className="size-2 rounded-full bg-[var(--approve)]" aria-hidden />
          You, in the console
        </p>
        <p className="mt-1 max-w-[40ch] text-[14px] leading-5 text-fg-muted">{YOU}</p>
      </div>
    </div>
  );
}

function Column({ title, note, big, children }: { title: string; note: string; big?: boolean; children: React.ReactNode }) {
  return (
    <div className="py-2">
      <h3 className={cn("font-display font-semibold tracking-[-0.01em]", big ? "text-[26px]" : "text-[21px]")}>{title}</h3>
      <p className="text-[13px] text-fg-subtle">{note}</p>
      <div className="mt-5">{children}</div>
    </div>
  );
}

/** A connector between two columns, labelled both ways. */
function Wire({ label, back }: { label: string; back: string }) {
  return (
    <div className="flex justify-center px-4 pt-12" aria-hidden>
      <div className="flex w-[110px] flex-col gap-1.5">
        <span className="text-[12px] whitespace-nowrap text-accent">{label} &rarr;</span>
        <span className="h-px w-full bg-mark" />
        <span className="text-right text-[12px] whitespace-nowrap text-fg-subtle">&larr; {back}</span>
      </div>
    </div>
  );
}

/* Narrower screens: one line, a dot per step, and what travels between them. */

function Timeline() {
  return (
    <ol className="lg:hidden">
      <Step title="Your agent" note="Any MCP client">
        <p className="text-[15px]">{AGENTS.join(", ")}</p>
      </Step>
      <Hop>Sends MCP tool calls; results come back</Hop>
      <Step title="ShipCube" note="Your workspace">
        <Parts className="sm:grid-cols-2" />
      </Step>
      <Hop approve>An approval link, for risky steps only</Hop>
      <Step title="You, in the console" approve>
        <p className="text-[14px] leading-5 text-fg-muted">{YOU}</p>
      </Step>
      <Hop>Builds and runs the work; usage comes back</Hop>
      <Step title="Google Cloud" note="Ours, or your project" last>
        <p className="text-[15px]">{CLOUD.join(", ")}</p>
      </Step>
    </ol>
  );
}

function Step({
  title,
  note,
  approve,
  last,
  children,
}: {
  title: string;
  note?: string;
  approve?: boolean;
  last?: boolean;
  children: React.ReactNode;
}) {
  return (
    <li className="relative pl-8">
      <span
        className={cn("absolute top-2 left-0 size-3 rounded-full", approve ? "bg-[var(--approve)]" : "bg-mark")}
        aria-hidden
      />
      {!last && <span className="absolute top-6 bottom-0 left-[5.5px] w-px bg-mark/60" aria-hidden />}
      <h3 className="font-display text-[21px] font-semibold tracking-[-0.01em]">{title}</h3>
      {note && <p className="text-[13px] text-fg-subtle">{note}</p>}
      <div className="mt-3 pb-2">{children}</div>
    </li>
  );
}

function Hop({ approve, children }: { approve?: boolean; children: React.ReactNode }) {
  return (
    <li className="relative py-3 pl-8">
      <span
        className={cn(
          "absolute inset-y-0 left-[5.5px] w-0",
          approve ? "border-l border-dashed border-[var(--approve)]" : "border-l border-mark/60",
        )}
        aria-hidden
      />
      <p className={cn("text-[13px]", approve ? "text-[var(--approve)]" : "text-fg-muted")}>{children}</p>
    </li>
  );
}
