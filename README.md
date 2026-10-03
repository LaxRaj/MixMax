# producer

A CLI-only pipeline that analyzes, mixes, masters and QA-gates a raw vocal. It
exists to answer one question before anything else gets built on top of it: **is
automated processing good enough to sound release-ready?**

![before / after](docs/example_before_after.png)

*Generated from the committed test fixtures with `producer master --premix --plot`.*

## Install

Requires Python 3.11.

```bash
git clone https://github.com/LaxRaj/MixMax.git
cd MixMax
uv venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[dev]"
```

Using pip instead of uv:

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quickstart

From a fresh clone, this runs end-to-end on the committed fixtures:

```bash
producer master \
  --vocal tests/fixtures/target.wav \
  --reference tests/fixtures/reference.wav \
  --out out.wav \
  --plot
```

You get `out.wav`, a QA verdict printed to the terminal, and
`out.comparison.png`.

## Commands

### `producer intake --input PATH --workspace DIR [--service NAME ...]`

**Start here.** Point it at a raw vocal or a folder of them (searched
recursively) and it validates every file, then scaffolds a comparison
workspace.

```bash
producer intake --input ~/vocals/friends --workspace comparisons --service landr
```

Accepts WAV, MP3, FLAC, AIFF, OGG, CAF — and `.m4a`/`.mp4`/`.aac`, which
libsndfile can't read, via macOS's built-in `afconvert`. iPhone Voice Memos
are `.m4a`, so this matters.

Each file is judged **ready**, **caution** or **blocked**:

| Check | Why it matters |
| --- | --- |
| clipped regions | distortion is baked in; no master removes it — **blocks** |
| silent / under 2s | nothing downstream can run — **blocks** |
| peak level | very quiet takes need makeup gain that lifts the noise with it |
| noise floor & SNR | compression brings room tone forward |
| sample rate | below 44.1 kHz gets resampled up, which adds no detail back |
| DC offset | wastes headroom |
| leading/trailing silence | drags the loudness measurement down |
| dual-mono | stereo file that's really mono |

Noise floor is only reported when the take actually has gaps. On continuous
singing the quietest frames are still *signal*, so the honest answer is
"unmeasurable" rather than a fabricated warning.

Files that pass are written to `<workspace>/<slug>/original.wav` as 24-bit WAV.
**That single file is what every service receives** — feeding one service a
different file invalidates the comparison. Blocked files get no folder at all.

You also get `INTAKE_REPORT.md` (what's wrong with what) and `MANIFEST.md` (the
upload checklist, since LANDR has no API to drive).

### `producer render --workspace DIR [--reference PATH] [--no-premix] [--dry-run]`

Fills in `producer.wav` for every track in the workspace by running our own
pipeline — mix chain, then reference-based mastering — and prints the QA
verdict per track. Re-run it after any change to the chain.

**References are per track.** Mastering matches a reference's tone and
loudness, so tracks in different genres need different targets. Either:

- drop a `reference.wav` (or `.mp3`, or a **symlink**) inside a track's folder
  to give that track its own, or
- pass `--reference` as the fallback for every track without one.

```bash
# per-track where it matters, one fallback for the rest
cp soul_ref.wav       comparisons/ballad/reference.wav
ln -s ~/refs/trap.wav comparisons/rap-hook/reference.wav
producer render --workspace comparisons --reference pop_ref.wav
```

```
  PASS  ballad -> comparisons/ballad/producer.wav
          reference: reference.wav (per-track)
  PASS  rap-hook -> comparisons/rap-hook/producer.wav
          reference: trap.wav (per-track)
  FAIL  indie-bridge -> comparisons/indie-bridge/producer.wav
          reference: pop_ref.wav (fallback)
          loudness: -7.9 LUFS is louder than -9
```

`--dry-run` prints which reference each track *would* use and stops, which is
worth doing before a long render.

`reference.*` is a **reserved name**: it's the tonal target, not a version of
the song, so `benchmark` and `blindtest` both ignore it. Without that, friends
would end up blind-scoring a completely different song.

