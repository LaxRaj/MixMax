import type { Page } from "@playwright/test";

/**
 * The suite serves its own manifest rather than whatever happens to be
 * published. A test that breaks because a different song went live is testing
 * the content, not the player.
 */
export const FIXTURE_MANIFEST = {
  slug: "e2e-fixture",
  labels: ["A", "B", "C"],
  urls: { A: "/fixtures/x.m4a", B: "/fixtures/y.m4a", C: "/fixtures/z.m4a" },
  excerpt: { start_s: 0, length_s: 6 },
};

export const SINGLE_MANIFEST = {
  slug: "e2e-single",
  title: "One track",
  labels: ["A"],
  urls: { A: "/fixtures/x.m4a" },
  excerpt: { start_s: 0, length_s: 6 },
};

/**
 * Pin the manifest the page will load. Call before `goto`.
 *
 * The page resolves a test from `?test=<slug>` via /tests/<slug>.json, or
 * offers /tests/index.json when no slug is given. Both are stubbed so a test
 * never depends on what happens to be published.
 */
export async function useManifest(
  page: Page,
  manifest: { slug?: string } & Record<string, unknown>,
): Promise<void> {
  const slug = (manifest.slug as string) ?? "e2e";
  const json = (body: unknown) => ({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  // One handler for both, because Playwright matches routes in reverse
  // registration order: a broad `**/tests/*.json` glob registered second
  // swallows index.json and hands the page a manifest where it expects a list.
  await page.route("**/tests/*.json", (route) => {
    const isIndex = route.request().url().endsWith("/index.json");
    route.fulfill(
      isIndex
        ? json({ tests: [{ slug, title: "E2E", blurb: "", labels: 3, length_s: 6 }] })
        : json(manifest),
    );
  });
}

/** Publish several tests, so the page has to offer a choice. */
export async function useTestIndex(
  page: Page,
  tests: { slug: string; title: string; blurb: string; labels: number; length_s: number }[],
): Promise<void> {
  await page.route("**/tests/index.json", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ tests }),
    }),
  );
}

/** Source names that must never reach the browser. */
export const SOURCE_NAMES = ["producer", "landr", "original", "emastered"];

export type GraphState = {
  concurrentSources: number;
  gains: Record<string, number>;
  startedAt: number;
  ctxState: string;
};

/**
 * The player exposes its audio graph on window in non-production builds, so
 * the gapless guarantee can be asserted rather than eyeballed.
 */
export async function graphState(page: Page): Promise<GraphState> {
  return page.evaluate(() => {
    const p = (window as never as { __player: never }).__player as unknown as {
      ctx: AudioContext;
      gains: Map<string, GainNode>;
      sources: Map<string, AudioBufferSourceNode>;
      startedAt: () => number;
    };
    return {
      concurrentSources: p.sources.size,
      gains: Object.fromEntries([...p.gains].map(([k, g]) => [k, Number(g.gain.value.toFixed(3))])),
      startedAt: p.startedAt(),
      ctxState: p.ctx.state,
    };
  });
}

/** Identity of the live source nodes, to prove a switch did not restart them. */
export async function tagSources(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const p = (window as never as { __player: never }).__player as unknown as {
      sources: Map<string, AudioBufferSourceNode>;
    };
    let n = 0;
    return [...p.sources.values()].map((s) => {
      const node = s as AudioBufferSourceNode & { __tag?: string };
      if (!node.__tag) node.__tag = `src-${Date.now()}-${n++}`;
      return node.__tag;
    });
  });
}

export async function positionText(page: Page): Promise<string> {
  const text = await page.locator('[class*="readout"]').first().textContent();
  return (text ?? "").split("loops")[0].trim();
}

/**
 * Labels the page is actually showing, so tests do not hard-code a count.
 *
 * Read from the DOM rather than refetched: the manifest path has moved once
 * already, and a helper that refetches breaks every time it does.
 */
export async function manifestLabels(page: Page): Promise<string[]> {
  const groups = page.locator('[role="radiogroup"][aria-label^="Score for "]');
  await groups.first().waitFor({ state: "attached", timeout: 15_000 });
  const names = await groups.evaluateAll((nodes) =>
    nodes.map((n) => (n.getAttribute("aria-label") ?? "").replace("Score for ", "")),
  );
  return names.filter(Boolean);
}

export async function loadAudio(page: Page): Promise<void> {
  await page.getByRole("button", { name: /load the audio/i }).click();
  // Single-version pages have no switcher, so wait on the transport instead.
  await page.getByRole("button", { name: "Play" }).waitFor({ state: "visible", timeout: 45_000 });
}
