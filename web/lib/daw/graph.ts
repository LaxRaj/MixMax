import { audible } from "./schedule";
import type { Project, Track } from "./types";
import { dbToGain } from "./voices";

/**
 * The mixer: one channel strip per track, two shared effects, one master.
 *
 *   track input → low shelf → mid bell → high shelf → fader → pan ─┬→ master
 *                                                                  ├→ reverb send
 *                                                                  └→ delay send
 *   master input → limiter → master fader → out
 *
 * Built the same way on a live context and an offline one, so the bounce is
 * the mix that was heard.
 */

export type Strip = {
  input: GainNode;
  low: BiquadFilterNode;
  mid: BiquadFilterNode;
  high: BiquadFilterNode;
  fader: GainNode;
  pan: StereoPannerNode;
  reverb: GainNode;
  delay: GainNode;
  meter: AnalyserNode | null;
};

export type Graph = {
  ctx: BaseAudioContext;
  strips: Map<string, Strip>;
  masterIn: GainNode;
  limiter: DynamicsCompressorNode;
  bypass: GainNode;
  limited: GainNode;
  masterOut: GainNode;
  masterMeter: AnalyserNode | null;
  reverbIn: GainNode;
  delayIn: GainNode;
  delayLine: DelayNode;
  /** The metronome goes straight to the speakers and is never bounced. */
  click: GainNode;
};

/** A plain room: decaying noise from a fixed seed, so every render uses the same one. */
function impulse(ctx: BaseAudioContext): AudioBuffer {
  const seconds = 2.2;
  const buffer = ctx.createBuffer(2, Math.floor(ctx.sampleRate * seconds), ctx.sampleRate);
  let seed = 0x51ed27;
  for (let channel = 0; channel < 2; channel++) {
    const data = buffer.getChannelData(channel);
    for (let i = 0; i < data.length; i++) {
      seed = (Math.imul(seed, 1664525) + 1013904223) | 0;
      const t = i / data.length;
      data[i] = (seed / 0x80000000) * (1 - t) ** 3.2;
    }
  }
  return buffer;
}

export function createGraph(ctx: BaseAudioContext, meters: boolean): Graph {
  const masterIn = ctx.createGain();
  const limiter = ctx.createDynamicsCompressor();
  limiter.threshold.value = -6;
  limiter.knee.value = 4;
  limiter.ratio.value = 12;
  limiter.attack.value = 0.003;
  limiter.release.value = 0.12;
  // Two parallel paths with one open at a time: a compressor set to "do
  // nothing" still delays the signal, so off has to mean out of the path.
  const bypass = ctx.createGain();
  const limited = ctx.createGain();
  const masterOut = ctx.createGain();
  masterIn.connect(limiter).connect(limited).connect(masterOut);
  masterIn.connect(bypass).connect(masterOut);
  masterOut.connect(ctx.destination);

  let masterMeter: AnalyserNode | null = null;
  if (meters) {
    masterMeter = ctx.createAnalyser();
    masterMeter.fftSize = 1024;
    masterOut.connect(masterMeter);
  }

  const reverbIn = ctx.createGain();
  const convolver = ctx.createConvolver();
  convolver.buffer = impulse(ctx);
  reverbIn.connect(convolver).connect(masterIn);

  // A dotted-eighth echo that darkens as it repeats.
  const delayIn = ctx.createGain();
  const delayLine = ctx.createDelay(2);
  const feedback = ctx.createGain();
  feedback.gain.value = 0.36;
  const damp = ctx.createBiquadFilter();
  damp.type = "lowpass";
  damp.frequency.value = 3200;
  delayIn.connect(delayLine).connect(damp).connect(masterIn);
  damp.connect(feedback).connect(delayLine);

  const click = ctx.createGain();
  click.connect(ctx.destination);

  return {
    ctx, strips: new Map(), masterIn, limiter, bypass, limited, masterOut, masterMeter,
    reverbIn, delayIn, delayLine, click,
  };
}

