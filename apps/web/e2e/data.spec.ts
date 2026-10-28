import { expect, test, type Page } from "@playwright/test";
import { signIn } from "./helpers";

// The Data section against the local stack (shipcube-data's /v1/data router, PR #36): the demo
// warehouse, the cubes, the applied model migrations, and a person deciding on an agent's proposal.
// `make smoke` runs `shipcube-data smoke --mcp` first, which leaves one proposed migration.

const EMAIL = process.env.E2E_EMAIL ?? `e2e-data-${Date.now()}@example.com`;

test.describe.configure({ mode: "serial" });

let page: Page;

test.beforeAll(async ({ browser }) => {
  page = await browser.newPage();
  await signIn(page, EMAIL);
});

test.afterAll(async () => {
  await page.close();
});

test("the warehouse is connected and reachable", async () => {
  await page.goto("/data/warehouse");
  await expect(page.getByText("Connected warehouse")).toBeVisible();
  await expect(page.getByText(/duckdb \(local development\)/i)).toBeVisible();
  await expect(page.getByText(/reachable/i).first()).toBeVisible();
});

test("lists the demo cubes, and orders is partitioned in the tenant schema", async () => {
  await page.goto("/data");  // the cube list is the Data overview; /data/cubes/<name> is one cube
  const cubes = page.locator('a[href^="/data/cubes/"]');
  await expect.poll(() => cubes.count()).toBeGreaterThanOrEqual(5);
  await page.goto("/data/cubes/orders");
  await expect(page.getByText("tenant_acme").first()).toBeVisible();
  await expect(page.getByText(/one table per partition/i)).toBeVisible();
});

test("shows the applied model migrations", async () => {
  await page.goto("/data/migrations");
  await expect(page.getByText("Applied", { exact: true }).first()).toBeVisible();
  const versions = page.getByText(/^v\d+$/);
  await expect.poll(() => versions.count()).toBeGreaterThanOrEqual(5);
});

test("the proposer can't apply their own change, but can reject it with a note", async () => {
  await page.goto("/data/migrations");
  const proposed = page.locator('a[href^="/data/migrations/"]').first();
  test.skip((await proposed.count()) === 0, "no proposed migration: run `make smoke` first");
  await proposed.click();
  await page.getByRole("button", { name: "Apply to the model" }).click();
  // The seeded console user also owns the PAT that proposed it, so the API refuses (two-person rule, 403).
  // The console currently shows its generic permission message rather than the API's reason.
  await expect(page.getByText(/person who proposed it/i)).toBeVisible();
  await page.locator("#mig-note").fill("e2e: rejected by the local stack's Playwright run");
  await page.getByRole("button", { name: "Reject" }).click();
  await expect(page.getByText("Rejected", { exact: true })).toBeVisible();
});
