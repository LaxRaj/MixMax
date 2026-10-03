# Loudness shootout

> No reference supplied, so loudness was set but tone was left alone.
> Supply one to enable reference matching.

Source: `original.wav` — detected as **full-mix**, mastering chain applied (subsonic cleanup + gentle glue).

> **Mono source.** There is no stereo image to work with, and widening a mono file means inventing the difference signal — which buys width by damaging mono fold-down. Left alone.


| Version | Target | LUFS | True peak | Crest | LRA |
| --- | --- | --- | --- | --- | --- |
| as_is | — | -23.7 | -4.84 | 18.9 | 4.5 |
| spotify | -14 | -14.1 | -1.00 | 12.1 | 1.9 |
| apple_music | -16 | -16.1 | -1.00 | 14.6 | 3.1 |
| loud_8 | -8 | -8.6 | -1.00 | 5.6 | 1.1 |

## What the listening test settles

These versions are the same master at different loudnesses, so the blind
test gain-matches them all to the quietest. Once level is equalised the
only thing left is the limiting each target required — which is precisely
the thing streaming normalisation makes invisible in the numbers.

If listeners cannot tell them apart, loudness was never worth chasing.
If the quieter renders win, chasing it was actively costing you.

Limiting engaged for: spotify, apple_music, loud_8. The rest reached their target on gain alone and are untouched.
