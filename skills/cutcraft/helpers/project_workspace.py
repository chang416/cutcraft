#!/usr/bin/env python3
"""Create and describe an CutCraft project workspace.

The helper intentionally only creates directories.  It never copies, moves, or
deletes a source video or any other existing file.

Examples::

    python3 helpers/project_workspace.py --name "台北街訪"
    python3 helpers/project_workspace.py --root /tmp/cutcraft --name "demo" --new
    python3 helpers/project_workspace.py --name "demo" --json
"""

from __future__ import annotations

import argparse
import json
import unicodedata
from pathlib import Path

from project_state import MANIFEST_NAME, atomic_write_json, initial_state


# Keep deliverables Finder-visible. The installable skill itself can live under
# ~/.codex, but users should not have to browse a hidden system folder for
# their finished videos or revision files.
DEFAULT_ROOT = Path.home() / "Documents" / "Codex" / "CutCraft"
WORK_DIR_NAME = "過程"
FINAL_DIR_NAME = "成品"
WORK_SUBDIRECTORIES = (
    "transcripts",
    "clips",
    "assets",
    "verify",
    "downloads",
    "animations",
    "proxies",
)


class ProjectNameError(ValueError):
    """Raised when a project name cannot safely become one directory name."""


def sanitize_project_name(name: str) -> str:
    """Return a safe, readable single-directory project name.

    Chinese and other Unicode letters/digits, ordinary spaces, and ASCII
    hyphens are retained.  Other punctuation is discarded after Unicode NFC
    normalization.  A slash or backslash is rejected before sanitization so a
    caller cannot accidentally turn a project name into a nested path.

    ``.`` and ``..`` are rejected explicitly, as are values that become empty
    after unsupported characters are removed.
    """

    if not isinstance(name, str):
        raise ProjectNameError("專案名稱必須是文字。")

    raw = unicodedata.normalize("NFC", name)
    stripped = raw.strip()
    if not stripped:
        raise ProjectNameError("專案名稱不可為空白。")
    if stripped in {".", ".."}:
        raise ProjectNameError("專案名稱不可為 '.' 或 '..'。")
    if "/" in raw or "\\" in raw:
        raise ProjectNameError("專案名稱不可包含路徑分隔符 '/' 或 '\\'。")

    # Keep Unicode letters/digits (including CJK), spaces, and hyphens.  Using
    # str.isalnum() instead of an ASCII-only regex is deliberate here.
    kept = "".join(
        character
        for character in raw
        if character.isalnum() or character in {" ", "\t", "-"}
    )
    # Tabs are harmless in a path but make CLI output and shell use awkward;
    # normalize all whitespace runs to one ordinary space.
    kept = " ".join(kept.split())
    kept = kept.strip()
    if not kept:
        raise ProjectNameError("專案名稱不含可用的中文、英數、空格或連字號。")
    if kept in {".", ".."}:
        raise ProjectNameError("專案名稱不可為 '.' 或 '..'。")
    return kept


def _make_project_directory(root: Path, project_name: str, new: bool) -> tuple[Path, bool]:
    """Select a project directory, atomically reserving a ``--new`` suffix."""

    root.mkdir(parents=True, exist_ok=True)
    base = root / project_name

    if not new:
        if base.exists() and not base.is_dir():
            raise OSError(f"專案路徑已存在但不是資料夾：{base}")
        created = not base.exists()
        base.mkdir(parents=False, exist_ok=True)
        return base, created

    # A missing base name is still the most stable name for the first --new
    # invocation.  For an existing project, reserve project-2, project-3, ...
    # with mkdir(exist_ok=False) so an existing directory is never overwritten.
    candidate = base
    suffix = 2
    while True:
        try:
            candidate.mkdir(parents=False, exist_ok=False)
            return candidate, True
        except FileExistsError:
            if not candidate.is_dir():
                # A file/symlink at the candidate is also occupied; keep
                # looking for the next stable suffix rather than touching it.
                pass
            candidate = root / f"{project_name}-{suffix}"
            suffix += 1


def create_project_workspace(root: Path, name: str, new: bool = False) -> dict[str, object]:
    """Create the requested workspace and return its machine-readable summary."""

    project_name = sanitize_project_name(name)
    root = root.expanduser().resolve()

    # Determine whether this invocation is reusing an existing directory.  The
    # helper below creates it atomically for --new; for the default path the
    # pre-check avoids reporting a newly-created workspace as reused.
    base = root / project_name
    existed_before = base.exists()
    project_dir, created = _make_project_directory(root, project_name, new)
    if not new and existed_before:
        created = False

    final_dir = project_dir / FINAL_DIR_NAME
    work_dir = project_dir / WORK_DIR_NAME
    final_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    subdirectories: dict[str, str] = {}
    for directory_name in WORK_SUBDIRECTORIES:
        directory = work_dir / directory_name
        directory.mkdir(parents=True, exist_ok=True)
        subdirectories[directory_name] = str(directory)

    manifest_path = project_dir / MANIFEST_NAME
    if not manifest_path.exists():
        atomic_write_json(manifest_path, initial_state(project_dir.name, project_dir))

    return {
        "project_name": project_dir.name,
        "project_dir": str(project_dir),
        "final_dir": str(final_dir),
        "work_dir": str(work_dir),
        "work_subdirectories": subdirectories,
        "manifest": str(manifest_path),
        "created": created,
        "reused": not created,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="建立 CutCraft 專案工作區（只建立資料夾，不搬移或刪除檔案）。"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help=f"工作區根目錄（預設：{DEFAULT_ROOT}）。",
    )
    parser.add_argument("--name", required=True, help="專案名稱（可含中文、英數、空格、連字號）。")
    parser.add_argument(
        "--new",
        action="store_true",
        help="同名專案已存在時，使用穩定的 -2、-3… 後綴建立新專案。",
    )
    parser.add_argument("--json", action="store_true", help="只輸出機器可讀的 JSON 摘要。")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        summary = create_project_workspace(args.root, args.name, new=args.new)
    except (OSError, ProjectNameError) as exc:
        parser.error(str(exc))

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        state = "新建" if summary["created"] else "沿用"
        print(f"CutCraft 專案工作區已{state}：{summary['project_name']}")
        print(f"  專案：{summary['project_dir']}")
        print(f"  成品：{summary['final_dir']}")
        print(f"  過程：{summary['work_dir']}")
        print("  子資料夾：" + ", ".join(WORK_SUBDIRECTORIES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
