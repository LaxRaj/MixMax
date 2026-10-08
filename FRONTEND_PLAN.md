# Listening Test Web App

> A hosted, blind, loudness-matched listening test friends can open on a phone — built to fix the one step that currently has the worst completion rate: getting real people to actually give feedback.

- **Success metric:** 5+ friends complete a blind test from a shared link without a single follow-up message from you, and `producer tally` reads their responses with no manual CSV wrangling.
- **Status:** F0 built, with a 16-test Playwright suite (desktop + phone viewport) asserting every guarantee. Outstanding: real-device testing on an actual iOS Safari / Android handset — emulated viewports do not test the engine or the memory ceiling.
- **Repo:** `~/Desktop/projects/MixMax` — https://github.com/LaxRaj/MixMax
- **Depends on:** `producer blindtest` (built, M6). This is a delivery layer over it, not a replacement.

## Why this, and why now

`PLAN.md` lists "no web UI" as a non-goal, on the reasoning that a UI comes after the pipeline proves it sounds good. That reasoning still holds for *most* of a UI — and this plan does not build most of a UI.

It builds one screen. The pipeline is unproven precisely because the proving step is manual: zip a folder of WAVs, email it, ask someone to open a CSV, fill it in, and send it back. That workflow loses people at every step, and it produces a weak comparison even when it works — listening to A in full, then B in full, judges memory more than sound.

So this is not the deferred UI layer arriving early. It is the missing half of the measurement instrument.

## Non-goals
- **Not an operator console.** Intake, render and benchmark stay on the CLI, which already works. See F4 — and only if the CLI becomes the bottleneck, which it currently is not.
- **No accounts, no login.** The unguessable link is the access control. Friends should never create anything.
- **No audio editing**, no waveform scrubbing UI, no DAW features.
- **No real-time collaboration** — listeners must not see each other's answers, which would contaminate the results.
- **The CLI stays the source of truth.** The web app never masters, never measures, never decides. It plays files and collects opinions.
- **No mobile app**, no PWA install flow. A URL in a message.

## Architecture

**Two surfaces, only one of them worth building.** The operator (you) and the listener (a friend) need opposite things: the operator needs power and runs where the audio already is; the listener needs zero setup and runs on a phone across the country. Only the second is unserved today.

**Stack:** Next.js (App Router) on Vercel. Audio and responses in Vercel Blob. No database — see the decision log.

**The bridge is a CLI command, not a rewrite.** `producer publish` takes a blind-test folder that `producer blindtest` already produced, encodes the audio for the web, uploads it, and prints a link. Python stays the engine; the web app is a thin shell.

```
producer blindtest  ->  blind/<slug>/listen/{A,B,C}.wav   (lossless, loudness-matched)
producer publish    ->  encode to AAC, upload to Blob, create test, print URL
        friends     ->  open URL, A/B, score, submit
producer tally      ->  pull responses, un-blind locally, aggregate
```

**The key never leaves your machine.** The deployed app only ever knows `A`, `B`, `C`. The label→source mapping stays in `blind/<slug>.key.json` locally, and `producer tally` does the un-blinding. This means a leak of the whole deployment still cannot reveal which version was LANDR — the existing blind guarantee survives going online, structurally rather than by policy.

**Core abstractions**
- `ListeningTest` — a published test: slug, label list, excerpt bounds, audio URLs
- `Submission` — one listener's answers: scores, ranking, notes, user agent, duration
- `Excerpt` — the section of the track under test (start, length)

**Audio format.** Blind renders are lossless WAV; the web gets AAC at 256 kbps. This is a deliberate trade and it cuts both ways:
- *Against:* lossy encoding is itself processing, so listeners judge the encoder a little.
- *For:* every version gets identical encoding, and friends will hear streaming-encoded audio in real life anyway. It is arguably the more representative test — and it interacts with the true-peak finding from M6: masters above −1 dBTP distort when encoded, which a WAV-only test would hide.
- **Measurements stay lossless.** `producer benchmark` must never read the encoded files.

