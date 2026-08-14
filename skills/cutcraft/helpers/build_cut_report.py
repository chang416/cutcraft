"""Turn an edl.json into a categorized, human-reviewable report.

Groups every range's edit decision by category so the user can accept/reject
whole categories before a refinement pass, instead of re-reading raw EDL
JSON. Prefers an explicit `category` field on each range (see CATEGORIES
below); falls back to keyword-matching the `reason` field for EDLs that
predate this field.

Usage:
    python helpers/build_cut_report.py <edl.json> [-o cut_report.md]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# Canonical categories. `category` on a range should be one of these keys;
# the value is (heading, one-line explanation shown once per section).
CATEGORIES: dict[str, tuple[str, str]] = {
    "kept": ("Kept as-is", "Clean delivery, no edit applied to this range."),
    "dead_air_trim": (
        "Dead-air / pause trims",
        "A pause longer than natural was shortened to a normal breathing gap. Audio content unchanged.",
    ),
    "filler_removal": (
        "Filler word cuts",
        "Standalone filler words (\"uh,\", \"um,\") cut from the audio entirely.",
    ),
    "repeat_removal": (
        "Repeats / false-start cuts",
        "A restarted or repeated phrase — the abandoned attempt was cut, the clean version kept.",
    ),
    "stuck_moment": (
        "Stuck-moment cuts",
        "An extended stall (long pause, throat-clear, trailing-off) cut out mid-sentence.",
    ),
    "speed_adjustment": (
        "Pace adjustments",
        "Segment sped up (audio pitch preserved) to even out a notably slower section against the rest of the video.",
    ),
    "other": ("Other edits", "Edits that don't fit the categories above — see each reason."),
}

# Fallback keyword -> category inference, for EDLs without an explicit
# `category` field. Checked in order; first match wins.
_KEYWORD_RULES: list[tuple[str, str]] = [
    (r"\buh,?\b|\bfiller\b|\bum,?\b", "filler_removal"),
    (r"\bstuck\b|\bthroat.?clear\b|\btrail(ed|ing) off\b", "stuck_moment"),
    (r"\brepeat(ed)?\b|\bfalse.?start\b|\brestart\b|\bduplicate\b", "repeat_removal"),
    (r"\bpause\b|\bdead.?air\b|\btrim", "dead_air_trim"),
]


def infer_category(r: dict) -> str:
    if r.get("category"):
        return r["category"]
    if float(r.get("speed", 1.0)) != 1.0:
        return "speed_adjustment"
    reason = (r.get("reason") or "").lower()
    for pattern, cat in _KEYWORD_RULES:
        if re.search(pattern, reason):
            return cat
    return "other" if reason else "kept"


def build_report(edl: dict) -> str:
    ranges = edl.get("ranges", [])
    by_cat: dict[str, list[dict]] = {}
    for i, r in enumerate(ranges):
        cat = infer_category(r)
        by_cat.setdefault(cat, []).append({**r, "_index": i})

    total_source_s = sum(float(r["end"]) - float(r["start"]) for r in ranges)
    total_output_s = sum(
        (float(r["end"]) - float(r["start"])) / float(r.get("speed", 1.0)) for r in ranges
    )

    lines: list[str] = []
    lines.append("# Cut report")
    lines.append("")
    lines.append(
        f"{len(ranges)} kept ranges, {total_output_s:.1f}s output "
        f"(source spans covered: {total_source_s:.1f}s before any speed changes)."
    )
    lines.append("")
    lines.append(
        "Check off what you agree with. Anything unchecked, tell the agent what to change "
        "and it'll revise just that range."
    )
    lines.append("")

    # Stable section order: CATEGORIES dict order, skip empty sections.
    for cat_key, (heading, blurb) in CATEGORIES.items():
        items = by_cat.get(cat_key)
        if not items:
            continue
        lines.append(f"## {heading} ({len(items)})")
        lines.append("")
        lines.append(f"*{blurb}*")
        lines.append("")
        for r in items:
            src = r.get("source", "?")
            s, e = float(r["start"]), float(r["end"])
            speed = float(r.get("speed", 1.0))
            speed_note = f", speed {speed:.2f}x" if speed != 1.0 else ""
            reason = r.get("reason", "").strip()
            beat = r.get("beat", "")
            beat_note = f" [{beat}]" if beat else ""
            lines.append(f"- [ ] range {r['_index']:02d}{beat_note} — `{src}` {s:.2f}s-{e:.2f}s{speed_note}")
            if reason:
                lines.append(f"      {reason}")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a categorized cut report from an EDL")
    ap.add_argument("edl", type=Path, help="Path to edl.json")
    ap.add_argument("-o", "--output", type=Path, default=None, help="Output markdown path (default: <edl_dir>/cut_report.md)")
    args = ap.parse_args()

    edl_path = args.edl.resolve()
    edl = json.loads(edl_path.read_text())
    out_path = args.output or (edl_path.parent / "cut_report.md")

    report = build_report(edl)
    out_path.write_text(report)
    print(f"cut report → {out_path} ({len(edl.get('ranges', []))} ranges)")


if __name__ == "__main__":
    main()
