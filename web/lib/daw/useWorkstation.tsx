"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { addAudio, historyReducer, initialHistory, type Edit, type History, type HistoryAction } from "./edit";
import { createGraph, peakDb, syncGraph, type Graph } from "./graph";
import { startRecording, type Recording } from "./recorder";
import { encodeWav, peaksOf, renderProject, type Bounce, type BounceOptions } from "./render";
import { secondsToBeats } from "./schedule";
import { Transport } from "./transport";
import {
  MEDIA_EXTENSIONS,
  normalizeProject,
  type AudioClip,
  type DrumVoice,
  type Media,
  type Project,
} from "./types";
import { playDrum, playNote } from "./voices";
import { newId } from "@/lib/song";

/**
 * Everything one open project needs: the project and its undo history, the
 * audio engine playing it, the decoded audio it refers to, and the saver.
 */

export type SaveState = "saved" | "dirty" | "saving" | "error" | "conflict";
export type MediaState = "loading" | "ready" | "error";
export type Selection = { track: string | null; clip: string | null };
export type View = { pxPerBeat: number; grid: number };

class ProjectStore {
  state: History | null = null;
  private listeners = new Set<() => void>();
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  get = () => this.state;
  dispatch(action: HistoryAction): void {
    if (!this.state) {
      if (action.type !== "load") return;
      this.state = initialHistory(action.project);
    } else {
      const next = historyReducer(this.state, action);
      if (next === this.state) return;
      this.state = next;
    }
    this.listeners.forEach((listener) => listener());
  }
}

type Engine = { ctx: AudioContext; graph: Graph; transport: Transport };

export type Workstation = {
  project: Project;
  canUndo: boolean;
  canRedo: boolean;
  /** Apply a change. `key` merges a run of the same gesture into one undo step. */
  apply: (edit: Edit, key?: string) => Project;
  undo: () => void;
  redo: () => void;

  save: SaveState;
  saveMessage: string | null;
  saveNow: () => void;
  resolveConflict: (keep: "mine" | "theirs") => void;

  playing: boolean;
  recording: boolean;
  toggle: () => void;
  stop: () => void;
  seek: (beat: number) => void;
  position: () => number;
  record: () => Promise<void>;

  selection: Selection;
  select: (track: string | null, clip?: string | null) => void;
  /** The pattern being edited on a drum or synth track. */
  patternOf: (trackId: string) => string | null;
  editPattern: (trackId: string, patternId: string) => void;
  view: View;
  setView: (view: Partial<View>) => void;

  mediaState: (id: string) => MediaState;
  peaks: (id: string) => Float32Array | null;
  importFile: (file: File, trackId?: string) => Promise<void>;
  addSongAudio: (name: string, path: string, song: string) => Promise<void>;
  bounce: (options?: BounceOptions) => Promise<Bounce>;

  previewDrum: (trackId: string, voice: DrumVoice, velocity?: number) => void;
  previewNote: (trackId: string, pitch: number) => void;
  level: (trackId: string | null) => number;

  busy: string | null;
  error: string | null;
  dismissError: () => void;
  mode: "blob" | "local";
};

const Context = createContext<Workstation | null>(null);

export function useWs(): Workstation {
  const value = useContext(Context);
  if (!value) throw new Error("useWs outside a workstation");
  return value;
}

export function WorkstationProvider({ value, children }: { value: Workstation; children: React.ReactNode }) {
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

async function fetchAudio(path: string): Promise<ArrayBuffer> {
  // Hosted, this redirects to the blob host; if that refuses a scripted read, go through the app.
  for (const url of [`/api/audio/${path}`, `/api/audio/${path}?inline=1`]) {
    try {
      const response = await fetch(url);
      if (response.ok) return await response.arrayBuffer();
      if (response.status === 404) break;
    } catch {
      // Try the next way in.
    }
  }
  throw new Error("not found");
}

async function uploadMedia(projectId: string, mediaId: string, ext: string, body: Blob, mode: "blob" | "local"): Promise<string> {
  const path = `projects/${projectId}/media/${mediaId}.${ext}`;
  if (mode === "blob") {
    const { upload } = await import("@vercel/blob/client");
    await upload(path, body, {
      access: "private",
      handleUploadUrl: `/api/projects/${projectId}/media`,
      multipart: body.size > 8 * 1024 * 1024,
    });
    return path;
  }
  const response = await fetch(`/api/projects/${projectId}/media?media=${mediaId}&ext=${ext}`, { method: "PUT", body });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error ?? `Could not store the audio (${response.status}).`);
  }
  return path;
}

