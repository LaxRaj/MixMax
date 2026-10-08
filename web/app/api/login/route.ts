import { NextResponse } from "next/server";
import { bad, readBody } from "@/lib/api";
import { SESSION_COOKIE, SESSION_MAX_AGE_S, passcode, sameString, sessionToken } from "@/lib/auth";

export async function POST(request: Request) {
  const code = passcode();
  if (code === null) return NextResponse.json({ ok: true, open: true });

  const body = await readBody(request);
  const given = String(body?.passcode ?? "").trim();
  if (!sameString(await sessionToken(given), await sessionToken(code))) {
    return bad("That passcode is not right.", 401);
  }

  const response = NextResponse.json({ ok: true });
  response.cookies.set(SESSION_COOKIE, await sessionToken(code), {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: SESSION_MAX_AGE_S,
  });
  return response;
}
