"""Render a 174-second, 1080p demo from real live-product captures."""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
import subprocess

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps


HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
CAPTURE = BUILD / "capture"
SCENES = BUILD / "scenes"
CLIPS = BUILD / "clips"
TIMELINE = json.loads((HERE / "timeline.json").read_text(encoding="utf-8"))
WIDTH, HEIGHT = 1920, 1080
FPS = 30
TRANSITION = 0.30
ACCENT = (112, 119, 255)
MINT = (89, 226, 194)
INK = (7, 9, 17)
WHITE = (246, 247, 252)
MUTED = (166, 172, 191)
FONT_REGULAR = Path("/System/Library/Fonts/SFNS.ttf")
FONT_BOLD = Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_BOLD if bold else FONT_REGULAR), size)


def gradient_background() -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), INK)
    pixels = image.load()
    for y in range(HEIGHT):
        for x in range(WIDTH):
            d1 = math.sqrt(((x - 1470) / 1050) ** 2 + ((y - 170) / 780) ** 2)
            d2 = math.sqrt(((x - 260) / 950) ** 2 + ((y - 1020) / 820) ** 2)
            glow1 = max(0.0, 1.0 - d1) * 0.14
            glow2 = max(0.0, 1.0 - d2) * 0.08
            pixels[x, y] = (
                int(INK[0] + 80 * glow1 + 18 * glow2),
                int(INK[1] + 70 * glow1 + 52 * glow2),
                int(INK[2] + 145 * glow1 + 105 * glow2),
            )
    draw = ImageDraw.Draw(image, "RGBA")
    for x in range(-HEIGHT, WIDTH, 88):
        draw.line((x, 0, x + HEIGHT, HEIGHT), fill=(255, 255, 255, 7), width=1)
    return image


def round_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0], size[1]), radius=radius, fill=255)
    return mask


def paste_rounded(canvas: Image.Image, image: Image.Image, box: tuple[int, int, int, int], radius: int = 26) -> None:
    x0, y0, x1, y1 = box
    fitted = ImageOps.fit(image.convert("RGB"), (x1 - x0, y1 - y0), method=Image.Resampling.LANCZOS)
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle((x0 + 4, y0 + 16, x1 + 4, y1 + 16), radius=radius, fill=(0, 0, 0, 135))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(16)))
    canvas.paste(fitted, (x0, y0), round_mask(fitted.size, radius))
    ImageDraw.Draw(canvas, "RGBA").rounded_rectangle(box, radius=radius, outline=(255, 255, 255, 34), width=2)


