"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { SongComponent } from "@/lib/song";

export type SongAudio = {
  /** Key of the component loaded into the transport, if any. */
  current: string | null;
  playing: boolean;
  time: number;
  duration: number;
  error: string | null;
  toggle: (component: SongComponent) => void;
  playAt: (component: SongComponent, seconds: number) => void;
  seek: (seconds: number) => void;
  pause: () => void;
};

/**
 * One audio element for the whole song screen, so only one thing ever plays.
 *
 * This streams, unlike the blind test's player, which decodes every version
 * up front for gapless switching. Here the job is listening to one component
 * at a time, and a three-minute master should start at once on a phone rather
 * than after a 60 MB decode.
 */
export function useSongAudio(): SongAudio {
  const element = useRef<HTMLAudioElement | null>(null);
  const [current, setCurrent] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const loaded = useRef<string | null>(null);

  useEffect(() => {
    const audio = new Audio();
    audio.preload = "metadata";
    element.current = audio;
    const onTime = () => setTime(audio.currentTime);
    const onMeta = () => setDuration(Number.isFinite(audio.duration) ? audio.duration : 0);
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    const onError = () => {
      setPlaying(false);
      setError("That audio would not load. It may still be on its way from the studio Mac.");
    };
    audio.addEventListener("timeupdate", onTime);
    audio.addEventListener("loadedmetadata", onMeta);
    audio.addEventListener("durationchange", onMeta);
    audio.addEventListener("play", onPlay);
    audio.addEventListener("pause", onPause);
    audio.addEventListener("ended", onPause);
    audio.addEventListener("error", onError);
    return () => {
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
      element.current = null;
    };
  }, []);

  const load = useCallback((component: SongComponent): HTMLAudioElement | null => {
    const audio = element.current;
    if (!audio || !component.audio) return null;
    const src = `/api/audio/${component.audio}`;
    if (loaded.current !== src) {
      loaded.current = src;
      audio.src = src;
      setTime(0);
      setDuration(component.measure?.duration_s ?? 0);
    }
    setError(null);
    setCurrent(component.key);
    return audio;
  }, []);

  const start = (audio: HTMLAudioElement) => {
    audio.play().catch(() => setPlaying(false));
  };

  const toggle = useCallback(
    (component: SongComponent) => {
      const audio = element.current;
      if (!audio) return;
      const same = loaded.current === `/api/audio/${component.audio}`;
      if (same && !audio.paused) {
        audio.pause();
        return;
      }
      const ready = load(component);
      if (ready) start(ready);
    },
    [load],
  );

  const playAt = useCallback(
    (component: SongComponent, seconds: number) => {
      const audio = load(component);
      if (!audio) return;
      const jump = () => {
        audio.currentTime = seconds;
        setTime(seconds);
        start(audio);
      };
      // Seeking before the metadata arrives is silently ignored by Safari.
      if (audio.readyState >= 1) jump();
      else audio.addEventListener("loadedmetadata", jump, { once: true });
    },
    [load],
  );

  const seek = useCallback((seconds: number) => {
    const audio = element.current;
    if (!audio) return;
    audio.currentTime = seconds;
    setTime(seconds);
  }, []);

  const pause = useCallback(() => element.current?.pause(), []);

  return { current, playing, time, duration, error, toggle, playAt, seek, pause };
}
