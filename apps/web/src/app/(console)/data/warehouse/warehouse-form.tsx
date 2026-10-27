"use client";

import { CheckCircle2, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useAction } from "@/components/use-action";
import type { WarehouseInput } from "@/lib/server/data/source";
import { Button, DescList, Field, Input, Select } from "@/components/ui";
import { cn } from "@/lib/cn";
import { date } from "@/lib/format";
import type { Warehouse, WarehouseKind } from "@/lib/types";
import { connectWarehouseAction } from "../../actions";

const KINDS: { id: WarehouseKind; label: string }[] = [
  { id: "bigquery", label: "BigQuery" },
  { id: "postgres", label: "Postgres" },
  { id: "clickhouse", label: "ClickHouse" },
];

const ACCESS_HELP: Record<WarehouseKind, string> = {
  bigquery: "Give the service account BigQuery Data Viewer on the datasets and BigQuery Job User on the project. Nothing else.",
  postgres: "Use a role with SELECT on the schemas you model and no write privileges.",
  clickhouse: "Use a user with readonly = 1 and SELECT on the databases you model.",
};

export function WarehouseForm({ initial }: { initial: Warehouse }) {
  const [editing, setEditing] = useState(!initial.connected);
  const [kind, setKind] = useState<WarehouseKind>(initial.kind ?? "bigquery");
  const [fields, setFields] = useState<Record<string, string>>({});
  const [test, setTest] = useState<"idle" | "testing" | "ok" | "error">("idle");
  const [found, setFound] = useState<{ tables: number; schemas: number } | null>(null);
  const [saved, setSaved] = useState(false);
  const connect = useAction(connectWarehouseAction);
  const router = useRouter();
  const error = test === "error" ? connect.message : null;

  function input(): WarehouseInput {
    if (kind === "bigquery") {
      return { kind, project: fields.project ?? "", location: fields.location ?? "US", serviceAccountKey: fields.key ?? "" };
    }
    return {
      kind,
      host: fields.host ?? "",
      port: Number(fields.port ?? (kind === "postgres" ? 5432 : 8443)),
      database: fields.db ?? "",
      user: fields.user ?? "",
      password: fields.pass ?? "",
      ssl: fields.ssl ?? "require",
    };
  }

  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => {
    setFields({ ...fields, [k]: e.target.value });
    setTest("idle");
  };

  async function runTest() {
    setTest("testing");
    const r = await connect.run(input(), false);
    if (r) {
      setFound({ tables: r.tables, schemas: r.schemas });
      setTest("ok");
    } else {
      setTest("error");
    }
  }

  if (!editing) {
    return (
      <div className="flex flex-col gap-4 p-4">
        <div className="flex items-center gap-2">
          <CheckCircle2 className="size-4 text-good" aria-hidden />
          <span className="font-medium">{initial.name}</span>
          <span className="text-fg-muted">connected {initial.connectedAt && date(initial.connectedAt)} by {initial.connectedBy}</span>
        </div>
        <DescList items={(initial.details ?? []).map((d) => ({ label: d.label, value: <span className={d.label === "Service account" ? "font-mono text-[12px] break-all" : undefined}>{d.value}</span> }))} />
        <div className="flex gap-2">
          <Button onClick={() => setEditing(true)}>Replace credentials</Button>
          <Button variant="ghost">Test connection</Button>
        </div>
      </div>
    );
  }

  if (saved) {
    return (
      <div role="status" className="flex items-start gap-2 p-4">
        <CheckCircle2 className="mt-0.5 size-4 text-good" aria-hidden />
        <div>
          <p className="font-medium">Warehouse saved</p>
          <p className="text-fg-muted">Agents can now read tables with data_list_cubes once cubes exist. Ask one to propose them.</p>
        </div>
      </div>
    );
  }

  return (
    <form
      className="flex flex-col gap-4 p-4"
      onSubmit={async (e) => {
        e.preventDefault();
        if (test !== "ok") return;
        const r = await connect.run(input(), true);
        if (r) {
          setSaved(true);
          router.refresh();
        } else {
          setTest("error");
        }
      }}
      autoComplete="off"
    >
      <div className="inline-flex w-fit rounded-md border border-border bg-surface-2 p-0.5" role="radiogroup" aria-label="Warehouse type">
        {KINDS.map((k) => (
          <button
            key={k.id}
            type="button"
            role="radio"
            aria-checked={kind === k.id}
            onClick={() => {
              setKind(k.id);
              setTest("idle");
              connect.clearError();
            }}
            className={cn("h-7 rounded px-3 text-[12px]", kind === k.id ? "bg-surface font-medium text-fg shadow-sm" : "text-fg-muted hover:text-fg")}
          >
            {k.label}
          </button>
        ))}
      </div>

      {kind === "bigquery" ? (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Billing project" htmlFor="wh-project" help="Queries run and bill here.">
            <Input id="wh-project" value={fields.project ?? ""} onChange={set("project")} placeholder="acme-analytics-prod" className="font-mono text-[12.5px]" />
          </Field>
          <Field label="Location" htmlFor="wh-location">
            <Select id="wh-location" value={fields.location ?? "US"} onChange={set("location")}>
              {["US", "EU", "southamerica-east1", "us-central1"].map((l) => <option key={l}>{l}</option>)}
            </Select>
          </Field>
          <div className="sm:col-span-2">
            <Field label="Service account key (JSON)" htmlFor="wh-key" error={error} help="Stored in Secret Manager. ShipCube never shows it again, and agents never see it.">
              <textarea
                id="wh-key"
                rows={5}
                value={fields.key ?? ""}
                onChange={set("key")}
                spellCheck={false}
                placeholder='{ "type": "service_account", "project_id": "…", … }'
                className={cn(
                  "w-full resize-y rounded-md border border-border-strong bg-surface px-2.5 py-2 font-mono text-[12px] placeholder:text-fg-subtle focus:border-accent focus:ring-2 focus:ring-accent/25 focus:outline-none",
                  error && "border-critical",
                )}
              />
            </Field>
          </div>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_120px]">
          <Field label="Host" htmlFor="wh-host">
            <Input id="wh-host" value={fields.host ?? ""} onChange={set("host")} placeholder="10.20.0.5" className="font-mono text-[12.5px]" />
          </Field>
          <Field label="Port" htmlFor="wh-port">
            <Input id="wh-port" value={fields.port ?? (kind === "postgres" ? "5432" : "8443")} onChange={set("port")} className="num" inputMode="numeric" />
          </Field>
          <Field label="Database" htmlFor="wh-db">
            <Input id="wh-db" value={fields.db ?? ""} onChange={set("db")} placeholder="olist" />
          </Field>
          <Field label="SSL" htmlFor="wh-ssl">
            <Select id="wh-ssl" value={fields.ssl ?? "require"} onChange={set("ssl")}>
              <option value="require">Require</option>
              <option value="verify-full">Verify CA</option>
            </Select>
          </Field>
          <Field label="User" htmlFor="wh-user" error={error}>
            <Input id="wh-user" value={fields.user ?? ""} onChange={set("user")} placeholder="shipcube_reader" />
          </Field>
          <Field label="Password" htmlFor="wh-pass">
            <Input id="wh-pass" type="password" value={fields.pass ?? ""} onChange={set("pass")} autoComplete="new-password" />
          </Field>
        </div>
      )}

      <p className="flex items-start gap-2 text-[12px] text-fg-muted">
        <ShieldCheck className="mt-0.5 size-3.5 shrink-0" aria-hidden />
        {ACCESS_HELP[kind]} ShipCube only reads. Model migrations change the catalog, never your tables.
      </p>

      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" onClick={runTest} pending={test === "testing"}>
          {test === "testing" ? "Connecting…" : "Test connection"}
        </Button>
        <Button type="submit" variant="primary" disabled={test !== "ok"} pending={connect.pending && test === "ok"}>
          Save warehouse
        </Button>
        {initial.connected && (
          <Button type="button" variant="ghost" onClick={() => setEditing(false)}>
            Cancel
          </Button>
        )}
        {test === "ok" && (
          <span role="status" className="flex items-center gap-1.5 text-[12px] text-good">
            <CheckCircle2 className="size-3.5" aria-hidden /> Connected. Found {found?.tables} tables in {found?.schemas}{" "}
            {kind === "bigquery" ? "datasets" : "schemas"}.
          </span>
        )}
      </div>
    </form>
  );
}
