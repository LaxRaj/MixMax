/**
 * A workstation project: tracks of drums, synths and audio on one timeline.
 *
 * Positions are in beats (quarter notes), so changing the tempo moves the
 * pattern clips with it. Audio is the exception: a recording has a fixed
 * length in seconds, so an audio clip starts on a beat and lasts `duration_s`.
 */

export const DRUM_VOICES = ["kick", "snare", "clap", "hat", "openhat", "tom", "rim", "perc"] as const;
export type DrumVoice = (typeof DRUM_VOICES)[number];

export const DRUM_LABELS: Record<DrumVoice, string> = {
  kick: "Kick",
  snare: "Snare",
  clap: "Clap",
  hat: "Hat",
  openhat: "Open hat",
  tom: "Tom",
  rim: "Rim",
  perc: "Cowbell",
};

export const KITS = ["808", "punch", "lofi"] as const;
export type KitName = (typeof KITS)[number];
export const KIT_LABELS: Record<KitName, string> = { "808": "808", punch: "Punchy", lofi: "Lo-fi" };

/** One step is a sixteenth note. */
export const STEP_BEATS = 0.25;
export const BEATS_PER_BAR = 4;

export type DrumPattern = {
  id: string;
  name: string;
  steps: number;
  /** Velocity per step, 0 for a rest. */
  lanes: Record<DrumVoice, number[]>;
};

export type Note = { id: string; pitch: number; start: number; length: number; velocity: number };
export type NotePattern = { id: string; name: string; length: number; notes: Note[] };

/**
 * A pattern placed on the timeline. It repeats to fill the clip's length.
 * `offset` is how far into the pattern the clip begins, so trimming the front
 * of a clip or splitting it does not shift what is heard after the cut.
 */
export type PatternClip = { id: string; pattern: string; start: number; length: number; offset: number };

export type AudioClip = {
  id: string;
  media: string;
  start: number;
  /** Where in the file the clip begins. */
  offset_s: number;
  duration_s: number;
  gain_db: number;
  fade_in_s: number;
  fade_out_s: number;
};

export const WAVES = ["sine", "triangle", "sawtooth", "square"] as const;
export type Wave = (typeof WAVES)[number];

export type SynthParams = {
  preset: string;
  wave: Wave;
  /** Stacked oscillators, spread by `detune` cents. */
  voices: number;
  detune: number;
  /** Level of a sine an octave below. */
  sub: number;
  octave: number;
  attack: number;
  decay: number;
  sustain: number;
  release: number;
  cutoff: number;
  resonance: number;
  /** How many octaves the envelope opens the filter. */
  env: number;
  /** Semitones the pitch falls from at the start of a note — the 808 thump. */
  drop: number;
  drive: number;
};

export type Mix = {
  volume_db: number;
  pan: number;
  mute: boolean;
  solo: boolean;
  eq: { low: number; mid: number; high: number };
  reverb: number;
  delay: number;
};

type TrackBase = Mix & { id: string; name: string; color: string };
export type DrumTrack = TrackBase & { kind: "drums"; kit: KitName; patterns: DrumPattern[]; clips: PatternClip[] };
export type SynthTrack = TrackBase & { kind: "synth"; synth: SynthParams; patterns: NotePattern[]; clips: PatternClip[] };
export type AudioTrack = TrackBase & { kind: "audio"; clips: AudioClip[] };
export type Track = DrumTrack | SynthTrack | AudioTrack;
export type TrackKind = Track["kind"];

export type Media = {
  id: string;
  name: string;
  /** Store path, served by /api/audio. */
  path: string;
  duration_s: number;
  /**
   * Song audio is the AAC preview `producer sync` published, not the lossless
   * file on the studio Mac. Fine to work against; not something to bounce and
   * send back as if it were the original.
   */
  from: "song" | "import" | "recording";
  song?: string;
};

export type Project = {
  id: string;
  name: string;
  /** Bumped by the server on every save, so a stale tab cannot overwrite a newer one. */
  rev: number;
  bpm: number;
  swing: number;
  loop: { on: boolean; start: number; end: number };
  metronome: boolean;
  master: { volume_db: number; limiter: boolean };
  tracks: Track[];
  media: Media[];
  created_at: string;
  updated_at: string;
  updated_by: string;
};

export type ProjectSummary = {
  id: string;
  name: string;
  bpm: number;
  tracks: number;
  bars: number;
  updated_at: string;
  updated_by: string;
};

export const PROJECT_ID = /^[a-z0-9][a-z0-9-]{5,60}$/;
export const MEDIA_EXTENSIONS = ["wav", "mp3", "m4a", "mp4", "aac", "flac", "ogg", "aif", "aiff", "webm"];
export const MEDIA_PATH =
  /^projects\/[a-z0-9][a-z0-9-]{5,60}\/media\/[a-z0-9][a-z0-9-]{5,60}\.(wav|mp3|m4a|mp4|aac|flac|ogg|aif|aiff|webm)$/;
