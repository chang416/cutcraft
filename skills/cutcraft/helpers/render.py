"""Render a video from an EDL.

Implements the CutCraft render pipeline in the correct order:

  1. Per-segment normalize/extract with color grade + 30ms audio fades
  2. Lossless hard-cut concat, or opt-in xfade/acrossfade assembly
  3. Overlay animations/inserts, mix optional SFX and ducked background music,
     then apply subtitles LAST → final.mp4

Optionally builds a master SRT from per-source transcripts plus speed- and
transition-aware output offsets. Cues are measured against the selected font
and real canvas width, and libass wrapping is disabled to guarantee one line.

Usage:
    python helpers/render.py <edl.json> -o final.mp4
    python helpers/render.py <edl.json> -o preview.mp4 --preview
    python helpers/render.py <edl.json> -o final.mp4 --build-subtitles
    python helpers/render.py <edl.json> -o final.mp4 --no-subtitles
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import ImageFont

try:
    from grade import get_preset, auto_grade_for_clip  # same directory
except Exception:
    def get_preset(name: str) -> str:
        return ""

    def auto_grade_for_clip(video, start=0.0, duration=None, verbose=False):  # type: ignore
        return "eq=contrast=1.03:saturation=0.98", {}


# -------- Subtitle styles -----------------------------------------------------
#
# MarginV is NOT taste — it is a platform/content safe-zone rule, and the right
# value depends on WHAT the caption needs to clear:
#
#   - Vertical/social (TikTok, IG Reels, Shorts): the platform's own UI chrome
#     (caption, username, music, right-rail actions) covers roughly the bottom
#     ~25-30% of a 1080x1920 frame. That's what bold-overlay's default (90) is
#     calibrated for. Do not drop this below ~75 without a specific reason.
#   - Landscape/non-social (documentary, education, screen recordings): there
#     is usually no platform UI to avoid, and parking captions 30% up from the
#     bottom reads as "floating in the middle" of the frame. YouTube-CC-style
#     placement — bottom 10-15% of the frame — is the expectation instead.
#
# libass auto-scales the render canvas relative to PlayResY=288 (empirically
# confirmed: MarginV=90 lands the caption baseline ~31% up from the bottom
# regardless of actual output resolution), so `MarginV = pct * 288` converts a
# target "percent up from the bottom" into the units force_style expects.
PLAYRES_Y = 288

SUB_FORCE_STYLE = (
    "FontName=Helvetica,FontSize=18,Bold=1,"
    "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H00000000,"
    "BorderStyle=1,Outline=2,Shadow=0,"
    "Alignment=2,MarginV=90"
)

OUTLINE_DEFAULT_FONTS = (
    "Noto Serif TC SemiBold",
    "PingFang TC Semibold",
    "Noto Sans TC",
    "Helvetica",
)

GENERAL_DEFAULT_FONTS = (
    "Noto Sans TC",
    "PingFang TC",
    "Noto Sans CJK TC",
    "Heiti TC",
    "Helvetica",
)


def resolve_subtitle_font(style: str, requested: str | None = None) -> str:
    """Choose an installed high-contrast caption font without requiring a download.

    The reference treatment uses a sturdy white glyph with a dark contour. For
    Traditional Chinese, a semi-bold serif face gives that editorial/short-form
    look; fall back to a clean CJK sans before using Helvetica on machines that
    do not have the preferred local font.
    """
    if requested:
        return requested
    candidates = OUTLINE_DEFAULT_FONTS if style == "high-contrast-outline" else GENERAL_DEFAULT_FONTS
    for candidate in candidates:
        try:
            family = subprocess.check_output(
                ["fc-match", "-f", "%{family}", candidate], text=True
            ).strip().lower().replace(" ", "")
            probe = candidate.lower().replace(" ", "").replace("semibold", "")
            if probe and probe in family.replace("semibold", ""):
                return candidate
        except Exception:
            continue
    return "Helvetica"


def margin_v_for_pct(pct: float) -> int:
    """Convert a target 'percent up from the bottom edge' into a MarginV value."""
    return round(pct / 100.0 * PLAYRES_Y)


def build_subtitle_force_style(
    style: str = "high-contrast-outline",
    margin_pct: float | None = None,
    safe_box: bool = False,
    font_name: str | None = None,
    font_size: float | None = None,
) -> str:
    """Build a force_style string for the `subtitles` filter.

    `style`: "high-contrast-outline" (default: white, dark contour, small
      shadow, no box), "bold-overlay", or "natural-sentence".
    `margin_pct`: override the vertical position as a percent up from the
      bottom edge. If omitted, uses the style's default (30% for
      high-contrast-outline / bold-overlay's platform-UI safe zone, 12.5% — the middle of the
      requested 10-15% band — for natural-sentence).
    `safe_box`: if the target zone turns out to overlap real content on a
      given video (check with a sampled frame before rendering), fall back to
      a semi-transparent YouTube-CC-style background box instead of moving
      the text — BorderStyle=3 with a translucent BackColour, rather than
      pushing captions somewhere else in the frame.
    `font_name`: override the font (see helpers/list_fonts.py for what's
      installed). The high-contrast default chooses an installed CJK-capable
      editorial face when available.
    """
    font_name = resolve_subtitle_font(style, font_name)
    if style == "natural-sentence":
        font, size, bold = (font_name or "Helvetica"), (font_size or 22), 0
        default_pct = 12.5
    elif style == "high-contrast-outline":
        # This mirrors the supplied reference: opaque white glyphs, a distinct
        # near-black contour, then a very short soft shadow. The contour creates
        # local contrast on both bright and dark footage without a banner.
        font, size, bold = font_name, (font_size or 22), 1
        default_pct = 30.0
    else:
        font, size, bold = (font_name or "Helvetica"), (font_size or 18), 1
        default_pct = 30.0

    pct = margin_pct if margin_pct is not None else default_pct
    margin_v = margin_v_for_pct(pct)

    if safe_box:
        # BorderStyle=3 draws a CC-style box behind the text. Outline=1 is the
        # box padding in ASS script pixels: only a few rendered pixels at
        # delivery resolution, intentionally almost flush with the glyphs so
        # the background improves contrast without masking the frame.
        # &H4D alpha is roughly 70% opaque black (ASS alpha is inverse).
        parts = (
            f"FontName={font},FontSize={size},Bold={bold},"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H4D000000,"
            "BorderStyle=3,Outline=1,Shadow=0,"
            f"Alignment=2,MarginV={margin_v},WrapStyle=2"
        )
    elif style == "high-contrast-outline":
        parts = (
            f"FontName={font},FontSize={size},Bold={bold},"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H00000000,"
            "BorderStyle=1,Outline=2.6,Shadow=1.1,Blur=0.35,"
            f"Alignment=2,MarginV={margin_v},WrapStyle=2"
        )
    else:
        parts = (
            f"FontName={font},FontSize={size},Bold={bold},"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H00000000,"
            "BorderStyle=1,Outline=2,Shadow=0,"
            f"Alignment=2,MarginV={margin_v},WrapStyle=2"
        )
    return parts


# -------- Helpers ------------------------------------------------------------


def run(cmd: list[str], quiet: bool = False) -> None:
    if not quiet:
        print(f"  $ {' '.join(str(c) for c in cmd[:6])}{' …' if len(cmd) > 6 else ''}")
    subprocess.run(cmd, check=True)


def resolve_grade_filter(grade_field: str | None) -> str:
    """The EDL's 'grade' field can be a preset name, a raw ffmpeg filter, or 'auto'.

    Returns the filter string to embed into the per-segment -vf chain.
    For 'auto', returns the sentinel "__AUTO__" which is resolved per-segment.
    """
    if not grade_field:
        return ""
    if grade_field == "auto":
        return "__AUTO__"
    # Preset names are short identifiers, filter strings contain '=' or ','.
    if re.fullmatch(r"[a-zA-Z0-9_\-]+", grade_field):
        try:
            return get_preset(grade_field)
        except KeyError:
            print(f"warning: unknown preset '{grade_field}', using as raw filter")
            return grade_field
    return grade_field


def resolve_path(maybe_path: str, base: Path) -> Path:
    """Resolve a path that may be absolute or relative to `base`."""
    p = Path(maybe_path)
    if p.is_absolute():
        return p
    return (base / p).resolve()


def video_map_label(has_video_filter: bool) -> str:
    """Return an ffmpeg map target for filtered or direct source video.

    Bracketed names are filter-graph labels; a direct source stream must use
    ffmpeg's stream-specifier syntax instead.  This matters for silent,
    screen-led edits where there are intentionally no overlays or subtitles.
    """
    return "[outv]" if has_video_filter else "0:v"


# -------- HDR → SDR tone mapping (HLG / PQ sources) --------------------------
#
# iPhone defaults to HLG HDR in Rec.2020 (and many mirrorless cameras ship PQ).
# If the source is HDR and we only downconvert bit depth (yuv420p10le → yuv420p)
# without tone-mapping, the output is 8-bit but still carries HLG/PQ transfer
# metadata. Players that honor the metadata (screen recorders, most social
# upload re-encodes) interpret 8-bit values in an HDR container and the result
# looks oversaturated / blown out. QuickTime on macOS can hide this locally —
# screen recording and uploaded renders cannot.
#
# Fix: detect HDR via color_transfer and prepend a zscale+tonemap chain to the
# vf graph so the output is clean Rec.709 SDR.

HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}  # PQ (HDR10) and HLG

TONEMAP_CHAIN = (
    "zscale=t=linear:npl=100,"
    "format=gbrpf32le,"
    "zscale=p=bt709,"
    "tonemap=tonemap=hable:desat=0,"
    "zscale=t=bt709:m=bt709:r=tv,"
    "format=yuv420p"
)


def is_hdr_source(video: Path) -> bool:
    """Return True if the source uses a PQ or HLG transfer function."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=color_transfer",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip() in HDR_TRANSFERS
    except subprocess.CalledProcessError:
        return False


