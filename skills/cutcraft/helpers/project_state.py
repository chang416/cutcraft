#!/usr/bin/env python3
"""Create and update CutCraft's resumable project state file atomically."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MANIFEST_NAME = "cutcraft.project.json"
VALID_STATUSES = {"created", "inspecting", "awaiting-approval", "editing", "reviewing", "delivered", "blocked"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def initial_state(project_name: str, project_dir: Path) -> dict[str, Any]:
    now = utc_now()
    return {
        "schema_version": 1,
        "app": "CutCraft",
        "project_name": project_name,
        "project_dir": str(project_dir.resolve()),
        "status": "created",
        "checkpoint": "workspace-created",
        "created_at": now,
        "updated_at": now,
        "revision": 1,
        "approved_brief": False,
        "source_files": [],
        "final_outputs": [],
        "history": [{"at": now, "status": "created", "checkpoint": "workspace-created", "note": ""}],
    }


def load_state(project_dir: Path) -> dict[str, Any]:
    path = project_dir.resolve() / MANIFEST_NAME
    if not path.is_file():
        raise FileNotFoundError(f"CutCraft project manifest not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("app") != "CutCraft" or data.get("schema_version") != 1:
        raise ValueError(f"unsupported or invalid CutCraft manifest: {path}")
    return data


def update_state(
    project_dir: Path,
    *,
    status: str | None = None,
    checkpoint: str | None = None,
    note: str = "",
    approved_brief: bool | None = None,
    source_files: list[str] | None = None,
    final_outputs: list[str] | None = None,
) -> dict[str, Any]:
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}; choose one of: {', '.join(sorted(VALID_STATUSES))}")
    project_dir = project_dir.resolve()
    state = load_state(project_dir)
    if status is not None:
        state["status"] = status
    if checkpoint is not None:
        state["checkpoint"] = checkpoint
    if approved_brief is not None:
        state["approved_brief"] = approved_brief
    if source_files is not None:
        state["source_files"] = sorted({str(Path(item).expanduser().resolve()) for item in source_files})
    if final_outputs is not None:
        state["final_outputs"] = sorted({str(Path(item).expanduser().resolve()) for item in final_outputs})
    state["revision"] = int(state.get("revision", 0)) + 1
    state["updated_at"] = utc_now()
    state.setdefault("history", []).append({
        "at": state["updated_at"],
        "status": state["status"],
        "checkpoint": state["checkpoint"],
        "note": note,
    })
    atomic_write_json(project_dir / MANIFEST_NAME, state)
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description="Show or update a resumable CutCraft project checkpoint")
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--status", choices=sorted(VALID_STATUSES))
    parser.add_argument("--checkpoint")
    parser.add_argument("--note", default="")
    parser.add_argument("--approve-brief", action="store_true")
    parser.add_argument("--source", action="append", default=None)
    parser.add_argument("--final-output", action="append", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if any((args.status, args.checkpoint, args.note, args.approve_brief, args.source, args.final_output)):
        state = update_state(
            args.project_dir,
            status=args.status,
            checkpoint=args.checkpoint,
            note=args.note,
            approved_brief=True if args.approve_brief else None,
            source_files=args.source,
            final_outputs=args.final_output,
        )
    else:
        state = load_state(args.project_dir)
    if args.json:
        print(json.dumps(state, ensure_ascii=False, indent=2))
    else:
        print(f"{state['project_name']}: {state['status']} / {state['checkpoint']} (revision {state['revision']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