export const SONG_AUDIO_PATH = /^songs\/[a-z0-9][a-z0-9-]*\/audio\/[a-z_]+\.[a-f0-9]{6,40}\.m4a$/;

export const TRACK_COLOURS = ["#ff9e2c", "#6fb8d8", "#7fd4a0", "#c98bdb", "#e8795f", "#d8c56f", "#8f9be8", "#e58fb6"];

export const LIMITS = { bpm: [40, 240], tracks: 32, clips: 400, notes: 2000, patterns: 64, media: 64 } as const;

export function uid(prefix = ""): string {
  const random = Math.random().toString(36).slice(2, 10).padEnd(8, "0");
  return `${prefix}${Date.now().toString(36)}-${random}`;
}

export const SYNTH_PRESETS: Record<string, Omit<SynthParams, "preset">> = {
  Keys: { wave: "triangle", voices: 2, detune: 6, sub: 0, octave: 0, attack: 0.005, decay: 0.5, sustain: 0.35, release: 0.4, cutoff: 2400, resonance: 0.7, env: 1.5, drop: 0, drive: 0 },
  "808": { wave: "sine", voices: 1, detune: 0, sub: 0, octave: -2, attack: 0.002, decay: 1.4, sustain: 0.25, release: 0.25, cutoff: 900, resonance: 0.5, env: 0, drop: 12, drive: 0.55 },
  "Sub bass": { wave: "sine", voices: 1, detune: 0, sub: 0, octave: -2, attack: 0.006, decay: 0.2, sustain: 0.9, release: 0.12, cutoff: 700, resonance: 0.5, env: 0, drop: 0, drive: 0.15 },
  Pluck: { wave: "sawtooth", voices: 1, detune: 0, sub: 0.2, octave: 0, attack: 0.002, decay: 0.18, sustain: 0, release: 0.14, cutoff: 900, resonance: 4, env: 3, drop: 0, drive: 0 },
  Lead: { wave: "square", voices: 2, detune: 12, sub: 0, octave: 0, attack: 0.01, decay: 0.2, sustain: 0.7, release: 0.2, cutoff: 3000, resonance: 1.2, env: 1, drop: 0, drive: 0.1 },
  Pad: { wave: "sawtooth", voices: 3, detune: 14, sub: 0.15, octave: 0, attack: 0.5, decay: 0.8, sustain: 0.8, release: 1.2, cutoff: 1400, resonance: 0.7, env: 0.5, drop: 0, drive: 0 },
};

export function synthPreset(name: string): SynthParams {
  const preset = SYNTH_PRESETS[name] ? name : "Keys";
  return { preset, ...SYNTH_PRESETS[preset] };
}

const X = 0.8;
const A = 1;
const g = 0.45;
const row = (text: string): number[] =>
  [...text.replace(/\s/g, "")].map((c) => (c === "x" ? X : c === "X" ? A : c === "o" ? g : 0));

/** Starting points, one bar each. `X` is an accent, `o` a ghost note. */
export const DRUM_PRESETS: Record<string, Partial<Record<DrumVoice, string>>> = {
  "Four on the floor": {
    kick: "X... x... x... x...",
    clap: ".... x... .... x...",
    hat: "..x. ..x. ..x. ..x.",
    openhat: ".... .... .... ..o.",
  },
  "Boom bap": {
    kick: "X... ...x ..x. ....",
    snare: ".... X... .... X...",
    hat: "x.x. x.x. x.x. x.xo",
  },
  Trap: {
    kick: "X... ..x. .... .x..",
    clap: ".... .... X... ....",
    hat: "x.x. x.xx x.x. xxxx",
    openhat: ".... .... .... ..o.",
  },
  Dembow: {
    kick: "X... x... x... x...",
    snare: "...x ..x. ...x ..x.",
    hat: "x.x. x.x. x.x. x.x.",
  },
  "Half time": {
    kick: "X... .... ..x. ....",
    snare: ".... .... X... ....",
    hat: "x.x. x.x. x.x. x.x.",
    rim: ".... ..o. .... .o..",
  },
};

export function emptyLanes(steps: number): Record<DrumVoice, number[]> {
  return Object.fromEntries(DRUM_VOICES.map((v) => [v, Array<number>(steps).fill(0)])) as Record<DrumVoice, number[]>;
}

