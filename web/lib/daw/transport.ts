import { syncGraph, type Graph } from "./graph";
import { beatsToSeconds, clipsBetween, contentEnd, eventsBetween, type ClipStart } from "./schedule";
import { BEATS_PER_BAR, type Project } from "./types";
import { playClick, playClip, playDrum, playNote } from "./voices";

/**
 * Live playback.
 *
 * A timer wakes every few milliseconds and schedules whatever starts in the
 * next slice of the timeline, reading the project as it is right then. That
 * is what lets a step be toggled or a note dragged while the loop plays and be
 * heard the next time round: nothing is scheduled further ahead than
 * `HORIZON_S`.
 */

const TICK_MS = 25;
const HORIZON_S = 0.14;
/** Keep going this far past the last clip, so reverb and release tails are heard. */
const RUN_OUT_BEATS = 8;

type Anchor = { time: number; beat: number; bpm: number };
type Playing = { source: AudioBufferSourceNode; gain: GainNode; ends: number };

export class Transport {
  playing = false;
  onStop: (() => void) | null = null;

  private timer: number | null = null;
  /** Where the timeline was, and at what tempo, from a given moment on. */
  private anchors: Anchor[] = [];
  private headBeat = 0;
  private headTime = 0;
  private parked = 0;
  private voices = new Set<AudioScheduledSourceNode>();
  private clips = new Set<Playing>();

  constructor(
    private ctx: AudioContext,
    private graph: Graph,
    private project: () => Project,
    private buffer: (media: string) => AudioBuffer | undefined,
  ) {}

  /** The playhead, in beats. */
  position(): number {
    return this.playing ? this.beatAt(this.ctx.currentTime) : this.parked;
  }

  /** Which beat the timeline is on at a given context time, while playing. */
  beatAt(time: number): number {
    let anchor = this.anchors[0];
    if (!anchor) return this.parked;
    for (const candidate of this.anchors) if (candidate.time <= time) anchor = candidate;
    return anchor.beat + ((Math.max(time, anchor.time) - anchor.time) * anchor.bpm) / 60;
  }

  async play(from: number = this.parked): Promise<void> {
    if (this.playing) this.halt();
    if (this.ctx.state !== "running") await this.ctx.resume();
    const project = this.project();
    syncGraph(this.graph, project, false);

    this.playing = true;
    this.headBeat = Math.max(0, from);
    // A short lead-in, so the first hit is not scheduled in the past.
    this.headTime = this.ctx.currentTime + 0.06;
    this.anchors = [{ time: this.headTime, beat: this.headBeat, bpm: project.bpm }];
    this.startSpanning(this.headBeat, this.headTime);
    this.tick();
    this.timer = window.setInterval(() => this.tick(), TICK_MS);
  }

  stop(): void {
    if (!this.playing) return;
    this.parked = this.position();
    this.halt();
    this.onStop?.();
  }

  /** Move the playhead; if playing, carry on from there. */
  seek(beat: number): void {
    const target = Math.max(0, beat);
    if (this.playing) void this.play(target);
    else this.parked = target;
  }

  /** Audio clips were moved, trimmed or deleted: restart the ones under the playhead. */
  refreshClips(): void {
    if (!this.playing) return;
    const now = this.ctx.currentTime;
    this.cutClips(now);
    const here = this.position();
    const project = this.project();
    for (const start of clipsBetween(project, here, this.headBeat, true)) {
      this.startClip(start, now + beatsToSeconds(start.beat - here, project.bpm));
    }
  }

  dispose(): void {
    this.halt();
  }

  private halt(): void {
    if (this.timer !== null) window.clearInterval(this.timer);
    this.timer = null;
    this.playing = false;
    for (const voice of this.voices) {
      try {
        voice.stop();
      } catch {
        // Already ended.
      }
    }
    this.voices.clear();
    this.cutClips(this.ctx.currentTime);
  }

