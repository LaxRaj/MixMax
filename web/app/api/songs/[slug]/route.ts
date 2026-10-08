import { NextResponse } from "next/server";
import { bad } from "@/lib/api";
import {
  SLUG,
  type Heartbeat,
  type Note,
  type RequestResult,
  type SettingsRequest,
  type Song,
} from "@/lib/song";
import { getJson, readAll } from "@/lib/store";

export const dynamic = "force-dynamic";

export async function GET(_request: Request, { params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  if (!SLUG.test(slug)) return bad("No such song.", 404);

  const [song, heartbeat, notes, requestObjects] = await Promise.all([
    getJson<Song>(`songs/${slug}/song.json`),
    getJson<NonNullable<Heartbeat>>("sync/heartbeat.json"),
    readAll<Note>(`feedback/${slug}/`),
    readAll<SettingsRequest | RequestResult>(`requests/${slug}/`),
  ]);
  if (!song) return bad("No such song.", 404);

  const results = new Map<string, RequestResult>();
  for (const { path, value } of requestObjects) {
    if (path.endsWith(".result.json")) {
      results.set(path.replace(".result.json", ".json"), value as RequestResult);
    }
  }
  const requests = requestObjects
    .filter(({ path }) => !path.endsWith(".result.json"))
    .map(({ path, value }) => ({ ...(value as SettingsRequest), result: results.get(path) }))
    .sort((a, b) => b.created_at.localeCompare(a.created_at));

  return NextResponse.json({
    song,
    heartbeat,
    notes: notes.map((n) => n.value).sort((a, b) => b.created_at.localeCompare(a.created_at)),
    requests,
  });
}
