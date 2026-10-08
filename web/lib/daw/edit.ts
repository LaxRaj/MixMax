import { beatsToSeconds, clipBeats, contentEnd, secondsToBeats } from "./schedule";
import {
  BEATS_PER_BAR,
  DRUM_VOICES,
  LIMITS,
  STEP_BEATS,
  TRACK_COLOURS,
  defaultMix,
  drumPattern,
  emptyLanes,
  notePattern,
  synthPreset,
  uid,
  type AudioClip,
  type AudioTrack,
  type DrumPattern,
  type DrumTrack,
  type DrumVoice,
  type Media,
  type Note,
  type NotePattern,
  type PatternClip,
  type Project,
  type SynthTrack,
  type Track,
} from "./types";

/**
 * Every change a person can make to a project, as a function from one project
 * to the next. Nothing is mutated, so the previous project is the undo step.
 */

export type Edit = (project: Project) => Project;

const MIN_CLIP_BEATS = STEP_BEATS;
const MIN_AUDIO_S = 0.05;

function mapTrack(project: Project, id: string, change: (track: Track) => Track): Project {
  return { ...project, tracks: project.tracks.map((t) => (t.id === id ? change(t) : t)) };
}

const nextColour = (project: Project): string => TRACK_COLOURS[project.tracks.length % TRACK_COLOURS.length];

function uniqueName(project: Project, base: string): string {
  const names = new Set(project.tracks.map((t) => t.name));
  if (!names.has(base)) return base;
  for (let n = 2; ; n++) if (!names.has(`${base} ${n}`)) return `${base} ${n}`;
}

// ── tracks ─────────────────────────────────────────────────────────────────

/** A drum track arrives with a beat already on it: an empty grid teaches nothing. */
export function addDrumTrack(preset = "Boom bap", bars = 4): Edit {
  return (project) => {
    if (project.tracks.length >= LIMITS.tracks) return project;
    const pattern = drumPattern("Beat 1", preset);
    const track: DrumTrack = {
      ...defaultMix(),
      id: uid("t"),
      name: uniqueName(project, "Drums"),
      color: nextColour(project),
      kind: "drums",
      kit: "808",
      patterns: [pattern],
      clips: [{ id: uid("c"), pattern: pattern.id, start: 0, length: bars * BEATS_PER_BAR, offset: 0 }],
    };
    return { ...project, tracks: [...project.tracks, track] };
  };
}

export function addSynthTrack(preset = "Keys", bars = 4): Edit {
  return (project) => {
    if (project.tracks.length >= LIMITS.tracks) return project;
    const pattern = notePattern("Part 1", 2 * BEATS_PER_BAR);
    const track: SynthTrack = {
      ...defaultMix(),
      id: uid("t"),
      name: uniqueName(project, preset),
      color: nextColour(project),
      kind: "synth",
      synth: synthPreset(preset),
      patterns: [pattern],
      clips: [{ id: uid("c"), pattern: pattern.id, start: 0, length: bars * BEATS_PER_BAR, offset: 0 }],
    };
    return { ...project, tracks: [...project.tracks, track] };
  };
}

/** Put a file on the timeline: on `trackId` if given, otherwise on a new track of its own. */
export function addAudio(media: Media, at = 0, trackId?: string): Edit {
  return (project) => {
    const known = project.media.some((m) => m.id === media.id);
    if (!known && project.media.length >= LIMITS.media) return project;
    const clip: AudioClip = {
      id: uid("c"), media: media.id, start: Math.max(0, at), offset_s: 0,
      duration_s: media.duration_s, gain_db: 0, fade_in_s: 0, fade_out_s: 0,
    };
    const withMedia = known ? project : { ...project, media: [...project.media, media] };
    const target = withMedia.tracks.find((t) => t.id === trackId && t.kind === "audio");
    if (target) return mapTrack(withMedia, target.id, (t) => ({ ...(t as AudioTrack), clips: [...(t as AudioTrack).clips, clip] }));
    if (withMedia.tracks.length >= LIMITS.tracks) return project;
    const track: AudioTrack = {
      ...defaultMix(),
      id: uid("t"),
      name: uniqueName(withMedia, media.name.replace(/\.[a-z0-9]+$/i, "").slice(0, 40) || "Audio"),
      color: nextColour(withMedia),
      kind: "audio",
      clips: [clip],
    };
    return { ...withMedia, tracks: [...withMedia.tracks, track] };
  };
}

