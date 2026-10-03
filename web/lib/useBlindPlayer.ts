"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Manifest } from "./manifest";

/**
 * Switching crossfade. Long enough to avoid a click from the discontinuity,
 * short enough to read as instant.
 */
const SWITCH_RAMP_S = 0.015;

/** Scheduling head start, so every source starts on the same sample. */
const START_LOOKAHEAD_S = 0.06;

export type PlayerStatus = "idle" | "loading" | "ready" | "error";

export type BlindPlayer = {
  status: PlayerStatus;
  error: string | null;
  loadedCount: number;
  totalCount: number;
  isPlaying: boolean;
  active: string;
  position: number;
  duration: number;
  switchCount: number;
  load: () => Promise<void>;
  toggle: () => Promise<void>;
  select: (label: string) => void;
  seek: (seconds: number) => void;
};

/**
 * Plays every version at once, muted except one.
 *
 * This is the whole reason the web test beats emailing a folder: switching
 * must be instant and sample-aligned, so a listener compares the *same moment*
 * rather than their memory of it. Stopping and restarting a source would cost
 * a gap and lose position, so sources run continuously and only gain changes.
 */
export function useBlindPlayer(manifest: Manifest): BlindPlayer {
  const [status, setStatus] = useState<PlayerStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [loadedCount, setLoadedCount] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [active, setActive] = useState(manifest.labels[0]);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [switchCount, setSwitchCount] = useState(0);

  const ctxRef = useRef<AudioContext | null>(null);
  const buffersRef = useRef<Map<string, AudioBuffer>>(new Map());
  const gainsRef = useRef<Map<string, GainNode>>(new Map());
  const sourcesRef = useRef<Map<string, AudioBufferSourceNode>>(new Map());
  const activeRef = useRef(active);
  const isPlayingRef = useRef(false);
  const startedAtRef = useRef(0);
  const startOffsetRef = useRef(0);
  const rafRef = useRef<number | null>(null);

  const loopStart = manifest.excerpt?.start_s ?? 0;
  const loopLengthRef = useRef(0);

  useEffect(() => {
    activeRef.current = active;
  }, [active]);

  useEffect(() => {
    isPlayingRef.current = isPlaying;
  }, [isPlaying]);

  const ensureContext = useCallback(() => {
    if (!ctxRef.current) {
      // iOS silences Web Audio when the hardware ring/silent switch is on,
      // unless the page declares a playback session. Safari 16.4+.
      const nav = navigator as Navigator & { audioSession?: { type: string } };
      if (nav.audioSession) nav.audioSession.type = "playback";

      const Ctor =
        window.AudioContext ??
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      ctxRef.current = new Ctor();
    }
    return ctxRef.current;
  }, []);

  const load = useCallback(async () => {
    if (status === "loading" || status === "ready") return;
    setStatus("loading");
    setError(null);
    setLoadedCount(0);

    try {
      const ctx = ensureContext();
      // iOS starts the context suspended until a gesture resumes it.
      if (ctx.state === "suspended") await ctx.resume();

      let loaded = 0;
      await Promise.all(
        manifest.labels.map(async (label) => {
          const response = await fetch(manifest.urls[label]);
          if (!response.ok) throw new Error(`Could not load ${label} (${response.status})`);
          const buffer = await ctx.decodeAudioData(await response.arrayBuffer());
          buffersRef.current.set(label, buffer);
          loaded += 1;
          setLoadedCount(loaded);
        }),
      );

      // Every version must share a loop window or the comparison drifts apart.
      const shortest = Math.min(
        ...manifest.labels.map((l) => buffersRef.current.get(l)!.duration),
      );
      const requested = manifest.excerpt?.length_s ?? shortest - loopStart;
      loopLengthRef.current = Math.max(0.1, Math.min(requested, shortest - loopStart));
      setDuration(loopLengthRef.current);

      for (const label of manifest.labels) {
        const gain = ctx.createGain();
        gain.gain.value = label === activeRef.current ? 1 : 0;
        gain.connect(ctx.destination);
        gainsRef.current.set(label, gain);
      }

      setStatus("ready");

      if (process.env.NODE_ENV !== "production") {
        (window as unknown as { __player?: unknown }).__player = {
          ctx,
          gains: gainsRef.current,
          sources: sourcesRef.current,
          startedAt: () => startedAtRef.current,
        };
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the audio");
      setStatus("error");
    }
  }, [ensureContext, loopStart, manifest, status]);

  const stopSources = useCallback(() => {
    for (const source of sourcesRef.current.values()) {
      try {
        source.stop();
      } catch {
        /* already stopped */
      }
      source.disconnect();
    }
    sourcesRef.current.clear();
  }, []);

  const startSources = useCallback(
    (offset: number) => {
      const ctx = ensureContext();
      const at = ctx.currentTime + START_LOOKAHEAD_S;

      for (const label of manifest.labels) {
        const buffer = buffersRef.current.get(label);
        const gain = gainsRef.current.get(label);
        if (!buffer || !gain) continue;

        const source = ctx.createBufferSource();
        source.buffer = buffer;
        source.loop = true;
        source.loopStart = loopStart;
        source.loopEnd = loopStart + loopLengthRef.current;
        source.connect(gain);
        // Identical `at` for every source is what keeps them sample-aligned.
        source.start(at, loopStart + offset);
        sourcesRef.current.set(label, source);
      }

      startedAtRef.current = at;
      startOffsetRef.current = offset;
    },
    [ensureContext, loopStart, manifest.labels],
  );

  const tick = useCallback(() => {
    const ctx = ctxRef.current;
    const length = loopLengthRef.current;
    // Only track while playing: a suspended context's currentTime is unrelated
    // to playback position, and reading it idle shows load time as position.
    if (ctx && length > 0 && isPlayingRef.current) {
      const elapsed = ctx.currentTime - startedAtRef.current + startOffsetRef.current;
      setPosition(elapsed > 0 ? elapsed % length : 0);
    }
    rafRef.current = requestAnimationFrame(tick);
  }, []);

  const toggle = useCallback(async () => {
    if (status !== "ready") {
      await load();
      return;
    }
    const ctx = ensureContext();
    if (ctx.state === "suspended") await ctx.resume();

    if (isPlaying) {
      const elapsed = ctx.currentTime - startedAtRef.current + startOffsetRef.current;
      startOffsetRef.current = loopLengthRef.current > 0 ? elapsed % loopLengthRef.current : 0;
      setPosition(startOffsetRef.current);
      stopSources();
      setIsPlaying(false);
    } else {
      startSources(startOffsetRef.current);
      setIsPlaying(true);
    }
  }, [ensureContext, isPlaying, load, startSources, status, stopSources]);

  const select = useCallback(
    (label: string) => {
      if (label === activeRef.current) return;
      const ctx = ctxRef.current;

      if (ctx) {
        const now = ctx.currentTime;
        for (const [key, gain] of gainsRef.current) {
          const target = key === label ? 1 : 0;
          gain.gain.cancelScheduledValues(now);
          gain.gain.setValueAtTime(gain.gain.value, now);
          gain.gain.linearRampToValueAtTime(target, now + SWITCH_RAMP_S);
        }
      }

      activeRef.current = label;
      setActive(label);
      setSwitchCount((n) => n + 1);
    },
    [],
  );

  const seek = useCallback(
    (seconds: number) => {
      const clamped = Math.max(0, Math.min(seconds, loopLengthRef.current));
      startOffsetRef.current = clamped;
      setPosition(clamped);
      if (isPlaying) {
        stopSources();
        startSources(clamped);
      }
    },
    [isPlaying, startSources, stopSources],
  );

  useEffect(() => {
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [tick]);

  useEffect(() => {
    return () => {
      stopSources();
      ctxRef.current?.close();
    };
  }, [stopSources]);

  return {
    status,
    error,
    loadedCount,
    totalCount: manifest.labels.length,
    isPlaying,
    active,
    position,
    duration,
    switchCount,
    load,
    toggle,
    select,
    seek,
  };
}
