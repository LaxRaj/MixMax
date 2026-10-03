import { expect, test } from "@playwright/test";

const FIXTURE = {
  stage_order: ["intake", "vocal", "backing", "master", "arrangement", "judged"],
  tracks: [
    {
      slug: "finished-one", kind: "full-mix", kind_reason: "plays continuously",
      duration_s: 185, percent: 90,
      stages: [
        { key: "intake", label: "Raw material", state: "done", detail: "118s", blocker: "" },
        { key: "backing", label: "Backing track", state: "done", detail: "a finished mix", blocker: "" },
        { key: "judged", label: "Judged by ear", state: "partial", detail: "1 listener — an anecdote", blocker: "needs 2 more" },
      ],
      measurements: {
        raw: { lufs: -23.6, true_peak_dbtp: -4.87, crest_factor_db: 18.8, lra: 4.6 },
        final: { lufs: -16.1, true_peak_dbtp: -3.0, crest_factor_db: 12.2, lra: 1.9 },
        final_file: "MASTER_extended.wav",
      },
      arrangement: {
        sections: 3, types: 2,
        transitions: [{ at_s: 110, from_label: "B", to_label: "E", kind: "breakdown", energy_change_db: -0.3, low_change_db: -11.1 }],
        notes: [],
        timeline: [
          { label: "A", start_s: 0, duration_s: 60, energy_db: -22, low_energy_db: -11 },
          { label: "B", start_s: 60, duration_s: 65, energy_db: -23, low_energy_db: -21 },
          { label: "A", start_s: 125, duration_s: 60, energy_db: -22, low_energy_db: -11 },
        ],
      },
      listening: { listeners: 1, preferred: "mastered_16" },
      next_step: "needs 2 more",
    },
    {
      slug: "bare-vocal", kind: "vocal-only", kind_reason: "stops between phrases",
      duration_s: 144, percent: 50,
      stages: [
        { key: "intake", label: "Raw material", state: "done", detail: "144s", blocker: "" },
        { key: "backing", label: "Backing track", state: "blocked", detail: "there is no instrumental under this vocal", blocker: "A beat has to be written" },
      ],
      measurements: {
        raw: { lufs: -21.6, true_peak_dbtp: 0.0, crest_factor_db: 21.6, lra: 7.59 },
        final: { lufs: -16.2, true_peak_dbtp: -3.0, crest_factor_db: 11.2, lra: 6.05 },
        final_file: "MASTER.wav",
      },
      arrangement: { sections: 1, types: 1, transitions: [], notes: [], timeline: [
        { label: "A", start_s: 0, duration_s: 144, energy_db: -20, low_energy_db: -30 },
      ] },
      listening: null,
      next_step: "A beat has to be written, bought or licensed.",
    },
  ],
};

test.describe("development comparison", () => {
  test.beforeEach(async ({ page }) => {
    await page.route("**/compare.json", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(FIXTURE) }),
    );
    await page.goto("/compare");
  });

  test("hydrates and shows every track", async ({ page }) => {
    await expect(page.getByRole("heading", { name: /how finished is each one/i })).toBeVisible();
    await expect(page.getByText("finished-one")).toBeVisible();
    await expect(page.getByText("bare-vocal")).toBeVisible();
    await expect(page.getByText(/^Loading…$/)).toHaveCount(0);
  });

  test("completion is shown per track, not as one number", async ({ page }) => {
    await expect(page.getByText("90%")).toBeVisible();
    await expect(page.getByText("50%")).toBeVisible();
  });

  test("a blocked stage is visibly distinct from a finished one", async ({ page }) => {
    await expect(page.locator('[data-s="blocked"]')).toHaveCount(1);
    await expect(page.locator('[data-s="done"]').first()).toBeVisible();
  });

  test("the blocker says what is actually missing", async ({ page }) => {
    await expect(page.getByText(/no instrumental under this vocal/i)).toBeVisible();
    await expect(page.getByText(/a beat has to be written/i)).toBeVisible();
  });

  test("the arrangement timeline renders a block per section", async ({ page }) => {
    const blocks = page.locator('[class*="block"][title]');
    await expect(blocks.first()).toBeVisible();
    expect(await blocks.count()).toBe(4);   // 3 + 1 across both tracks
  });

  test("a breakdown is surfaced as a marker", async ({ page }) => {
    await expect(page.locator('[data-k="breakdown"]')).toHaveCount(1);
  });

  test("a track with no transitions says so rather than rendering nothing", async ({ page }) => {
    await expect(page.getByText("no transitions")).toBeVisible();
  });

  test("raw and final measurements are both shown", async ({ page }) => {
    await expect(page.getByText("-23.6")).toBeVisible();
    await expect(page.getByText("-16.1")).toBeVisible();
  });

  test("explains itself when there is no data", async ({ page }) => {
    await page.route("**/compare.json", (route) => route.fulfill({ status: 404, body: "" }));
    await page.reload();
    await expect(page.getByRole("heading", { name: /no data yet/i })).toBeVisible();
    await expect(page.getByText(/producer compare --workspace/)).toBeVisible();
  });
});
