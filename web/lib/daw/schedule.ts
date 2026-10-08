import {
  BEATS_PER_BAR,
  DRUM_VOICES,
  STEP_BEATS,
  type AudioClip,
  type DrumVoice,
  type Project,
  type ProjectSummary,
  type Track,
} from "./types";

/**
 * What sounds when, worked out from the project alone.
 *
 * Nothing here touches Web Audio, so playback and the offline bounce ask the
 * same question of the same function and cannot disagree about the answer.
 */

export type DrumEvent = { kind: "drum"; track: string; voice: DrumVoice; beat: number; velocity: number };
export type NoteEvent = {
  kind: "note";
  track: string;
  pitch: number;
  beat: number;
  /** Beats, already cut short where the clip ends. */
  length: number;
  velocity: number;
};
export type TimelineEvent = DrumEvent | NoteEvent;

/** Full swing pushes every other sixteenth back to the triplet. */
const SWING_MAX_BEATS = 1 / 12;

export const beatsToSeconds = (beats: number, bpm: number): number => (beats * 60) / bpm;
export const secondsToBeats = (seconds: number, bpm: number): number => (seconds * bpm) / 60;

export function clipBeats(clip: AudioClip, bpm: number): number {
  return secondsToBeats(clip.duration_s, bpm);
}

export function snap(beat: number, grid: number): number {
  return grid > 0 ? Math.round(beat / grid) * grid : beat;
}

/** "3.2.1" — bar, beat, sixteenth — the way a sequencer counts. */
export function barBeat(beat: number): string {
  const safe = Math.max(0, beat);
  const bar = Math.floor(safe / BEATS_PER_BAR) + 1;
  const inBar = safe % BEATS_PER_BAR;
  return `${bar}.${Math.floor(inBar) + 1}.${Math.floor((inBar % 1) / STEP_BEATS) + 1}`;
}

/** A track is heard unless it is muted, or another track is soloed and it is not. */
export function audible(track: Track, project: Project): boolean {
  if (track.mute) return false;
  return track.solo || !project.tracks.some((t) => t.solo);
}

/** The last beat anything is placed on. */
export function contentEnd(project: Project): number {
  let end = 0;
  for (const track of project.tracks) {
    for (const clip of track.clips) {
      const length = track.kind === "audio" ? clipBeats(clip as AudioClip, project.bpm) : (clip as { length: number }).length;
      end = Math.max(end, clip.start + length);
    }
  }
  return end;
}

export function summarize(project: Project): ProjectSummary {
  return {
    id: project.id,
    name: project.name,
    bpm: project.bpm,
    tracks: project.tracks.length,
    bars: Math.ceil(contentEnd(project) / BEATS_PER_BAR),
    updated_at: project.updated_at,
    updated_by: project.updated_by,
  };
}

/**
 * Every drum hit and note that starts in `[from, to)`.
 *
 * A pattern repeats to fill its clip, and a note that would ring past the end
 * of its clip is cut there — otherwise shortening a clip would not shorten
 * what you hear.
 */
export function eventsBetween(project: Project, from: number, to: number): TimelineEvent[] {
  const events: TimelineEvent[] = [];
  const swing = project.swing * SWING_MAX_BEATS;

  for (const track of project.tracks) {
    if (track.kind === "audio") continue;
    for (const clip of track.clips) {
      const clipEnd = clip.start + clip.length;
      if (clipEnd <= from - SWING_MAX_BEATS || clip.start >= to) continue;

      // Where the pattern's first step would fall if the clip had not been trimmed.
      const origin = clip.start - clip.offset;

      if (track.kind === "drums") {
        const pattern = track.patterns.find((p) => p.id === clip.pattern);
        if (!pattern) continue;
        // Walk the steps the window covers; one swung step early so a delayed hit is not missed.
        const first = Math.max(
          Math.ceil((clip.start - origin) / STEP_BEATS - 1e-9),
          Math.floor((from - SWING_MAX_BEATS - origin) / STEP_BEATS),
        );
        for (let step = first; ; step++) {
          const straight = origin + step * STEP_BEATS;
          if (straight >= clipEnd - 1e-9 || straight >= to) break;
          const beat = straight + (step % 2 === 1 ? swing : 0);
          if (beat < from || beat >= to) continue;
          const column = step % pattern.steps;
          for (const voice of DRUM_VOICES) {
            const velocity = pattern.lanes[voice][column];
            if (velocity > 0) events.push({ kind: "drum", track: track.id, voice, beat, velocity });
          }
        }
        continue;
      }

      const pattern = track.patterns.find((p) => p.id === clip.pattern);
      if (!pattern || pattern.length <= 0) continue;
      const firstRepeat = Math.max(0, Math.floor((Math.max(from, clip.start) - origin) / pattern.length));
      for (let repeat = firstRepeat; origin + repeat * pattern.length < Math.min(to, clipEnd); repeat++) {
        const repeatStart = origin + repeat * pattern.length;
        for (const note of pattern.notes) {
          const beat = repeatStart + note.start;
          if (beat < from || beat >= to || beat < clip.start - 1e-9 || beat >= clipEnd - 1e-9) continue;
          events.push({
            kind: "note",
            track: track.id,
            pitch: note.pitch,
            beat,
            length: Math.min(note.length, clipEnd - beat),
            velocity: note.velocity,
          });
        }
      }
    }
  }
  return events.sort((a, b) => a.beat - b.beat);
}

export type ClipStart = {
  track: string;
  clip: AudioClip;
  /** The beat it starts sounding at, within the window asked for. */
  beat: number;
  /** Seconds of the clip already behind that beat. */
  skip_s: number;
};

/**
 * Audio clips to start for `[from, to)`. With `spanning`, clips already under
 * way at `from` are included, entered part-way through — what pressing play in
 * the middle of a clip needs.
 */
export function clipsBetween(project: Project, from: number, to: number, spanning: boolean): ClipStart[] {
  const starts: ClipStart[] = [];
  for (const track of project.tracks) {
    if (track.kind !== "audio") continue;
    for (const clip of track.clips) {
      const end = clip.start + clipBeats(clip, project.bpm);
      if (clip.start >= from && clip.start < to) {
        starts.push({ track: track.id, clip, beat: clip.start, skip_s: 0 });
      } else if (spanning && clip.start < from && end > from) {
        starts.push({ track: track.id, clip, beat: from, skip_s: beatsToSeconds(from - clip.start, project.bpm) });
      }
    }
  }
  return starts;
}
