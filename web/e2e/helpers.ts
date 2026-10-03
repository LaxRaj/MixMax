import type { Page } from "@playwright/test";

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

/** Labels the current manifest declares, so tests do not hard-code a count. */
export async function manifestLabels(page: Page): Promise<string[]> {
  return page.evaluate(() => fetch("/test.json").then((r) => r.json()).then((m) => m.labels));
}

export async function loadAudio(page: Page): Promise<void> {
  await page.getByRole("button", { name: /load the audio/i }).click();
  await page.getByRole("radio", { name: "Version A" }).waitFor({ state: "visible", timeout: 45_000 });
}
