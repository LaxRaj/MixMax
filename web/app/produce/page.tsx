"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { NameGate, useIdentity } from "@/components/Identity";
import styles from "@/components/studio.module.css";
import type { ProjectSummary } from "@/lib/daw/types";
import { ago } from "@/lib/song";

export default function ProducePage() {
  const router = useRouter();
  const { name } = useIdentity();
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [creating, setCreating] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);

  const load = useCallback(
    () =>
      fetch("/api/projects")
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`Could not load projects (${r.status})`))))
        .then((data) => setProjects(data.projects))
        .catch((e: Error) => setError(e.message)),
    [],
  );

  useEffect(() => {
    document.title = "Produce — MixMax studio";
    void load();
  }, [load]);

  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    setCreating(true);
    setError(null);
    try {
      const response = await fetch("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: title, author: name }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error ?? `Could not start a project (${response.status}).`);
      router.push(`/produce/${data.project.id}`);
    } catch (e) {
      setError((e as Error).message);
      setCreating(false);
    }
  };

  const remove = async (id: string) => {
    setConfirming(null);
    const response = await fetch(`/api/projects/${id}`, { method: "DELETE" });
    if (!response.ok) setError(`Could not delete the project (${response.status}).`);
    void load();
  };

  return (
    <main className={styles.page}>
      <header className={`${styles.head} rise`}>
        <p className={styles.eyebrow}>Produce</p>
        <h1>Make something</h1>
        <p>
          A workstation in the browser: program drums, write parts on a synth, record, cut and arrange
          audio, mix it, and send the result to the studio. Bring in a song&apos;s vocal to build a beat
          around it.
        </p>
      </header>

      <NameGate />

      <form className={`${styles.panel} ${styles.nameRow}`} onSubmit={create}>
        <input
          className={styles.input}
          aria-label="Project name"
          placeholder="Name the project"
          value={title}
          maxLength={80}
          onChange={(event) => setTitle(event.target.value)}
        />
        <button className={styles.primary} type="submit" disabled={!name || creating}>
          {creating ? "Starting…" : "New project"}
        </button>
      </form>
      {error && (
        <p className={styles.notice} data-tone="bad" role="alert" style={{ marginTop: 16 }}>
          {error}
        </p>
      )}

      <div className={styles.sectionHead}>
        <h2>Projects</h2>
      </div>
      {projects === null ? (
        <p className={styles.muted}>Loading…</p>
      ) : projects.length === 0 ? (
        <div className={styles.empty}>Nothing yet. Name a project above to start one.</div>
      ) : (
        <div className={styles.grid}>
          {projects.map((project) => (
            <div key={project.id} className={styles.songCard} style={{ cursor: "default" }}>
              <Link href={`/produce/${project.id}`} className={styles.songTitle} style={{ color: "inherit", textDecoration: "none" }}>
                {project.name}
              </Link>
              <span className={styles.kind}>
                {project.tracks} track{project.tracks === 1 ? "" : "s"} · {project.bars} bar{project.bars === 1 ? "" : "s"} ·{" "}
                <span className="mono">{project.bpm}</span> bpm
              </span>
              <div className={styles.cardFoot}>
                <span>
                  {project.updated_by ? `${project.updated_by}, ` : ""}
                  {ago(project.updated_at)}
                </span>
                {confirming === project.id ? (
                  <>
                    <button className={styles.linkButton} onClick={() => void remove(project.id)}>
                      Yes, delete it and its audio
                    </button>
                    <button className={styles.linkButton} onClick={() => setConfirming(null)}>
                      Keep
                    </button>
                  </>
                ) : (
                  <button className={styles.linkButton} onClick={() => setConfirming(project.id)} aria-label={`Delete ${project.name}`}>
                    Delete
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </main>
  );
}
