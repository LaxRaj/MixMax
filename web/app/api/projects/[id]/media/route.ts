import { NextResponse } from "next/server";
import { handleUpload, type HandleUploadBody } from "@vercel/blob/client";
import { bad } from "@/lib/api";
import { MEDIA_EXTENSIONS, MEDIA_PATH, PROJECT_ID } from "@/lib/daw/types";
import { ID } from "@/lib/song";
import { getJson, isBlob, writeLocalStream } from "@/lib/store";

const MAX_BYTES = 500 * 1024 * 1024;

type Params = { params: Promise<{ id: string }> };

async function exists(id: string): Promise<boolean> {
  return PROJECT_ID.test(id) && (await getJson(`projects/${id}/project.json`)) !== null;
}

/**
 * Audio imported or recorded into a project. It is working material for the
 * workstation and never reaches the pipeline from here: `producer sync` only
 * looks under `uploads/`, and only a bounce sent from the export dialog goes there.
 *
 * Hosted, the bytes go straight to Blob with a short-lived token, exactly as
 * song uploads do. See `/api/upload` for why.
 */
export async function POST(request: Request, { params }: Params) {
  const { id } = await params;
  if (!isBlob()) return bad("Direct uploads are only used when hosted.", 404);
  if (!(await exists(id))) return bad("No such project.", 404);
  const body = (await request.json()) as HandleUploadBody;
  try {
    const result = await handleUpload({
      body,
      request,
      onBeforeGenerateToken: async (pathname) => {
        if (!MEDIA_PATH.test(pathname) || !pathname.startsWith(`projects/${id}/media/`)) {
          throw new Error("Project audio goes to projects/<id>/media/<id>.<ext>.");
        }
        return { maximumSizeInBytes: MAX_BYTES, addRandomSuffix: false, allowOverwrite: false };
      },
    });
    return NextResponse.json(result);
  } catch (error) {
    return bad((error as Error).message);
  }
}

/** Local development: stream the body to the data folder. */
export async function PUT(request: Request, { params }: Params) {
  const { id } = await params;
  if (isBlob()) return bad("Use the direct upload when hosted.", 404);
  if (!(await exists(id))) return bad("No such project.", 404);
  const url = new URL(request.url);
  const media = url.searchParams.get("media") ?? "";
  const ext = (url.searchParams.get("ext") ?? "").toLowerCase();
  if (!ID.test(media) || !MEDIA_EXTENSIONS.includes(ext)) return bad("Bad media address.");
  if (!request.body) return bad("No file was sent.");
  const size = await writeLocalStream(`projects/${id}/media/${media}.${ext}`, request.body);
  if (size === 0) return bad("The file arrived empty.", 422);
  return NextResponse.json({ ok: true, size });
}
