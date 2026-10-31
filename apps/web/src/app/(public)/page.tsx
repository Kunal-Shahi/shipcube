import type { Metadata } from "next";
import { Bricolage_Grotesque } from "next/font/google";
import { headers } from "next/headers";
import { Building2, KeyRound, ShieldCheck, UserCheck } from "lucide-react";
import Link from "next/link";
import { CodeBlock } from "@/components/copy-button";
import { Wordmark } from "@/components/logo";
import { RequestAccessForm } from "@/components/request-access-form";
import { ButtonLink } from "@/components/ui";
import { auth } from "@/lib/auth";
import { config } from "@/lib/server/config";
import { ComputeDay } from "./compute-day";
import { ControlPlane } from "./control-plane";
import { FlowDiagram } from "./flow-diagram";
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

const SECURITY = [
  { icon: Building2, text: "Every workspace builds and runs apps under its own service accounts, isolated from every other workspace." },
  { icon: ShieldCheck, text: "Apps are private by default, behind Google sign-in. Only the people and groups you share with can open them." },
  { icon: KeyRound, text: "Tokens are stored as hashes, and provider keys go to Secret Manager. Neither is ever shown again." },
  { icon: UserCheck, text: "A red team reviewed tokens, tenancy and the deploy path, and its findings were fixed before launch." },
];

// One scale for every section: the same padding, heading, intro and gap to the content.
const SECTION = "mx-auto max-w-[1180px] px-4 py-20 sm:px-6 lg:py-28";
const H2 = "font-display text-[34px] leading-[1.05] font-extrabold tracking-[-0.025em] text-balance sm:text-[44px]";
const INTRO = "mt-5 max-w-[56ch] text-[17px] leading-[28px] text-fg-muted";

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
      <a href="#main" className="sr-only-focusable rounded-md bg-cta px-3 py-1.5 font-medium text-cta-fg">
        Skip to content
      </a>

      <div className="landing-hero tone-blue">
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
              <ButtonLink href="/apps" size="sm" className="font-semibold">
                Open console
              </ButtonLink>
            ) : (
              <Link href="/sign-in" className="rounded-md px-2 py-1 text-fg hover:text-accent">
                Sign in
              </Link>
            )}
          </nav>
        </header>

        <section className="mx-auto max-w-[1180px] px-4 pt-8 pb-16 sm:px-6 lg:pt-12 lg:pb-24">
          <HeroStage>
            <h1 className="font-display text-[44px] leading-[0.96] font-extrabold tracking-[-0.03em] text-balance sm:text-[64px] lg:text-[80px]">
              Your agent builds it. The cloud runs it.
            </h1>
            <p className="mt-6 max-w-[52ch] text-[17px] leading-[28px] text-fg-muted">
              ShipCube is one control plane for AI-built apps, jobs and data on Google Cloud. Ask in Muse, Claude Code or Cursor;
              it estimates, deploys, schedules and caps the work. No heavy laptop, and anything risky waits for your yes.
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-x-5 gap-y-3">
              <ButtonLink href="#request-access" variant="primary" className="h-11 px-5 text-[15px] font-semibold">
                Request access
              </ButtonLink>
              <Link href="/sign-in" className="text-fg-muted underline-offset-4 hover:text-fg hover:underline">
                Approved already? Sign in
              </Link>
            </div>
          </HeroStage>
        </section>
      </div>

      <main id="main">
        <section id="how-it-works" aria-labelledby="plane-heading">
          <div className={SECTION}>
            <h2 id="plane-heading" className={`max-w-[20ch] ${H2}`}>
              One control plane for everything your agent builds
            </h2>
            <p className={INTRO}>Apps, heavy jobs, data and AI spend, in one workspace your agent can drive and you can oversee.</p>
            <div className="mt-12 lg:mt-16">
              <ControlPlane />
            </div>
          </div>
        </section>

        <section aria-labelledby="compute-heading" className="tone-ink">
          <div className={SECTION}>
            <div className="grid gap-6 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] lg:items-end lg:gap-16">
              <h2 id="compute-heading" className={H2}>
                Your laptop isn&apos;t a server
              </h2>
              <p className={`${INTRO} lg:mt-0`}>
                Most heavy work runs once a day or once a week. You shouldn&apos;t need an expensive machine for it, or a server
                that runs all month. ShipCube puts each workload where it costs least and stops billing when it&apos;s done.
              </p>
            </div>
            <div className="mt-12 lg:mt-16">
              <ComputeDay />
            </div>
          </div>
        </section>

        <section aria-labelledby="flow-heading" className="bg-surface">
          <div className={SECTION}>
            <h2 id="flow-heading" className={H2}>
              How it fits together
            </h2>
            <p className={INTRO}>
              Your agent talks to ShipCube, and ShipCube runs the work on Google Cloud. You only hear about the steps that need
              you.
            </p>
            <div className="mt-12 lg:mt-16">
              <FlowDiagram />
            </div>
            <div className="mt-12 grid gap-6 lg:mt-16 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] lg:items-center lg:gap-16">
              <div>
                <h3 className="font-display text-[22px] font-semibold">Connect in one command</h3>
                <p className="mt-2 text-[15px] leading-6 text-fg-muted">
                  Start on ShipCube&apos;s GCP project with nothing to set up. Link your own project with one script, and apps
                  bill to your account.
                </p>
              </div>
              <CodeBlock
                label="In a terminal"
                code={`claude mcp add --transport http shipcube ${c.mcpUrl} \\\n  --header "Authorization: Bearer $SHIPCUBE_TOKEN"`}
              />
            </div>
          </div>
        </section>

        <section id="security" aria-labelledby="security-heading">
          <div className={`${SECTION} grid gap-12 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] lg:gap-16`}>
            <div>
              <h2 id="security-heading" className={H2}>
                Built to be handed to an agent
              </h2>
              <p className={INTRO}>
                An agent with deploy rights is only useful if you can trust what it can reach. ShipCube draws those lines for you.
              </p>
            </div>
            <ul className="grid gap-x-10 gap-y-8 sm:grid-cols-2 lg:pt-2">
              {SECURITY.map(({ icon: Icon, text }) => (
                <li key={text}>
                  <Icon className="size-5 text-accent" aria-hidden />
                  <p className="mt-3 text-[15px] leading-6">{text}</p>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section id="request-access" aria-labelledby="request-heading" className="landing-hero tone-blue">
          <div className={`${SECTION} grid gap-12 md:grid-cols-[minmax(0,1fr)_minmax(0,440px)] md:items-start lg:gap-16`}>
            <div>
              <h2 id="request-heading" className="font-display text-[40px] leading-[1] font-extrabold tracking-[-0.03em] text-balance sm:text-[56px]">
                Ship your first app this week.
              </h2>
              <p className={INTRO}>
                ShipCube is in early access. Tell us what you&apos;ll build. A person reviews every request, and once yours is
                approved, you sign in with the same Google account and your workspace is there.
              </p>
            </div>
            <div className="rounded-xl bg-surface p-6 sm:p-8">
              {formElsewhere ? (
                <div className="flex flex-col items-start gap-3">
                  <p className="text-fg-muted">The request form opens on ShipCube&apos;s console.</p>
                  <ButtonLink href={formElsewhere} variant="primary" className="h-11 px-5 text-[15px] font-semibold">
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
