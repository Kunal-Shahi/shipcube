"use client";

import { Check, ExternalLink, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { linkGcpProjectAction } from "@/app/(console)/actions";
import { cn } from "@/lib/cn";
import type { GcpLink } from "@/lib/types";
import { ActionErrorText } from "./action-error";
import { CodeBlock } from "./copy-button";
import { Button, Field, Input, Select } from "./ui";
import { useAction } from "./use-action";

// Linking follows security's design (docs/research/security-tenancy.md §3): the project owner
// runs a script that grants this workspace's deployer service account a fixed set of roles.
// ShipCube never holds an OAuth token for the user's cloud. The API then verifies the grants
// with testIamPermissions.

const SCRIPT_URL = "https://raw.githubusercontent.com/Kunal-Shahi/shipcube/main/infra/scripts/link-project.sh";
const REGIONS = ["asia-south1", "us-central1", "us-east1", "europe-west1", "southamerica-east1"];
const PROJECT_ID = /^[a-z][a-z0-9-]{4,28}[a-z0-9]$/;

const GRANTS = [
  { role: "Cloud Run Developer", why: "Deploy and update your app services and jobs" },
  { role: "Run IAM (2 permissions)", why: "Let IAP call the app, so only people you allow can open it" },
  { role: "IAP Admin", why: "Manage who can open each app" },
  { role: "Service Account User, on 2 ShipCube accounts only", why: "Run apps and builds as identities with no project roles" },
  { role: "Artifact Registry Writer, on the shipcube repo only", why: "Store the images ShipCube builds" },
  { role: "Cloud Build Editor", why: "Build from source, billed to your project" },
  { role: "Cloud Scheduler Admin", why: "Run scheduled jobs (T0 refreshes, T2 batch)" },
  { role: "Logging and Monitoring Viewer", why: "Show logs, latency and cost in the console" },
];

type Choice = "shipcube" | "own";

export function GcpLinker({
  initial,
  deployer,
  canLink,
  onDone,
  doneLabel = "Continue",
  creditsLabel = "ShipCube's free credits",
}: {
  initial?: GcpLink;
  /** This workspace's deployer service account, filled into the script. */
  deployer: string;
  /** False when this deployment can't link projects yet (the API endpoint is missing). */
  canLink: boolean;
  onDone?: (link: GcpLink | null) => void;
  doneLabel?: string;
  creditsLabel?: string;
}) {
  const [choice, setChoice] = useState<Choice>(initial?.linked ? "own" : "shipcube");
  const [projectId, setProjectId] = useState(initial?.projectId ?? "");
  const [region, setRegion] = useState(initial?.region ?? REGIONS[0]);
  const [link, setLink] = useState<GcpLink | null>(initial?.linked ? initial : null);
  const check = useAction(linkGcpProjectAction);

  const validId = PROJECT_ID.test(projectId);
  const script = `curl -fsSLO ${SCRIPT_URL}\nbash link-project.sh ${validId ? projectId : "<your-project-id>"} \\\n  ${deployer} ${region}`;

  return (
    <div className="flex flex-col gap-4">
      <fieldset className="grid gap-2 sm:grid-cols-2">
        <legend className="sr-only">Where should your apps run?</legend>
        <ChoiceCard
          checked={choice === "shipcube"}
          onSelect={() => setChoice("shipcube")}
          title="Use ShipCube's GCP project"
          body={`Apps run in ShipCube's project and draw from ${creditsLabel}. Nothing to set up.`}
        />
        <ChoiceCard
          checked={choice === "own"}
          onSelect={() => setChoice("own")}
          title="Link your own GCP project"
          body="Apps run in your project and bill to its billing account, so your own credits and commitments apply."
        />
      </fieldset>

      {choice === "own" && !canLink && (
        <p role="status" className="rounded-lg border border-border bg-surface p-4 text-fg-muted">
          Linking your own project isn&apos;t available in this deployment yet. Apps run in ShipCube&apos;s project until it is.
        </p>
      )}

      {choice === "own" && canLink && link && (
        <div className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-border bg-surface p-4">
          <div className="flex gap-2.5">
            <Check className="mt-0.5 size-4 shrink-0 text-good" aria-hidden />
            <div className="min-w-0">
              <p className="font-medium">
                Linked <span className="font-mono">{link.projectId}</span>
              </p>
              <p className="mt-0.5 break-all text-fg-muted">
                Deploying as <span className="font-mono text-[12px]">{link.serviceAccount ?? deployer}</span>
                {link.region && <> in {link.region}</>}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <a
              className="inline-flex items-center gap-1 text-accent hover:underline"
              href={`https://console.cloud.google.com/iam-admin/iam?project=${link.projectId}`}
              target="_blank"
              rel="noreferrer"
            >
              See the grants in Google Cloud <ExternalLink className="size-3" aria-hidden />
              <span className="sr-only">(opens in a new tab)</span>
            </a>
            <Button size="sm" variant="ghost" onClick={() => setLink(null)}>
              Link a different project
            </Button>
          </div>
        </div>
      )}

      {choice === "own" && canLink && !link && (
        <div className="flex flex-col gap-4 rounded-lg border border-border bg-surface p-4">
          <ol className="flex list-decimal flex-col gap-4 pl-5 marker:text-fg-subtle">
            <li>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Project ID" htmlFor="gcp-project" help="Shown on the Google Cloud console dashboard, for example acme-analytics-prod.">
                  <Input
                    id="gcp-project"
                    value={projectId}
                    onChange={(e) => setProjectId(e.target.value.trim().toLowerCase())}
                    placeholder="acme-analytics-prod"
                    className="font-mono text-[12.5px]"
                    autoComplete="off"
                    spellCheck={false}
                  />
                </Field>
                <Field label="Default region" htmlFor="gcp-region" help="New apps deploy here. You can change it per app.">
                  <Select id="gcp-region" value={region} onChange={(e) => setRegion(e.target.value)}>
                    {REGIONS.map((r) => (
                      <option key={r}>{r}</option>
                    ))}
                  </Select>
                </Field>
              </div>
            </li>
            <li>
              <p>
                As a project owner, run this in{" "}
                <a href="https://shell.cloud.google.com/?show=terminal" target="_blank" rel="noreferrer" className="text-accent hover:underline">
                  Cloud Shell<span className="sr-only"> (opens in a new tab)</span>
                </a>{" "}
                or any terminal where <code className="font-mono text-[12px]">gcloud</code> is signed in. Read the script first; it only
                makes the grants listed below.
              </p>
              <div className="mt-2">
                <CodeBlock label="Terminal" code={script} />
              </div>
            </li>
            <li>
              <p>Check that ShipCube can deploy. It tests each permission and changes nothing.</p>
              <div className="mt-2 flex flex-wrap items-center gap-3">
                <Button
                  variant="primary"
                  disabled={!validId}
                  pending={check.pending}
                  onClick={async () => {
                    const r = await check.run({ projectId, region });
                    if (r) setLink(r);
                  }}
                >
                  {check.pending ? "Checking permissions…" : "Check access"}
                </Button>
                <ActionErrorText error={check.error} message={check.message} />
              </div>
            </li>
          </ol>

          <div>
            <h3 className="flex items-center gap-1.5 text-[12px] font-medium">
              <ShieldCheck className="size-3.5 text-fg-muted" aria-hidden />
              What the script grants ShipCube&apos;s deployer, and nothing else
            </h3>
            <ul className="mt-2 divide-y divide-border rounded-md border border-border">
              {GRANTS.map((r) => (
                <li key={r.role} className="flex flex-wrap justify-between gap-x-4 gap-y-0.5 px-3 py-2">
                  <span className="font-medium">{r.role}</span>
                  <span className="text-fg-muted">{r.why}</span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-[12px] text-fg-muted">
              No billing permissions. Everything runs and bills inside your project. To undo it, run{" "}
              <code className="font-mono">unlink-project.sh</code> or remove the grants in IAM.
            </p>
          </div>
        </div>
      )}

      {onDone && (
        <div className="flex flex-wrap items-center gap-3 pt-2">
          <Button variant="primary" disabled={choice === "own" && canLink && !link} onClick={() => onDone(choice === "own" ? link : null)}>
            {doneLabel}
          </Button>
          {choice === "own" && canLink && !link && (
            <span className="text-[12px] text-fg-muted">Link a project first, or use ShipCube&apos;s.</span>
          )}
        </div>
      )}
    </div>
  );
}

function ChoiceCard({ checked, onSelect, title, body }: { checked: boolean; onSelect: () => void; title: string; body: string }) {
  return (
    <label
      className={cn(
        "flex cursor-pointer gap-3 rounded-lg border bg-surface p-3.5 has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-accent",
        checked ? "border-accent" : "border-border hover:border-border-strong",
      )}
    >
      <input type="radio" name="gcp-choice" className="sr-only" checked={checked} onChange={onSelect} />
      <span
        className={cn("mt-0.5 grid size-4 shrink-0 place-items-center rounded-full border", checked ? "border-accent" : "border-border-strong")}
        aria-hidden
      >
        {checked && <span className="size-2 rounded-full bg-accent" />}
      </span>
      <span>
        <span className="block font-medium">{title}</span>
        <span className="mt-0.5 block text-fg-muted">{body}</span>
      </span>
    </label>
  );
}
