"use client";

import { AppWindow, Cpu, Database, Gauge, Lock, Sparkles, UserCheck } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/cn";

type Job = { icon: typeof AppWindow; title: string; body: string; points: string[]; Art: () => React.ReactNode };

// What one ShipCube workspace does, as jobs. Each picture is an illustration of the console, not a screenshot.
const JOBS: Job[] = [
  {
    icon: AppWindow,
    title: "Run the apps your agent builds",
    body: "Dashboards, trackers and internal tools go live at a private URL, behind Google sign-in, without anyone writing a Dockerfile.",
    points: ["Built and deployed from the conversation", "Private by default", "Shared with people and groups you name"],
    Art: AppArt,
  },
  {
    icon: Cpu,
    title: "Run heavy work in the cloud, not on your laptop",
    body: "Notebooks, scripts and models that need memory or a GPU run as Cloud Run jobs or on an NVIDIA L4, on a schedule, and stop when they're done.",
    points: ["Scheduled jobs, billed only while they run", "GPU when the code needs one", "Nothing left running on your machine"],
    Art: JobsArt,
  },
  {
    icon: Gauge,
    title: "Know the cost before anything runs",
    body: "ShipCube profiles the code, classifies the workload and picks where it should run. The reasoning and a monthly cost range come back first.",
    points: ["Workload class and confidence", "Tier, resources and a cost range", "Caps recommended up front"],
    Art: EstimateArt,
  },
  {
    icon: Database,
    title: "Connect your warehouse, safely",
    body: "Agents query your data through named measures and dimensions, not raw SQL. They can propose a model change; a person reviews the diff.",
    points: ["One definition per metric", "Read through the semantic layer", "Model changes are only proposed"],
    Art: DataArt,
  },
  {
    icon: Sparkles,
    title: "Cap what AI costs",
    body: "Apps call models through ShipCube's gateway, with token and spend limits per app. Bring your own provider key, or use the default.",
    points: ["Per-app token and spend caps", "Your key, kept in Secret Manager", "Usage visible per app"],
    Art: GatewayArt,
  },
  {
    icon: UserCheck,
    title: "Approve the risky parts",
    body: "First deploys to your project, cap raises, domain-wide sharing and deletes wait for a person. The agent can't approve its own requests.",
    points: ["One click in the console", "The page says exactly what changes", "Everything else just happens"],
    Art: ApprovalArt,
  },
];

