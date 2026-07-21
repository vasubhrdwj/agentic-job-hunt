"""Merge ElevenLabs narration with the final visual master.

Accepts either:
  - a directory of per-segment files (01.* ... 08.*) -> placed at exact segment offsets
  - a single audio file -> placed at 0.35s

Usage:
    .venv/bin/python demo/build-week-video/merge_voiceover_final.py ~/Downloads/vo-segments/
    .venv/bin/python demo/build-week-video/merge_voiceover_final.py ~/Downloads/full-read.mp3
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
VISUAL = BUILD / "job-hunt-signal-final-visual.mp4"
FINAL_LIMIT = 179.0
MASTER_CHAIN = (
    "highpass=f=55,lowpass=f=15800,"
    "acompressor=threshold=-16dB:ratio=1.45:attack=18:release=180,"
    "loudnorm=I=-16:TP=-1.5:LRA=7"
)


def duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(result.stdout.strip())


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
        for path, offset in zip(files, offsets):
            seg_duration = duration(path)
            window_end = offsets[offsets.index(offset) + 1] if offsets.index(offset) + 1 < len(offsets) else total
            if offset + seg_duration > window_end + 2.5:
                print(f"WARNING: {path.name} runs {seg_duration:.1f}s, past its window end {window_end:.1f}s")
        inputs: list[str] = []
        parts: list[str] = []
        for i, (path, offset) in enumerate(zip(files, offsets)):
            inputs.extend(["-i", str(path)])
            parts.append(f"[{i}:a]aresample=48000,adelay={round(offset * 1000)}:all=1[a{i}]")
        joined = "".join(f"[a{i}]" for i in range(len(files)))
        parts.append(f"{joined}amix=inputs={len(files)}:normalize=0[mix]")
        run(
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
            "-filter_complex", ";".join(parts), "-map", "[mix]",
            "-t", f"{total:.3f}", "-ar", "48000", str(mixed),
        )
    else:
        run(
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
            "-af", "aresample=48000,adelay=350:all=1,apad", "-t", f"{total:.3f}",
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
        raise SystemExit("Render the visual master first: render_final_cut.py")
    if not source.exists():
        raise SystemExit(f"Voiceover not found: {source}")

    visual_duration = duration(VISUAL)
    raw_track = build_track(source, visual_duration)
    audio_duration = duration(raw_track)
    output_duration = min(FINAL_LIMIT, max(visual_duration, audio_duration + 0.8))
    hold = max(0.0, output_duration - visual_duration)

    mastered = BUILD / "elevenlabs-master-final.wav"
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
    print(f"Wrote {args.output} ({output_duration:.1f}s)")


if __name__ == "__main__":
    main()
