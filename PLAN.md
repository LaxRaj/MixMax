# Producer CLI

> A CLI-only pipeline — analyze, mix, master, QA-gate a raw vocal — built to answer one question before anything else gets built: is automated processing good enough to sound release-ready?

- **Success metric:** `producer batch` runs against 3–5 real vocal files from friends and produces a `report.md` with automated QA results, plus your own subjective "does this sound release-ready" verdict per file.
- **Deadline:** ~1 week out (assumption — 6 milestones, each ≤1 day; adjust if wrong).
- **Status:** M0–M7 built plus chain fitting, a reference library and standards conformance, 140 tests green from a clean clone. Outstanding: every acceptance criterion that requires **real friend-supplied vocals** is still unchecked — the pipeline has only been exercised against synthetic fixtures. M6 adds the benchmarking and blind-listening tooling; M7 adds the intake gate so real vocals can go in. **Next blocker: reference tracks** — `producer render` needs one per track, or a fallback.
- **Repo:** `~/Desktop/projects/MixMax` — https://github.com/LaxRaj/MixMax

## Non-goals
- No web UI, no chat interface, no Producer Agent orchestrator — CLI only. That layer comes after this proves the pipeline sounds good.
- No instrumental generation — friends supply their own beat or a cappella vocal. "Generate" half of the architecture doc's Phase 1 is deferred to its own plan.
- No release/distribution, rights/splits, or promo-asset generation — that's Phases 2–4 of the architecture doc, not this build.
- No cloud deployment, auth, or persistent database — this runs on your machine against local files.
- No pitch-correction/autotune in the vocal chain yet — known gap, not built here; revisit once the base chain is validated.

