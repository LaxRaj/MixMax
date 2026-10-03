export type Excerpt = { start_s: number; length_s: number };

export type Manifest = {
  slug: string;
  title?: string;
  labels: string[];
  urls: Record<string, string>;
  excerpt?: Excerpt;
};

/**
 * The manifest is deliberately label-only. It must never carry a source name
 * ("producer", "landr"), because anything shipped to the browser is visible in
 * the network tab and would un-blind the test.
 */
export function assertBlind(manifest: Manifest): void {
  const serialized = JSON.stringify(manifest).toLowerCase();
  for (const label of manifest.labels) {
    if (!manifest.urls[label]) throw new Error(`Manifest is missing audio for ${label}`);
  }
  const leaked = ["producer", "landr", "original", "emastered"].filter((name) =>
    serialized.includes(name),
  );
  if (leaked.length > 0) {
    throw new Error(`Manifest leaks source names: ${leaked.join(", ")}`);
  }
}
