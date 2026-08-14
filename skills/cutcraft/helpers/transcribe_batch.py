"""Batch-transcribe video files with ElevenLabs or optional local Whisper.

Walks <videos_dir> for common video extensions, uses the selected cloud or
local provider, and writes transcripts to <work_dir>/transcripts/<name>.json.

Cached per-file: any source that already has a transcript is skipped.

Usage:
    python helpers/transcribe_batch.py <videos_dir>
    python helpers/transcribe_batch.py <videos_dir> --workers 4
    python helpers/transcribe_batch.py <videos_dir> --num-speakers 2
    python helpers/transcribe_batch.py <videos_dir> --work-dir /path/to/project/過程
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from transcribe import available_local_provider, cache_is_current, find_api_keys, transcribe_one


VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".mts", ".m2ts"}


def find_videos(videos_dir: Path) -> list[Path]:
    videos = sorted(
        p for p in videos_dir.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS
    )
    return videos


def main() -> None:
    ap = argparse.ArgumentParser(description="Parallel batch transcription of a videos directory")
    ap.add_argument("videos_dir", type=Path, help="Directory containing source videos")
    ap.add_argument(
        "--work-dir",
        dest="edit_dir",
        type=Path,
        required=True,
        help="CutCraft project process directory (過程).",
    )
    ap.add_argument("--provider", choices=("auto", "elevenlabs", "local"), default="auto")
    ap.add_argument("--model", default=None)
    ap.add_argument("--workers", type=int, default=4, help="Parallel workers (default: 4)")
    ap.add_argument(
        "--language",
        type=str,
        default=None,
        help="Optional ISO language code. Omit to auto-detect per file.",
    )
    ap.add_argument(
        "--num-speakers",
        type=int,
        default=None,
        help="Optional number of speakers. Improves diarization when known.",
    )
    args = ap.parse_args()

    videos_dir = args.videos_dir.resolve()
    if not videos_dir.is_dir():
        sys.exit(f"not a directory: {videos_dir}")

    edit_dir = args.edit_dir.resolve()
    (edit_dir / "transcripts").mkdir(parents=True, exist_ok=True)

    videos = find_videos(videos_dir)
    if not videos:
        sys.exit(f"no videos found in {videos_dir}")

    stems: dict[str, list[Path]] = {}
    for video in videos:
        stems.setdefault(video.stem, []).append(video)
    collisions = {stem: paths for stem, paths in stems.items() if len(paths) > 1}
    if collisions:
        details = "; ".join(f"{stem}: {', '.join(p.name for p in paths)}" for stem, paths in collisions.items())
        sys.exit(
            "duplicate source stems would overwrite transcript caches; rename the files so "
            f"each stem is unique ({details})"
        )

    already_cached = [
        v for v in videos
        if cache_is_current(v, edit_dir / "transcripts" / f"{v.stem}.json")
    ]
    pending = [v for v in videos if v not in already_cached]

    print(f"found {len(videos)} videos ({len(already_cached)} cached, {len(pending)} to transcribe)")
    if not pending:
        print("nothing to do")
        return

    api_keys = find_api_keys()
    local = available_local_provider()
    if args.provider == "elevenlabs":
        if not api_keys:
            ap.error("ElevenLabs provider selected but no key is configured")
        provider = "elevenlabs"
    elif args.provider == "local":
        if not local:
            ap.error("local provider selected but neither mlx-whisper nor openai-whisper is installed")
        provider = local
    elif api_keys:
        provider = "elevenlabs"
    elif local:
        provider = local
    else:
        ap.error("no transcription provider is ready; run check_environment.py")
    if provider != "elevenlabs" and args.workers > 1:
        print("local transcription loads a model per process; limiting workers to 1")
        args.workers = 1
    model = args.model or ("mlx-community/whisper-small-mlx" if provider == "mlx-whisper" else "small")

    print(f"transcribing {len(pending)} files with {args.workers} parallel workers")
    t0 = time.time()

    errors: list[tuple[Path, str]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                transcribe_one,
                video=v,
                edit_dir=edit_dir,
                api_key=api_keys or None,
                language=args.language,
                num_speakers=args.num_speakers,
                verbose=False,
                provider=provider,
                model=model,
            ): v
            for v in pending
        }
        for fut in as_completed(futures):
            v = futures[fut]
            try:
                out = fut.result()
                print(f"  + {v.stem}  →  {out.name}")
            except Exception as e:
                errors.append((v, str(e)))
                print(f"  x {v.stem}  FAILED: {e}")

    dt = time.time() - t0
    print(f"\ndone in {dt:.1f}s")
    if errors:
        print(f"{len(errors)} failures:")
        for v, msg in errors:
            print(f"  {v.name}: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