**Excerpts, not full tracks.** 60–90 seconds from the most revealing section, not three minutes. Three decoded 3-minute versions cost ~191 MB of browser memory, which a mid-range phone will not survive. Excerpting fixes that (~63 MB at 60s), cuts the download from 17 MB to ~6 MB, and makes a *better* test: listener fatigue sets in fast, and a focused chorus discriminates harder than a full take.

**Riskiest assumption:** that a phone browser can switch between 3 decoded streams instantly, sample-aligned, without a gap or a memory crash. Gapless switching is the entire reason to build this rather than keep emailing folders — if it does not work on a mid-range Android, the project is a nicer-looking version of what we already have. F0 exists to kill this risk before anything is deployed.

## Milestones

### [x] F0 — Gapless A/B player, running locally

- **Deliverable:** A Next.js page that loads a local blind-test folder and switches between versions instantly at the same playback position. No hosting, no upload, no backend.
- **Why first:** It is the riskiest assumption and the whole value proposition. Everything else is plumbing around it.
- **Prompt:**
  ```
  Scaffold a Next.js App Router app in `web/` for a blind listening test.

  Build a single page that:
  - Loads N audio files (labels A, B, C...) through Web Audio API: one
    AudioContext, one BufferSource per version, all started together at the
    same instant, each through its own GainNode.
  - Switching version sets the chosen gain to 1 and the others to 0, so the
    switch is instant and sample-aligned — never stop/start a source.
    Crossfade gains over ~15ms to avoid a click.
  - Exposes: play/pause, a seek bar, and one button per label. Keyboard: space
    to toggle, 1/2/3 to switch.
  - Shows elapsed time and loops the excerpt by default.
  - Renders a scoring form: 1-5 "would you hear this on a release" per label,
    a drag-to-order ranking, and a free-text notes box per label.
  - Reads its manifest from `public/test.json` for now:
    {slug, labels: ["A","B"], urls: {...}, excerpt: {start_s, length_s}}

  Do NOT display or fetch any source names — the page must only ever know
  labels.

  Verify in the browser that switching mid-playback is gapless and position
  is preserved.
  ```
- **Acceptance criteria:**
  - [x] Switching between versions mid-playback is inaudible as a transition and preserves position exactly — asserted in-browser: source objects unchanged across the switch, shared start time unchanged, gains flip 1→0/0→1, clock reads 0:02 before and after
  - [x] Desktop Chrome, plus an automated Playwright suite covering both viewports
  - [ ] Desktop Safari, iOS Safari, one mid-range Android — **not tested**, no device access here
  - [ ] Three 60s versions load and play without a crash on a phone — **not tested on a phone**; 3×20s verified on desktop
  - [x] Page source and network tab reveal no source names — only labels (`assertBlind` enforces it at load)
  - [x] Scoring form captures scores, ranking and notes
  - [x] Submission exports CSV that `producer tally` reads unmodified — verified end to end
- **Verify:** `cd web && npm run dev`, load a 3-version test, switch repeatedly mid-playback, confirm no gap and no position jump.

### [ ] F1 — `producer publish`

- **Deliverable:** `producer publish --blind-dir DIR [--excerpt START:LENGTH]` encodes, uploads and returns a shareable URL.
- **Prompt:**
  ```
  Add `producer/publish.py` and a `producer publish` CLI command.

  - Read a blind-test folder produced by `producer blindtest` (listen/*.wav).
  - Cut each version to the same excerpt window (default: full file; option
    --excerpt START:LENGTH in seconds). Every version MUST get an identical
    window — assert equal output lengths.
  - Encode each to AAC 256k via afconvert (macOS), falling back to a clear
    error elsewhere, mirroring producer/intake.py's transcode approach.
  - Upload to Vercel Blob under an unguessable prefix; never include the
    source name in any path or filename.
  - Write a manifest {slug, labels, urls, excerpt} and POST it to the app's
    create-test endpoint.
  - Print the listener URL and remind the operator the key file stays local.

  Assert the key file is NOT uploaded. Add a test proving no source name
  appears anywhere in the uploaded payload.
  ```
