import { expect, test, type Page } from "@playwright/test";
import { signIn } from "./helpers";

// One person's path through the console against the local stack, in order: sign in, create a
// PAT, connect an agent, find the app `make smoke` deployed, share it, cap it, delete it, restore it.
// Each step builds on the one before, so the file runs serially.

const MCP_URL = process.env.E2E_MCP_URL ?? "http://localhost:8080/mcp";
const API_URL = (process.env.E2E_API_URL ?? "http://127.0.0.1:8080").replace(/\/+$/, "");
/** The app `make smoke` deploys (services/mcp/scripts/smoke.py). */
const APP = process.env.E2E_APP ?? "olist-tracker";
const EMAIL = process.env.E2E_EMAIL ?? `e2e-${Date.now()}@example.com`;

test.describe.configure({ mode: "serial" });

let page: Page;
let pat = "";
let appPath = "";

test.beforeAll(async ({ browser }) => {
  page = await browser.newPage();
});

test.afterAll(async () => {
  await page.close();
});

test("signs in", async () => {
  await signIn(page, EMAIL);
  await expect(page.getByRole("heading", { name: "Apps", level: 1 })).toBeVisible();
  const me = await page.request.get("/api/healthz");
  expect(me.ok()).toBe(true);
});

test("creates a workspace PAT", async () => {
  await page.goto("/setup");
  await page.getByLabel("Token name").fill(`e2e ${Date.now()}`);
  await page.getByRole("button", { name: /create token/i }).click();
  const secret = page.locator("pre, code").filter({ hasText: /^sc_pat_[A-Za-z0-9]{8}_/ }).first();
  await expect(secret).toBeVisible();
  pat = (await secret.textContent())!.trim();
  expect(pat).toMatch(/^sc_pat_[A-Za-z0-9]{8}_[A-Za-z0-9_-]{32,}$/);

  // The token works against the API.
  const me = await page.request.get(`${API_URL}/v1/me`, { headers: { Authorization: `Bearer ${pat}` } });
  expect(me.status()).toBe(200);
});

test("the setup guide shows the local MCP URL", async () => {
  // Still on /setup: the snippets point at this stack's MCP server, and the shell step has the new token.
  await expect(page.getByText(`claude mcp add --transport http shipcube ${MCP_URL}`)).toBeVisible();
  await expect(page.getByText(`export SHIPCUBE_TOKEN=${pat}`)).toBeVisible();
  await expect(page.getByText(/shipcube\.dev/)).toHaveCount(0);
});

test("the apps list shows the app make smoke deployed", async () => {
  await page.goto("/apps");
  const link = page.getByRole("link", { name: APP, exact: true });
  await expect(link).toBeVisible();
  await link.click();
  await page.waitForURL(/\/apps\/[^/]+$/);
  appPath = new URL(page.url()).pathname;
  await expect(page.getByRole("heading", { name: APP, level: 1 })).toBeVisible();
});

test("the Overview shows the app's load estimate", async () => {
  const app = await (await page.request.get(`${API_URL}/v1/apps/${APP}`, { headers: { Authorization: `Bearer ${pat}` } })).json();
  // GET /v1/apps/{app} gains `estimate` with networking's change; until then there's nothing to show.
  test.skip(!app.estimate, "the API doesn't return the app's estimate yet");
  await page.goto(appPath);
  await expect(page.getByText("No load estimate on record")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Load estimate" })).toBeVisible();
  const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(app.estimate.monthly_cost_usd);
  await expect(page.getByText(usd, { exact: true }).first()).toBeVisible();
  await expect(page.getByText(`${Math.round(app.estimate.confidence * 100)}% confidence`)).toBeVisible();
});

test("shares the app with a person", async () => {
  const viewer = `viewer-${Date.now()}@example.com`;
  await page.goto(`${appPath}/access`);
  await page.locator("#grant-principal").fill(viewer);
  await page.getByRole("button", { name: "Grant access" }).click();
  await expect(page.getByText(viewer).first()).toBeVisible();

  // The API has it too.
  const app = await (await page.request.get(`${API_URL}/v1/apps/${APP}`, { headers: { Authorization: `Bearer ${pat}` } })).json();
  expect(app.access.map((g: { principal: string }) => g.principal)).toContain(`user:${viewer}`);
});

test("changes the spending cap", async () => {
  await page.goto(appPath);
  const spend = page.getByLabel("Monthly spend ($)");
  // Two small values, both under the workspace default so no approval is needed; whichever
  // isn't set now, so the form is always dirty, however many times the suite has run.
  const lower = Number(await spend.inputValue()) === 4 ? 3 : 4;
  await spend.fill(String(lower));
  const save = page.getByRole("button", { name: "Save caps" });
  await expect(save).toBeEnabled();
  await save.click();
  await expect(page.getByText(`Caps saved for ${APP}`)).toBeVisible();

  await page.reload();
  await expect(page.getByLabel("Monthly spend ($)")).toHaveValue(String(lower));
  const app = await (await page.request.get(`${API_URL}/v1/apps/${APP}`, { headers: { Authorization: `Bearer ${pat}` } })).json();
  expect(app.caps.monthly_spend_usd).toBe(lower);
});

test("deletes the app: the agent asks, a person approves", async () => {
  // Agents delete (delete_app); the API holds it for a person. Ask with the PAT, as an agent would.
  const res = await page.request.delete(`${API_URL}/v1/apps/${APP}`, {
    headers: { Authorization: `Bearer ${pat}`, "Idempotency-Key": `e2e-${Date.now()}` },
  });
  expect(res.status()).toBe(202);
  const op = await res.json();
  expect(op.status).toBe("awaiting_approval");
  expect(op.approval_url).toBeTruthy();

  // The approval link lands on the console's decision page.
  await page.goto(new URL(op.approval_url).pathname);
  await page.getByRole("button", { name: "Delete app" }).click();
  await expect(page.getByText(/You approved this/)).toBeVisible();

  // Gone from the main list, restorable from the app page.
  await expect
    .poll(async () => (await (await page.request.get(`${API_URL}/v1/operations/${op.id}`, { headers: { Authorization: `Bearer ${pat}` } })).json()).status, {
      timeout: 60_000,
    })
    .toBe("succeeded");
  await page.goto(appPath);
  await expect(page.getByRole("button", { name: "Restore" })).toBeVisible();
});

test("restores the deleted app", async () => {
  // Restoring needs a person, so it's a console action. It also leaves the stack ready for another run.
  await page.getByRole("button", { name: "Restore" }).click();
  await expect(page.getByText(`Restored. ${APP} is serving again.`)).toBeVisible();
  await expect
    .poll(async () => (await (await page.request.get(`${API_URL}/v1/apps/${APP}`, { headers: { Authorization: `Bearer ${pat}` } })).json()).deleted_at, {
      timeout: 60_000,
    })
    .toBeNull();
});
