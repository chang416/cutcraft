"""List fonts available to libass for subtitle burning, with a few
content-aware recommendations.

Usage:
    python helpers/list_fonts.py                     # full list, sorted
    python helpers/list_fonts.py --recommend social   # just the recommended set
    python helpers/list_fonts.py --recommend documentary

`fc-list` (fontconfig) is what libass actually resolves FontName against at
render time — a name not in this list will silently fall back to whatever
fontconfig picks as default, which is how you get a subtitle render that
doesn't match what you asked for. Always check a name is here before using
it in --subtitle-font.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

# Curated starting points, not an exhaustive catalogue. Each is checked
# against the live fc-list output before being offered — a font missing from
# the system just gets skipped, not offered as if it were installed.
RECOMMENDATIONS: dict[str, list[str]] = {
    # Fast-paced short-form / social — bold, high x-height, reads at a glance.
    "social": ["Helvetica Neue", "Arial Black", "Futura", "Avenir Next", "Impact"],
    # Documentary / education / talking-head — calm, high legibility at length.
    "documentary": ["Helvetica Neue", "Avenir", "PT Sans", "Noto Sans", "Georgia"],
    # Code/tutorial/screen-recording content — monospace reads as "technical".
    "tutorial": ["Menlo", "SF Mono", "Consolas", "JetBrains Mono", "Courier New"],
    # CJK content needs a font with actual glyph coverage — Helvetica silently
    # tofu-boxes CJK text.
    "cjk": ["Noto Sans TC", "PingFang TC", "PingFang SC", "Noto Sans CJK TC", "Noto Sans CJK SC", "Heiti TC"],
}


def list_families() -> list[str]:
    try:
        out = subprocess.run(
            ["fc-list", ":", "family"],
            capture_output=True, text=True, check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return []
    families: set[str] = set()
    for line in out.stdout.splitlines():
        # fc-list can print comma-separated aliases per line; take the first.
        name = line.split(",")[0].strip()
        if name:
            families.add(name)
    return sorted(families)


def recommend(category: str, installed: list[str]) -> list[str]:
    installed_set = set(installed)
    return [f for f in RECOMMENDATIONS.get(category, []) if f in installed_set]


def main() -> None:
    ap = argparse.ArgumentParser(description="List fonts available for subtitle burning")
    ap.add_argument(
        "--recommend",
        choices=sorted(RECOMMENDATIONS.keys()),
        help="Print only the recommended subset for this content category (that's actually installed)",
    )
    args = ap.parse_args()

    installed = list_families()
    if not installed:
        sys.exit(
            "fc-list not found or returned nothing — fontconfig may not be installed. "
            "On macOS this ships with ffmpeg-full's dependencies; run "
            "`brew install fontconfig` if it's still missing."
        )

    if args.recommend:
        for name in recommend(args.recommend, installed):
            print(name)
        return

    for name in installed:
        print(name)


if __name__ == "__main__":
    main()
