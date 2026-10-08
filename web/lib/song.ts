import type { StageState, TrackProgress } from "@/lib/compare";

/** The shapes `producer sync` publishes and the studio writes back. */

export type Measure = {
  lufs: number | null;
  true_peak_dbtp: number | null;
  lra: number | null;
  crest_factor_db: number | null;
  duration_s: number | null;
  sample_rate: number | null;
  channels: number | null;
};

export type UploadKind = "vocal" | "beat" | "reference" | "other";

export type SongComponent = {
  key: string;
  label: string;
  about: string;
  /** The upload kind that replaces this file; empty for pipeline outputs. */
  replace_as: UploadKind | "";
  present: boolean;
  file?: string;
  hash?: string;
  audio?: string;
  measure?: Measure;
};

export type SettingField = {
  key: string;
  group: string;
  label: string;
  unit: string;
  min: number;
  max: number;
  step: number;
  default: number;
  help: string;
  kind: "number" | "toggle";
  value: number;
  /** False when nobody has chosen this value yet and the default is shown. */
  recorded: boolean;
};

export type SettingGroup = {
  key: string;
  label: string;
  about: string;
  applies: boolean;
  why_not: string;
  fields: SettingField[];
};

export type Song = {
  slug: string;
  title: string;
  kind: string;
  version: string;
  progress: TrackProgress;
  components: SongComponent[];
  final_component: string;
  timeline_component: string;
  settings: SettingGroup[];
};

export type SongSummary = {
  slug: string;
  title: string;
  kind: string;
  percent: number;
  duration_s: number;
  stages: { key: string; label: string; state: StageState }[];
  next_step: string;
  final_component: string;
  version: string;
  notes: number;
  pending: number;
};

export type Heartbeat = { at: string; host: string; songs: number } | null;

export type NoteTarget =
  | { kind: "song" }
  | { kind: "component"; component: string }
  | {
      kind: "section";
      component: string;
      label: string;
      index: number;
      start_s: number;
      end_s: number;
    };

export type Note = {
  id: string;
  slug: string;
  target: NoteTarget;
  author: string;
  text: string;
  created_at: string;
  updated_at: string;
  /** Settings in force that were not the defaults when this was written. */
  settings_changed?: Record<string, number>;
  song_version?: string;
};

export type RequestResult = {
  state: "rendering" | "done" | "failed" | "rejected";
  at: string;
  message?: string;
  rendered?: string[];
  took_s?: number;
  before?: Record<string, number>;
};

export type SettingsRequest = {
  id: string;
  slug: string;
  author: string;
  created_at: string;
  changes: Record<string, number>;
  note?: string;
  result?: RequestResult;
};

export type UploadReport = {
  id: string;
  slug: string;
  kind: UploadKind;
  verdict: "ready" | "caution" | "blocked";
  blockers: string[];
  warnings: string[];
  metrics: Record<string, number | boolean | null>;
  placed: string | null;
  kept: string | null;
  rendered: string[];
  checked_at: string;
  note?: string;
};

export type UploadMeta = {
  id: string;
  filename: string;
  size: number;
  kind: UploadKind;
  /** Slug of an existing song, or the title of a new one. */
  song: string;
  song_title?: string;
  replaces: boolean;
  uploader: string;
  uploaded_at: string;
  client_warnings?: string[];
};

export type Upload = UploadMeta & { report?: UploadReport };

export const SLUG = /^[a-z0-9][a-z0-9-]{0,80}$/;
export const ID = /^[a-z0-9][a-z0-9-]{5,60}$/;

export const UPLOAD_EXTENSIONS = [
  "wav", "flac", "aiff", "aif", "ogg", "mp3", "caf", "m4a", "mp4", "aac", "m4r",
];

/** Mirrors `producer.intake.slugify`, so the browser and the Mac agree on a name. */
export function slugify(name: string): string {
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  return slug || "track";
}

export function newId(): string {
  const random = Math.random().toString(36).slice(2, 10);
  return `${Date.now().toString(36)}-${random}`;
}

export function clock(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "–:––";
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

export function ago(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return "unknown";
  const seconds = Math.max(0, Math.round((now - then) / 1000));
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return `${Math.floor(seconds / 86400)} d ago`;
}

/** The studio Mac is "there" if it checked in recently. */
export function isLive(heartbeat: Heartbeat, now: number = Date.now()): boolean {
  if (!heartbeat) return false;
  return now - new Date(heartbeat.at).getTime() < 90_000;
}

export function targetKey(target: NoteTarget): string {
  if (target.kind === "song") return "song";
  if (target.kind === "component") return `component:${target.component}`;
  return `section:${target.component}:${target.index}`;
}
