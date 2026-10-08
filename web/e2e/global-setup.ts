import { request, type FullConfig } from "@playwright/test";
import { cpSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";

export const DATA_DIR = path.join(__dirname, ".data");
export const AUTH_FILE = path.join(__dirname, ".auth.json");
export const PASSCODE = "e2e-passcode";

const field = (key: string, group: string, label: string, value: number, extra: object = {}) => ({
  key, group, label, unit: "dB", min: -30, max: 30, step: 0.5, default: value,
  help: `What ${label.toLowerCase()} does.`, kind: "number", value, recorded: false, ...extra,
});

/** What `producer sync` would have published, small enough to read. */
export const SONG = {
  slug: "e2e-song",
  title: "Night Drive",
  kind: "vocal-only",
  version: "v1",
  final_component: "master",
  timeline_component: "master",
  progress: {
    slug: "e2e-song", kind: "vocal-only", kind_reason: "stops between phrases",
    duration_s: 6, percent: 60,
    stages: [
      { key: "intake", label: "Raw material", state: "done", detail: "6s, mono", blocker: "" },
      { key: "master", label: "Master", state: "done", detail: "-16.0 LUFS", blocker: "" },
      { key: "judged", label: "Judged by ear", state: "todo", detail: "nobody has listened", blocker: "" },
    ],
    measurements: {
      raw: { lufs: -21, true_peak_dbtp: -1, crest_factor_db: 18, lra: 6 },
      final: { lufs: -16, true_peak_dbtp: -3, crest_factor_db: 12, lra: 4 },
      final_file: "MASTER.wav",
    },
    arrangement: {
      sections: 2, types: 2, notes: [],
      transitions: [{ at_s: 3, from_label: "A", to_label: "B", kind: "breakdown", energy_change_db: -1, low_change_db: -10 }],
      timeline: [
        { label: "A", start_s: 0, duration_s: 3, energy_db: -20, low_energy_db: -10 },
        { label: "B", start_s: 3, duration_s: 3, energy_db: -22, low_energy_db: -22 },
      ],
    },
    listening: null,
    next_step: "Needs listeners.",
  },
  components: [
    { key: "raw", label: "Raw vocal", about: "The take as it was recorded.", replace_as: "vocal", present: true,
      file: "original.wav", hash: "aaaaaaaaaaaa", audio: "songs/e2e-song/audio/raw.aaaaaaaaaaaa.m4a",
      measure: { lufs: -21, true_peak_dbtp: -1, lra: 6, crest_factor_db: 18, duration_s: 6, sample_rate: 44100, channels: 1 } },
    { key: "beat", label: "Beat", about: "The instrumental under the vocal.", replace_as: "beat", present: false },
    { key: "master", label: "Master", about: "The finished master.", replace_as: "", present: true,
      file: "MASTER.wav", hash: "bbbbbbbbbbbb", audio: "songs/e2e-song/audio/master.bbbbbbbbbbbb.m4a",
      measure: { lufs: -16, true_peak_dbtp: -3, lra: 4, crest_factor_db: 12, duration_s: 6, sample_rate: 44100, channels: 2 } },
  ],
  settings: [
    { key: "vocal", label: "Vocal chain", about: "Shapes the vocal.", applies: true, why_not: "",
      fields: [field("comp_ratio", "vocal", "Compressor ratio", 3, { unit: ":1", min: 1, max: 10, step: 0.1 })] },
    { key: "balance", label: "Vocal against the beat", about: "", applies: false,
      why_not: "There is no beat yet. Upload one and the balance can be set.",
      fields: [field("duck_db", "balance", "Beat ducking", 2.5)] },
    { key: "master", label: "Master", about: "Loudness.", applies: true, why_not: "",
      fields: [field("target_lufs", "master", "Loudness", -16, { unit: "LUFS", min: -24, max: -8 })] },
  ],
};

export function seed(): void {
  rmSync(DATA_DIR, { recursive: true, force: true });
  const audio = path.join(DATA_DIR, "songs", SONG.slug, "audio");
  mkdirSync(audio, { recursive: true });
  mkdirSync(path.join(DATA_DIR, "sync"), { recursive: true });
  const fixture = path.join(__dirname, "..", "public", "fixtures", "x.m4a");
  cpSync(fixture, path.join(audio, "raw.aaaaaaaaaaaa.m4a"));
  cpSync(fixture, path.join(audio, "master.bbbbbbbbbbbb.m4a"));
  writeFileSync(path.join(DATA_DIR, "songs", SONG.slug, "song.json"), JSON.stringify(SONG));
  writeFileSync(
    path.join(DATA_DIR, "songs", "index.json"),
    JSON.stringify({
      songs: [{
        slug: SONG.slug, title: SONG.title, kind: SONG.kind, percent: 60, duration_s: 6,
        stages: SONG.progress.stages.map(({ key, label, state }) => ({ key, label, state })),
        next_step: SONG.progress.next_step, final_component: "master", version: "v1",
      }],
    }),
  );
  writeFileSync(
    path.join(DATA_DIR, "sync", "heartbeat.json"),
    JSON.stringify({ at: new Date().toISOString(), host: "e2e", songs: 1 }),
  );
}

/** Seed the store, then sign in once so every test starts past the passcode. */
export default async function globalSetup(config: FullConfig): Promise<void> {
  seed();
  const baseURL = config.projects[0].use.baseURL!;
  const api = await request.newContext({ baseURL });
  const response = await api.post("/api/login", { data: { passcode: PASSCODE } });
  if (!response.ok()) throw new Error(`Could not sign in for the suite: ${response.status()}`);
  await api.storageState({ path: AUTH_FILE });
  await api.dispose();
}
