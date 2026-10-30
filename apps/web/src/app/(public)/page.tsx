import type { Metadata } from "next";
import { ArrowRight, Check, Database, Gauge, ShieldCheck, Sparkles, UserCheck } from "lucide-react";
import Link from "next/link";
import { CodeBlock } from "@/components/copy-button";
import { Wordmark } from "@/components/logo";
import { RequestAccessForm } from "@/components/request-access-form";
import { ThemeToggle } from "@/components/theme-toggle";
import { ButtonLink } from "@/components/ui";
import { auth } from "@/lib/auth";
import { config } from "@/lib/server/config";

export const metadata: Metadata = {
  title: { absolute: "ShipCube: deploy internal apps from your coding agent" },
  description:
    "ShipCube is an MCP server for Claude Code, Cursor and other agents. Agents estimate, deploy, inspect and cap internal apps on Google Cloud; risky actions wait for a person.",
};

// Public: the only console page that needs no sign-in (src/middleware.ts).
export const dynamic = "force-dynamic";

const STEPS = [
  {
    title: "Connect your agent",
    body: "Add ShipCube's MCP server to Claude Code, Cursor or any MCP client, with a token you create in the console.",
  },
  {
    title: "Ask in plain words",
    body: "“Deploy this and share it with the finance team.” The agent profiles the app, picks a tier and deploys it to Cloud Run.",
  },
  {
    title: "Approve and keep watch",
    body: "Actions that need a person wait in the console. Caps, access, logs and spend for every app are there too.",
  },
];

const FEATURES = [
  {
    icon: Gauge,
    title: "Placement from a load estimate",
    body: "Before deploying, ShipCube profiles the app, classifies its workload and picks serverless, batch or always-on. The reasoning and the expected monthly cost come back with it.",
  },
  {
    icon: UserCheck,
    title: "Approvals for risky actions",
    body: "A first deploy to your own GCP project, a cap raise, sharing with a whole domain and deletes wait for a person. Agents can't approve their own requests.",
  },
  {
    icon: ShieldCheck,
    title: "Private by default, capped",
    body: "Apps open only for the people and groups you share them with, behind Google sign-in. Spend, request and instance caps are set per app.",
  },
  {
    icon: Database,
    title: "A governed data layer",
    body: "Agents query your warehouse through a semantic model of named measures and dimensions. They can propose model changes; a person reviews the diff and applies it.",
  },
  {
    icon: Sparkles,
    title: "An AI gateway with caps",
    body: "Deployed apps call models through ShipCube's gateway, with per-app token and spend limits. Bring your own provider key, or use the default.",
  },
];

const TRANSCRIPT = [
  { who: "you", text: "Deploy this tracker and share it with finance-team@example.com. Keep it under $20 a month." },
  { who: "tool", name: "estimate_load", text: "bursty interactive → T1 Serverless, scale to zero" },
  { who: "tool", name: "deploy_app", text: "succeeded: private Cloud Run URL" },
  { who: "tool", name: "set_access", text: "group:finance-team@example.com can view" },
  { who: "tool", name: "set_caps", text: "monthly spend capped at $20" },
] as const;

