import { NextResponse } from "next/server";
import type { Heartbeat, SongSummary } from "@/lib/song";
import { getJson, list } from "@/lib/store";

export const dynamic = "force-dynamic";

export async function GET() {
  const [index, heartbeat, feedback, requests] = await Promise.all([
    getJson<{ songs: Omit<SongSummary, "notes" | "pending">[] }>("songs/index.json"),
    getJson<NonNullable<Heartbeat>>("sync/heartbeat.json"),
    list("feedback/"),
    list("requests/"),
  ]);

  const notes = new Map<string, number>();
  for (const entry of feedback) {
    const slug = entry.path.split("/")[1];
    notes.set(slug, (notes.get(slug) ?? 0) + 1);
  }
  // A request is pending until sync writes its result beside it.
  const answered = new Set(
    requests.filter((e) => e.path.endsWith(".result.json")).map((e) => e.path.replace(".result.json", ".json")),
  );
  const pending = new Map<string, number>();
  for (const entry of requests) {
    if (entry.path.endsWith(".result.json") || answered.has(entry.path)) continue;
    const slug = entry.path.split("/")[1];
    pending.set(slug, (pending.get(slug) ?? 0) + 1);
  }

  const songs: SongSummary[] = (index?.songs ?? []).map((song) => ({
    ...song,
    notes: notes.get(song.slug) ?? 0,
    pending: pending.get(song.slug) ?? 0,
  }));
  return NextResponse.json({ songs, heartbeat, published: index !== null });
}
