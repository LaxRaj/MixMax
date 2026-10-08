"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useIdentity } from "@/components/Identity";
import { newId, type Note, type NoteTarget, type Song } from "@/lib/song";
import styles from "./studio.module.css";

type SaveState = "idle" | "dirty" | "saving" | "saved" | "error";

const SAVE_COPY: Record<SaveState, string> = {
  idle: "",
  dirty: "Saving…",
  saving: "Saving…",
  saved: "Saved",
  error: "Not saved — check your connection",
};

const DEBOUNCE_MS = 1200;

/** Settings that were not the defaults — the context a note was written in. */
export function settingsInForce(song: Song): Record<string, number> {
  const changed: Record<string, number> = {};
  for (const group of song.settings) {
    if (!group.applies) continue;
    for (const field of group.fields) {
      if (field.recorded && field.value !== field.default) changed[field.key] = field.value;
    }
  }
  return changed;
}

/**
 * A note that saves itself.
 *
 * There is no submit button: what is typed is saved a moment after typing
 * stops and again when the box loses focus, always under the same id, so a
 * note being written is one object rewritten rather than a trail of drafts.
 */
export function NoteBox({
  song,
  target,
  placeholder,
  label,
  onSaved,
}: {
  song: Song;
  target: NoteTarget;
  placeholder: string;
  label: string;
  onSaved: (note: Note | null, id: string) => void;
}) {
  const { name } = useIdentity();
  const [text, setText] = useState("");
  const [state, setState] = useState<SaveState>("idle");
  const id = useRef<string | null>(null);
  const timer = useRef<number | null>(null);
  const latest = useRef("");
  const saved = useRef("");
  const inFlight = useRef(false);

  const save = useCallback(async () => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = null;
    if (inFlight.current) return;
    const body = latest.current;
    if (body === saved.current || !name) return;
    if (!id.current) {
      if (!body.trim()) return;
      id.current = newId();
    }
    const noteId = id.current;
    inFlight.current = true;
    setState("saving");
    try {
      const response = await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          slug: song.slug,
          id: noteId,
          target,
          text: body,
          author: name,
          settings_changed: settingsInForce(song),
          song_version: song.version,
        }),
      });
      if (!response.ok) throw new Error(String(response.status));
      const data = await response.json();
      saved.current = body;
      onSaved(data.deleted ? null : (data.note as Note), noteId);
      if (data.deleted) id.current = null;
      setState(body.trim() ? "saved" : "idle");
    } catch {
      setState("error");
    } finally {
      inFlight.current = false;
      // Typing continued while the request was out: save what is there now.
      if (latest.current !== saved.current && !timer.current) {
        timer.current = window.setTimeout(save, DEBOUNCE_MS);
      }
    }
  }, [name, onSaved, song, target]);

  // Whatever is unsaved when the box goes away still gets sent.
  const saveRef = useRef(save);
  saveRef.current = save;
  useEffect(() => () => void saveRef.current(), []);

  const change = (value: string) => {
    setText(value);
    latest.current = value;
    setState("dirty");
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(save, DEBOUNCE_MS);
  };

  const another = () => {
    id.current = null;
    latest.current = "";
    saved.current = "";
    setText("");
    setState("idle");
  };

  return (
    <div className={styles.noteBox}>
      <textarea
        className={styles.textarea}
        aria-label={label}
        value={text}
        placeholder={name ? placeholder : "Say who you are above, then leave a note."}
        disabled={!name}
        maxLength={4000}
        rows={2}
        onChange={(event) => change(event.target.value)}
        onBlur={() => void save()}
      />
      <div className={styles.noteMeta}>
        <span className={styles.saveState} data-s={state} role="status">
          {SAVE_COPY[state]}
        </span>
        {state === "saved" && (
          <button className={styles.linkButton} type="button" onClick={another}>
            Start another note
          </button>
        )}
      </div>
    </div>
  );
}
