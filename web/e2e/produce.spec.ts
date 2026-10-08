import { expect, test, type Page } from "@playwright/test";
import { existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import {
  addDrumTrack,
  addNote,
  addSynthTrack,
  historyReducer,
  initialHistory,
  setStep,
  splitClip,
  trimClipStart,
  updateTrack,
  addAudio,
} from "../lib/daw/edit";
import { audible, barBeat, clipsBetween, contentEnd, eventsBetween } from "../lib/daw/schedule";
import { newProject, normalizeProject, type Media, type Project } from "../lib/daw/types";
import { DATA_DIR, SONG, seed } from "./global-setup";

const projectFile = (id: string) => path.join(DATA_DIR, "projects", id, "project.json");
const stored = (id: string): Project => JSON.parse(readFileSync(projectFile(id), "utf8"));

/** Just enough of a WAV reader to check what the bounce actually contains. */
function readWav(file: string) {
  const bytes = readFileSync(file);
  const channels = bytes.readUInt16LE(22);
  const sampleRate = bytes.readUInt32LE(24);
  const bits = bytes.readUInt16LE(34);
  const frames = bytes.readUInt32LE(40) / (channels * 3);
  const left = new Float32Array(frames);
  let peak = 0;
  for (let i = 0; i < frames; i++) {
    for (let c = 0; c < channels; c++) {
      const value = bytes.readIntLE(44 + (i * channels + c) * 3, 3) / 8388608;
      if (c === 0) left[i] = value;
      peak = Math.max(peak, Math.abs(value));
    }
  }
  const rmsDb = (from: number, to: number) => {
    let sum = 0;
    const a = Math.floor(from * sampleRate);
    const b = Math.min(frames, Math.floor(to * sampleRate));
    for (let i = a; i < b; i++) sum += left[i] * left[i];
    return 10 * Math.log10(sum / Math.max(1, b - a) + 1e-12);
  };
  return { header: bytes.toString("ascii", 0, 4), channels, sampleRate, bits, seconds: frames / sampleRate, peakDb: 20 * Math.log10(peak), rmsDb };
}

async function open(page: Page, name = "Test beat"): Promise<string> {
  await page.addInitScript(() => window.localStorage.setItem("mixmax.name", "Lakshya"));
  const response = await page.request.post("/api/projects", { data: { name, author: "Lakshya" } });
  const { project } = await response.json();
  await page.goto(`/produce/${project.id}`);
  await expect(page.getByRole("toolbar", { name: "Transport" })).toBeVisible();
  return project.id;
}

async function addTrack(page: Page, choice: string): Promise<void> {
  await page.getByRole("button", { name: "+ Track" }).click();
  await page.getByRole("dialog", { name: "Add a track" }).getByRole("button", { name: choice, exact: true }).click();
}

async function saved(page: Page): Promise<void> {
  // The indicator still reads "Saved" for a frame after an edit; let it catch up first.
  await page.waitForTimeout(200);
  await expect(page.getByTestId("save-state")).toHaveText("Saved");
}

async function download(page: Page, testInfo: { outputPath: (name: string) => string }) {
  const [file] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: "Download WAV" }).click(),
  ]);
  const target = testInfo.outputPath("bounce.wav");
  await file.saveAs(target);
  return readWav(target);
}

test.beforeEach(() => seed());

// ── the engine's arithmetic, with no browser involved ───────────────────────

