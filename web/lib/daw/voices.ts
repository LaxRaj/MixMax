import type { AudioClip, DrumVoice, KitName, SynthParams } from "./types";

/**
 * The sounds: a synthesized drum kit, a subtractive synth, and audio clips.
 *
 * Everything is scheduled against a `BaseAudioContext` at an exact time, so
 * the same calls drive live playback and the offline bounce. Nothing is
 * sampled — the kit is built from oscillators and noise — so a project needs
 * no downloads to make a sound and renders the same on every machine.
 */

/** Told about every source that starts, so playback can stop them on demand. */
export type Sink = (source: AudioScheduledSourceNode) => void;

const SILENT = 0.0001;

// Noise is generated from a fixed seed: an export made twice is the same file.
const noiseCache = new WeakMap<BaseAudioContext, AudioBuffer>();
function noise(ctx: BaseAudioContext): AudioBuffer {
  let buffer = noiseCache.get(ctx);
  if (!buffer) {
    buffer = ctx.createBuffer(1, Math.floor(ctx.sampleRate * 2), ctx.sampleRate);
    const data = buffer.getChannelData(0);
    let seed = 0x2f6e2b1;
    for (let i = 0; i < data.length; i++) {
      seed = (Math.imul(seed, 1664525) + 1013904223) | 0;
      data[i] = seed / 0x80000000;
    }
    noiseCache.set(ctx, buffer);
  }
  return buffer;
}

/** A percussive envelope: up fast, down exponentially. */
function hit(param: AudioParam, time: number, peak: number, decay: number, attack = 0.001): void {
  param.setValueAtTime(SILENT, time);
  param.exponentialRampToValueAtTime(Math.max(peak, SILENT), time + attack);
  param.exponentialRampToValueAtTime(SILENT, time + attack + decay);
}

type Kit = {
  kick: { from: number; to: number; sweep: number; decay: number; drive: number };
  snare: { tone: number; body: number; decay: number; bright: number };
  hat: { cut: number; decay: number; open: number };
  clap: { centre: number; decay: number };
  tom: { from: number; to: number; decay: number };
  level: number;
};

const KIT: Record<KitName, Kit> = {
  "808": {
    kick: { from: 130, to: 44, sweep: 0.11, decay: 0.75, drive: 0.25 },
    snare: { tone: 185, body: 0.12, decay: 0.2, bright: 1800 },
    hat: { cut: 8200, decay: 0.045, open: 0.34 },
    clap: { centre: 1200, decay: 0.2 },
    tom: { from: 190, to: 95, decay: 0.3 },
    level: 1,
  },
  punch: {
    kick: { from: 210, to: 52, sweep: 0.05, decay: 0.26, drive: 0.5 },
    snare: { tone: 215, body: 0.08, decay: 0.15, bright: 2600 },
    hat: { cut: 9500, decay: 0.03, open: 0.22 },
    clap: { centre: 1500, decay: 0.14 },
    tom: { from: 240, to: 120, decay: 0.2 },
    level: 1,
  },
  lofi: {
    kick: { from: 150, to: 50, sweep: 0.08, decay: 0.34, drive: 0.7 },
    snare: { tone: 170, body: 0.1, decay: 0.17, bright: 1100 },
    hat: { cut: 5200, decay: 0.05, open: 0.26 },
    clap: { centre: 950, decay: 0.16 },
    tom: { from: 170, to: 85, decay: 0.26 },
    level: 0.9,
  },
};

const curves = new Map<number, Float32Array<ArrayBuffer>>();
function driveCurve(amount: number): Float32Array<ArrayBuffer> {
  const key = Math.round(amount * 20);
  let curve = curves.get(key);
  if (!curve) {
    const k = 1 + (key / 20) * 9;
    curve = new Float32Array(1024);
    for (let i = 0; i < 1024; i++) {
      const x = (i / 1023) * 2 - 1;
      curve[i] = Math.tanh(k * x) / Math.tanh(k);
    }
    curves.set(key, curve);
  }
  return curve;
}

function noiseBurst(
  ctx: BaseAudioContext,
  dest: AudioNode,
  time: number,
  length: number,
  filter: { type: BiquadFilterType; frequency: number; q?: number },
  shape: (gain: AudioParam) => void,
  sink?: Sink,
): void {
  const source = ctx.createBufferSource();
  source.buffer = noise(ctx);
  // Start somewhere different per hit time, so repeated hits are not phase-identical.
  const offset = (time * 0.37) % 1;
  const biquad = ctx.createBiquadFilter();
  biquad.type = filter.type;
  biquad.frequency.value = filter.frequency;
  if (filter.q) biquad.Q.value = filter.q;
  const gain = ctx.createGain();
  shape(gain.gain);
  source.connect(biquad).connect(gain).connect(dest);
  source.start(time, offset, length);
  sink?.(source);
}