- **Acceptance criteria:**
  - [ ] One command turns a blind folder into a working link
  - [ ] Every version gets a byte-identical excerpt window
  - [ ] No source name appears in any URL, path or manifest
  - [ ] The key file is never uploaded
  - [ ] `pytest` passes
- **Verify:** `producer blindtest ... && producer publish --blind-dir blind/<slug> --excerpt 45:60`, open the link on a phone.

### [ ] F2 — Response collection and `producer tally --from`

- **Deliverable:** Friends submit in-browser; `producer tally --from <url-or-slug>` pulls responses and un-blinds them locally.
- **Prompt:**
  ```
  Add response collection.

  - A route handler accepting a submission: {slug, listener, responses:
    [{label, score, rank, notes}], meta: {user_agent, headphones: bool,
    listened_s}}.
  - Store one JSON object per submission in Blob under the test's prefix.
    One object per submission, never appending to a shared file, so
    concurrent submits cannot race.
  - Validate: known slug, known labels, score 1-5, ranks a permutation.
    Reject with a readable message.
  - After submitting, show a thank-you state; do not reveal the mapping or
    other listeners' answers.
  - Extend `producer tally` with `--from <slug|url>`: fetch submissions, map
    labels to sources via the LOCAL key file, and reuse the existing
    aggregation and report. Falls back to --responses for CSVs.

  Ask whether the listener used headphones and record it — laptop speakers
  hide most of what is being tested, and it is worth being able to filter.
  ```
- **Acceptance criteria:**
  - [ ] A submission from a phone is readable by `producer tally` with no manual steps
  - [ ] Concurrent submissions from several listeners do not overwrite each other
  - [ ] Malformed submissions are rejected with a readable message
  - [ ] Un-blinding still happens locally; the deployment never holds the mapping
  - [ ] Listeners cannot see each other's responses
- **Verify:** Submit from two devices simultaneously, then `producer tally --from <slug> --out results.md`.

### [ ] F3 — Operator results view

- **Deliverable:** A private page showing live results for a test: who has responded, mean scores, rankings, notes.
- **Why after F2:** Until responses exist, there is nothing to view. `producer tally` already covers the need; this is convenience.
- **Acceptance criteria:**
  - [ ] Shows completion ("3 of 6 responded") so you know when to chase
  - [ ] Un-blinds only for the operator, never on the listener route
  - [ ] Flags results below 3 listeners as an anecdote, matching the CLI report

### [ ] F4 — Operator console (only if the CLI becomes the bottleneck)

- **Deliverable:** A local web UI over intake / render / benchmark.
- **Explicitly conditional.** Do not start this until you have run the full loop against real vocals at least twice and can name the specific step the CLI made slow. Build it to fix a bottleneck you have felt, not one you anticipate.

## Open questions
- **How are friends invited?** One link per test, or one link per friend? Per-friend links identify the listener without asking them to type a name, and let F3 show who is outstanding — at the cost of generating and tracking more links.
- **Which excerpt?** Choosing the section is a judgement call that affects the result. Picking the most flattering 60 seconds of our own master would quietly bias the test; the honest default is the same structural section (first chorus) for every version, chosen before listening.
- **Does the encoder need its own control?** If results are close, it is worth confirming the AAC encode is not what listeners are hearing, by re-running one test losslessly over wifi.

