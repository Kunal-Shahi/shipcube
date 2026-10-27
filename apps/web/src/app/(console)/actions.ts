"use server";

// Every console mutation. Each one checks the session, validates its input, calls the data
// source (the control-plane API, or DEV_MODE mocks) and returns an ActionResult the client
// can render. Errors are logged with their request id and never thrown to the client.

import { revalidatePath } from "next/cache";
import { ApiError, type ActionResult } from "@/lib/errors";
import { auth } from "@/lib/auth";
import { data } from "@/lib/server/data";
import type { WarehouseInput } from "@/lib/server/data/source";
import { log } from "@/lib/server/log";
import type { Caps, ProviderConfig } from "@/lib/types";

const APP_ID = /^[a-z][a-z0-9_-]{1,40}$/;
const OP_ID = /^[A-Za-z0-9_-]{3,80}$/;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
const DOMAIN = /^(?!-)[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$/i;
const PROVIDERS: ProviderConfig["id"][] = ["meta", "openai-compatible", "openai", "anthropic"];

class Invalid extends ApiError {
  constructor(message: string) {
    super("invalid_argument", message, 400, "");
  }
}

async function run<T>(name: string, fn: () => Promise<T>, revalidate?: string): Promise<ActionResult<T>> {
  try {
    const session = await auth.getSession();
    if (!session) throw new ApiError("unauthenticated", "Sign in again.", 401, "");
    const result = await fn();
    if (revalidate) revalidatePath(revalidate, "layout");
    return { ok: true, data: result };
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError("internal", "Something went wrong on our side. Try again.", 500, "");
    log(err.status >= 500 || err.code === "internal" ? "ERROR" : "WARNING", `action ${name} failed`, {
      code: err.code,
      requestId: err.requestId,
      error: e instanceof ApiError ? e.message : String(e),
    });
    return { ok: false, error: err.toJSON() };
  }
}

function checkApp(id: string) {
  if (!APP_ID.test(id)) throw new Invalid("That app name isn't valid.");
}

export async function saveCapsAction(appId: string, caps: Partial<Caps>, idempotencyKey: string) {
  return run("saveCaps", async () => {
    checkApp(appId);
    for (const [k, v] of Object.entries(caps)) {
      if (k === "memory") {
        if (typeof v !== "string" || !/^\d+ (Mi|Gi)B$/.test(v)) throw new Invalid("Memory must look like 512 MiB or 1 GiB.");
      } else if (typeof v !== "number" || !Number.isFinite(v) || v < 0) {
        throw new Invalid(`${k} must be a number of 0 or more.`);
      }
    }
    if (caps.maxInstances !== undefined && caps.maxInstances > 10) throw new Invalid("Max instances can be at most 10.");
    return (await data()).saveCaps(appId, caps, idempotencyKey.slice(0, 64));
  }, `/apps/${appId}`);
}

export async function changeAccessAction(
  appId: string,
  change: { add?: { principal: string; role: "viewer" | "editor" }[]; remove?: string[] },
  idempotencyKey: string,
) {
  return run("changeAccess", async () => {
    checkApp(appId);
    for (const g of change.add ?? []) {
      const [kind, value] = [g.principal.split(":")[0], g.principal.slice(g.principal.indexOf(":") + 1)];
      const ok = kind === "domain" ? DOMAIN.test(value) : (kind === "user" || kind === "group") && EMAIL.test(value);
      if (!ok) throw new Invalid(`${g.principal} isn't a valid person, group or domain.`);
      if (g.role !== "viewer" && g.role !== "editor") throw new Invalid("Role must be viewer or editor.");
    }
    return (await data()).changeAccess(appId, change, idempotencyKey.slice(0, 64));
  }, `/apps/${appId}`);
}

export async function restoreAppAction(appId: string) {
  return run("restoreApp", async () => {
    checkApp(appId);
    await (await data()).restoreApp(appId);
    return null;
  }, "/apps");
}

export async function decideApprovalAction(id: string, decision: "approve" | "reject", comment?: string) {
  return run("decideApproval", async () => {
    if (!OP_ID.test(id)) throw new Invalid("That approval link isn't valid.");
    if (comment && comment.length > 500) throw new Invalid("Keep the note under 500 characters.");
    const a = await (await data()).decideApproval(id, decision, comment?.trim() || undefined);
    if (!a) throw new ApiError("not_found", "This approval request doesn't exist.", 404, "");
    return a;
  }, "/approvals");
}

export async function createTokenAction(input: { name: string; scopes: string[]; days: number }) {
  return run("createToken", async () => {
    const name = input.name.trim();
    if (!name || name.length > 80) throw new Invalid("Name the token (up to 80 characters).");
    if (!input.scopes.length) throw new Invalid("Pick at least one permission.");
    if (![7, 30, 90].includes(input.days)) throw new Invalid("Tokens expire after 7, 30 or 90 days.");
    return (await data()).createToken({ name, scopes: input.scopes, days: input.days });
  }, "/settings/tokens");
}

export async function revokeTokenAction(id: string) {
  return run("revokeToken", async () => {
    if (!OP_ID.test(id)) throw new Invalid("Unknown token.");
    await (await data()).revokeToken(id);
    return null;
  }, "/settings/tokens");
}

export async function saveProviderKeyAction(id: ProviderConfig["id"], input: { key: string; baseUrl?: string; model?: string }) {
  return run("saveProviderKey", async () => {
    if (!PROVIDERS.includes(id) || id === "meta") throw new Invalid("Unknown provider.");
    if (!input.key.trim() || input.key.length > 500) throw new Invalid("Paste the API key.");
    if (id === "openai-compatible" && !/^https:\/\/.+/.test(input.baseUrl ?? "")) {
      throw new Invalid("Enter an https base URL, for example https://api.fireworks.ai/inference/v1.");
    }
    return (await data()).saveProviderKey(id, { key: input.key.trim(), baseUrl: input.baseUrl, model: input.model });
  }, "/settings/ai");
}

export async function removeProviderKeyAction(id: ProviderConfig["id"]) {
  return run("removeProviderKey", async () => {
    if (!PROVIDERS.includes(id) || id === "meta") throw new Invalid("Unknown provider.");
    await (await data()).removeProviderKey(id);
    return null;
  }, "/settings/ai");
}

export async function linkGcpProjectAction(input: { projectId: string; region: string }) {
  return run("linkGcpProject", async () => {
    if (!/^[a-z][a-z0-9-]{4,28}[a-z0-9]$/.test(input.projectId)) {
      throw new Invalid("That isn't a valid project ID. It's 6–30 lowercase letters, digits or hyphens, like acme-analytics-prod.");
    }
    if (!/^[a-z]+-[a-z]+\d$/.test(input.region)) throw new Invalid("Pick a region.");
    return (await data()).linkGcpProject(input);
  }, "/settings/gcp");
}

export async function connectWarehouseAction(input: WarehouseInput, save: boolean) {
  return run("connectWarehouse", async () => {
    if (input.kind === "bigquery") {
      if (!input.serviceAccountKey || input.serviceAccountKey.length > 20_000) throw new Invalid("Paste the service account key JSON.");
    } else if (!["postgres", "clickhouse"].includes(input.kind)) {
      throw new Invalid("Unknown warehouse type.");
    }
    return (await data()).connectWarehouse(input, save);
  }, save ? "/data" : undefined);
}

export async function decideMigrationAction(id: string, decision: "apply" | "reject", opts: { allowBreaking: boolean; note?: string }) {
  return run("decideMigration", async () => {
    if (!/^[0-9]{4}_[a-z0-9_]+$/.test(id)) throw new Invalid("That migration id isn't valid.");
    return (await data()).decideMigration(id, decision, opts);
  }, "/data");
}
