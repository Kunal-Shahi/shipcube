import type { Metadata } from "next";
import { Bricolage_Grotesque } from "next/font/google";
import { headers } from "next/headers";
import { Building2, Database, Gauge, KeyRound, ShieldCheck, Sparkles, UserCheck } from "lucide-react";
import Link from "next/link";
import { CodeBlock } from "@/components/copy-button";
import { Wordmark } from "@/components/logo";
import { RequestAccessForm } from "@/components/request-access-form";
import { ButtonLink } from "@/components/ui";
import { auth } from "@/lib/auth";
import { config } from "@/lib/server/config";

const display = Bricolage_Grotesque({ subsets: ["latin"], variable: "--font-display", weight: ["600", "800"] });

const TITLE = "ShipCube: your agent ships the app, you approve what matters";
const DESCRIPTION =
  "An MCP server for Claude Code, Cursor and other agents. Estimate, deploy, share and cap internal apps on Google Cloud, with a person's approval for anything risky.";

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

type Line = { kind: "you"; text: string } | { kind: "call"; tool: string; result: string; tone?: "wait" | "ok" };

// The hero: one request, followed through. Values are an illustration, not a measurement.
const TRANSCRIPT: Line[] = [
  { kind: "you", text: "Deploy this revenue tracker to our GCP project. Share it with finance, and keep it under $20 a month." },
  { kind: "call", tool: "estimate_load", result: "bursty, interactive: T1 serverless, scales to zero, about $7 a month" },
  { kind: "call", tool: "deploy_app", result: "first deploy to acme-prod waits for your approval", tone: "wait" },
  { kind: "call", tool: "approval", result: "in the console, with one click; the deploy goes ahead", tone: "ok" },
  { kind: "call", tool: "set_caps", result: "spend capped at $20 a month, AI tokens capped per day" },
  { kind: "call", tool: "set_access", result: "finance-team@example.com can open it; nobody else can" },
  { kind: "call", tool: "deploy_app", result: "live: private URL behind Google sign-in", tone: "ok" },
];

const STEPS = [
  { title: "Connect your agent", body: "Create a token in the console and add ShipCube to Claude Code, Cursor or any MCP client. One command." },
  { title: "Ask for what you want", body: "“Ship this and share it with finance.” The agent estimates the load, picks a tier and deploys it." },
  { title: "Approve what's risky", body: "First deploys to your project, cap raises, domain-wide sharing and deletes wait for you. Everything else just happens." },
];

