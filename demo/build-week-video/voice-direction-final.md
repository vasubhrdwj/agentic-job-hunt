# ElevenLabs voice direction — premium cut (2:33 video)

Generate **one audio file per segment** of `narration-final.txt` (8 segments, S1–S8).
Paste only the paragraph text into ElevenLabs — never the `[Snn @ …]` header lines.
Name the downloads `01.mp3` … `08.mp3` and keep them together in one folder.

**Voice:** a thoughtful, confident documentary narrator (e.g. a warm male or neutral
mid-tone). Start intimate and slightly urgent (S1, "almost midnight"), open up to clear
and optimistic through the product sections (S2–S6), keep the Codex credit (S7)
matter-of-fact and proud, and land S8 with quiet, earned confidence.

**Pauses (short beats):** after "five more jobs" (S1), after "That is the feature."
(S4), after "this system remembers decisions." (S6), and before "One resume." (S8).

Per-segment duration targets (the video holds these windows):

| Segment | Window (s)  | Max length | Beat on screen                     |
|---------|-------------|------------|------------------------------------|
| S1      | 0.4–26.0    | 25s        | Animated intro: the 5 hidden jobs  |
| S2      | 30.2–52.2   | 21s        | One private profile (resume)       |
| S3      | 52.7–77.3   | 24s        | Today queue → the Amazon opening   |
| S4      | 77.7–85.0   | 7s         | Evidence before claims (locked)    |
| S5      | 85.3–110.0  | 24s        | Five source-backed people + notes  |
| S6      | 110.3–120.7 | 10s        | The loop closes (Weekly Review)    |
| S7      | 121.0–141.0 | 19s        | Built by Codex, with GPT-5.6       |
| S8      | 141.3–152.6 | 11s        | Close card                         |

- Pace: ~0.92–0.96×, depending on the selected voice. Conversational and precise —
  never salesy or breathless.
- Export: MP3 or WAV, 48 kHz preferred.
- A single continuous read of all 8 paragraphs also works (the merge accepts one
  file), but per-segment files sync far more precisely.

Then merge:

```bash
.venv/bin/python demo/build-week-video/merge_voiceover_premium.py ~/Downloads/vo-segments/
# -> build/job-hunt-signal-demo-final.mp4  (speech mastered to -16 LUFS, capped < 3:00)
```
