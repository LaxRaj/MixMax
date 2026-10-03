# Loudness shootout

> No reference supplied, so loudness was set but tone was left alone.
> Supply one to enable reference matching.

Source: `original.wav` — detected as **full-mix**, vocal chain skipped (it would thin the low end of a full track).

| Version | Target | LUFS | True peak | Crest | LRA |
| --- | --- | --- | --- | --- | --- |
| as_is | — | -22.7 | -1.31 | 21.6 | 3.5 |
| loud_8 | -8 | -10.4 | -1.00 | 5.9 | 1.0 |
| spotify | -14 | -14.1 | -1.00 | 11.1 | 1.2 |
| apple_music | -16 | -16.1 | -1.00 | 14.2 | 1.9 |

## What the listening test settles

These versions are the same master at different loudnesses, so the blind
test gain-matches them all to the quietest. Once level is equalised the
only thing left is the limiting each target required — which is precisely
the thing streaming normalisation makes invisible in the numbers.

If listeners cannot tell them apart, loudness was never worth chasing.
If the quieter renders win, chasing it was actively costing you.

Limiting engaged for: loud_8, spotify, apple_music. The rest reached their target on gain alone and are untouched.
