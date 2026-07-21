# Build Week demo video

This creates a **2:54, 1920×1080** visual master using only captures from the live product. Login happens before capture and credentials are never written to disk. The raw résumé text is privacy-masked.

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

