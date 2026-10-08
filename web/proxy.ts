import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { SESSION_COOKIE, isAuthorized, misconfigured } from "@/lib/auth";

/**
 * The studio is behind the passcode; a blind listening test is not.
 *
 * `/listen` and the files it loads stay open links, because the whole point of
 * that page is that a friend opens it with no setup. Everything else — songs,
 * uploads, the pipeline, and every API route — needs the session cookie.
 */
const OPEN = [
  /^\/listen(\/|$)/,
  /^\/login(\/|$)/,
  /^\/api\/login$/,
  /^\/tests\//,      // blind-test manifests: labels only, by construction
  /^\/audio\//,      // blind-test audio
  /^\/fixtures\//,
];

export async function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  if (OPEN.some((pattern) => pattern.test(pathname))) return NextResponse.next();

  // An already-shared blind-test link used to live at the root.
  if (pathname === "/" && request.nextUrl.searchParams.has("test")) {
    return NextResponse.redirect(new URL(`/listen${search}`, request.url));
  }

  if (misconfigured()) {
    return new NextResponse(
      "This deployment has no MIXMAX_PASSCODE set, so the studio is closed. " +
        "Set it in the project's environment variables and redeploy.",
      { status: 503 },
    );
  }

  if (await isAuthorized(request.cookies.get(SESSION_COOKIE)?.value)) return NextResponse.next();

  if (pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "Sign in first." }, { status: 401 });
  }
  const login = new URL("/login", request.url);
  login.searchParams.set("next", `${pathname}${search}`);
  return NextResponse.redirect(login);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
