import { expect, test, type Page } from "@playwright/test";

// Sign-in where a popup can't work. Two cases, each in a fresh browser context:
// - popups are blocked, so the console continues in the same tab (signInWithRedirect) at once;
// - the browser opens the "popup" in the same tab (as the Claude Code browser pane does), so the
//   emulator can't report back; coming back to /sign-in offers, and then uses, the redirect flow.
// Both must end with the session exchange, landing in the console. Needs AUTH_PROVIDER=firebase-emulator.

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

test("signs in through the redirect flow when popups are blocked", async ({ browser }) => {
  const context = await browser.newContext();
  // A popup blocker: window.open returns null, which Firebase reports as auth/popup-blocked.
  await context.addInitScript(() => {
    window.open = () => null;
  });
  const page = await context.newPage();
  await page.goto("/sign-in");
  await page.getByRole("button", { name: /continue with google/i }).click();
  await pickEmulatorAccount(page, `redirect-${Date.now()}@example.com`);
  await expectSignedIn(page);
  await context.close();
});

test("recovers when the sign-in window opens in the same tab", async ({ browser }) => {
  const context = await browser.newContext();
  // The browser loads the "popup" in this tab instead of a new window, so it has no opener.
  await context.addInitScript(() => {
    window.open = ((url?: string | URL) => {
      if (url) location.assign(String(url));
      return window;
    }) as typeof window.open;
  });
  const page = await context.newPage();
  page.on("dialog", (d) => void d.dismiss()); // the emulator's "No matching frame"
  await page.goto("/sign-in");
  await page.getByRole("button", { name: /continue with google/i }).click();
  await pickEmulatorAccount(page, `same-tab-${Date.now()}@example.com`);

  // The person comes back to the sign-in page, which now offers to continue in this tab.
  await page.goto("/sign-in");
  await expect(page.getByRole("button", { name: "Continue in this tab" })).toBeVisible();
  await page.getByRole("button", { name: /continue with google/i }).click();
  await pickEmulatorAccount(page, `same-tab-${Date.now()}@example.com`);
  await expectSignedIn(page);
  await context.close();
});
