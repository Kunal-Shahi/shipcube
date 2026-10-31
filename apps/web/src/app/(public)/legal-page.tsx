import Link from "next/link";
import { Wordmark } from "@/components/logo";
import { TERMS_VERSION } from "@/lib/legal";
import { config } from "@/lib/server/config";

/** The frame for /terms and /privacy: the landing page's dark theme, a narrow column of plain text. */
export function LegalPage({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="landing min-h-dvh">
      <header className="mx-auto flex h-16 max-w-[720px] items-center justify-between gap-4 px-4 sm:px-6">
        <Link href="/" aria-label="ShipCube home">
          <Wordmark />
        </Link>
        <Link href="/sign-in" className="rounded-md px-2 py-1 text-fg hover:text-accent">
          Sign in
        </Link>
      </header>
      <main id="main" className="mx-auto max-w-[720px] px-4 pt-8 pb-20 sm:px-6">
        <h1 className="text-[32px] leading-[1.15] font-semibold tracking-[-0.02em]">{title}</h1>
        <p className="mt-2 text-[13px] text-fg-subtle">Version {TERMS_VERSION}</p>
        <div className="legal mt-8">{children}</div>
      </main>
      <LegalFooter />
    </div>
  );
}

export function LegalFooter() {
  return (
    <footer className="border-t border-border">
      <nav
        aria-label="Legal"
        className="mx-auto flex max-w-[720px] flex-wrap gap-x-5 gap-y-2 px-4 py-6 text-[13px] text-fg-muted sm:px-6"
      >
        <Link href="/" className="hover:text-fg">
          Home
        </Link>
        <Link href="/terms" className="hover:text-fg">
          Terms
        </Link>
        <Link href="/privacy" className="hover:text-fg">
          Privacy
        </Link>
      </nav>
    </footer>
  );
}

/** Something Kunal must fill in before relying on the page. Visible on purpose. */
export function Fill({ children }: { children: React.ReactNode }) {
  return <span className="rounded bg-warning-soft px-1 text-warning">[{children}]</span>;
}

/** The contact address from SHIPCUBE_CONTACT_EMAIL, or a visible placeholder until it's set. */
export function Contact() {
  const email = config().contactEmail;
  return email ? (
    <a href={`mailto:${email}`} className="text-accent hover:underline">
      {email}
    </a>
  ) : (
    <Fill>contact email: set SHIPCUBE_CONTACT_EMAIL</Fill>
  );
}
