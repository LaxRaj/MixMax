import { createGraph, syncGraph } from "./graph";
import { audible, beatsToSeconds, clipsBetween, contentEnd, eventsBetween } from "./schedule";
import type { Project } from "./types";
import { playClip, playDrum, playNote } from "./voices";

/**
 * The bounce: the project rendered faster than real time into a WAV.
 *
 * It measures nothing the pipeline cares about — loudness, true peak and the
 * intake checks stay with `producer` on the studio Mac. The one thing it does
 * guarantee is that the file is not clipped, because a clipped file is the one
 * problem intake cannot forgive.
 */

export const EXPORT_SAMPLE_RATE = 44100;
/** Sample peak the bounce is held under. */
const CEILING_DB = -1;
const TAIL_S = 3;
const SILENCE = 10 ** (-72 / 20);

export type Bounce = {
  buffer: AudioBuffer;
  /** Sample peak of the file as written, dBFS. */
  peak_db: number;
  /** How far the whole mix was turned down to stay under the ceiling; 0 if it already was. */
  trimmed_db: number;
  duration_s: number;
};

export type BounceOptions = {
  /** Track ids to include. Muted and un-soloed tracks are left out either way. */
  tracks?: Set<string>;
  /** Bounce only this stretch, in beats. */
  from?: number;
  to?: number;
};

export async function renderProject(
  project: Project,
  buffers: Map<string, AudioBuffer>,
  options: BounceOptions = {},
): Promise<Bounce> {
  const included = project.tracks.filter(
    (t) => audible(t, project) && (!options.tracks || options.tracks.has(t.id)),
  );
  // Everything left out is simply absent, so solo flags no longer mean anything.
  const mix: Project = { ...project, tracks: included.map((t) => ({ ...t, solo: false })) };

  const from = options.from ?? 0;
  const to = options.to ?? contentEnd(mix);
  if (to <= from) throw new Error("There is nothing on the timeline to export.");

  const seconds = beatsToSeconds(to - from, mix.bpm) + TAIL_S;
  const ctx = new OfflineAudioContext(2, Math.ceil(seconds * EXPORT_SAMPLE_RATE), EXPORT_SAMPLE_RATE);
  const graph = createGraph(ctx, false);
  syncGraph(graph, mix, false);
  const at = (beat: number) => beatsToSeconds(beat - from, mix.bpm);
  const byId = new Map(mix.tracks.map((t) => [t.id, t]));

  for (const event of eventsBetween(mix, from, to)) {
    const track = byId.get(event.track);
    const strip = graph.strips.get(event.track);
    if (!track || !strip) continue;
    if (event.kind === "drum" && track.kind === "drums") {
      playDrum(ctx, strip.input, track.kit, event.voice, at(event.beat), event.velocity);
    } else if (event.kind === "note" && track.kind === "synth") {
      playNote(ctx, strip.input, track.synth, event.pitch, at(event.beat), beatsToSeconds(event.length, mix.bpm), event.velocity);
    }
  }
  for (const start of clipsBetween(mix, from, to, true)) {
    const buffer = buffers.get(start.clip.media);
    const strip = graph.strips.get(start.track);
    if (!buffer || !strip) continue;
    const played = playClip(ctx, strip.input, start.clip, buffer, at(start.beat), start.skip_s);
    // A range bounce ends where the range does, even mid-clip.
    if (played && options.to !== undefined) played.source.stop(at(to));
  }

  const rendered = await ctx.startRendering();
  return finish(rendered);
}

/** Drop the silent tail and hold the peak under the ceiling. */
function finish(rendered: AudioBuffer): Bounce {
  const channels = [rendered.getChannelData(0), rendered.getChannelData(1)];
  let peak = 0;
  let last = 0;
  for (const data of channels) {
    for (let i = 0; i < data.length; i++) {
      const value = Math.abs(data[i]);
      if (value > peak) peak = value;
      if (value > SILENCE && i > last) last = i;
    }
  }
  if (peak <= SILENCE) throw new Error("The export came out silent. Check that a track is not muted.");

  // Trailing silence drags an integrated loudness reading down; leave only a breath of it.
  const length = Math.min(rendered.length, last + Math.floor(0.05 * rendered.sampleRate));
  const ceiling = 10 ** (CEILING_DB / 20);
  const scale = peak > ceiling ? ceiling / peak : 1;

  const buffer = new AudioBuffer({ numberOfChannels: 2, length, sampleRate: rendered.sampleRate });
  channels.forEach((data, channel) => {
    const out = buffer.getChannelData(channel);
    for (let i = 0; i < length; i++) out[i] = data[i] * scale;
  });
  return {
    buffer,
    peak_db: 20 * Math.log10(peak * scale),
    trimmed_db: scale < 1 ? -20 * Math.log10(scale) : 0,
    duration_s: length / rendered.sampleRate,
  };
}

/** 24-bit PCM WAV: what the pipeline standardizes to. */
export function encodeWav(buffer: AudioBuffer): Blob {
  const channels = buffer.numberOfChannels;
  const frames = buffer.length;
  const bytesPerSample = 3;
  const dataBytes = frames * channels * bytesPerSample;
  const out = new DataView(new ArrayBuffer(44 + dataBytes));
  const ascii = (offset: number, value: string) => {
    for (let i = 0; i < value.length; i++) out.setUint8(offset + i, value.charCodeAt(i));
  };
  ascii(0, "RIFF");
  out.setUint32(4, 36 + dataBytes, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  out.setUint32(16, 16, true);
  out.setUint16(20, 1, true);
  out.setUint16(22, channels, true);
  out.setUint32(24, buffer.sampleRate, true);
  out.setUint32(28, buffer.sampleRate * channels * bytesPerSample, true);
  out.setUint16(32, channels * bytesPerSample, true);
  out.setUint16(34, 24, true);
  ascii(36, "data");
  out.setUint32(40, dataBytes, true);

  const data = Array.from({ length: channels }, (_, c) => buffer.getChannelData(c));
  let offset = 44;
  for (let i = 0; i < frames; i++) {
    for (let c = 0; c < channels; c++) {
      const clamped = Math.max(-1, Math.min(1, data[c][i]));
      const value = Math.round(clamped * 8388607);
      out.setUint8(offset, value & 0xff);
      out.setUint8(offset + 1, (value >> 8) & 0xff);
      out.setUint8(offset + 2, (value >> 16) & 0xff);
      offset += 3;
    }
  }
  return new Blob([out.buffer], { type: "audio/wav" });
}

/** Min/max pairs per bucket, for drawing a waveform. */
export function peaksOf(buffer: AudioBuffer, buckets: number): Float32Array {
  const peaks = new Float32Array(buckets * 2);
  const size = buffer.length / buckets;
  const channels = Array.from({ length: buffer.numberOfChannels }, (_, c) => buffer.getChannelData(c));
  // Reading every sample of a long file is slow; a stride is plenty for a picture.
  const stride = Math.max(1, Math.floor(size / 64));
  for (let b = 0; b < buckets; b++) {
    let min = 0;
    let max = 0;
    const end = Math.min(buffer.length, Math.floor((b + 1) * size));
    for (let i = Math.floor(b * size); i < end; i += stride) {
      for (const data of channels) {
        if (data[i] < min) min = data[i];
        if (data[i] > max) max = data[i];
      }
    }
    peaks[b * 2] = min;
    peaks[b * 2 + 1] = max;
  }
  return peaks;
}
