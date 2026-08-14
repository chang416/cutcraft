"""Probe source videos once and write a machine-readable inventory.

The report exposes the mismatches that commonly break multi-clip joins:
canvas/orientation, frame rate, HDR transfer, audio sample rate, and channels.

Usage:
    python3 helpers/inventory.py <videos_dir>
    python3 helpers/inventory.py <videos_dir> --json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path


VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".mts", ".m2ts"}


def rate_to_float(value: str | None) -> float | None:
    if not value or value == "0/0":
        return None
    try:
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            return round(float(numerator) / float(denominator), 3)
        return round(float(value), 3)
    except (ValueError, ZeroDivisionError):
        return None


def probe(path: Path) -> dict:
    raw = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ], text=True)
    data = json.loads(raw)
    video = next((stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"), {})
    audio = next((stream for stream in data.get("streams", []) if stream.get("codec_type") == "audio"), {})
    width, height = video.get("width"), video.get("height")
    return {
        "id": path.stem,
        "file": str(path.resolve()),
        "bytes": path.stat().st_size,
        "duration_s": round(float(data.get("format", {}).get("duration", 0.0)), 3),
        "video": {
            "codec": video.get("codec_name"),
            "width": width,
            "height": height,
            "orientation": "portrait" if width and height and height > width else "landscape",
            "fps": rate_to_float(video.get("avg_frame_rate")),
            "pixel_format": video.get("pix_fmt"),
            "color_space": video.get("color_space"),
            "color_transfer": video.get("color_transfer"),
            "hdr": video.get("color_transfer") in {"smpte2084", "arib-std-b67"},
        },
        "audio": {
            "present": bool(audio),
            "codec": audio.get("codec_name"),
            "sample_rate": int(audio["sample_rate"]) if audio.get("sample_rate") else None,
            "channels": audio.get("channels"),
            "layout": audio.get("channel_layout"),
        },
    }


def mismatch_summary(items: list[dict]) -> list[str]:
    warnings: list[str] = []
    fields = {
        "canvas": [(item["video"]["width"], item["video"]["height"]) for item in items],
        "orientation": [item["video"]["orientation"] for item in items],
        "frame rate": [item["video"]["fps"] for item in items],
        "HDR/SDR": ["HDR" if item["video"]["hdr"] else "SDR" for item in items],
        "audio sample rate": [item["audio"]["sample_rate"] for item in items],
        "audio channels": [item["audio"]["channels"] for item in items],
    }
    for label, values in fields.items():
        unique = {json.dumps(value, sort_keys=True) for value in values}
        if len(unique) > 1:
            warnings.append(f"mixed {label}: {values}")
    if any(not item["audio"]["present"] for item in items):
        warnings.append("one or more sources has no audio stream")
    return warnings


def render_text(report: dict) -> str:
    lines = [f"Video inventory: {len(report['sources'])} source(s)"]
    for item in report["sources"]:
        video, audio = item["video"], item["audio"]
        lines.append(
            f"- {Path(item['file']).name}: {item['duration_s']:.2f}s, "
            f"{video['width']}x{video['height']} {video['fps']}fps {video['orientation']}, "
            f"{'HDR' if video['hdr'] else 'SDR'}, audio "
            f"{audio['sample_rate']}Hz/{audio['channels']}ch"
        )
    if report["warnings"]:
        lines.append("Warnings requiring normalization:")
        lines.extend(f"- {warning}" for warning in report["warnings"])
    else:
        lines.append("All probed delivery properties match.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inventory video sources with ffprobe")
    parser.add_argument("videos_dir", type=Path)
    parser.add_argument("--work-dir", dest="edit_dir", type=Path, required=True,
                        help="CutCraft project process directory (過程).")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of the concise report")
    args = parser.parse_args()

    videos_dir = args.videos_dir.resolve()
    if not videos_dir.is_dir():
        sys.exit(f"not a directory: {videos_dir}")
    videos = sorted(path for path in videos_dir.iterdir() if path.is_file() and path.suffix.lower() in VIDEO_EXTS)
    if not videos:
        sys.exit(f"no video files found in {videos_dir}")

    stem_counts = Counter(path.stem for path in videos)
    duplicates = sorted(stem for stem, count in stem_counts.items() if count > 1)
    if duplicates:
        sys.exit(f"duplicate source stems must be renamed before transcription: {', '.join(duplicates)}")

    items = [probe(path) for path in videos]
    report = {"version": 1, "sources": items, "warnings": mismatch_summary(items)}
    edit_dir = args.edit_dir.resolve()
    edit_dir.mkdir(parents=True, exist_ok=True)
    output = edit_dir / "inventory.json"
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_text(report))
        print(f"Saved: {output}")


if __name__ == "__main__":
    main()