If any track has neither its own reference nor a fallback, the whole run
refuses up front rather than rendering half the workspace.

### `producer analyze --vocal PATH [--out PATH]`

Tempo, voiced pitch range and dynamic range, as JSON. Every value is guaranteed
finite, so downstream reports can rely on it.

```json
{ "tempo_bpm": 120.19, "pitch_min_hz": 0.0, "pitch_max_hz": 0.0, "dynamic_range_db": 43.94 }
```

### `producer mix --vocal PATH --out PATH`

Runs the vocal through the mix chain, in signal order:

| Stage | Purpose |
| --- | --- |
| `HighpassFilter` 80 Hz | rumble, mic handling, HVAC |
| `NoiseGate` −40 dB | room noise between phrases |
| `Compressor` −18 dB, 3:1 | even out performance dynamics |
| `PeakFilter` 7 kHz, −4 dB | de-ess the sibilance band |
| `Reverb` room 0.15, wet 0.08 | subtle space |

These are conservative starting values meant to be tuned against real listening
feedback, not learned parameters.

### `producer master --vocal PATH --reference PATH --out PATH [--premix] [--plot]`

Matches the vocal to the tonal and loudness profile of a reference track via
[`matchering`](https://github.com/sergree/matchering), then runs the QA gate on
the result automatically.

- `--premix` runs the mix chain first and masters *that* instead of the raw vocal.
- `--plot` also writes a before/after comparison PNG next to the output.

### `producer qa --file PATH`

The automated gate. Reports integrated LUFS, full-scale clipping and mono
fold-down compatibility, and names each failed check in plain words.

```json
{
  "lufs": -11.9,
  "clipping_detected": false,
  "mono_compatible": true,
  "pass": true,
  "flags": []
}
```

A file passes when it does not clip, sits between −16 and −9 LUFS, and survives
a mono fold-down. Thresholds live at the top of `producer/qa.py`.

### `producer batch --input-dir DIR --reference PATH --out-dir DIR`

The friend-test harness. Runs analyze → mix → master → QA over every `.wav` and
`.mp3` in a folder, writes `<name>_mastered.wav` per file, and generates
`report.md`: a pass-rate summary, a per-file metrics table, the specific flags,
and an empty table for your own subjective "does this sound release-ready"
verdict per file.

```bash
producer batch \
  --input-dir ~/vocals/friends \
  --reference tests/fixtures/reference.wav \
  --out-dir ./out
```

A failure on one file is recorded as a flag rather than sinking the whole batch.

### `producer benchmark --versions-dir DIR [--baseline NAME] [--out PATH]`

Measure our master against commercial ones. Put the same song rendered by each
service in one folder, named by service:

```
comparisons/my_song/
  original.wav     <- written by `intake`, the shared input
  producer.wav     <- written by `render`
  landr.wav        <- you download and drop in
```

```bash
producer benchmark --versions-dir comparisons/my_song --out comparisons/my_song/benchmark.md
```

Reports integrated LUFS, loudness range, **true peak** (4x oversampled, so
inter-sample overs show up), crest factor, spectral centroid, stereo
correlation, and a six-band spectral balance — then diffs everything against
`producer` and says which chain stage to reach for.

Bands where neither version has audible content are reported as `n/a` rather
than generating confident advice about silence.

### `producer blindtest --versions-dir DIR --out-dir DIR [--seed N] [--target-lufs X]`

Builds the listening test to send friends. This exists because two biases
otherwise make informal feedback worthless:

- **Loudness bias** — the louder master wins every time. Our masters run hot
  (~−7 LUFS); LANDR targets ~−14. Unmatched, you'd be measuring gain staging.
- **Brand bias** — "this one is LANDR" scores higher regardless of sound.

So every version is gain-matched to a common LUFS target (matching *downward*
only, so nothing clips), renamed `A.wav`/`B.wav`/…, and shuffled. The folder you
share contains only the audio, instructions and a blank scoresheet. **The
un-blinding key is written outside that folder** — share `--out-dir`, keep the
key.

```bash
producer blindtest --versions-dir comparisons/my_song --out-dir blind/my_song --seed 42
```

### `producer tally --key PATH --responses PATH [--out PATH]`

Un-blinds the returned scoresheets and aggregates them by source — mean
release-ready score, mean rank, first-place votes, and every free-text note.
Accepts one CSV or a folder of them.

```bash
producer tally --key blind/my_song.key.json --responses responses/ --out results.md
```

With fewer than three listeners the report labels itself an anecdote rather
than a verdict.

### `producer deliver` — master into platform normalisation

Streaming normalisation is usually treated as something done *to* a master. It
can be aimed at instead.

A master sitting on Spotify's −14 LUFS target with −1 dBTP has no headroom, so
the platform cannot lift it and it plays at −14. A **quieter, more dynamic**
master at −16 LUFS with **−3 dBTP** leaves exactly the 2 dB Spotify wants to
close — so it arrives at −14 LUFS with the dynamics of a −16 master.

```bash
producer deliver --source mix.wav --out MASTER.wav --lufs -16 --standard spotify
```

```
Mastered to -16.2 LUFS @ -3.00 dBTP  (limited)
Spotify lifts it +2.0 dB -> delivered -14.2 LUFS @ -1.00 dBTP
Lands on Spotify's target while keeping the dynamics of a -16 LUFS master.
```

This only works where a platform raises quiet tracks. Apple Sound Check and
YouTube only turn things down, so headroom buys nothing there and the normal
ceiling applies.

### `producer arrange` — add a bridge and extend to a full song

Acts on what `structure` found. Cuts the source **on bar lines** and reassembles
it: repeating the hook, filtering a section down into a breakdown, sweeping a
riser into the return.

```bash
producer arrange --source mix.wav --out extended.wav --minutes 2.8
```

```
148.0 BPM · bar 1.621s · edits snapped to bar lines

role                                   from       to     len
original body                          0.19   110.44  110.3s
bridge (low end removed)              50.45    69.91   19.5s
riser into the drop                   69.91    73.15    3.2s
drop — B returns full                 32.62    50.45   17.8s
B again                               32.62    50.45   17.8s
outro                                110.44   117.52    7.1s

1.96 min -> 2.93 min
```

**It composes nothing.** Every sample out is a sample in — moved, filtered or
faded. A breakdown built from the track's own material is a real technique, but
it is not the same as writing a new part.

Edits snap to the bar grid, because a cut landing off the downbeat is what makes
a rearrangement sound wrong. Joins use a 12 ms equal-power crossfade, and
`--dry-run` prints the plan without rendering.

### `producer structure` — break the arrangement down

Turning a loop into a song is an arrangement problem, not a processing one — no
amount of mastering adds a bridge. This segments a track, groups sections that
sound alike, and says where the arrangement is thin.

```bash
producer structure --source mix.wav --out arrangement.md
```

```
 # sec   start    len   energy  onsets/s      low
 1 A    0:00        7s   -33.6       3.6   -10.0
 2 B    0:07       13s   -22.3       6.0   -13.5
 3 C    0:20       12s   -25.4       6.6   -11.7
 ...
repetition: Ax1 Bx4 Cx2 Dx2 Ex1
transitions:
  ▲ lift  at 0:07  A -> B  +11.2 dB
```

It checks specifically for the thing people mean by a **drop** — the low end
being pulled out and brought back, not just an energy dip — and names where to
put one. Flatness is judged on the body, since an intro and outro are supposed
to be quiet.

It reports tempo and key as search parameters for finding compatible beats, but
**nothing here queries a streaming catalogue**. To rank candidates, add them
with `producer library add` and use `producer library match`.

### `producer shootout` — the loudness experiment

Renders one track at several published targets so a blind test can answer the
question the numbers cannot: **once a platform takes the loudness away, does
chasing it leave the master better or worse?**

```bash
producer shootout \
  --source track_with_beat.wav \
  --reference references/soul.wav \
  --out-dir shootout/ \
  --target loud:-8 --target spotify --target ebu_r128 \
  --competitor landr.wav
```

```
Detected full-mix; vocal chain skipped — it would thin a full track's low end.

version             target    LUFS     peak   crest
as_is                    —   -13.6    -0.39    13.1
loud_8                  -8    -8.0    -1.00     4.2
spotify                -14   -14.2    -1.00    11.0
ebu_r128               -23   -23.0    -9.75    13.1

Limiting engaged for: loud_8, spotify. The rest reached their target on gain alone.
```

That table is the finding: `loud_8` gave up **9 dB of crest factor** to gain
5.6 dB of loudness every platform discards. Feed the folder to
`producer blindtest` and the versions are gain-matched, so the only thing left
to hear is the limiting.

Three traps it closes for you:

- **Full tracks skip the vocal chain.** An 80 Hz highpass and a de-ess are
  right for a lone voice and wrong for anything with a kick in it. Detected
  from sub-bass energy, overridable with `--kind`.
- **It refuses to ship a vacuous test.** If no version needed limiting they are
  one master at different levels, and gain-matching makes them the same file —
  so it says so rather than asking listeners to tell identical audio apart.
- **It names versions that are identical after matching**, so you do not waste
  a listening slot on a duplicate.

Loudness targeting is exact: plain gain first (transparent), limiting only when
a target cannot be reached without it, and true peak held at the ceiling
because that is what stops lossy encoders distorting.

### `producer standards` — check against published targets

Every streaming platform normalises playback loudness, which makes most
loudness-chasing pointless. This says exactly what each one does to a file.

```bash
producer standards list                       # the targets, and where each came from
producer standards check --file out.wav       # what every platform does to this master
producer standards profile --standard spotify --out qa_profile.json
producer qa --file out.wav --standard spotify
```

```
out.wav: -11.0 LUFS, true peak -6.71 dBTP, crest 6.1 dB

platform                      target     gain  delivered  peak after
Spotify                         -14    -3.0     -14.0      -9.75   ok
Apple Music (Sound Check)       -16    -5.0     -16.0     -11.75   ok
EBU R128 (broadcast)            -23   -12.0     -23.0     -18.75   ok

What that means:
  - Every target here attenuates this, by 3.0 to 12.0 dB. It plays back at -14.0
    to -23.0 LUFS whatever you do, so the limiting that reached -11.0 LUFS buys
    nothing on playback.
  - Crest factor is 6.1 dB. Mastering around -14 LUFS instead would give back
    roughly 3.0 dB of headroom at no cost in delivered loudness.
```

It models behaviour, not just numbers: YouTube only turns masters *down*, so a
quiet one stays quiet and sounds weak; Spotify turns them *up*, which can push
true peak over the ceiling. Amazon's peak ceiling is stricter than everyone
else's.

**Targets drift and these ship with a date.** Each entry carries `source` and
`as_of`, and `--standards table.json` replaces the whole table without a code
change. Verify against the current published spec before trusting one for a
release.

### `producer library` — learn from real releases

Two numbers in this pipeline were invented rather than measured: the QA
loudness window, and which reference to master against. A corpus of finished
records replaces both with fact.

```bash
producer library add --input ~/Music/refs/soul --genre soul
producer library add --input ~/Music/refs/trap --genre trap
producer library list
```

Each track is measured (LUFS, loudness range, true peak, crest, spectral
balance) and described musically — **tempo**, **key**, and whether it reads as
a full mix or a bare vocal. Only the measurements are stored; the audio stays
where it is and never enters the repo.

#### `producer library thresholds [--genre X] --out qa_profile.json`

Derives the QA window from what records in that corpus actually measure:

```
From 13 reference(s):
  LUFS window  -21.92 .. -12.64   (built-in was -16.0 .. -9.0)
  11/13 of the corpus sits inside that window (85%)
```

That gap is the point — the built-in window would have failed genuinely
finished records. Then `producer qa --file OUT.wav --profile qa_profile.json`.

It refuses fewer than 8 references, because a handful of tracks just encodes
their quirks. The corpus pass-rate is reported so a broken window is obvious:
if most of the records it learned from would fail it, the window is wrong.

#### `producer library match --vocal PATH [--genre X]`

Suggests the reference that asks least of the mastering stage, ranked on
tempo, brightness, spectral balance and dynamics. Half- and double-time are
folded together, so 70 and 140 BPM read as the same groove.

It warns when your source reads as a bare vocal but the references are full
mixes — mastering toward those asks matchering to invent low end that was
never recorded.

### `producer tune --vocal PATH --reference PATH --target PATH [--budget N]`

Fits the mix chain to a target master you already trust — LANDR's version of
the *same* vocal, or a commercial track — by searching the nine chain
parameters for the settings that minimise measured distance.

```bash
producer tune \
  --vocal comparisons/ballad/original.wav \
  --reference references/soul.wav \
  --target comparisons/ballad/landr.wav \
  --out chain_params.json --budget 60
```

```
Distance  13.924 -> 12.183 (+12.5%)
  * deess_gain_db           -8.61   (was -4.00)
  * reverb_wet               0.00   (was 0.08)
  ...
```

Then `producer mix --params chain_params.json`.

**This does not learn what sounds good — nothing here listens.** The distance
is a weighted sum of spectral-balance, crest-factor, loudness-range and
centroid differences. Loudness is deliberately excluded, since matchering sets
it from the reference and scoring it would just measure your reference choice.

A smaller distance is a lead worth testing, not proof of a better master. The
blind listening test is still the only thing that settles it.

## Tests

```bash
pytest -q              # 105 tests, pipeline
cd web && npm run e2e  # 16 tests, listening test app in a real browser
```

The Python suite runs entirely on synthetic fixtures generated at test-setup
time — no real audio required. The fixtures are also committed so the
quickstart above works from a clean clone.

The browser suite drives the real app in Chromium at both desktop and phone
viewports, asserting the guarantees that matter: the page hydrates, every
version decodes and plays at once, switching does not restart a source or lose
position, nothing in the DOM or network traffic reveals which version is which,
and the exported CSV matches the schema `producer tally` reads.

### `producer dashboard` — the evidence ledger

```bash
producer dashboard --workspace comparisons --library reference_library.json
cd web && npm run dev     # then open /dashboard
```

Collects every stage's state into `web/public/dashboard.json` and renders one
page answering: **what do we actually know, and what am I still guessing?**

Every tunable number is labelled by where it came from:

| | |
| --- | --- |
| **published** | a platform or standards body's own document |
| **measured** | derived from the reference corpus |
| **fitted** | optimised against a measured target by `producer tune` |
| **reported** | widely reported, not confirmed at a primary source |
| **guessed** | a default nobody has checked against anything |

```
Evidence: 58% of 19 numbers are grounded
  (published 5, measured 1, fitted 0, reported 5, guessed 8)
8 number(s) are still hand-picked defaults nobody has checked.
```

The page is read-only and computes nothing of its own — the CLI stays the
engine. It also states plainly where the evidence runs out, which is still at
the only question that matters.

## The testing loop

```bash
producer intake    --input ~/vocals/friends --workspace comparisons
#   read comparisons/INTAKE_REPORT.md; re-record anything blocked
#   upload each original.wav to LANDR, save the result as landr.wav
producer render    --workspace comparisons --reference my_reference.wav
producer benchmark --versions-dir comparisons/<slug> --out comparisons/<slug>/benchmark.md
producer blindtest --versions-dir comparisons/<slug> --out-dir blind/<slug> --seed 42
#   send blind/<slug> to friends, collect the filled scoresheets
producer tally     --key blind/<slug>.key.json --responses responses/ --out results.md
```

`original.wav` stays in the folder deliberately — it rides through the blind
test as a control. If listeners rank the unmastered take first, the problem is
the chain, not the recording.

## Scope

This is an internal tool, not a public release. No web UI, no instrumental
generation, no distribution — see `PLAN.md` for the full non-goals list and the
milestone history.

Known gap: no pitch correction in the vocal chain yet. That's deliberate — the
base chain gets validated against real listening feedback first.

## License

MIT — see [LICENSE](LICENSE).
