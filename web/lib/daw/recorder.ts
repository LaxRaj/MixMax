/**
 * Record the microphone as raw samples.
 *
 * `MediaRecorder` would be less code, but it only produces Opus or AAC, and a
 * vocal take is exactly the thing that should not be lossy before it has even
 * been mixed. An audio worklet hands over the samples as they arrive instead.
 */

const WORKLET = `
class Tap extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (input && input[0] && input[0].length) this.port.postMessage(input[0].slice(0));
    return true;
  }
}
registerProcessor("mixmax-tap", Tap);
`;

const loaded = new WeakSet<AudioContext>();

export type Recording = {
  /** Start keeping samples. `first` is told the context time of the first ones kept. */
  begin: (first: (time: number) => void) => void;
  /** How late the take is relative to what was heard, in seconds. */
  latency_s: number;
  stop: () => AudioBuffer | null;
};

export async function startRecording(ctx: AudioContext): Promise<Recording> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("This browser cannot record. It needs a secure (https or localhost) page.");
  }
  let stream: MediaStream;
  try {
    // The browser's call-quality processing gates and pumps a sung vocal; turn it all off.
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
    });
  } catch {
    throw new Error("The microphone was not allowed. Allow it for this site and try again.");
  }

  if (!loaded.has(ctx)) {
    const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
    try {
      await ctx.audioWorklet.addModule(url);
    } finally {
      URL.revokeObjectURL(url);
    }
    loaded.add(ctx);
  }
  if (ctx.state !== "running") await ctx.resume();

  const source = ctx.createMediaStreamSource(stream);
  const tap = new AudioWorkletNode(ctx, "mixmax-tap", { numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1 });
  // A node only runs while it leads somewhere; lead it to the output at zero volume.
  const mute = ctx.createGain();
  mute.gain.value = 0;
  source.connect(tap).connect(mute).connect(ctx.destination);

  // Samples flow as soon as the microphone opens, but the take only starts
  // when `begin` is called — after playback has, so the two share a clock.
  const chunks: Float32Array[] = [];
  let onFirst: ((time: number) => void) | null = null;
  let armed = false;
  tap.port.onmessage = (event: MessageEvent<Float32Array>) => {
    if (!armed) return;
    if (chunks.length === 0) onFirst?.(ctx.currentTime);
    chunks.push(event.data);
  };

  const settings = stream.getAudioTracks()[0]?.getSettings() as MediaTrackSettings & { latency?: number };
  // What was sung is late twice over: the backing reached the ears late, and
  // the voice reached the page late.
  const latency_s = (ctx.outputLatency || ctx.baseLatency || 0) + (settings?.latency ?? ctx.baseLatency ?? 0);

  return {
    begin: (first) => {
      onFirst = first;
      armed = true;
    },
    latency_s,
    stop: () => {
      tap.port.onmessage = null;
      source.disconnect();
      tap.disconnect();
      mute.disconnect();
      stream.getTracks().forEach((track) => track.stop());
      const length = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
      if (length === 0) return null;
      const buffer = ctx.createBuffer(1, length, ctx.sampleRate);
      const data = buffer.getChannelData(0);
      let offset = 0;
      for (const chunk of chunks) {
        data.set(chunk, offset);
        offset += chunk.length;
      }
      return buffer;
    },
  };
}
