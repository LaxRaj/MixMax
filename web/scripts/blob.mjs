#!/usr/bin/env node
// Blob access for `producer sync`. Python calls this as a subprocess, so there
// is one implementation of talking to Vercel Blob rather than a second,
// hand-rolled REST client on the Python side.
//
//   blob.mjs list <prefix>                  -> JSON [{path, stamp, size}]
//   blob.mjs get  <path> <outfile>          -> exit 3 when the object is missing
//   blob.mjs put  <path> <file> <type>
//   blob.mjs del  <path>

import { createWriteStream, existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import { del, get, list, put } from "@vercel/blob";

// `vercel env pull` writes credentials here; an already-set variable wins.
if (existsSync(".env.local")) process.loadEnvFile(".env.local");

const ACCESS = "private";
const [command, ...args] = process.argv.slice(2);

async function main() {
  if (command === "list") {
    const rows = [];
    let cursor;
    do {
      const page = await list({ prefix: args[0] ?? "", cursor, limit: 1000 });
      for (const blob of page.blobs) {
        rows.push({
          path: blob.pathname,
          // Size alone misses a same-length rewrite; the upload time does not.
          stamp: `${new Date(blob.uploadedAt).getTime()}-${blob.size}`,
          size: blob.size,
        });
      }
      cursor = page.hasMore ? page.cursor : undefined;
    } while (cursor);
    process.stdout.write(JSON.stringify(rows));
    return 0;
  }

  if (command === "get") {
    // Sync must see what was just written, not a CDN copy of what was there.
    const found = await get(args[0], { access: ACCESS, useCache: false });
    if (!found || found.statusCode !== 200) return 3;
    await pipeline(Readable.fromWeb(found.stream), createWriteStream(args[1]));
    return 0;
  }

  if (command === "put") {
    await put(args[0], await readFile(args[1]), {
      access: ACCESS,
      contentType: args[2] ?? "application/octet-stream",
      addRandomSuffix: false,
      allowOverwrite: true,
      multipart: true,
    });
    return 0;
  }

  if (command === "del") {
    await del(args[0]);
    return 0;
  }

  console.error(`Unknown command: ${command ?? "(none)"}`);
  return 2;
}

main().then(
  (code) => process.exit(code),
  (error) => {
    console.error(error?.message ?? String(error));
    process.exit(1);
  },
);
