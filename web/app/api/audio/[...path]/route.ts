import { NextResponse } from "next/server";
import { bad } from "@/lib/api";
import { isBlob, openLocal, presignedRead } from "@/lib/store";

export const dynamic = "force-dynamic";

// Only published song audio is served. Uploads and notes are not reachable here.
const ALLOWED = /^songs\/[a-z0-9][a-z0-9-]*\/audio\/[a-z_]+\.[a-f0-9]{6,40}\.m4a$/;

/**
 * Song audio, for someone already past the passcode (the proxy checked).
 *
 * The file name carries a hash of its content, so a given URL never changes
 * what it plays and can be cached hard; a re-render publishes a new name.
 */
export async function GET(request: Request, { params }: { params: Promise<{ path: string[] }> }) {
  const storePath = (await params).path.join("/");
  if (!ALLOWED.test(storePath)) return bad("Not found.", 404);

  if (isBlob()) {
    // Seeking needs byte ranges, which the blob host serves and a function
    // streaming the body would have to reimplement. Hand over a short-lived URL.
    const url = await presignedRead(storePath, 60 * 60 * 1000);
    return NextResponse.redirect(url, {
      status: 302,
      headers: { "Cache-Control": "private, max-age=1800" },
    });
  }

  const file = await openLocal(storePath);
  if (!file) return bad("Not found.", 404);

  const headers = new Headers({
    "Content-Type": "audio/mp4",
    "Accept-Ranges": "bytes",
    "Cache-Control": "private, max-age=31536000, immutable",
  });
  const range = /^bytes=(\d*)-(\d*)$/.exec(request.headers.get("range") ?? "");
  if (!range || (range[1] === "" && range[2] === "")) {
    headers.set("Content-Length", String(file.size));
    return new Response(file.read(0, Math.max(file.size - 1, 0)), { headers });
  }

  const last = file.size - 1;
  const start = range[1] === "" ? Math.max(file.size - Number(range[2]), 0) : Number(range[1]);
  const end = range[1] === "" || range[2] === "" ? last : Math.min(Number(range[2]), last);
  if (start > end || start > last) {
    return new Response(null, { status: 416, headers: { "Content-Range": `bytes */${file.size}` } });
  }
  headers.set("Content-Range", `bytes ${start}-${end}/${file.size}`);
  headers.set("Content-Length", String(end - start + 1));
  return new Response(file.read(start, end), { status: 206, headers });
}
