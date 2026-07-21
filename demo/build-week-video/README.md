# Build Week demo video

The original pipeline creates a **2:54, 1920×1080** visual master using only
captures from the live product. Login happens before capture and credentials
are never written to disk. The raw résumé text is privacy-masked.

## 1. Capture the real product

```bash
DEMO_EMAIL='your-email' \
DEMO_PASSWORD='your-password' \
node demo/build-week-video/capture_live.mjs
```

The capture is read-only: it navigates, scrolls, and screenshots an existing Amazon dossier. It never clicks Pursue, Approve, Copy, Send, or any status action.

## 2. Render the visual master

```bash
.venv/bin/python demo/build-week-video/render_demo.py
```

Outputs are ignored by Git under `demo/build-week-video/build/`:

- `job-hunt-signal-visual-master.mp4`
- `contact-sheet.jpg`
- `narration.srt`

## 3. Add ElevenLabs narration

Paste `narration.txt` into ElevenLabs using `voice-direction.md`, download MP3/WAV, then run:

```bash
.venv/bin/python demo/build-week-video/merge_voiceover.py ~/Downloads/job-hunt-signal-voice.mp3
```

The merge script masters speech to -16 LUFS, holds the final visual if needed, and rejects narration that would push the finished video to three minutes.

## Premium voiced cut

The demo pairs a **frame-accurate, HTML-rendered animated intro/outro** with
the real 90.7s walkthrough footage composited onto the same aurora "stage" (captions
timed to what's on screen, source audio stripped, brand: **Job Hunt Signal**). Total
for the current voiced master is **2:38.7, 1920×1080, 30fps**. Built during
OpenAI Build Week — the outro credits Codex.

The intro/outro are a deterministic timeline (`film/film.html`): every element is a pure
function of time, so Playwright can drive `render(t)` per frame and screenshot exact,
reproducible frames — real motion (kinetic checklist, node-split, brand reveals), not
zoom-on-a-still.

```bash
# 1. render the animated intro + outro to PNG frames (uses system Chrome via playwright-core)
node demo/build-week-video/film/capture.mjs --phase intro --duration 30.5 --fps 30 --out demo/build-week-video/build/frames/intro
node demo/build-week-video/film/capture.mjs --phase outro --duration 32.3 --fps 30 --out demo/build-week-video/build/frames/outro

# 2. render the walkthrough chrome (gradient base, rounded-panel mask, border, captions)
node demo/build-week-video/film/capture_stage.mjs

# 3. generate the subtle ambient music bed (original, royalty-free, level-safe)
.venv/bin/python demo/build-week-video/film/make_music.py
# -> build/audio/bed.wav  (Am–F–C–G pad, -20 LUFS, headroom for narration)

# 4. encode, composite the footage, crossfade the three acts, and add the music bed
.venv/bin/python demo/build-week-video/render_premium.py ~/Downloads/screenrecdemo.mp4
# -> build/job-hunt-signal-premium-visual.mp4   (silent visual master)
# -> build/job-hunt-signal-premium-scored.mp4   (same cut + music)

# 5. after generating ElevenLabs audio per narration-final.txt / voice-direction-final.md:
.venv/bin/python demo/build-week-video/merge_voiceover_premium.py ~/Downloads/vo-segments/
# -> build/job-hunt-signal-demo-final.mp4
#    (VO mastered to -16 LUFS; music sidechain-ducked under speech; peak-limited so it never clips)
```

The music is synthesized, not sampled — swap `build/audio/bed.wav` for any track (trimmed
to ~153s) and re-run steps 4–5 to use a different bed.

The current voiced timeline is intro `0–24.1s` · walkthrough `24.1–121.5s`
(footage slowed about 7% to fill the narration window) · outro `121.5–158.7s`.
Caption windows live in `render_premium.py`.

To retime to a different voiceover, transcribe it, update `INTRO_LEN` / `WALK_ACT` /
`OUTRO_LEN` and `CAPTIONS` in `render_premium.py` and the scene windows in `film/film.html`.

## Refined submission cut (recommended upload)

The submission refinement keeps the final narration and authentic capture,
then fixes one visible story mismatch: the Amazon narration now shows the real
captured Amazon assessment instead of a later Redwood card. It also removes a
loading hold, uses the captured evidence view at the matching narration beat,
masks public identities and exact outreach wording, changes “referral” to the
accurate “lead,” and trims the tail to **2:38.016**.

```bash
.venv/bin/python demo/build-week-video/refine_submission_video.py
```

Upload `build/job-hunt-signal-demo-submission-v2-1440p.mp4` to YouTube. It is a
Lanczos 2560×1440 upload master intended to preserve small UI text through
YouTube transcoding; it does not synthesize detail. The 1920×1080 fallback is
`build/job-hunt-signal-demo-submission-v2.mp4`. Upload
`subtitles-submission.srt` as the manual English caption track; the older
`build/narration-final.srt` does not match the final voice timing.

### Earlier pipeline (PIL cards)

`render_final_cut.py` + `merge_voiceover_final.py` build the original zoom-on-still card
version against `timeline-final.json`. Superseded by the premium cut above.
