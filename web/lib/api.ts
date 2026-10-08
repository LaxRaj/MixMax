import { NextResponse } from "next/server";

export function bad(message: string, status = 400): NextResponse {
  return NextResponse.json({ error: message }, { status });
}

export async function readBody(request: Request): Promise<Record<string, unknown> | null> {
  try {
    const value = await request.json();
    return value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

/** A person's name as typed: trimmed, single-line, and short. */
export function cleanName(value: unknown): string {
  return String(value ?? "").replace(/\s+/g, " ").trim().slice(0, 60);
}