function tone(
  ctx: BaseAudioContext,
  dest: AudioNode,
  time: number,
  type: OscillatorType,
  from: number,
  to: number,
  sweep: number,
  peak: number,
  decay: number,
  sink?: Sink,
): void {
  const osc = ctx.createOscillator();
  osc.type = type;
  osc.frequency.setValueAtTime(from, time);
  if (to !== from) osc.frequency.exponentialRampToValueAtTime(to, time + sweep);
  const gain = ctx.createGain();
  hit(gain.gain, time, peak, decay);
  osc.connect(gain).connect(dest);
  osc.start(time);
  osc.stop(time + decay + 0.05);
  sink?.(osc);
}

export function playDrum(
  ctx: BaseAudioContext,
  dest: AudioNode,
  kitName: KitName,
  voice: DrumVoice,
  time: number,
  velocity: number,
  sink?: Sink,
): void {
  const kit = KIT[kitName] ?? KIT["808"];
  // Velocity is perceived roughly as its square.
  const v = velocity * velocity * kit.level;

  switch (voice) {
    case "kick": {
      const k = kit.kick;
      const shaper = ctx.createWaveShaper();
      shaper.curve = driveCurve(k.drive);
      shaper.connect(dest);
      tone(ctx, shaper, time, "sine", k.from, k.to, k.sweep, 0.95 * v, k.decay, sink);
      // The click that lets a kick be heard on small speakers.
      noiseBurst(ctx, dest, time, 0.02, { type: "lowpass", frequency: 3000 }, (g) => hit(g, time, 0.25 * v, 0.012), sink);
      break;
    }
    case "snare": {
      const s = kit.snare;
      tone(ctx, dest, time, "triangle", s.tone * 1.5, s.tone, 0.03, 0.5 * v, s.body, sink);
      noiseBurst(ctx, dest, time, s.decay + 0.05, { type: "highpass", frequency: s.bright }, (g) => hit(g, time, 0.6 * v, s.decay), sink);
      break;
    }
    case "clap": {
      const c = kit.clap;
      // Three quick slaps, then the tail: several hands, never quite together.
      noiseBurst(ctx, dest, time, c.decay + 0.08, { type: "bandpass", frequency: c.centre, q: 1.4 }, (g) => {
        g.setValueAtTime(SILENT, time);
        for (let i = 0; i < 3; i++) {
          const at = time + i * 0.011;
          g.exponentialRampToValueAtTime(0.7 * v, at + 0.001);
          g.exponentialRampToValueAtTime(0.12 * v, at + 0.01);
        }
        g.exponentialRampToValueAtTime(0.7 * v, time + 0.034);
        g.exponentialRampToValueAtTime(SILENT, time + 0.034 + c.decay);
      }, sink);
      break;
    }
    case "hat":
      noiseBurst(ctx, dest, time, kit.hat.decay + 0.03, { type: "highpass", frequency: kit.hat.cut }, (g) => hit(g, time, 0.42 * v, kit.hat.decay), sink);
      break;
    case "openhat":
      noiseBurst(ctx, dest, time, kit.hat.open + 0.05, { type: "highpass", frequency: kit.hat.cut * 0.85 }, (g) => hit(g, time, 0.38 * v, kit.hat.open), sink);
      break;
    case "tom":
      tone(ctx, dest, time, "sine", kit.tom.from, kit.tom.to, 0.12, 0.7 * v, kit.tom.decay, sink);
      break;
    case "rim": {
      tone(ctx, dest, time, "triangle", 1700, 1700, 0, 0.3 * v, 0.03, sink);
      tone(ctx, dest, time, "square", 420, 420, 0, 0.16 * v, 0.025, sink);
      break;
    }
    case "perc": {
      // The classic two-square cowbell.
      const band = ctx.createBiquadFilter();
      band.type = "bandpass";
      band.frequency.value = 2600;
      band.Q.value = 1;
      band.connect(dest);
      tone(ctx, band, time, "square", 540, 540, 0, 0.5 * v, 0.28, sink);
      tone(ctx, band, time, "square", 800, 800, 0, 0.5 * v, 0.28, sink);
      break;
    }
  }
}

export const midiToHz = (pitch: number): number => 440 * 2 ** ((pitch - 69) / 12);

/**
 * One synth note: oscillators into a lowpass filter into an ADSR amp.
 *
 * The release is scheduled from the level the envelope will have reached by
 * then, worked out here rather than read back from the node — reading back is
 * not possible when the note is still in the future, and in an offline render
 * every note is.
 */
