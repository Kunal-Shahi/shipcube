import { expect, test, type Page } from "@playwright/test";
import { signIn } from "./helpers";

// The approvals inbox and AI providers (BYOK) against the local stack (networking's #35 endpoints).
// An agent asks to share the app `make smoke` deployed with a whole domain; a person finds it in the
// inbox and approves it. Then a person adds an Anthropic key, which never comes back to the browser.

const API_URL = (process.env.E2E_API_URL ?? "http://127.0.0.1:8080").replace(/\/+$/, "");
const APP = process.env.E2E_APP ?? "olist-tracker";
/** The seeded PAT (`make e2e` loads deploy/local/.state/seed.env): requests made as an agent. */
const PAT = process.env.SHIPCUBE_PAT ?? "";
const EMAIL = process.env.E2E_EMAIL ?? `e2e-${Date.now()}@example.com`;
const DOMAIN = `e2e-${Date.now()}.example.org`;

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

test("the inbox lists an agent's domain-sharing request, and a person approves it", async () => {
  const res = await page.request.patch(`${API_URL}/v1/apps/${APP}/access`, {
    headers: agent({ "Idempotency-Key": `e2e-${DOMAIN}` }),
    data: { add: [{ principal: `domain:${DOMAIN}`, role: "viewer" }] },
  });
  expect(res.status()).toBe(200);
  const op = (await res.json()).operation;
  expect(op.status).toBe("awaiting_approval");

  await page.goto("/approvals");
  await expect(page.getByText("isn't available yet")).toHaveCount(0);
  const row = page.getByRole("link", { name: `Share ${APP} with a whole domain` }).first();
  await expect(row).toBeVisible();
  // The nav badge counts what's waiting.
  await expect(page.getByLabel(/^\d+ waiting$/).first()).toBeVisible();

  await page.goto(`/approvals/${op.id}`);
  await expect(page.getByText(DOMAIN).first()).toBeVisible();
  await page.getByRole("button", { name: "Approve sharing" }).click();
  await expect(page.getByText(/You approved this/)).toBeVisible();

  await expect
    .poll(async () => (await (await page.request.get(`${API_URL}/v1/operations/${op.id}`, { headers: agent() })).json()).status, {
      timeout: 60_000,
    })
    .toBe("succeeded");
  const app = await (await page.request.get(`${API_URL}/v1/apps/${APP}`, { headers: agent() })).json();
  expect(app.access.map((g: { principal: string }) => g.principal)).toContain(`domain:${DOMAIN}`);

  // Decided requests stay in the inbox's history.
  await page.goto("/approvals");
  await expect(page.getByRole("link", { name: `Share ${APP} with a whole domain` }).first()).toBeVisible();

  // Leave the app shared as before.
  await page.request.patch(`${API_URL}/v1/apps/${APP}/access`, {
    headers: agent({ "Idempotency-Key": `e2e-rm-${DOMAIN}` }),
    data: { remove: [`domain:${DOMAIN}`] },
  });
});

test("adds a BYOK Anthropic key; the key itself never comes back", async () => {
  const key = `sk-ant-e2e-${Date.now()}-${Math.random().toString(36).slice(2)}Wxyz`;
  await page.goto("/settings/ai");
  await expect(page.getByText("isn't available yet")).toHaveCount(0);
  const anthropic = page.locator("li").filter({ has: page.getByText("Anthropic", { exact: true }) });
  await anthropic.getByRole("button", { name: "Add key" }).click();
  await anthropic.getByLabel("API key").fill(key);
  await anthropic.getByRole("button", { name: "Test and save" }).click();
  await expect(anthropic.getByText("Connected")).toBeVisible();
  await expect(anthropic.getByText("…Wxyz")).toBeVisible();
  // Saving a key makes Anthropic the workspace default; the console says so, not Muse.
  const muse = page.locator("li").filter({ has: page.getByText("Meta Muse", { exact: true }) });
  await expect(anthropic.getByText("Default", { exact: true })).toBeVisible();
  await expect(muse.getByText("Default", { exact: true })).toHaveCount(0);

  // Not in the page after a reload, nor in the API's answer: only the last 4 characters.
  await page.reload();
  await expect(anthropic.getByText("…Wxyz")).toBeVisible();
  await expect(anthropic.getByText("Default", { exact: true })).toBeVisible();
  expect(await page.content()).not.toContain(key);
  const body = await (await page.request.get(`${API_URL}/v1/ai/providers`, { headers: agent() })).text();
  expect(body).not.toContain(key);
  const p = JSON.parse(body).providers.find((x: { id: string }) => x.id === "anthropic");
  expect(p).toMatchObject({ connected: true, key_hint: "…Wxyz" });

  // Remove it: the workspace falls back to Muse.
  await anthropic.getByRole("button", { name: "Remove" }).click();
  await expect(anthropic.getByText("Not connected")).toBeVisible();
  await expect(muse.getByText("Default", { exact: true })).toBeVisible();
  const after = await (await page.request.get(`${API_URL}/v1/ai/providers`, { headers: agent() })).json();
  expect(after.providers.find((x: { id: string }) => x.id === "anthropic").connected).toBe(false);
  expect(after.providers.find((x: { id: string }) => x.id === "meta").default).toBe(true);
});
