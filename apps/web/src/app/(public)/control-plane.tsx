import { cn } from "@/lib/cn";

// What one ShipCube workspace does, as six jobs. Each says what it does in a sentence, then gives a
// real example: what you'd say to your agent, and the ShipCube tool that does it. The requests are
// made up; the tools are the MCP server's own.
const JOBS = [
  {
    verb: "Deploy",
    what: "Dashboards and internal tools your agent builds go live at a private URL, behind Google sign-in. No Dockerfile.",
    say: "Ship this revenue tracker and share it with finance.",
    tools: ["deploy_app", "set_access"],
  },
  {
    verb: "Schedule",
    what: "Notebooks, scripts and models run as Cloud Run jobs or on a GPU, on a schedule, and stop when they're done.",
    say: "Run my churn notebook every night at 2am.",
    tools: ["deploy_app"],
  },
  {
    verb: "Estimate",
    what: "Before anything runs, ShipCube profiles the code and says what kind of work it is, where it should run, and roughly what it costs a month.",
    say: "What would this cost to run for 25 people?",
    tools: ["estimate_load"],
  },
  {
    verb: "Query",
    what: "Your agent reads the warehouse through named measures, like revenue or active customers, not raw SQL. Model changes are only proposed.",
    say: "Revenue by region for last month.",
    tools: ["data_query"],
  },
  {
    verb: "Cap",
    what: "Every app gets a monthly spend limit and a daily limit on AI tokens, enforced where its model calls go out.",
    say: "Keep this under $20 a month.",
    tools: ["set_caps"],
  },
  {
    verb: "Approve",
    what: "First deploys to your project, cap raises, sharing with everyone, and deletes wait for a person. The agent can't approve its own requests.",
    say: "Delete the dashboard nobody opens anymore.",
    tools: [],
  },
];

export function ControlPlane() {
  return (
    <ul className="grid gap-x-12 gap-y-14 sm:grid-cols-2 lg:grid-cols-3">
      {JOBS.map((j) => (
        <li key={j.verb} className="flex flex-col border-t-2 border-fg pt-5">
          <h3 className="font-display text-[30px] leading-none font-extrabold tracking-[-0.02em]">{j.verb}</h3>
          <p className="mt-3 text-[15px] leading-6 text-fg-muted">{j.what}</p>
          {/* The example: what you say, then what answers it. */}
          <div className="mt-auto pt-6">
            <p className="text-[15px] leading-6 text-fg">&ldquo;{j.say}&rdquo;</p>
            <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px]">
              <span className="text-fg-subtle" aria-hidden>
                &rarr;
              </span>
              {j.tools.length ? (
                j.tools.map((t) => (
                  <code key={t} className="rounded bg-accent-soft px-1.5 py-0.5 font-mono text-[12.5px] text-accent">
                    shipcube.{t}
                  </code>
                ))
              ) : (
                <span className={cn("inline-flex items-center gap-1.5 font-medium text-[var(--approve)]")}>
                  <span className="size-2 rounded-full bg-[var(--approve)]" aria-hidden />
                  waits for your yes in the console
                </span>
              )}
            </p>
          </div>
        </li>
      ))}
    </ul>
  );
}
