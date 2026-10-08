import { NextResponse } from "next/server";
import { bad, cleanName, readBody } from "@/lib/api";
import { summarize } from "@/lib/daw/schedule";
import { newProject, normalizeProject, type Project } from "@/lib/daw/types";
import { newId } from "@/lib/song";
import { putJson, readAll } from "@/lib/store";

export const dynamic = "force-dynamic";

/** Every workstation project, most recently touched first. */
export async function GET() {
  const stored = await readAll<unknown>("projects/");
  const projects = stored
    .filter((entry) => entry.path.endsWith("/project.json"))
    .flatMap((entry) => normalizeProject(entry.value) ?? [])
    .map(summarize)
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  return NextResponse.json({ projects });
}

export async function POST(request: Request) {
  const body = await readBody(request);
  if (!body) return bad("Expected a JSON body.");
  const author = cleanName(body.author);
  if (!author) return bad("Say who you are before starting a project.");

  const project: Project = newProject(newId(), String(body.name ?? ""), author);
  await putJson(`projects/${project.id}/project.json`, project);
  return NextResponse.json({ project });
}