def wrap(draw: ImageDraw.ImageDraw, text: str, selected_font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if current and draw.textbbox((0, 0), candidate, font=selected_font)[2] > width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def draw_brand(draw: ImageDraw.ImageDraw) -> None:
    draw.ellipse((70, 58, 92, 80), fill=(*MINT, 255))
    draw.ellipse((78, 66, 84, 72), fill=(*INK, 255))
    draw.text((108, 53), "JOB HUNT SIGNAL", font=font(22, True), fill=(*WHITE, 230))
    draw.text((1646, 55), "BUILD WEEK DEMO", font=font(17, True), fill=(*MUTED, 210))


def card_scene(scene: dict) -> Image.Image:
    canvas = gradient_background().convert("RGBA")
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw_brand(draw)
    draw.text((120, 286), scene.get("eyebrow", ""), font=font(28, True), fill=(*MINT, 255))
    title_font = font(88, True)
    lines = wrap(draw, scene["title"], title_font, 1450)
    y = 356
    for line in lines:
        draw.text((120, y), line, font=title_font, fill=(*WHITE, 255), stroke_width=1)
        y += 106
    if scene.get("step"):
        draw.rounded_rectangle((120, 765, 340, 827), radius=31, fill=(*ACCENT, 42), outline=(*ACCENT, 130), width=2)
        draw.text((160, 779), scene["step"], font=font(25, True), fill=(*WHITE, 245))
    if scene.get("accent"):
        draw.rounded_rectangle((120, 766, 462, 833), radius=33, fill=(*ACCENT, 48), outline=(*ACCENT, 140), width=2)
        draw.text((158, 784), scene["accent"], font=font(22, True), fill=(*WHITE, 245))
    draw.line((120, 932, 1800, 932), fill=(*WHITE, 24), width=1)
    draw.text((120, 961), "THE JOB SEARCH BREAKS BEFORE APPLY", font=font(19, True), fill=(*MUTED, 190))
    return canvas


def brand_scene(scene: dict) -> Image.Image:
    canvas = gradient_background().convert("RGBA")
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw_brand(draw)
    draw.ellipse((158, 286, 270, 398), fill=(*MINT, 255))
    draw.ellipse((194, 322, 234, 362), fill=(*INK, 255))
    draw.text((158, 458), scene["title"], font=font(96, True), fill=(*WHITE, 255))
    subtitle = scene.get("subtitle", "")
    draw.text((164, 584), subtitle, font=font(35), fill=(*MUTED, 255))
    if scene.get("footer"):
        draw.rounded_rectangle((158, 747, 766, 819), radius=36, fill=(255, 255, 255, 12), outline=(255, 255, 255, 35), width=1)
        draw.text((195, 766), scene["footer"], font=font(26, True), fill=(*WHITE, 235))
    return canvas


def screenshot(path: Path) -> Image.Image:
    if not path.exists():
        raise FileNotFoundError(f"Missing real capture: {path}. Run capture_live.mjs first.")
    return Image.open(path).convert("RGB")


def screen_scene(scene: dict) -> Image.Image:
    source = screenshot(CAPTURE / scene["asset"])
    blurred = ImageOps.fit(source, (WIDTH, HEIGHT), method=Image.Resampling.LANCZOS)
    blurred = ImageEnhance.Brightness(blurred.filter(ImageFilter.GaussianBlur(28))).enhance(0.22)
    canvas = blurred.convert("RGBA")
    overlay = gradient_background().convert("RGBA")
    overlay.putalpha(178)
    canvas.alpha_composite(overlay)
    paste_rounded(canvas, source, (370, 132, 1840, 959), 28)
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw_brand(draw)
    draw.text((75, 204), scene.get("eyebrow", ""), font=font(19, True), fill=(*MINT, 255))
    title_font = font(48, True)
    y = 248
    for line in wrap(draw, scene["title"], title_font, 250):
        draw.text((75, y), line, font=title_font, fill=(*WHITE, 255))
        y += 61
    draw.line((75, y + 20, 310, y + 20), fill=(*ACCENT, 180), width=4)
    draw.text((75, 912), "REAL LIVE PRODUCT", font=font(16, True), fill=(*MUTED, 210))
    return canvas


def collage_scene(scene: dict) -> Image.Image:
    canvas = gradient_background().convert("RGBA")
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw_brand(draw)
    assets = [screenshot(CAPTURE / name) for name in scene["assets"]]
    paste_rounded(canvas, assets[0], (82, 190, 1010, 712), 22)
    paste_rounded(canvas, assets[1], (906, 118, 1825, 635), 22)
    paste_rounded(canvas, assets[2], (644, 560, 1572, 1026), 22)
    draw.rounded_rectangle((115, 742, 730, 946), radius=24, fill=(5, 7, 13, 224), outline=(*ACCENT, 70), width=2)
    title_font = font(51, True)
    y = 776
    for line in wrap(draw, scene["title"], title_font, 548):
        draw.text((151, y), line, font=title_font, fill=(*WHITE, 255))
        y += 64
    return canvas


def codex_scene(scene: dict) -> Image.Image:
    canvas = gradient_background().convert("RGBA")
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw_brand(draw)
    draw.text((115, 225), scene["eyebrow"], font=font(24, True), fill=(*MINT, 255))
    y = 282
    for line in wrap(draw, scene["title"], font(72, True), 1420):
        draw.text((115, y), line, font=font(72, True), fill=(*WHITE, 255))
        y += 88
    chips = [part.strip() for part in scene["subtitle"].split("·")]
    x = 115
    for chip in chips:
        width = ImageDraw.Draw(canvas).textbbox((0, 0), chip, font=font(24, True))[2] + 58
        draw.rounded_rectangle((x, 574, x + width, 636), radius=31, fill=(*ACCENT, 38), outline=(*ACCENT, 105), width=2)
        draw.text((x + 29, 591), chip, font=font(24, True), fill=(*WHITE, 245))
        x += width + 18
    draw.rounded_rectangle((115, 755, 927, 838), radius=22, fill=(255, 255, 255, 12), outline=(255, 255, 255, 28), width=1)
    draw.text((151, 781), scene["note"], font=font(29, True), fill=(*MUTED, 245))
    draw.text((115, 931), "GPT-5.6 HELPED BUILD IT · PRODUCTION FIT REMAINS TRANSPARENT", font=font(18, True), fill=(*MUTED, 190))
    return canvas


def render_scene(scene: dict) -> Image.Image:
    kind = scene["kind"]
    if kind == "card":
        return card_scene(scene)
    if kind == "brand":
        return brand_scene(scene)
    if kind == "screen":
        return screen_scene(scene)
    if kind == "collage":
        return collage_scene(scene)
    if kind == "codex":
        return codex_scene(scene)
    raise ValueError(f"Unknown scene kind: {kind}")


def timestamp(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def write_srt() -> None:
    text = (HERE / "narration.txt").read_text(encoding="utf-8").strip()
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    weights = [len(sentence.split()) for sentence in sentences]
    start, end = 0.35, 172.5
    cursor = start
    lines: list[str] = []
    for index, (sentence, weight) in enumerate(zip(sentences, weights), start=1):
        duration = (end - start) * weight / sum(weights)
        caption_end = cursor + duration
        lines.extend([str(index), f"{timestamp(cursor)} --> {timestamp(caption_end - 0.08)}", sentence, ""])
        cursor = caption_end
    (BUILD / "narration.srt").write_text("\n".join(lines), encoding="utf-8")


def contact_sheet(images: list[Path]) -> None:
    selected = [images[i] for i in [0, 5, 6, 8, 10, 12, 14, 15, 16, 17, 18]]
    thumb_w, thumb_h = 600, 338
    sheet = Image.new("RGB", (thumb_w * 3, thumb_h * 4), (4, 5, 10))
    for index, path in enumerate(selected):
        image = Image.open(path).convert("RGB").resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        sheet.paste(image, ((index % 3) * thumb_w, (index // 3) * thumb_h))
    sheet.save(BUILD / "contact-sheet.jpg", quality=92)


def main() -> None:
    SCENES.mkdir(parents=True, exist_ok=True)
    CLIPS.mkdir(parents=True, exist_ok=True)
    for left, right in zip(TIMELINE, TIMELINE[1:]):
        if abs(float(left["end"]) - float(right["start"])) > 0.001:
            raise ValueError(f"Timeline gap between {left['id']} and {right['id']}")

    scene_images: list[Path] = []
    clips: list[Path] = []
    for index, scene in enumerate(TIMELINE):
        scene_path = SCENES / f"{index:02}-{scene['id']}.png"
        render_scene(scene).convert("RGB").save(scene_path, quality=96)
        scene_images.append(scene_path)

        intended = float(scene["end"]) - float(scene["start"])
        clip_duration = intended + (TRANSITION if index < len(TIMELINE) - 1 else 0.0)
        frames = math.ceil(clip_duration * FPS)
        clip_path = CLIPS / f"{index:02}-{scene['id']}.mp4"
        zoom = "min(zoom+0.000018,1.014)" if scene["kind"] in {"screen", "collage"} else "1"
        run(
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-loop", "1", "-i", str(scene_path),
            "-vf", (
                "scale=2048:1152:flags=lanczos,"
                f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"d={frames}:s=1920x1080:fps={FPS},format=yuv420p"
            ),
            "-frames:v", str(frames), "-an", "-c:v", "libx264", "-crf", "17", "-preset", "medium",
            str(clip_path),
        )
        clips.append(clip_path)

    filter_parts: list[str] = []
    current = "[0:v]"
    for index in range(1, len(clips)):
        output = f"[xf{index}]"
        offset = float(TIMELINE[index]["start"])
        filter_parts.append(
            f"{current}[{index}:v]xfade=transition=fade:duration={TRANSITION}:offset={offset:.3f}{output}"
        )
        current = output
    filter_path = BUILD / "xfade.txt"
    filter_path.write_text(";\n".join(filter_parts), encoding="utf-8")
    output = BUILD / "job-hunt-signal-visual-master.mp4"
    inputs: list[str] = []
    for clip in clips:
        inputs.extend(["-i", str(clip)])
    run(
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
        "-filter_complex_script", str(filter_path), "-map", current,
        "-t", f"{float(TIMELINE[-1]['end']):.3f}", "-an",
        "-c:v", "libx264", "-crf", "17", "-preset", "slow", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(output),
    )
    write_srt()
    contact_sheet(scene_images)
    print(f"Rendered {output}")


if __name__ == "__main__":
    main()

