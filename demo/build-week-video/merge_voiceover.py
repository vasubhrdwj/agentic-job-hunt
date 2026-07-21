"""Master an ElevenLabs narration file and merge it with the visual master."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
VISUAL = BUILD / "job-hunt-signal-visual-master.mp4"
FINAL_LIMIT = 179.0


def duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("voiceover", type=Path, help="ElevenLabs MP3 or WAV")
    parser.add_argument("--output", type=Path, default=BUILD / "job-hunt-signal-demo-final.mp4")
    args = parser.parse_args()
    voiceover = args.voiceover.expanduser().resolve()
    if not VISUAL.exists():
        raise SystemExit("Render the visual master first.")
    if not voiceover.exists():
        raise SystemExit(f"Voiceover not found: {voiceover}")

    audio_duration = duration(voiceover)
    if audio_duration >= FINAL_LIMIT - 1.0:
        raise SystemExit(f"Narration is {audio_duration:.1f}s. Regenerate it below 178s to keep the video under three minutes.")
    visual_duration = duration(VISUAL)
    output_duration = min(FINAL_LIMIT, max(visual_duration, audio_duration + 1.2))
    hold = max(0.0, output_duration - visual_duration)
    mastered = BUILD / "elevenlabs-master.wav"
    run(
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(voiceover),
        "-af", "highpass=f=55,lowpass=f=15800,acompressor=threshold=-16dB:ratio=1.45:attack=18:release=180,loudnorm=I=-16:TP=-1.5:LRA=7,apad",
        "-t", f"{output_duration:.3f}", "-ar", "48000", str(mastered),
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