export function addEmptyAudioTrack(name = "Audio"): Edit {
  return (project) => {
    if (project.tracks.length >= LIMITS.tracks) return project;
    const track: AudioTrack = {
      ...defaultMix(), id: uid("t"), name: uniqueName(project, name), color: nextColour(project), kind: "audio", clips: [],
    };
    return { ...project, tracks: [...project.tracks, track] };
  };
}

export const updateTrack = (id: string, patch: Partial<Track>): Edit => (project) =>
  mapTrack(project, id, (t) => ({ ...t, ...patch }) as Track);

/** Drop media nothing points at any more, so a deleted take does not ride along forever. */
function pruneMedia(project: Project): Project {
  const used = new Set(project.tracks.flatMap((t) => (t.kind === "audio" ? t.clips.map((c) => c.media) : [])));
  const media = project.media.filter((m) => used.has(m.id));
  return media.length === project.media.length ? project : { ...project, media };
}

export const removeTrack = (id: string): Edit => (project) =>
  pruneMedia({ ...project, tracks: project.tracks.filter((t) => t.id !== id) });

export function duplicateTrack(id: string): Edit {
  return (project) => {
    const index = project.tracks.findIndex((t) => t.id === id);
    if (index < 0 || project.tracks.length >= LIMITS.tracks) return project;
    const source = project.tracks[index];
    const copy = {
      ...source,
      id: uid("t"),
      name: uniqueName(project, source.name),
      solo: false,
      clips: source.clips.map((c) => ({ ...c, id: uid("c") })),
    } as Track;
    const tracks = [...project.tracks];
    tracks.splice(index + 1, 0, copy);
    return { ...project, tracks };
  };
}

export function moveTrack(id: string, by: -1 | 1): Edit {
  return (project) => {
    const index = project.tracks.findIndex((t) => t.id === id);
    const target = index + by;
    if (index < 0 || target < 0 || target >= project.tracks.length) return project;
    const tracks = [...project.tracks];
    [tracks[index], tracks[target]] = [tracks[target], tracks[index]];
    return { ...project, tracks };
  };
}

// ── clips ──────────────────────────────────────────────────────────────────

const lengthOf = (track: Track, clip: PatternClip | AudioClip, bpm: number): number =>
  track.kind === "audio" ? clipBeats(clip as AudioClip, bpm) : (clip as PatternClip).length;

function mapClip(project: Project, trackId: string, clipId: string, change: (clip: never, track: Track) => unknown): Project {
  return mapTrack(project, trackId, (track) => ({
    ...track,
    clips: track.clips.map((c) => (c.id === clipId ? change(c as never, track) : c)),
  }) as Track);
}

export function addPatternClip(trackId: string, patternId: string, start: number, length?: number): Edit {
  return (project) =>
    mapTrack(project, trackId, (track) => {
      if (track.kind === "audio" || track.clips.length >= LIMITS.clips) return track;
      const pattern = track.patterns.find((p) => p.id === patternId);
      if (!pattern) return track;
      const natural = track.kind === "drums" ? (pattern as DrumPattern).steps * STEP_BEATS : (pattern as NotePattern).length;
      const clip: PatternClip = { id: uid("c"), pattern: patternId, start: Math.max(0, start), length: length ?? natural, offset: 0 };
      return { ...track, clips: [...track.clips, clip] } as Track;
    });
}

