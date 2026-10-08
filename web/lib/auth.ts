/**
 * One shared passcode, no accounts.
 *
 * The studio lets someone upload files and request renders, so it is not an
 * open link the way a blind test is. A passcode in `MIXMAX_PASSCODE` becomes a
 * signed cookie. This file is imported by `proxy.ts`, so it uses only Web
 * Crypto and nothing from Node.
 */

export const SESSION_COOKIE = "mixmax_session";
export const SESSION_MAX_AGE_S = 60 * 60 * 24 * 90;

export function passcode(): string | null {
  const value = process.env.MIXMAX_PASSCODE?.trim();
  return value ? value : null;
}

/**
 * Deployed with no passcode set is a mistake, not an open door: refuse rather
 * than publish someone's unreleased songs. Local development stays open.
 */
export function misconfigured(): boolean {
  return passcode() === null && Boolean(process.env.VERCEL);
}

async function hmacHex(key: string, message: string): Promise<string> {
  const encoder = new TextEncoder();
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    encoder.encode(key),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign("HMAC", cryptoKey, encoder.encode(message));
  return [...new Uint8Array(signature)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** The cookie value. It is derived from the passcode, so changing one ends every session. */
export function sessionToken(code: string): Promise<string> {
  return hmacHex(code, "mixmax-session-v1");
}

/** Compare without leaking how many leading characters matched. */
export function sameString(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export async function isAuthorized(cookieValue: string | undefined): Promise<boolean> {
  const code = passcode();
  if (code === null) return !misconfigured();
  if (!cookieValue) return false;
  return sameString(cookieValue, await sessionToken(code));
}
