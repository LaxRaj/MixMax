import { expect, test } from "@playwright/test";
import { SOURCE_NAMES } from "./helpers";

test.describe("pipeline dashboard", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/dashboard");
  });

  test("hydrates and renders the evidence meter", async ({ page }) => {
    await expect(page.getByRole("heading", { name: /what do we actually know/i })).toBeVisible();
    await expect(page.getByText(/of \d+ numbers come from a published spec/i)).toBeVisible();
    await expect(page.getByText(/^Loading…$/)).toHaveCount(0);
  });

  test("every evidence row carries a provenance label", async ({ page }) => {
    const rows = page.getByTestId("evidence-row");
    // The ledger arrives from a client-side fetch, and count() does not
    // auto-wait the way an expect() does.
    await expect(rows.first()).toBeVisible();
    const count = await rows.count();
    expect(count).toBeGreaterThan(5);

    const labels = new Set<string>();
    for (let i = 0; i < count; i++) {
      const p = await rows.nth(i).getAttribute("data-p");
      expect(["published", "measured", "fitted", "reported", "guessed"]).toContain(p);
      labels.add(p!);
    }
    // The page is pointless if everything claims the same footing.
    expect(labels.size).toBeGreaterThan(1);
  });

  test("a row expands to show where the number came from", async ({ page }) => {
    const row = page.getByTestId("evidence-row").first();
    await expect(row).toHaveAttribute("aria-expanded", "false");
    await row.click();
    await expect(row).toHaveAttribute("aria-expanded", "true");
    await expect(row.getByTestId("evidence-detail")).toBeVisible();
  });

  test("guessed defaults are called guessed", async ({ page }) => {
    await expect(page.getByTestId("evidence-row").filter({ has: page.locator('[data-p="guessed"]') }).first().or(page.locator('[data-testid="evidence-row"][data-p="guessed"]').first())).toBeVisible();
  });

  test("the pipeline shows every stage with a count", async ({ page }) => {
    for (const label of ["Intake", "Our master", "Benchmarked", "Blind listening"]) {
      await expect(page.getByText(label, { exact: true })).toBeVisible();
    }
  });

  test("the unanswered question is stated plainly, not hidden", async ({ page }) => {
    await expect(page.getByRole("heading", { name: /does it sound good/i })).toBeVisible();
    await expect(page.getByText(/nobody has listened yet/i)).toBeVisible();
  });

  test("no source name from a blind test leaks here", async ({ page }) => {
    const dom = (await page.content()).toLowerCase();
    for (const name of SOURCE_NAMES) {
      // "producer" is the product name and legitimately appears in copy; what
      // must not appear is a blind label mapped to a source.
      if (name === "producer" || name === "original") continue;
      expect(dom, `dashboard leaks "${name}"`).not.toContain(`>${name}<`);
    }
  });

  test("survives a completely empty pipeline", async ({ page }) => {
    await page.route("**/dashboard.json", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          stages: [{ key: "intake", label: "Intake", done: 0, total: 0, blurb: "x" }],
          tracks: [],
          evidence: [],
          evidence_summary: { counts: {}, total: 0, grounded_pct: 0 },
          library: null,
          listening: null,
        }),
      }),
    );
    await page.reload();

    await expect(page.getByText(/nothing ingested yet/i)).toBeVisible();
    await expect(page.getByText(/no listening data/i)).toBeVisible();
  });

  test("explains itself when the data file is missing", async ({ page }) => {
    await page.route("**/dashboard.json", (route) => route.fulfill({ status: 404, body: "" }));
    await page.reload();

    await expect(page.getByRole("heading", { name: /no data yet/i })).toBeVisible();
    await expect(page.getByText(/producer dashboard --workspace/)).toBeVisible();
  });
});
