"""Assemble the premium Build Week demo: animated HTML intro + live walkthrough + Codex outro.

Pipeline:
  1. film/capture.mjs renders the deterministic intro & outro to PNG frames.
  2. film/capture_stage.mjs renders the walkthrough chrome (bg / mask / frame / captions).
  3. This script encodes the frame sequences, composites the real footage onto the
     stage, times the captions, and crossfades the three acts into one silent master.

Usage:
  node film/capture.mjs --phase intro --duration 30.5 --fps 30 --out build/frames/intro
  node film/capture.mjs --phase outro --duration 32.3 --fps 30 --out build/frames/outro
  node film/capture_stage.mjs
  .venv/bin/python render_premium.py [~/Downloads/screenrecdemo.mp4]

Output: build/job-hunt-signal-premium-visual.mp4  (1920x1080, 30fps, silent)
"""

from __future__ import annotations

import math
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
FRAMES = BUILD / "frames"
STAGE = BUILD / "stage"
CLIPS = BUILD / "clips"
FOOTAGE = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path("~/Downloads/screenrecdemo.mp4").expanduser()

WIDTH, HEIGHT, FPS = 1920, 1080, 30
XFADE = 0.4
# Timeline retimed to the ElevenLabs narration (see narration-final.txt + transcript).
INTRO_LEN = 24.1          # intro act; footage enters here (narration S2 begins ~24.1s)
WALK_ACT = 97.4           # walkthrough act length; the 90.7s footage is slowed to fill it
OUTRO_LEN = 36.4          # outro act (S7 Codex credit + S8 close) — trimmed VO ends ~157.1s
PANEL_X, PANEL_Y, PANEL_W, PANEL_H = 100, 238, 1720, 784

# Caption windows in walkthrough-local seconds, aligned to the narration segments.
CAPTIONS = [
    (0.0, 25.4),    # ONE PRIVATE PROFILE        (S2)
    (25.4, 39.9),   # TODAY                       (S3 pt.1)
    (39.9, 46.4),   # A REAL AMAZON OPENING       (S3 pt.2)
    (46.4, 58.5),   # EVIDENCE BEFORE CLAIMS      (S4)
    (58.5, 71.7),   # FIVE SOURCE-BACKED PATHS    (S5 pt.1)
    (71.7, 82.6),   # GROUNDED, MANUAL OUTREACH   (S5 pt.2)
    (82.6, 97.4),   # THE LOOP CLOSES             (S6)
]


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(out.stdout.strip())


def encode_frames(name: str) -> Path:
    src = FRAMES / name
    frames = sorted(src.glob("frame_*.png"))
    if not frames:
        raise SystemExit(f"No frames in {src}. Run: node film/capture.mjs --phase {name} ...")
    out = CLIPS / f"{name}.mp4"
    run(
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-framerate", str(FPS), "-i", str(src / "frame_%05d.png"),
        "-vf", "setsar=1,format=yuv420p",
        "-r", str(FPS), "-c:v", "libx264", "-crf", "17", "-preset", "medium", str(out),
    )
    return out


