"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import styles from "./studio.module.css";

const KEY = "mixmax.name";

type Identity = { name: string; ready: boolean; setName: (name: string) => void };

const IdentityContext = createContext<Identity>({ name: "", ready: false, setName: () => {} });

/**
 * Who is leaving this note. There are no accounts — two friends share one
 * passcode — so a name typed once and kept in this browser is the whole
 * identity. It is attached to every note, upload and settings change.
 */
export function IdentityProvider({ children }: { children: React.ReactNode }) {
  const [name, setStored] = useState("");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    try {
      setStored(window.localStorage.getItem(KEY) ?? "");
    } catch {
      // Private windows may refuse storage; the name then lasts for this visit.
    }
    setReady(true);
  }, []);

  const setName = useCallback((next: string) => {
    const clean = next.replace(/\s+/g, " ").trim().slice(0, 60);
    setStored(clean);
    try {
      if (clean) window.localStorage.setItem(KEY, clean);
      else window.localStorage.removeItem(KEY);
    } catch {
      // See above.
    }
  }, []);

  return (
    <IdentityContext.Provider value={{ name, ready, setName }}>{children}</IdentityContext.Provider>
  );
}

export function useIdentity(): Identity {
  return useContext(IdentityContext);
}

/** Asks for a name once, on the screens where things get written. */
export function NameGate() {
  const { name, ready, setName } = useIdentity();
  const [draft, setDraft] = useState("");
  if (!ready || name) return null;

  return (
    <form
      className={styles.nameGate}
      onSubmit={(event) => {
        event.preventDefault();
        setName(draft);
      }}
    >
      <label htmlFor="who">
        <strong>Who&apos;s this?</strong>
        <span>Your name goes on the notes you leave, so they can be told apart.</span>
      </label>
      <div className={styles.nameRow}>
        <input
          id="who"
          className={styles.input}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Your name"
          autoComplete="given-name"
          maxLength={60}
        />
        <button className={styles.primary} type="submit" disabled={!draft.trim()}>
          Continue
        </button>
      </div>
    </form>
  );
}
