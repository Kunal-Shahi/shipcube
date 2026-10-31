import type { Metadata } from "next";
import { Contact, Fill, LegalPage } from "../legal-page";

export const metadata: Metadata = { title: "Privacy notice" };
// The contact address comes from the environment at request time.
export const dynamic = "force-dynamic";

// Draft text for Kunal to review; not legal advice. The bracketed placeholders are his to fill in.
export default function PrivacyPage() {
  return (
    <LegalPage title="Privacy notice">
      <p>
        This notice explains what ShipCube collects, why, who else handles it, and how to have it deleted. ShipCube is run by{" "}
        <Fill>operator name</Fill>.
      </p>

      <h2>What we collect</h2>
      <ul>
        <li>Your email address and name, and what you tell us you&apos;ll build, when you request access.</li>
        <li>Your Google sign-in identity: your email address and Google account ID.</li>
        <li>The source code you or your agent upload to deploy an app.</li>
        <li>
          Records of what happens in your workspace: deploys, approvals, sharing and caps, so you and we can see who did
          what.
        </li>
      </ul>
      <p>We use a cookie to keep you signed in. We don&apos;t use advertising or tracking cookies.</p>

      <h2>How we use your code</h2>
      <ul>
        <li>We store it to build your app.</li>
        <li>We analyse it automatically to estimate what the app will cost to run and where it should run.</li>
        <li>We delete the uploaded source after the build.</li>
        <li>
          We keep the built app image while the app exists. When you delete an app, it can be restored for 7 days; after
          that, its image is purged.
        </li>
      </ul>

      <h2>Who else handles it</h2>
      <ul>
        <li>
          <strong>Google Cloud</strong> hosts everything ShipCube stores and runs, in the asia-south1 (Mumbai) region.
        </li>
        <li>
          <strong>Meta&apos;s Muse API</strong> helps explain cost estimates. It receives facts derived from your app, such as
          its framework, its sizes and its measured usage. It doesn&apos;t receive your source code.
        </li>
        <li>
          If your app calls AI models through ShipCube&apos;s gateway, what the app sends goes to the model provider it uses:
          one you choose with your own key, or ShipCube&apos;s default.
        </li>
      </ul>

      <h2>How long we keep it</h2>
      <p>
        We keep your request and account details while your request is open or your account exists, and app records while the
        app exists, until you ask us to delete them. Operational logs are kept for <Fill>log retention period</Fill>.
      </p>

      <h2>Deleting your data</h2>
      <p>
        You can delete your apps in the console at any time. To delete your access request, your account or everything in
        your workspace, email <Contact /> from the address you signed up with. We&apos;ll confirm when it&apos;s done.
      </p>

      <h2>Your rights</h2>
      <p>
        Depending on where you live, you may have the right to see, correct, export or delete your data, or to object to how
        we use it. Write to <Contact /> to ask. <Fill>applicable law and any regulator to name</Fill>
      </p>

      <h2>Changes</h2>
      <p>We may update this notice. The version date at the top changes when we do.</p>

      <h2>Contact</h2>
      <p>
        <Contact />
        <br />
        <Fill>operator name and postal address</Fill>
      </p>
    </LegalPage>
  );
}
