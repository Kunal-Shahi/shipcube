import { expect, test, type Page } from "@playwright/test";
import { signIn } from "./helpers";

// Linking a GCP project against the local stack. The local backend simulates the permission check
// (deploy/local.py): broken-* projects report a missing permission, any other project passes. The
// agent then sees the project as a deploy target, and its first deploy there waits for a person.

const API_URL = (process.env.E2E_API_URL ?? "http://127.0.0.1:8080").replace(/\/+$/, "");
const PAT = process.env.SHIPCUBE_PAT ?? "";
const EMAIL = process.env.E2E_EMAIL ?? `e2e-${Date.now()}@example.com`;
// Fixed names: linking the same project again updates its link, so reruns deploy the same app to the same target.
const PROJECT = "e2e-linked-proj";
const APP = "e2e-linked-app";

test.describe.configure({ mode: "serial" });

let page: Page;
const agent = (extra: Record<string, string> = {}) => ({ Authorization: `Bearer ${PAT}`, ...extra });

test.beforeAll(async ({ browser }) => {
  test.skip(!PAT, "SHIPCUBE_PAT isn't set: run through `make e2e`");
  page = await browser.newPage();
  await signIn(page, EMAIL);
});

test.afterAll(async () => {
  await page?.close();
});

test("an unfinished link says what's missing; a finished one links", async () => {
  await page.goto("/settings/gcp");
  await page.getByText("Link your own GCP project").click();
  // An earlier run may have linked a project already; start a new link either way.
  const relink = page.getByRole("button", { name: "Link a different project" });
  if (await relink.isVisible()) await relink.click();
  await expect(page.getByText(/-deployer@/).first()).toBeVisible(); // the workspace's deployer, filled into the script

  await page.getByLabel("Project ID").fill("broken-e2e-proj");
  await page.getByRole("button", { name: "Check access" }).click();
  await expect(page.getByText(/can't deploy to broken-e2e-proj yet\. Still missing: run\.services\.create/)).toBeVisible();

  await page.getByLabel("Project ID").fill(PROJECT);
  await page.getByRole("button", { name: "Check access" }).click();
  await expect(page.getByText(`Linked ${PROJECT}`)).toBeVisible();
});

test("the agent sees the linked project, and its first deploy there waits for approval", async () => {
  const me = await (await page.request.get(`${API_URL}/v1/me`, { headers: agent() })).json();
  const target = me.gcp_targets.find((t: { kind: string; project: string }) => t.kind === "linked" && t.project === PROJECT);
  expect(target).toBeTruthy();

  const res = await page.request.post(`${API_URL}/v1/apps/${APP}/deployments`, {
    headers: agent({ "Idempotency-Key": `e2e-link-${Date.now()}` }),
    data: { source: { kind: "image", uri: "us-docker.pkg.dev/cloudrun/container/hello:latest" }, target: target.id },
  });
  expect(res.status()).toBe(202);
  const op = await res.json();
  expect(op.status).toBe("awaiting_approval");
  expect(op.approval_actions).toEqual(["first_linked_deploy"]);

  // Leave nothing queued for the next run: reject it in the console.
  await page.goto(new URL(op.approval_url).pathname);
  await expect(page.getByText(`first deploy to linked GCP project ${PROJECT}`)).toBeVisible();
  await page.getByRole("button", { name: "Reject" }).click();
  await expect(page.getByText(/^Rejected$/).first()).toBeVisible();
  await expect
    .poll(async () => (await (await page.request.get(`${API_URL}/v1/operations/${op.id}`, { headers: agent() })).json()).status)
    .toBe("cancelled");
});
