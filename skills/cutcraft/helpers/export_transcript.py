#!/usr/bin/env python3
"""Export a cue-addressable Markdown transcript from an SRT file."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


BLOCK_RE = re.compile(
    r"(?ms)^\s*(\d+)\s*\n"
    r"(\d{2}:\d{2}:\d{2}[,.]\d{3}\s+-->\s+\d{2}:\d{2}:\d{2}[,.]\d{3})\s*\n"
    r"(.*?)(?=\n{2,}|\Z)"
)


def parse_srt(text: str) -> list[tuple[int, str, str]]:
    cues = []
    for match in BLOCK_RE.finditer(text.replace("\r\n", "\n")):
        number = int(match.group(1))
        timing = match.group(2).replace(".", ",")
        cue_text = " ".join(line.strip() for line in match.group(3).splitlines() if line.strip())
        cues.append((number, timing, cue_text))
    if not cues:
        raise ValueError("No valid SRT cues found")
    return cues


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a readable Markdown transcript with stable SRT cue numbers")
    parser.add_argument("--srt", type=Path, required=True)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--title", default="逐字稿")
    args = parser.parse_args()
    cues = parse_srt(args.srt.read_text(encoding="utf-8-sig"))
    lines = [f"# {args.title}", "", "可直接指定句號修正，例如：`第 18 句改成……`。", ""]
    for number, timing, text in cues:
        lines.extend([f"## 第 {number} 句", "", f"`{timing}`", "", text, ""])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"exported {len(cues)} cues → {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
