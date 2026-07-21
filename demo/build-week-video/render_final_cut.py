"""Render the final Build Week demo: animated story intro + real walkthrough footage + Codex credit.

Reuses the scene system from render_demo.py for cards/collage/brand/codex scenes and
composites the live screen recording onto the same stage with timed captions.

Usage:
    .venv/bin/python demo/build-week-video/render_final_cut.py [path/to/screenrecdemo.mp4]
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import render_demo as rd  # noqa: E402

BUILD = HERE / "build"
SCENES = BUILD / "scenes-final"
CLIPS = BUILD / "clips-final"
STAGE = BUILD / "stage"
TIMELINE = json.loads((HERE / "timeline-final.json").read_text(encoding="utf-8"))
FOOTAGE = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path("~/Downloads/screenrecdemo.mp4").expanduser()
WIDTH, HEIGHT = 1920, 1080
FPS = 30
TRANSITION = 0.30

# Footage panel geometry: 1918x874 source scaled to 1780x811 (same 2.194 aspect).
PANEL = (70, 195, 1850, 1006)
PANEL_RADIUS = 26


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def stage_frame() -> Path:
    """Gradient stage with a transparent rounded window where the footage shows through."""
    canvas = rd.gradient_background().convert("RGBA")
    x0, y0, x1, y1 = PANEL
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((x0 + 4, y0 + 16, x1 + 4, y1 + 16), radius=PANEL_RADIUS, fill=(0, 0, 0, 135))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(16)))

    hole = Image.new("L", canvas.size, 255)
    ImageDraw.Draw(hole).rounded_rectangle(PANEL, radius=PANEL_RADIUS, fill=0)
    alpha = canvas.getchannel("A").point(lambda v: v)
    canvas.putalpha(Image.composite(alpha, Image.new("L", canvas.size, 0), hole))

    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rounded_rectangle(PANEL, radius=PANEL_RADIUS, outline=(255, 255, 255, 34), width=2)
    rd.draw_brand(draw)
    draw.text((70, 1032), "REAL PRODUCT · UNEDITED SCREEN CAPTURE", font=rd.font(16, True), fill=(*rd.MUTED, 210))
    path = STAGE / "stage-frame.png"
    canvas.save(path)
    return path


def caption_png(index: int, eyebrow: str, title: str) -> Path:
    """Transparent overlay holding one caption band above the panel."""
    layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    draw.text((70, 104), eyebrow, font=rd.font(20, True), fill=(*rd.MINT, 255))
    draw.text((70, 134), title, font=rd.font(44, True), fill=(*rd.WHITE, 255))
    width = draw.textbbox((0, 0), title, font=rd.font(44, True))[2]
    draw.line((70 + width + 26, 166, 70 + width + 156, 166), fill=(*rd.ACCENT, 180), width=4)
    path = STAGE / f"caption-{index}.png"
    layer.save(path)
    return path


def scene_clip(index: int, scene: dict, duration: float, tail: float) -> Path:
    image_path = SCENES / f"{index:02}-{scene['id']}.png"
    rd.render_scene(scene).convert("RGB").save(image_path, quality=96)
    frames = math.ceil((duration + tail) * FPS)
    clip = CLIPS / f"{index:02}-{scene['id']}.mp4"
    zoom = "min(zoom+0.000018,1.014)" if scene["kind"] in {"screen", "collage"} else "1"
    run(
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1", "-i", str(image_path),
        "-vf", (
            "scale=2048:1152:flags=lanczos,"
            f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS},format=yuv420p"
        ),
        "-frames:v", str(frames), "-an", "-c:v", "libx264", "-crf", "17", "-preset", "medium",
        str(clip),
    )
    return clip


def walkthrough_clip(tail: float) -> Path:
    act = TIMELINE["walkthrough_act"]
    trim_start, trim_end = act["source_trim"]
    duration = trim_end - trim_start
    frame = stage_frame()
    captions = [caption_png(i, c["eyebrow"], c["title"]) for i, c in enumerate(act["captions"])]

    inputs = ["-ss", f"{trim_start}", "-t", f"{duration}", "-i", str(FOOTAGE), "-loop", "1", "-i", str(frame)]
    for path in captions:
        inputs.extend(["-loop", "1", "-i", str(path)])

    x0, y0 = PANEL[0], PANEL[1]
    pw, ph = PANEL[2] - PANEL[0], PANEL[3] - PANEL[1]
    parts = [
        f"color=c=#07090f:s={WIDTH}x{HEIGHT}:r={FPS}:d={duration + tail}[base]",
        f"[0:v]fps={FPS},scale={pw}:{ph}:flags=lanczos,tpad=stop_mode=clone:stop_duration={tail + 0.2}[vid]",
        f"[base][vid]overlay={x0}:{y0}[b0]",
        "[b0][1:v]overlay=0:0[b1]",
    ]
    current = "[b1]"
    for i, cap in enumerate(act["captions"]):
        start, end = cap["local"]
        fade_in = f"fade=t=in:st={start:.2f}:d=0.45:alpha=1" if start > 0 else "fade=t=in:st=0:d=0.01:alpha=1"
        last = i == len(act["captions"]) - 1
        fade_out = "" if last else f",fade=t=out:st={end - 0.45:.2f}:d=0.45:alpha=1"
        parts.append(f"[{i + 2}:v]format=rgba,{fade_in}{fade_out}[c{i}]")
        out = f"[k{i}]"
        parts.append(f"{current}[c{i}]overlay=0:0{out}")
        current = out
    clip = CLIPS / "80-walkthrough.mp4"
    run(
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
        "-filter_complex", ";".join(parts), "-map", current,
        "-t", f"{duration + tail:.3f}", "-an",
        "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p",
        str(clip),
    )
    return clip


def timestamp(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def write_srt() -> None:
    """Segment-timed SRT from narration-final.txt ([Snn @ start-end] headers)."""
    text = (HERE / "narration-final.txt").read_text(encoding="utf-8")
    entries: list[tuple[float, float, str]] = []
    for match in re.finditer(r"\[S\d+ @ ([\d.]+)-([\d.]+)\]\s*\n(.+?)(?=\n\[S|\Z)", text, re.S):
        start, end, body = float(match.group(1)), float(match.group(2)), " ".join(match.group(3).split())
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]
        weights = [len(s.split()) for s in sentences]
        cursor = start
        for sentence, weight in zip(sentences, weights):
            duration = (end - start) * weight / sum(weights)
            entries.append((cursor, cursor + duration - 0.08, sentence))
            cursor += duration
    lines: list[str] = []
    for index, (start, end, sentence) in enumerate(entries, start=1):
        lines.extend([str(index), f"{timestamp(start)} --> {timestamp(end)}", sentence, ""])
    (BUILD / "narration-final.srt").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    if not FOOTAGE.exists():
        raise SystemExit(f"Footage not found: {FOOTAGE}")
    for directory in (SCENES, CLIPS, STAGE):
        directory.mkdir(parents=True, exist_ok=True)

    scenes = TIMELINE["intro"] + TIMELINE["outro"]
    boundaries: list[float] = []
    clips: list[Path] = []
    index = 0
    for scene in TIMELINE["intro"]:
        duration = float(scene["end"]) - float(scene["start"])
        clips.append(scene_clip(index, scene, duration, TRANSITION))
        boundaries.append(float(scene["start"]))
        index += 1
    act = TIMELINE["walkthrough_act"]
    clips.append(walkthrough_clip(TRANSITION))
    boundaries.append(float(act["start"]))
    for scene in TIMELINE["outro"]:
        duration = float(scene["end"]) - float(scene["start"])
        tail = TRANSITION if scene is not TIMELINE["outro"][-1] else 0.0
        clips.append(scene_clip(index + 90, scene, duration, tail))
        boundaries.append(float(scene["start"]))
        index += 1

    filter_parts: list[str] = []
    current = "[0:v]"
    for i in range(1, len(clips)):
        out = f"[xf{i}]"
        filter_parts.append(
            f"{current}[{i}:v]xfade=transition=fade:duration={TRANSITION}:offset={boundaries[i]:.3f}{out}"
        )
        current = out
    filter_path = BUILD / "xfade-final.txt"
    filter_path.write_text(";\n".join(filter_parts), encoding="utf-8")

    inputs: list[str] = []
    for clip in clips:
        inputs.extend(["-i", str(clip)])
    total = float(TIMELINE["outro"][-1]["end"])
    output = BUILD / "job-hunt-signal-final-visual.mp4"
    run(
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
        "-filter_complex_script", str(filter_path), "-map", current,
        "-t", f"{total:.3f}", "-an",
        "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(output),
    )
    if (HERE / "narration-final.txt").exists():
        write_srt()
    print(f"Rendered {output} ({total:.2f}s)")


if __name__ == "__main__":
    main()