const SAVE_AFTER_MS = 900;

export function useWorkstation(id: string, author: string): { ws: Workstation | null; loadError: string | null } {
  const [store] = useState(() => new ProjectStore());
  const history = useSyncExternalStore(store.subscribe, store.get, store.get);
  const [loadError, setLoadError] = useState<string | null>(null);

  const engine = useRef<Engine | null>(null);
  const buffers = useRef(new Map<string, AudioBuffer>());
  const peakCache = useRef(new Map<string, Float32Array>());
  const mediaStates = useRef(new Map<string, MediaState>());
  // Decoded audio lives in refs; this is what tells the screen some of it arrived.
  const [mediaVersion, bump] = useState(0);
  const redraw = useCallback(() => bump((n) => n + 1), []);

  const [playing, setPlaying] = useState(false);
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"blob" | "local">("local");
  const [selection, setSelection] = useState<Selection>({ track: null, clip: null });
  const [patterns, setPatterns] = useState<Record<string, string>>({});
  const [view, setViewState] = useState<View>({ pxPerBeat: 28, grid: 1 });

  const [save, setSave] = useState<SaveState>("saved");
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const savedEdits = useRef(0);
  const attemptedEdits = useRef(-1);
  const saving = useRef(false);
  const saveState = useRef<SaveState>("saved");
  const authorRef = useRef(author);
  authorRef.current = author;
  const setSaveBoth = useCallback((next: SaveState, message: string | null = null) => {
    saveState.current = next;
    setSave(next);
    setSaveMessage(message);
  }, []);

  // ── loading ──────────────────────────────────────────────────────────────

  const load = useCallback(async () => {
    const response = await fetch(`/api/projects/${id}`);
    if (response.status === 404) throw new Error("There is no project here.");
    if (!response.ok) throw new Error(`Could not load the project (${response.status}).`);
    const project = normalizeProject((await response.json()).project);
    if (!project) throw new Error("The project could not be read.");
    store.dispatch({ type: "load", project });
    savedEdits.current = 0;
    setSaveBoth("saved");
  }, [id, store, setSaveBoth]);

  useEffect(() => {
    load().catch((e: Error) => setLoadError(e.message));
    fetch("/api/status")
      .then((r) => (r.ok ? r.json() : null))
      .then((status) => status && setMode(status.mode))
      .catch(() => {});
  }, [load]);

  // ── the engine ───────────────────────────────────────────────────────────

  useEffect(() => {
    const Ctx = window.AudioContext ?? (window as never as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const ctx = new Ctx({ latencyHint: "interactive" });
    const graph = createGraph(ctx, true);
    const transport = new Transport(
      ctx,
      graph,
      () => store.state!.project,
      (media) => buffers.current.get(media),
    );
    transport.onStop = () => setPlaying(false);
    engine.current = { ctx, graph, transport };
    return () => {
      transport.dispose();
      void ctx.close();
      engine.current = null;
    };
  }, [store]);

  const project = history?.project ?? null;
  const audioClips = useRef(new Map<string, AudioClip[]>());

  useEffect(() => {
    const live = engine.current;
    if (!project || !live) return;
    syncGraph(live.graph, project, true);

    // Notes are picked up by the scheduler as it goes; audio already playing is not.
    const next = new Map<string, AudioClip[]>();
    for (const track of project.tracks) if (track.kind === "audio") next.set(track.id, track.clips);
    const before = audioClips.current;
    const changed = next.size !== before.size || [...next].some(([trackId, clips]) => before.get(trackId) !== clips);
    audioClips.current = next;
    if (changed) live.transport.refreshClips();
  }, [project]);

  // ── media ────────────────────────────────────────────────────────────────

  useEffect(() => {
    const live = engine.current;
    if (!project || !live) return;
    for (const media of project.media) {
      if (mediaStates.current.has(media.id)) continue;
      mediaStates.current.set(media.id, "loading");
      fetchAudio(media.path)
        .then((bytes) => live.ctx.decodeAudioData(bytes))
        .then((buffer) => {
          buffers.current.set(media.id, buffer);
          mediaStates.current.set(media.id, "ready");
          engine.current?.transport.refreshClips();
        })
        .catch(() => mediaStates.current.set(media.id, "error"))
        .finally(redraw);
    }
  }, [project, redraw]);

  const adopt = useCallback((media: Media, buffer: AudioBuffer) => {
    buffers.current.set(media.id, buffer);
    mediaStates.current.set(media.id, "ready");
  }, []);

  /** Show what was just brought in: select the clip that plays it. */
  const reveal = useCallback((after: Project, mediaId: string) => {
    for (const track of after.tracks) {
      if (track.kind !== "audio") continue;
      const clip = [...track.clips].reverse().find((c) => c.media === mediaId);
      if (clip) return setSelection({ track: track.id, clip: clip.id });
    }
  }, []);

  // ── saving ───────────────────────────────────────────────────────────────

  const saveNow = useCallback(
    async (force = false, keepalive = false) => {
      const state = store.state;
      if (!state || !authorRef.current || saving.current) return;
      if (saveState.current === "conflict" && !force) return;
      saving.current = true;
      setSaveBoth("saving");
      const sent = state.edits;
      attemptedEdits.current = sent;
      try {
        const response = await fetch(`/api/projects/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ project: state.project, base_rev: state.project.rev, author: authorRef.current, force }),
          keepalive,
        });
        const data = await response.json().catch(() => ({}));
        if (response.status === 409) {
          setSaveBoth("conflict", data.error ?? "Someone else saved a newer version.");
          return;
        }
        if (!response.ok) throw new Error(data.error ?? `Could not save (${response.status}).`);
        store.dispatch({ type: "saved", rev: data.rev, updated_at: data.updated_at });
        savedEdits.current = sent;
        setSaveBoth(store.state!.edits === sent ? "saved" : "dirty");
      } catch (e) {
        setSaveBoth("error", (e as Error).message);
      } finally {
        saving.current = false;
      }
    },
    [id, store, setSaveBoth],
  );

  const edits = history?.edits ?? 0;
  useEffect(() => {
    if (!history || edits === savedEdits.current || saveState.current === "conflict") return;
    // A save that failed is not retried on a loop; the next edit, or a click on the indicator, tries again.
    if (saveState.current === "error" && edits === attemptedEdits.current) return;
    if (saveState.current !== "saving") setSaveBoth("dirty", author ? null : "Type your name to save.");
    const timer = window.setTimeout(() => void saveNow(), SAVE_AFTER_MS);
    return () => window.clearTimeout(timer);
    // `save` is a dependency so an edit made while a save was in flight is picked up when it lands.
  }, [edits, save, author, history, saveNow, setSaveBoth]);

  useEffect(() => {
    // Closing the tab a moment after an edit should not lose it.
    const flush = () => {
      if (store.state && store.state.edits !== savedEdits.current) void saveNow(false, true);
    };
    window.addEventListener("pagehide", flush);
    return () => window.removeEventListener("pagehide", flush);
  }, [store, saveNow]);

  const resolveConflict = useCallback(
    (keep: "mine" | "theirs") => {
      if (keep === "mine") void saveNow(true);
      else load().catch((e: Error) => setError(e.message));
    },
    [saveNow, load],
  );

  // ── editing ──────────────────────────────────────────────────────────────

  const apply = useCallback(
    (edit: Edit, key?: string): Project => {
      store.dispatch({ type: "edit", edit, key, at: Date.now() });
      return store.state!.project;
    },
    [store],
  );
  const undo = useCallback(() => store.dispatch({ type: "undo" }), [store]);
  const redo = useCallback(() => store.dispatch({ type: "redo" }), [store]);

  // ── transport ────────────────────────────────────────────────────────────

  const take = useRef<{ recording: Recording; startBeat: number; trackId?: string } | null>(null);

  const finishTake = useCallback(async () => {
    const live = engine.current;
    const current = take.current;
    take.current = null;
    setRecording(false);
    if (!live || !current) return;
    const buffer = current.recording.stop();
    live.transport.stop();
    if (!buffer || buffer.duration < 0.2) {
      setError("Nothing was recorded.");
      return;
    }
    setBusy("Saving the take…");
    try {
      const mediaId = newId();
      const path = await uploadMedia(id, mediaId, "wav", encodeWav(buffer), mode);
      const media: Media = { id: mediaId, name: "Take", path, duration_s: buffer.duration, from: "recording" };
      adopt(media, buffer);
      const bpm = store.state!.project.bpm;
      const at = Math.max(0, current.startBeat - secondsToBeats(current.recording.latency_s, bpm));
      reveal(apply(addAudio(media, at, current.trackId)), media.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }, [id, mode, adopt, apply, reveal, store]);

  const record = useCallback(async () => {
    const live = engine.current;
    if (!live) return;
    if (take.current) return finishTake();
    // The browser may be showing a permission prompt; say what is being waited for.
    setBusy("Waiting for the microphone…");
    try {
      const recording = await startRecording(live.ctx);
      setBusy(null);
      const selected = store.state!.project.tracks.find((t) => t.id === selection.track);
      const current = { recording, startBeat: live.transport.position(), trackId: selected?.kind === "audio" ? selected.id : undefined };
      take.current = current;
      if (!live.transport.playing) await live.transport.play();
      setPlaying(true);
      recording.begin((time) => {
        current.startBeat = live.transport.beatAt(time);
      });
      setRecording(true);
    } catch (e) {
      take.current = null;
      setBusy(null);
      setError((e as Error).message);
    }
  }, [finishTake, selection.track, store]);

  const stop = useCallback(() => {
    if (take.current) void finishTake();
    else engine.current?.transport.stop();
    setPlaying(false);
  }, [finishTake]);

  const toggle = useCallback(() => {
    const live = engine.current;
    if (!live) return;
    if (live.transport.playing) return stop();
    void live.transport.play();
    setPlaying(true);
  }, [stop]);

  const seek = useCallback((beat: number) => engine.current?.transport.seek(beat), []);
  const position = useCallback(() => engine.current?.transport.position() ?? 0, []);

  // ── audio in and out ─────────────────────────────────────────────────────

  const importFile = useCallback(
    async (file: File, trackId?: string) => {
      const live = engine.current;
      if (!live) return;
      const ext = file.name.includes(".") ? file.name.split(".").pop()!.toLowerCase() : "";
      if (!MEDIA_EXTENSIONS.includes(ext)) {
        setError(`.${ext || "?"} is not audio this can open. Use WAV, MP3, M4A, FLAC or OGG.`);
        return;
      }
      setBusy(`Opening ${file.name}…`);
      try {
        let buffer: AudioBuffer;
        try {
          buffer = await live.ctx.decodeAudioData(await file.arrayBuffer());
        } catch {
          throw new Error(`This browser could not open ${file.name}. Try a WAV or MP3 of it.`);
        }
        const mediaId = newId();
        const path = await uploadMedia(id, mediaId, ext, file, mode);
        const media: Media = { id: mediaId, name: file.name.slice(0, 120), path, duration_s: buffer.duration, from: "import" };
        adopt(media, buffer);
        reveal(apply(addAudio(media, 0, trackId)), media.id);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setBusy(null);
      }
    },
    [id, mode, adopt, apply, reveal],
  );

  const addSongAudio = useCallback(
    async (name: string, path: string, song: string) => {
      const live = engine.current;
      if (!live) return;
      setBusy(`Loading ${name}…`);
      try {
        const buffer = await live.ctx.decodeAudioData(await fetchAudio(path));
        const media: Media = { id: newId(), name, path, duration_s: buffer.duration, from: "song", song };
        adopt(media, buffer);
        reveal(apply(addAudio(media, 0)), media.id);
      } catch {
        setError(`${name} would not load. It may still be on its way from the studio Mac.`);
      } finally {
        setBusy(null);
      }
    },
    [adopt, apply, reveal],
  );

  const bounce = useCallback(
    (options?: BounceOptions) => renderProject(store.state!.project, buffers.current, options),
    [store],
  );

  // ── auditioning ──────────────────────────────────────────────────────────

  const previewDrum = useCallback(
    (trackId: string, voice: DrumVoice, velocity = 0.85) => {
      const live = engine.current;
      const track = store.state?.project.tracks.find((t) => t.id === trackId);
      const strip = live?.graph.strips.get(trackId);
      if (!live || !strip || track?.kind !== "drums") return;
      void live.ctx.resume();
      playDrum(live.ctx, strip.input, track.kit, voice, live.ctx.currentTime + 0.01, velocity);
    },
    [store],
  );

  const previewNote = useCallback(
    (trackId: string, pitch: number) => {
      const live = engine.current;
      const track = store.state?.project.tracks.find((t) => t.id === trackId);
      const strip = live?.graph.strips.get(trackId);
      if (!live || !strip || track?.kind !== "synth") return;
      void live.ctx.resume();
      playNote(live.ctx, strip.input, track.synth, pitch, live.ctx.currentTime + 0.01, 0.25, 0.8);
    },
    [store],
  );

  const scratch = useRef(new Float32Array(1024));
  const level = useCallback((trackId: string | null) => {
    const graph = engine.current?.graph;
    if (!graph) return -Infinity;
    return peakDb(trackId ? (graph.strips.get(trackId)?.meter ?? null) : graph.masterMeter, scratch.current);
  }, []);

  // ── selection and view ───────────────────────────────────────────────────

  const select = useCallback((track: string | null, clip: string | null = null) => {
    setSelection((current) => (current.track === track && current.clip === clip ? current : { track, clip }));
  }, []);

  const patternOf = useCallback(
    (trackId: string): string | null => {
      const track = store.state?.project.tracks.find((t) => t.id === trackId);
      if (!track || track.kind === "audio") return null;
      const chosen = patterns[trackId];
      return track.patterns.some((p) => p.id === chosen) ? chosen : (track.patterns[0]?.id ?? null);
    },
    // `project` is here so the answer is recomputed when patterns come and go.
    [patterns, store, project],
  );
  const editPattern = useCallback((trackId: string, patternId: string) => {
    setPatterns((current) => ({ ...current, [trackId]: patternId }));
  }, []);
  const setView = useCallback((next: Partial<View>) => setViewState((current) => ({ ...current, ...next })), []);

  const mediaState = useCallback((mediaId: string): MediaState => mediaStates.current.get(mediaId) ?? "loading", []);
  const peaks = useCallback((mediaId: string): Float32Array | null => {
    const cached = peakCache.current.get(mediaId);
    if (cached) return cached;
    const buffer = buffers.current.get(mediaId);
    if (!buffer) return null;
    const made = peaksOf(buffer, Math.min(6000, Math.max(200, Math.ceil(buffer.duration * 40))));
    peakCache.current.set(mediaId, made);
    return made;
  }, []);

  const dismissError = useCallback(() => setError(null), []);

  const ws = useMemo<Workstation | null>(() => {
    if (!history || !project) return null;
    return {
      project,
      canUndo: history.past.length > 0,
      canRedo: history.future.length > 0,
      apply, undo, redo,
      save, saveMessage, saveNow: () => void saveNow(), resolveConflict,
      playing, recording, toggle, stop, seek, position, record,
      selection, select, patternOf, editPattern, view, setView,
      mediaState, peaks, importFile, addSongAudio, bounce,
      previewDrum, previewNote, level,
      busy, error, dismissError, mode,
    };
  }, [
    history, project, mediaVersion, apply, undo, redo, save, saveMessage, saveNow, resolveConflict, playing, recording, toggle, stop, seek,
    position, record, selection, select, patternOf, editPattern, view, setView, mediaState, peaks, importFile,
    addSongAudio, bounce, previewDrum, previewNote, level, busy, error, dismissError, mode,
  ]);

  return { ws, loadError };
}
