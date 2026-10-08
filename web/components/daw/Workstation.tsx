"use client";

import { useEffect, useState } from "react";
import { NameGate } from "@/components/Identity";
import { duplicateClip, removeClip, splitClip } from "@/lib/daw/edit";
import { useWs } from "@/lib/daw/useWorkstation";
import { AddTrack } from "./AddTrack";
import { Arrangement } from "./Arrangement";
import { ExportDialog } from "./ExportDialog";
import { Mixer } from "./Mixer";
import { TrackPanel } from "./TrackPanel";
import { TransportBar } from "./TransportBar";
import styles from "./daw.module.css";

const typing = (target: EventTarget | null): boolean => {
  const element = target as HTMLElement | null;
  if (!element?.tagName) return false;
  if (element.tagName === "TEXTAREA" || element.tagName === "SELECT" || element.isContentEditable) return true;
  // A range slider takes arrow keys but not letters, so shortcuts still work with one focused.
  return element.tagName === "INPUT" && (element as HTMLInputElement).type !== "range" && (element as HTMLInputElement).type !== "checkbox";
};

export function Workstation() {
  const ws = useWs();
  const [dialog, setDialog] = useState<"add" | "export" | null>(null);
  const [tab, setTab] = useState<"edit" | "mixer">("edit");

  useEffect(() => {
    document.title = `${ws.project.name} — MixMax studio`;
  }, [ws.project.name]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setDialog(null);
        return;
      }
      if (dialog || typing(event.target)) return;
      const command = event.metaKey || event.ctrlKey;
      const { track, clip } = ws.selection;

      if (event.code === "Space") {
        event.preventDefault();
        ws.toggle();
      } else if (event.key === "Enter") {
        ws.seek(ws.project.loop.on ? ws.project.loop.start : 0);
      } else if (command && event.key.toLowerCase() === "z") {
        event.preventDefault();
        if (event.shiftKey) ws.redo();
        else ws.undo();
      } else if (command && event.key.toLowerCase() === "d" && track && clip) {
        event.preventDefault();
        ws.apply(duplicateClip(track, clip));
      } else if ((event.key === "Delete" || event.key === "Backspace") && track && clip) {
        event.preventDefault();
        ws.apply(removeClip(track, clip));
        ws.select(track);
      } else if (!command && event.key.toLowerCase() === "s" && track && clip) {
        ws.apply(splitClip(track, clip, ws.position()));
      } else if (!command && event.key.toLowerCase() === "l") {
        ws.apply((p) => ({ ...p, loop: { ...p.loop, on: !p.loop.on } }));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ws, dialog]);

  return (
    <main className={styles.daw}>
      <NameGate />
      <TransportBar onAdd={() => setDialog("add")} onExport={() => setDialog("export")} />

      {ws.save === "conflict" && (
        <div className={styles.banner} role="alert">
          <span>{ws.saveMessage} Your changes here have not been saved.</span>
          <button className={styles.chip} onClick={() => ws.resolveConflict("theirs")}>Load theirs</button>
          <button className={styles.chip} data-tone="danger" onClick={() => ws.resolveConflict("mine")}>Keep mine</button>
        </div>
      )}
      {ws.error && (
        <div className={styles.banner} role="alert">
          <span>{ws.error}</span>
          <button className={styles.chip} onClick={ws.dismissError}>Dismiss</button>
        </div>
      )}
      {ws.busy && (
        <div className={styles.banner} data-tone="quiet" role="status">
          {ws.busy}
        </div>
      )}

      <Arrangement onAdd={() => setDialog("add")} />

      <section className={styles.panel}>
        <div className={styles.tabs} role="tablist" aria-label="Lower panel">
          <button role="tab" className={styles.tab} aria-selected={tab === "edit"} onClick={() => setTab("edit")}>
            Edit
          </button>
          <button role="tab" className={styles.tab} aria-selected={tab === "mixer"} onClick={() => setTab("mixer")}>
            Mixer
          </button>
        </div>
        {tab === "edit" ? <TrackPanel /> : <Mixer />}
      </section>

      {dialog === "add" && <AddTrack onClose={() => setDialog(null)} />}
      {dialog === "export" && <ExportDialog onClose={() => setDialog(null)} />}
    </main>
  );
}