## Decision log
<!-- Append-only. Format: {date} — {decision} — {why} -->
2026-10-02 — Scope the frontend to the listening test only, not a general UI. — The pipeline is unproven because the proving step is manual; this is the measurement instrument, not the deferred UI layer.
2026-10-02 — The CLI remains the engine; the web app is a shell over `producer blindtest`. — Reimplementing mastering or QA in TypeScript would create a second source of truth for numbers that must not disagree.
2026-10-02 — No database; one JSON object per submission in Blob. — Volume is tens of rows; one object per submission removes write races without provisioning or maintaining a database. Revisit if a results dashboard needs querying.
2026-10-02 — The label→source key is never uploaded. — Un-blinding locally means even a full compromise of the deployment cannot reveal which version was LANDR, keeping the blind guarantee structural rather than procedural.
2026-10-02 — Listener audio is AAC 256k, measurements stay lossless. — Phones cannot stream 48 MB WAVs, every version gets identical treatment, and encoded playback matches how the music will actually be heard.
2026-10-02 — Test 60-90s excerpts rather than full tracks. — Three decoded 3-minute versions cost ~191 MB of browser memory; excerpting also reduces listener fatigue and sharpens discrimination.
2026-10-02 — Gapless switching is the riskiest assumption and gets its own first milestone. — Instant same-position switching is the only thing the web version does that emailing a folder cannot; if it fails on a mid-range phone there is no reason to build the rest.
2026-10-02 — F0 exports a CSV in `producer tally`'s existing schema rather than inventing a payload. — It makes F0 shippable on its own: friends can download and send the file back, which already beats mailing a folder, and F2 then only removes a manual step.
2026-10-03 — Ranking uses arrow controls, not drag-and-drop as the plan's prompt specified. — The HTML5 drag API does not fire on touch, and phones are the primary target; arrows also give keyboard and screen-reader users the same affordance.
2026-10-03 — No spectrum or level visualisation anywhere in the player. — Any per-version visual difference lets a listener rank by looking instead of listening, which would quietly void the blind test.
2026-10-03 — Position is tracked only while playing. — A suspended AudioContext's `currentTime` is unrelated to playback position; reading it idle displayed load time as position.
2026-10-03 — Added a Playwright suite that drives the real app, rather than an LLM agent clicking through it. — The guarantees here are mechanical (same source objects across a switch, no source name in the DOM); a deterministic assertion proves them, where an agent's impression cannot.
2026-10-03 — No agent scores the listening test. — An agent cannot hear. Invented scores would feed `producer tune` and optimise the chain toward noise, corrupting the one measurement the project depends on.

---

# Part 2 — The pipeline dashboard

> One page showing the whole process and, more importantly, **how much of it rests on evidence**.

- **Success metric:** You can open one page and answer "what do we actually know, and what am I still guessing?" without running a command.
- **Status:** F5 built — `producer dashboard` plus the page, covered by 9 Playwright tests and 11 Python tests.

## Why this screen, and not a prettier one

This project keeps surfacing the same tension: some numbers are **published**, some are **measured from a corpus**, and some are **invented by whoever typed them**. Three times now, a number that looked authoritative turned out to be a guess — the `-16..-9` QA window, the de-ess frequency, and my own Spotify normalization model before it was checked against the docs.

So the dashboard's job is not to look like a plugin. It is to make the **provenance of every number visible**, and to show plainly where the evidence runs out — which is currently at the only question that matters, whether any of this sounds good.

## What it shows

1. **The pipeline** — intake → mix → master → QA → benchmark → blind test, with each stage's real state: how many tracks are through it, what failed, what is stale.
2. **Evidence ledger** — every tunable number, with where it came from: `published` (a platform spec), `measured` (the reference corpus), `fitted` (`producer tune`), or `guessed` (a default nobody has checked). This is the heart of the page.
3. **Standards conformance** — per master, what each platform does to it, and the headroom given up for nothing.
4. **Library profile** — what the corpus of real releases measures like, and where our masters sit against it.
5. **Listening results** — blind test scores when they exist, and an honest empty state when they do not.

## Non-goals
- No audio processing in the browser. The CLI stays the engine; this reads its JSON output.
- No live transport or waveform editing — the listening test already covers playing audio.
- Not a replacement for the CLI, and not a control surface. **Read-only.**

## Architecture

