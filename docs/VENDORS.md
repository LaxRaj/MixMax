# Generation vendors

**Reviewed:** 2026-10-08 · **Type:** new-vendor comparison for Phase A (G2 Step 0)

Every claim below was read on 2026-10-08 from the page linked beside it. Where a
vendor's own page could not be read, the row says so rather than repeating a
reseller or a review. Terms and prices drift: re-read the linked page before a
release, and before relying on any number here for more than a test.

## Summary

No vendor checked here is confirmed to do the thing Phase A most needs: take
**this vocal** and write a backing that follows it.

- **Mureka** is the only one with an official API that claims it ("Generate
  track"), and its API agreement is the clearest on rights. But the request
  schema for that endpoint is not readable without an account, so it is
  **unverified** and no adapter was written against it.
- **ElevenLabs Music** is fully documented and its plan table is explicit about
  streaming rights, but it is **text-only**: it never hears the vocal.
- **Stability AI (Stable Audio 2.5)** accepts uploaded audio, but its
  audio-to-audio mode transforms the input; it does not accompany it.

**Recommendation:** proceed on two tracks. Use ElevenLabs now as the text-only
path (adapter built: `producer/generate/vendors/elevenlabs.py`), because it can
be run today and tests the weak form of the riskiest assumption: can a backing
written from "98 BPM, F# minor" be fitted under a real vocal. In parallel, open
a Mureka API account and read the `track/generate` schema; if it conditions on
the vocal as claimed, it becomes the primary and gets its own adapter. This is
a recommendation, not the decision: Step 0 is the owner's call.

## Comparison

| | Mureka | ElevenLabs Music | Stability AI — Stable Audio 2.5 |
|---|---|---|---|
| Official API | Yes — `https://api.mureka.ai`, bearer key [1] | Yes — `POST https://api.elevenlabs.io/v1/music` [4] | Yes — `POST /v2beta/audio/stable-audio-2/audio-to-audio` [7] |
| **1. Conditions on your uploaded vocal?** | **Claimed, unverified.** `POST /v1/track/generate`: "Generate a specific track type from the input song or audio" [2]. The request fields are not shown on the public page. File upload accepts `audio`, `melody` (5–60s, "uses the vocal extracted from the audio"), `instrumental`, `reference` (30s) [3]. | **No.** No field accepts uploaded audio; audio can only be referenced by the `song_id` of a track it generated itself [4]. | **Partly.** Takes an input audio file plus a prompt and `strength`; the input is the "structural and tonal foundation" and is transformed, not accompanied [7]. |
| **2. Tempo / key control** | Not documented on the pages read. | No parameters. Tempo goes in the prompt text (docs example: "130–150 bpm"); key is not addressed [5]. Section durations can be fixed with a composition plan [4]. | Not documented on the page read; follows the input audio at higher `strength` [7]. |
| **3. Stems or stereo only** | A `POST /v1/song/stem` endpoint exists [2]; inputs and outputs not read. | Stereo file returned. The plan table lists stems as a feature the Free plan lacks [6]; the stems endpoint was not read. | Stereo only on the pages read. |
| **4. Commercial use and ownership** | API agreement (Skywork AI Pte. Ltd., effective 2025-12-03) §3.2: the customer "retain[s] all ownership rights in Input" and "own[s] all Output"; Mureka assigns its rights in Output. §3.3: Customer Content is not used "to develop or improve the Services". §3.4: you warrant you hold the rights to Input. §3.5: Output may not be unique [8]. Training-data licensing: no statement found. | Model-specific terms (last updated 2026-05-26): self-serve plans permit "all online and offline commercial use … except film, TV, radio, & Studio Games". **Streaming** ("making Output(s) available on third party music streaming platforms") is prohibited on Free and Starter and allowed from **Creator** up. No reselling or building libraries on self-serve plans. Attribution required on Free only [6]. Marketed as trained on licensed music [9]. | Terms of service (effective 2026-09-30) §4(a): Stability assigns "all of our right, title, and interest (if any) in the Outputs"; you warrant rights in what you upload. §4(c): inputs and outputs may be used for training unless you opt out [10]. |
| **5. Price and limits** | Official pricing page could not be read. A search snippet of it showed about $0.03–0.045 per song by model; unverified. Upload limit 10 MB per file [3]. | Music API is "only available to paid users" [5]. Billed in generation minutes per plan: Starter 17, Creator 62, Pro 304, Scale 1,100, Business 4,800 per month; 2 concurrent jobs up to Pro, 5 above [6]. Per-minute overage price not read. Track length 3s–10min [4]. | Official pricing page could not be read. Third-party pages report 20 credits ($0.20) per generation; unverified. |
| **6. ToS stance on API use** | Explicitly a developer agreement: use in "your own websites, applications, products, or services" and making them available to end users is granted (§1.2); no reselling account access (§1.5); no using output to train models (§2(f)) [8]. | API access is a listed plan feature from Starter [6]. Prompts naming copyrighted material are rejected with `bad_prompt` [5]. | No reselling or redistributing the API (§3(b)(3)); no training on outputs (§3(b)(2)); no removing watermarks or content credentials (§3(b)(10)) [10]. |

## Disqualified

| Vendor | Why |
|---|---|
| **Suno** | No official public API as of this review: no public keys, documentation or pricing. Every "Suno API" found (sunoapi.org, kie.ai, ttapi.io and others) is a third-party reseller, some built on the web app's private API [11]. One of them documents exactly the feature wanted ("add instrumental" to an uploaded vocal), which is why this is worth re-checking: if Suno opens its API, it goes to the top of the list. |
| **Udio** | No official API found. |
| **ACE-Step 1.5 (open weights, via a host)** | The project documents a vocal-to-accompaniment feature, but the one hosted API found is text-only, and sources disagree on whether the licence is MIT or Apache-2.0 [12]. Self-hosting is outside the roadmap ("rent generation"). Worth revisiting if a host exposes the accompaniment task. |
| **MusicGen-melody (via a host)** | Conditions on a melody, which is close to what is needed, but the released weights are non-commercial. Not re-verified today; excluded on that basis until someone reads the current licence. |

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Text-only generation cannot follow a vocal closely enough | High | High — it is the Phase A gate | `fit` rejects what cannot be repaired; the blind test decides; Mureka is the conditioned alternative |
| A vocal with no measurable tempo or key (rap a cappella) gives the generator nothing to follow and `fit` nothing to check | High for rap | High | Stated in every summary; ask for a BPM, or a take over a click, at intake (not built) |
| Mureka's track endpoint does not do what its name suggests | Medium | Medium | Read the schema with an account before writing code |
| Streaming rights depend on the plan (ElevenLabs: Creator or above) | Certain | High if missed | Recorded in provenance; R0 must check the plan before export |
| Mureka publishes no training-data statement | Unknown | Medium — distributor and reputational | Ask Mureka in writing before any release |
| Terms change after a song is made | Medium | Medium | `provenance.json` stores the terms URL and the date it was read with every generation |

## What is still unverified

- Mureka: the `track/generate` and `instrumental/generate` request fields, per-song price, rate limits.
- ElevenLabs: the per-minute price beyond the plan allowance, and whether the response reports cost. The adapter therefore **estimates** cost and flags it.
- ElevenLabs: the auth header. The compose page does not state it; the adapter sends `xi-api-key`, which is what ElevenLabs' other endpoints use.
- Stability: price per generation, maximum duration, input formats.
- None of the three was called. No audio from any vendor has been heard.

## Sources (all read 2026-10-08)

1. Mureka quickstart — https://platform.mureka.ai/docs/en/quickstart.html
2. Mureka API reference, Generate track — https://platform.mureka.ai/docs/api/operations/post-v1-track-generate.html (endpoint list from the same site's navigation)
3. Mureka API reference, Upload file — https://platform.mureka.ai/docs/api/operations/post-v1-files-upload.html
4. ElevenLabs API reference, Compose music — https://elevenlabs.io/docs/api-reference/music/compose
5. ElevenLabs Music quickstart — https://elevenlabs.io/docs/cookbooks/music/quickstart
6. Eleven Music Model-Specific Terms (last updated 2026-05-26) — https://elevenlabs.io/eleven-music-model-specific-terms
7. Stability AI knowledge base, audio-to-audio tips — https://kb.stability.ai/knowledge-base/tips-for-using-the-audio-to-audio-api
8. Mureka API Service Agreement (effective 2025-12-03) — https://platform.mureka.ai/service_terms.pdf
9. Eleven Music API product page — https://elevenlabs.io/eleven-music-api
10. Stability AI Terms of Service (effective 2026-09-30) — https://stability.ai/terms-of-service
11. Third-party Suno wrapper documentation, read only to confirm it is not Suno's own — https://docs.sunoapi.org/suno-api/add-instrumental
12. Search results for ACE-Step 1.5 hosting and licence; no primary page was read.

Pages that would not load for this review and so are **not** cited as read:
https://platform.mureka.ai/pricing, https://platform.stability.ai/pricing,
https://platform.stability.ai/docs/api-reference.
