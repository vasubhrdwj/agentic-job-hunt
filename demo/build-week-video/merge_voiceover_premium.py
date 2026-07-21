"""Lay ElevenLabs narration onto the premium silent master.

Accepts either:
  - a folder of 8 per-segment files (01.* .. 08.*) placed at each segment's start offset
  - a single continuous read placed at 0.35s

The speech is mastered to broadcast-ish -16 LUFS, the final frame is held if the
voice runs a touch long, and the finished video is capped under three minutes.

Usage:
  .venv/bin/python merge_voiceover_premium.py ~/Downloads/vo-segments/
  .venv/bin/python merge_voiceover_premium.py ~/Downloads/full-read.mp3
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
VISUAL = BUILD / "job-hunt-signal-premium-visual.mp4"
FINAL_LIMIT = 179.0          # keep the finished cut under 3:00
MASTER_CHAIN = (
    "highpass=f=55,lowpass=f=15800,"
    "acompressor=threshold=-16dB:ratio=1.45:attack=18:release=180,"
    "loudnorm=I=-16:TP=-1.5:LRA=7"
)


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(out.stdout.strip())


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def segment_offsets() -> list[float]:
    text = (HERE / "narration-final.txt").read_text(encoding="utf-8")
    return [float(m.group(1)) for m in re.finditer(r"\[S\d+ @ ([\d.]+)-", text)]


def build_track(source: Path, total: float) -> Path:
    mixed = BUILD / "narration-track.wav"
    if source.is_dir():
        files = sorted(p for p in source.iterdir() if p.suffix.lower() in {".mp3", ".wav", ".m4a"})
        offsets = segment_offsets()
        if len(files) != len(offsets):
            raise SystemExit(f"Expected {len(offsets)} segment files (01..0{len(offsets)}), found {len(files)}.")
        for i, (path, offset) in enumerate(zip(files, offsets)):
            seg = duration(path)
            window_end = offsets[i + 1] if i + 1 < len(offsets) else total
            if offset + seg > window_end + 2.5:
                print(f"WARNING: {path.name} runs {seg:.1f}s, past its window end {window_end:.1f}s")
        inputs, parts = [], []
        for i, (path, offset) in enumerate(zip(files, offsets)):
            inputs += ["-i", str(path)]
            parts.append(f"[{i}:a]aresample=48000,adelay={round(offset * 1000)}:all=1[a{i}]")
        joined = "".join(f"[a{i}]" for i in range(len(files)))
        parts.append(f"{joined}amix=inputs={len(files)}:normalize=0[mix]")
        run(
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
            "-filter_complex", ";".join(parts), "-map", "[mix]",
            "-t", f"{total:.3f}", "-ar", "48000", str(mixed),
        )
    else:
        # Video is retimed to the audio, so place a continuous read at ~t=0.
        run(
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
            "-af", "aresample=48000,adelay=100:all=1,apad", "-t", f"{total:.3f}",
            "-ar", "48000", str(mixed),
        )
    return mixed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("voiceover", type=Path, help="ElevenLabs file or folder of 8 segment files")
    parser.add_argument("--output", type=Path, default=BUILD / "job-hunt-signal-demo-final.mp4")
    args = parser.parse_args()
    source = args.voiceover.expanduser().resolve()
    if not VISUAL.exists():
        raise SystemExit("Render the visual master first: render_premium.py")
    if not source.exists():
        raise SystemExit(f"Voiceover not found: {source}")

    visual_duration = duration(VISUAL)
    raw_track = build_track(source, visual_duration)
    audio_duration = duration(raw_track)
    output_duration = min(FINAL_LIMIT, max(visual_duration, audio_duration + 0.8))
    hold = max(0.0, output_duration - visual_duration)

    mastered = BUILD / "elevenlabs-master-final.wav"
    bed = BUILD / "audio" / "bed.wav"
    if bed.exists():
        # Master the VO, duck the music under speech (sidechain), mix, then peak-safe
        # loudness so the combined track never clips or distorts.
        fade_at = max(0.0, output_duration - 1.5)
        chain = (
            f"[0:a]{MASTER_CHAIN},asplit=2[vo1][vo2];"
            f"[1:a]atrim=0:{output_duration:.3f},afade=t=out:st={fade_at:.3f}:d=1.5[bedt];"
            f"[bedt][vo1]sidechaincompress=threshold=0.04:ratio=7:attack=12:release=380[duck];"
            f"[vo2][duck]amix=inputs=2:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=9,apad[out]"
        )
        run(
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(raw_track), "-i", str(bed),
            "-filter_complex", chain, "-map", "[out]",
            "-t", f"{output_duration:.3f}", "-ar", "48000", str(mastered),
        )
    else:
        run(
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw_track),
            "-af", f"{MASTER_CHAIN},apad", "-t", f"{output_duration:.3f}", "-ar", "48000", str(mastered),
        )
    run(
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(VISUAL), "-i", str(mastered),
        "-filter_complex", f"[0:v]tpad=stop_mode=clone:stop_duration={hold:.3f}[v]",
        "-map", "[v]", "-map", "1:a:0", "-t", f"{output_duration:.3f}",
        "-c:v", "libx264", "-crf", "17", "-preset", "slow", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(args.output),
    )
    print(f"Wrote {args.output} ({output_duration:.1f}s / {output_duration/60:.2f}min)")


if __name__ == "__main__":
    main()