The CLI already emits JSON for every stage. A `producer dashboard` command collects those into one `dashboard.json`, and the page renders it. No server, no database, no second source of truth — the same decision as the listening test.

```
producer dashboard --workspace comparisons --library reference_library.json
  -> web/public/dashboard.json  -> open the page
```

**Riskiest assumption:** that an evidence ledger is actually more useful than a conventional dashboard of charts. It is a bet that knowing *why* a threshold exists beats seeing another gauge of the threshold itself.

## Milestones

### [x] F5 — Dashboard

- **Deliverable:** `producer dashboard` emits the aggregate JSON; a page renders the pipeline, the evidence ledger, standards conformance and library profile, with honest empty states.
- **Acceptance criteria:**
  - [x] Every number on the page is labelled published / measured / fitted / reported / guessed
  - [x] Stages with no data say so plainly rather than rendering an empty chart
  - [x] Works with a completely empty workspace, and when dashboard.json is missing
  - [x] No source names leak from any blind test in progress
  - [x] Covered by the Playwright suite (9 dashboard tests, desktop and phone)
2026-10-03 — A fifth provenance tier, `reported`, sits between measured and guessed. — A widely reported platform target is weaker evidence than a published spec but far stronger than a de-ess frequency I picked; collapsing both to "guessed" misrepresented each.
2026-10-03 — The dashboard computes nothing; it renders `dashboard.json`. — Same call as the listening test: a second implementation of any of these numbers could disagree with the CLI's.

---

# Part 3 — The studio

> A hosted place where two people can open any song, hear each piece of it, say what they think, change how it is rendered, and send in files.

- **Success metric:** A friend opens a link on a phone, leaves a note on the bridge of a song and nudges the vocal up a dB, and both show up in the repo — `feedback.md` and a new master — without a message being sent.
- **Status:** F6 built, tested, and running as a Vercel preview against a private Blob store. Production (`mixmax-studio.vercel.app`) has not been promoted yet, and a friend cannot open a preview — see `web/README.md` → *Hosting it*.

## What changed, and what did not

Parts 1 and 2 both say "not an operator console" and "read-only". This reverses that, on purpose: the bottleneck stopped being the pipeline and became the conversation about the songs, which was happening in messages the project could not read.

One rule survives untouched: **the CLI is the engine.** The web app still processes no audio and measures nothing. It records intent — a note, a file, a settings request — and `producer sync` on the studio Mac does the work and publishes the result.

```
browser ──► Next.js on Vercel ──► private Vercel Blob (no database)
                                        ▲
                     producer sync --watch      (the studio Mac: the only worker)
                       uploads   → intake gate → vocals/, comparisons/<slug>/
                       requests  → validate → re-render from the stage they touch
                       feedback  → comparisons/<slug>/feedback.md
                       publish   → song.json + AAC audio → the UI
```

## Milestones

### [x] F6 — Songs, feedback, uploads

- **Screens:** `/` songs home · `/songs/<slug>` song · `/upload` · `/listen` (the blind test, moved from `/`) · `/dashboard` (unchanged) · `/login`.
- **Acceptance criteria:**
  - [x] One nav reaches every screen; it is hidden on `/listen` so a blind listener sees no song names
  - [x] Every component of a song that exists can be played, with its measured loudness, peak and length
  - [x] A note saves itself (no submit button), can be about the whole song, a component, or a section of the arrangement, and lands in `comparisons/<slug>/feedback.md`
  - [x] A settings change is validated twice (page and Mac), re-renders only the stages downstream of it, and its outcome is shown on the song and written to `feedback.md`
  - [x] A failed render leaves every file as it was
  - [x] An uploaded file is checked by the same intake gate as the CLI; every problem is shown on `/upload`, badged in the nav, printed by `producer sync`, and kept in `comparisons/UPLOAD_LOG.md`
  - [x] An upload never overwrites a song's vocal or beat unless it was sent as a replacement from that song; a replaced file is moved to `.replaced/`, never deleted
  - [x] The UI says plainly when the studio Mac is not syncing
  - [x] Everything except `/listen` is behind one shared passcode; a deployment with no passcode set refuses to serve rather than serving openly
  - [x] 29 Python tests and 28 Playwright tests (×2 viewports)
  - [x] Preview deployed to Vercel with a private Blob store; login, songs, audio (presigned, with byte ranges), notes and direct-to-Blob uploads all checked against it
  - [ ] Promoted to production — **not done**, waiting on the operator
  - [ ] Used from a real phone over the hosted URL — **not tested**

