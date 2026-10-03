import { expect, test } from "@playwright/test";
import {
  graphState,
  loadAudio,
  manifestLabels,
  positionText,
  SOURCE_NAMES,
  tagSources,
  useManifest,
  FIXTURE_MANIFEST,
  SINGLE_MANIFEST,
  useTestIndex,
} from "./helpers";

test.describe("blind listening test", () => {
  test.beforeEach(async ({ page }) => {
    await useManifest(page, FIXTURE_MANIFEST);
    await page.goto("/");
  });

  test("the page hydrates", async ({ page }) => {
    // The failure this guards: HTML renders, JS never runs, and the listener
    // stares at the server-rendered "Loading…" forever with no error shown.
    // That is exactly what Next's cross-origin dev-resource block caused.
    await expect(page.getByRole("button", { name: /load the audio/i })).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.getByText(/^Loading…$/)).toHaveCount(0);
  });

  test("every version decodes and plays at once", async ({ page }) => {
    await loadAudio(page);
    await page.getByRole("button", { name: "Play" }).click();
    await page.waitForTimeout(800);

    const labels = await manifestLabels(page);
    const state = await graphState(page);
    expect(state.concurrentSources).toBe(labels.length);
    expect(state.ctxState).toBe("running");
    // Exactly one version audible, the rest fully muted.
    expect(Object.values(state.gains).filter((g) => g === 1)).toHaveLength(1);
    expect(Object.values(state.gains).filter((g) => g === 0)).toHaveLength(labels.length - 1);
  });

  test("switching is gapless: no restart, no lost position", async ({ page }) => {
    await loadAudio(page);
    await page.getByRole("button", { name: "Play" }).click();
    await page.waitForTimeout(1200);

    const before = await graphState(page);
    const tagsBefore = await tagSources(page);
    const posBefore = await positionText(page);

    await page.getByRole("radio", { name: "Version B" }).click();
    await page.waitForTimeout(100);

    const after = await graphState(page);
    const tagsAfter = await tagSources(page);

    // The same node objects are still playing — nothing was stopped/recreated.
    expect(tagsAfter).toEqual(tagsBefore);
    expect(after.startedAt).toBe(before.startedAt);
    expect(after.gains.B).toBe(1);
    expect(after.gains.A).toBe(0);
    // Position must not jump: comparing the same moment is the entire point.
    expect(await positionText(page)).toBe(posBefore);
  });

  test("the clock does not run while paused", async ({ page }) => {
    await loadAudio(page);
    const first = await positionText(page);
    await page.waitForTimeout(1500);
    expect(await positionText(page)).toBe(first);
    expect(first).toBe("0:00");
  });

  test("nothing in the browser reveals which version is which", async ({ page }) => {
    const seen: string[] = [];
    page.on("request", (r) => seen.push(r.url()));

    await loadAudio(page);
    await page.getByRole("button", { name: "Play" }).click();
    await page.waitForTimeout(500);

    const dom = (await page.content()).toLowerCase();
    const manifest = await page.evaluate(() =>
      fetch("/tests/e2e-fixture.json").then((r) => r.text()),
    );

    for (const name of SOURCE_NAMES) {
      expect(manifest.toLowerCase(), `manifest leaks "${name}"`).not.toContain(name);
      expect(seen.join(" ").toLowerCase(), `a request URL leaks "${name}"`).not.toContain(name);
      // "producer" appears in no user-facing copy; a leak here would un-blind.
      expect(dom, `the DOM leaks "${name}"`).not.toContain(`>${name}<`);
    }
  });

  test("submission is gated until the form is complete", async ({ page }) => {
    const submit = page.getByRole("button", { name: /finish and download/i });
    await expect(submit).toBeDisabled();

    await page.getByLabel(/your name/i).fill("Ana Test");
    await expect(submit).toBeDisabled(); // scores still missing

    for (const label of await manifestLabels(page)) {
      await page.getByRole("radiogroup", { name: `Score for ${label}` })
        .getByRole("radio", { name: "4" })
        .click();
    }
    await expect(submit).toBeEnabled();
  });

  test("the exported CSV matches the schema producer tally reads", async ({ page }) => {
    await page.getByLabel(/your name/i).fill("Ana Test");
    const labels = await manifestLabels(page);
    const scores = Object.fromEntries(labels.map((l, i) => [l, String((i % 5) + 1)]));
    for (const [label, score] of Object.entries(scores)) {
      await page.getByRole("radiogroup", { name: `Score for ${label}` })
        .getByRole("radio", { name: score })
        .click();
    }
    await page.getByPlaceholder(/anything you noticed/i).first().fill("harsh, squashed");

    const download = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: /finish and download/i }).click(),
    ]).then(([d]) => d);

    const stream = await download.createReadStream();
    const csv = await new Promise<string>((resolve, reject) => {
      let out = "";
      stream.on("data", (c) => (out += c));
      stream.on("end", () => resolve(out));
      stream.on("error", reject);
    });

    const lines = csv.trim().split("\n");
    expect(lines[0]).toBe("listener,label,release_ready_1_5,rank,notes");
    expect(lines).toHaveLength(labels.length + 1);
    expect(lines[1]).toContain("Ana Test,A,1,1");
    expect(lines[1]).toContain('"harsh, squashed"');
    // Ranks must be a permutation — producer tally counts first-place votes.
    const ranks = lines.slice(1).map((l) => Number(l.split(",")[3])).sort((a, b) => a - b);
    expect(ranks).toEqual(labels.map((_, i) => i + 1));
  });

  test("reordering the ranking changes the exported ranks", async ({ page }) => {
    await page.getByRole("button", { name: /move c up/i }).click();
    await page.getByRole("button", { name: /move c up/i }).click();

    const labels = await manifestLabels(page);
    const order = await page.locator('[class*="rankLabel"]').allTextContents();
    expect(order.slice(0, 3)).toEqual(["C", "A", "B"]);
    expect(order).toHaveLength(labels.length);
  });
});


