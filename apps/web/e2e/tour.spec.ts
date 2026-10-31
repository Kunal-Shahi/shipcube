import { expect, test } from "@playwright/test";
import { MEMBER_EMAIL, signIn } from "./helpers";

// The first-run tour: it opens on the Overview the first time, can be skipped, stays away after
// that, and comes back from "Take the tour" in the sidebar.

test("the tour shows once, can be skipped, and can be replayed", async ({ page }) => {
  await signIn(page, MEMBER_EMAIL, { tour: true });
  await page.goto("/overview");
  const tour = page.getByRole("dialog");
  await expect(tour.getByRole("heading", { name: "Welcome to ShipCube" })).toBeVisible();

  await tour.getByRole("button", { name: "Start the tour", exact: true }).click();
  await expect(tour.getByText("Step 1 of 6")).toBeVisible();
  await tour.getByRole("button", { name: "Skip", exact: true }).click();
  await expect(tour).toHaveCount(0);

  await page.reload();
  await expect(page.getByRole("heading", { name: "Overview", level: 1 })).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);

  await page.getByRole("link", { name: "Take the tour" }).click();
  await expect(page.getByRole("dialog").getByRole("heading", { name: "Welcome to ShipCube" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});