test.describe("what sounds when", () => {
  const base = () => addDrumTrack("Four on the floor", 4)(newProject("abcdef-1", "T", "x"));

  test("a pattern repeats to fill its clip, and stops where the clip stops", () => {
    const project = base();
    const kicks = eventsBetween(project, 0, 100).filter((e) => e.kind === "drum" && e.voice === "kick");
    expect(kicks.map((e) => e.beat)).toEqual(Array.from({ length: 16 }, (_, i) => i));
    expect(contentEnd(project)).toBe(16);
  });

  test("scheduling in small slices finds every event exactly once", () => {
    const withNotes = addSynthTrack("Keys", 4)(base());
    const synth = withNotes.tracks[1];
    const pattern = synth.kind === "synth" ? synth.patterns[0].id : "";
    const project = { ...addNote(synth.id, pattern, { pitch: 60, start: 1.25, length: 1, velocity: 0.8 })(withNotes), swing: 0.7 };
    const whole = eventsBetween(project, 0, 16);
    const sliced: unknown[] = [];
    for (let at = 0; at < 16; at += 0.093) sliced.push(...eventsBetween(project, at, Math.min(16, at + 0.093)));
    expect(sliced.length).toBe(whole.length);
    expect(new Set(sliced.map((e) => JSON.stringify(e))).size).toBe(whole.length);
  });

  test("swing delays every other sixteenth and leaves the beat alone", () => {
    const project = { ...addDrumTrack("Trap", 1)(newProject("abcdef-1", "T", "x")), swing: 1 };
    const hats = eventsBetween(project, 0, 4).filter((e) => e.kind === "drum" && e.voice === "hat").map((e) => e.beat);
    expect(hats).toContain(0);
    expect(hats).toContain(1.5);
    expect(hats.some((b) => Math.abs(b - (1.75 + 1 / 12)) < 1e-9)).toBe(true);
  });

  test("splitting a clip does not change what is heard", () => {
    const project = base();
    const track = project.tracks[0];
    const cut = splitClip(track.id, track.clips[0].id, 6.5)(project);
    expect(cut.tracks[0].clips).toHaveLength(2);
    const strip = (p: Project) => eventsBetween(p, 0, 16).map((e) => `${e.beat}:${e.kind === "drum" ? e.voice : e.pitch}`);
    expect(strip(cut)).toEqual(strip(project));
  });

  test("trimming the front of an audio clip keeps the audio where it was", () => {
    const media: Media = { id: "m1", name: "v.wav", path: "projects/abcdef-1/media/m1abcdef.wav", duration_s: 10, from: "import" };
    const project = addAudio(media, 4)(newProject("abcdef-1", "T", "x"));
    const track = project.tracks[0];
    const trimmed = trimClipStart(track.id, track.clips[0].id, 6)(project);
    const clip = trimmed.tracks[0].clips[0] as { start: number; offset_s: number; duration_s: number };
    // 2 beats at 120 bpm is one second.
    expect(clip).toMatchObject({ start: 6, offset_s: 1, duration_s: 9 });
    // Pressing play in the middle of a clip enters it part-way through.
    expect(clipsBetween(trimmed, 8, 8.1, true)[0]).toMatchObject({ beat: 8, skip_s: 1 });
  });

  test("solo silences everything else; mute wins over solo", () => {
    let project = addSynthTrack("Keys")(base());
    const [drums, keys] = project.tracks;
    project = updateTrack(keys.id, { solo: true })(project);
    expect(audible(project.tracks[0], project)).toBe(false);
    expect(audible(project.tracks[1], project)).toBe(true);
    project = updateTrack(keys.id, { mute: true })(project);
    expect(audible(project.tracks[1], project)).toBe(false);
    expect(drums.id).not.toBe(keys.id);
  });

  test("a fader drag is one undo step, and undo keeps the server's revision", () => {
    const project = base();
    const id = project.tracks[0].id;
    let state = initialHistory(project);
    for (const [i, db] of [-1, -2, -3].entries()) {
      state = historyReducer(state, { type: "edit", edit: updateTrack(id, { volume_db: db }), key: "vol", at: 1000 + i * 50 });
    }
    expect(state.past).toHaveLength(1);
    state = historyReducer(state, { type: "saved", rev: 7, updated_at: "now" });
    state = historyReducer(state, { type: "undo" });
    expect(state.project.tracks[0].volume_db).toBe(0);
    expect(state.project.rev).toBe(7);
    state = historyReducer(state, { type: "redo" });
    expect(state.project.tracks[0].volume_db).toBe(-3);
  });

  test("a project from outside is clamped, and what it cannot play is dropped", () => {
    expect(normalizeProject({ id: "../etc" })).toBeNull();
    const project = normalizeProject({
      id: "abcdef-1", bpm: 9000, name: "x",
      media: [{ id: "m1", name: "a", path: "uploads/secret/file.wav", duration_s: 3 }],
      tracks: [
        { kind: "audio", clips: [{ media: "m1", start: 0, duration_s: 3 }] },
        { kind: "drums", patterns: [{ id: "p1", steps: 16, lanes: { kick: [5, "x"] } }], clips: [{ pattern: "gone" }, { pattern: "p1", start: -4 }] },
        { kind: "theremin" },
      ],
    })!;
    expect(project.bpm).toBe(240);
    // A path outside song audio and project media is not something this may load.
    expect(project.media).toHaveLength(0);
    expect(project.tracks).toHaveLength(2);
    expect(project.tracks[0].clips).toHaveLength(0);
    const drums = project.tracks[1];
    expect(drums.kind === "drums" && drums.patterns[0].lanes.kick.slice(0, 2)).toEqual([1, 0]);
    expect(drums.clips).toMatchObject([{ pattern: "p1", start: 0, offset: 0 }]);
  });

  test("counts bars the way a sequencer does", () => {
    expect(barBeat(0)).toBe("1.1.1");
    expect(barBeat(5.75)).toBe("2.2.4");
  });

  test("turning a step on is heard, and off is not", () => {
    const project = base();
    const track = project.tracks[0];
    const pattern = track.kind === "drums" ? track.patterns[0].id : "";
    const on = setStep(track.id, pattern, "tom", 3, 1)(project);
    expect(eventsBetween(on, 0, 4).filter((e) => e.kind === "drum" && e.voice === "tom").map((e) => e.beat)).toEqual([0.75]);
    expect(eventsBetween(setStep(track.id, pattern, "tom", 3, 0)(on), 0, 4).some((e) => e.kind === "drum" && e.voice === "tom")).toBe(false);
  });
});

