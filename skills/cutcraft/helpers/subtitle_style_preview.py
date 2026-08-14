"""Render a contact sheet that shows exactly how proposed burned subtitles look.

Use after footage inspection but before the one consolidated checkpoint. The
preview renders representative source frames through the same libass style
path as the final video, so font, size, placement, and tight CC background
are reviewable without committing to an edit.

Usage:
    python3 helpers/subtitle_style_preview.py source.mp4 -o edit/verify/subtitle_style.png
    python3 helpers/subtitle_style_preview.py source.mp4 -o preview.png \
      --times 1.0,8.0,15.0 --font "PingFang TC" --font-size 22 \
      --margin-pct 30 --background cc-tight --width 1080 --height 1920
"""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from render import build_subtitle_force_style, build_subtitles_filter, probe_duration, resolve_subtitle_font


def parse_times(value: str | None, duration: float) -> list[float]:
    if value:
        times = [float(item.strip()) for item in value.split(",") if item.strip()]
    else:
        # Show early, middle, and late layout without using the unstable first
        # frame or the tail frame where a clip may have faded out.
        times = [duration * 0.15, duration * 0.50, duration * 0.85]
    return [min(max(0.0, item), max(0.0, duration - 0.04)) for item in times[:6]]


def canvas_filter(width: int | None, height: int | None, fit: str, focus_x: float, focus_y: float) -> str:
    if not width or not height:
        return ""
    if fit == "contain":
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
        )
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}:(iw-ow)*{focus_x:.4f}:(ih-oh)*{focus_y:.4f}"
    )


def render_frame(
    video: Path,
    time_s: float,
    srt_path: Path,
    out_path: Path,
    force_style: str,
    width: int | None,
    height: int | None,
    fit: str,
    focus_x: float,
    focus_y: float,
) -> None:
    filters = [canvas_filter(width, height, fit, focus_x, focus_y)]
    filters = [item for item in filters if item]
    filters.append(build_subtitles_filter(srt_path, force_style))
    subprocess.run([
        "ffmpeg", "-y", "-ss", f"{time_s:.3f}", "-i", str(video),
        "-frames:v", "1", "-vf", ",".join(filters), "-update", "1", str(out_path),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def load_font(size: int) -> ImageFont.ImageFont:
    for candidate in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNS.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def compose_contact_sheet(frame_paths: list[Path], times: list[float], out_path: Path, label: str) -> None:
    images = [Image.open(path).convert("RGB") for path in frame_paths]
    thumb_h = 360
    thumbs: list[Image.Image] = []
    for image in images:
        width = max(1, round(image.width / image.height * thumb_h))
        thumbs.append(image.resize((width, thumb_h), Image.Resampling.LANCZOS))

    gap, pad, header_h, caption_h = 12, 24, 48, 30
    total_w = sum(image.width for image in thumbs) + gap * (len(thumbs) - 1) + pad * 2
    total_h = header_h + thumb_h + caption_h + pad * 2
    sheet = Image.new("RGB", (total_w, total_h), (18, 18, 22))
    draw = ImageDraw.Draw(sheet)
    title_font, time_font = load_font(24), load_font(16)
    draw.text((pad, pad), label, fill=(240, 240, 240), font=title_font)
    x = pad
    for image, time_s in zip(thumbs, times):
        sheet.paste(image, (x, pad + header_h))
        draw.text((x, pad + header_h + thumb_h + 6), f"{time_s:.1f}s", fill=(190, 190, 195), font=time_font)
        x += image.width + gap
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, "PNG", optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render representative subtitle style previews")
    parser.add_argument("video", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--times", default=None, help="Comma-separated source times; default is 15%%, 50%%, 85%%.")
    parser.add_argument("--text", default="字幕樣式預覽", help="Single-line sample caption to render.")
    parser.add_argument("--font", default=None)
    parser.add_argument("--font-size", type=float, default=None)
    parser.add_argument(
        "--style",
        choices=["high-contrast-outline", "bold-overlay", "natural-sentence"],
        default="high-contrast-outline",
    )
    parser.add_argument("--margin-pct", type=float, default=None)
    parser.add_argument("--background", choices=["none", "cc-tight"], default="none")
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument("--fit", choices=["cover", "contain"], default="cover")
    parser.add_argument("--focus-x", type=float, default=0.5)
    parser.add_argument("--focus-y", type=float, default=0.5)
    parser.add_argument("--workers", type=int, default=3, help="Parallel frame renders (default 3).")
    args = parser.parse_args()

    video = args.video.resolve()
    if not video.exists():
        raise SystemExit(f"video not found: {video}")
    if (args.width is None) != (args.height is None):
        parser.error("--width and --height must be supplied together")
    if not 0 <= args.focus_x <= 1 or not 0 <= args.focus_y <= 1:
        parser.error("--focus-x and --focus-y must be between 0 and 1")

    duration = probe_duration(video)
    times = parse_times(args.times, duration)
    font = resolve_subtitle_font(args.style, args.font)
    force_style = build_subtitle_force_style(
        style=args.style,
        margin_pct=args.margin_pct,
        safe_box=args.background == "cc-tight",
        font_name=font,
        font_size=args.font_size,
    )
    label = (
        f"{font} · {args.font_size or ('22' if args.style != 'bold-overlay' else '18')} · "
        f"{args.style} · {args.background} · {args.margin_pct if args.margin_pct is not None else 'default'}%"
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        srt = temp / "preview.srt"
        srt.write_text(f"1\n00:00:00,000 --> 00:00:10,000\n{args.text}\n", encoding="utf-8")
        frame_paths = [temp / f"frame_{index:02d}.png" for index in range(len(times))]

        def render_one(job: tuple[Path, float]) -> None:
            frame, time_s = job
            render_frame(
                video, time_s, srt, frame, force_style, args.width, args.height,
                args.fit, args.focus_x, args.focus_y,
            )
        workers = max(1, min(int(args.workers), len(times)))
        if workers == 1:
            for job in zip(frame_paths, times):
                render_one(job)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(render_one, zip(frame_paths, times)))
        compose_contact_sheet(frame_paths, times, args.output.resolve(), label)

    print(f"subtitle style preview → {args.output.resolve()}")
    print(f"  {label}")


if __name__ == "__main__":
    main()