def build_walkthrough(target_dur: float, tail: float) -> Path:
    footage_dur = probe_duration(FOOTAGE)
    factor = target_dur / footage_dur     # slow the footage so it fills the narration window
    length = target_dur + tail
    inputs = [
        "-i", str(FOOTAGE),
        "-loop", "1", "-i", str(STAGE / "bg.png"),
        "-loop", "1", "-i", str(STAGE / "mask.png"),
        "-loop", "1", "-i", str(STAGE / "frame.png"),
    ]
    for i in range(len(CAPTIONS)):
        inputs += ["-loop", "1", "-i", str(STAGE / f"caption_{i}.png")]

    parts = [
        f"[0:v]setpts={factor:.5f}*(PTS-STARTPTS),fps={FPS},scale={PANEL_W}:{PANEL_H}:flags=lanczos,setsar=1,"
        f"tpad=stop_mode=clone:stop_duration={tail + 0.3}[vraw]",
        "[vraw][2:v]alphamerge[vround]",
        f"[1:v]scale={WIDTH}:{HEIGHT},setsar=1[base]",
        f"[base][vround]overlay={PANEL_X}:{PANEL_Y}:shortest=0[b1]",
        "[b1][3:v]overlay=0:0[b2]",
    ]
    current = "[b2]"
    for i, (start, end) in enumerate(CAPTIONS):
        idx = 4 + i
        fin = f"fade=t=in:st={start:.2f}:d=0.35:alpha=1" if start > 0 else "fade=t=in:st=0:d=0.30:alpha=1"
        fout = f",fade=t=out:st={end - 0.4:.2f}:d=0.4:alpha=1"
        parts.append(f"[{idx}:v]format=rgba,{fin}{fout}[c{i}]")
        out = f"[k{i}]"
        parts.append(f"{current}[c{i}]overlay=0:0{out}")
        current = out

    clip = CLIPS / "walkthrough.mp4"
    run(
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
        "-filter_complex", ";".join(parts), "-map", current,
        "-t", f"{length:.3f}", "-an", "-r", str(FPS),
        "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p", str(clip),
    )
    return clip


def main() -> None:
    CLIPS.mkdir(parents=True, exist_ok=True)

    intro = encode_frames("intro")
    outro = encode_frames("outro")
    # The composited walkthrough clip only depends on the footage + caption windows;
    # reuse the cached clip if the raw footage is no longer on disk.
    walkthrough = CLIPS / "walkthrough.mp4"
    if FOOTAGE.exists():
        walkthrough = build_walkthrough(WALK_ACT, tail=0.6)
    elif walkthrough.exists():
        print(f"Footage missing ({FOOTAGE}) — reusing cached {walkthrough}")
    else:
        raise SystemExit(f"Footage not found ({FOOTAGE}) and no cached walkthrough clip.")

    # Crossfade the three acts. Offsets are cumulative starts on the first stream.
    wt_offset = INTRO_LEN                        # walkthrough enters at 24.1s
    outro_offset = INTRO_LEN + WALK_ACT          # outro enters at 121.5s
    total = outro_offset + OUTRO_LEN

    output = BUILD / "job-hunt-signal-premium-visual.mp4"
    run(
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(intro), "-i", str(walkthrough), "-i", str(outro),
        "-filter_complex",
        f"[0:v][1:v]xfade=transition=fade:duration={XFADE}:offset={wt_offset:.3f}[xf1];"
        f"[xf1][2:v]xfade=transition=fade:duration={XFADE}:offset={outro_offset:.3f}[xf2]",
        "-map", "[xf2]", "-t", f"{total:.3f}", "-an",
        "-c:v", "libx264", "-crf", "17", "-preset", "slow", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(output),
    )
    print(f"Rendered {output}")
    print(f"  intro 0.0–{INTRO_LEN:.1f} · walkthrough {INTRO_LEN:.1f}–{outro_offset:.2f} · "
          f"outro {outro_offset:.2f}–{total:.2f}  (total {total:.2f}s / {total/60:.2f}min)")

    # Music-scored preview (subtle bed, no narration yet) so the cut can be watched now.
    bed = BUILD / "audio" / "bed.wav"
    if bed.exists():
        scored = BUILD / "job-hunt-signal-premium-scored.mp4"
        fade_at = max(0.0, total - 1.5)
        run(
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(output), "-i", str(bed),
            "-filter_complex", f"[1:a]atrim=0:{total:.3f},afade=t=out:st={fade_at:.3f}:d=1.5[a]",
            "-map", "0:v", "-map", "[a]", "-t", f"{total:.3f}",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(scored),
        )
        print(f"Scored preview (music, no VO): {scored}")


if __name__ == "__main__":
    main()