// ── the workstation, in a browser ───────────────────────────────────────────

test.describe("projects", () => {
  test("the nav reaches Produce, and a new project opens in the workstation", async ({ page }) => {
    await page.addInitScript(() => window.localStorage.setItem("mixmax.name", "Lakshya"));
    await page.goto("/");
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Produce" }).click();
    await expect(page.getByRole("heading", { name: /make something/i })).toBeVisible();

    await page.getByLabel("Project name").fill("Late bus");
    await page.getByRole("button", { name: "New project" }).click();
    await expect(page).toHaveURL(/\/produce\/[a-z0-9-]+$/);
    await expect(page.getByLabel("Project name")).toHaveValue("Late bus");
    await expect(page.getByText("Nothing here yet.")).toBeVisible();

    await page.goto("/produce");
    await expect(page.getByRole("link", { name: "Late bus" })).toBeVisible();
  });

  test("deleting a project takes its file with it, after being asked twice", async ({ page }) => {
    const id = await open(page, "Scrap");
    await page.goto("/produce");
    await page.getByRole("button", { name: "Delete Scrap" }).click();
    expect(existsSync(projectFile(id))).toBe(true);
    await page.getByRole("button", { name: /yes, delete/i }).click();
    await expect(page.getByRole("link", { name: "Scrap" })).toHaveCount(0);
    expect(existsSync(projectFile(id))).toBe(false);
  });

  test("a project that does not exist says so", async ({ page }) => {
    await page.goto("/produce/nope-nope-nope");
    await expect(page.getByRole("heading", { name: "Not found" })).toBeVisible();
  });
});

