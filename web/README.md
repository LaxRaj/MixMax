# MixMax web app

Two things live here, and they have opposite audiences.

- **The studio** — `/`, `/songs/<slug>`, `/upload`, `/dashboard`. For you and a
  friend, behind a passcode. Open a song, play each piece of it, leave notes,
  change how it is rendered, send in files.
- **The blind listening test** — `/listen`. An open link for anyone, showing
  nothing but `A`, `B`, `C`.

See `../FRONTEND_PLAN.md` for scope, milestones and the decision log.

## Run it

```bash
npm install
npm run dev                      # the app, on http://localhost:3000
cd .. && producer sync --watch   # the worker: checks uploads, renders, publishes
```

With no Vercel credentials both use the folder `web/.data/` as their store, so
the whole loop — upload, check, note, re-render — works on one machine.
Nothing appears on the songs screen until `producer sync` has run once.

To let a friend on the same Wi-Fi in without hosting anything, set a passcode
and share your LAN address:

```bash
MIXMAX_PASSCODE=something npm run dev -- -H 0.0.0.0
```

## How the studio works

**The web app never processes audio.** It writes three kinds of small object to
the store — a note, an upload, a settings request — and reads back what
`producer sync` published. One object per event, never an append to a shared
file, so two people saving at once cannot overwrite each other.

| Store path | Written by | Holds |
| --- | --- | --- |
| `songs/index.json`, `songs/<slug>/song.json` | sync | stages, components, measurements, settings |
| `songs/<slug>/audio/<component>.<hash>.m4a` | sync | AAC 256k of each component |
| `feedback/<slug>/<id>.json` | web | one note, rewritten under the same id as it is edited |
| `uploads/<id>/file.<ext>` + `meta.json` | web | an uploaded file and who sent it, for what |
| `uploads/<id>/report.json` | sync | the intake verdict, blockers and warnings |
| `requests/<slug>/<id>.json` + `.result.json` | web / sync | a settings change and its outcome |
| `sync/heartbeat.json` | sync | when the studio Mac last checked in |

**Notes save themselves.** No submit button: a moment after typing stops, and
again on blur. They end up in `comparisons/<slug>/feedback.md` in the repo.

**"Not recorded" means what it says.** A setting nobody has chosen through the
studio shows its default and says so. The first re-render will use it.

**The UI says when the Mac is away.** Renders and file checks only happen while
`producer sync --watch` is running; the nav shows when it was last seen.

**Access is one shared passcode** (`MIXMAX_PASSCODE`) and a name typed once per
browser. Deployed without a passcode, the studio refuses to serve at all.
`/listen` and the audio it plays stay open.

## Hosting it

It is a Vercel project, `mixmax-studio`, with a private Blob store of the same
name. `web/.env.local` (written by `vercel env pull`, not committed) holds the
blob token and the passcode; `producer sync` and `npm run dev` both use the
hosted store whenever that file has `BLOB_READ_WRITE_TOKEN` in it.

```bash
vercel                 # preview deploy — behind Vercel login, so only you can open it
vercel --prod          # production: https://mixmax-studio.vercel.app, behind the passcode
vercel env pull .env.local   # after changing an env var
```

A friend needs the **production** URL: preview deployments sit behind Vercel's
own login, which they do not have. Keep `producer sync --watch` running on the
Mac while people use it.

To go back to a purely local store for a session:

```bash
MIXMAX_DATA_DIR=.data npm run dev
producer sync --store web/.data
```

To change the passcode: `vercel env rm MIXMAX_PASSCODE`, `vercel env add
MIXMAX_PASSCODE` for Production and Preview, then redeploy. Every existing
session ends, because the cookie is derived from it.

## Tests

```bash
npm run e2e          # desktop + phone viewport
```

The suite starts its own server against its own store (`e2e/.data/`) and
passcode, so it never touches real songs.

## The blind test (`/listen`)

**Every version plays at once.** All versions are decoded up front and started
together through their own `GainNode`; switching only moves gain. Stopping and
restarting a source would cost a gap and lose the playback position, and
comparing the *same moment* is the entire point.

**The page never learns which version is which.** It only ever sees `A`, `B`,
`C`. The label-to-source key stays on the operator's machine and `producer
tally` does the un-blinding. `lib/manifest.ts` asserts no source name is present.

**No spectrum analyser, deliberately.** Nothing on screen may differ by version,
or someone could rank by looking instead of listening.

**Reordering uses arrows, not drag-and-drop.** The HTML5 drag API doesn't fire
on touch, and phones are the primary target.

Submitting downloads a CSV in exactly the schema `producer tally --responses`
already reads.
