#!/usr/bin/env python3
"""Analyze shot changes, silence, black frames, and frozen frames with FFmpeg."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"


def _values(pattern: str, text: str) -> list[float]:
    return [round(float(value), 3) for value in re.findall(pattern, text)]


def parse_scene_times(stderr: str) -> list[float]:
    return _values(r"pts_time:(" + NUMBER + r")", stderr)


def parse_silence(stderr: str) -> list[dict[str, float | None]]:
    starts = _values(r"silence_start:\s*(" + NUMBER + r")", stderr)
    ends = _values(r"silence_end:\s*(" + NUMBER + r")", stderr)
    durations = _values(r"silence_duration:\s*(" + NUMBER + r")", stderr)
    count = max(len(starts), len(ends))
    return [
        {
            "start": starts[index] if index < len(starts) else None,
            "end": ends[index] if index < len(ends) else None,
            "duration": durations[index] if index < len(durations) else None,
        }
        for index in range(count)
    ]


def parse_black(stderr: str) -> list[dict[str, float]]:
    matches = re.findall(
        r"black_start:(" + NUMBER + r")\s+black_end:(" + NUMBER + r")\s+black_duration:(" + NUMBER + r")",
        stderr,
    )
    return [{"start": round(float(a), 3), "end": round(float(b), 3), "duration": round(float(c), 3)} for a, b, c in matches]


def parse_freeze(stderr: str) -> list[dict[str, float | None]]:
    starts = _values(r"freeze_start:\s*(" + NUMBER + r")", stderr)
    ends = _values(r"freeze_end:\s*(" + NUMBER + r")", stderr)
    durations = _values(r"freeze_duration:\s*(" + NUMBER + r")", stderr)
    count = max(len(starts), len(ends), len(durations))
    return [
        {
            "start": starts[index] if index < len(starts) else None,
            "end": ends[index] if index < len(ends) else None,
            "duration": durations[index] if index < len(durations) else None,
        }
        for index in range(count)
    ]


def run_ffmpeg(path: Path, *, scene_threshold: float, silence_db: float, min_silence: float) -> dict:
    scene_cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-map", "0:v:0", "-vf", f"select='gt(scene,{scene_threshold})',showinfo",
        "-an", "-f", "null", "-",
    ]
    diagnostics_cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-vf", "blackdetect=d=0.5:pix_th=0.10,freezedetect=n=-60dB:d=2",
        "-af", f"silencedetect=noise={silence_db}dB:d={min_silence}",
        "-f", "null", "-",
    ]
    scene = subprocess.run(scene_cmd, capture_output=True, text=True)
    diagnostics = subprocess.run(diagnostics_cmd, capture_output=True, text=True)
    if scene.returncode != 0:
        raise RuntimeError(f"scene analysis failed: {scene.stderr[-600:]}")
    if diagnostics.returncode != 0:
        raise RuntimeError(f"media diagnostics failed: {diagnostics.stderr[-600:]}")
    stat = path.stat()
    return {
        "schema_version": 1,
        "source": {"path": str(path.resolve()), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns},
        "settings": {"scene_threshold": scene_threshold, "silence_db": silence_db, "min_silence_s": min_silence},
        "scene_changes_s": parse_scene_times(scene.stderr),
        "silences": parse_silence(diagnostics.stderr),
        "black_frames": parse_black(diagnostics.stderr),
        "frozen_frames": parse_freeze(diagnostics.stderr),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze CutCraft source or final media with FFmpeg")
    parser.add_argument("media", type=Path)
    parser.add_argument("--scene-threshold", type=float, default=0.35)
    parser.add_argument("--silence-db", type=float, default=-35.0)
    parser.add_argument("--min-silence", type=float, default=0.6)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    media = args.media.expanduser().resolve()
    if not media.is_file():
        parser.error(f"media file not found: {media}")
    if not 0.01 <= args.scene_threshold <= 1.0:
        parser.error("--scene-threshold must be between 0.01 and 1.0")
    report = run_ffmpeg(media, scene_threshold=args.scene_threshold, silence_db=args.silence_db, min_silence=args.min_silence)
    output = args.output or media.with_suffix(".analysis.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