function createStrip(graph: Graph, meters: boolean): Strip {
  const { ctx } = graph;
  const input = ctx.createGain();
  const low = ctx.createBiquadFilter();
  low.type = "lowshelf";
  low.frequency.value = 180;
  const mid = ctx.createBiquadFilter();
  mid.type = "peaking";
  mid.frequency.value = 1200;
  mid.Q.value = 0.8;
  const high = ctx.createBiquadFilter();
  high.type = "highshelf";
  high.frequency.value = 6000;
  const fader = ctx.createGain();
  const pan = ctx.createStereoPanner();
  const reverb = ctx.createGain();
  const delay = ctx.createGain();
  input.connect(low).connect(mid).connect(high).connect(fader).connect(pan);
  pan.connect(graph.masterIn);
  pan.connect(reverb).connect(graph.reverbIn);
  pan.connect(delay).connect(graph.delayIn);

  let meter: AnalyserNode | null = null;
  if (meters) {
    meter = ctx.createAnalyser();
    meter.fftSize = 512;
    pan.connect(meter);
  }
  return { input, low, mid, high, fader, pan, reverb, delay, meter };
}

function set(param: AudioParam, value: number, ctx: BaseAudioContext, glide: boolean): void {
  // A fader moved during playback glides; a jump would be heard as a click.
  if (glide) param.setTargetAtTime(value, ctx.currentTime, 0.015);
  else param.value = value;
}

function applyStrip(strip: Strip, track: Track, project: Project, ctx: BaseAudioContext, glide: boolean): void {
  set(strip.low.gain, track.eq.low, ctx, glide);
  set(strip.mid.gain, track.eq.mid, ctx, glide);
  set(strip.high.gain, track.eq.high, ctx, glide);
  set(strip.fader.gain, audible(track, project) ? dbToGain(track.volume_db) : 0, ctx, glide);
  set(strip.pan.pan, track.pan, ctx, glide);
  set(strip.reverb.gain, track.reverb, ctx, glide);
  set(strip.delay.gain, track.delay, ctx, glide);
}

/**
 * Make the graph match the project: add strips for new tracks, drop strips for
 * deleted ones, and set every level. Safe to call on every change.
 */
export function syncGraph(graph: Graph, project: Project, glide: boolean): void {
  const { ctx } = graph;
  const wanted = new Set(project.tracks.map((t) => t.id));
  for (const [id, strip] of graph.strips) {
    if (wanted.has(id)) continue;
    strip.pan.disconnect();
    strip.input.disconnect();
    graph.strips.delete(id);
  }
  for (const track of project.tracks) {
    let strip = graph.strips.get(track.id);
    if (!strip) {
      strip = createStrip(graph, graph.masterMeter !== null);
      graph.strips.set(track.id, strip);
      applyStrip(strip, track, project, ctx, false);
    } else {
      applyStrip(strip, track, project, ctx, glide);
    }
  }
  set(graph.masterOut.gain, dbToGain(project.master.volume_db), ctx, glide);
  set(graph.limited.gain, project.master.limiter ? 1 : 0, ctx, glide);
  set(graph.bypass.gain, project.master.limiter ? 0 : 1, ctx, glide);
  set(graph.delayLine.delayTime, Math.min(1.9, (0.75 * 60) / project.bpm), ctx, glide);
  graph.click.gain.value = project.metronome ? 1 : 0;
}

/** Peak level of whatever an analyser is hearing, in dBFS. */
export function peakDb(meter: AnalyserNode | null, scratch: Float32Array<ArrayBuffer>): number {
  if (!meter) return -Infinity;
  meter.getFloatTimeDomainData(scratch);
  let peak = 0;
  const n = Math.min(scratch.length, meter.fftSize);
  for (let i = 0; i < n; i++) peak = Math.max(peak, Math.abs(scratch[i]));
  return peak > 0 ? 20 * Math.log10(peak) : -Infinity;
}
