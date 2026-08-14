"""Preflight an EDL and subtitle file before an expensive render.

Checks source/range integrity, transcript-adjacent cut edges, transition math,
asset existence, declared runtime, and subtitle single-line/readability rules.

Usage:
    python3 helpers/validate_project.py edit/edl.json
    python3 helpers/validate_project.py edit/edl.json --subtitle edit/master.srt
    python3 helpers/validate_project.py edit/edl.json --report edit/preflight.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import ImageFont

from render import (
    PLAYRES_Y,
    XFADE_TRANSITIONS,
    _caption_text_width,
    _contains_cjk,
    _font_path,
    _range_output_duration,
    _transition_duration,
    probe_dimensions,
    resolve_path,
)


@dataclass
class Finding:
    level: str
    code: str
    message: str


def parse_srt_timestamp(value: str) -> float:
    match = re.fullmatch(r"(\d+):(\d+):(\d+)[,.](\d+)", value.strip())
    if not match:
        raise ValueError(f"bad SRT timestamp: {value}")
    h, m, s, ms = map(int, match.groups())
    return h * 3600 + m * 60 + s + ms / (10 ** len(match.group(4)))


def parse_srt(path: Path) -> list[tuple[float, float, list[str]]]:
    blocks = re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").strip())
    cues: list[tuple[float, float, list[str]]] = []
    for block in blocks:
        lines = block.splitlines()
        timing_idx = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_idx is None:
            continue
        left, right = lines[timing_idx].split("-->", 1)
        cues.append((parse_srt_timestamp(left), parse_srt_timestamp(right), lines[timing_idx + 1 :]))
    return cues


def transcript_edges(path: Path) -> tuple[list[float], list[float]]:
    data = json.loads(path.read_text())
    starts, ends = [], []
    for word in data.get("words", []):
        if word.get("type") != "word":
            continue
        if word.get("start") is not None:
            starts.append(float(word["start"]))
        if word.get("end") is not None:
            ends.append(float(word["end"]))
    return starts, ends


def nearest_distance(value: float, candidates: list[float]) -> float:
    return min((abs(value - candidate) for candidate in candidates), default=999.0)


def expected_canvas(source: Path) -> tuple[int, int]:
    width, height = probe_dimensions(source)
    if height > width:
        return round(width / height * 1920) // 2 * 2, 1920
    return 1920, round(height / width * 1920) // 2 * 2


def validate_edl(edl: dict, edit_dir: Path) -> tuple[list[Finding], float, tuple[int, int] | None]:
    findings: list[Finding] = []
    sources = edl.get("sources")
    ranges = edl.get("ranges")
    if not isinstance(sources, dict) or not sources:
        findings.append(Finding("error", "sources", "EDL must contain a non-empty sources object."))
        return findings, 0.0, None
    if not isinstance(ranges, list) or not ranges:
        findings.append(Finding("error", "ranges", "EDL must contain at least one kept range."))
        return findings, 0.0, None

    resolved_sources: dict[str, Path] = {}
    for name, raw_path in sources.items():
        path = resolve_path(str(raw_path), edit_dir)
        resolved_sources[name] = path
        if not path.exists():
            findings.append(Finding("error", "missing-source", f"Source {name!r} does not exist: {path}"))

    canvas = None
    first_existing = next((path for path in resolved_sources.values() if path.exists()), None)
    output = edl.get("output") or {}
    if output.get("width") and output.get("height"):
        canvas = (int(output["width"]), int(output["height"]))
    elif first_existing:
        canvas = expected_canvas(first_existing)
    if len(sources) > 1 and not all(output.get(key) for key in ("width", "height", "fps")):
        findings.append(Finding(
            "error", "output-spec",
            "Multi-source edits must declare output.width, output.height, and output.fps so every segment normalizes identically.",
        ))
    if output:
        try:
            if int(output.get("width", 0)) <= 0 or int(output.get("height", 0)) <= 0:
                raise ValueError
            fps = float(output.get("fps", 0))
            if not 1 <= fps <= 120:
                raise ValueError
            if str(output.get("fit", "cover")) not in {"cover", "contain"}:
                raise ValueError
        except (TypeError, ValueError):
            findings.append(Finding("error", "output-spec", "Output needs positive width/height, fps 1-120, and fit cover/contain."))

    transcript_cache: dict[str, tuple[list[float], list[float]]] = {}
    for index, item in enumerate(ranges):
        prefix = f"range {index:02d}"
        source_name = item.get("source")
        if source_name not in sources:
            findings.append(Finding("error", "unknown-source", f"{prefix} references unknown source {source_name!r}."))
            continue
        try:
            start, end = float(item["start"]), float(item["end"])
            speed = float(item.get("speed", 1.0))
        except (KeyError, TypeError, ValueError):
            findings.append(Finding("error", "bad-range", f"{prefix} needs numeric start, end, and speed."))
            continue
        if start < 0 or end <= start:
            findings.append(Finding("error", "bad-range", f"{prefix} has invalid bounds {start:.3f}-{end:.3f}."))
        if not 0.5 <= speed <= 2.0:
            findings.append(Finding("error", "bad-speed", f"{prefix} speed {speed:.2f} is outside ffmpeg atempo's 0.5-2.0 range."))

        transcript = edit_dir / "transcripts" / f"{source_name}.json"
        if transcript.exists():
            if source_name not in transcript_cache:
                transcript_cache[source_name] = transcript_edges(transcript)
            starts, ends = transcript_cache[source_name]
            # Cut padding is expected, so allow up to 250ms around the nearest word edge.
            if nearest_distance(start, starts + ends) > 0.25:
                findings.append(Finding("warning", "cut-edge", f"{prefix} start {start:.3f}s is not within 250ms of a transcript word edge."))
            if nearest_distance(end, starts + ends) > 0.25:
                findings.append(Finding("warning", "cut-edge", f"{prefix} end {end:.3f}s is not within 250ms of a transcript word edge."))
        else:
            findings.append(Finding("warning", "missing-transcript", f"No cached transcript for {source_name}; cut-edge and subtitle checks are incomplete."))

    transition = edl.get("transition") or {}
    transition_type = str(transition.get("type", "none")).lower()
    if transition_type not in {"none", "hard", "cut"}:
        if transition_type not in XFADE_TRANSITIONS:
            findings.append(Finding("error", "transition", f"Unsupported transition type: {transition_type}"))
        duration = float(transition.get("duration", 0.20))
        if not 0.04 <= duration <= 1.5:
            findings.append(Finding("error", "transition", "Transition duration must be 0.04-1.5 seconds."))
        if any(_range_output_duration(item) <= duration * 2 for item in ranges):
            findings.append(Finding("error", "transition", "Every kept range must be longer than twice the transition duration."))

    for index, overlay in enumerate(edl.get("overlays") or []):
        path = resolve_path(str(overlay.get("file", "")), edit_dir)
        if not path.exists():
            findings.append(Finding("error", "missing-overlay", f"Overlay {index} does not exist: {path}"))
        if float(overlay.get("duration", 0)) <= 0:
            findings.append(Finding("error", "overlay-duration", f"Overlay {index} needs a positive duration."))

    music = edl.get("music") or None
    if music:
        path = resolve_path(str(music.get("file", "")), edit_dir)
        if not path.exists():
            findings.append(Finding("error", "missing-music", f"Music file does not exist: {path}"))
        volume = float(music.get("volume", 0.12))
        if not 0 <= volume <= 1:
            findings.append(Finding("error", "music-volume", "Music volume must be between 0 and 1."))

    total = sum(_range_output_duration(item) for item in ranges)
    total -= _transition_duration(edl) * max(0, len(ranges) - 1)
    declared = edl.get("total_duration_s")
    if declared is not None and abs(float(declared) - total) > 0.08:
        findings.append(Finding("error", "runtime", f"Declared total_duration_s={float(declared):.3f}, calculated={total:.3f}."))
    return findings, total, canvas


def validate_subtitles(
    path: Path,
    canvas: tuple[int, int] | None,
    font_name: str,
    font_size: float,
    max_width_pct: float,
) -> list[Finding]:
    findings: list[Finding] = []
    if not path.exists():
        return [Finding("error", "missing-subtitle", f"Subtitle file does not exist: {path}")]
    cues = parse_srt(path)
    if not cues:
        return [Finding("error", "empty-subtitle", "Subtitle file has no readable cues.")]

    width, height = canvas or (1920, 1080)
    pixel_size = max(8, round(font_size * height / PLAYRES_Y))
    font_path = _font_path(font_name)
    try:
        font = ImageFont.truetype(font_path, pixel_size) if font_path else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()
    max_px = width * max_width_pct / 100.0

    previous_end = -1.0
    for index, (start, end, lines) in enumerate(cues, start=1):
        visible_lines = [line for line in lines if line.strip()]
        text = " ".join(line.strip() for line in visible_lines)
        duration = end - start
        if len(visible_lines) != 1:
            findings.append(Finding("error", "subtitle-lines", f"Cue {index} contains {len(visible_lines)} text lines; exactly one is required."))
        if _caption_text_width(text, font) > max_px:
            findings.append(Finding("error", "subtitle-width", f"Cue {index} exceeds {max_width_pct:.0f}% of frame width: {text!r}"))
        if duration <= 0:
            findings.append(Finding("error", "subtitle-time", f"Cue {index} has non-positive duration."))
        else:
            compact_length = len(text.replace(" ", ""))
            cps = compact_length / duration
            threshold = 12.0 if _contains_cjk(text) else 20.0
            if cps > threshold:
                findings.append(Finding(
                    "warning", "subtitle-speed",
                    f"Cue {index} may be too fast to read ({cps:.1f} chars/s; target ≤{threshold:.0f}).",
                ))
        if start < previous_end - 0.001:
            findings.append(Finding("error", "subtitle-overlap", f"Cue {index} overlaps the previous cue by {previous_end - start:.3f}s."))
        previous_end = max(previous_end, end)
    return findings


def render_report(findings: list[Finding], total: float) -> str:
    errors = sum(item.level == "error" for item in findings)
    warnings = sum(item.level == "warning" for item in findings)
    lines = ["# Video project preflight", "", f"Calculated runtime: **{total:.3f}s**", "", f"Result: **{errors} error(s), {warnings} warning(s)**", ""]
    if not findings:
        lines.append("PASS — no deterministic preflight issues found.")
    else:
        for item in findings:
            lines.append(f"- **{item.level.upper()} [{item.code}]** {item.message}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate an CutCraft EDL and subtitles")
    parser.add_argument("edl", type=Path)
    parser.add_argument("--subtitle", type=Path, default=None)
    parser.add_argument("--font", default="Helvetica")
    parser.add_argument("--font-size", type=float, default=18.0)
    parser.add_argument("--max-width-pct", type=float, default=82.0)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    edl_path = args.edl.resolve()
    if not edl_path.exists():
        sys.exit(f"EDL not found: {edl_path}")
    edl = json.loads(edl_path.read_text())
    findings, total, canvas = validate_edl(edl, edl_path.parent)
    subtitle = args.subtitle
    if subtitle is None and edl.get("subtitles"):
        subtitle = resolve_path(str(edl["subtitles"]), edl_path.parent)
    if subtitle is not None:
        findings.extend(validate_subtitles(subtitle.resolve(), canvas, args.font, args.font_size, args.max_width_pct))

    if args.json:
        print(json.dumps({"runtime_s": total, "findings": [item.__dict__ for item in findings]}, indent=2, ensure_ascii=False))
    else:
        report = render_report(findings, total)
        print(report, end="")
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(report, encoding="utf-8")
    if any(item.level == "error" for item in findings):
        sys.exit(1)


if __name__ == "__main__":
    main()
