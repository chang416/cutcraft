#!/usr/bin/env python3
"""Delete generated process artifacts only after explicit user authorization."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove children of one marked CutCraft 過程 directory")
    parser.add_argument("work_dir", type=Path)
    parser.add_argument("--confirm-delete-process-files", action="store_true",
                        help="Required explicit safety flag after the user asks to delete process files")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    work_dir = args.work_dir.expanduser().resolve()
    if not args.confirm_delete_process_files and not args.dry_run:
        raise SystemExit("Refusing cleanup without --confirm-delete-process-files")
    if not work_dir.is_dir() or work_dir.name != "過程" or work_dir.parent.parent.name != "CutCraft":
        raise SystemExit(f"Refusing unsafe target: {work_dir}")
    if not (work_dir / "creative_brief.json").is_file():
        raise SystemExit("Refusing unmarked process directory: creative_brief.json is missing")
    final_dir = work_dir.parent / "成品"
    if not final_dir.is_dir() or not any(final_dir.glob("*_final.mp4")):
        raise SystemExit("Refusing cleanup before a final delivery exists in 成品/")
    for path in sorted(work_dir.iterdir(), key=lambda item: item.name):
        print(f"REMOVE {path}")
        if args.dry_run:
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    print(f"KEEP   {final_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
