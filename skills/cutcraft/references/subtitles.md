# Subtitle workflow

## Approval dimensions

Resolve language, wording cleanup, case, cue rhythm, font, size, actual pixel width, placement, background, burned-in/sidecar delivery, and conflicts with existing on-screen text in the single checkpoint.

Generate three real-frame previews with `subtitle_style_preview.py`. Recommended set when material does not suggest otherwise:

1. `high-contrast-outline`: white semi-bold, near-black contour, short shadow, no box.
2. `natural-sentence`: calmer sentence rhythm and lower placement for landscape education/documentary.
3. `bold-overlay`: short, energetic social cues, only when the content supports it.

Use installed fonts only. For Chinese, prefer an installed CJK-capable semi-bold face. Use `cc-tight` only when the preview shows an outline cannot maintain contrast; never create a full-width black banner without explicit approval.

## Construction rules

- Build Chinese cues with `build_cn_subtitles.py` so segmentation follows Chinese word boundaries and rebalances orphan cues.
- Build other cues from word-level transcript timestamps on the output timeline.
- Keep one-line captions within the approved safe width (default 82%) using the actual selected font and canvas.
- Strip punctuation and quote marks from cue edges while preserving meaningful internal punctuation.
- Keep reading speed near 20 Latin characters/s or 12 CJK characters/s; prefer shorter text cleanup and longer holds when speech is faster.
- Never insert manual line breaks into a single-line cue.
- Check beginning, middle, end, every layout change, and frames containing lower-thirds or platform UI.

## User corrections

Deliver a cue-addressable Markdown transcript and sidecar SRT. Translate a request such as `第 18 句「舊字」改成「新字」` into `transcript_corrections.json`, apply it fail-closed, and keep timings unchanged unless sync is also disputed.

After correction:

1. Validate cue count, order, timestamps, pixel width, overlaps, and reading speed.
2. Re-render the final video using the corrected `master.srt`; do not rebuild captions from ASR afterward.
3. Regenerate the delivered Markdown transcript and SRT.
4. Inspect the changed cue in the rendered video and report the replacement.

For lyric videos, transcribe the exact trimmed/resampled audio asset used in the final mix. Do not shift timestamps from a full-song transcript by hand.
