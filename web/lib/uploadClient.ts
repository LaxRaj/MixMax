import { UPLOAD_EXTENSIONS, newId, type UploadKind, type UploadMeta } from "@/lib/song";

/**
 * What the browser can tell about a file before spending the upload.
 *
 * These are the cheap, certain problems. Everything that takes real
 * measurement — clipping, noise floor, silence — is the intake gate's job on
 * the studio Mac, and is reported after the next sync.
 */
export type ClientCheck = { blockers: string[]; warnings: string[]; duration_s: number | null };

const MAX_BYTES = 500 * 1024 * 1024;
const DECODE_UP_TO = 80 * 1024 * 1024;
const MIN_SECONDS = 2;

export function extensionOf(name: string): string {
  return name.includes(".") ? name.split(".").pop()!.toLowerCase() : "";
}

export async function checkFile(file: File, kind: UploadKind): Promise<ClientCheck> {
  const check: ClientCheck = { blockers: [], warnings: [], duration_s: null };
  const ext = extensionOf(file.name);

  if (file.size === 0) {
    check.blockers.push("The file is empty (0 bytes).");
    return check;
  }
  if (file.size > MAX_BYTES) {
    check.blockers.push("Larger than 500 MB. Send a shorter or compressed version.");
    return check;
  }
  if (kind === "other") return check;
  if (!UPLOAD_EXTENSIONS.includes(ext)) {
    check.blockers.push(
      `.${ext || "?"} is not audio the pipeline reads. Use WAV, AIFF, FLAC, MP3 or M4A — or mark it “Something else”.`,
    );
    return check;
  }
  if (file.size > DECODE_UP_TO) return check;

  try {
    const Ctx = window.AudioContext ?? (window as never as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const ctx = new Ctx();
    try {
      const decoded = await ctx.decodeAudioData(await file.arrayBuffer());
      check.duration_s = decoded.duration;
      if (decoded.duration < MIN_SECONDS) {
        check.blockers.push(
          `Only ${decoded.duration.toFixed(1)}s long — too short for anything downstream to work with.`,
        );
      }
    } finally {
      void ctx.close();
    }
  } catch {
    // Browsers disagree about AIFF, FLAC and CAF, so this is not proof the file is broken.
    check.warnings.push(
      "This browser could not open it to check. It will still be checked properly on the studio Mac.",
    );
  }
  return check;
}

export type UploadRequest = {
  file: File;
  kind: UploadKind;
  song: string;
  song_title?: string;
  replaces: boolean;
  uploader: string;
  client_warnings?: string[];
};

function putLocal(file: File, id: string, ext: string, onProgress: (fraction: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `/api/upload?id=${id}&ext=${ext}`);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    xhr.onload = () =>
      xhr.status >= 200 && xhr.status < 300
        ? resolve()
        : reject(new Error(`Upload failed (${xhr.status}).`));
    xhr.onerror = () => reject(new Error("The connection dropped during the upload."));
    xhr.send(file);
  });
}

/** Send the bytes, then register the upload. The registration is what sync looks for. */
export async function sendUpload(
  request: UploadRequest,
  mode: "blob" | "local",
  onProgress: (fraction: number) => void,
): Promise<UploadMeta> {
  const id = newId();
  const ext = extensionOf(request.file.name) || "bin";

  if (mode === "blob") {
    const { upload } = await import("@vercel/blob/client");
    await upload(`uploads/${id}/file.${ext}`, request.file, {
      access: "private",
      handleUploadUrl: "/api/upload",
      multipart: request.file.size > 8 * 1024 * 1024,
      onUploadProgress: (event) => onProgress(event.percentage / 100),
    });
  } else {
    await putLocal(request.file, id, ext, onProgress);
  }

  const response = await fetch("/api/uploads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      id,
      filename: request.file.name,
      kind: request.kind,
      song: request.song,
      song_title: request.song_title,
      replaces: request.replaces,
      uploader: request.uploader,
      client_warnings: request.client_warnings,
    }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error ?? `Could not register the upload (${response.status}).`);
  return data.upload as UploadMeta;
}
