import { expect, type Page } from "@playwright/test";

// Sign-in shared by the specs: the Firebase Auth emulator's popup, or the DEV_MODE mock sign-in.

/**
 * With AUTH_PROVIDER=firebase-emulator, the button opens the emulator's Google stand-in in a popup:
 * the real ID token → session cookie flow. With the DEV_MODE mock sign-in there is no popup.
 */
/**
 * Who the member-only specs sign in as: E2E_EMAIL, else the local stack's seeded workspace owner
 * (seed.env's SHIPCUBE_SEED_EMAIL, created by `admin bootstrap` without a uid, so the first emulator
 * sign-in claims it), else a fresh address for a console that runs with a dev API session.
 */
export const MEMBER_EMAIL = process.env.E2E_EMAIL ?? process.env.SHIPCUBE_SEED_EMAIL ?? `e2e-${Date.now()}@example.com`;

export async function signIn(page: Page, email: string): Promise<void> {
  // The emulator's popup hands its result to a relay iframe in this page, which loads
  // apis.google.com/js/api.js. When that load stalls the hand-off never arrives, so a stalled
  // attempt is abandoned and the whole sign-in retried.
  let lastError: unknown;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      await attemptSignIn(page, email);
      if (page.url().includes("/onboarding")) await page.goto("/apps");
      return;
    } catch (e) {
      lastError = e;
      for (const p of page.context().pages()) if (p !== page) await p.close();
    }
  }
  throw lastError;
}

async function attemptSignIn(page: Page, email: string) {
  await page.goto("/sign-in");
  const popup = page.waitForEvent("popup", { timeout: 5_000 }).catch(() => null);
  await page.getByRole("button", { name: /continue with google/i }).click();
  const p = await popup;
  if (p) {
    let alert: string | null = null;
    p.on("dialog", (d) => {
      alert = d.message();
      void d.dismiss();
    });
    await p.waitForLoadState();
    // Submitting before the relay iframe has loaded fails with "No matching frame".
    const relay = () => page.frames().find((f) => f.url().includes("/emulator/auth/iframe"));
    await expect.poll(() => Boolean(relay()), { timeout: 10_000 }).toBe(true);
    await relay()!.waitForLoadState("load", { timeout: 15_000 });
    // Reuse the emulator account for this email when it exists, so its uid (which the API has
    // bound to a user) stays the same from run to run.
    const existing = p.getByText(email, { exact: true });
    if (await existing.count()) {
      await existing.first().click();
    } else {
      await p.getByRole("button", { name: /add new account/i }).click();
      await p.getByLabel(/email/i).fill(email);
      await p.getByRole("button", { name: /sign in with google\.com/i }).click();
    }
    await page.waitForURL(/\/(overview|apps|onboarding|setup|waitlist)/, { timeout: 15_000 }).catch((e) => {
      throw alert ? new Error(`Auth emulator: ${alert}`) : e;
    });
    return;
  }
  await page.waitForURL(/\/(overview|apps|onboarding|setup)/, { timeout: 15_000 });
}
