import { expect, test, type Browser } from "@playwright/test";
import { signIn } from "./helpers";

// Early access, end to end: a visitor requests access on the public landing page, a platform admin
// approves it in the console, and the visitor's first sign-in claims the new workspace.
//
// The landing and route checks run against any console. The full flow needs the local stack's API
// to serve the waitlist (POST /v1/waitlist, /v1/waitlist/me, /v1/admin/waitlist), the Firebase Auth
// emulator, and E2E_ADMIN_EMAIL: an address the API treats as a platform admin.

const API_URL = (process.env.E2E_API_URL ?? "http://127.0.0.1:8080").replace(/\/+$/, "");
const ADMIN = process.env.E2E_ADMIN_EMAIL ?? "";
const REQUESTER = `waitlist-${Date.now()}@example.com`;

test("the landing page is public, and the console isn't", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: /deploy internal apps/i })).toBeVisible();
  await expect(page.getByRole("link", { name: "Sign in" }).first()).toBeVisible();
  for (const path of ["/apps", "/admin/waitlist", "/waitlist"]) {
    await page.goto(path);
    await expect(page).toHaveURL(/\/sign-in\?returnTo=/);
  }
});

test("a bot that fills the honeypot is told it worked", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Work email").fill(`bot-${Date.now()}@example.com`);
  await page.getByLabel("Name").fill("Bot");
  await page.getByLabel("What will you build?").fill("Anything");
  await page.locator('input[name="website"]').fill("http://spam.example", { force: true });
  await page.getByRole("button", { name: "Request access" }).click();
  await expect(page.getByText("Thanks, you're on the list.")).toBeVisible();
});

test.describe("request, approve, sign in", () => {
  test.describe.configure({ mode: "serial" });

  test.beforeAll(async ({ request }) => {
    const health = await (await request.get("/api/healthz")).json();
    test.skip(health.auth !== "firebase-emulator", "needs AUTH_PROVIDER=firebase-emulator");
    test.skip(!ADMIN, "set E2E_ADMIN_EMAIL to a platform admin's address");
    // A filled honeypot stores nothing, so this probe leaves no trace.
    const probe = await request.post(`${API_URL}/v1/waitlist`, { data: { email: "probe@example.com", name: "p", use_case: "p", website: "x" } });
    test.skip(probe.status() === 404, "the API doesn't serve /v1/waitlist yet");
  });

  test("a visitor requests access", async ({ page }) => {
    await page.goto("/#request-access");
    await page.getByLabel("Work email").fill(REQUESTER);
    await page.getByLabel("Name").fill("Wait Lister");
    await page.getByLabel("What will you build?").fill("A small dashboard, to test the waitlist.");
    await page.getByRole("button", { name: "Request access" }).click();
    await expect(page.getByText("Thanks, you're on the list.")).toBeVisible();
  });

  test("signed in before approval, they see the waitlist, not an error", async ({ browser }) => {
    const page = await fresh(browser);
    await signIn(page, REQUESTER);
    await page.goto("/apps");
    await expect(page).toHaveURL(/\/waitlist$/);
    await expect(page.getByRole("heading", { name: "You're on the waitlist" })).toBeVisible();
    await page.context().close();
  });

  test("a platform admin approves the request", async ({ browser }) => {
    const page = await fresh(browser);
    await signIn(page, ADMIN);
    await page.goto("/admin/waitlist");
    const row = page.getByRole("row").filter({ hasText: REQUESTER });
    await row.getByRole("button", { name: /approve/i }).click();
    await expect(row.getByText("Approved")).toBeVisible();
    await page.context().close();
  });

  test("their next sign-in opens the new workspace", async ({ browser }) => {
    const page = await fresh(browser);
    await signIn(page, REQUESTER);
    await page.goto("/apps");
    await expect(page).toHaveURL(/\/(apps|onboarding)/);
    await expect(page.getByText("You're on the waitlist")).toHaveCount(0);
    await page.context().close();
  });
});

async function fresh(browser: Browser) {
  return (await browser.newContext()).newPage();
}
