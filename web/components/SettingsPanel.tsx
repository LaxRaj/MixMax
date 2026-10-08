"use client";

import { useMemo, useState } from "react";
import { useIdentity } from "@/components/Identity";
import {
  ago,
  isLive,
  type Heartbeat,
  type SettingField,
  type SettingsRequest,
  type Song,
} from "@/lib/song";
import styles from "./studio.module.css";

function show(value: number): string {
  return String(Number(value.toFixed(4)));
}

function describeChanges(request: SettingsRequest, labels: Map<string, SettingField>): string {
  return Object.entries(request.changes)
    .map(([key, value]) => {
      const field = labels.get(key);
      const before = request.result?.before?.[key];
      const unit = field?.unit ? ` ${field.unit}` : "";
      const to = field?.kind === "toggle" ? (value >= 0.5 ? "on" : "off") : `${show(value)}${unit}`;
      return `${field?.label ?? key} ${before != null && field?.kind !== "toggle" ? `${show(before)} → ` : "→ "}${to}`;
    })
    .join(", ");
}

/**
 * The knobs. Changing one renders nothing here: it queues a request, and the
 * studio Mac re-renders from the first stage the change touches.
 */
export function SettingsPanel({
  song,
  requests,
  heartbeat,
  onQueued,
}: {
  song: Song;
  requests: SettingsRequest[];
  heartbeat: Heartbeat;
  onQueued: () => void;
}) {
  const { name } = useIdentity();
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fields = useMemo(
    () => new Map(song.settings.flatMap((g) => g.fields.map((f) => [f.key, f] as const))),
    [song],
  );

  const changes: Record<string, number> = {};
  const invalid = new Set<string>();
  for (const [key, raw] of Object.entries(drafts)) {
    const field = fields.get(key);
    if (!field) continue;
    const value = Number(raw);
    if (raw.trim() === "" || !Number.isFinite(value) || value < field.min || value > field.max) {
      invalid.add(key);
    } else if (value !== field.value) {
      changes[key] = value;
    }
  }
  const count = Object.keys(changes).length;
  const pending = requests.filter((r) => !r.result || r.result.state === "rendering");
  const live = isLive(heartbeat);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/requests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ slug: song.slug, author: name, changes }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error ?? `Could not queue the change (${response.status}).`);
      setDrafts({});
      onQueued();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className={styles.settingsGroups}>
        {song.settings.map((group) => (
          <div key={group.key} className={styles.group} data-off={!group.applies}>
            <div className={styles.groupHead}>
              <h3>{group.label}</h3>
            </div>
            <p className={styles.groupAbout}>{group.applies ? group.about : group.why_not}</p>
            {group.applies && (
              <div className={styles.fields}>
                {group.fields.map((field) => {
                  const draft = drafts[field.key];
                  const shown = draft ?? show(field.value);
                  const changed = field.key in changes;
                  const bad = invalid.has(field.key);
                  if (field.kind === "toggle") {
                    const on = Number(shown) >= 0.5;
                    return (
                      <div key={field.key} className={styles.field} data-changed={changed}>
                        <label className={styles.toggle}>
                          <input
                            type="checkbox"
                            checked={on}
                            onChange={(event) =>
                              setDrafts((d) => ({ ...d, [field.key]: event.target.checked ? "1" : "0" }))
                            }
                          />
                          {field.label}
                        </label>
                        <span className={styles.fieldHelp}>{field.help}</span>
                      </div>
                    );
                  }
                  return (
                    <div key={field.key} className={styles.field} data-changed={changed} data-invalid={bad}>
                      <label className={styles.fieldLabel} htmlFor={`f-${field.key}`}>
                        {field.label}
                        <span>{field.unit}</span>
                      </label>
                      <div className={styles.fieldInput}>
                        <input
                          id={`f-${field.key}`}
                          className={styles.input}
                          type="number"
                          inputMode="decimal"
                          min={field.min}
                          max={field.max}
                          step={field.step}
                          value={shown}
                          onChange={(event) => setDrafts((d) => ({ ...d, [field.key]: event.target.value }))}
                        />
                      </div>
                      <span className={styles.fieldHelp} data-tone={bad ? "bad" : undefined}>
                        {bad
                          ? `Between ${field.min} and ${field.max}${field.unit ? ` ${field.unit}` : ""}.`
                          : field.help}{" "}
                        {!bad && (
                          <>
                            {changed
                              ? `Was ${show(field.value)}.`
                              : field.recorded
                                ? field.value !== field.default
                                  ? `Default ${show(field.default)}.`
                                  : ""
                                : "Not recorded for the current render — default shown."}
                          </>
                        )}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        ))}
      </div>

      <div className={styles.requestBar}>
        <p>
          {count === 0
            ? "Change a value above, then ask for a re-render."
            : `${count} change${count === 1 ? "" : "s"} ready. The studio Mac re-renders this song and everything downstream of the change.`}
          {!live && count > 0 && (
            <>
              {" "}
              <strong>
                It is not syncing right now
                {heartbeat ? ` (last seen ${ago(heartbeat.at)})` : ""}, so this will wait.
              </strong>
            </>
          )}
        </p>
        {count > 0 && (
          <button className={styles.ghost} type="button" onClick={() => setDrafts({})} disabled={busy}>
            Reset
          </button>
        )}
        <button
          className={styles.primary}
          type="button"
          disabled={count === 0 || invalid.size > 0 || busy || !name}
          onClick={submit}
        >
          {busy ? "Sending…" : "Request re-render"}
        </button>
      </div>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}

      {requests.length > 0 && (
        <div className={styles.requests} aria-label="Settings changes">
          {requests.slice(0, pending.length + 4).map((request) => {
            const state = request.result?.state ?? "queued";
            return (
              <div key={request.id} className={styles.request} data-s={state}>
                <span className={styles.requestState}>{state}</span>
                {describeChanges(request, fields)}
                <span className={styles.requestDetail}>
                  {request.author} · {ago(request.created_at)}
                  {state === "queued" &&
                    (live
                      ? " · the studio Mac picks this up within half a minute; a render takes a minute or two"
                      : " · waiting for the studio Mac")}
                  {state === "rendering" && " · rendering now"}
                  {state === "done" &&
                    ` · re-rendered ${request.result?.rendered?.join(", ") || "nothing"}` +
                      (request.result?.took_s ? ` in ${Math.round(request.result.took_s)}s` : "")}
                  {(state === "failed" || state === "rejected") && ` · ${request.result?.message ?? ""}`}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
