"""Create the judge-ready cut from the existing final master.

The refinement never fabricates product UI. It re-times four authentic frames from
the delivered capture so the visible state matches the narration, and masks public
identities / exact outreach text for the public submission.
"""

from __future__ import annotations

import math
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw, ImageFilter, ImageFont


HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
REFINE = BUILD / "submission-refine"
SOURCE = BUILD / "job-hunt-signal-demo-final.mp4"
OUTPUT = BUILD / "job-hunt-signal-demo-submission-v2.mp4"
OUTPUT_1440 = BUILD / "job-hunt-signal-demo-submission-v2-1440p.mp4"
WIDTH, HEIGHT, FPS = 1920, 1080, 30
PANEL = (100, 238, 1820, 1022)

# Narration-aligned scene boundaries measured from the delivered voice track.
AMAZON_START = 63.60
EVIDENCE_START = 70.50
PEOPLE_START = 82.60
OUTREACH_START = 95.80
LOOP_START = 106.70
END = 158.00

FONT_REGULAR = Path("/System/Library/Fonts/SFNS.ttf")
FONT_BOLD = Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_BOLD if bold else FONT_REGULAR), size)


def extract(timestamp: float, name: str) -> Path:
    path = REFINE / f"{name}.png"
    run(
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{timestamp:.3f}", "-i", str(SOURCE), "-frames:v", "1", str(path),
    )
    return path


def blur_region(image: Image.Image, box: tuple[int, int, int, int], radius: int = 18) -> None:
    crop = image.crop(box).filter(ImageFilter.GaussianBlur(radius))
    image.paste(crop, box[:2])