/** Move a clip in time and, when the destination is the same kind of track, across tracks. */
export function moveClip(trackId: string, clipId: string, start: number, toTrackId?: string): Edit {
  return (project) => {
    const from = project.tracks.find((t) => t.id === trackId);
    const clip = from?.clips.find((c) => c.id === clipId);
    if (!from || !clip) return project;
    const moved = { ...clip, start: Math.max(0, start) };
    const to = project.tracks.find((t) => t.id === toTrackId);
    // Patterns belong to their track, so only audio can change tracks.
    if (!to || to.id === from.id || to.kind !== "audio" || from.kind !== "audio") {
      return mapClip(project, trackId, clipId, () => moved);
    }
    return {
      ...project,
      tracks: project.tracks.map((t) => {
        if (t.id === from.id) return { ...t, clips: t.clips.filter((c) => c.id !== clipId) } as Track;
        if (t.id === to.id) return { ...t, clips: [...(t as AudioTrack).clips, moved as AudioClip] } as Track;
        return t;
      }),
    };
  };
}

/** Drag the right-hand edge. Audio cannot be made longer than the file. */
export function trimClipEnd(trackId: string, clipId: string, end: number, media?: Media): Edit {
  return (project) =>
    mapClip(project, trackId, clipId, (clip: PatternClip | AudioClip, track) => {
      if (track.kind !== "audio") {
        return { ...clip, length: Math.max(MIN_CLIP_BEATS, end - clip.start) };
      }
      const audio = clip as AudioClip;
      const most = (media?.duration_s ?? Infinity) - audio.offset_s;
      const wanted = beatsToSeconds(end - audio.start, project.bpm);
      return { ...audio, duration_s: Math.max(MIN_AUDIO_S, Math.min(most, wanted)) };
    });
}

/** Drag the left-hand edge: the clip starts later, and what it plays starts later with it. */
export function trimClipStart(trackId: string, clipId: string, start: number): Edit {
  return (project) =>
    mapClip(project, trackId, clipId, (clip: PatternClip | AudioClip, track) => {
      if (track.kind !== "audio") {
        const pattern = clip as PatternClip;
        const end = pattern.start + pattern.length;
        // Dragging left past the pattern's own start would need a negative offset.
        const next = Math.max(0, pattern.start - pattern.offset, Math.min(start, end - MIN_CLIP_BEATS));
        return { ...pattern, start: next, length: end - next, offset: pattern.offset + (next - pattern.start) };
      }
      const audio = clip as AudioClip;
      const earliest = Math.max(0, audio.start - secondsToBeats(audio.offset_s, project.bpm));
      const latest = audio.start + secondsToBeats(audio.duration_s - MIN_AUDIO_S, project.bpm);
      const next = Math.max(earliest, Math.min(start, latest));
      const shift = beatsToSeconds(next - audio.start, project.bpm);
      return { ...audio, start: next, offset_s: Math.max(0, audio.offset_s + shift), duration_s: audio.duration_s - shift };
    });
}

/** Cut a clip in two at `beat`. Returns the project unchanged if the beat is not inside it. */
export function splitClip(trackId: string, clipId: string, beat: number): Edit {
  return (project) =>
    mapTrack(project, trackId, (track) => {
      const clip = track.clips.find((c) => c.id === clipId);
      if (!clip || track.clips.length >= LIMITS.clips) return track;
      const length = lengthOf(track, clip, project.bpm);
      if (beat <= clip.start + 1e-6 || beat >= clip.start + length - 1e-6) return track;
      const cut = beat - clip.start;

      let halves: (PatternClip | AudioClip)[];
      if (track.kind === "audio") {
        const audio = clip as AudioClip;
        const cut_s = beatsToSeconds(cut, project.bpm);
        halves = [
          { ...audio, duration_s: cut_s, fade_out_s: 0 },
          { ...audio, id: uid("c"), start: beat, offset_s: audio.offset_s + cut_s, duration_s: audio.duration_s - cut_s, fade_in_s: 0 },
        ];
      } else {
        const pattern = clip as PatternClip;
        halves = [
          { ...pattern, length: cut },
          { ...pattern, id: uid("c"), start: beat, length: length - cut, offset: pattern.offset + cut },
        ];
      }
      return { ...track, clips: track.clips.flatMap((c) => (c.id === clipId ? halves : [c])) } as Track;
    });
}