## Architecture
- **Stack:** Python 3.11, `uv` (or pip + venv), Click for the CLI, `matchering` (reference-based mastering), `pedalboard` (Spotify's audio-FX library, confirmed current — classes used: `HighpassFilter`, `NoiseGate`, `Compressor`, `PeakFilter`, `Reverb`) for the vocal chain, `librosa` (tempo/pitch analysis), `pyloudnorm` (LUFS + QA metrics), `matplotlib` (before/after comparison plot), `pytest`.
- **Core abstractions:**
  - `Track` — a loaded audio file (path, sample array, sample rate)
  - `VocalAnalysis` — tempo, pitch range, dynamic range as structured data
  - `MixChain` — the vocal-specific Pedalboard chain (de-noise/de-ess/EQ/compression/space)
  - `QAReport` — pass/fail + specific flags (clipping, loudness, mono-compatibility)
  - `PipelineResult` — one file's bundled analysis + QA, used to build the batch report
- **Faked vs. real:** Reference tracks are real, user-supplied per genre. Instrumental generation is faked — friends supply their own beat. QA thresholds (LUFS range, clipping cutoff) start as reasonable defaults, not learned — they get tuned against real listening feedback from the batch runs, not against an ML model.
- **Riskiest assumption:** that `matchering` + a basic Pedalboard vocal chain gets close enough to "sounds professional" that friends can't easily tell it apart from a manually produced track. M0 and M2 exist specifically to de-risk this in the first two milestones, before anything else gets built on top of it.

## Milestones

### [x] M0 — Walking skeleton
- **Deliverable:** Repo scaffolded; `producer master --vocal PATH --reference PATH --out PATH` runs end-to-end on a real file using `matchering`.
- **Prompt:**
  ```
  Scaffold a new Python 3.11 project named `producer` for an AI music-production
  pipeline CLI.

  Create:
  - `pyproject.toml` with dependencies: click, matchering, numpy, soundfile
  - `producer/__init__.py`
  - `producer/cli.py` — a Click CLI group `producer` with one command `master`:
    `producer master --vocal PATH --reference PATH --out PATH`
    which calls `matchering.process(target=vocal, reference=reference,
    results=[matchering.pcm16(out)])` and prints a one-line confirmation with
    the output path and file size.
  - `tests/fixtures/` — generate two tiny synthetic WAV fixtures with numpy +
    soundfile at test-setup time (a 2-second 440Hz sine as `target.wav`, a
    2-second 220Hz sine at a different RMS as `reference.wav`) so tests don't
    depend on real audio.
  - `tests/test_cli.py` — a pytest test that runs the `master` command via
    Click's CliRunner against the synthetic fixtures and asserts the output
    file exists and is non-empty.
  - `.gitignore` (standard Python + `.env` + `*.wav` except `tests/fixtures/`)
  - `.env.example` (empty for now — no secrets needed at this stage)
  - `README.md` stub with install + one-line usage

  Install deps, run `pytest`, confirm it's green before stopping.
  ```
- **Acceptance criteria:**
  - [ ] `producer master` runs on a real friend-supplied vocal + a real reference track and produces a playable WAV
  - [x] `pytest` passes using only synthetic fixtures (no real audio required for CI)
  - [x] `.env`/`.gitignore` in place, no secrets committed
- **Verify:** `pytest -q && python -m producer master --vocal tests/fixtures/target.wav --reference tests/fixtures/reference.wav --out /tmp/out.wav && test -s /tmp/out.wav`

### [x] M1 — Vocal analysis
- **Deliverable:** `producer analyze --vocal PATH` returns tempo, pitch range, dynamic range as JSON.
- **Prompt:**
  ```
  Add vocal analysis to the `producer` CLI.

  Create:
  - `producer/analysis.py` with `analyze_vocal(path: str) -> dict` using
    librosa to compute: estimated tempo (`librosa.beat.beat_track`), pitch
    range (min/max over voiced frames via `librosa.pyin`), and RMS-based
    dynamic range. Return
    `{"tempo_bpm": float, "pitch_min_hz": float, "pitch_max_hz": float,
    "dynamic_range_db": float}`.
  - `producer analyze --vocal PATH [--out PATH]` CLI command: prints the dict
    as formatted JSON, writes it to `--out` if given.
  - Add `librosa` to `pyproject.toml`.
  - `tests/test_analysis.py`: build a synthetic click-track fixture at a known,
    controllable 120 BPM; assert `tempo_bpm` is within ±5 BPM of 120. Assert
    all four keys are present and finite (no NaN/inf) on both this fixture and
    the existing sine-wave fixture.

  Run `pytest`, confirm green.
  ```
- **Acceptance criteria:**
  - [ ] `producer analyze` on a real vocal returns valid JSON with all four fields populated
  - [x] `pytest` passes, tempo detection within ±5 BPM on the synthetic click track
  - [x] No NaN/inf in output on either fixture
- **Verify:** `pytest -q && python -m producer analyze --vocal tests/fixtures/target.wav | python -m json.tool`

### [x] M2 — Vocal mix chain
- **Deliverable:** `producer mix --vocal PATH --out PATH` applies a real vocal-processing chain; `producer master --premix` feeds mixed output into mastering instead of the raw vocal.
- **Prompt:**
  ```
  Add a vocal mix chain using Spotify's `pedalboard` library.

  Create:
  - `producer/mix.py` with `build_vocal_chain() -> pedalboard.Pedalboard`
    returning, in order: `HighpassFilter(cutoff_hz=80)` (rumble removal),
    `NoiseGate(threshold_db=-40)`, `Compressor(threshold_db=-18, ratio=3)`,
    a de-essing band via `PeakFilter` tuned around 6–8kHz with negative gain,
    and `Reverb(room_size=0.15, wet_level=0.08)` for subtle space. Comment
    each stage's purpose.
  - `mix_vocal(path: str, out_path: str) -> None`: loads the file, runs it
    through the chain at native sample rate, writes the result.
  - `producer mix --vocal PATH --out PATH` CLI command.
  - Add `pedalboard` to `pyproject.toml`.
  - Extend `producer master` with an optional `--premix` flag: when set, runs
    `mix_vocal` first, then feeds that result into matchering instead of the
    raw vocal.
  - `tests/test_mix.py`: run the sine-wave fixture through `mix_vocal`, assert
    the output exists, has matching duration (±1 sample-rate worth of
    tolerance for filter delay), and is not silent (RMS above a small
    epsilon).

  Run `pytest`, confirm green.
  ```
- **Acceptance criteria:**
  - [ ] `producer mix` on a real vocal produces an audibly processed file
  - [x] `producer master --premix` runs the full chain end-to-end
  - [x] `pytest` passes, output non-silent and correct duration
- **Verify:** `pytest -q && python -m producer mix --vocal tests/fixtures/target.wav --out /tmp/mixed.wav && test -s /tmp/mixed.wav`

### [x] M3 — QA gate
- **Deliverable:** `producer qa --file PATH` returns pass/fail + specific flags; wired automatically into `producer master`.
- **Prompt:**
  ```
  Add an automated QA gate.

  Create:
  - `producer/qa.py` with `run_qa(path: str) -> dict` using `pyloudnorm` for
    integrated LUFS and numpy/soundfile for clipping and phase checks. Return
    `{"lufs": float, "clipping_detected": bool, "mono_compatible": bool,
    "pass": bool, "flags": [str, ...]}`.
    Rules: clipping_detected = any sample at or above 0.999 full-scale;
    mono_compatible = L/R correlation above a threshold (e.g. > -0.5) when
    stereo, else True; pass = not clipping_detected AND -16 <= lufs <= -9
    (a reasonable streaming-adjacent range) AND mono_compatible. flags lists
    which specific checks failed, in plain words.
  - Add `pyloudnorm` to `pyproject.toml`.
  - `producer qa --file PATH` CLI command, prints the dict as JSON.
  - Wire QA into `producer master`: after mastering, automatically run
    `run_qa` on the output and print pass/fail + flags.
  - `tests/test_qa.py`: one fixture that deliberately clips (samples at 1.0) —
    assert `clipping_detected` True and `pass` False. One clean, normalized
    fixture at a reasonable LUFS — assert `pass` True.

  Run `pytest`, confirm green.
  ```
- **Acceptance criteria:**
  - [ ] `producer qa` on a real mastered file returns accurate pass/fail with specific flags
  - [x] `producer master` auto-prints QA results after mastering
  - [x] `pytest` passes: clipped fixture fails QA, clean fixture passes
- **Verify:** `pytest -q && python -m producer qa --file /tmp/out.wav`

### [x] M4 — Batch friend-test harness
- **Deliverable:** `producer batch --input-dir DIR --reference PATH --out-dir DIR` runs the full pipeline across every file in a folder and writes a `report.md`.
- **Prompt:**
  ```
  Add batch processing so friends' vocal files can be tested in one run.

  Create:
  - `producer/batch.py` with `run_batch(input_dir: str, reference: str,
    out_dir: str) -> list[dict]`: for every .wav/.mp3 in `input_dir`, run
    analyze → mix (premixed) → master (against the shared reference) → qa;
    write mastered output to `out_dir/<name>_mastered.wav`; collect
    `{"filename", "analysis", "qa"}` per file.
  - Generate `out_dir/report.md`: a markdown table, one row per file —
    `File | Tempo (BPM) | LUFS | Clipping | Mono-OK | QA Pass` — sorted by
    filename, with a summary line at the top: `{n}/{total} passed automated QA`.
  - `producer batch --input-dir DIR --reference PATH --out-dir DIR` CLI command.
  - `tests/test_batch.py`: run batch over a `tests/fixtures/batch/` folder with
    2–3 synthetic fixtures; assert `report.md` exists, has one row per input
    file, and the summary counts match the actual pass/fail results.

  Run `pytest`, confirm green.
  ```
- **Acceptance criteria:**
  - [ ] Running batch against a real folder of 3–5 friends' vocal files produces mastered outputs + a readable `report.md`
  - [x] Automated QA pass rate is visible at a glance
  - [x] `pytest` passes
- **Verify:** `pytest -q && python -m producer batch --input-dir tests/fixtures/batch --reference tests/fixtures/reference.wav --out-dir /tmp/batch_out && test -f /tmp/batch_out/report.md`

### [x] M5 — Ship (internal — not a public launch)
- **Deliverable:** README with a clean-clone quickstart, a before/after comparison image as visual proof, license. No UI, no distribution post — this is a working tool for you and your friends to run.
- **Prompt:**
  ```
  Finalize for internal use only.

  - Write `README.md`: what this is in two sentences, install (`pip install -e .`
    or `uv sync`), quickstart (the exact `producer master ...` command from a
    fresh clone using the committed test fixtures), and a short section on
    each command (`analyze`, `mix`, `master`, `qa`, `batch`).
  - Add `producer/report_plot.py`: `plot_comparison(before_path, after_path,
    out_png)` using matplotlib — stacked waveform + loudness-over-time for
    before vs. after, saved as PNG. Wire a `--plot` flag onto `producer master`.
  - Embed one example PNG (generated from the committed sine-wave fixtures) in
    the README as proof it works.
  - Add `LICENSE` (MIT by default unless you have a reason to pick otherwise).
  - Confirm the quickstart works from a clean clone: fresh venv, install, run
    the exact README quickstart command, nothing else.

  This is explicitly not a public ship — no UI, no distribution post. It's a
  working tool ready to run against the friends' vocal files you already have.
  ```
- **Acceptance criteria:**
  - [x] Quickstart works from a clean clone with no undocumented steps
  - [x] README embeds a real before/after comparison image
  - [ ] `producer batch` has been run at least once against real friend-supplied vocals — `report.md` is the artifact that answers "how good are we"
- **Verify:** `rm -rf /tmp/clean_clone && git clone . /tmp/clean_clone && cd /tmp/clean_clone && pip install -e . && producer master --vocal tests/fixtures/target.wav --reference tests/fixtures/reference.wav --out /tmp/clean_clone/out.wav --plot`

### [x] M6 — Benchmark against commercial services

- **Deliverable:** `producer benchmark` diffs our master against LANDR and others; `producer blindtest` builds a loudness-matched, anonymized listening test; `producer tally` aggregates the returned scoresheets.
- **Why this came after M5:** M5 shipped the tool; this milestone is what makes the success metric answerable. Automated QA says "legal", not "good" — only a controlled listening test says good.
- **Acceptance criteria:**
  - [x] Objective metrics beyond the QA gate: LRA, true peak (oversampled), crest factor, spectral balance, centroid
  - [x] Deltas expressed as actions tied to a chain stage, not just numbers
  - [x] Blind test is loudness-matched, attenuating only, and never clips
  - [x] Un-blinding key is written outside the folder shared with listeners
  - [x] `pytest` passes
  - [ ] Run against a real producer master vs a real LANDR master of the same song
  - [ ] At least 3 listeners returned scoresheets
- **Verify:** `pytest -q && producer benchmark --versions-dir <dir> && producer blindtest --versions-dir <dir> --out-dir <dir>`

### [x] M7 — Intake gate and comparison workspace

- **Deliverable:** `producer intake` validates raw vocals and scaffolds a per-track comparison workspace; `producer render` fills in our own version.
- **Why:** Friends send phone recordings. Discovering that a take was already clipped *after* paying for LANDR credits and booking an evening of listening is the expensive failure mode. This is the cheap gate in front of it.
- **Acceptance criteria:**
  - [x] Accepts `.m4a` (iPhone Voice Memos) by transcoding through `afconvert`
  - [x] Clipped, silent and sub-2s takes are blocked and get no workspace folder
  - [x] Every service receives a byte-identical `original.wav`
  - [x] Noise floor reports "unmeasurable" on gapless takes rather than a false positive
  - [x] `INTAKE_REPORT.md` and `MANIFEST.md` generated
  - [x] `producer render` fills `producer.wav` for every scaffolded track
  - [x] References resolve per track, with a workspace-wide fallback
  - [x] `pytest` passes
  - [ ] Run against real friend-supplied vocals
- **Verify:** `pytest -q && producer intake --input <raw> --workspace <ws> && producer render --workspace <ws> --reference <ref>`

## Decision log
<!-- Append-only. Format: {date} — {decision} — {why} -->
2026-10-02 — Built M0–M5 in one pass; each milestone verified with its own `Verify` command before commit. — The plan's milestones were already sequenced and independently checkable, so there was nothing to re-plan.
2026-10-02 — Used `HighpassFilter(cutoff_frequency_hz=...)` instead of the plan's `cutoff_hz`. — The plan's prompt had the wrong keyword; `cutoff_hz` raises TypeError on pedalboard 0.9.25.
2026-10-02 — Set `Reverb(dry_level=0.92)` rather than leaving the 0.4 default. — The default drops the dry signal ~8 dB, so the "subtle space" stage was silently acting as a large volume cut.
2026-10-02 — Kept the plan's 2-second fixtures after checking matchering empirically. — Current matchering exposes no `min_length`, only `max_length=900`; 2s files process fine, so no need to inflate committed fixture size.
2026-10-02 — Committed the generated fixtures instead of generating them only at test time. — M5 requires the README quickstart to work from a clean clone, which needs the WAVs present before pytest has ever run.
2026-10-02 — `report.md` carries an empty subjective-verdict table per file. — The plan's success metric is automated QA *plus* a human "release-ready" call; the report is where that belongs.
2026-10-02 — Deferred all real-vocal acceptance criteria. — No friend-supplied audio available in this environment; synthetic fixtures cannot answer "does this sound release-ready".

2026-10-02 — Added M6 (benchmark + blind listening test) beyond the original six milestones. — The plan's success metric needs a comparison baseline and friend feedback; both need tooling that didn't exist.
2026-10-02 — Blind tests gain-match downward to the quietest version, never upward. — Boosting risks clipping and alters character; attenuation is transparent, so the comparison stays honest.
2026-10-02 — The un-blinding key is written outside `--out-dir`. — If the key ships alongside the audio, one careless folder share destroys the blind.
2026-10-02 — Band deltas below -45 dB relative to total are reported `n/a`. — Comparing two inaudible bands produced confident nonsense ("+49 dB in low_mid") from what was really clipping harmonics vs silence.
2026-10-02 — True peak is measured at 4x oversampling, not sample peak. — Our own demo master read -0.0 dBFS by sample peak but +0.13 dBTP true peak, which distorts after lossy encoding.
2026-10-02 — Added M7: an intake gate in front of the comparison. — Friends send phone audio; catching a clipped take before LANDR credits are spent is the whole point.
2026-10-02 — `.m4a` is transcoded via macOS `afconvert` rather than adding an ffmpeg dependency. — libsndfile cannot read m4a, iPhone Voice Memos are m4a by default, and afconvert already ships on the target machine.
2026-10-02 — Blocked files are deliberately not written to the workspace. — A folder with no `original.wav` cannot be accidentally uploaded or rendered, so the block is structural rather than advisory.
2026-10-02 — Noise floor returns None on takes with no silent passages. — The 10th-percentile estimator was reading the quiet part of a continuously-sung note as room tone and flagging every clean file; a gate that cries wolf gets ignored.
2026-10-02 — All services are fed one standardized 24-bit `original.wav`. — Comparing services that received different input files measures the input, not the service.
2026-10-02 — References resolve per track (`<slug>/reference.*`) with `--reference` as fallback. — Vocals span genres; one tonal target for a ballad and a rap hook masters at least one of them wrong.
2026-10-02 — `reference` is a reserved stem, excluded from version discovery. — It lives in the song folder but is a different song; left discoverable it would be benchmarked as a master and, worse, land in the blind test for friends to score.
2026-10-02 — `render` resolves every reference before rendering any track. — A mid-run failure leaves a workspace where some `producer.wav` files are current and others are stale, which silently corrupts the next benchmark.
2026-10-03 — `producer tune` fits the chain to a measured target, and says so loudly. — "Train the producer" has an honest reading (close the measured gap to a master you trust) and a dishonest one (have software judge what sounds good); only the first is buildable, so every output repeats that a smaller distance is a lead, not a verdict.
2026-10-03 — The tuning distance excludes loudness. — Matchering sets loudness from the reference, so scoring it would optimise for the reference choice rather than the chain.
2026-10-03 — Chain parameters moved into `ChainParams` with the shipped values as defaults. — Tuning needs a search space, and a test pins the defaults so the refactor cannot silently change what everyone has been listening to.
2026-10-03 — Added a reference library built from real releases, deriving QA thresholds and reference choice from it. — The shipped -16..-9 LUFS window and the by-hand reference pick were the two places the pipeline ran on my guesses; a corpus of finished records replaces both with measurement.
2026-10-03 — Derived thresholds need at least 8 references and report what fraction of the corpus they admit. — A window derived from three tracks encodes their quirks, and one that fails most of its own corpus is broken; both failures are now visible rather than silent.
2026-10-03 — The library stores measurements only, never audio. — Reference tracks are commercial records; the catalogue is derived data and the audio stays wherever the user keeps it.
2026-10-03 — Tracks are classified full-mix vs vocal-only from low-end energy. — Mastering a bare vocal toward a full-mix reference asks matchering to invent bass that was never recorded, which looks like a chain fault and is not one.
2026-10-03 — Tempo matching folds half and double time. — 70 and 140 BPM are the same groove, and librosa reports either; without folding, the right reference ranks as the most distant.
2026-10-03 — Added `producer standards`: published platform targets, and what each does to a master. — Loudness normalisation means a master louder than the target is turned down on playback and the limiting that bought it is discarded; stating that for a specific file is the most actionable thing the pipeline can say.
2026-10-03 — Standards are data with `source` and `as_of`, replaceable from JSON. — Platform targets drift and my figures have a knowledge cutoff; the mechanism stays correct when a number goes stale, and the provenance travels with every profile.
2026-10-03 — Platform behaviour is modelled, not just the target number. — YouTube only attenuates (a quiet master stays quiet), Spotify also raises (which can push true peak over the ceiling), and Amazon's peak limit is stricter; the number alone would miss all three.
2026-10-03 — Conformance consequences are summarised once per master, not once per platform. — The first version printed the same sentence eight times, which buried the finding it was making.