export default async function Landing() {
  const session = await auth.getSession().catch(() => null);
  const mcpUrl = config().mcpUrl;

  return (
    <div className="min-h-dvh">
      <a href="#main" className="sr-only-focusable rounded-md bg-accent px-3 py-1.5 font-medium text-accent-fg">
        Skip to content
      </a>
      <header className="border-b border-border">
        <div className="mx-auto flex h-14 max-w-[1120px] items-center justify-between gap-4 px-4 sm:px-6">
          <Link href="/" aria-label="ShipCube home">
            <Wordmark />
          </Link>
          <nav aria-label="Site" className="flex items-center gap-1 sm:gap-2">
            <a href="#how-it-works" className="hidden rounded-md px-2 py-1 text-fg-muted hover:text-fg sm:inline">
              How it works
            </a>
            <ThemeToggle />
            {session ? (
              <ButtonLink href="/apps" size="sm" variant="secondary">
                Open console
              </ButtonLink>
            ) : (
              <ButtonLink href="/sign-in" size="sm" variant="secondary">
                Sign in
              </ButtonLink>
            )}
          </nav>
        </div>
      </header>

      <main id="main">
        <section className="mx-auto grid max-w-[1120px] gap-10 px-4 pt-14 pb-16 sm:px-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,460px)] lg:items-center lg:pt-20">
          <div>
            <p className="text-[12px] font-medium text-accent">Early access</p>
            <h1 className="mt-2 text-[32px] leading-[38px] font-semibold tracking-[-0.02em] sm:text-[40px] sm:leading-[46px]">
              Deploy internal apps from your coding agent.
            </h1>
            <p className="mt-4 max-w-[58ch] text-[15px] leading-6 text-fg-muted">
              ShipCube is an MCP server. Your agent estimates the load, deploys to Google Cloud, shares the app and sets its
              caps, all from the conversation you&apos;re already in. Anything risky waits for a person to approve it.
            </p>
            <div className="mt-6 flex flex-wrap items-center gap-3">
              <ButtonLink href="#request-access" variant="primary">
                Request access
              </ButtonLink>
              <Link href="/sign-in" className="inline-flex items-center gap-1 text-accent hover:underline">
                Already approved? Sign in <ArrowRight className="size-3.5" aria-hidden />
              </Link>
            </div>
          </div>

          <figure className="overflow-hidden rounded-lg border border-border bg-surface">
            <figcaption className="border-b border-border bg-surface-2 px-3 py-2 text-[12px] text-fg-muted">
              A prompt in Claude Code, and ShipCube&apos;s tool calls
            </figcaption>
            <ol className="flex flex-col gap-2 p-3">
              {TRANSCRIPT.map((t, i) =>
                t.who === "you" ? (
                  <li key={i} className="rounded-md bg-accent-soft px-3 py-2">
                    {t.text}
                  </li>
                ) : (
                  <li key={i} className="flex flex-wrap items-baseline gap-x-2 rounded-md border border-border px-3 py-1.5 font-mono text-[12px]">
                    <span className="font-medium text-fg">{t.name}</span>
                    <span className="text-fg-muted">{t.text}</span>
                  </li>
                ),
              )}
            </ol>
          </figure>
        </section>

        <section id="how-it-works" aria-labelledby="how-heading" className="border-t border-border bg-surface">
          <div className="mx-auto max-w-[1120px] px-4 py-14 sm:px-6">
            <h2 id="how-heading" className="text-[20px] leading-7 font-semibold">
              How it works
            </h2>
            <ol className="mt-6 grid gap-6 md:grid-cols-3">
              {STEPS.map((s, i) => (
                <li key={s.title} className="flex gap-3">
                  <span className="num grid size-7 shrink-0 place-items-center rounded-full border border-border-strong text-[12px] font-medium text-fg-muted" aria-hidden>
                    {i + 1}
                  </span>
                  <div>
                    <h3 className="font-semibold">{s.title}</h3>
                    <p className="mt-1 text-fg-muted">{s.body}</p>
                  </div>
                </li>
              ))}
            </ol>
            <div className="mt-8 max-w-[760px]">
              <CodeBlock
                label="Terminal"
                code={`claude mcp add --transport http shipcube ${mcpUrl} \\\n  --header "Authorization: Bearer $SHIPCUBE_TOKEN"`}
              />
            </div>
          </div>
        </section>

        <section aria-labelledby="features-heading" className="border-t border-border">
          <div className="mx-auto max-w-[1120px] px-4 py-14 sm:px-6">
            <h2 id="features-heading" className="text-[20px] leading-7 font-semibold">
              What the agent can do, and what it can&apos;t
            </h2>
            <ul className="mt-6 grid gap-x-8 gap-y-6 sm:grid-cols-2 lg:grid-cols-3">
              {FEATURES.map(({ icon: Icon, title, body }) => (
                <li key={title}>
                  <h3 className="flex items-center gap-2 font-semibold">
                    <Icon className="size-4 text-accent" aria-hidden />
                    {title}
                  </h3>
                  <p className="mt-1 text-fg-muted">{body}</p>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section id="request-access" aria-labelledby="request-heading" className="border-t border-border bg-surface">
          <div className="mx-auto grid max-w-[1120px] gap-8 px-4 py-14 sm:px-6 md:grid-cols-[minmax(0,1fr)_minmax(0,440px)]">
            <div>
              <h2 id="request-heading" className="text-[20px] leading-7 font-semibold">
                Request access
              </h2>
              <p className="mt-2 max-w-[52ch] text-fg-muted">
                ShipCube is in early access, and each request is reviewed by a person. When yours is approved, sign in with
                the Google account for the email you gave, and your workspace is waiting.
              </p>
              <ul className="mt-4 flex flex-col gap-1.5 text-fg-muted">
                {["Apps run in ShipCube's GCP project, or link your own", "Works with Claude Code, Cursor and other MCP clients"].map((t) => (
                  <li key={t} className="flex gap-2">
                    <Check className="mt-0.5 size-3.5 shrink-0 text-good" aria-hidden />
                    {t}
                  </li>
                ))}
              </ul>
            </div>
            <div className="rounded-lg border border-border bg-bg p-4 sm:p-5">
              <RequestAccessForm accountEmail={session?.user.email} />
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-border">
        <div className="mx-auto flex max-w-[1120px] flex-wrap items-center justify-between gap-2 px-4 py-6 text-[12px] text-fg-muted sm:px-6">
          <span>ShipCube, built for the Meta Global AI Developer Hackathon.</span>
          <Link href="/sign-in" className="hover:text-fg">
            Sign in
          </Link>
        </div>
      </footer>
    </div>
  );
}
