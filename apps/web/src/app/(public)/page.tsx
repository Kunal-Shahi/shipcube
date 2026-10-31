import type { Metadata } from "next";
import { Bricolage_Grotesque } from "next/font/google";
import { headers } from "next/headers";
import { ArrowRight, Building2, KeyRound, ShieldCheck, UserCheck } from "lucide-react";
import Link from "next/link";
import { CodeBlock } from "@/components/copy-button";
import { Wordmark } from "@/components/logo";
import { RequestAccessForm } from "@/components/request-access-form";
import { ButtonLink } from "@/components/ui";
import { auth } from "@/lib/auth";
import { config } from "@/lib/server/config";
import { ControlPlane } from "./control-plane";
import { HeroStage } from "./hero-stage";

const display = Bricolage_Grotesque({ subsets: ["latin"], variable: "--font-display", weight: ["600", "800"] });

const TITLE = "ShipCube: your agent builds it, the cloud runs it";
const DESCRIPTION =
  "One control plane for AI-built apps, jobs and data on Google Cloud. Muse, Claude Code, Cursor and other agents estimate, deploy, schedule and cap work from the conversation, and a person approves anything risky.";

/** src/app/opengraph-image.tsx, made absolute with the request's host below. */
const OG_IMAGE = { url: "/opengraph-image", width: 1200, height: 630, alt: TITLE };

// The page is served on more than one host (run.app, a Firebase Hosting address), so absolute
// URLs, like the Open Graph image's, come from the request rather than from config.
async function requestOrigin() {
  const h = await headers();
  const host = h.get("x-forwarded-host") ?? h.get("host") ?? "localhost:3000";
  const proto = h.get("x-forwarded-proto") ?? (host.startsWith("localhost") || host.startsWith("127.") ? "http" : "https");
  return `${proto}://${host}`;
}

export async function generateMetadata(): Promise<Metadata> {
  return {
    metadataBase: new URL(await requestOrigin()),
    title: { absolute: TITLE },
    description: DESCRIPTION,
    openGraph: { title: TITLE, description: DESCRIPTION, type: "website", url: "/", siteName: "ShipCube", images: [OG_IMAGE] },
    twitter: { card: "summary_large_image", title: TITLE, description: DESCRIPTION, images: [OG_IMAGE] },
  };
}

// Public: the only page besides sign-in that needs no session (src/middleware.ts).
export const dynamic = "force-dynamic";

// Where each kind of work lands (packages/placement), and the hours of a day it's billed for.
// The hours are a picture of the billing model, not a measurement.
const COMPUTE = [
  { work: "A report that changes once a day", where: "Static snapshot, refreshed on a schedule", hours: [5] },
  { work: "A dashboard your team opens", where: "Serverless, scales to zero between visits", hours: [9, 10, 13, 14, 15, 17] },
  { work: "A notebook or script that runs nightly", where: "Cloud Run job on a schedule, billed while it runs", hours: [2, 3] },
  { work: "A model that needs a GPU", where: "NVIDIA L4, billed while it works", hours: [11, 12, 13] },
  { work: "An API that can't wait", where: "Always on, for steady latency", hours: Array.from({ length: 24 }, (_, h) => h) },
];

const FLOW = [
  { title: "Your agent", items: ["Muse", "Claude Code", "Cursor", "Any MCP client"] },
  { title: "ShipCube", items: ["Load estimator", "Approval gate", "AI gateway", "Semantic data layer"] },
  { title: "Google Cloud", items: ["Cloud Run services and jobs", "GPUs", "BigQuery and your warehouse"] },
];

const SECURITY = [
  { icon: Building2, text: "Every workspace builds and runs apps under its own service accounts, isolated from every other workspace." },
  { icon: ShieldCheck, text: "Apps are private by default, behind Google sign-in. Only the people and groups you share with can open them." },
  { icon: KeyRound, text: "Tokens are stored as hashes, and provider keys go to Secret Manager. Neither is ever shown again." },
  { icon: UserCheck, text: "A red team reviewed tokens, tenancy and the deploy path, and its findings were fixed before launch." },
];

const H2 = "font-display text-[32px] leading-[1.1] font-extrabold tracking-[-0.025em] sm:text-[40px]";

