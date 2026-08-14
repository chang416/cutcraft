#!/usr/bin/env python3
"""Generate machine-readable and human-readable QC evidence for a final render."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from analyze_media import run_ffmpeg


def rate(value: str | None) -> float | None:
    if not value or value == "0/0":
        return None
    try:
        left, right = value.split("/", 1) if "/" in value else (value, "1")
        return round(float(left) / float(right), 3)
    except (ValueError, ZeroDivisionError):
        return None


def probe(path: Path) -> dict:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    raw = json.loads(proc.stdout)
    video = next((item for item in raw.get("streams", []) if item.get("codec_type") == "video"), {})
    audio = next((item for item in raw.get("streams", []) if item.get("codec_type") == "audio"), {})
    return {
        "duration_s": round(float(raw.get("format", {}).get("duration", 0)), 3),
        "bytes": path.stat().st_size,
        "video": {
            "codec": video.get("codec_name"), "width": video.get("width"), "height": video.get("height"),
            "fps": rate(video.get("avg_frame_rate")), "pixel_format": video.get("pix_fmt"),
        },
        "audio": {
            "present": bool(audio), "codec": audio.get("codec_name"),
            "sample_rate": int(audio["sample_rate"]) if audio.get("sample_rate") else None,
            "channels": audio.get("channels"),
        },
    }


def findings(report: dict, expected_duration: float | None) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    media = report["media"]
    if not media["video"]["codec"]:
        items.append({"level": "error", "message": "no video stream"})
    if not media["audio"]["present"]:
        items.append({"level": "warning", "message": "no audio stream"})
    if expected_duration is not None:
        drift = abs(media["duration_s"] - expected_duration)
        if drift > max(0.25, expected_duration * 0.005):
            items.append({"level": "error", "message": f"duration differs from expected by {drift:.3f}s"})
    for interval in report["diagnostics"]["black_frames"]:
        if interval["duration"] >= 1.0:
            items.append({"level": "warning", "message": f"black interval {interval['start']:.3f}-{interval['end']:.3f}s"})
    for interval in report["diagnostics"]["frozen_frames"]:
        duration = interval.get("duration")
        if duration is not None and duration >= 2.0:
            items.append({"level": "warning", "message": f"frozen interval near {interval.get('start')}s for {duration:.3f}s"})
    return items


def markdown(report: dict) -> str:
    media = report["media"]
    lines = [
        "# CutCraft quality report", "",
        f"- File: `{report['file']}`",
        f"- Duration: {media['duration_s']:.3f}s",
        f"- Video: {media['video']['codec']} {media['video']['width']}×{media['video']['height']} @ {media['video']['fps']} fps ({media['video']['pixel_format']})",
        f"- Audio: {media['audio']['codec'] or 'none'} / {media['audio']['sample_rate'] or '-'} Hz / {media['audio']['channels'] or '-'} ch",
        f"- Scene changes: {len(report['diagnostics']['scene_changes_s'])}",
        f"- Silence intervals: {len(report['diagnostics']['silences'])}",
        f"- Black intervals: {len(report['diagnostics']['black_frames'])}",
        f"- Frozen intervals: {len(report['diagnostics']['frozen_frames'])}",
        "", "## Findings", "",
    ]
    if report["findings"]:
        lines.extend(f"- [{item['level'].upper()}] {item['message']}" for item in report["findings"])
    else:
        lines.append("- No automatic QC findings.")
    lines.extend(["", "> Automatic checks support, but do not replace, visual and listening review.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate CutCraft final-render QC evidence")
    parser.add_argument("media", type=Path)
    parser.add_argument("--expected-duration", type=float)
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args()
    media_path = args.media.expanduser().resolve()
    if not media_path.is_file():
        parser.error(f"file not found: {media_path}")
    output_dir = (args.work_dir.expanduser().resolve() / "verify") if args.work_dir else media_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": 1,
        "file": str(media_path),
        "media": probe(media_path),
        "diagnostics": run_ffmpeg(media_path, scene_threshold=0.35, silence_db=-45, min_silence=2.0),
    }
    report["findings"] = findings(report, args.expected_duration)
    json_path = output_dir / "quality_report.json"
    md_path = output_dir / "quality_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(markdown(report), encoding="utf-8")
    print(f"Saved: {md_path}")
    return 1 if any(item["level"] == "error" for item in report["findings"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