const FEATURES = [
  { icon: Gauge, title: "A cost estimate before every deploy", body: "ShipCube profiles the app, classifies the workload and picks serverless, batch or always-on. The reasoning and the expected monthly cost come back before anything runs." },
  { icon: UserCheck, title: "A person approves the risky parts", body: "The agent asks; you decide in the console. It can't approve its own requests, and the approval page says exactly what changes." },
  { icon: Sparkles, title: "Spend caps on AI", body: "Deployed apps call models through ShipCube's gateway, with per-app token and spend limits. Bring your own provider key, or use the default." },
  { icon: Database, title: "A governed data layer", body: "Agents query your warehouse through named measures and dimensions, not raw SQL. They can propose model changes; a person reviews the diff and applies it." },
  { icon: Building2, title: "Your GCP project, or ours", body: "Start on ShipCube's project with nothing to set up. Link your own project with one script, and apps bill to your account." },
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

        <section className="mx-auto grid max-w-[1180px] gap-12 px-4 pt-10 pb-20 sm:px-6 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)] lg:items-center lg:pt-16 lg:pb-28">
          <div>
            <h1 className="font-display text-[44px] leading-[0.98] font-extrabold tracking-[-0.025em] text-balance sm:text-[64px] lg:text-[76px]">
              Your agent ships the app. You approve what matters.
            </h1>
            <p className="mt-6 max-w-[54ch] text-[16px] leading-[26px] text-fg-muted sm:text-[17px] sm:leading-[28px]">
              ShipCube is an MCP server for Claude Code, Cursor and other agents. They estimate, deploy, share and cap internal
              apps on Google Cloud from the conversation you&apos;re already in. Anything risky waits for your yes.
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-4">
              <ButtonLink href="#request-access" variant="primary" className="h-11 px-5 text-[15px]">
                Request access
              </ButtonLink>
              <Link href="/sign-in" className="text-fg-muted underline-offset-4 hover:text-fg hover:underline">
                Approved already? Sign in
              </Link>
            </div>
            <p className="mt-6 text-[13px] text-fg-subtle">Early access. A person reviews every request.</p>
          </div>

          <Transcript />
        </section>
      </div>

      <main id="main">
        <section id="how-it-works" aria-labelledby="how-heading" className="border-t border-border">
          <div className="mx-auto max-w-[1180px] px-4 py-20 sm:px-6">
            <h2 id="how-heading" className={H2}>
              Three steps, then it&apos;s just a conversation
            </h2>
            <ol className="mt-12 grid gap-10 md:grid-cols-3 md:gap-8">
              {STEPS.map((s, i) => (
                <li key={s.title}>
                  <span className="num font-display text-[56px] leading-none font-extrabold text-accent" aria-hidden>
                    {i + 1}
                  </span>
                  <h3 className="mt-3 text-[17px] font-semibold">{s.title}</h3>
                  <p className="mt-1.5 max-w-[40ch] text-[15px] leading-6 text-fg-muted">{s.body}</p>
                </li>
              ))}
            </ol>
            <div className="mt-12 max-w-[760px]">
              <CodeBlock
                label="Step 1, in a terminal"
                code={`claude mcp add --transport http shipcube ${c.mcpUrl} \\\n  --header "Authorization: Bearer $SHIPCUBE_TOKEN"`}
              />
            </div>
          </div>
        </section>

        <section aria-labelledby="features-heading" className="border-t border-border bg-surface">
          <div className="mx-auto max-w-[1180px] px-4 py-20 sm:px-6">
            <h2 id="features-heading" className={`max-w-[20ch] ${H2}`}>
              What your agent gets, and where it stops
            </h2>
            <ul className="mt-12 grid gap-px overflow-hidden rounded-xl border border-border bg-border md:grid-cols-6">
              {FEATURES.map(({ icon: Icon, title, body }, i) => (
                <li key={title} className={`bg-surface p-6 sm:p-8 ${i < 2 ? "md:col-span-3" : "md:col-span-2"}`}>
                  <Icon className="size-5 text-accent" aria-hidden />
                  <h3 className="mt-4 text-[17px] font-semibold">{title}</h3>
                  <p className="mt-2 text-[15px] leading-6 text-fg-muted">{body}</p>
                </li>
              ))}
            </ul>
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
          <nav aria-label="Legal" className="flex gap-4">
            <Link href="/terms" className="hover:text-fg">
              Terms
            </Link>
            <Link href="/privacy" className="hover:text-fg">
              Privacy
            </Link>
            <Link href="/sign-in" className="hover:text-fg">
              Sign in
            </Link>
          </nav>
        </div>
      </footer>
    </div>
  );
}

/** The hero's moment: a request followed through to a live app, with the one step that waited for a person. */
function Transcript() {
  return (
    <figure className="relative overflow-hidden rounded-xl border border-border-strong bg-[#0a0f24]/90 shadow-[0_30px_80px_-30px_rgb(123_140_255/0.45)]">
      <figcaption className="flex items-center gap-2 border-b border-border px-4 py-2.5 text-[12px] text-fg-subtle">
        <span className="flex gap-1.5" aria-hidden>
          <span className="size-2.5 rounded-full bg-border-strong" />
          <span className="size-2.5 rounded-full bg-border-strong" />
          <span className="size-2.5 rounded-full bg-border-strong" />
        </span>
        <span className="ml-2">Claude Code, with ShipCube connected</span>
      </figcaption>
      <ol className="flex flex-col gap-2.5 p-4 font-mono text-[12.5px] leading-[20px] sm:p-5" aria-label="Example: a deploy request followed through">
        {TRANSCRIPT.map((l, i) => (
          <li key={i} className="term-line" style={{ "--i": i } as React.CSSProperties}>
            {l.kind === "you" ? (
              <p className="font-sans text-[14px] leading-[22px] text-fg">
                <span className="mr-2 text-accent" aria-hidden>
                  &gt;
                </span>
                {l.text}
              </p>
            ) : (
              <p className="flex flex-wrap gap-x-2">
                <span className={l.tone === "wait" ? "text-[var(--approve)]" : l.tone === "ok" ? "text-good" : "text-accent"}>
                  {l.tool === "approval" ? "you approved" : `shipcube.${l.tool}`}
                </span>
                <span className="text-fg-muted">
                  {l.tone === "wait" && (
                    <span
                      className="term-wait-dot mr-1.5 inline-block size-2 rounded-full bg-[var(--approve)] align-middle"
                      style={{ "--i": i } as React.CSSProperties}
                      aria-hidden
                    />
                  )}
                  {l.result}
                </span>
              </p>
            )}
          </li>
        ))}
        <li className="term-line" style={{ "--i": TRANSCRIPT.length } as React.CSSProperties} aria-hidden>
          <span className="text-accent">&gt;</span> <span className="term-caret inline-block h-4 w-2 translate-y-0.5 bg-fg-muted" />
        </li>
      </ol>
    </figure>
  );
}
