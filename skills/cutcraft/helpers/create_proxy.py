#!/usr/bin/env python3
"""Create cached editing proxies while preserving a map back to original media."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def signature(path: Path) -> dict[str, int | str]:
    stat = path.stat()
    return {"path": str(path.resolve()), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def create_proxy(source: Path, work_dir: Path, *, max_width: int = 1280, crf: int = 24) -> tuple[Path, bool]:
    source = source.resolve()
    proxy_dir = work_dir.resolve() / "proxies"
    proxy_dir.mkdir(parents=True, exist_ok=True)
    output = proxy_dir / f"{source.stem}.proxy.mp4"
    manifest = proxy_dir / f"{source.stem}.proxy.json"
    expected = {"schema_version": 1, "source": signature(source), "proxy": str(output), "max_width": max_width, "crf": crf}
    if output.is_file() and manifest.is_file():
        try:
            if json.loads(manifest.read_text(encoding="utf-8")) == expected:
                return output, True
        except (OSError, json.JSONDecodeError):
            pass
    command = [
        "ffmpeg", "-y", "-hide_banner", "-i", str(source),
        "-map", "0:v:0", "-map", "0:a?",
        "-vf", f"scale='min({max_width},iw)':-2:flags=lanczos",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(output),
    ]
    subprocess.run(command, check=True)
    manifest.write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output, False


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a cached CutCraft editing proxy")
    parser.add_argument("source", type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--max-width", type=int, default=1280)
    parser.add_argument("--crf", type=int, default=24)
    args = parser.parse_args()
    source = args.source.expanduser().resolve()
    if not source.is_file():
        parser.error(f"source file not found: {source}")
    if args.max_width < 320 or not 0 <= args.crf <= 51:
        parser.error("invalid proxy size or CRF")
    output, cached = create_proxy(source, args.work_dir, max_width=args.max_width, crf=args.crf)
    print(f"{'Cached' if cached else 'Created'}: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