/** A copy placed directly after the original. */
export function duplicateClip(trackId: string, clipId: string): Edit {
  return (project) =>
    mapTrack(project, trackId, (track) => {
      const clip = track.clips.find((c) => c.id === clipId);
      if (!clip || track.clips.length >= LIMITS.clips) return track;
      const copy = { ...clip, id: uid("c"), start: clip.start + lengthOf(track, clip, project.bpm) };
      return { ...track, clips: [...track.clips, copy] } as Track;
    });
}

export const removeClip = (trackId: string, clipId: string): Edit => (project) =>
  pruneMedia(mapTrack(project, trackId, (track) => ({ ...track, clips: track.clips.filter((c) => c.id !== clipId) }) as Track));

export const updateAudioClip = (trackId: string, clipId: string, patch: Partial<AudioClip>): Edit => (project) =>
  mapClip(project, trackId, clipId, (clip: AudioClip) => ({ ...clip, ...patch }));

// ── patterns ───────────────────────────────────────────────────────────────

function mapPattern<P extends DrumPattern | NotePattern>(
  project: Project, trackId: string, patternId: string, change: (pattern: P) => P,
): Project {
  return mapTrack(project, trackId, (track) => {
    if (track.kind === "audio") return track;
    return { ...track, patterns: track.patterns.map((p) => (p.id === patternId ? change(p as P) : p)) } as Track;
  });
}

/** A new pattern on a track, optionally a copy of another. Returns its id through `created`. */
export function addPattern(trackId: string, copyOf?: string, created?: (id: string) => void): Edit {
  return (project) =>
    mapTrack(project, trackId, (track) => {
      if (track.kind === "audio" || track.patterns.length >= LIMITS.patterns) return track;
      const source = track.patterns.find((p) => p.id === copyOf);
      const label = track.kind === "drums" ? "Beat" : "Part";
      const name = `${label} ${track.patterns.length + 1}`;
      const pattern = source
        ? { ...structuredClone(source), id: uid("p"), name }
        : track.kind === "drums" ? drumPattern(name) : notePattern(name);
      created?.(pattern.id);
      return { ...track, patterns: [...track.patterns, pattern] } as Track;
    });
}

/** Deleting a pattern takes its clips with it; the last pattern cannot be deleted. */
export function removePattern(trackId: string, patternId: string): Edit {
  return (project) =>
    mapTrack(project, trackId, (track) => {
      if (track.kind === "audio" || track.patterns.length <= 1) return track;
      return {
        ...track,
        patterns: track.patterns.filter((p) => p.id !== patternId),
        clips: track.clips.filter((c) => c.pattern !== patternId),
      } as Track;
    });
}

export const renamePattern = (trackId: string, patternId: string, name: string): Edit => (project) =>
  mapPattern(project, trackId, patternId, (p) => ({ ...p, name: name.slice(0, 40) }));

export function setStep(trackId: string, patternId: string, voice: DrumVoice, step: number, velocity: number): Edit {
  return (project) =>
    mapPattern<DrumPattern>(project, trackId, patternId, (pattern) => {
      if (step < 0 || step >= pattern.steps) return pattern;
      const lane = [...pattern.lanes[voice]];
      lane[step] = velocity;
      return { ...pattern, lanes: { ...pattern.lanes, [voice]: lane } };
    });
}

/** One bar or two. Going longer repeats the first bar; going shorter keeps it. */
export function setPatternSteps(trackId: string, patternId: string, steps: number): Edit {
  return (project) =>
    mapPattern<DrumPattern>(project, trackId, patternId, (pattern) => {
      if (steps === pattern.steps) return pattern;
      const lanes = emptyLanes(steps);
      for (const voice of DRUM_VOICES) {
        for (let i = 0; i < steps; i++) lanes[voice][i] = pattern.lanes[voice][i % pattern.steps] ?? 0;
      }
      return { ...pattern, steps, lanes };
    });
}

export const fillPattern = (trackId: string, patternId: string, preset: string | null): Edit => (project) =>
  mapPattern<DrumPattern>(project, trackId, patternId, (pattern) => ({
    ...pattern,
    lanes: preset ? drumPattern("", preset, pattern.steps).lanes : emptyLanes(pattern.steps),
  }));

