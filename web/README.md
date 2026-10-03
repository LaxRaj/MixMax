# Listening test web app

The friend-facing half of the `producer` pipeline: a blind, loudness-matched
A/B listening test. See `../FRONTEND_PLAN.md` for scope and milestones.

## Run it

```bash
npm install
npm run dev
```

It reads `public/test.json` and the audio in `public/audio/`. A working demo
fixture is committed, so a fresh clone plays immediately.

## Why it's built this way

**Every version plays at once.** All versions are decoded up front and started
together through their own `GainNode`; switching only moves gain. Stopping and
restarting a source would cost a gap and lose the playback position, and
comparing the *same moment* is the entire point — otherwise a listener is
judging their memory of version A, not version A.

**The page never learns which version is which.** It only ever sees `A`, `B`,
`C`. The label-to-source key stays on the operator's machine and `producer
tally` does the un-blinding, so even a full compromise of this app can't reveal
which master was LANDR. `lib/manifest.ts` asserts no source name is present.

**No spectrum analyser, deliberately.** A frequency display would let someone
rank by looking at which version is brighter rather than listening, which would
quietly void the test. Nothing on screen may differ by version.

**Reordering uses arrows, not drag-and-drop.** The HTML5 drag API doesn't fire
on touch, and phones are the primary target.

## Output

Submitting downloads a CSV in exactly the schema `producer tally --responses`
already reads, so it drops straight into the CLI with no conversion.
