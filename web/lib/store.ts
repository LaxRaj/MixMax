import { createReadStream, createWriteStream } from "node:fs";
import { mkdir, readFile, readdir, rename, rm, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";

/**
 * The store the studio and `producer sync` share.
 *
 * Hosted, it is a private Vercel Blob store. Without blob credentials it is a
 * folder on disk — the same folder `producer sync` uses by default — so
 * `npm run dev` and the test suite work with no account and no network.
 *
 * Every write is one object per event. Nothing here appends to a shared file,
 * so two people saving at once cannot overwrite each other.
 */

export type StoreEntry = { path: string; size: number; updated: number };

const ACCESS = "private" as const;

export function isBlob(): boolean {
  if (process.env.MIXMAX_DATA_DIR) return false;
  return Boolean(process.env.BLOB_READ_WRITE_TOKEN || process.env.BLOB_STORE_ID);
}

export function storeMode(): "blob" | "local" {
  return isBlob() ? "blob" : "local";
}

function localRoot(): string {
  // The folder only exists off Vercel, so the bundler must not try to trace it
  // (it would pull the whole project, audio included, into every function).
  return path.resolve(
    /* turbopackIgnore: true */ process.env.MIXMAX_DATA_DIR ||
      path.join(/* turbopackIgnore: true */ process.cwd(), ".data"),
  );
}

/** Store paths are relative and may not climb out of the store. */
export function safePath(input: string): string {
  const parts = input.split("/").filter(Boolean);
  if (parts.length === 0 || parts.some((p) => p === "." || p === ".." || p.includes("\\"))) {
    throw new Error(`Unsafe store path: ${input}`);
  }
  return parts.join("/");
}

function localFile(storePath: string): string {
  return path.join(localRoot(), safePath(storePath));
}

async function walk(dir: string, base: string, out: StoreEntry[]): Promise<void> {
  let names: string[];
  try {
    names = await readdir(dir);
  } catch {
    return;
  }
  for (const name of names.sort()) {
    if (name.startsWith(".") || name.endsWith(".tmp")) continue;
    const full = path.join(dir, name);
    const info = await stat(full).catch(() => null);
    if (!info) continue;
    if (info.isDirectory()) await walk(full, `${base}${name}/`, out);
    else out.push({ path: `${base}${name}`, size: info.size, updated: info.mtimeMs });
  }
}

export async function list(prefix: string): Promise<StoreEntry[]> {
  const clean = prefix.replace(/^\/+/, "");
  if (isBlob()) {
    const blob = await import("@vercel/blob");
    const found: StoreEntry[] = [];
    let cursor: string | undefined;
    do {
      const page = await blob.list({ prefix: clean, cursor, limit: 1000 });
      for (const b of page.blobs) {
        found.push({ path: b.pathname, size: b.size, updated: new Date(b.uploadedAt).getTime() });
      }
      cursor = page.hasMore ? page.cursor : undefined;
    } while (cursor);
    return found;
  }
  const out: StoreEntry[] = [];
  const dir = clean ? path.join(localRoot(), safePath(clean)) : localRoot();
  await walk(dir, clean ? `${safePath(clean)}/` : "", out);
  return out;
}

export async function getJson<T>(storePath: string): Promise<T | null> {
  try {
    if (isBlob()) {
      const blob = await import("@vercel/blob");
      // What was just saved must be what is read back, not a cached copy.
      const found = await blob.get(safePath(storePath), { access: ACCESS, useCache: false });
      if (!found || found.statusCode !== 200) return null;
      return (await new Response(found.stream).json()) as T;
    }
    return JSON.parse(await readFile(localFile(storePath), "utf8")) as T;
  } catch {
    return null;
  }
}

export async function putJson(storePath: string, value: unknown): Promise<void> {
  const body = JSON.stringify(value, null, 2) + "\n";
  if (isBlob()) {
    const blob = await import("@vercel/blob");
    await blob.put(safePath(storePath), body, {
      access: ACCESS,
      contentType: "application/json",
      addRandomSuffix: false,
      allowOverwrite: true,
    });
    return;
  }
  const file = localFile(storePath);
  await mkdir(path.dirname(file), { recursive: true });
  // Write beside the target and rename, so sync never reads half a file.
  await writeFile(`${file}.tmp`, body);
  await rename(`${file}.tmp`, file);
}

export async function remove(storePath: string): Promise<void> {
  if (isBlob()) {
    const blob = await import("@vercel/blob");
    await blob.del(safePath(storePath));
    return;
  }
  await rm(localFile(storePath), { force: true });
}

/** Local mode only: stream an upload body straight to disk. */
export async function writeLocalStream(
  storePath: string,
  body: ReadableStream<Uint8Array>,
): Promise<number> {
  const file = localFile(storePath);
  await mkdir(path.dirname(file), { recursive: true });
  await pipeline(Readable.fromWeb(body as never), createWriteStream(`${file}.tmp`));
  await rename(`${file}.tmp`, file);
  return (await stat(file)).size;
}

/** Local mode only: a file's size and a way to read a byte range of it. */
export async function openLocal(
  storePath: string,
): Promise<{ size: number; read: (start: number, end: number) => ReadableStream<Uint8Array> } | null> {
  const file = localFile(storePath);
  const info = await stat(file).catch(() => null);
  if (!info || !info.isFile()) return null;
  return {
    size: info.size,
    read: (start, end) =>
      Readable.toWeb(createReadStream(file, { start, end })) as unknown as ReadableStream<Uint8Array>,
  };
}

/** Blob mode only: a short-lived URL the browser can stream and seek directly. */
export async function presignedRead(storePath: string, validForMs: number): Promise<string> {
  const blob = await import("@vercel/blob");
  const pathname = safePath(storePath);
  const validUntil = Date.now() + validForMs;
  const token = await blob.issueSignedToken({ pathname, operations: ["get"], validUntil });
  const { presignedUrl } = await blob.presignUrl(token, {
    access: ACCESS,
    operation: "get",
    pathname,
    validUntil,
  });
  return presignedUrl;
}

/** Read every JSON object under a prefix, in parallel. */
export async function readAll<T>(prefix: string): Promise<{ path: string; value: T }[]> {
  const entries = (await list(prefix)).filter((e) => e.path.endsWith(".json"));
  const values = await Promise.all(entries.map((e) => getJson<T>(e.path)));
  return entries.flatMap((e, i) => (values[i] ? [{ path: e.path, value: values[i] as T }] : []));
}
