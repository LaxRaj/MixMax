import { NextResponse } from "next/server";
import { handleUpload, type HandleUploadBody } from "@vercel/blob/client";
import { bad } from "@/lib/api";
import { ID } from "@/lib/song";
import { isBlob, writeLocalStream } from "@/lib/store";

const MAX_BYTES = 500 * 1024 * 1024;
const FILE_PATH = /^uploads\/([a-z0-9][a-z0-9-]{5,60})\/file\.[a-z0-9]{1,8}$/;

/**
 * Hosted: hand the browser a short-lived token so the file goes straight to
 * Blob. A function body is capped at 4.5 MB and a WAV is ten times that, so
 * the bytes must never pass through here.
 */
export async function POST(request: Request) {
  if (!isBlob()) return bad("Direct uploads are only used when hosted.", 404);
  const body = (await request.json()) as HandleUploadBody;
  try {
    const result = await handleUpload({
      body,
      request,
      onBeforeGenerateToken: async (pathname) => {
        if (!FILE_PATH.test(pathname)) throw new Error("Uploads go to uploads/<id>/file.<ext>.");
        return { maximumSizeInBytes: MAX_BYTES, addRandomSuffix: false, allowOverwrite: true };
      },
    });
    return NextResponse.json(result);
  } catch (error) {
    return bad((error as Error).message);
  }
}

/** Local development: there is no Blob, so the body is streamed to the data folder. */
export async function PUT(request: Request) {
  if (isBlob()) return bad("Use the direct upload when hosted.", 404);
  const url = new URL(request.url);
  const id = url.searchParams.get("id") ?? "";
  const ext = (url.searchParams.get("ext") ?? "").toLowerCase();
  if (!ID.test(id) || !/^[a-z0-9]{1,8}$/.test(ext)) return bad("Bad upload address.");
  if (!request.body) return bad("No file was sent.");
  const size = await writeLocalStream(`uploads/${id}/file.${ext}`, request.body);
  return NextResponse.json({ ok: true, size });
}
