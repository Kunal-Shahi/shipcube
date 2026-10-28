import { expect, type Page } from "@playwright/test";

// Sign-in through the Firebase Auth emulator (or the DEV_MODE mock), shared by the specs.
// The same flow as console.spec.ts's signIn; frontend may fold that one onto this.

export async function signIn(page: Page, email: string): Promise<void> {
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

async function attemptSignIn(page: Page, email: string): Promise<void> {
  await page.goto("/sign-in");
  const popup = page.waitForEvent("popup", { timeout: 5_000 }).catch(() => null);
  await page.getByRole("button", { name: /continue with google/i }).click();
  const p = await popup;
  if (p) {
    await p.waitForLoadState();
    const relay = () => page.frames().find((f) => f.url().includes("/emulator/auth/iframe"));
    await expect.poll(() => Boolean(relay()), { timeout: 10_000 }).toBe(true);
    await relay()!.waitForLoadState("load", { timeout: 15_000 });
    await p.getByRole("button", { name: /add new account/i }).click();
    await p.getByLabel(/email/i).fill(email);
    await p.getByRole("button", { name: /sign in with google\.com/i }).click();
  }
  await page.waitForURL(/\/(apps|onboarding|setup)/, { timeout: 15_000 });
}