def privacy_panel(
    image: Image.Image,
    box: tuple[int, int, int, int],
    title: str | None = None,
    subtitle: str | None = None,
) -> None:
    """Mask private capture content with an intentional, opaque product card."""
    blur_region(image, box, 24)
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    draw.rounded_rectangle(
        box,
        radius=18,
        fill=(10, 13, 22, 246),
        outline=(151, 163, 184, 42),
        width=1,
    )
    if title:
        title_font = font(21, True)
        title_box = draw.textbbox((0, 0), title, font=title_font)
        title_width = title_box[2] - title_box[0]
        title_x = box[0] + ((box[2] - box[0] - title_width) // 2)
        title_y = box[1] + ((box[3] - box[1]) // 2) - (26 if subtitle else 12)
        draw.text((title_x, title_y), title, font=title_font, fill=(226, 232, 241, 255))
        if subtitle:
            subtitle_font = font(16)
            subtitle_box = draw.textbbox((0, 0), subtitle, font=subtitle_font)
            subtitle_width = subtitle_box[2] - subtitle_box[0]
            subtitle_x = box[0] + ((box[2] - box[0] - subtitle_width) // 2)
            draw.text(
                (subtitle_x, title_y + 40),
                subtitle,
                font=subtitle_font,
                fill=(153, 164, 184, 255),
            )
    image.alpha_composite(layer)


def draw_authentic_label(image: Image.Image) -> None:
    """Replace the source's inaccurate 'unedited' label on one full frame."""
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    draw.rounded_rectangle((1325, 20, 1915, 120), radius=18, fill=(7, 9, 17, 255))
    draw.ellipse((1360, 62, 1378, 80), fill=(255, 108, 102, 255))
    draw.text(
        (1400, 55),
        "REAL PRODUCT · AUTHENTIC CAPTURE",
        font=font(15, True),
        fill=(186, 190, 207, 255),
    )
    image.alpha_composite(layer)


def authentic_amazon_frame() -> Path:
    # t=59 contains the real Amazon title + Promising assessment. t=65 contains
    # the correctly timed editorial heading. Only the captured app panel is moved.
    product = Image.open(extract(59.0, "amazon-product-source")).convert("RGBA")
    editorial = Image.open(extract(65.0, "amazon-editorial-source")).convert("RGBA")
    editorial.paste(product.crop(PANEL), PANEL[:2])
    draw_authentic_label(editorial)
    path = REFINE / "amazon-aligned.png"
    editorial.convert("RGB").save(path, quality=96)
    return path


def evidence_frame() -> Path:
    source = Image.open(extract(77.7, "evidence-source")).convert("RGBA")
    draw_authentic_label(source)
    path = REFINE / "evidence-aligned.png"
    source.convert("RGB").save(path, quality=96)
    return path


def people_frame() -> Path:
    source = Image.open(extract(85.3, "people-source")).convert("RGBA")
    # Preserve the real 5/5 result and source-evidence explanation while removing
    # identifiable public-search result details from the public video.
    privacy_panel(
        source,
        (350, 704, 1585, 1022),
        "CONTACT IDENTITIES HIDDEN IN PUBLIC DEMO",
        "The live workspace retains each source-backed record.",
    )
    # Replace the overclaiming editorial noun; these are leads, not referrals.
    overlay = Image.new("RGBA", source.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    draw.rounded_rectangle((60, 108, 1080, 228), radius=10, fill=(6, 9, 17, 255))
    draw.text((100, 135), "FIVE SOURCE-BACKED PATHS", font=font(20, True), fill=(102, 224, 195, 255))
    draw.text((100, 174), "Not one fragile lead.", font=font(43, True), fill=(247, 248, 252, 255))
    source.alpha_composite(overlay)
    draw_authentic_label(source)
    path = REFINE / "people-private.png"
    source.convert("RGB").save(path, quality=96)
    return path


def outreach_frame() -> Path:
    # t=96.5 is fully revealed and predates the captured profile-link hover,
    # so there is no browser status URL to conceal outside the app panel.
    source = Image.open(extract(96.5, "outreach-source")).convert("RGBA")
    privacy_panel(source, (395, 332, 905, 468))
    privacy_panel(
        source,
        (395, 492, 1535, 726),
        "RECIPIENT AND EXACT DRAFT HIDDEN FOR PRIVACY",
        "The real product generated a distinct, grounded note.",
    )
    privacy_panel(source, (395, 796, 1535, 1022))
    draw_authentic_label(source)
    path = REFINE / "outreach-private.png"
    source.convert("RGB").save(path, quality=96)
    return path


def label_overlay() -> Path:
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw_authentic_label(image)
    path = REFINE / "authentic-capture-label.png"
    image.save(path)
    return path


def still_clip(image: Path, name: str, duration: float) -> Path:
    output = REFINE / f"{name}.mp4"
    frames = math.ceil(duration * FPS)
    run(
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1", "-i", str(image),
        "-vf", (
            "scale=2048:1152:flags=lanczos,"
            f"zoompan=z='min(zoom+0.000012,1.008)':x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS},"
            "format=yuv420p"
        ),
        "-frames:v", str(frames), "-an", "-c:v", "libx264", "-crf", "14", "-preset", "medium",
        str(output),
    )
    return output


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"Missing source video: {SOURCE}")
    REFINE.mkdir(parents=True, exist_ok=True)

    clips = [
        still_clip(authentic_amazon_frame(), "amazon", EVIDENCE_START - AMAZON_START),
        still_clip(evidence_frame(), "evidence", PEOPLE_START - EVIDENCE_START),
        still_clip(people_frame(), "people", OUTREACH_START - PEOPLE_START),
        still_clip(outreach_frame(), "outreach", LOOP_START - OUTREACH_START),
    ]
    label = label_overlay()
    filter_graph = (
        "[0:v]trim=start=0:end=24.1,setpts=PTS-STARTPTS[intro];"
        f"[0:v]trim=start=24.1:end={AMAZON_START},setpts=PTS-STARTPTS[early-base];"
        f"[1:v]trim=duration={EVIDENCE_START - AMAZON_START},setpts=PTS-STARTPTS[v1];"
        f"[2:v]trim=duration={PEOPLE_START - EVIDENCE_START},setpts=PTS-STARTPTS[v2];"
        f"[3:v]trim=duration={OUTREACH_START - PEOPLE_START},setpts=PTS-STARTPTS[v3];"
        f"[4:v]trim=duration={LOOP_START - OUTREACH_START},setpts=PTS-STARTPTS[v4];"
        f"[0:v]trim=start={LOOP_START}:end=121.5,setpts=PTS-STARTPTS[late-base];"
        f"[0:v]trim=start=121.5:end={END},setpts=PTS-STARTPTS[outro];"
        "[5:v]fps=30,setpts=PTS-STARTPTS,split=2[label-early][label-late];"
        "[early-base][label-early]overlay=0:0:shortest=1:eof_action=repeat:repeatlast=1[early];"
        "[late-base][label-late]overlay=0:0:shortest=1:eof_action=repeat:repeatlast=1[late];"
        "[intro][early][v1][v2][v3][v4][late][outro]concat=n=8:v=1:a=0[v]"
    )
    run(
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(SOURCE),
        *sum((["-i", str(clip)] for clip in clips), []),
        "-framerate", str(FPS), "-loop", "1", "-i", str(label),
        "-filter_complex", filter_graph,
        "-map", "[v]", "-map", "0:a:0", "-t", f"{END:.3f}",
        "-c:v", "libx264", "-crf", "14", "-preset", "slow", "-pix_fmt", "yuv420p",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        "-c:a", "copy", "-metadata", "title=Job Hunt Signal — OpenAI Build Week Demo",
        "-metadata", "comment=Authentic product capture; public identities masked for submission privacy",
        "-movflags", "+faststart", str(OUTPUT),
    )

    # YouTube typically allocates more bitrate to 1440p transcodes. This master
    # upscales clean UI edges with Lanczos; it does not add synthetic detail.
    run(
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(OUTPUT),
        "-vf", "scale=2560:1440:flags=lanczos,format=yuv420p",
        "-c:v", "libx264", "-crf", "15", "-preset", "slow", "-profile:v", "high",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        "-c:a", "copy", "-movflags", "+faststart", str(OUTPUT_1440),
    )
    print(f"Wrote {OUTPUT}")
    print(f"Wrote {OUTPUT_1440}")


if __name__ == "__main__":
    main()