export function playNote(
  ctx: BaseAudioContext,
  dest: AudioNode,
  synth: SynthParams,
  pitch: number,
  time: number,
  duration: number,
  velocity: number,
  sink?: Sink,
): void {
  const hz = midiToHz(pitch + synth.octave * 12);
  const peak = 0.34 * velocity * velocity;
  const attack = Math.max(0.001, synth.attack);
  const tau = Math.max(0.005, synth.decay / 3);
  const held = Math.max(duration, 0.02);
  const releaseAt = time + held;
  const sustain = peak * synth.sustain;
  const levelAtRelease =
    held <= attack ? (peak * held) / attack : sustain + (peak - sustain) * Math.exp(-(held - attack) / tau);
  const end = releaseAt + synth.release;

  const amp = ctx.createGain();
  amp.gain.setValueAtTime(0, time);
  if (held <= attack) {
    amp.gain.linearRampToValueAtTime(levelAtRelease, releaseAt);
  } else {
    amp.gain.linearRampToValueAtTime(peak, time + attack);
    amp.gain.setTargetAtTime(sustain, time + attack, tau);
    amp.gain.setValueAtTime(Math.max(levelAtRelease, SILENT), releaseAt);
  }
  amp.gain.exponentialRampToValueAtTime(SILENT, end);

  const filter = ctx.createBiquadFilter();
  filter.type = "lowpass";
  filter.Q.value = synth.resonance;
  const open = Math.min(18000, synth.cutoff * 2 ** synth.env);
  filter.frequency.setValueAtTime(open, time);
  filter.frequency.setTargetAtTime(synth.cutoff, time, tau + 0.01);

  let out: AudioNode = filter;
  if (synth.drive > 0) {
    const shaper = ctx.createWaveShaper();
    shaper.curve = driveCurve(synth.drive);
    filter.connect(shaper);
    out = shaper;
  }
  out.connect(amp).connect(dest);

  const voices = Math.max(1, Math.min(3, Math.round(synth.voices)));
  const sources: OscillatorNode[] = [];
  for (let i = 0; i < voices; i++) {
    const osc = ctx.createOscillator();
    osc.type = synth.wave;
    // Spread the stack evenly around the centre pitch.
    osc.detune.value = voices === 1 ? 0 : (i / (voices - 1) - 0.5) * 2 * synth.detune;
    const level = ctx.createGain();
    level.gain.value = 1 / Math.sqrt(voices);
    osc.connect(level).connect(filter);
    sources.push(osc);
  }
  if (synth.sub > 0) {
    const sub = ctx.createOscillator();
    sub.type = "sine";
    const level = ctx.createGain();
    level.gain.value = synth.sub;
    sub.connect(level).connect(filter);
    sub.frequency.value = hz / 2;
    sources.push(sub);
  }
  sources.forEach((osc, i) => {
    const base = synth.sub > 0 && i === sources.length - 1 ? hz / 2 : hz;
    if (synth.drop > 0) {
      osc.frequency.setValueAtTime(base * 2 ** (synth.drop / 12), time);
      osc.frequency.exponentialRampToValueAtTime(base, time + 0.06);
    } else {
      osc.frequency.setValueAtTime(base, time);
    }
    osc.start(time);
    osc.stop(end + 0.02);
    sink?.(osc);
  });
}

/** A short tick for the metronome; higher on the first beat of the bar. */
export function playClick(ctx: BaseAudioContext, dest: AudioNode, time: number, downbeat: boolean, sink?: Sink): void {
  tone(ctx, dest, time, "square", downbeat ? 1760 : 1175, downbeat ? 1760 : 1175, 0, 0.25, 0.03, sink);
}

export const dbToGain = (db: number): number => (db <= -60 ? 0 : 10 ** (db / 20));

/**
 * Start an audio clip, `skip` seconds in. Returns the source and the gain that
 * shapes it, so a caller can cut it off cleanly.
 */
export function playClip(
  ctx: BaseAudioContext,
  dest: AudioNode,
  clip: AudioClip,
  buffer: AudioBuffer,
  time: number,
  skip: number,
): { source: AudioBufferSourceNode; gain: GainNode; ends: number } | null {
  const offset = clip.offset_s + skip;
  const length = Math.min(clip.duration_s - skip, buffer.duration - offset);
  if (length <= 0.001) return null;

  const level = dbToGain(clip.gain_db);
  const gain = ctx.createGain();
  // Fades longer than the clip are scaled to meet in the middle rather than overlap.
  const squeeze = Math.min(1, clip.duration_s / Math.max(clip.fade_in_s + clip.fade_out_s, 0.001));
  const fadeIn = clip.fade_in_s * squeeze;
  const fadeOut = clip.fade_out_s * squeeze;
  // A few milliseconds at each edge, so a cut in the middle of a waveform does not click.
  const edge = 0.004;

  if (skip < fadeIn) {
    gain.gain.setValueAtTime(level * (skip / fadeIn), time);
    gain.gain.linearRampToValueAtTime(level, time + fadeIn - skip);
  } else {
    gain.gain.setValueAtTime(0, time);
    gain.gain.linearRampToValueAtTime(level, time + edge);
  }
  const ends = time + length;
  const fadeStart = Math.max(time + edge, ends - Math.max(fadeOut, edge));
  gain.gain.setValueAtTime(level, fadeStart);
  gain.gain.linearRampToValueAtTime(0, ends);

  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.connect(gain).connect(dest);
  source.start(time, offset, length);
  return { source, gain, ends };
}