test.describe("making a beat", () => {
  test("a drum track arrives with a beat, and steps toggle and save themselves", async ({ page }) => {
    const id = await open(page);
    await addTrack(page, "Four on the floor");
    await expect(page.getByRole("button", { name: "Beat 1 on Drums" })).toBeVisible();

    const kick1 = page.getByRole("button", { name: "Kick step 1", exact: true });
    const tom4 = page.getByRole("button", { name: "Tom step 4", exact: true });
    await expect(kick1).toHaveAttribute("aria-pressed", "true");
    await kick1.click();
    await tom4.click();
    await expect(kick1).toHaveAttribute("aria-pressed", "false");
    await expect(tom4).toHaveAttribute("aria-pressed", "true");

    await saved(page);
    const track = stored(id).tracks[0];
    expect(track.kind).toBe("drums");
    if (track.kind !== "drums") return;
    expect(track.patterns[0].lanes.kick[0]).toBe(0);
    expect(track.patterns[0].lanes.tom[3]).toBeGreaterThan(0);
    expect(stored(id).updated_by).toBe("Lakshya");

    // And it is all still there after a reload.
    await page.reload();
    await page.getByLabel("Track name").click();
    await expect(page.getByRole("button", { name: "Tom step 4", exact: true })).toHaveAttribute("aria-pressed", "true");
  });

  test("undo and redo step through edits", async ({ page }) => {
    await open(page);
    await addTrack(page, "Boom bap");
    const step = page.getByRole("button", { name: "Cowbell step 2", exact: true });
    await step.click();
    await expect(step).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: "Undo" }).click();
    await expect(step).toHaveAttribute("aria-pressed", "false");
    await page.getByRole("button", { name: "Redo" }).click();
    await expect(step).toHaveAttribute("aria-pressed", "true");
    // Undoing past the track removes it.
    await page.getByRole("button", { name: "Undo" }).click();
    await page.getByRole("button", { name: "Undo" }).click();
    await expect(page.getByText("Nothing here yet.")).toBeVisible();
  });

  test("play moves the playhead and stop holds it", async ({ page }) => {
    await open(page);
    await addTrack(page, "Trap");
    const position = page.getByTestId("position");
    await expect(position).toHaveText("1.1.1");
    await page.getByRole("button", { name: "Play", exact: true }).click();
    await expect(position).not.toHaveText(/^1\.1\./, { timeout: 5000 });
    await page.getByRole("button", { name: "Stop", exact: true }).click();
    const held = await position.textContent();
    await page.waitForTimeout(400);
    await expect(position).toHaveText(held!);
    await page.getByRole("button", { name: "Back to the start" }).click();
    await expect(position).toHaveText("1.1.1");
  });

  test("the loop keeps the playhead inside it", async ({ page }) => {
    await open(page);
    await addTrack(page, "Trap");
    await page.getByLabel("Tempo in beats per minute").fill("240");
    await page.getByLabel("Loop to bar").fill("1");
    await page.getByRole("button", { name: "Loop", exact: true }).click();
    await page.getByRole("button", { name: "Play", exact: true }).click();
    // One bar at 240 bpm is a second; after three, an unlooped playhead would be in bar 4.
    await page.waitForTimeout(3000);
    await expect(page.getByTestId("position")).toHaveText(/^1\./);
    await page.getByRole("button", { name: "Stop", exact: true }).click();
  });

  test("the bounce is a 24-bit WAV with the kicks where the grid says", async ({ page }, testInfo) => {
    await open(page);
    await addTrack(page, "Four on the floor");
    // Leave only the kick: one hit per beat, nothing between.
    for (const name of ["Clap step 5", "Clap step 13", "Hat step 3", "Hat step 7", "Hat step 11", "Hat step 15", "Open hat step 15"]) {
      await page.getByRole("button", { name, exact: true }).click();
    }
    await page.getByRole("button", { name: "Export" }).click();
    const wav = await download(page, testInfo);

    expect(wav).toMatchObject({ header: "RIFF", channels: 2, sampleRate: 44100, bits: 24 });
    // Four bars at 120 bpm is eight seconds, plus the last kick's tail.
    expect(wav.seconds).toBeGreaterThan(7.5);
    expect(wav.seconds).toBeLessThan(11.1);
    expect(wav.peakDb).toBeLessThanOrEqual(-0.99);
    expect(wav.peakDb).toBeGreaterThan(-12);
    for (const beat of [0, 1, 7, 15]) {
      const on = wav.rmsDb(beat * 0.5, beat * 0.5 + 0.08);
      const before = wav.rmsDb(beat * 0.5 + 0.42, beat * 0.5 + 0.49);
      expect(on - before).toBeGreaterThan(12);
    }
    await expect(page.getByTestId("bounce-facts")).toContainText("24-bit");
  });

  test("a muted track is left out of the export, and an empty export is refused", async ({ page }) => {
    await open(page);
    await addTrack(page, "Trap");
    await page.getByRole("button", { name: "Mute Drums" }).click();
    await page.getByRole("button", { name: "Export" }).click();
    const dialog = page.getByRole("dialog", { name: "Export" });
    await expect(dialog.getByText("— muted")).toBeVisible();
    await expect(dialog.getByRole("checkbox")).toBeDisabled();
    await expect(dialog.getByRole("button", { name: "Download WAV" })).toBeDisabled();
  });
});