export function ControlPlane() {
  const [active, setActive] = useState(0);
  const job = JOBS[active];
  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] lg:gap-12">
      <ol className="flex flex-col" aria-label="What ShipCube does">
        {JOBS.map((j, i) => (
          <li key={j.title}>
            <button
              type="button"
              onClick={() => setActive(i)}
              onMouseEnter={() => setActive(i)}
              aria-pressed={i === active}
              className={cn(
                "flex w-full items-center gap-3 border-l-2 py-3 pr-2 pl-4 text-left transition-colors",
                i === active ? "border-accent text-fg" : "border-border text-fg-muted hover:text-fg",
              )}
            >
              <j.icon className={cn("size-[18px] shrink-0", i === active ? "text-accent" : "text-fg-subtle")} aria-hidden />
              <span className="text-[16px] font-medium sm:text-[17px]">{j.title}</span>
            </button>
          </li>
        ))}
      </ol>
      <div className="flex flex-col gap-6" aria-live="polite">
        <div className="tone-blue blueprint rounded-xl border border-border p-4 sm:p-6">
          <job.Art key={active} />
        </div>
        <div>
          <p className="max-w-[60ch] text-[15px] leading-6 text-fg-muted">{job.body}</p>
          <ul className="mt-3 flex flex-wrap gap-2">
            {job.points.map((p) => (
              <li key={p} className="rounded-full border border-border px-3 py-1 text-[12.5px] text-fg-muted">
                {p}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

/* The illustrations: small, quiet pieces of console, drawn with the page's tokens. */

function Row({ label, value, tone }: { label: string; value: React.ReactNode; tone?: "good" | "wait" }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-border py-2 text-[13px] last:border-0">
      <span className="text-fg-muted">{label}</span>
      <span className={cn("text-right", tone === "good" ? "text-good" : tone === "wait" ? "text-warning" : "text-fg")}>{value}</span>
    </div>
  );
}

function AppArt() {
  return (
    <div className="art-in">
      <div className="flex items-center gap-2 rounded-md border border-border bg-surface px-3 py-2 font-mono text-[12px] text-fg-muted">
        <Lock className="size-3 text-good" aria-hidden />
        revenue-tracker, private URL
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2" aria-hidden>
        {[62, 80, 45].map((v, i) => (
          <div key={i} className="rounded-md border border-border bg-surface p-3">
            <div className="h-1.5 w-10 rounded bg-border-strong" />
            <div className="mt-6 h-1.5 rounded bg-accent" style={{ width: `${v}%` }} />
          </div>
        ))}
      </div>
      <div className="mt-3">
        <Row label="Who can open it" value="finance-team@example.com" />
        <Row label="Sign-in" value="Google" />
        <Row label="Status" value="Serving" tone="good" />
      </div>
    </div>
  );
}

function JobsArt() {
  const jobs = [
    { name: "churn-notebook", where: "Cloud Run job", when: "Nightly, 02:00", runs: [2] },
    { name: "ticket-embeddings", where: "NVIDIA L4", when: "Once, on demand", runs: [9, 10, 11] },
    { name: "weekly-forecast", where: "Cloud Run job", when: "Mondays, 06:00", runs: [6] },
  ];
  return (
    <div className="art-in flex flex-col gap-4">
      {jobs.map((j) => (
        <div key={j.name}>
          <div className="flex items-baseline justify-between gap-3 text-[13px]">
            <span className="font-mono text-fg">{j.name}</span>
            <span className="text-fg-muted">
              {j.where}, {j.when.toLowerCase()}
            </span>
          </div>
          {/* A day in hours: lit cells are when it runs, and the only time it bills. */}
          <div className="mt-2 grid grid-cols-24 gap-[3px]" aria-hidden>
            {Array.from({ length: 24 }, (_, h) => (
              <div key={h} className={cn("h-3 rounded-[2px]", j.runs.includes(h) ? "bg-accent" : "bg-surface-2")} />
            ))}
          </div>
        </div>
      ))}
      <p className="text-[12px] text-fg-subtle">Lit hours are when each job runs, and the only hours it costs anything.</p>
    </div>
  );
}

function EstimateArt() {
  return (
    <div className="art-in">
      <div className="flex items-baseline justify-between gap-4">
        <span className="text-[13px] text-fg-muted">Expected monthly cost</span>
        <span className="text-[12px] text-fg-subtle">82% confidence</span>
      </div>
      <div className="mt-1 font-display text-[40px] leading-none font-semibold tracking-[-0.02em]">$4 to $11</div>
      <div className="relative mt-4 h-2 rounded-full bg-surface-2" aria-hidden>
        <div className="absolute inset-y-0 left-[18%] w-[34%] rounded-full bg-accent/60" />
        <div className="absolute -top-1 left-[30%] h-4 w-0.5 bg-fg" />
      </div>
      <div className="mt-4">
        <Row label="Workload" value="Interactive, bursty" />
        <Row label="Runs on" value="Serverless, scales to zero" />
        <Row label="Recommended caps" value="$20 a month, 200k tokens a day" />
      </div>
    </div>
  );
}

function DataArt() {
  return (
    <div className="art-in grid gap-4 sm:grid-cols-2">
      <div>
        <p className="text-[12px] text-fg-subtle">Measures</p>
        <ul className="mt-2 flex flex-col gap-1.5 font-mono text-[12.5px]">
          {["orders.revenue", "orders.count", "customers.active", "customers.churned"].map((m) => (
            <li key={m} className="rounded border border-border bg-surface px-2 py-1 text-fg">
              {m}
            </li>
          ))}
        </ul>
      </div>
      <div>
        <p className="text-[12px] text-fg-subtle">Proposed by the agent</p>
        <pre className="mt-2 overflow-hidden rounded border border-border bg-surface p-2 font-mono text-[12px] leading-5">
          <span className="text-good">+ refunds:</span>
          {"\n"}
          <span className="text-good">+   sql: amount &lt; 0</span>
          {"\n"}
          <span className="text-good">+   type: sum</span>
        </pre>
        <p className="mt-2 text-[12px] text-warning">Waiting for review</p>
      </div>
    </div>
  );
}

function GatewayArt() {
  const meters = [
    { label: "Tokens today", used: 64, cap: "200k" },
    { label: "Spend this month", used: 38, cap: "$20" },
  ];
  return (
    <div className="art-in flex flex-col gap-5">
      {meters.map((m) => (
        <div key={m.label}>
          <div className="flex justify-between text-[13px]">
            <span className="text-fg-muted">{m.label}</span>
            <span className="text-fg">
              {m.used}% of {m.cap}
            </span>
          </div>
          <div className="mt-2 h-2 rounded-full bg-surface-2" aria-hidden>
            <div className="h-full rounded-full bg-accent" style={{ width: `${m.used}%` }} />
          </div>
        </div>
      ))}
      <div>
        <Row label="Provider" value="Your key, in Secret Manager" />
        <Row label="When a cap is reached" value="Calls stop until it resets" />
      </div>
    </div>
  );
}

function ApprovalArt() {
  return (
    <div className="art-in">
      <div className="flex items-center gap-2 text-[13px] text-warning">
        <span className="stage-wait inline-block size-2 rounded-full bg-warning" aria-hidden />
        Your agent is asking
      </div>
      <p className="mt-2 font-display text-[20px] font-semibold">Delete old-dashboard</p>
      <div className="mt-3">
        <Row label="What changes" value="The app stops serving" />
        <Row label="Data" value="Kept, restorable from the console" />
        <Row label="Asked by" value="Muse / Claude Code, with your token" />
      </div>
      <div className="mt-4 flex gap-2" aria-hidden>
        <span className="rounded-md bg-cta px-3 py-1.5 text-[13px] font-medium text-cta-fg">Delete app</span>
        <span className="rounded-md border border-border px-3 py-1.5 text-[13px] text-fg-muted">Decline</span>
      </div>
    </div>
  );
}