export function drumPattern(name: string, preset?: string, steps = 16): DrumPattern {
  const lanes = emptyLanes(steps);
  const source = preset ? DRUM_PRESETS[preset] : undefined;
  if (source) {
    for (const voice of DRUM_VOICES) {
      const hits = row(source[voice] ?? "");
      for (let i = 0; i < steps; i++) lanes[voice][i] = hits[i % 16] ?? 0;
    }
  }
  return { id: uid("p"), name, steps, lanes };
}

export function notePattern(name: string, length = 4): NotePattern {
  return { id: uid("p"), name, length, notes: [] };
}

export function defaultMix(): Mix {
  return { volume_db: 0, pan: 0, mute: false, solo: false, eq: { low: 0, mid: 0, high: 0 }, reverb: 0, delay: 0 };
}

export function newProject(id: string, name: string, author: string): Project {
  const now = new Date().toISOString();
  return {
    id,
    name: name.trim().slice(0, 80) || "Untitled",
    rev: 0,
    bpm: 120,
    swing: 0,
    loop: { on: false, start: 0, end: 16 },
    metronome: false,
    master: { volume_db: 0, limiter: true },
    tracks: [],
    media: [],
    created_at: now,
    updated_at: now,
    updated_by: author,
  };
}

// ── reading a project that came from somewhere else ─────────────────────────

const num = (value: unknown, fallback: number, min: number, max: number): number => {
  const n = typeof value === "number" && Number.isFinite(value) ? value : fallback;
  return Math.min(max, Math.max(min, n));
};
const text = (value: unknown, fallback: string, max = 80): string =>
  (typeof value === "string" ? value : fallback).replace(/\s+/g, " ").trim().slice(0, max) || fallback;
const ident = (value: unknown): string =>
  typeof value === "string" && /^[a-z0-9-]{1,64}$/i.test(value) ? value : uid("x");
const list = (value: unknown, max: number): Record<string, unknown>[] =>
  Array.isArray(value)
    ? (value.filter((v) => v && typeof v === "object").slice(0, max) as Record<string, unknown>[])
    : [];

function normalizeMix(raw: Record<string, unknown>): Mix {
  const eq = (raw.eq ?? {}) as Record<string, unknown>;
  return {
    volume_db: num(raw.volume_db, 0, -60, 12),
    pan: num(raw.pan, 0, -1, 1),
    mute: raw.mute === true,
    solo: raw.solo === true,
    eq: { low: num(eq.low, 0, -18, 18), mid: num(eq.mid, 0, -18, 18), high: num(eq.high, 0, -18, 18) },
    reverb: num(raw.reverb, 0, 0, 1),
    delay: num(raw.delay, 0, 0, 1),
  };
}

function normalizeSynth(raw: Record<string, unknown>): SynthParams {
  const base = synthPreset(String(raw.preset ?? "Keys"));
  return {
    preset: text(raw.preset, base.preset, 24),
    wave: (WAVES as readonly string[]).includes(raw.wave as string) ? (raw.wave as Wave) : base.wave,
    voices: Math.round(num(raw.voices, base.voices, 1, 3)),
    detune: num(raw.detune, base.detune, 0, 50),
    sub: num(raw.sub, base.sub, 0, 1),
    octave: Math.round(num(raw.octave, base.octave, -3, 3)),
    attack: num(raw.attack, base.attack, 0.001, 2),
    decay: num(raw.decay, base.decay, 0.01, 4),
    sustain: num(raw.sustain, base.sustain, 0, 1),
    release: num(raw.release, base.release, 0.01, 4),
    cutoff: num(raw.cutoff, base.cutoff, 60, 16000),
    resonance: num(raw.resonance, base.resonance, 0.1, 18),
    env: num(raw.env, base.env, 0, 5),
    drop: num(raw.drop, base.drop, 0, 24),
    drive: num(raw.drive, base.drive, 0, 1),
  };
}

function normalizePatternClips(raw: unknown, patterns: { id: string }[]): PatternClip[] {
  const known = new Set(patterns.map((p) => p.id));
  return list(raw, LIMITS.clips)
    .filter((c) => known.has(String(c.pattern)))
    .map((c) => ({
      id: ident(c.id),
      pattern: String(c.pattern),
      start: num(c.start, 0, 0, 4000),
      length: num(c.length, 4, STEP_BEATS, 4000),
      offset: num(c.offset, 0, 0, 4000),
    }));
}