export default async function Landing() {
  const session = await auth.getSession().catch(() => null);
  const c = config();
  // The API accepts the form's cross-origin POST only from the listed console origins. On a host
  // that isn't listed, anonymous visitors are sent to the canonical one to request access.
  const origin = await requestOrigin();
  const formElsewhere =
    !session && c.consoleOrigins.length && !c.consoleOrigins.includes(origin) ? `${c.consoleOrigins[0]}/#request-access` : null;

  return (
    <div className={`landing ${display.variable} min-h-dvh`}>
      <a href="#main" className="sr-only-focusable rounded-md bg-accent px-3 py-1.5 font-medium text-accent-fg">
        Skip to content
      </a>

      <div className="landing-hero">
        <header className="mx-auto flex h-16 max-w-[1180px] items-center justify-between gap-4 px-4 sm:px-6">
          <Link href="/" aria-label="ShipCube home">
            <Wordmark />
          </Link>
          <nav aria-label="Site" className="flex items-center gap-1 sm:gap-3">
            <a href="#how-it-works" className="hidden rounded-md px-2 py-1 text-fg-muted hover:text-fg sm:inline">
              How it works
            </a>
            <a href="#security" className="hidden rounded-md px-2 py-1 text-fg-muted hover:text-fg sm:inline">
              Security
            </a>
            {session ? (
              <ButtonLink href="/apps" size="sm">
                Open console
              </ButtonLink>
            ) : (
              <Link href="/sign-in" className="rounded-md px-2 py-1 text-fg hover:text-accent">
                Sign in
              </Link>
            )}
          </nav>
        </header>

        <section className="mx-auto max-w-[1180px] px-4 pt-10 pb-16 sm:px-6 lg:pt-16 lg:pb-24">
          <div className="grid gap-8 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)] lg:items-end">
            <h1 className="font-display text-[44px] leading-[0.95] font-extrabold tracking-[-0.03em] text-balance sm:text-[68px] lg:text-[88px]">
              Your agent builds it. The cloud runs it.
            </h1>
            <div className="lg:pb-2">
              <p className="max-w-[48ch] text-[16px] leading-[26px] text-fg-muted sm:text-[17px] sm:leading-[28px]">
                ShipCube is one control plane for AI-built apps, jobs and data on Google Cloud. Ask in Muse, Claude Code or Cursor;
                it estimates, deploys, schedules and caps the work. No heavy laptop, and anything risky waits for your yes.
              </p>
              <div className="mt-6 flex flex-wrap items-center gap-4">
                <ButtonLink href="#request-access" variant="primary" className="h-11 px-5 text-[15px]">
                  Request access
                </ButtonLink>
                <Link href="/sign-in" className="text-fg-muted underline-offset-4 hover:text-fg hover:underline">
                  Approved already? Sign in
                </Link>
              </div>
            </div>
          </div>
          <div className="mt-12 lg:mt-16">
            <HeroStage />
          </div>
        </section>
      </div>

      <main id="main">
        <section id="how-it-works" aria-labelledby="plane-heading" className="border-t border-border bg-surface">
          <div className="mx-auto max-w-[1180px] px-4 py-20 sm:px-6">
            <h2 id="plane-heading" className={`max-w-[22ch] ${H2}`}>
              One control plane for everything your agent builds
            </h2>
            <p className="mt-4 max-w-[60ch] text-[16px] leading-[26px] text-fg-muted">
              Apps, heavy jobs, data and AI spend, in one workspace your agent can drive and you can oversee. Pick a job to see
              it.
            </p>
            <div className="mt-12">
              <ControlPlane />
            </div>
          </div>
        </section>

        <section aria-labelledby="compute-heading" className="border-t border-border">
          <div className="mx-auto grid max-w-[1180px] gap-12 px-4 py-20 sm:px-6 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
            <div>
              <h2 id="compute-heading" className={H2}>
                Your laptop isn&apos;t a server
              </h2>
              <p className="mt-4 max-w-[44ch] text-[16px] leading-[26px] text-fg-muted">
                Most heavy work runs once a day or once a week. You shouldn&apos;t need an expensive machine for it, or a
                server that runs all month. ShipCube profiles each workload, puts it where it costs least, and stops billing
                when it&apos;s done.
              </p>
            </div>
            <div>
              <div className="hidden pb-2 text-[12px] text-fg-subtle sm:grid sm:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] sm:gap-6" aria-hidden>
                <span />
                <span className="flex justify-between">
                  <span>Midnight</span>
                  <span>Noon</span>
                  <span>Midnight</span>
                </span>
              </div>
              <ul className="flex flex-col">
                {COMPUTE.map((c) => (
                  <li key={c.work} className="grid gap-2 border-t border-border py-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] sm:items-center sm:gap-6">
                    <div>
                      <p className="text-[15px] font-medium">{c.work}</p>
                      <p className="text-[13px] text-fg-muted">{c.where}</p>
                    </div>
                    <div className="grid grid-cols-24 gap-[3px]" role="img" aria-label={`Billed for ${c.hours.length} of 24 hours`}>
                      {Array.from({ length: 24 }, (_, h) => (
                        <span key={h} className={`h-5 rounded-[2px] ${c.hours.includes(h) ? "bg-accent" : "bg-surface-2"}`} />
                      ))}
                    </div>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-[12px] text-fg-subtle">Lit hours are billed. An illustration of each billing model, not a measurement.</p>
            </div>
          </div>
        </section>

        <section aria-labelledby="flow-heading" className="border-t border-border bg-surface">
          <div className="mx-auto max-w-[1180px] px-4 py-20 sm:px-6">
            <h2 id="flow-heading" className={H2}>
              How it fits together
            </h2>
            <ol className="mt-12 grid items-stretch gap-3 md:grid-cols-[1fr_auto_1.2fr_auto_1fr]">
              {FLOW.map((f, i) => (
                <li key={f.title} className="contents">
                  {i > 0 && (
                    <ArrowRight className="size-5 rotate-90 self-center justify-self-center text-fg-subtle md:rotate-0" aria-hidden />
                  )}
                  <div
                    className={`rounded-xl border p-5 ${f.title === "ShipCube" ? "border-accent bg-accent-soft" : "border-border bg-bg"}`}
                  >
                    <h3 className="text-[15px] font-semibold">{f.title}</h3>
                    <ul className="mt-3 flex flex-col gap-1.5 text-[14px] text-fg-muted">
                      {f.items.map((it) => (
                        <li key={it}>{it}</li>
                      ))}
                    </ul>
                  </div>
                </li>
              ))}
            </ol>
            <p className="mt-6 max-w-[64ch] text-[15px] leading-6 text-fg-muted">
              Start on ShipCube&apos;s GCP project with nothing to set up. Link your own project with one script, and apps bill
              to your account.
            </p>
            <div className="mt-10 max-w-[760px]">
              <CodeBlock
                label="Connect your agent, in a terminal"
                code={`claude mcp add --transport http shipcube ${c.mcpUrl} \\\n  --header "Authorization: Bearer $SHIPCUBE_TOKEN"`}
              />
            </div>
          </div>
        </section>

        <section id="security" aria-labelledby="security-heading" className="border-t border-border">
          <div className="mx-auto grid max-w-[1180px] gap-10 px-4 py-20 sm:px-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
            <div>
              <h2 id="security-heading" className={H2}>
                Built to be handed to an agent
              </h2>
              <p className="mt-4 max-w-[44ch] text-[15px] leading-6 text-fg-muted">
                An agent with deploy rights is only useful if you can trust what it can reach. ShipCube draws those lines for you.
              </p>
            </div>
            <ul className="grid gap-6 sm:grid-cols-2">
              {SECURITY.map(({ icon: Icon, text }) => (
                <li key={text} className="flex gap-3">
                  <Icon className="mt-0.5 size-5 shrink-0 text-accent" aria-hidden />
                  <p className="text-[15px] leading-6">{text}</p>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section id="request-access" aria-labelledby="request-heading" className="landing-hero border-t border-border">
          <div className="mx-auto grid max-w-[1180px] gap-10 px-4 py-20 sm:px-6 md:grid-cols-[minmax(0,1fr)_minmax(0,460px)] md:items-start">
            <div>
              <h2 id="request-heading" className="font-display text-[40px] leading-[1.02] font-extrabold tracking-[-0.03em] sm:text-[56px]">
                Ship your first app this week.
              </h2>
              <p className="mt-4 max-w-[46ch] text-[16px] leading-[26px] text-fg-muted">
                ShipCube is in early access. Tell us what you&apos;ll build. A person reviews every request, and once yours is
                approved, you sign in with the same Google account and your workspace is there.
              </p>
            </div>
            <div className="rounded-xl border border-border bg-surface p-5 sm:p-6">
              {formElsewhere ? (
                <div className="flex flex-col items-start gap-3">
                  <p className="text-fg-muted">The request form opens on ShipCube&apos;s console.</p>
                  <ButtonLink href={formElsewhere} variant="primary" className="h-11 px-5 text-[15px]">
                    Request access
                  </ButtonLink>
                </div>
              ) : (
                <RequestAccessForm apiBase={c.publicApiUrl} accountEmail={session?.user.email} />
              )}
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-border">
        <div className="mx-auto flex max-w-[1180px] flex-wrap items-center justify-between gap-3 px-4 py-8 text-[13px] text-fg-muted sm:px-6">
          <Wordmark />
          <span>Built for the Meta Global AI Developer Hackathon.</span>
          <Link href="/sign-in" className="hover:text-fg">
            Sign in
          </Link>
        </div>
      </footer>
    </div>
  );
}
