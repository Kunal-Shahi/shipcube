"use client";

import { Check, Cloud, ExternalLink, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/cn";
import type { GcpLink } from "@/lib/types";
import { Button, Field, Select } from "./ui";

// Projects the signed-in Google account can see. A real adapter lists them with the
// Resource Manager API after the user grants access; these are mock values.
const MOCK_PROJECTS = [
  { id: "acme-analytics-prod", name: "Acme Analytics Prod", billing: "01A2B3-C4D5E6-F7G8H9" },
  { id: "acme-sandbox-2291", name: "Acme Sandbox", billing: "01A2B3-C4D5E6-F7G8H9" },
  { id: "olist-research", name: "Olist research", billing: null },
];

const REGIONS = ["us-central1", "us-east1", "southamerica-east1", "europe-west1", "asia-south1"];

const ROLES = [
  { role: "Cloud Run Admin", why: "Create and update your app services and jobs" },
  { role: "Artifact Registry Writer", why: "Store the container images ShipCube builds" },
  { role: "Cloud Scheduler Admin", why: "Run T0 refreshes and T2 batch jobs on a schedule" },
  { role: "Monitoring Viewer", why: "Read latency, errors and cost for the console" },
];

type Choice = "shipcube" | "own";
type Step = "choose" | "authorize" | "pick" | "linking" | "done";

export function GcpLinker({
  initial,
  onDone,
  doneLabel = "Continue",
  creditsLabel = "$300 of ShipCube credits",
}: {
  initial?: GcpLink;
  onDone?: (link: GcpLink | null) => void;
  doneLabel?: string;
  creditsLabel?: string;
}) {
  const [choice, setChoice] = useState<Choice>(initial?.linked ? "own" : "shipcube");
  const [step, setStep] = useState<Step>(initial?.linked ? "done" : "choose");
  const [project, setProject] = useState(initial?.projectId ?? MOCK_PROJECTS[0].id);
  const [region, setRegion] = useState(initial?.region ?? "us-central1");
  const [authorizing, setAuthorizing] = useState(false);

  const selected = MOCK_PROJECTS.find((p) => p.id === project);
  const noBilling = selected && !selected.billing;

  function authorize() {
    setAuthorizing(true);
    setTimeout(() => {
      setAuthorizing(false);
      setStep("pick");
    }, 700);
  }

  function link() {
    setStep("linking");
    setTimeout(() => setStep("done"), 900);
  }

  const linked: GcpLink = {
    linked: true,
    projectId: project,
    region,
    billingAccount: selected?.billing ?? undefined,
    serviceAccount: `shipcube-deployer@${project}.iam.gserviceaccount.com`,
  };

  return (
    <div className="flex flex-col gap-4">
      <fieldset className="grid gap-2 sm:grid-cols-2" disabled={step !== "choose" && step !== "done"}>
        <legend className="sr-only">Where should your apps run?</legend>
        <ChoiceCard
          checked={choice === "shipcube"}
          onSelect={() => {
            setChoice("shipcube");
            setStep("choose");
          }}
          title="Use ShipCube's GCP project"
          body={`Apps run in ShipCube's project and draw from ${creditsLabel}. Nothing to set up.`}
        />
        <ChoiceCard
          checked={choice === "own"}
          onSelect={() => {
            setChoice("own");
            if (step === "done" && !initial?.linked) setStep("choose");
          }}
          title="Link your own GCP project"
          body="Apps run in your project and bill to your Google Cloud account, so your own credits and commitments apply."
        />
      </fieldset>

      {choice === "own" && step === "choose" && (
        <div className="rounded-lg border border-border bg-surface p-4">
          <p className="max-w-[62ch] text-fg-muted">
            Google will ask you to let ShipCube see your projects. You choose the project next, and ShipCube only gets the
            roles listed there.
          </p>
          <Button variant="secondary" className="mt-3" onClick={authorize} pending={authorizing}>
            <Cloud className="size-3.5" aria-hidden />
            {authorizing ? "Waiting for Google…" : "Connect Google Cloud"}
          </Button>
        </div>
      )}

      {choice === "own" && (step === "pick" || step === "linking") && (
        <div className="flex flex-col gap-4 rounded-lg border border-border bg-surface p-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field
              label="Project"
              htmlFor="gcp-project"
              error={noBilling ? "This project has no billing account. Attach one in the Google Cloud console, then pick it again." : null}
              help={selected?.billing ? `Billing account ${selected.billing}` : undefined}
            >
              <Select id="gcp-project" value={project} onChange={(e) => setProject(e.target.value)}>
                {MOCK_PROJECTS.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} ({p.id})
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Default region" htmlFor="gcp-region" help="New apps deploy here. You can change it per app.">
              <Select id="gcp-region" value={region} onChange={(e) => setRegion(e.target.value)}>
                {REGIONS.map((r) => (
                  <option key={r}>{r}</option>
                ))}
              </Select>
            </Field>
          </div>

          <div>
            <h3 className="flex items-center gap-1.5 text-[12px] font-medium">
              <ShieldCheck className="size-3.5 text-fg-muted" aria-hidden />
              ShipCube gets these roles in <span className="font-mono">{project}</span>, and nothing else
            </h3>
            <ul className="mt-2 divide-y divide-border rounded-md border border-border">
              {ROLES.map((r) => (
                <li key={r.role} className="flex flex-wrap justify-between gap-x-4 gap-y-0.5 px-3 py-2">
                  <span className="font-medium">{r.role}</span>
                  <span className="text-fg-muted">{r.why}</span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-[12px] text-fg-muted">
              You can remove access at any time from IAM in the Google Cloud console, or unlink the project here.
            </p>
          </div>

          <div className="flex gap-2">
            <Button variant="primary" onClick={link} pending={step === "linking"} disabled={!!noBilling}>
              {step === "linking" ? "Linking project…" : "Link project"}
            </Button>
            <Button variant="ghost" onClick={() => setStep("choose")} disabled={step === "linking"}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {choice === "own" && step === "done" && (
        <div className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-border bg-surface p-4">
          <div className="flex gap-2.5">
            <Check className="mt-0.5 size-4 shrink-0 text-good" aria-hidden />
            <div className="min-w-0">
              <p className="font-medium">
                Linked <span className="font-mono">{linked.projectId}</span>
              </p>
              <p className="mt-0.5 break-all text-fg-muted">
                Deploying as <span className="font-mono text-[12px]">{linked.serviceAccount}</span> in {linked.region}
              </p>
            </div>
          </div>
          <a
            className="inline-flex items-center gap-1 text-accent hover:underline"
            href={`https://console.cloud.google.com/iam-admin/iam?project=${linked.projectId}`}
            target="_blank"
            rel="noreferrer"
          >
            View in Google Cloud <ExternalLink className="size-3" aria-hidden />
          </a>
        </div>
      )}

      {onDone && (
        <div className="flex items-center gap-3 pt-2">
          <Button
            variant="primary"
            disabled={choice === "own" && step !== "done"}
            onClick={() => onDone(choice === "own" ? linked : null)}
          >
            {doneLabel}
          </Button>
          {choice === "own" && step !== "done" && (
            <span className="text-[12px] text-fg-muted">Link a project first, or use ShipCube&apos;s.</span>
          )}
        </div>
      )}
    </div>
  );
}

function ChoiceCard({
  checked,
  onSelect,
  title,
  body,
}: {
  checked: boolean;
  onSelect: () => void;
  title: string;
  body: string;
}) {
  return (
    <label
      className={cn(
        "flex cursor-pointer gap-3 rounded-lg border bg-surface p-3.5 has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-accent",
        checked ? "border-accent" : "border-border hover:border-border-strong",
      )}
    >
      <input type="radio" name="gcp-choice" className="sr-only" checked={checked} onChange={onSelect} />
      <span
        className={cn(
          "mt-0.5 grid size-4 shrink-0 place-items-center rounded-full border",
          checked ? "border-accent" : "border-border-strong",
        )}
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
