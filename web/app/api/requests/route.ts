import { NextResponse } from "next/server";
import { bad, cleanName, readBody } from "@/lib/api";
import { SLUG, newId, type SettingsRequest, type Song } from "@/lib/song";
import { getJson, putJson } from "@/lib/store";

/**
 * Queue a settings change. Nothing is rendered here: this records what was
 * asked for, and `producer sync` validates it again and does the work.
 */
export async function POST(request: Request) {
  const body = await readBody(request);
  if (!body) return bad("Expected a JSON body.");

  const slug = String(body.slug ?? "");
  if (!SLUG.test(slug)) return bad("No such song.", 404);
  const song = await getJson<Song>(`songs/${slug}/song.json`);
  if (!song) return bad("No such song.", 404);

  const author = cleanName(body.author);
  if (!author) return bad("Say who you are before changing settings.");

  const fields = new Map(
    song.settings.flatMap((group) => group.fields.map((field) => [field.key, { field, group }] as const)),
  );
  const changes: Record<string, number> = {};
  for (const [key, raw] of Object.entries((body.changes as object) ?? {})) {
    const known = fields.get(key);
    if (!known) return bad(`"${key}" is not a setting.`);
    if (!known.group.applies) return bad(known.group.why_not || `${known.group.label} does not apply here.`);
    const value = Number(raw);
    if (!Number.isFinite(value)) return bad(`${known.field.label} must be a number.`);
    if (value < known.field.min || value > known.field.max) {
      return bad(
        `${known.field.label} must be between ${known.field.min} and ${known.field.max}` +
          `${known.field.unit ? ` ${known.field.unit}` : ""}.`,
      );
    }
    changes[key] = value;
  }
  if (Object.keys(changes).length === 0) return bad("Nothing was changed.");

  const queued: SettingsRequest = {
    id: newId(),
    slug,
    author,
    created_at: new Date().toISOString(),
    changes,
    note: String(body.note ?? "").slice(0, 500) || undefined,
  };
  await putJson(`requests/${slug}/${queued.id}.json`, queued);
  return NextResponse.json({ ok: true, request: queued });
}
