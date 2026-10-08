import { NextResponse } from "next/server";
import { bad, cleanName, readBody } from "@/lib/api";
import {
  ID,
  UPLOAD_EXTENSIONS,
  type Heartbeat,
  type Upload,
  type UploadKind,
  type UploadMeta,
  type UploadReport,
} from "@/lib/song";
import { getJson, list, putJson, readAll, storeMode } from "@/lib/store";

export const dynamic = "force-dynamic";

const KINDS: UploadKind[] = ["vocal", "beat", "reference", "other"];

/** Everything uploaded, newest first, each with the check's verdict once sync has run. */
export async function GET() {
  const [objects, heartbeat] = await Promise.all([
    readAll<UploadMeta | UploadReport>("uploads/"),
    getJson<NonNullable<Heartbeat>>("sync/heartbeat.json"),
  ]);
  const uploads = new Map<string, Upload>();
  const reports = new Map<string, UploadReport>();
  for (const { path, value } of objects) {
    const [, id, name] = path.split("/");
    if (name === "meta.json") uploads.set(id, value as UploadMeta);
    if (name === "report.json") reports.set(id, value as UploadReport);
  }
  for (const [id, report] of reports) {
    const upload = uploads.get(id);
    if (upload) upload.report = report;
  }
  return NextResponse.json({
    uploads: [...uploads.values()].sort((a, b) => b.uploaded_at.localeCompare(a.uploaded_at)),
    heartbeat,
    mode: storeMode(),
  });
}

/**
 * Register a file that has finished uploading. The meta object is written
 * last, on purpose: sync ignores a file with no meta, so a half-finished
 * upload is never checked.
 */
export async function POST(request: Request) {
  const body = await readBody(request);
  if (!body) return bad("Expected a JSON body.");

  const id = String(body.id ?? "");
  if (!ID.test(id)) return bad("Bad upload id.");
  const filename = String(body.filename ?? "").replace(/[\\/]/g, "_").slice(0, 200);
  const extension = filename.includes(".") ? filename.split(".").pop()!.toLowerCase() : "";
  const kind = KINDS.includes(body.kind as UploadKind) ? (body.kind as UploadKind) : null;
  if (!filename || !kind) return bad("An upload needs a file name and a kind.");
  if (kind !== "other" && !UPLOAD_EXTENSIONS.includes(extension)) {
    return bad(`.${extension || "?"} is not an audio format the pipeline reads.`);
  }
  const song = String(body.song ?? "").trim().slice(0, 80);
  if (!song) return bad("Say which song this is for.");
  const uploader = cleanName(body.uploader);
  if (!uploader) return bad("Say who you are before uploading.");

  const stored = (await list(`uploads/${id}/`)).find((e) => e.path.startsWith(`uploads/${id}/file.`));
  if (!stored) return bad("The file did not arrive. Try the upload again.", 409);
  if (stored.size === 0) return bad("The file arrived empty.", 422);

  const meta: UploadMeta = {
    id,
    filename,
    size: stored.size,
    kind,
    song,
    song_title: String(body.song_title ?? "").trim().slice(0, 80) || undefined,
    replaces: body.replaces === true,
    uploader,
    uploaded_at: new Date().toISOString(),
    client_warnings: Array.isArray(body.client_warnings)
      ? body.client_warnings.map((w) => String(w).slice(0, 300)).slice(0, 10)
      : undefined,
  };
  await putJson(`uploads/${id}/meta.json`, meta);
  return NextResponse.json({ ok: true, upload: meta });
}
