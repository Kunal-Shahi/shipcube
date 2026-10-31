import type { Metadata } from "next";
import Link from "next/link";
import { Contact, Fill, LegalPage } from "../legal-page";

export const metadata: Metadata = { title: "Terms of use" };
// The contact address comes from the environment at request time.
export const dynamic = "force-dynamic";

// Draft text for Kunal to review; not legal advice. The bracketed placeholders are his to fill in.
export default function TermsPage() {
  return (
    <LegalPage title="Terms of use">
      <p>
        These terms apply when you use ShipCube: the website, the console, the API and the MCP server. ShipCube is run by{" "}
        <Fill>operator name</Fill>. By requesting access or using ShipCube, you agree to them.
      </p>

      <h2>Early access</h2>
      <p>
        ShipCube is in early access. It&apos;s offered as is, without guarantees of any kind: it may change, break, lose data
        or stop at any time. Don&apos;t rely on it for anything you can&apos;t afford to lose.
      </p>

      <h2>Your apps and code</h2>
      <p>
        You&apos;re responsible for the apps you deploy and the code you upload, including code your agent writes for you,
        and for having the right to use any data they read. You keep the rights to your code.
      </p>

      <h2>Acceptable use</h2>
      <p>Don&apos;t use ShipCube for:</p>
      <ul>
        <li>mining cryptocurrency</li>
        <li>spam or bulk email</li>
        <li>scanning, probing or attacking other systems</li>
        <li>phishing, or impersonating a person or organisation</li>
        <li>illegal content, or content that infringes someone else&apos;s rights</li>
        <li>abusing, working around or evading spend and usage caps</li>
      </ul>

      <h2>Suspension and deletion</h2>
      <p>
        If an app or workspace breaks these terms, or puts ShipCube or other people at risk, we may suspend or delete it, with
        or without notice. We may also end early access for anyone, at any time.
      </p>

      <h2>Your data</h2>
      <p>
        The <Link href="/privacy">Privacy notice</Link> says what we collect, how we use it, and how to have it deleted.
      </p>

      <h2>Changes</h2>
      <p>
        We may update these terms. The version date at the top changes when we do.
      </p>

      <h2>Governing law</h2>
      <p>
        <Fill>governing law and jurisdiction</Fill>
      </p>

      <h2>Contact</h2>
      <p>
        Questions about these terms: <Contact />.
      </p>
    </LegalPage>
  );
}
