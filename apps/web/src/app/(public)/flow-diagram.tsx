import { cn } from "@/lib/cn";

// "How it fits together", on blueprint paper. One line, three stops: your agent, ShipCube, Google
// Cloud, with what travels between them on the line. It runs across on wide screens and down on
// narrow ones. The person who approves isn't a stop on that line: they branch off the approval gate.

const PARTS = [
  { name: "Load estimator", does: "Profiles the code, picks where it runs, prices it" },
  { name: "Approval gate", does: "Holds first deploys, cap raises, sharing and deletes" },
  { name: "AI gateway", does: "Caps each app's tokens and spend" },
  { name: "Data layer", does: "Named measures over your warehouse, not raw SQL" },
];

export function FlowDiagram() {
  return (
    <figure className="tone-blue blueprint overflow-hidden rounded-2xl px-6 py-8 sm:p-10 lg:p-12">
      <figcaption className="sr-only">
        Your agent calls ShipCube&apos;s MCP tools. ShipCube estimates, gates, caps and queries, then builds and runs the
        work on Google Cloud. Risky steps send you an approval link; you decide in the console.
      </figcaption>
      <ol className="grid lg:grid-cols-[minmax(0,0.75fr)_minmax(0,1.55fr)_minmax(0,0.85fr)]">
        <Stop title="Your agent" note="Any MCP client" next="MCP tool calls" back="results">
          <List items={["Muse", "Claude Code", "Cursor"]} />
        </Stop>

        <Stop title="ShipCube" note="Your workspace" next="builds and runs" back="usage">
          <ul className="grid gap-x-8 gap-y-5 sm:grid-cols-2">
            {PARTS.map((p) => (
              <li key={p.name}>
                <p className="font-display text-[17px] font-semibold">{p.name}</p>
                <p className="mt-1 text-[14px] leading-5 text-fg-muted">{p.does}</p>
              </li>
            ))}
          </ul>
          {/* The branch: the approval gate asks a person. */}
          <div className="mt-6 border-l-2 border-dashed border-[var(--approve)] py-1 pl-5">
            <p className="text-[13px] text-[var(--approve)]">The approval gate sends you a link, for risky steps only</p>
            <p className="mt-2 font-display text-[19px] font-semibold">You, in the console</p>
            <p className="mt-1 text-[14px] leading-5 text-fg-muted">
              Approve or decline with one click. The agent can&apos;t approve its own requests.
            </p>
          </div>
        </Stop>

        <Stop title="Google Cloud" note="Ours, or your project">
          <List items={["Cloud Run services", "Cloud Run jobs", "NVIDIA L4 GPUs", "BigQuery, your warehouse"]} />
        </Stop>
      </ol>
    </figure>
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

/**
 * A stop on the line. Narrow: the dot sits left and the line runs down to the next stop, with what
 * travels written beside it. Wide: the dot sits on top and the line runs right, labelled above.
 */
function Stop({
  title,
  note,
  next,
  back,
  children,
}: {
  title: string;
  note: string;
  next?: string;
  back?: string;
  children: React.ReactNode;
}) {
  return (
    <li className={cn("relative pl-9 lg:pl-0", next ? "pb-6 lg:pr-12 lg:pb-0" : "")}>
      {/* The rail: dot and line. */}
      <div className="absolute inset-y-0 left-0 w-3 lg:relative lg:inset-auto lg:mb-8 lg:h-3 lg:w-auto" aria-hidden>
        <span className="absolute top-2 left-0 size-3 rounded-full bg-mark lg:top-0" />
        {next && (
          <>
            <span className="absolute top-7 -bottom-2 left-[5.5px] w-px bg-mark/70 lg:top-[5.5px] lg:-right-12 lg:bottom-auto lg:left-6 lg:h-px lg:w-auto" />
            <span className="absolute -top-6 right-0 hidden text-[12px] whitespace-nowrap text-accent lg:block">
              {next} &rarr;
            </span>
            <span className="absolute top-4 right-0 hidden text-[12px] whitespace-nowrap text-fg-subtle lg:block">
              &larr; {back}
            </span>
          </>
        )}
      </div>

      <h3 className="font-display text-[22px] leading-7 font-semibold tracking-[-0.01em]">{title}</h3>
      <p className="text-[13px] text-fg-subtle">{note}</p>
      <div className="mt-5">{children}</div>

      {next && (
        <p className="mt-6 text-[13px] text-fg-muted lg:hidden">
          <span className="text-accent">{next} &darr;</span>
          <span className="ml-3 text-fg-subtle">{back} &uarr;</span>
        </p>
      )}
    </li>
  );
}
