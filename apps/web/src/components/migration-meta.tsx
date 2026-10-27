import type { MigrationKind, MigrationStatus } from "@/lib/types";
import { Pill } from "./ui";

export function KindPill({ kind }: { kind: MigrationKind }) {
  if (kind === "breaking") return <Pill tone="critical">Breaking</Pill>;
  if (kind === "change") return <Pill tone="warning">Changes results</Pill>;
  return <Pill tone="good">Additive</Pill>;
}

export function MigrationStatusPill({ status }: { status: MigrationStatus }) {
  if (status === "proposed") return <Pill tone="accent">Needs review</Pill>;
  if (status === "applied") return <Pill>Applied</Pill>;
  return <Pill tone="critical">Rejected</Pill>;
}

type Op = { op: string } & Record<string, unknown>;

/** One plain-language line per op, so reviewers don't have to read JSON. */
export function describeOp(o: Op): { verb: string; target: string; detail?: string } {
  const s = (k: string) => (o[k] === undefined ? undefined : String(o[k]));
  switch (o.op) {
    case "add_dimension":
      return { verb: "Add dimension", target: s("name")!, detail: s("sql") };
    case "add_measure": {
      const agg = s("agg");
      const detail = s("sql") ?? (agg ? `${agg}${s("filter") ? ` where ${s("filter")}` : ""}` : undefined);
      return { verb: "Add measure", target: s("name")!, detail };
    }
    case "change_sql":
      return { verb: "Change definition of", target: s("member")!, detail: s("sql") };
    case "rename_member":
      return { verb: "Rename", target: s("member")!, detail: `to ${s("to")}. The old name keeps working with a warning.` };
    case "deprecate_member":
      return { verb: "Deprecate", target: s("member")!, detail: s("reason") };
    case "drop_member":
      return { verb: "Remove", target: s("member")! };
    case "add_join":
      return { verb: "Add join", target: `${s("cube")} → ${s("to")}`, detail: s("on") ?? (s("key") ? `on ${s("key")}` : undefined) };
    case "drop_join":
      return { verb: "Remove join", target: `${s("cube")} → ${s("to")}` };
    case "create_cube":
      return { verb: "Create cube", target: s("name")!, detail: s("table") ?? s("sql") };
    case "drop_cube":
      return { verb: "Remove cube", target: s("cube")! };
    default:
      return { verb: o.op.replaceAll("_", " "), target: s("name") ?? s("member") ?? s("cube") ?? "", detail: undefined };
  }
}
