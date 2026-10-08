"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { useIdentity } from "@/components/Identity";
import type { SongComponent, UploadKind } from "@/lib/song";
import { checkFile, sendUpload } from "@/lib/uploadClient";
import styles from "./studio.module.css";

type Status =
  | { step: "idle" }
  | { step: "working"; text: string }
  | { step: "sent"; warnings: string[] }
  | { step: "refused"; reasons: string[] };

/**
 * Swap one of a song's inputs — the vocal, the beat, the reference.
 *
 * Nothing is replaced here. The file is uploaded and marked as a replacement;
 * the studio Mac checks it, and only swaps it in (keeping the old one) if it
 * passes. Everything built from the old file is then re-rendered.
 */
export function ReplaceFile({
  slug,
  component,
  mode,
}: {
  slug: string;
  component: SongComponent;
  mode: "blob" | "local";
}) {
  const { name } = useIdentity();
  const input = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState<Status>({ step: "idle" });
  const kind = component.replace_as as UploadKind;
  const noun = kind === "vocal" ? "vocal" : kind;

  const choose = async (file: File | undefined) => {
    if (!file) return;
    setStatus({ step: "working", text: "Checking the file…" });
    const check = await checkFile(file, kind);
    if (check.blockers.length > 0) {
      setStatus({ step: "refused", reasons: check.blockers });
      return;
    }
    try {
      await sendUpload(
        { file, kind, song: slug, replaces: true, uploader: name, client_warnings: check.warnings },
        mode,
        (fraction) => setStatus({ step: "working", text: `Uploading… ${Math.round(fraction * 100)}%` }),
      );
      setStatus({ step: "sent", warnings: check.warnings });
    } catch (error) {
      setStatus({ step: "refused", reasons: [(error as Error).message] });
    }
  };

  return (
    <>
      <input
        ref={input}
        className={styles.hiddenInput}
        type="file"
        accept="audio/*,.wav,.aif,.aiff,.flac,.mp3,.m4a,.caf,.ogg"
        aria-label={`${component.present ? "Replace" : "Add"} the ${noun} file`}
        onChange={(event) => {
          void choose(event.target.files?.[0]);
          event.target.value = "";
        }}
      />
      <button
        className={styles.ghost}
        type="button"
        disabled={!name || status.step === "working"}
        onClick={() => input.current?.click()}
      >
        {component.present ? "Replace file" : `Add a ${noun}`}
      </button>
      {status.step === "working" && <span className={styles.fileStatus}>{status.text}</span>}
      {status.step === "sent" && (
        <span className={styles.fileStatus} data-tone="good">
          Uploaded. The studio Mac checks it before anything is swapped —{" "}
          <Link href="/upload">see its status</Link>.
        </span>
      )}
      {status.step === "refused" && (
        <span className={styles.fileStatus} data-tone="bad" role="alert">
          Not uploaded: {status.reasons.join(" ")}
        </span>
      )}
    </>
  );
}