export function addNote(trackId: string, patternId: string, note: Omit<Note, "id">, created?: (id: string) => void): Edit {
  return (project) =>
    mapPattern<NotePattern>(project, trackId, patternId, (pattern) => {
      if (pattern.notes.length >= LIMITS.notes) return pattern;
      const id = uid("n");
      created?.(id);
      return { ...pattern, notes: [...pattern.notes, { ...note, id }] };
    });
}

export const updateNote = (trackId: string, patternId: string, noteId: string, patch: Partial<Note>): Edit => (project) =>
  mapPattern<NotePattern>(project, trackId, patternId, (pattern) => ({
    ...pattern,
    notes: pattern.notes.map((n) => {
      if (n.id !== noteId) return n;
      const next = { ...n, ...patch };
      const start = Math.max(0, Math.min(next.start, pattern.length - 0.0625));
      return { ...next, start, pitch: Math.max(0, Math.min(127, Math.round(next.pitch))), length: Math.max(0.0625, next.length) };
    }),
  }));

export const removeNote = (trackId: string, patternId: string, noteId: string): Edit => (project) =>
  mapPattern<NotePattern>(project, trackId, patternId, (pattern) => ({
    ...pattern,
    notes: pattern.notes.filter((n) => n.id !== noteId),
  }));

/** Shortening a part drops the notes that no longer fit, rather than leaving them silently unplayed. */
export const setPatternLength = (trackId: string, patternId: string, length: number): Edit => (project) =>
  mapPattern<NotePattern>(project, trackId, patternId, (pattern) => ({
    ...pattern,
    length,
    notes: pattern.notes.filter((n) => n.start < length),
  }));

/** Set the loop to the whole arrangement, rounded out to full bars. */
export const loopAll: Edit = (project) => {
  const end = Math.max(BEATS_PER_BAR, Math.ceil(contentEnd(project) / BEATS_PER_BAR) * BEATS_PER_BAR);
  return { ...project, loop: { on: true, start: 0, end } };
};

// ── history ────────────────────────────────────────────────────────────────

export type History = {
  project: Project;
  past: Project[];
  future: Project[];
  /** Counts edits, so the saver can tell whether what it saved is still current. */
  edits: number;
  lastKey: string | null;
  lastAt: number;
};

export type HistoryAction =
  /** `key` groups a run of the same gesture — a fader drag — into one undo step. */
  | { type: "edit"; edit: Edit; key?: string; at: number }
  | { type: "undo" }
  | { type: "redo" }
  | { type: "load"; project: Project }
  /** The server accepted a save; take its revision without disturbing anything else. */
  | { type: "saved"; rev: number; updated_at: string };

const MAX_UNDO = 100;
const COALESCE_MS = 900;

export function initialHistory(project: Project): History {
  return { project, past: [], future: [], edits: 0, lastKey: null, lastAt: 0 };
}

export function historyReducer(state: History, action: HistoryAction): History {
  switch (action.type) {
    case "edit": {
      const project = action.edit(state.project);
      if (project === state.project) return state;
      const merges = action.key !== undefined && action.key === state.lastKey && action.at - state.lastAt < COALESCE_MS;
      return {
        project,
        past: merges ? state.past : [...state.past, state.project].slice(-MAX_UNDO),
        future: [],
        edits: state.edits + 1,
        lastKey: action.key ?? null,
        lastAt: action.at,
      };
    }
    case "undo": {
      const previous = state.past[state.past.length - 1];
      if (!previous) return state;
      return {
        // The revision belongs to the server, not to the edit being undone.
        project: { ...previous, rev: state.project.rev },
        past: state.past.slice(0, -1),
        future: [state.project, ...state.future],
        edits: state.edits + 1,
        lastKey: null,
        lastAt: 0,
      };
    }
    case "redo": {
      const next = state.future[0];
      if (!next) return state;
      return {
        project: { ...next, rev: state.project.rev },
        past: [...state.past, state.project],
        future: state.future.slice(1),
        edits: state.edits + 1,
        lastKey: null,
        lastAt: 0,
      };
    }
    case "load":
      return initialHistory(action.project);
    case "saved":
      return { ...state, project: { ...state.project, rev: action.rev, updated_at: action.updated_at } };
  }
}