function normalizeTrack(raw: Record<string, unknown>, index: number, media: Set<string>): Track | null {
  const base = {
    id: ident(raw.id),
    name: text(raw.name, `Track ${index + 1}`, 40),
    color: typeof raw.color === "string" && /^#[0-9a-f]{6}$/i.test(raw.color)
      ? raw.color
      : TRACK_COLOURS[index % TRACK_COLOURS.length],
    ...normalizeMix(raw),
  };
  if (raw.kind === "drums") {
    const patterns = list(raw.patterns, LIMITS.patterns).map((p, i): DrumPattern => {
      const steps = [16, 32].includes(p.steps as number) ? (p.steps as number) : 16;
      const source = (p.lanes ?? {}) as Record<string, unknown>;
      const lanes = emptyLanes(steps);
      for (const voice of DRUM_VOICES) {
        const hits = Array.isArray(source[voice]) ? (source[voice] as unknown[]) : [];
        for (let s = 0; s < steps; s++) lanes[voice][s] = num(hits[s], 0, 0, 1);
      }
      return { id: ident(p.id), name: text(p.name, `Pattern ${i + 1}`, 40), steps, lanes };
    });
    return {
      ...base,
      kind: "drums",
      kit: (KITS as readonly string[]).includes(raw.kit as string) ? (raw.kit as KitName) : "808",
      patterns,
      clips: normalizePatternClips(raw.clips, patterns),
    };
  }
  if (raw.kind === "synth") {
    const patterns = list(raw.patterns, LIMITS.patterns).map((p, i): NotePattern => {
      const length = num(p.length, 4, 1, 64);
      return {
        id: ident(p.id),
        name: text(p.name, `Pattern ${i + 1}`, 40),
        length,
        notes: list(p.notes, LIMITS.notes).map((n) => ({
          id: ident(n.id),
          pitch: Math.round(num(n.pitch, 60, 0, 127)),
          start: num(n.start, 0, 0, length),
          length: num(n.length, STEP_BEATS, 0.0625, 64),
          velocity: num(n.velocity, 0.8, 0.05, 1),
        })),
      };
    });
    return {
      ...base,
      kind: "synth",
      synth: normalizeSynth((raw.synth ?? {}) as Record<string, unknown>),
      patterns,
      clips: normalizePatternClips(raw.clips, patterns),
    };
  }
  if (raw.kind === "audio") {
    return {
      ...base,
      kind: "audio",
      clips: list(raw.clips, LIMITS.clips)
        .filter((c) => media.has(String(c.media)))
        .map((c) => ({
          id: ident(c.id),
          media: String(c.media),
          start: num(c.start, 0, 0, 4000),
          offset_s: num(c.offset_s, 0, 0, 7200),
          duration_s: num(c.duration_s, 1, 0.01, 7200),
          gain_db: num(c.gain_db, 0, -40, 12),
          fade_in_s: num(c.fade_in_s, 0, 0, 60),
          fade_out_s: num(c.fade_out_s, 0, 0, 60),
        })),
    };
  }
  return null;
}

/**
 * Rebuild a project from untrusted JSON, clamping every number and dropping
 * anything that does not belong. Used on save and on load, so a project that
 * reaches the audio engine is always one it can play.
 */
export function normalizeProject(value: unknown): Project | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  if (typeof raw.id !== "string" || !PROJECT_ID.test(raw.id)) return null;

  const media = list(raw.media, LIMITS.media)
    .filter((m) => typeof m.path === "string" && (MEDIA_PATH.test(m.path) || SONG_AUDIO_PATH.test(m.path)))
    .map((m): Media => ({
      id: ident(m.id),
      name: text(m.name, "Audio", 120),
      path: m.path as string,
      duration_s: num(m.duration_s, 0, 0, 7200),
      from: m.from === "song" || m.from === "recording" ? m.from : "import",
      song: typeof m.song === "string" ? m.song.slice(0, 80) : undefined,
    }));
  const mediaIds = new Set(media.map((m) => m.id));
  const loop = (raw.loop ?? {}) as Record<string, unknown>;
  const master = (raw.master ?? {}) as Record<string, unknown>;
  const loopStart = num(loop.start, 0, 0, 4000);
  const now = new Date().toISOString();

  return {
    id: raw.id,
    name: text(raw.name, "Untitled"),
    rev: Math.round(num(raw.rev, 0, 0, 1e9)),
    bpm: num(raw.bpm, 120, LIMITS.bpm[0], LIMITS.bpm[1]),
    swing: num(raw.swing, 0, 0, 1),
    loop: { on: loop.on === true, start: loopStart, end: Math.max(loopStart + 1, num(loop.end, 16, 0, 4000)) },
    metronome: raw.metronome === true,
    master: { volume_db: num(master.volume_db, 0, -60, 6), limiter: master.limiter !== false },
    tracks: list(raw.tracks, LIMITS.tracks).flatMap((t, i) => normalizeTrack(t, i, mediaIds) ?? []),
    media,
    created_at: typeof raw.created_at === "string" ? raw.created_at.slice(0, 40) : now,
    updated_at: typeof raw.updated_at === "string" ? raw.updated_at.slice(0, 40) : now,
    updated_by: text(raw.updated_by, "", 60),
  };
}