2026-10-07 — The studio reverses "not an operator console" and "read-only". — The pipeline works; what was missing was a record of what two people think of each song, and a way for the second person to act on it.
2026-10-07 — The web app queues settings changes; it never renders. — A second implementation of the chain in the browser would produce numbers that disagree with the CLI's. The cost is that nothing happens while the Mac is off, so the UI says when it is.
2026-10-07 — Settings nobody chose are shown as "not recorded", not as fact. — The existing masters were made from the CLI before `settings.json` existed, so the balance and arrangement values that produced them are unknown. Displaying the default as if it were the value in force would be the same mistake the evidence ledger exists to catch.
2026-10-07 — A stage re-renders only if its output already exists or the change names it. — Changing the vocal chain should refresh the master built on it, not conjure an extended cut nobody asked for.
2026-10-07 — Renders go to a scratch folder and are moved in only when every stage succeeds. — A half-applied change would leave a master that matches no recorded settings.
2026-10-07 — `feedback.md` is rebuilt from all notes on every sync, never appended to. — Notes are edited and deleted; a deterministic rebuild makes re-running sync safe and keeps the file a faithful view of the store.
2026-10-07 — An upload from the Upload screen cannot replace a song's vocal or beat. — Replacement is destructive to everything downstream, so it has to be asked for from the song itself.
2026-10-07 — The browser's pre-upload check treats "could not decode" as a caution, not a blocker. — Browsers disagree about AIFF, FLAC and CAF; refusing a good file because Chrome cannot open it would be a false alarm. The intake gate on the Mac is the authority.
2026-10-07 — The song screen streams audio; it does not decode it up front like the blind test. — Gapless switching is what the blind test is for. Here one component plays at a time and a three-minute master should start at once on a phone.
2026-10-07 — One shared passcode, and a name typed once per browser. — Two friends do not need accounts; they need notes that say who wrote them.

---

# Part 4 — The workstation

> A place in the studio to make the music, not only to talk about it: program drums, write parts, record, cut and arrange audio, mix, and send the result to the pipeline.

- **Success metric:** a beat for an existing song is built around its vocal in the browser, sent from the export dialog, and comes out of `producer sync` mixed under the lossless vocal — with nobody opening a desktop DAW.
- **Status:** F7 built and tested in Chromium at desktop and phone sizes. **Not yet judged by ear** — every check so far is a measurement or an assertion. Not tried against the hosted Blob store, in Safari, or on a real phone.

## What changed, and what did not

Part 1 lists "no audio editing, no waveform scrubbing UI, no DAW features" as a non-goal and Part 3 says "the web app never processes audio". `/produce` reverses both, on purpose: until now a beat had to be made somewhere else and uploaded, so the one creative step in the whole process happened outside the project.

What survives:

- **The CLI is still the only thing that measures or masters.** The workstation reports no LUFS, makes no release-readiness claim, and a bounce sent to the studio goes through the same intake gate as any upload.
- **Lossless originals stay on the studio Mac.** A song's audio in the workstation is the AAC copy `producer sync` published. It is there to build against; the export dialog leaves it out by default, so what goes back is the new part, and the pipeline mixes it with the original.
- **No database.** A project is one JSON object in the store; imported and recorded audio sit beside it.