test.describe("writing a part", () => {
  test("clicking the piano roll adds a note that is saved and exported", async ({ page }, testInfo) => {
    const id = await open(page);
    await addTrack(page, "Keys");
    const roll = page.getByLabel("Piano roll");
    await roll.scrollIntoViewIfNeeded();
    const box = (await roll.boundingBox())!;
    // Past the 48px keyboard, on the second beat (88px a beat).
    await page.mouse.click(box.x + 48 + 88 + 10, box.y + 60);
    await expect(roll.getByRole("button", { name: /at beat 2$/ })).toHaveCount(1);

    await saved(page);
    const track = stored(id).tracks[0];
    expect(track.kind === "synth" && track.patterns[0].notes).toMatchObject([{ start: 1, velocity: 0.8 }]);

    await page.getByRole("button", { name: "Export" }).click();
    const wav = await download(page, testInfo);
    // Silent until the note, then not.
    expect(wav.rmsDb(0, 0.45)).toBeLessThan(-80);
    expect(wav.rmsDb(0.5, 0.7)).toBeGreaterThan(-40);

    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Delete note" }).click();
    await expect(roll.getByRole("button", { name: /at beat/ })).toHaveCount(0);
  });

  test("a second pattern can be made and placed on the timeline", async ({ page }) => {
    const id = await open(page);
    await addTrack(page, "Boom bap");
    await page.getByRole("button", { name: "Copy", exact: true }).click();
    await expect(page.getByRole("tab", { name: "Beat 2" })).toHaveAttribute("aria-selected", "true");
    await page.getByRole("button", { name: "Place at playhead" }).click();
    await expect(page.getByRole("button", { name: "Beat 2 on Drums" })).toHaveCount(1);
    await saved(page);
    expect(stored(id).tracks[0].clips).toHaveLength(2);
  });
});

test.describe("working with audio", () => {
  test("a song's vocal comes in as a clip, and is left out of the export by default", async ({ page }) => {
    const id = await open(page);
    await page.getByRole("button", { name: "+ Track" }).click();
    const dialog = page.getByRole("dialog", { name: "Add a track" });
    await dialog.getByRole("button", { name: SONG.title }).click();
    await dialog.getByRole("button", { name: "+ Raw vocal" }).click();

    await expect(page.getByRole("button", { name: /Night Drive — Raw vocal on/ })).toBeVisible();
    await expect(page.getByText(/streaming copy the studio published/)).toBeVisible();
    await saved(page);
    expect(stored(id).media[0]).toMatchObject({ from: "song", song: SONG.slug, path: SONG.components[0].audio });

    await addTrack(page, "Trap");
    await page.getByRole("button", { name: "Export" }).click();
    const checks = page.getByRole("dialog", { name: "Export" }).getByRole("checkbox");
    await expect(checks.nth(0)).not.toBeChecked();
    await expect(checks.nth(1)).toBeChecked();
  });

  test("an imported file can be split at the playhead, and the cut undone", async ({ page }) => {
    const id = await open(page);
    await page.getByRole("button", { name: "+ Track" }).click();
    await page.getByLabel("Import an audio file").setInputFiles(path.join(__dirname, "..", "public", "fixtures", "x.m4a"));
    const clips = page.getByRole("button", { name: /x\.m4a on/ });
    await expect(clips).toHaveCount(1);
    await saved(page);

    const media = stored(id).media[0];
    expect(media.from).toBe("import");
    expect(existsSync(path.join(DATA_DIR, media.path))).toBe(true);

    // Put the playhead two beats in by clicking the ruler, then cut.
    const ruler = (await page.getByTestId("ruler").boundingBox())!;
    await page.mouse.click(ruler.x + 2 * 28 + 2, ruler.y + 10);
    await expect(page.getByTestId("position")).toHaveText("1.3.1");
    await page.getByRole("button", { name: "Split at playhead" }).click();
    await expect(clips).toHaveCount(2);
    await saved(page);
    const [first, second] = stored(id).tracks[0].clips as { start: number; offset_s: number; duration_s: number }[];
    expect(first.duration_s).toBeCloseTo(1, 5);
    expect(second).toMatchObject({ start: 2 });
    expect(second.offset_s).toBeCloseTo(1, 5);

    await page.getByRole("button", { name: "Undo" }).click();
    await expect(clips).toHaveCount(1);
  });

  test("a file the browser cannot open is refused with a reason", async ({ page }) => {
    await open(page);
    await page.getByRole("button", { name: "+ Track" }).click();
    await page.getByLabel("Import an audio file").setInputFiles({
      name: "notes.wav", mimeType: "audio/wav", buffer: Buffer.from("this is not audio"),
    });
    await expect(page.getByText(/could not open notes\.wav/)).toBeVisible();
    await expect(page.getByText("Nothing here yet.")).toBeVisible();
  });
});

