import { expect, test, type Page } from "@playwright/test";

// Sign-in where a popup can't work, each case in a fresh browser context. Every case must end with
// the session exchange, landing in the console. Needs AUTH_PROVIDER=firebase-emulator.

test.beforeAll(async ({ request }) => {
  const health = await (await request.get("/api/healthz")).json();
  test.skip(health.auth !== "firebase-emulator", "needs AUTH_PROVIDER=firebase-emulator");
});

/** The emulator's Google stand-in, open in this tab. */
async function pickEmulatorAccount(page: Page, email: string) {
  await page.waitForURL(/\/emulator\/auth\/handler/);
  await page.getByRole("button", { name: /add new account/i }).click();
  await page.getByLabel(/email/i).fill(email);
  await page.getByRole("button", { name: /sign in with google\.com/i }).click();
}

async function expectSignedIn(page: Page) {
  await page.waitForURL(/\/(apps|onboarding)/, { timeout: 20_000 });
  const res = await page.request.get("/apps", { maxRedirects: 0 });
  expect(res.status()).toBe(200);
}

test("signs in with the always-visible 'Sign in in this tab' link", async ({ browser }) => {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto("/sign-in");
  await page.getByRole("button", { name: "Sign in in this tab" }).click();
  await pickEmulatorAccount(page, `link-${Date.now()}@example.com`);
  await expectSignedIn(page);
  await context.close();
});

test("continues in the same tab when popups are blocked", async ({ browser }) => {
  const context = await browser.newContext();
  // A popup blocker: window.open returns null, which Firebase reports as auth/popup-blocked.
  await context.addInitScript(() => {
    window.open = () => null;
  });
  const page = await context.newPage();
  await page.goto("/sign-in");
  await page.getByRole("button", { name: /continue with google/i }).click();
  await pickEmulatorAccount(page, `blocked-${Date.now()}@example.com`);
  await expectSignedIn(page);
  await context.close();
});

test("uses the same tab in the Claude desktop app's browser pane", async ({ browser }) => {
  // The pane's user agent; there the main button goes straight to the redirect flow.
  const context = await browser.newContext({
    userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Claude/3.6556.2 Chrome/152.0.0.0 Safari/537.36",
  });
  const page = await context.newPage();
  await page.goto("/sign-in");
  await page.getByRole("button", { name: /continue with google/i }).click();
  await pickEmulatorAccount(page, `pane-${Date.now()}@example.com`);
  await expectSignedIn(page);
  await context.close();
});

test("recovers when the sign-in window took over this tab", async ({ browser }) => {
  const context = await browser.newContext();
  // What the pane does with an ordinary user agent: window.open sends this tab to the popup URL
  // and returns null, so the emulator page has no opener ("No matching frame").
  await context.addInitScript(() => {
    window.open = ((url?: string | URL) => {
      if (url) location.assign(String(url));
      return null;
    }) as typeof window.open;
  });
  const page = await context.newPage();
  page.on("dialog", (d) => void d.dismiss());
  await page.goto("/sign-in");
  await page.getByRole("button", { name: /continue with google/i }).click();
  await pickEmulatorAccount(page, `takeover-${Date.now()}@example.com`);

  // Either the redirect won the race and sign-in finished, or the popup page took the tab and
  // failed; then, back on /sign-in, the page explains and the link finishes it.
  const done = await page.waitForURL(/\/(apps|onboarding)/, { timeout: 8_000 }).then(() => true, () => false);
  if (!done) {
    await page.goto("/sign-in");
    await page.getByRole("button", { name: "Sign in in this tab" }).click();
    await pickEmulatorAccount(page, `takeover-${Date.now()}@example.com`);
  }
  await expectSignedIn(page);
  await context.close();
});