```
/produce/<id> ──► projects/<id>/project.json          the arrangement, rewritten on every save
              ──► projects/<id>/media/<id>.<ext>      imported files and recorded takes
   Export     ──► uploads/<id>/file.wav + meta.json   the normal upload path; `producer sync` takes it from here
```

## Architecture

- **One scheduling function for playback and for the bounce.** `lib/daw/schedule.ts` answers "what starts between these two beats" from the project alone. Live playback asks it every 25 ms for the next 140 ms; the offline render asks it once for the whole song. They cannot drift apart because there is only one answer.
- **Nothing is sampled.** The drum kits and the synth are oscillators and seeded noise (`lib/daw/voices.ts`), so a project needs no downloads to make sound and renders identically everywhere.
- **Edits are pure functions** from one project to the next (`lib/daw/edit.ts`); the previous project is the undo step.
- **Recording is raw PCM** through an audio worklet, not `MediaRecorder`, which can only produce Opus or AAC.

## Milestones

### [x] F7 — Workstation

- **Screens:** `/produce` projects · `/produce/<id>` the workstation.
- **Acceptance criteria:**
  - [x] Drum machine: 8 voices, 3 kits, 1- or 2-bar step patterns with accents and ghost notes, starter beats, swing
  - [x] Synth: piano roll, 6 presets, and every parameter of the sound editable
  - [x] Audio: import a file, bring in any published piece of a song, or record the microphone; move, trim, split, duplicate, fade and level clips, with waveforms
  - [x] Timeline in bars with snapping, a loop region, a metronome, tap tempo, and a playhead that follows playback
  - [x] Mixer: volume, pan, mute, solo, 3-band EQ, reverb and echo sends per track, level meters, master limiter
  - [x] Undo and redo for every edit; a dragged fader is one step
  - [x] Projects save themselves; a save from a stale copy is refused and the person chooses whose version to keep
  - [x] Export is a 24-bit, 44.1 kHz WAV held under −1 dBFS; muted tracks are left out; it can be downloaded or sent to the studio as a beat, vocal or reference for a song
  - [x] 27 Playwright tests (×2 viewports), 10 of them on the scheduling and edit arithmetic with no browser
  - [ ] Listened to — **not done**; nobody has judged how the kits and synth sound
  - [ ] Hosted — **not tested** against Vercel Blob (media upload and decode-through-redirect are written but unexercised)
  - [ ] Safari and a real phone — **not tested**

## Decision log

2026-10-08 — The studio gets a workstation, reversing "no DAW features". — Every beat so far was made outside the project and uploaded; the creative step was the one thing the studio could not see or share.
2026-10-08 — The browser renders audio now, but still measures nothing. — A bounce is an input to the pipeline like any other upload. Loudness and true peak in two implementations would disagree, and the CLI's is the one with tests against references.
2026-10-08 — A song's audio in the workstation is the published AAC, and is left out of exports by default. — Bouncing a lossy vocal and sending it back as the song would quietly replace the original with a worse copy. Send the new part; let the Mac mix it with the real one.
2026-10-08 — The bounce keeps leading silence and trims trailing silence. — A beat has to line up with the vocal from zero; a silent tail only drags the loudness reading down.
2026-10-08 — The bounce is turned down as a whole if it would pass −1 dBFS, and says so. — Clipping is the one fault intake blocks outright. A limiter that hides it would change the mix; a gain trim does not.
2026-10-08 — Synthesized drums and synth, no sample library. — Samples mean licensing, hosting and a download before the first sound. The cost is range: there is no acoustic kit and no piano.
2026-10-08 — A project is one object with a revision number, not one object per event. — Notes could be one-per-event because they are independent. An arrangement is not; two half-merged arrangements are worse than being told someone else saved first.
2026-10-08 — Audio clips keep their length in seconds when the tempo changes. — There is no time-stretching. Stretching badly would be worse than saying a recording is the length it is.
2026-10-08 — Patterns are shared by their clips. — Fixing the hi-hat once should fix it in every bar; "Copy" makes an independent variation.