def is_portrait_source(video: Path) -> bool:
    """Return True if the video's height > width (portrait / vertical)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, check=True,
        )
        w, h = map(int, out.stdout.strip().split(","))
        return h > w
    except Exception:
        return False


# -------- Per-segment extraction (Rule 2 + Rule 3) --------------------------


def atempo_chain(speed: float) -> str:
    """ffmpeg's atempo is only valid in [0.5, 2.0]; chain stages for anything else.

    2.8x -> "atempo=2.0,atempo=1.4"; 0.3x -> "atempo=0.5,atempo=0.6".
    """
    if speed == 1.0:
        return ""
    stages: list[float] = []
    remaining = float(speed)
    while remaining > 2.0:
        stages.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        stages.append(0.5)
        remaining /= 0.5
    stages.append(remaining)
    return ",".join(f"atempo={s:.4f}" for s in stages)


def extract_segment(
    source: Path,
    seg_start: float,
    duration: float,
    grade_filter: str,
    out_path: Path,
    preview: bool = False,
    draft: bool = False,
    speed: float = 1.0,
    output_spec: dict | None = None,
    gain_db: float = 0.0,
    mute: bool = False,
) -> None:
    """Extract a cut range as its own MP4 with grade + 30ms audio fades baked in.

    `-ss` before `-i` for fast accurate seeking. Scale to 1080p from 4K.
    Portrait sources (height > width) are scaled by height to preserve orientation.

    `speed` (default 1.0) applies a pitch-preserving tempo change: video via
    `setpts=PTS/speed`, audio via `atempo=speed`. speed > 1.0 speeds the
    segment up (use for segments reading notably slower than the rest of the
    cut, to even out pacing — not a substitute for a real re-take).
    ffmpeg's `atempo` is only valid in [0.5, 2.0]; chain multiple stages if
    a caller ever needs more range.

    Quality ladder:
      - final (default): 1080p libx264 fast CRF 20
      - preview:         1080p libx264 medium CRF 22 (evaluable for QC)
      - draft:           720p libx264 ultrafast CRF 28 (cut-point check only)
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    output_spec = output_spec or {}
    target_width = output_spec.get("width")
    target_height = output_spec.get("height")
    fit = str(output_spec.get("fit", "cover"))
    focus_x = min(1.0, max(0.0, float(output_spec.get("focus_x", 0.5))))
    focus_y = min(1.0, max(0.0, float(output_spec.get("focus_y", 0.5))))
    if target_width and target_height:
        w, h = int(target_width), int(target_height)
        if draft:
            factor = min(1.0, 1280 / max(w, h))
            w, h = max(2, round(w * factor) // 2 * 2), max(2, round(h * factor) // 2 * 2)
        if fit == "contain":
            scale = (
                f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
            )
        else:
            scale = (
                f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h}:(iw-ow)*{focus_x:.4f}:(ih-oh)*{focus_y:.4f}"
            )
    else:
        portrait = is_portrait_source(source)
        if draft:
            scale = "scale=-2:1280" if portrait else "scale=1280:-2"
        else:
            scale = "scale=-2:1920" if portrait else "scale=1920:-2"
    fps = float(output_spec.get("fps") or probe_fps(source))

    vf_parts: list[str] = []
    if is_hdr_source(source):
        vf_parts.append(TONEMAP_CHAIN)
    vf_parts.append(scale)
    if grade_filter:
        vf_parts.append(grade_filter)
    if speed != 1.0:
        vf_parts.append(f"setpts=PTS/{speed:.4f}")
    vf = ",".join(vf_parts)

    # Fades are computed on the POST-speed duration — atempo runs first so the
    # 30ms window lands on the actual output edges, not the pre-speed ones.
    out_duration = duration / speed
    fade_out_start = max(0.0, out_duration - 0.03)
    af_parts: list[str] = []
    if speed != 1.0:
        af_parts.append(atempo_chain(speed))
    if mute:
        af_parts.append("volume=0")
    elif gain_db:
        af_parts.append(f"volume={gain_db:.2f}dB")
    af_parts.append(f"afade=t=in:st=0:d=0.03,afade=t=out:st={fade_out_start:.3f}:d=0.03")
    af = ",".join(p for p in af_parts if p)

    if draft:
        preset, crf = "ultrafast", "28"
    elif preview:
        preset, crf = "medium", "22"
    else:
        preset, crf = "fast", "20"

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{seg_start:.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(source),
        "-vf", vf,
        "-af", af,
        "-c:v", "libx264", "-preset", preset, "-crf", crf,
        "-pix_fmt", "yuv420p", "-r", f"{fps:.3f}",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def extract_all_segments(
    edl: dict,
    edit_dir: Path,
    preview: bool,
    draft: bool = False,
    workers: int = 1,
) -> list[Path]:
    """Extract every EDL range into edit_dir/clips_graded/seg_NN.mp4.
    Returns the ordered list of segment paths.

    If the EDL `grade` is "auto", analyze each segment range with
    `auto_grade_for_clip` and apply a per-segment subtle correction.
    Otherwise, apply the same preset/raw filter to every segment.
    """
    resolved = resolve_grade_filter(edl.get("grade"))
    is_auto = resolved == "__AUTO__"
    clips_dir = edit_dir / (
        "clips_draft" if draft else ("clips_preview" if preview else "clips_graded")
    )
    clips_dir.mkdir(parents=True, exist_ok=True)

    ranges = edl["ranges"]
    sources = edl["sources"]
    output_spec = edl.get("output") or {}

    jobs: list[tuple[Path, Path, float, float, str, float, dict, float, bool]] = []
    print(f"extracting {len(ranges)} segment(s) → {clips_dir.name}/")
    if is_auto:
        print("  (auto-grade per segment: analyzing each range)")
    for i, r in enumerate(ranges):
        src_name = r["source"]
        src_path = resolve_path(sources[src_name], edit_dir)
        start = float(r["start"])
        end = float(r["end"])
        duration = end - start
        out_path = clips_dir / f"seg_{i:02d}_{src_name}.mp4"

        # A per-range `grade` (preset name or raw filter) overrides the project
        # grade for that one segment — the front-end exposes this as a per-clip
        # look, and it costs nothing here because grading is already per-segment.
        if r.get("grade"):
            seg_filter = resolve_grade_filter(str(r["grade"]))
            if seg_filter == "__AUTO__":
                seg_filter, _stats = auto_grade_for_clip(
                    src_path, start=start, duration=duration, verbose=False)
        elif is_auto:
            seg_filter, _stats = auto_grade_for_clip(src_path, start=start, duration=duration, verbose=False)
        else:
            seg_filter = resolved

        speed = float(r.get("speed", 1.0))
        gain_db = float(r.get("gain_db", 0.0))
        mute = bool(r.get("mute", False))

        note = r.get("beat") or r.get("note") or ""
        speed_note = f"  speed={speed:.2f}x" if speed != 1.0 else ""
        audio_note = "  muted" if mute else (f"  {gain_db:+.1f}dB" if gain_db else "")
        print(f"  [{i:02d}] {src_name}  {start:7.2f}-{end:7.2f}  ({duration:5.2f}s){speed_note}{audio_note}  {note}")
        if is_auto or r.get("grade"):
            print(f"        grade: {seg_filter or '(none)'}")
        range_output_spec = dict(output_spec)
        for key in ("fit", "focus_x", "focus_y"):
            if key in r:
                range_output_spec[key] = r[key]
        jobs.append((src_path, out_path, start, duration, seg_filter, speed,
                     range_output_spec, gain_db, mute))

    def run_job(job: tuple[Path, Path, float, float, str, float, dict, float, bool]) -> Path:
        (src_path, out_path, start, duration, seg_filter, speed,
         range_output_spec, gain_db, mute) = job
        extract_segment(
            src_path, start, duration, seg_filter, out_path,
            preview=preview, draft=draft, speed=speed, output_spec=range_output_spec,
            gain_db=gain_db, mute=mute,
        )
        return out_path

    # Every job reads immutable sources and writes one unique segment filename.
    # This is intentionally the only render-stage parallelism: EDL selection,
    # concat, subtitle timing, compositing, and final QC depend on ordered data.
    effective_workers = max(1, min(int(workers), len(jobs)))
    if effective_workers == 1:
        return [run_job(job) for job in jobs]
    print(f"  running {effective_workers} independent segment extracts in parallel")
    with ThreadPoolExecutor(max_workers=effective_workers) as pool:
        return list(pool.map(run_job, jobs))


# -------- Lossless concat ----------------------------------------------------


def concat_segments(segment_paths: list[Path], out_path: Path, edit_dir: Path) -> None:
    """Lossless concat via the concat demuxer. No re-encode."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    concat_list = edit_dir / "_concat.txt"
    concat_list.write_text("".join(f"file '{p.resolve()}'\n" for p in segment_paths))

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        "-movflags", "+faststart",
        str(out_path),
    ]
    print(f"concat → {out_path.name}")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    concat_list.unlink(missing_ok=True)


XFADE_TRANSITIONS = {
    "fade", "fadeblack", "fadewhite", "dissolve", "wipeleft", "wiperight",
    "wipeup", "wipedown", "slideleft", "slideright", "slideup", "slidedown",
    "smoothleft", "smoothright", "smoothup", "smoothdown", "circleopen",
    "circleclose", "pixelize",
}


def probe_duration(path: Path) -> float:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ], text=True).strip()
    return float(out)


def probe_dimensions(path: Path) -> tuple[int, int]:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path),
    ], text=True).strip()
    width, height = map(int, out.split(","))
    return width, height


def probe_fps(path: Path) -> float:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=avg_frame_rate", "-of", "csv=p=0", str(path),
    ], text=True).strip()
    if "/" in out:
        numerator, denominator = out.split("/", 1)
        return float(numerator) / max(1.0, float(denominator))
    return float(out)


def has_audio_stream(path: Path) -> bool:
    out = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=index", "-of", "csv=p=0", str(path),
    ], capture_output=True, text=True)
    return out.returncode == 0 and bool(out.stdout.strip())


def concat_segments_with_transition(
    segment_paths: list[Path], out_path: Path, transition: dict
) -> None:
    """Join all boundaries with one deliberate xfade/acrossfade style.

    Hard cuts still use concat_segments() and stream-copy. A transition is an
    opt-in creative effect and necessarily re-encodes the assembled base once.
    Keep it short and use only at silence/non-speech handles.
    """
    if len(segment_paths) < 2:
        run(["ffmpeg", "-y", "-i", str(segment_paths[0]), "-c", "copy", str(out_path)], quiet=True)
        return

    kind = str(transition.get("type", "fade")).lower()
    if kind not in XFADE_TRANSITIONS:
        raise ValueError(f"unsupported transition '{kind}'; choose one of {sorted(XFADE_TRANSITIONS)}")
    duration = float(transition.get("duration", 0.20))
    if not 0.04 <= duration <= 1.5:
        raise ValueError("transition duration must be between 0.04s and 1.5s")

    durations = [probe_duration(p) for p in segment_paths]
    if any(d <= duration * 2 for d in durations):
        raise ValueError("every segment must be longer than twice the transition duration")

    inputs: list[str] = []
    for path in segment_paths:
        inputs += ["-i", str(path)]

    filters: list[str] = []
    current_v = "[0:v]"
    current_a = "[0:a]"
    current_duration = durations[0]
    for i in range(1, len(segment_paths)):
        offset = current_duration - duration
        next_v = f"[vx{i}]"
        next_a = f"[ax{i}]"
        filters.append(
            f"{current_v}[{i}:v]xfade=transition={kind}:duration={duration:.3f}:"
            f"offset={offset:.3f}{next_v}"
        )
        filters.append(
            f"{current_a}[{i}:a]acrossfade=d={duration:.3f}:c1=tri:c2=tri{next_a}"
        )
        current_v, current_a = next_v, next_a
        current_duration += durations[i] - duration

    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(filters),
        "-map", current_v, "-map", current_a,
        "-c:v", "libx264", "-preset", "fast", "-crf", "16",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", str(out_path),
    ]
    print(f"transition concat ({kind}, {duration:.2f}s) → {out_path.name}")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


# -------- Master SRT (Rule 5) ------------------------------------------------


PUNCT_BREAK = set(".,!?;:")

# Pure filler — dropped entirely in "normal" mode, kept in "verbatim".
FILLER_WORDS = {"uh", "um", "erm", "ah", "eh", "hmm", "mm", "uhh", "umm"}


def clean_disfluency_word(text: str) -> str | None:
    """Clean a single transcript word token for 'normal' subtitle mode.

    Returns None if the token is pure filler (drop it from the caption
    entirely). Otherwise returns the cleaned text: for a self-repair token
    (Scribe writes these as one hyphenated word, e.g. 'pa-participation',
    'you-youth', 'neutral-neutrality,') returns just the completed word.

    Self-repair detection: split on the first hyphen: if the part before it
    is (case-insensitively) a prefix of the part after, it's a stutter/
    restart, not a real compound word. Real compounds fail this test
    ('decision' is not a prefix of 'making', 'open' is not a prefix of
    'minded', 'long' is not a prefix of 'standing') and pass through
    unchanged.
    """
    stripped = text.strip()
    if not stripped:
        return stripped

    bare = re.sub(r"[^\w]", "", stripped).lower()
    if bare in FILLER_WORDS:
        return None

    if "-" in stripped:
        idx = stripped.index("-")
        before_bare = re.sub(r"[^\w]", "", stripped[:idx]).lower()
        after_raw = stripped[idx + 1:]
        after_bare = re.sub(r"[^\w]", "", after_raw).lower()
        if before_bare and after_bare.startswith(before_bare) and after_raw:
            # Self-repair: keep only the completed word. Carry over a
            # sentence-initial capital if the original token had one.
            if stripped[0].isupper() and after_raw[0].islower():
                after_raw = after_raw[0].upper() + after_raw[1:]
            return after_raw

    return stripped


def _srt_timestamp(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _words_in_range(transcript: dict, t_start: float, t_end: float) -> list[dict]:
    out: list[dict] = []
    for w in transcript.get("words", []):
        if w.get("type") != "word":
            continue
        ws = w.get("start")
        we = w.get("end")
        if ws is None or we is None:
            continue
        if we <= t_start or ws >= t_end:
            continue
        out.append(w)
    return out


def _range_speed(r: dict) -> float:
    return float(r.get("speed", 1.0))


def _range_output_duration(r: dict) -> float:
    """Kept-segment duration as it appears on the OUTPUT timeline (post-speed)."""
    return (float(r["end"]) - float(r["start"])) / _range_speed(r)


def _to_output_time(source_time: float, r: dict) -> float:
    """Map a timestamp inside a source range to its position on the output
    timeline, relative to the start of this range (speed-aware)."""
    return max(0.0, source_time - float(r["start"])) / _range_speed(r)


def _apply_subtitle_mode(words_in_seg: list[dict], mode: str) -> list[dict]:
    """'verbatim': pass through unchanged. 'normal': drop pure-filler words and
    clean self-repair tokens down to the completed word (see
    clean_disfluency_word). Returns a new list; never mutates the input."""
    if mode != "normal":
        return words_in_seg
    cleaned: list[dict] = []
    for w in words_in_seg:
        raw = (w.get("text") or "").strip()
        if not raw:
            continue
        text = clean_disfluency_word(raw)
        if text is None:
            continue
        w2 = dict(w)
        w2["text"] = text
        cleaned.append(w2)
    return cleaned


def _font_path(font_name: str) -> str | None:
    """Resolve the same fontconfig font that libass will use."""
    try:
        out = subprocess.check_output(
            ["fc-match", "-f", "%{file}\n", font_name], text=True
        ).strip().splitlines()
        return out[0] if out and Path(out[0]).exists() else None
    except Exception:
        return None


def _escape_subtitles_filter_path(path: Path) -> str:
    return str(path.resolve()).replace(":", r"\:").replace("'", r"\'")


def build_subtitles_filter(subtitles_path: Path, force_style: str) -> str:
    """Build one libass filter and explicitly expose the selected font folder.

    macOS FFmpeg builds may use CoreText inside libass even though CutCraft uses
    fontconfig to inspect installed fonts. Passing the resolved font directory
    keeps preview and final rendering on the same actual font instead of
    silently falling back to Helvetica and producing tofu boxes for CJK text.
    """
    parts = [f"subtitles='{_escape_subtitles_filter_path(subtitles_path)}'"]
    match = re.search(r"(?:^|,)FontName=([^,]+)", force_style)
    if match:
        font_path = _font_path(match.group(1).strip())
        if font_path:
            font_dir = _escape_subtitles_filter_path(Path(font_path).parent)
            parts.append(f"fontsdir='{font_dir}'")
    parts.append(f"force_style='{force_style}'")
    return ":".join(parts)


def _contains_cjk(text: str) -> bool:
    return any(
        "\u3400" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff" or "\uac00" <= ch <= "\ud7af"
        for ch in text
    )


def _strip_caption_edge_punctuation(text: str) -> str:
    """Remove punctuation only at a subtitle cue's visible start and end.

    ASR commonly attaches quotation marks, commas, sentence marks, or ellipses
    to the first/last token in a cue. Those marks look like a dangling visual
    fragment when each cue appears independently, so retain punctuation inside
    a cue but trim Unicode punctuation from both visible edges.
    """
    text = text.strip()
    while text and unicodedata.category(text[0]).startswith("P"):
        text = text[1:].lstrip()
    while text and unicodedata.category(text[-1]).startswith("P"):
        text = text[:-1].rstrip()
    return text


def _join_caption_words(words: list[dict]) -> str:
    """Join Latin words with spaces without injecting spaces between CJK tokens."""
    result = ""
    for w in words:
        token = (w.get("text") or "").strip()
        if not token:
            continue
        if result and not (_contains_cjk(result[-1]) and _contains_cjk(token[0])):
            result += " "
        result += token
    result = re.sub(r"\s+([,.;:!?，。！？；：])", r"\1", result)
    return _strip_caption_edge_punctuation(result)


def _transition_duration(edl: dict) -> float:
    transition = edl.get("transition") or {}
    if str(transition.get("type", "none")).lower() in {"none", "hard", "cut"}:
        return 0.0
    return max(0.0, float(transition.get("duration", 0.20)))


def _apply_caption_case(text: str, case_mode: str) -> str:
    if case_mode == "upper":
        return text.upper()
    if case_mode == "title":
        return text.title()
    return text


def _caption_text_width(text: str, font: ImageFont.ImageFont) -> float:
    try:
        box = font.getbbox(text)
        return float(box[2] - box[0])
    except Exception:
        return float(len(text) * 10)


def _split_oversize_cjk_token(
    word: dict,
    font: ImageFont.ImageFont,
    max_px: float,
    case_mode: str,
) -> list[dict]:
    """Split a long CJK ASR token into timed pieces that each fit one line.

    Some ASR responses return an entire Chinese phrase as one "word". Failing
    or shrinking the whole subtitle is unnecessary; interpolate timing across
    characters so the normal cue chunker can work at a readable width.
    """
    text = (word.get("text") or "").strip()
    rendered = _apply_caption_case(text, case_mode)
    if not _contains_cjk(text) or _caption_text_width(rendered, font) <= max_px:
        return [word]

    pieces: list[tuple[int, int, str]] = []
    start_index = 0
    current = ""
    for index, char in enumerate(text):
        candidate = current + char
        if current and _caption_text_width(_apply_caption_case(candidate, case_mode), font) > max_px:
            pieces.append((start_index, index, current))
            start_index = index
            current = char
        else:
            current = candidate
    if current:
        pieces.append((start_index, len(text), current))

    word_start = float(word.get("start", 0.0))
    word_end = float(word.get("end", word_start))
    span = max(0.0, word_end - word_start)
    result: list[dict] = []
    for char_start, char_end, piece in pieces:
        item = dict(word)
        item["text"] = piece
        item["start"] = word_start + span * char_start / max(1, len(text))
        item["end"] = word_start + span * char_end / max(1, len(text))
        result.append(item)
    return result


def _finalize_caption_entries(
    entries: list[tuple[float, float, str]], min_duration: float
) -> list[tuple[float, float, str]]:
    """Give cues enough hold time without allowing cue overlap."""
    entries.sort(key=lambda e: e[0])
    out: list[tuple[float, float, str]] = []
    for i, (start, end, text) in enumerate(entries):
        next_start = entries[i + 1][0] if i + 1 < len(entries) else None
        desired_end = max(end, start + min_duration)
        if next_start is not None:
            # Back-to-back cues are easier to read than deliberate flashing
            # gaps, especially when one fast CJK ASR token had to be split.
            desired_end = max(start + 0.001, min(desired_end, next_start))
        out.append((start, desired_end, text))
    return out


def build_master_srt_configured(
    edl: dict,
    edit_dir: Path,
    out_path: Path,
    *,
    mode: str = "normal",
    style: str = "high-contrast-outline",
    font_name: str | None = None,
    font_size: float | None = None,
    canvas_width: int = 1920,
    canvas_height: int = 1080,
    max_width_pct: float = 82.0,
    max_words: int | None = None,
    case_mode: str | None = None,
) -> None:
    """Build pixel-aware, single-line subtitle cues on the output timeline.

    Chunking stops before the rendered text exceeds max_width_pct of the
    canvas. Combined with libass WrapStyle=2, this prevents accidental second
    and third lines instead of merely hoping a word-count limit will fit.
    """
    if not 25.0 <= max_width_pct <= 95.0:
        raise ValueError("subtitle max width must be between 25% and 95%")

    sentence_style = style in {"natural-sentence", "high-contrast-outline"}
    natural = sentence_style
    chosen_size = float(font_size or (22 if sentence_style else 18))
    word_cap = int(max_words or (7 if sentence_style else 2))
    case_mode = case_mode or ("natural" if sentence_style else "upper")
    font_name = resolve_subtitle_font(style, font_name)
    if word_cap < 1:
        raise ValueError("subtitle max words must be at least 1")

    # libass scales FontSize from a 288-high script canvas to output height.
    pixel_size = max(8, round(chosen_size * canvas_height / PLAYRES_Y))
    path = _font_path(font_name)
    try:
        font = ImageFont.truetype(path, pixel_size) if path else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()
    max_px = canvas_width * max_width_pct / 100.0

    transcripts_dir = edit_dir / "transcripts"
    entries: list[tuple[float, float, str]] = []
    seg_offset = 0.0
    overlap = _transition_duration(edl)

    for range_index, r in enumerate(edl["ranges"]):
        if range_index:
            seg_offset -= overlap
        src_name = r["source"]
        seg_start, seg_end = float(r["start"]), float(r["end"])
        tr_path = transcripts_dir / f"{src_name}.json"
        if not tr_path.exists():
            print(f"  no transcript for {src_name}, skipping captions for this segment")
            seg_offset += _range_output_duration(r)
            continue

        transcript = json.loads(tr_path.read_text())
        words = _apply_subtitle_mode(
            _words_in_range(transcript, seg_start, seg_end), mode
        )
        expanded_words: list[dict] = []
        for word in words:
            expanded_words.extend(
                _split_oversize_cjk_token(word, font, max_px, case_mode)
            )
        words = expanded_words
        chunks: list[list[dict]] = []
        current: list[dict] = []
        for idx, word in enumerate(words):
            token = (word.get("text") or "").strip()
            if not token:
                continue
            candidate = current + [word]
            candidate_text = _apply_caption_case(_join_caption_words(candidate), case_mode)
            width_exceeded = bool(current) and _caption_text_width(candidate_text, font) > max_px
            if len(candidate) > word_cap or width_exceeded:
                chunks.append(current)
                current = [word]
            else:
                current = candidate

            next_gap = None
            if idx + 1 < len(words) and word.get("end") is not None and words[idx + 1].get("start") is not None:
                next_gap = float(words[idx + 1]["start"]) - float(word["end"])
            punctuation_break = token[-1] in (set(".!?。！？") if natural else PUNCT_BREAK)
            pause_break = natural and next_gap is not None and next_gap >= 0.30 and len(current) >= 3
            if current and (punctuation_break or pause_break or len(current) >= word_cap):
                chunks.append(current)
                current = []
        if current:
            chunks.append(current)

        for chunk in chunks:
            local_start = max(seg_start, float(chunk[0].get("start", seg_start)))
            local_end = min(seg_end, float(chunk[-1].get("end", seg_end)))
            out_start = _to_output_time(local_start, r) + seg_offset
            out_end = _to_output_time(local_end, r) + seg_offset
            text = _apply_caption_case(_join_caption_words(chunk), case_mode)
            if not natural:
                text = text.rstrip(",;:，；：")
            if not text:
                continue
            if _caption_text_width(text, font) > max_px:
                raise ValueError(
                    f"single subtitle token is too wide for one line: {text!r}; "
                    "reduce --subtitle-font-size or increase --subtitle-max-width-pct"
                )
            entries.append((max(0.0, out_start), max(out_start + 0.08, out_end), text))
        seg_offset += _range_output_duration(r)

    entries = _finalize_caption_entries(entries, 0.70 if natural else 0.48)
    lines: list[str] = []
    for i, (start, end, text) in enumerate(entries, start=1):
        lines.extend([str(i), f"{_srt_timestamp(start)} --> {_srt_timestamp(end)}", text, ""])
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(
        f"master SRT (single-line, {style}) → {out_path.name} "
        f"({len(entries)} cues, max {max_width_pct:.0f}% width)"
    )


def build_master_srt(edl: dict, edit_dir: Path, out_path: Path, mode: str = "normal") -> None:
    """Build an output-timeline SRT from per-source transcripts.

    - 2-word chunks (break on any punctuation in between)
    - UPPERCASE text
    - Output times computed as (word.start - segment_start)/speed + segment_offset
    - `mode`: "verbatim" keeps filler/disfluency tokens as transcribed;
      "normal" (default) drops filler words and cleans self-repair tokens.
    """
    transcripts_dir = edit_dir / "transcripts"
    sources = edl["sources"]

    entries: list[tuple[float, float, str]] = []
    seg_offset = 0.0

    for r in edl["ranges"]:
        src_name = r["source"]
        seg_start = float(r["start"])
        seg_end = float(r["end"])

        tr_path = transcripts_dir / f"{src_name}.json"
        if not tr_path.exists():
            print(f"  no transcript for {src_name}, skipping captions for this segment")
            seg_offset += _range_output_duration(r)
            continue

        transcript = json.loads(tr_path.read_text())
        words_in_seg = _words_in_range(transcript, seg_start, seg_end)
        words_in_seg = _apply_subtitle_mode(words_in_seg, mode)

        # Group into 2-word chunks, break on punctuation
        chunks: list[list[dict]] = []
        current: list[dict] = []
        for w in words_in_seg:
            text = (w.get("text") or "").strip()
            if not text:
                continue
            current.append(w)
            # Break if the current text ends in punctuation or we hit 2 words
            ends_in_punct = bool(text) and text[-1] in PUNCT_BREAK
            if len(current) >= 2 or ends_in_punct:
                chunks.append(current)
                current = []
        if current:
            chunks.append(current)

        for chunk in chunks:
            local_start = max(seg_start, chunk[0].get("start", seg_start))
            local_end = min(seg_end, chunk[-1].get("end", seg_end))
            out_start = _to_output_time(local_start, r) + seg_offset
            out_end = _to_output_time(local_end, r) + seg_offset
            if out_end <= out_start:
                out_end = out_start + 0.4
            text = _strip_caption_edge_punctuation(
                re.sub(r"\s+", " ", " ".join((w.get("text") or "").strip() for w in chunk))
            )
            if not text:
                continue
            text = text.upper()
            entries.append((out_start, out_end, text))

        seg_offset += _range_output_duration(r)

    # Sort and write as SRT
    entries.sort(key=lambda e: e[0])
    lines: list[str] = []
    for i, (a, b, t) in enumerate(entries, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_timestamp(a)} --> {_srt_timestamp(b)}")
        lines.append(t)
        lines.append("")
    out_path.write_text("\n".join(lines))
    print(f"master SRT → {out_path.name} ({len(entries)} cues)")


def build_master_srt_natural(
    edl: dict, edit_dir: Path, out_path: Path, mode: str = "normal"
) -> None:
    """Build an output-timeline SRT in the 'natural-sentence' style:

    - 4-7 word chunks, sentence case (no upper-casing)
    - break early on a natural pause (>= 0.3s gap to the next word) or a
      sentence-ending mark, once the chunk already has >= 3 words
    - hard-break at 7 words regardless
    - output times are speed-aware: (word.time - seg_start)/speed + seg_offset
    - `mode`: "verbatim" keeps filler/disfluency tokens as transcribed;
      "normal" (default) drops filler words and cleans self-repair tokens
      (e.g. 'pa-participation' -> 'participation') to the completed word.
    """
    transcripts_dir = edit_dir / "transcripts"
    sources = edl["sources"]

    SENTENCE_END = set(".!?")
    MIN_CHUNK, MAX_CHUNK = 3, 7
    PAUSE_BREAK_S = 0.30

    entries: list[tuple[float, float, str]] = []
    seg_offset = 0.0

    for r in edl["ranges"]:
        src_name = r["source"]
        seg_start = float(r["start"])
        seg_end = float(r["end"])

        tr_path = transcripts_dir / f"{src_name}.json"
        if not tr_path.exists():
            print(f"  no transcript for {src_name}, skipping captions for this segment")
            seg_offset += _range_output_duration(r)
            continue

        transcript = json.loads(tr_path.read_text())
        words_in_seg = _words_in_range(transcript, seg_start, seg_end)
        words_in_seg = _apply_subtitle_mode(words_in_seg, mode)

        chunks: list[list[dict]] = []
        current: list[dict] = []
        for idx, w in enumerate(words_in_seg):
            text = (w.get("text") or "").strip()
            if not text:
                continue
            current.append(w)

            next_gap = None
            if idx + 1 < len(words_in_seg):
                nxt = words_in_seg[idx + 1]
                if nxt.get("start") is not None and w.get("end") is not None:
                    next_gap = nxt["start"] - w["end"]

            ends_sentence = text[-1] in SENTENCE_END
            natural_break = (next_gap is not None and next_gap >= PAUSE_BREAK_S)
            long_enough = len(current) >= MIN_CHUNK

            if len(current) >= MAX_CHUNK or (long_enough and (ends_sentence or natural_break)):
                chunks.append(current)
                current = []
        if current:
            chunks.append(current)

        for chunk in chunks:
            local_start = max(seg_start, chunk[0].get("start", seg_start))
            local_end = min(seg_end, chunk[-1].get("end", seg_end))
            out_start = _to_output_time(local_start, r) + seg_offset
            out_end = _to_output_time(local_end, r) + seg_offset
            if out_end <= out_start:
                out_end = out_start + 0.4
            text = _strip_caption_edge_punctuation(
                re.sub(r"\s+", " ", " ".join((w.get("text") or "").strip() for w in chunk))
            )
            if not text:
                continue
            entries.append((out_start, out_end, text))

        seg_offset += _range_output_duration(r)

    entries.sort(key=lambda e: e[0])
    lines: list[str] = []
    for i, (a, b, t) in enumerate(entries, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_timestamp(a)} --> {_srt_timestamp(b)}")
        lines.append(t)
        lines.append("")
    out_path.write_text("\n".join(lines))
    print(f"master SRT (natural-sentence) → {out_path.name} ({len(entries)} cues)")


# -------- Loudness normalization (social-ready audio) -----------------------


# Social-media standard: -14 LUFS integrated, -1 dBTP peak, LRA 11 LU.
# Matches YouTube / Instagram / TikTok / X / LinkedIn normalization targets.
LOUDNORM_I = -14.0
LOUDNORM_TP = -1.0
LOUDNORM_LRA = 11.0


def measure_loudness(video_path: Path) -> dict[str, str] | None:
    """Run ffmpeg loudnorm first pass and parse the JSON measurement.

    Returns a dict with measured_i, measured_tp, measured_lra, measured_thresh,
    target_offset, or None if measurement failed.
    """
    filter_str = (
        f"loudnorm=I={LOUDNORM_I}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}:print_format=json"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-nostats",
        "-i", str(video_path),
        "-af", filter_str,
        "-vn", "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    # loudnorm prints the JSON to stderr at the end of the run
    stderr = proc.stderr

    # Find the JSON block — loudnorm output contains a `{ ... }` block
    start = stderr.rfind("{")
    end = stderr.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(stderr[start : end + 1])
    except json.JSONDecodeError:
        return None
    needed = {"input_i", "input_tp", "input_lra", "input_thresh", "target_offset"}
    if not needed.issubset(data.keys()):
        return None
    return data


def apply_loudnorm_two_pass(
    input_path: Path,
    output_path: Path,
    preview: bool = False,
) -> bool:
    """Run two-pass loudnorm on input_path, write normalized copy to output_path.

    Returns True on success, False if measurement failed (caller should fall
    back to copying the input unchanged).

    In preview mode, skips the measurement pass and uses a one-pass approximation
    for speed. Final mode always does the proper two-pass.
    """
    if preview:
        # One-pass approximation — faster, slightly less accurate.
        filter_str = f"loudnorm=I={LOUDNORM_I}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}"
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-nostats",
            "-i", str(input_path),
            "-c:v", "copy",
            "-af", filter_str,
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart",
            str(output_path),
        ]
        print(f"  loudnorm (1-pass preview) → {output_path.name}")
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return True

    # Full two-pass
    print(f"  loudnorm pass 1: measuring {input_path.name}")
    measurement = measure_loudness(input_path)
    if measurement is None:
        print("  loudnorm measurement failed — falling back to 1-pass")
        return apply_loudnorm_two_pass(input_path, output_path, preview=True)

    print(f"    measured: I={measurement['input_i']} LUFS  "
          f"TP={measurement['input_tp']}  LRA={measurement['input_lra']}")

    filter_str = (
        f"loudnorm=I={LOUDNORM_I}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}"
        f":measured_I={measurement['input_i']}"
        f":measured_TP={measurement['input_tp']}"
        f":measured_LRA={measurement['input_lra']}"
        f":measured_thresh={measurement['input_thresh']}"
        f":offset={measurement['target_offset']}"
        f":linear=true"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-nostats",
        "-i", str(input_path),
        "-c:v", "copy",
        "-af", filter_str,
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        str(output_path),
    ]
    print(f"  loudnorm pass 2: normalizing → {output_path.name}")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return True


# -------- Final compositing (Rule 1 + Rule 4) -------------------------------


def build_final_composite(
    base_path: Path,
    overlays: list[dict],
    subtitles_path: Path | None,
    out_path: Path,
    edit_dir: Path,
    force_style: str = SUB_FORCE_STYLE,
    music: dict | None = None,
    total_duration: float | None = None,
) -> None:
    """Final pass: base → overlays → subtitles LAST, plus optional audio mix.

    Overlay entries may set audio_volume to mix a meme/SFX clip at its visual
    start. Music supports looping, fades, and narration-driven ducking.
    """
    has_overlays = bool(overlays)
    has_subs = subtitles_path is not None and subtitles_path.exists()
    music = music or None
    has_music = bool(music and music.get("file"))

    if not has_overlays and not has_subs and not has_music:
        # Nothing to do — just rename/copy base to final name
        run(["ffmpeg", "-y", "-i", str(base_path), "-c", "copy", str(out_path)], quiet=True)
        return

    inputs: list[str] = ["-i", str(base_path)]
    overlay_paths: list[Path] = []
    for ov in overlays:
        ov_path = resolve_path(ov["file"], edit_dir)
        overlay_paths.append(ov_path)
        inputs += ["-i", str(ov_path)]

    music_input_index: int | None = None
    if has_music:
        music_path = resolve_path(str(music["file"]), edit_dir)
        if not music_path.exists():
            raise FileNotFoundError(f"music file not found: {music_path}")
        music_input_index = 1 + len(overlays)
        if bool(music.get("loop", True)):
            inputs += ["-stream_loop", "-1"]
        inputs += ["-i", str(music_path)]

    filter_parts: list[str] = []
    # PTS-shift every overlay so its frame 0 lands at start_in_output
    for idx, ov in enumerate(overlays, start=1):
        t = float(ov["start_in_output"])
        filter_parts.append(f"[{idx}:v]setpts=PTS-STARTPTS+{t}/TB[a{idx}]")

    # Chain overlays on top of base
    current = "[0:v]"
    for idx, ov in enumerate(overlays, start=1):
        t = float(ov["start_in_output"])
        dur = float(ov["duration"])
        end = t + dur
        next_label = f"[v{idx}]"
        filter_parts.append(
            f"{current}[a{idx}]overlay=enable='between(t,{t:.3f},{end:.3f})'{next_label}"
        )
        current = next_label

    has_video_filter = has_subs or has_overlays
    # Subtitles LAST — Rule 1
    if has_subs:
        filter_parts.append(f"{current}{build_subtitles_filter(subtitles_path, force_style)}[outv]")
        out_label = "[outv]"
    else:
        # Rename the last overlay output to [outv] for consistency
        if has_overlays:
            filter_parts.append(f"{current}null[outv]")
            out_label = "[outv]"
        else:
            out_label = "0:v"

    # Optional audio sources: overlay SFX/meme audio and background music.
    mix_labels: list[str] = ["[0:a]"]
    for idx, (ov, ov_path) in enumerate(zip(overlays, overlay_paths), start=1):
        volume = float(ov.get("audio_volume", 0.0))
        if volume <= 0 or not has_audio_stream(ov_path):
            continue
        start = max(0.0, float(ov.get("start_in_output", 0.0)))
        raw_duration = ov.get("duration")
        duration = max(0.05, float(raw_duration if raw_duration is not None else probe_duration(ov_path)))
        fade = min(0.04, duration / 4)
        label = f"[sfx{idx}]"
        filter_parts.append(
            f"[{idx}:a]atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,"
            f"volume={volume:.4f},afade=t=in:st=0:d={fade:.3f},"
            f"afade=t=out:st={max(0.0, duration-fade):.3f}:d={fade:.3f},"
            f"adelay={round(start * 1000)}:all=1{label}"
        )
        mix_labels.append(label)

    if has_music and music_input_index is not None:
        project_duration = float(total_duration or probe_duration(base_path))
        start = max(0.0, float(music.get("start", 0.0)))
        active_duration = max(0.05, project_duration - start)
        volume = min(1.0, max(0.0, float(music.get("volume", 0.12))))
        fade_in = min(active_duration / 2, max(0.0, float(music.get("fade_in", 1.0))))
        fade_out = min(active_duration / 2, max(0.0, float(music.get("fade_out", 1.5))))
        music_chain = (
            f"[{music_input_index}:a]atrim=0:{active_duration:.3f},asetpts=PTS-STARTPTS,"
            f"volume={volume:.4f}"
        )
        if fade_in > 0:
            music_chain += f",afade=t=in:st=0:d={fade_in:.3f}"
        if fade_out > 0:
            music_chain += f",afade=t=out:st={max(0.0, active_duration-fade_out):.3f}:d={fade_out:.3f}"
        if start > 0:
            music_chain += f",adelay={round(start * 1000)}:all=1"
        music_chain += "[musicbed]"
        filter_parts.append(music_chain)

        if bool(music.get("ducking", True)):
            threshold = float(music.get("duck_threshold", 0.035))
            ratio = float(music.get("duck_ratio", 8.0))
            filter_parts.append(
                f"[musicbed][0:a]sidechaincompress=threshold={threshold:.4f}:"
                f"ratio={ratio:.2f}:attack=20:release=350[musicduck]"
            )
            mix_labels.append("[musicduck]")
        else:
            mix_labels.append("[musicbed]")

    has_extra_audio = len(mix_labels) > 1
    if has_extra_audio:
        filter_parts.append(
            "".join(mix_labels)
            + f"amix=inputs={len(mix_labels)}:duration=first:normalize=0,"
              "alimiter=limit=0.95[aout]"
        )

    filter_complex = ";".join(filter_parts)

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", video_map_label(has_video_filter),
        "-map", "[aout]" if has_extra_audio else "0:a",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac" if has_extra_audio else "copy",
        *( ["-b:a", "192k", "-ar", "48000"] if has_extra_audio else [] ),
        "-movflags", "+faststart",
        str(out_path),
    ]
    print(f"compositing → {out_path.name}")
    print(
        f"  overlays: {len(overlays)}, subtitles: {'yes' if has_subs else 'no'}, "
        f"music: {'yes' if has_music else 'no'}, sfx: {len(mix_labels) - 1 - int(has_music)}"
    )
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


# -------- Main ---------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="Render a video from an EDL")
    ap.add_argument("edl", type=Path, help="Path to edl.json")
    ap.add_argument("-o", "--output", type=Path, required=False, help="Output video path")
    ap.add_argument(
        "--preview",
        action="store_true",
        help="Preview mode: 1080p, medium, CRF 22 — evaluable for QC, faster than final.",
    )
    ap.add_argument(
        "--draft",
        action="store_true",
        help="Draft mode: 720p, ultrafast, CRF 28 — cut-point verification only.",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Parallel workers for independent segment extraction (default 4; use 1 for constrained machines).",
    )
    ap.add_argument(
        "--build-subtitles",
        action="store_true",
        help="Build master.srt from transcripts + EDL offsets before compositing",
    )
    ap.add_argument(
        "--subtitles-only",
        action="store_true",
        help="Build master.srt from the EDL/transcripts and exit before video rendering.",
    )
    ap.add_argument(
        "--subtitle-style",
        choices=["high-contrast-outline", "bold-overlay", "natural-sentence"],
        default="high-contrast-outline",
        help="high-contrast-outline: default white text with dark contour and short shadow, no box. "
             "bold-overlay: 2-word UPPERCASE chunks. natural-sentence: 4-7 word sentence-case chunks.",
    )
    ap.add_argument(
        "--no-subtitles",
        action="store_true",
        help="Skip subtitles even if the EDL references one",
    )
    ap.add_argument(
        "--subtitle-margin-pct",
        type=float,
        default=None,
        help="Vertical caption position as percent up from the bottom edge. "
             "Defaults: 30 for high-contrast-outline/bold-overlay (platform-UI safe zone), 12.5 for "
             "natural-sentence (bottom 10-15%% band). Override after checking a "
             "sampled frame for this specific video.",
    )
    ap.add_argument(
        "--subtitle-background",
        choices=["none", "cc-tight"],
        default="none",
        help="Caption background: none or a near-flush translucent black CC box.",
    )
    ap.add_argument(
        "--subtitle-font",
        default=None,
        help="Font name for burned captions (must be installed / fontconfig-resolvable; "
             "see helpers/list_fonts.py). Default chooses an installed high-contrast CJK font.",
    )
    ap.add_argument(
        "--subtitle-font-size",
        type=float,
        default=None,
        help="ASS font size. Defaults: 22 high-contrast-outline/natural-sentence, 18 bold-overlay.",
    )
    ap.add_argument(
        "--subtitle-max-width-pct",
        type=float,
        default=82.0,
        help="Maximum one-line caption width as a percentage of the frame (default 82).",
    )
    ap.add_argument(
        "--subtitle-max-words",
        type=int,
        default=None,
        help="Maximum words/tokens per cue. Defaults: 7 high-contrast-outline/natural-sentence, 2 bold-overlay.",
    )
    ap.add_argument(
        "--subtitle-case",
        choices=["upper", "natural", "title"],
        default=None,
        help="Caption letter case. Defaults by style; CJK text is unaffected.",
    )
    ap.add_argument(
        "--subtitle-mode",
        choices=["verbatim", "normal"],
        default="normal",
        help="verbatim: every word as transcribed, including filler and disfluency tokens. "
             "normal (default): filler words dropped, self-repair tokens cleaned to the "
             "completed word. Always ask the user which one before rendering.",
    )
    ap.add_argument(
        "--no-loudnorm",
        action="store_true",
        help="Skip audio loudness normalization. Default is on (-14 LUFS, -1 dBTP, LRA 11).",
    )
    args = ap.parse_args()

    edl_path = args.edl.resolve()
    if not edl_path.exists():
        sys.exit(f"edl not found: {edl_path}")

    edl = json.loads(edl_path.read_text())
    edit_dir = edl_path.parent
    if args.subtitles_only:
        output_spec = edl.get("output") or {}
        if output_spec.get("width") and output_spec.get("height"):
            canvas_width, canvas_height = int(output_spec["width"]), int(output_spec["height"])
        else:
            first_range = edl["ranges"][0]
            first_source = resolve_path(edl["sources"][first_range["source"]], edit_dir)
            source_width, source_height = probe_dimensions(first_source)
            if source_height > source_width:
                canvas_height = 1920
                canvas_width = round(source_width / source_height * canvas_height) // 2 * 2
            else:
                canvas_width = 1920
                canvas_height = round(source_height / source_width * canvas_width) // 2 * 2
        build_master_srt_configured(
            edl, edit_dir, edit_dir / "master.srt",
            mode=args.subtitle_mode, style=args.subtitle_style,
            font_name=resolve_subtitle_font(args.subtitle_style, args.subtitle_font), font_size=args.subtitle_font_size,
            canvas_width=canvas_width, canvas_height=canvas_height,
            max_width_pct=args.subtitle_max_width_pct, max_words=args.subtitle_max_words,
            case_mode=args.subtitle_case,
        )
        return
    if args.output is None:
        ap.error("-o/--output is required unless --subtitles-only is used")
    out_path = args.output.resolve()

    # 1. Extract per-segment (auto-grade per range if EDL grade is "auto")
    segment_paths = extract_all_segments(
        edl, edit_dir, preview=args.preview, draft=args.draft, workers=args.workers
    )

    # 2. Concat → base
    if args.draft:
        base_name = "base_draft.mp4"
    elif args.preview:
        base_name = "base_preview.mp4"
    else:
        base_name = "base.mp4"
    base_path = edit_dir / base_name
    transition = edl.get("transition") or {}
    transition_type = str(transition.get("type", "none")).lower()
    if transition_type in {"none", "hard", "cut"}:
        concat_segments(segment_paths, base_path, edit_dir)
    else:
        concat_segments_with_transition(segment_paths, base_path, transition)

    # 3. Subtitles: build if requested, resolve final path
    subs_path: Path | None = None
    if not args.no_subtitles:
        if args.build_subtitles:
            subs_path = edit_dir / "master.srt"
            canvas_width, canvas_height = probe_dimensions(base_path)
            build_master_srt_configured(
                edl,
                edit_dir,
                subs_path,
                mode=args.subtitle_mode,
                style=args.subtitle_style,
                font_name=resolve_subtitle_font(args.subtitle_style, args.subtitle_font),
                font_size=args.subtitle_font_size,
                canvas_width=canvas_width,
                canvas_height=canvas_height,
                max_width_pct=args.subtitle_max_width_pct,
                max_words=args.subtitle_max_words,
                case_mode=args.subtitle_case,
            )
        elif edl.get("subtitles"):
            subs_path = resolve_path(edl["subtitles"], edit_dir)
            if not subs_path.exists():
                print(f"warning: subtitles path in EDL does not exist: {subs_path}")
                subs_path = None

    force_style = build_subtitle_force_style(
        style=args.subtitle_style,
        margin_pct=args.subtitle_margin_pct,
        safe_box=args.subtitle_background == "cc-tight",
        font_name=args.subtitle_font,
        font_size=args.subtitle_font_size,
    )

    # 4. Composite (overlays + subtitles LAST) → intermediate (pre-loudnorm) path
    overlays = edl.get("overlays") or []
    music = edl.get("music") or None
    transition_overlap = _transition_duration(edl) * max(0, len(edl.get("ranges", [])) - 1)
    total_duration = sum(_range_output_duration(r) for r in edl.get("ranges", [])) - transition_overlap
    if args.no_loudnorm:
        # Composite directly to final output
        build_final_composite(
            base_path, overlays, subs_path, out_path, edit_dir,
            force_style=force_style, music=music, total_duration=total_duration,
        )
    else:
        # Composite to a temp file, then run loudnorm → final output
        tmp_composite = out_path.with_suffix(".prenorm.mp4")
        build_final_composite(
            base_path, overlays, subs_path, tmp_composite, edit_dir,
            force_style=force_style, music=music, total_duration=total_duration,
        )
        print("loudness normalization → social-ready (-14 LUFS / -1 dBTP / LRA 11)")
        apply_loudnorm_two_pass(tmp_composite, out_path, preview=args.preview or args.draft)
        tmp_composite.unlink(missing_ok=True)

    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"\ndone: {out_path} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
