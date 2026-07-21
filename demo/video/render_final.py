"""Assemble the final narrated demo, captions, and mastered audio."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
VIDEO_DIR = ROOT / "demo" / "video"
BUILD = VIDEO_DIR / "build"
NARRATION = json.loads((VIDEO_DIR / "narration.json").read_text(encoding="utf-8"))
ELEVENLABS_AUDIO = VIDEO_DIR / "elevenlabs" / "full-narration.mp3"
ELEVENLABS_SOURCE = Path(
    "/Users/ramansharma/Downloads/"
    "ElevenLabs_2026-06-11T18_40_28_Ember - Energetic, Confident Protagonist_pvc_sp100_s7_sb100_v3.mp3"
)
# Boundaries are the long paragraph pauses in the supplied ElevenLabs read.
ELEVENLABS_BOUNDARIES = [0.0, 20.45873, 54.489615, 87.648231, 126.34551, 135.1575]
FINAL_DIR = Path.home() / ".codex" / "artifacts" / "job-hunt-signal"
FPS = 30
FRAME_SIZE = (1920, 1080)
CAPTION_FONT_PATH = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
TRANSITION_SECONDS = 0.55


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    return float(result.stdout.strip())


def timestamp(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def caption_chunks(text: str, max_chars: int = 54) -> list[str]:
    words = text.split()
    chunks: list[str] = []
    line: list[str] = []
    for word in words:
        proposed = " ".join([*line, word])
        if line and len(proposed) > max_chars:
            chunks.append(" ".join(line))
            line = [word]
        else:
            line.append(word)
    if line:
        chunks.append(" ".join(line))
    return chunks


def caption_image(path: Path, text: str) -> None:
    image = Image.new("RGBA", FRAME_SIZE, (0, 0, 0, 0))
    if text:
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(str(CAPTION_FONT_PATH), 42)
        bbox = draw.textbbox((0, 0), text, font=font)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        x = (FRAME_SIZE[0] - width) // 2
        y = FRAME_SIZE[1] - 112
        padding_x = 24
        padding_y = 16
        draw.rounded_rectangle(
            (
                x - padding_x,
                y - padding_y,
                x + width + padding_x,
                y + height + padding_y,
            ),
            radius=18,
            fill=(5, 7, 12, 194),
            outline=(255, 255, 255, 35),
            width=1,
        )
        draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))
    image.save(path)


def build_caption_overlay(
    captions: list[tuple[float, float, str]],
    total_duration: float,
) -> Path:
    caption_dir = BUILD / "captions"
    caption_dir.mkdir(parents=True, exist_ok=True)
    blank = caption_dir / "blank.png"
    caption_image(blank, "")

    timeline: list[tuple[Path, float]] = []
    cursor = 0.0
    for index, (start, end, text) in enumerate(captions, start=1):
        if start > cursor:
            timeline.append((blank, start - cursor))
        image_path = caption_dir / f"caption-{index:03}.png"
        caption_image(image_path, text)
        timeline.append((image_path, end - start))
        cursor = end
    if total_duration > cursor:
        timeline.append((blank, total_duration - cursor))

    concat_file = caption_dir / "captions.txt"
    lines: list[str] = []
    for path, segment_duration in timeline:
        lines.append(f"file '{path}'")
        lines.append(f"duration {segment_duration:.6f}")
    lines.append(f"file '{timeline[-1][0]}'")
    concat_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    overlay = BUILD / "captions.mov"
    run(
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-vf", f"fps={FPS},format=rgba", "-c:v", "qtrle", str(overlay),
    )
    return overlay


def resolve_elevenlabs_audio() -> Path | None:
    if ELEVENLABS_AUDIO.exists():
        return ELEVENLABS_AUDIO
    if ELEVENLABS_SOURCE.exists():
        return ELEVENLABS_SOURCE
    return None


def build_visual_master(visual_files: list[Path], beat_durations: list[float]) -> Path:
    output = BUILD / "silent-final.mp4"
    if len(visual_files) == 1:
        return visual_files[0]

    inputs: list[str] = []
    for visual in visual_files:
        inputs.extend(["-i", str(visual)])

    filters: list[str] = []
    current = "[0:v]"
    cumulative = beat_durations[0]
    for index in range(1, len(visual_files)):
        out = f"[xf{index}]"
        offset = cumulative - TRANSITION_SECONDS * index
        filters.append(
            f"{current}[{index}:v]xfade=transition=fade:"
            f"duration={TRANSITION_SECONDS}:offset={offset:.6f}{out}"
        )
        current = out
        cumulative += beat_durations[index]

    run(
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        *inputs,
        "-filter_complex", ";".join(filters),
        "-map", current,
        "-c:v", "libx264", "-crf", "16", "-preset", "slow",
        "-pix_fmt", "yuv420p", str(output),
    )
    return output


def visual_for_beat(beat_id: str, target: float) -> Path:
    output = BUILD / f"visual-{beat_id}.mp4"
    if beat_id in {"intro", "close"}:
        card = BUILD / "cards" / f"{'title' if beat_id == 'intro' else 'close'}.png"
        run(
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-loop", "1", "-i", str(card), "-t", f"{target:.3f}",
            "-vf", f"zoompan=z='min(zoom+0.00018,1.035)':d={round(target * FPS)}:s=1920x1080:fps={FPS},format=yuv420p",
            "-c:v", "libx264", "-crf", "16", "-preset", "slow", str(output),
        )
        return output

    if beat_id in {"product", "trace"}:
        source = BUILD / f"{beat_id}.mp4"
        if beat_id == "product" and (BUILD / "live-product-dark.mp4").exists():
            source = BUILD / "live-product-dark.mp4"
        elif beat_id == "product" and (BUILD / "live-product.mp4").exists():
            source = BUILD / "live-product.mp4"
        if beat_id == "product" and source.name.startswith("live-product"):
            source_duration = duration(source)
            opening_source = min(12.0, source_duration * 0.15)
            results_start = min(source_duration * 0.80, source_duration - 8.0)
            opening_target = min(7.0, target * 0.25)
            progress_target = min(7.0, target * 0.25)
            results_target = target - opening_target - progress_target
            run(
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(source),
                "-filter_complex",
                (
                    f"[0:v]trim=start=0:end={opening_source:.6f},"
                    f"setpts={(opening_target / opening_source):.9f}*(PTS-STARTPTS)[opening];"
                    f"[0:v]trim=start={opening_source:.6f}:end={results_start:.6f},"
                    f"setpts={(progress_target / (results_start - opening_source)):.9f}*(PTS-STARTPTS)[progress];"
                    f"[0:v]trim=start={results_start:.6f}:end={source_duration:.6f},"
                    f"setpts={(results_target / (source_duration - results_start)):.9f}*(PTS-STARTPTS)[results];"
                    "[opening][progress][results]concat=n=3:v=1:a=0,"
                    f"crop=1200:675:(iw-ow)/2:(ih-oh)/2,scale=1920:1080:flags=lanczos,"
                    f"fps={FPS},format=yuv420p[v]"
                ),
                "-map", "[v]", "-t", f"{target:.3f}",
                "-an", "-c:v", "libx264", "-crf", "16", "-preset", "slow", str(output),
            )
            return output
        factor = target / duration(source)
        run(
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(source), "-t", f"{target:.3f}",
            "-vf", f"setpts={factor:.8f}*PTS,fps={FPS},format=yuv420p",
            "-an", "-c:v", "libx264", "-crf", "16", "-preset", "slow", str(output),
        )
        return output

    loop_card = BUILD / "cards" / "loop.png"
    chart = ROOT / "demo" / "round_comparison.png"
    intro_duration = min(7.0, target * 0.2)
    chart_duration = target - intro_duration
    card_video = BUILD / "loop-card.mp4"
    chart_video = BUILD / "loop-chart.mp4"
    run(
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1", "-i", str(loop_card), "-t", f"{intro_duration:.3f}",
        "-vf", f"zoompan=z='min(zoom+0.00025,1.03)':d={round(intro_duration * FPS)}:s=1920x1080:fps={FPS},format=yuv420p",
        "-c:v", "libx264", "-crf", "16", "-preset", "slow", str(card_video),
    )
    run(
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1", "-i", str(chart), "-t", f"{chart_duration:.3f}",
        "-vf",
        (
            "scale=1720:-1:flags=lanczos,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x0b0e14,"
            f"zoompan=z='min(zoom+0.00012,1.025)':d={round(chart_duration * FPS)}:"
            f"s=1920x1080:fps={FPS},format=yuv420p"
        ),
        "-c:v", "libx264", "-crf", "16", "-preset", "slow", str(chart_video),
    )
    list_file = BUILD / "loop-visuals.txt"
    list_file.write_text(f"file '{card_video}'\nfile '{chart_video}'\n", encoding="utf-8")
    run(
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", str(output),
    )
    return output


def main() -> None:
    FINAL_DIR.mkdir(parents=True, exist_ok=True)
    BUILD.mkdir(parents=True, exist_ok=True)

    visual_files: list[Path] = []
    visual_durations: list[float] = []
    captions: list[tuple[float, float, str]] = []
    offset = 0.0
    full_narration = resolve_elevenlabs_audio()

    for index, beat in enumerate(NARRATION):
        if full_narration:
            beat_duration = ELEVENLABS_BOUNDARIES[index + 1] - ELEVENLABS_BOUNDARIES[index]
        else:
            audio = BUILD / "audio" / f"{beat['id']}.wav"
            beat_duration = duration(audio) + 0.45
        visual_duration = beat_duration + (
            TRANSITION_SECONDS if index < len(NARRATION) - 1 else 0.0
        )
        visual_files.append(visual_for_beat(beat["id"], visual_duration))
        visual_durations.append(visual_duration)

        chunks = caption_chunks(re.sub(r"\s+", " ", beat["text"]).strip())
        weights = [max(1, len(chunk.split())) for chunk in chunks]
        usable = max(0.1, beat_duration - 0.55)
        cursor = offset + 0.20
        for chunk, weight in zip(chunks, weights):
            chunk_duration = usable * weight / sum(weights)
            captions.append((cursor, cursor + chunk_duration, chunk))
            cursor += chunk_duration
        offset += beat_duration

    silent_video = build_visual_master(visual_files, visual_durations)
    mastered = BUILD / "narration-master.wav"
    if full_narration:
        run(
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(full_narration),
            "-af",
            (
                "highpass=f=55,lowpass=f=15800,"
                "acompressor=threshold=-16dB:ratio=1.45:attack=18:release=180,"
                "loudnorm=I=-16:TP=-1.5:LRA=7"
            ),
            "-ar", "48000", str(mastered),
        )
    else:
        audio_files: list[Path] = []
        for beat in NARRATION:
            audio = BUILD / "audio" / f"{beat['id']}.wav"
            padded_audio = BUILD / "audio" / f"{beat['id']}-padded.wav"
            beat_duration = duration(audio) + 0.45
            run(
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(audio), "-af", "apad=pad_dur=0.45",
                "-t", f"{beat_duration:.3f}", "-ar", "48000", str(padded_audio),
            )
            audio_files.append(padded_audio)
        audio_list = BUILD / "audio.txt"
        audio_list.write_text(
            "".join(f"file '{path}'\n" for path in audio_files),
            encoding="utf-8",
        )
        run(
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(audio_list),
            "-af", "highpass=f=70,lowpass=f=14500,loudnorm=I=-16:TP=-1.5:LRA=8",
            "-ar", "48000", str(mastered),
        )

    srt = FINAL_DIR / "job-hunt-signal-demo.srt"
    srt.write_text(
        "\n".join(
            f"{index}\n{timestamp(start)} --> {timestamp(end)}\n{text}\n"
            for index, (start, end, text) in enumerate(captions, start=1)
        ),
        encoding="utf-8",
    )

    caption_overlay = build_caption_overlay(captions, offset)
    final = FINAL_DIR / "job-hunt-signal-demo.mp4"
    run(
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(silent_video), "-i", str(mastered), "-i", str(caption_overlay),
        "-filter_complex", "[0:v][2:v]overlay=0:0:format=auto[v]",
        "-map", "[v]", "-map", "1:a:0",
        "-c:v", "libx264", "-crf", "18", "-preset", "slow",
        "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
        str(final),
    )
    print(f"Rendered {final} ({duration(final):.1f}s)")


if __name__ == "__main__":
    main()
