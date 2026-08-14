"""Chinese-aware single-line subtitle builder.

Why not render.py's built-in build_master_srt_configured():
  * its chunker counts every CJK character as one "word" and only hard-breaks on
    。！？, so cues land mid-word (「主要分／為四個部分」, 「就是以成／長性」);
  * standalone ASR punctuation tokens leave stray spaces (「產業分析 、 投資」);
  * it chunks per EDL range, so a sentence split by a disfluency cut turns into
    an orphan cue (「那麼在這樣的總經背景」／「之下」).

This builder keeps render.py's output-timeline math, pixel-width guarantee and
cue-hold finalizer, but:
  1. flattens every kept range onto one continuous output timeline first, so a
     cue can span a disfluency cut;
  2. only breaks on jieba word boundaries, so no cue ever splits a word;
  3. rebalances orphan and dangling cues after greedy chunking.

jieba is segmented on a t2s (Traditional -> Simplified) projection of the text:
its bundled dictionary is Simplified, and OpenCC t2s is length-preserving on
CJK prose, so word boundaries map back to the original characters 1:1. Falls
back to per-character breaks if a t2s pass ever changes the length (flag it
loudly instead of guessing).

Usage:
    python3 helpers/build_cn_subtitles.py <edl.json> -o master.srt \
        --font "PingFang TC" --font-size 25 --max-width-pct 82

Requires: jieba, opencc (pip install jieba opencc). Both are optional deps of
this helper only -- render.py itself doesn't need them.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render as R  # noqa: E402

try:
    import jieba
    import opencc
except ImportError:
    sys.exit(
        "build_cn_subtitles.py needs jieba + opencc: "
        "pip install jieba opencc / uv pip install jieba opencc"
    )
from PIL import ImageFont  # noqa: E402

MAX_CHARS_DEFAULT = 15     # hard cap so a cue never becomes a wall of text
MIN_CHARS = 5              # below this a cue reads as an orphan fragment
MIN_CHARS_SOFT = 6         # don't break on 、／， until the cue has some body
PAUSE_BREAK = 0.30         # speech gap that justifies a new cue

HARD_PUNCT = set("。！？!?")
SOFT_PUNCT = set("，、；：,;:")
DROP_TOKENS = {"-", "——", "—", "–"}
# A cue ending on one of these reads as cut off mid-thought.
DANGLING = set("跟和與及或的之在被把從對向為是有")


def is_ascii_alnum(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def join_tokens(tokens: list[dict]) -> str:
    """Concatenate CJK tightly, but keep CJK/Latin and Latin/Latin spacing."""
    out = ""
    for t in tokens:
        tok = t["text"]
        if out:
            a, b = out[-1], tok[0]
            need = (
                (is_ascii_alnum(a) and is_ascii_alnum(b))
                or (not a.isascii() and b.isascii() and b.isalpha())
                or (a.isascii() and a.isalpha() and not b.isascii())
            )
            if need:
                out += " "
        out += tok
    return out


def visible(tokens: list[dict]) -> str:
    return R._strip_caption_edge_punctuation(join_tokens(tokens))


def rendered_durations(edit_dir: Path, n: int) -> list[float] | None:
    """Actual duration of each extracted segment, if they have been rendered.

    ffmpeg rounds every segment up to a whole frame / audio packet, so the real
    timeline can run several hundred ms longer than the EDL's nominal sum
    across many cuts. Using the nominal sum leaves the tail captions early.
    Run `render.py ... --no-subtitles` once (or a full render) before this
    helper so clips_graded/ exists; otherwise it falls back to nominal timing
    and prints a warning.
    """
    clips = sorted((edit_dir / "clips_graded").glob("*.mp4"))
    if len(clips) != n:
        return None
    out = []
    for c in clips:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(c)],
            capture_output=True, text=True,
        )
        if probe.returncode != 0:
            return None
        out.append(float(probe.stdout.strip()))
    return out


def flatten(edl: dict, edit_dir: Path) -> list[dict]:
    """Map every kept word onto one concatenated output timeline."""
    words: list[dict] = []
    seg_offset = 0.0
    actual = rendered_durations(edit_dir, len(edl["ranges"]))
    if actual:
        drift = sum(actual) - sum(R._range_output_duration(r) for r in edl["ranges"])
        print(f"  using rendered segment durations (total drift {drift:+.3f}s)")
    else:
        print("  warn: clips_graded/ not rendered yet; using nominal EDL durations "
              "(run render.py --no-subtitles first for exact sync)")

    def owns(w: dict, r: dict) -> bool:
        """Assign each source word to exactly one range: the one it overlaps most.

        A word straddling a cut edge otherwise satisfies _words_in_range on both
        sides and gets emitted twice (e.g. 「背景之之下」).

        Candidates are restricted to ranges sharing this word's source. Range
        `start`/`end` values are LOCAL to their own source file, so in a
        multi-source EDL a word from source A can numerically overlap an
        unrelated range on source B (e.g. B's range [0.8, 15.7) will "contain"
        practically any short A-side timestamp in that span) — comparing
        across sources picks an arbitrary foreign range instead of A's own,
        which silently drops or duplicates words and can shift hundreds of
        cues by tens of seconds. This is easy to miss with a single-source EDL
        (nothing to collide with) and only shows up once a second file is cut
        into the same project — verify caption timing against the rendered
        output whenever more than one source is used.
        """
        same_source = [rr for rr in edl["ranges"] if rr["source"] == r["source"]]
        ws, we = float(w["start"]), float(w["end"])
        if we <= ws:
            # Scribe emits punctuation as zero-duration tokens, so an overlap
            # test would silently discard every 。 and 、. Place them by instant.
            for rr in same_source:
                if float(rr["start"]) <= ws <= float(rr["end"]):
                    return rr is r
            return False

        def ov(rr):
            return max(0.0, min(we, float(rr["end"])) - max(ws, float(rr["start"])))
        best = max(same_source, key=ov)
        return ov(best) > 0 and best is r

    for idx, r in enumerate(edl["ranges"]):
        seg_start, seg_end = float(r["start"]), float(r["end"])
        transcript = json.loads((edit_dir / "transcripts" / f"{r['source']}.json").read_text())
        for w in R._words_in_range(transcript, seg_start, seg_end):
            text = (w.get("text") or "").strip()
            if not text or text in DROP_TOKENS:
                continue
            if not owns(w, r):
                continue
            local_start = max(seg_start, float(w["start"]))
            local_end = min(seg_end, float(w["end"]))
            words.append({
                "text": text,
                "start": R._to_output_time(local_start, r) + seg_offset,
                "end": R._to_output_time(local_end, r) + seg_offset,
            })
        seg_offset += actual[idx] if actual else R._range_output_duration(r)

    # Fold punctuation-only tokens into the preceding word so joining never
    # inserts a space before or after a full-width mark.
    merged: list[dict] = []
    for w in words:
        only_punct = all(c in HARD_PUNCT or c in SOFT_PUNCT or c == "…" for c in w["text"])
        if only_punct and merged:
            merged[-1]["text"] += w["text"]
            merged[-1]["end"] = max(merged[-1]["end"], w["end"])
            continue
        merged.append(w)
    return merged


def mark_word_boundaries(tokens: list[dict]) -> None:
    """Set token['can_break_after'] from jieba segmentation of the whole script."""
    full = "".join(t["text"] for t in tokens)
    simp = opencc.OpenCC("t2s").convert(full)
    if len(simp) != len(full):
        print("  warn: t2s changed length; falling back to per-character breaks")
        for t in tokens:
            t["can_break_after"] = True
        return

    starts = set()
    pos = 0
    for seg in jieba.cut(simp, HMM=True):
        starts.add(pos)
        pos += len(seg)
    starts.add(len(simp))

    offset = 0
    for t in tokens:
        offset += len(t["text"])
        # A break here is legal only if the next character starts a new word.
        t["can_break_after"] = offset in starts


def chunk(tokens: list[dict], font, max_px: float, max_chars: int) -> list[list[dict]]:
    def fits_text(text: str) -> bool:
        return R._caption_text_width(text, font) <= max_px and len(text) <= max_chars

    chunks: list[list[dict]] = []
    current: list[dict] = []
    for i, w in enumerate(tokens):
        current.append(w)
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if nxt is None:
            break

        text = visible(current)
        last_char = w["text"][-1]
        gap = nxt["start"] - w["end"]
        overflow = not fits_text(visible(current + [nxt]))
        wants_break = (
            last_char in HARD_PUNCT
            or (last_char in SOFT_PUNCT and len(text) >= MIN_CHARS_SOFT)
            or (gap >= PAUSE_BREAK and len(text) >= 4)
            or overflow
        )
        if not wants_break:
            continue

        split = len(current) if w.get("can_break_after") else 0
        if not split:
            # Walk back to the newest legal word boundary inside this cue.
            for j in range(len(current) - 1, 0, -1):
                if current[j - 1].get("can_break_after"):
                    split = j
                    break
            if not split:
                split = len(current)  # no legal boundary; take the cap
        chunks.append(current[:split])
        current = current[split:]
    if current:
        chunks.append(current)
    return chunks


def rebalance(chunks: list[list[dict]], font, max_px: float, max_chars: int) -> list[list[dict]]:
    """Repair orphan cues and cues left dangling on a conjunction.

    Greedy chunking breaks at the width cap, which strands tails like 「績效」 or
    「percent」. Prefer merging back; otherwise shift whole words across the
    boundary until both cues stand on their own.
    """
    def fits(tokens: list[dict]) -> bool:
        text = visible(tokens)
        return R._caption_text_width(text, font) <= max_px and len(text) <= max_chars

    changed = True
    guard = 0
    while changed and guard < 200:
        changed = False
        guard += 1
        i = 1
        while i < len(chunks):
            prev, cur = chunks[i - 1], chunks[i]
            pv = visible(prev)
            short = len(visible(cur)) < MIN_CHARS
            dangling = pv[-1:] in DANGLING if pv else False
            if not (short or dangling):
                i += 1
                continue
            if short and fits(prev + cur):
                chunks[i - 1] = prev + cur
                del chunks[i]
                changed = True
                continue
            # Shift whole words (not single characters) back across the
            # boundary: moving one character at a time stalls the moment the
            # next step would split a word.
            best = None
            for k in range(len(prev) - 1, 0, -1):
                if not prev[k - 1].get("can_break_after"):
                    continue
                new_prev, new_cur = prev[:k], prev[k:] + cur
                if not fits(new_cur) or len(visible(new_prev)) < MIN_CHARS:
                    continue
                best = (new_prev, new_cur)
                if len(visible(new_cur)) >= MIN_CHARS and visible(new_prev)[-1:] not in DANGLING:
                    break
            if best:
                chunks[i - 1], chunks[i] = best
                changed = True
            i += 1
    return chunks


def build(
    edl_path: Path,
    out_path: Path,
    *,
    font_name: str,
    font_size: float,
    canvas_width: int | None,
    canvas_height: int | None,
    max_width_pct: float,
    max_chars: int,
    min_hold: float,
) -> None:
    edit_dir = edl_path.parent
    edl = json.loads(edl_path.read_text())

    if canvas_width is None or canvas_height is None:
        output_spec = edl.get("output") or {}
        canvas_width = canvas_width or int(output_spec.get("width") or 1920)
        canvas_height = canvas_height or int(output_spec.get("height") or 1080)

    pixel_size = max(8, round(font_size * canvas_height / R.PLAYRES_Y))
    path = R._font_path(font_name)
    if not path:
        sys.exit(f"font not resolvable: {font_name!r} (see helpers/list_fonts.py)")
    font = ImageFont.truetype(path, pixel_size)
    max_px = canvas_width * max_width_pct / 100.0

    tokens = flatten(edl, edit_dir)
    if not tokens:
        sys.exit("no words found inside the EDL's kept ranges; check transcripts/ and edl.json")
    mark_word_boundaries(tokens)
    chunks = rebalance(chunk(tokens, font, max_px, max_chars), font, max_px, max_chars)

    entries: list[tuple[float, float, str]] = []
    for c in chunks:
        text = visible(c)
        if not text:
            continue
        width = R._caption_text_width(text, font)
        if width > max_px:
            sys.exit(f"cue too wide ({width:.0f}px > {max_px:.0f}px): {text!r} "
                     "-- reduce --font-size or increase --max-width-pct")
        entries.append((max(0.0, c[0]["start"]), max(c[0]["start"] + 0.08, c[-1]["end"]), text))

    entries = R._finalize_caption_entries(entries, min_hold)
    lines: list[str] = []
    for i, (start, end, text) in enumerate(entries, start=1):
        lines.extend([str(i), f"{R._srt_timestamp(start)} --> {R._srt_timestamp(end)}", text, ""])
    out_path.write_text("\n".join(lines), encoding="utf-8")

    widest = max(entries, key=lambda e: R._caption_text_width(e[2], font))
    shortest = min(entries, key=lambda e: len(e[2]))
    rate = max(len(t) / max(0.01, b - a) for a, b, t in entries)
    briefest = min(b - a for a, b, _ in entries)
    declared = edl.get("total_duration_s")
    print(f"{out_path.name} → {len(entries)} cues, timeline ends {entries[-1][1]:.2f}s"
          + (f" (EDL expects {declared:.2f}s)" if declared else ""))
    print(f"  widest  : {widest[2]!r} {R._caption_text_width(widest[2], font):.0f}px / {max_px:.0f}px")
    print(f"  shortest: {shortest[2]!r} ({len(shortest[2])} chars)")
    print(f"  peak reading rate: {rate:.1f} chars/s (target <= 12 for CJK)")
    print(f"  briefest cue: {briefest:.2f}s")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("edl", type=Path, help="path to edl.json")
    ap.add_argument("-o", "--output", type=Path, default=None,
                     help="output .srt path (default: <edl_dir>/master.srt)")
    ap.add_argument("--font", default="PingFang TC",
                     help="installed CJK font name (see helpers/list_fonts.py --recommend cjk)")
    ap.add_argument("--font-size", type=float, default=25.0,
                     help="ASS font size on the 288-high script canvas (default 25)")
    ap.add_argument("--canvas-width", type=int, default=None,
                     help="defaults to edl.output.width, else 1920")
    ap.add_argument("--canvas-height", type=int, default=None,
                     help="defaults to edl.output.height, else 1080")
    ap.add_argument("--max-width-pct", type=float, default=82.0,
                     help="max one-line caption width as %% of frame width (default 82)")
    ap.add_argument("--max-chars", type=int, default=MAX_CHARS_DEFAULT,
                     help=f"hard cap on characters per cue (default {MAX_CHARS_DEFAULT})")
    ap.add_argument("--min-hold", type=float, default=0.70,
                     help="minimum on-screen seconds per cue (default 0.70)")
    args = ap.parse_args()

    edl_path = args.edl.resolve()
    if not edl_path.exists():
        sys.exit(f"edl not found: {edl_path}")
    out_path = args.output.resolve() if args.output else edl_path.parent / "master.srt"

    build(
        edl_path, out_path,
        font_name=args.font, font_size=args.font_size,
        canvas_width=args.canvas_width, canvas_height=args.canvas_height,
        max_width_pct=args.max_width_pct, max_chars=args.max_chars,
        min_hold=args.min_hold,
    )


if __name__ == "__main__":
    main()
