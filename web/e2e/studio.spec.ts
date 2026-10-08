import { expect, test, type Page } from "@playwright/test";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { DATA_DIR, SONG, seed } from "./global-setup";

const read = (...parts: string[]) => JSON.parse(readFileSync(path.join(DATA_DIR, ...parts), "utf8"));
const files = (...parts: string[]) => {
  const dir = path.join(DATA_DIR, ...parts);
  return existsSync(dir) ? readdirSync(dir) : [];
};

async function asLakshya(page: Page): Promise<void> {
  await page.addInitScript(() => window.localStorage.setItem("mixmax.name", "Lakshya"));
}

test.beforeEach(() => seed());

test.describe("getting around", () => {
  test("the nav reaches every screen", async ({ page }) => {
    await asLakshya(page);
    await page.goto("/");
    const nav = page.getByRole("navigation", { name: "Main" });

    await nav.getByRole("link", { name: "Upload" }).click();
    await expect(page.getByRole("heading", { name: /send in files/i })).toBeVisible();

    await nav.getByRole("link", { name: "Pipeline" }).click();
    await expect(page).toHaveURL(/\/dashboard$/);

    await nav.getByRole("link", { name: "Songs" }).click();
    await expect(page.getByRole("heading", { name: /what we're working on/i })).toBeVisible();

    await nav.getByRole("link", { name: "Listening tests" }).click();
    await expect(page).toHaveURL(/\/listen$/);
  });

  test("the songs home lists each song with how finished it is", async ({ page }) => {
    await page.goto("/");
    const card = page.getByRole("link", { name: /night drive/i });
    await expect(card).toContainText("60%");
    await expect(card).toContainText("Needs listeners.");
    await card.click();
    await expect(page.getByRole("heading", { name: "Night Drive" })).toBeVisible();
  });

  test("a blind listener sees no nav and no song names", async ({ page }) => {
    await page.goto("/listen");
    await expect(page.getByRole("navigation", { name: "Main" })).toHaveCount(0);
    await expect(page.getByText("Night Drive")).toHaveCount(0);
  });

  test("an old blind-test link at the root still works", async ({ page }) => {
    await page.goto("/?test=whatever");
    await expect(page).toHaveURL(/\/listen\?test=whatever$/);
  });

  test("the old comparison page lands on the songs home", async ({ page }) => {
    await page.goto("/compare");
    await expect(page).toHaveURL(/\/$/);
  });

  test("says so when the studio Mac has stopped syncing", async ({ page }) => {
    await page.route("**/api/songs", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.heartbeat = { at: new Date(Date.now() - 3 * 3600_000).toISOString(), host: "x", songs: 1 };
      await route.fulfill({ response, json: body });
    });
    await page.goto("/");
    await expect(page.getByText(/not syncing right now/i)).toBeVisible();
    await expect(page.getByText(/last seen 3 h ago/i)).toBeVisible();
  });
});

test.describe("the passcode", () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test("the studio is closed without it, and the blind test is not", async ({ page }) => {
    await page.goto(`/songs/${SONG.slug}`);
    await expect(page).toHaveURL(/\/login\?next=/);
    await expect(page.getByRole("heading", { name: /enter the passcode/i })).toBeVisible();

    await page.goto("/listen");
    await expect(page).toHaveURL(/\/listen$/);
  });

  test("the API is closed too", async ({ request }) => {
    expect((await request.get("/api/songs")).status()).toBe(401);
    expect((await request.get(`/api/audio/${SONG.components[0].audio}`)).status()).toBe(401);
    const note = await request.post("/api/feedback", { data: { slug: SONG.slug, id: "note-1234", text: "x" } });
    expect(note.status()).toBe(401);
  });

  test("a wrong passcode is refused and the right one lets you in", async ({ page }) => {
    await page.goto(`/songs/${SONG.slug}`);
    await page.getByLabel("Passcode").fill("nope");
    await page.getByRole("button", { name: /open the studio/i }).click();
    await expect(page.getByText("That passcode is not right.")).toBeVisible();

    await page.getByLabel("Passcode").fill("e2e-passcode");
    await page.getByRole("button", { name: /open the studio/i }).click();
    await expect(page.getByRole("heading", { name: "Night Drive" })).toBeVisible();
  });
});

test.describe("a song", () => {
  test.beforeEach(async ({ page }) => {
    await asLakshya(page);
    await page.goto(`/songs/${SONG.slug}`);
    await expect(page.getByRole("heading", { name: "Night Drive" })).toBeVisible();
  });

  test("shows every component with its measurements", async ({ page }) => {
    await expect(page.getByRole("button", { name: "Play Raw vocal" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Play Master" })).toBeVisible();
    await expect(page.getByText("-16.0").first()).toBeVisible();
    // A missing input is offered rather than hidden.
    await expect(page.getByRole("button", { name: "Add a beat" })).toBeVisible();
  });

  test("asks who you are before anything can be written", async ({ page, context }) => {
    const fresh = await context.newPage();
    await fresh.addInitScript(() => window.localStorage.removeItem("mixmax.name"));
    await fresh.goto(`/songs/${SONG.slug}`);
    await expect(fresh.getByText(/who's this/i)).toBeVisible();
    await expect(fresh.getByLabel("A note about the whole song")).toBeDisabled();
    await fresh.getByPlaceholder("Your name").fill("Asha");
    await fresh.getByRole("button", { name: "Continue" }).click();
    await expect(fresh.getByLabel("A note about the whole song")).toBeEnabled();
  });

  test("plays one component at a time", async ({ page }) => {
    await page.getByRole("button", { name: "Play Raw vocal" }).click();
    await expect(page.getByRole("button", { name: "Pause Raw vocal" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Now playing" })).toContainText("Raw vocal");

    await page.getByRole("button", { name: "Play Master" }).click();
    await expect(page.getByRole("button", { name: "Pause Master" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Play Raw vocal" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Now playing" })).toContainText("Master");
  });

  test("a note saves itself and survives a reload", async ({ page }) => {
    const box = page.getByLabel("A note about the whole song");
    await box.fill("The hook lands, the second verse drags.");
    await expect(page.getByRole("status").filter({ hasText: "Saved" })).toBeVisible();

    const saved = files("feedback", SONG.slug);
    expect(saved).toHaveLength(1);
    const note = read("feedback", SONG.slug, saved[0]);
    expect(note).toMatchObject({
      author: "Lakshya", text: "The hook lands, the second verse drags.", target: { kind: "song" },
    });

    await page.reload();
    await expect(page.getByLabel("Notes on this song")).toContainText("the second verse drags");
    await expect(page.getByLabel("Notes on this song")).toContainText("Lakshya");
  });

  test("editing a note rewrites it rather than adding another", async ({ page }) => {
    const box = page.getByLabel("A note about the whole song");
    await box.fill("First thought");
    await expect(page.getByRole("status").filter({ hasText: "Saved" })).toBeVisible();
    await box.fill("First thought, revised");
    await expect(page.getByLabel("Notes on this song")).toContainText("First thought, revised");
    await expect.poll(() => files("feedback", SONG.slug).length).toBe(1);
  });

  test("a note can be pinned to a component", async ({ page }) => {
    const master = page.getByRole("group", { name: "Master" });
    await master.getByRole("button", { name: "Add a note" }).click();
    await page.getByLabel("A note about Master").fill("Too bright up top.");
    await expect(page.getByRole("status").filter({ hasText: "Saved" })).toBeVisible();

    const [file] = files("feedback", SONG.slug);
    expect(read("feedback", SONG.slug, file).target).toEqual({ kind: "component", component: "master" });
  });

  test("a note can be pinned to a section of the arrangement", async ({ page }) => {
    await page.getByRole("button", { name: /Section B, 0:03 to 0:06/ }).click();
    await page.getByLabel(/A note about section B/).fill("Breakdown is too long.");
    await expect(page.getByRole("status").filter({ hasText: "Saved" })).toBeVisible();

    const [file] = files("feedback", SONG.slug);
    expect(read("feedback", SONG.slug, file).target).toMatchObject({
      kind: "section", component: "master", label: "B", start_s: 3, end_s: 6,
    });
    await expect(page.getByLabel("Notes on this song")).toContainText("Section B · 0:03");
  });

  test("your own note can be deleted", async ({ page }) => {
    await page.getByLabel("A note about the whole song").fill("Never mind");
    await expect(page.getByLabel("Notes on this song")).toContainText("Never mind");
    await page.getByRole("button", { name: "Delete" }).click();
    await expect(page.getByText("Nobody has left a note on this song yet.")).toBeVisible();
    expect(files("feedback", SONG.slug)).toHaveLength(0);
  });

  test("a settings change is queued, not rendered", async ({ page }) => {
    const send = page.getByRole("button", { name: "Request re-render" });
    await expect(send).toBeDisabled();

    await page.getByLabel("Loudness").fill("-14");
    await expect(page.getByText(/1 change ready/)).toBeVisible();
    await send.click();

    const queue = page.getByLabel("Settings changes");
    await expect(queue).toContainText(/queued/i);
    await expect(queue).toContainText("Loudness → -14 LUFS");

    const [file] = files("requests", SONG.slug);
    expect(read("requests", SONG.slug, file)).toMatchObject({
      slug: SONG.slug, author: "Lakshya", changes: { target_lufs: -14 },
    });
  });

  test("a value out of range cannot be sent", async ({ page }) => {
    await page.getByLabel("Loudness").fill("3");
    await expect(page.getByText("Between -24 and -8 LUFS.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Request re-render" })).toBeDisabled();
  });

  test("the server refuses a bad value even if the page is bypassed", async ({ request }) => {
    const bad = await request.post("/api/requests", {
      data: { slug: SONG.slug, author: "x", changes: { target_lufs: 99 } },
    });
    expect(bad.status()).toBe(400);
    const off = await request.post("/api/requests", {
      data: { slug: SONG.slug, author: "x", changes: { duck_db: 3 } },
    });
    expect(off.status()).toBe(400);
    expect((await off.json()).error).toMatch(/no beat yet/i);
    expect(files("requests", SONG.slug)).toHaveLength(0);
  });

  test("a setting that does not apply explains why instead of offering a knob", async ({ page }) => {
    await expect(page.getByText("There is no beat yet. Upload one and the balance can be set.")).toBeVisible();
    await expect(page.getByLabel("Beat ducking")).toHaveCount(0);
  });

  test("a finished request shows what was re-rendered", async ({ page, request }) => {
    const queued = await request.post("/api/requests", {
      data: { slug: SONG.slug, author: "Asha", changes: { target_lufs: -14 } },
    });
    const { request: made } = await queued.json();
    const { writeFileSync } = await import("node:fs");
    writeFileSync(
      path.join(DATA_DIR, "requests", SONG.slug, `${made.id}.result.json`),
      JSON.stringify({ state: "done", at: new Date().toISOString(), rendered: ["MASTER.wav"], before: { target_lufs: -16 } }),
    );
    await page.reload();
    const queue = page.getByLabel("Settings changes");
    await expect(queue).toContainText(/done/i);
    await expect(queue).toContainText("Loudness -16 → -14 LUFS");
    await expect(queue).toContainText("re-rendered MASTER.wav");
  });

  test("replacing a file uploads it as a replacement for this song", async ({ page }) => {
    await page.getByLabel("Replace the vocal file").setInputFiles(path.join(__dirname, "..", "public", "fixtures", "y.m4a"));
    await expect(page.getByText(/the studio mac checks it before anything is swapped/i)).toBeVisible();

    const [id] = files("uploads");
    expect(read("uploads", id, "meta.json")).toMatchObject({
      kind: "vocal", song: SONG.slug, replaces: true, uploader: "Lakshya", filename: "y.m4a",
    });
    expect(files("uploads", id)).toContain("file.m4a");
  });
});

test.describe("uploading", () => {
  test.beforeEach(async ({ page }) => {
    await asLakshya(page);
    await page.goto("/upload");
    await expect(page.getByRole("heading", { name: /send in files/i })).toBeVisible();
  });

  test("a file goes up and waits for its check", async ({ page }) => {
    await page.getByTestId("file-picker").setInputFiles(path.join(__dirname, "..", "public", "fixtures", "x.m4a"));
    await expect(page.getByText("x.m4a")).toBeVisible();
    await page.getByLabel("Song title").fill("Brand New");
    await page.getByRole("button", { name: "Upload 1 file" }).click();

    const sent = page.getByLabel("Uploaded files");
    await expect(sent).toContainText("x.m4a");
    await expect(sent).toContainText(/awaiting check/i);

    const [id] = files("uploads");
    expect(read("uploads", id, "meta.json")).toMatchObject({
      kind: "vocal", song: "brand-new", song_title: "Brand New", replaces: false, uploader: "Lakshya",
    });
  });

  test("an empty file is stopped before it is sent", async ({ page }) => {
    await page.getByTestId("file-picker").setInputFiles({ name: "silence.wav", mimeType: "audio/wav", buffer: Buffer.alloc(0) });
    await expect(page.getByText("The file is empty (0 bytes).")).toBeVisible();
    await expect(page.getByText("0 of 1 ready to send")).toBeVisible();
  });

  test("something that is not audio is stopped, unless it is marked as something else", async ({ page }) => {
    await page.getByTestId("file-picker").setInputFiles({ name: "lyrics.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF") });
    await expect(page.getByText(/\.pdf is not audio the pipeline reads/)).toBeVisible();
    await page.getByLabel("What is it?").selectOption("other");
    await expect(page.getByText("1 of 1 ready to send")).toBeVisible();
  });

  test("a beat is recognised from its name and matched to its song", async ({ page }) => {
    await page.getByTestId("file-picker").setInputFiles({
      name: "e2e-song-beat.wav", mimeType: "audio/wav", buffer: Buffer.from("RIFFxxxx"),
    });
    await expect(page.getByLabel("What is it?")).toHaveValue("beat");
    await expect(page.getByLabel("Which song?")).toHaveValue(SONG.slug);
  });

  test("a problem found by the studio Mac is shown with its reason, and badges the nav", async ({ page, request }) => {
    const { mkdirSync, writeFileSync } = await import("node:fs");
    const dir = path.join(DATA_DIR, "uploads", "upload-bad1");
    mkdirSync(dir, { recursive: true });
    writeFileSync(path.join(dir, "file.wav"), "x");
    await request.post("/api/uploads", {
      data: { id: "upload-bad1", filename: "hot take.wav", kind: "vocal", song: "Hot Take", uploader: "Asha" },
    });
    writeFileSync(path.join(dir, "report.json"), JSON.stringify({
      id: "upload-bad1", slug: "hot-take", kind: "vocal", verdict: "blocked",
      blockers: ["3 clipped region(s) in the raw recording."], warnings: [], metrics: {},
      placed: null, kept: "vocals/hot-take/hot take.wav", rendered: [], checked_at: new Date().toISOString(),
    }));

    await page.reload();
    const sent = page.getByLabel("Uploaded files");
    await expect(sent).toContainText("blocked");
    await expect(sent).toContainText("3 clipped region(s) in the raw recording.");
    await expect(page.getByLabel("1 file(s) with problems")).toBeVisible();

    await page.getByRole("button", { name: "Mark problems as seen" }).click();
    await expect(page.getByLabel("1 file(s) with problems")).toHaveCount(0);
  });
});
