"use client";

import { useRef } from "react";
import {
  duplicateClip,
  duplicateTrack,
  moveTrack,
  removeClip,
  removeTrack,
  splitClip,
  updateAudioClip,
} from "@/lib/daw/edit";
import { barBeat, clipBeats } from "@/lib/daw/schedule";
import type { AudioClip, AudioTrack, PatternClip, Track } from "@/lib/daw/types";
import { useWs } from "@/lib/daw/useWorkstation";
import { clock } from "@/lib/song";
import { PianoRoll } from "./PianoRoll";
import { StepSequencer } from "./StepSequencer";
import styles from "./daw.module.css";

const KIND_COPY: Record<Track["kind"], string> = { drums: "Drum machine", synth: "Synth", audio: "Audio" };

function AudioPanel({ track, clip }: { track: AudioTrack; clip: AudioClip | undefined }) {
  const ws = useWs();
  const picker = useRef<HTMLInputElement>(null);
  const media = clip ? ws.project.media.find((m) => m.id === clip.media) : undefined;
  const set = (patch: Partial<AudioClip>, key: string) => clip && ws.apply(updateAudioClip(track.id, clip.id, patch), `${key}:${clip.id}`);

  return (
    <div className={styles.editor}>
      <div className={styles.patternTools}>
        <button className={styles.chip} onClick={() => picker.current?.click()}>
          Import audio onto this track
        </button>
        <input
          ref={picker}
          type="file"
          accept="audio/*,.wav,.mp3,.m4a,.flac,.ogg,.aif,.aiff"
          className={styles.hiddenInput}
          aria-label="Import audio onto this track"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void ws.importFile(file, track.id);
            event.target.value = "";
          }}
        />
        <span className={styles.hint}>Or press record with this track selected to sing or play onto it.</span>
      </div>

      {!clip || !media ? (
        <p className={styles.hint}>Select a clip on this track to trim, fade or level it.</p>
      ) : (
        <>
          <p className={styles.clipFacts}>
            <strong>{media.name}</strong>
            <span className="mono">
              starts {barBeat(clip.start)} · {clock(clip.duration_s)} of {clock(media.duration_s)}
            </span>
          </p>
          {media.from === "song" && (
            <p className={styles.caution}>
              This is the streaming copy the studio published, not the lossless file on the studio Mac. Work
              against it freely, but leave it out when you export — send the new parts and let the pipeline
              mix them with the original.
            </p>
          )}
          <div className={styles.synth}>
            <label className={styles.knob}>
              <span>
                Clip gain <b className="mono">{clip.gain_db.toFixed(1)} dB</b>
              </span>
              <input
                type="range" className={styles.miniRange} min={-24} max={12} step={0.5} aria-label="Clip gain"
                value={clip.gain_db} onChange={(event) => set({ gain_db: Number(event.target.value) }, "gain")}
              />
            </label>
            <label className={styles.knob}>
              <span>
                Fade in <b className="mono">{clip.fade_in_s.toFixed(2)} s</b>
              </span>
              <input
                type="range" className={styles.miniRange} min={0} max={Math.min(10, clip.duration_s)} step={0.01} aria-label="Fade in"
                value={clip.fade_in_s} onChange={(event) => set({ fade_in_s: Number(event.target.value) }, "fin")}
              />
            </label>
            <label className={styles.knob}>
              <span>
                Fade out <b className="mono">{clip.fade_out_s.toFixed(2)} s</b>
              </span>
              <input
                type="range" className={styles.miniRange} min={0} max={Math.min(10, clip.duration_s)} step={0.01} aria-label="Fade out"
                value={clip.fade_out_s} onChange={(event) => set({ fade_out_s: Number(event.target.value) }, "fout")}
              />
            </label>
          </div>
        </>
      )}
    </div>
  );
}

/** Everything about the selected track: its tools, and the editor for what is on it. */
export function TrackPanel() {
  const ws = useWs();
  const { project, selection } = ws;
  const track = project.tracks.find((t) => t.id === selection.track);

  if (!track) {
    return (
      <p className={styles.hint} style={{ padding: 16 }}>
        {project.tracks.length === 0 ? "Add a track to get started." : "Select a track to edit what is on it."}
      </p>
    );
  }

  const clip = track.clips.find((c) => c.id === selection.clip) as PatternClip | AudioClip | undefined;
  const clipEnd = clip
    ? clip.start + (track.kind === "audio" ? clipBeats(clip as AudioClip, project.bpm) : (clip as PatternClip).length)
    : 0;
  const here = ws.position();
  const index = project.tracks.indexOf(track);

  return (
    <div>
      <div className={styles.trackTools}>
        <span className={styles.trackKind} style={{ ["--c" as string]: track.color }}>
          {track.name} · {KIND_COPY[track.kind]}
        </span>
        <span className={styles.toolGroup} aria-label="Clip">
          <button
            className={styles.chip}
            disabled={!clip}
            onClick={() => clip && ws.apply(splitClip(track.id, clip.id, ws.position()))}
            title={clip && (here <= clip.start || here >= clipEnd) ? "Move the playhead inside the clip first" : "Cut the selected clip at the playhead (S)"}
          >
            Split at playhead
          </button>
          <button className={styles.chip} disabled={!clip} onClick={() => clip && ws.apply(duplicateClip(track.id, clip.id))} title="Repeat the selected clip straight after itself (⌘D)">
            Duplicate clip
          </button>
          <button
            className={styles.chip}
            data-tone="danger"
            disabled={!clip}
            onClick={() => {
              if (!clip) return;
              ws.apply(removeClip(track.id, clip.id));
              ws.select(track.id);
            }}
            title="Delete the selected clip (Delete)"
          >
            Delete clip
          </button>
        </span>
        <span className={styles.toolGroup} aria-label="Track">
          <button className={styles.chip} disabled={index === 0} onClick={() => ws.apply(moveTrack(track.id, -1))} aria-label="Move track up">
            ↑
          </button>
          <button className={styles.chip} disabled={index === project.tracks.length - 1} onClick={() => ws.apply(moveTrack(track.id, 1))} aria-label="Move track down">
            ↓
          </button>
          <button className={styles.chip} onClick={() => ws.apply(duplicateTrack(track.id))}>
            Duplicate track
          </button>
          <button
            className={styles.chip}
            data-tone="danger"
            onClick={() => {
              ws.apply(removeTrack(track.id));
              ws.select(null);
            }}
          >
            Delete track
          </button>
        </span>
      </div>
      {track.kind === "drums" && <StepSequencer track={track} />}
      {track.kind === "synth" && <PianoRoll track={track} />}
      {track.kind === "audio" && <AudioPanel track={track} clip={clip as AudioClip | undefined} />}
    </div>
  );
}
