import { NextResponse } from "next/server";
import type { Heartbeat, UploadReport } from "@/lib/song";
import { getJson, readAll, storeMode } from "@/lib/store";

export const dynamic = "force-dynamic";

/** Light enough for the nav to ask on every screen. */
export async function GET() {
  const [heartbeat, reports] = await Promise.all([
    getJson<NonNullable<Heartbeat>>("sync/heartbeat.json"),
    readAll<UploadReport>("uploads/"),
  ]);
  const problems = reports
    .filter((r) => r.path.endsWith("/report.json") && r.value.verdict !== "ready")
    .map((r) => r.value.id);
  return NextResponse.json({ heartbeat, problems, mode: storeMode() });
}
