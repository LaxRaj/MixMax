# Producer CLI

> A CLI-only pipeline — analyze, mix, master, QA-gate a raw vocal — built to answer one question before anything else gets built: is automated processing good enough to sound release-ready?

- **Success metric:** `producer batch` runs against 3–5 real vocal files from friends and produces a `report.md` with automated QA results, plus your own subjective "does this sound release-ready" verdict per file.
- **Deadline:** ~1 week out (assumption — 6 milestones, each ≤1 day; adjust if wrong).
- **Status:** M0–M5 built, all 26 tests green from a clean clone. Outstanding: every acceptance criterion that requires **real friend-supplied vocals** is still unchecked — the pipeline has only been exercised against synthetic fixtures.
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

## Decision log
<!-- Append-only. Format: {date} — {decision} — {why} -->
2026-10-02 — Built M0–M5 in one pass; each milestone verified with its own `Verify` command before commit. — The plan's milestones were already sequenced and independently checkable, so there was nothing to re-plan.
2026-10-02 — Used `HighpassFilter(cutoff_frequency_hz=...)` instead of the plan's `cutoff_hz`. — The plan's prompt had the wrong keyword; `cutoff_hz` raises TypeError on pedalboard 0.9.25.
2026-10-02 — Set `Reverb(dry_level=0.92)` rather than leaving the 0.4 default. — The default drops the dry signal ~8 dB, so the "subtle space" stage was silently acting as a large volume cut.
2026-10-02 — Kept the plan's 2-second fixtures after checking matchering empirically. — Current matchering exposes no `min_length`, only `max_length=900`; 2s files process fine, so no need to inflate committed fixture size.
2026-10-02 — Committed the generated fixtures instead of generating them only at test time. — M5 requires the README quickstart to work from a clean clone, which needs the WAVs present before pytest has ever run.
2026-10-02 — `report.md` carries an empty subjective-verdict table per file. — The plan's success metric is automated QA *plus* a human "release-ready" call; the report is where that belongs.
2026-10-02 — Deferred all real-vocal acceptance criteria. — No friend-supplied audio available in this environment; synthetic fixtures cannot answer "does this sound release-ready".
