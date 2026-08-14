#!/usr/bin/env python3
"""Maintain a fail-closed provenance registry for music, SFX, B-roll, and generated assets."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path


REGISTRY_NAME = "asset_registry.json"
REQUIRED = ("title", "kind", "source", "license")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_registry(work_dir: Path) -> dict:
    path = work_dir / REGISTRY_NAME
    if not path.exists():
        return {"schema_version": 1, "assets": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("assets"), list):
        raise ValueError(f"invalid asset registry: {path}")
    return data


def validate_entry(entry: dict) -> list[str]:
    problems = [f"missing {field}" for field in REQUIRED if not str(entry.get(field, "")).strip()]
    local = entry.get("local_path")
    if local and not Path(local).is_file():
        problems.append(f"local file missing: {local}")
    if entry.get("license") not in {"user-provided", "generated", "public-domain"} and not entry.get("license_url"):
        problems.append("license_url is required for third-party licensed assets")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Register or validate CutCraft asset provenance")
    parser.add_argument("work_dir", type=Path)
    parser.add_argument("--add", action="store_true")
    parser.add_argument("--title")
    parser.add_argument("--kind", choices=("music", "sfx", "b-roll", "image", "font", "voice", "other"))
    parser.add_argument("--source")
    parser.add_argument("--license")
    parser.add_argument("--license-url")
    parser.add_argument("--creator", default="")
    parser.add_argument("--attribution", default="")
    parser.add_argument("--local-path", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    work_dir = args.work_dir.expanduser().resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    registry = load_registry(work_dir)
    if args.add:
        entry = {
            "title": args.title or "",
            "kind": args.kind or "",
            "source": args.source or "",
            "license": args.license or "",
            "license_url": args.license_url or "",
            "creator": args.creator,
            "attribution": args.attribution,
            "acquired_at": date.today().isoformat(),
        }
        if args.local_path:
            local = args.local_path.expanduser().resolve()
            entry["local_path"] = str(local)
            if local.is_file():
                entry["sha256"] = sha256(local)
        problems = validate_entry(entry)
        if problems:
            parser.error("; ".join(problems))
        registry["assets"].append(entry)
        (work_dir / REGISTRY_NAME).write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    all_problems = []
    for index, entry in enumerate(registry["assets"], start=1):
        all_problems.extend(f"asset {index}: {problem}" for problem in validate_entry(entry))
    if args.json:
        print(json.dumps({"registry": registry, "valid": not all_problems, "problems": all_problems}, ensure_ascii=False, indent=2))
    else:
        print(f"Assets: {len(registry['assets'])}; {'valid' if not all_problems else 'invalid'}")
        for problem in all_problems:
            print(f"- {problem}")
    return 1 if all_problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