  private cutClips(time: number): void {
    for (const playing of this.clips) {
      playing.gain.gain.cancelScheduledValues(time);
      playing.gain.gain.setTargetAtTime(0, time, 0.003);
      try {
        playing.source.stop(time + 0.02);
      } catch {
        // Already ended.
      }
    }
    this.clips.clear();
  }

  private track = (source: AudioScheduledSourceNode): void => {
    this.voices.add(source);
    source.addEventListener("ended", () => this.voices.delete(source), { once: true });
  };

  private startClip(start: ClipStart, time: number): void {
    const buffer = this.buffer(start.clip.media);
    const strip = this.graph.strips.get(start.track);
    if (!buffer || !strip) return;
    const playing = playClip(this.ctx, strip.input, start.clip, buffer, Math.max(time, this.ctx.currentTime), start.skip_s);
    if (!playing) return;
    this.clips.add(playing);
    playing.source.addEventListener("ended", () => this.clips.delete(playing), { once: true });
  }

  private startSpanning(beat: number, time: number): void {
    const project = this.project();
    for (const start of clipsBetween(project, beat, beat, true)) this.startClip(start, time);
  }

  private tick(): void {
    const project = this.project();
    const horizon = this.ctx.currentTime + HORIZON_S;

    // A tempo change takes hold from the edge of what is already scheduled.
    const current = this.anchors[this.anchors.length - 1];
    if (current.bpm !== project.bpm) {
      this.anchors.push({ time: this.headTime, beat: this.headBeat, bpm: project.bpm });
      // Audio does not stretch, so every clip now sits at a different time.
      this.refreshClips();
    }
    const beatsPerSecond = project.bpm / 60;
    const loop = project.loop.on && project.loop.end > project.loop.start ? project.loop : null;

    while (this.headTime < horizon) {
      let to = this.headBeat + (horizon - this.headTime) * beatsPerSecond;
      const wraps = loop !== null && this.headBeat < loop.end && to >= loop.end;
      if (wraps) to = loop.end;
      this.schedule(project, this.headBeat, to, this.headTime);
      this.headTime += (to - this.headBeat) / beatsPerSecond;
      this.headBeat = to;
      if (!wraps) break;

      this.cutClips(this.headTime);
      this.headBeat = loop.start;
      this.anchors.push({ time: this.headTime, beat: loop.start, bpm: project.bpm });
      this.startSpanning(loop.start, this.headTime);
    }
    if (this.anchors.length > 8) this.anchors.splice(0, this.anchors.length - 8);

    const end = contentEnd(project);
    if (!loop && end > 0 && this.position() > end + RUN_OUT_BEATS) {
      this.parked = 0;
      this.halt();
      this.onStop?.();
    }
  }

  private schedule(project: Project, from: number, to: number, timeAtFrom: number): void {
    const at = (beat: number) => timeAtFrom + beatsToSeconds(beat - from, project.bpm);

    for (const event of eventsBetween(project, from, to)) {
      const strip = this.graph.strips.get(event.track);
      const track = project.tracks.find((t) => t.id === event.track);
      if (!strip || !track) continue;
      if (event.kind === "drum" && track.kind === "drums") {
        playDrum(this.ctx, strip.input, track.kit, event.voice, at(event.beat), event.velocity, this.track);
      } else if (event.kind === "note" && track.kind === "synth") {
        playNote(
          this.ctx, strip.input, track.synth, event.pitch, at(event.beat),
          beatsToSeconds(event.length, project.bpm), event.velocity, this.track,
        );
      }
    }
    // Clips already under way at `from` were started when playback began or the loop wrapped.
    for (const start of clipsBetween(project, from, to, false)) this.startClip(start, at(start.beat));
    if (project.metronome) {
      for (let beat = Math.ceil(from - 1e-9); beat < to; beat++) {
        playClick(this.ctx, this.graph.click, at(beat), beat % BEATS_PER_BAR === 0, this.track);
      }
    }
  }
}
