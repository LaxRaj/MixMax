import { NextResponse } from "next/server";
import { bad, cleanName, readBody } from "@/lib/api";
import { PROJECT_ID, normalizeProject, type Project } from "@/lib/daw/types";
import { getJson, list, putJson, remove } from "@/lib/store";

export const dynamic = "force-dynamic";

const MAX_PROJECT_BYTES = 1_500_000;
const pathOf = (id: string) => `projects/${id}/project.json`;

type Params = { params: Promise<{ id: string }> };

async function load(id: string): Promise<Project | null> {
  if (!PROJECT_ID.test(id)) return null;
  return normalizeProject(await getJson<unknown>(pathOf(id)));
}

export async function GET(_request: Request, { params }: Params) {
  const project = await load((await params).id);
  if (!project) return bad("No such project.", 404);
  return NextResponse.json({ project });
}

/**
 * Save the whole project. Unlike a note, a project is one object that two
 * people can both have open, so each save names the revision it was made
 * from; one made from a stale revision is refused rather than allowed to
 * silently undo someone else's work.
 */
export async function PUT(request: Request, { params }: Params) {
  const { id } = await params;
  const stored = await load(id);
  if (!stored) return bad("No such project.", 404);

  const body = await readBody(request);
  const incoming = normalizeProject(body?.project);
  if (!body || !incoming || incoming.id !== id) return bad("That is not a project.");
  const author = cleanName(body.author);
  if (!author) return bad("Say who you are before saving.");

  if (body.force !== true && Number(body.base_rev) !== stored.rev) {
    return NextResponse.json(
      {
        error: `${stored.updated_by || "Someone"} saved a newer version of this project.`,
        rev: stored.rev,
        updated_by: stored.updated_by,
        updated_at: stored.updated_at,
      },
      { status: 409 },
    );
  }

  const project: Project = {
    ...incoming,
    rev: stored.rev + 1,
    created_at: stored.created_at,
    updated_at: new Date().toISOString(),
    updated_by: author,
  };
  if (JSON.stringify(project).length > MAX_PROJECT_BYTES) return bad("The project is too large to save.", 413);
  await putJson(pathOf(id), project);
  return NextResponse.json({ ok: true, rev: project.rev, updated_at: project.updated_at });
}

/** Delete a project and the audio that was imported or recorded into it. */
export async function DELETE(_request: Request, { params }: Params) {
  const { id } = await params;
  if (!PROJECT_ID.test(id)) return bad("No such project.", 404);
  const entries = await list(`projects/${id}/`);
  if (entries.length === 0) return bad("No such project.", 404);
  await Promise.all(entries.map((entry) => remove(entry.path)));
  return NextResponse.json({ ok: true });
}
