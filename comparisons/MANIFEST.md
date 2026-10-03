# Upload checklist

LANDR has no API we can drive, so this step is manual. For each track
below, upload `original.wav`, download the result, and save it under the
exact filename given — that name is how `producer benchmark` labels it.

Upload the **same** `original.wav` to every service. Feeding one service
a different file invalidates the comparison.

## iced-latte

Upload: `comparisons/iced-latte/original.wav`

- [ ] **landr** → save as `comparisons/iced-latte/landr.wav`
- [ ] **producer** → `producer render` writes `comparisons/iced-latte/producer.wav`
- [ ] _(optional)_ **reference** → drop one at `comparisons/iced-latte/reference.wav` to give this track its own tonal target

## sector-79-60

Upload: `comparisons/sector-79-60/original.wav`

- [ ] **landr** → save as `comparisons/sector-79-60/landr.wav`
- [ ] **producer** → `producer render` writes `comparisons/sector-79-60/producer.wav`
- [ ] _(optional)_ **reference** → drop one at `comparisons/sector-79-60/reference.wav` to give this track its own tonal target

## References

`producer render` matches each track to a reference track — a commercially
released song whose tone and loudness you want to land near. Either:

- drop a `reference.wav` (or `.mp3`, or a symlink) inside a track's folder
  to give that track its own, or
- pass `--reference` as the fallback for every track without one.

`reference.*` is a reserved name: it is the tonal target, not a version of
the song, so `producer benchmark` and `producer blindtest` both ignore it.

## Then

```bash
producer render    --workspace <workspace> --reference <fallback.wav>
producer benchmark --versions-dir <workspace>/<slug> --out <workspace>/<slug>/benchmark.md
producer blindtest --versions-dir <workspace>/<slug> --out-dir <workspace>/<slug>/blind
```

`original.wav` stays in the folder on purpose: it rides through the blind
test as a control. If listeners rank the unmastered take first, the
problem is the chain, not the recording.