test.describe("sending to the studio", () => {
  test("a bounce is uploaded as a beat for a song, through the normal upload path", async ({ page }) => {
    await open(page, "Night Drive beat");
    await addTrack(page, "Trap");
    await page.getByRole("button", { name: "Export" }).click();
    const dialog = page.getByRole("dialog", { name: "Export" });
    await dialog.getByLabel("For which song").selectOption(SONG.slug);
    await dialog.getByRole("button", { name: "Send to the studio" }).click();
    await expect(dialog.getByText(/Sent as a beat for/)).toBeVisible();

    const uploads = path.join(DATA_DIR, "uploads");
    const [upload] = readdirSync(uploads);
    const meta = JSON.parse(readFileSync(path.join(uploads, upload, "meta.json"), "utf8"));
    expect(meta).toMatchObject({ kind: "beat", song: SONG.slug, replaces: false, uploader: "Lakshya", filename: "night-drive-beat.wav" });
    expect(readFileSync(path.join(uploads, upload, "file.wav")).toString("ascii", 0, 4)).toBe("RIFF");

    await dialog.getByRole("link", { name: /see the result on Upload/ }).click();
    await expect(page.getByText("night-drive-beat.wav")).toBeVisible();
  });
});

test.describe("two people, one project", () => {
  test("a save made from a stale copy is refused, not silently applied", async ({ page }) => {
    const id = await open(page);
    await addTrack(page, "Trap");
    await saved(page);

    // Someone else saves in the meantime.
    const theirs = stored(id);
    writeFileSync(projectFile(id), JSON.stringify({ ...theirs, name: "Theirs", rev: theirs.rev + 1, updated_by: "Asha" }));

    await page.getByRole("button", { name: "Kick step 2", exact: true }).click();
    await expect(page.getByText(/Asha saved a newer version/)).toBeVisible();
    expect(stored(id).name).toBe("Theirs");

    await page.getByRole("button", { name: "Load theirs" }).click();
    await expect(page.getByLabel("Project name")).toHaveValue("Theirs");
    await expect(page.getByText(/saved a newer version/)).toHaveCount(0);
  });
});

test.describe("recording", () => {
  test("a take lands on the timeline as lossless audio", async ({ page }) => {
    // No real microphone in a test browser: stand a 440 Hz tone in for one. Everything
    // after the browser hands over a stream — the capture, the WAV, the placement — is real.
    await page.addInitScript(() => {
      navigator.mediaDevices.getUserMedia = async () => {
        const ctx = new AudioContext();
        const tone = ctx.createOscillator();
        const out = ctx.createMediaStreamDestination();
        tone.connect(out);
        tone.start();
        return out.stream;
      };
    });
    const id = await open(page);
    await page.getByRole("button", { name: "Record", exact: true }).click();
    await expect(page.getByRole("button", { name: "Stop recording" })).toBeVisible();
    await page.waitForTimeout(1500);
    await page.getByRole("button", { name: "Stop recording" }).click();

    await expect(page.getByRole("button", { name: /Take on/ })).toBeVisible();
    await saved(page);
    const media = stored(id).media[0];
    expect(media.from).toBe("recording");
    expect(media.duration_s).toBeGreaterThan(1);
    const file = path.join(DATA_DIR, media.path);
    expect(path.extname(file)).toBe(".wav");
    const wav = readWav(file);
    expect(wav).toMatchObject({ header: "RIFF", channels: 1, bits: 24 });
    // The tone made it through at full level, not as silence.
    expect(wav.rmsDb(0.2, 1)).toBeGreaterThan(-6);
  });
});
