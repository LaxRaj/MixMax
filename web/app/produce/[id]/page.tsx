"use client";

import Link from "next/link";
import { use } from "react";
import { Workstation } from "@/components/daw/Workstation";
import { useIdentity } from "@/components/Identity";
import studio from "@/components/studio.module.css";
import { WorkstationProvider, useWorkstation } from "@/lib/daw/useWorkstation";

export default function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { name } = useIdentity();
  const { ws, loadError } = useWorkstation(id, name);

  if (loadError) {
    return (
      <main className={studio.page}>
        <div className={studio.head}>
          <p className={studio.eyebrow}>
            <Link href="/produce">← Projects</Link>
          </p>
          <h1>Not found</h1>
          <p>{loadError}</p>
        </div>
      </main>
    );
  }
  if (!ws) {
    return (
      <main className={studio.page}>
        <p className={studio.muted}>Loading…</p>
      </main>
    );
  }
  return (
    <WorkstationProvider value={ws}>
      <Workstation />
    </WorkstationProvider>
  );
}
