import { NextResponse } from "next/server";
import { bad, cleanName, readBody } from "@/lib/api";
import { ID, SLUG, type Note, type NoteTarget } from "@/lib/song";
import { getJson, putJson, remove } from "@/lib/store";

const MAX_NOTE = 4000;

function cleanTarget(value: unknown): NoteTarget | null {
  const target = value as Record<string, unknown> | null;
  if (!target || typeof target !== "object") return null;
  if (target.kind === "song") return { kind: "song" };
  const component = String(target.component ?? "");
  if (!/^[a-z_]{1,40}$/.test(component)) return null;
  if (target.kind === "component") return { kind: "component", component };
  if (target.kind === "section") {
    const start = Number(target.start_s);
    const end = Number(target.end_s);
    const index = Number(target.index);
    if (![start, end, index].every(Number.isFinite)) return null;
    return {
      kind: "section",
      component,
      label: String(target.label ?? "?").slice(0, 8),
      index,
      start_s: start,
      end_s: end,
    };
  }
  return null;
}

/**
 * Save one note. The same id is saved again as the note is edited, so a note
 * being typed is one object rewritten, not a trail of drafts. An empty note
 * deletes it.
 */
export async function POST(request: Request) {
  const body = await readBody(request);
  if (!body) return bad("Expected a JSON body.");

  const slug = String(body.slug ?? "");
  const id = String(body.id ?? "");
  if (!SLUG.test(slug) || !ID.test(id)) return bad("Bad note address.");
  if (!(await getJson(`songs/${slug}/song.json`))) return bad("No such song.", 404);

  const path = `feedback/${slug}/${id}.json`;
  const text = String(body.text ?? "").slice(0, MAX_NOTE);
  if (!text.trim()) {
    await remove(path);
    return NextResponse.json({ ok: true, deleted: true });
  }

  const author = cleanName(body.author);
  if (!author) return bad("Say who you are before leaving a note.");
  const target = cleanTarget(body.target);
  if (!target) return bad("A note has to be about the song, a component or a section.");

  const existing = await getJson<Note>(path);
  const now = new Date().toISOString();
  const settings: Record<string, number> = {};
  for (const [key, value] of Object.entries((body.settings_changed as object) ?? {})) {
    if (/^[a-z_]{1,40}$/.test(key) && typeof value === "number" && Number.isFinite(value)) {
      settings[key] = value;
    }
  }

  const note: Note = {
    id,
    slug,
    target: existing?.target ?? target,
    author: existing?.author ?? author,
    text,
    created_at: existing?.created_at ?? now,
    updated_at: now,
    settings_changed: existing?.settings_changed ?? settings,
    song_version: existing?.song_version ?? String(body.song_version ?? "").slice(0, 40),
  };
  await putJson(path, note);
  return NextResponse.json({ ok: true, note });
}