test.describe("single version", () => {
  test.beforeEach(async ({ page }) => {
    await useManifest(page, SINGLE_MANIFEST);
    await page.goto("/");
  });

  test("there is nothing to switch between, so no switcher is shown", async ({ page }) => {
    await loadAudio(page);
    await expect(page.getByRole("radio", { name: "Version A" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Play" })).toBeVisible();
  });

  test("the blind-test copy is dropped", async ({ page }) => {
    await expect(page.getByRole("heading", { name: /one track/i })).toBeVisible();
    await expect(page.getByText(/volume-matched/i)).toHaveCount(0);
    await expect(page.getByText(/names are meaningless/i)).toHaveCount(0);
  });

  test("ranking is hidden when there is nothing to rank", async ({ page }) => {
    await expect(page.getByRole("heading", { name: /rank them/i })).toHaveCount(0);
  });

  test("it can still be scored and exported", async ({ page }) => {
    await page.getByLabel(/your name/i).fill("Lakshya");
    await page.getByRole("radiogroup", { name: "Score for A" })
      .getByRole("radio", { name: "4" }).click();

    const download = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: /finish and download/i }).click(),
    ]).then(([d]) => d);

    const stream = await download.createReadStream();
    const csv = await new Promise<string>((resolve, reject) => {
      let out = "";
      stream.on("data", (c) => (out += c));
      stream.on("end", () => resolve(out));
      stream.on("error", reject);
    });
    expect(csv.trim().split("\n")).toHaveLength(2);
    expect(csv).toContain("Lakshya,A,4,1");
  });
});


test.describe("several tests published at once", () => {
  const PUBLISHED = [
    { slug: "one", title: "First song — how loud is the vocal?", blurb: "Three balances.", labels: 3, length_s: 50 },
    { slug: "two", title: "Second song — how deep is the breakdown?", blurb: "Three depths.", labels: 3, length_s: 50 },
  ];

  test("offers a choice rather than guessing", async ({ page }) => {
    await useTestIndex(page, PUBLISHED);
    await page.goto("/");

    await expect(page.getByRole("heading", { name: /two things to listen to/i })).toBeVisible();
    for (const test of PUBLISHED) {
      await expect(page.getByRole("link", { name: new RegExp(test.title, "i") })).toBeVisible();
    }
  });

  test("each choice links to its own test", async ({ page }) => {
    await useTestIndex(page, PUBLISHED);
    await page.goto("/");

    const first = page.getByRole("link").first();
    await expect(first).toHaveAttribute("href", "/?test=one");
  });

  test("a named test loads straight into the player", async ({ page }) => {
    await useManifest(page, { ...FIXTURE_MANIFEST, slug: "one", title: "First song" });
    await page.goto("/?test=one");

    await expect(page.getByRole("heading", { name: /first song/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /load the audio/i })).toBeVisible();
  });

  test("an unknown test says so instead of hanging", async ({ page }) => {
    await page.route("**/tests/*.json", (route) => route.fulfill({ status: 404, body: "" }));
    await page.goto("/?test=nope");

    await expect(page.getByRole("heading", { name: /nothing to listen to/i })).toBeVisible();
    await expect(page.getByText(/nope/)).toBeVisible();
  });
});
