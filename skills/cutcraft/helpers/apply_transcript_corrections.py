#!/usr/bin/env python3
"""Apply auditable, text-only corrections to an SRT subtitle file.

The input SRT is parsed before any write.  Numbering, cue order, and timing
lines are treated as immutable; only cue text is changed.  If any requested
correction is invalid, no output is written.

Correction JSON (the value passed to ``--corrections`` may be inline JSON or a
path to a JSON file) uses this format::

    {
      "cue_replacements": [
        {"cue": 12, "replacement": "修正後的整句文字"}
      ],
      "text_replacements": [
        {"old": "錯誤文字", "new": "正確文字"}
      ]
    }

``cue_replacements`` replaces the complete text payload of one cue.  Cue
numbers must exist and may occur at most once in this list.  Each
``text_replacements`` item finds ``old`` across all cue text (or only the cue
identified by its optional ``cue`` field); it must occur exactly once.  An
empty or repeated ``old`` value is rejected.  A mapping is also accepted for
convenience, for example ``{"cue_replacements": {"12": "..."}}`` and
``{"text_replacements": {"old": "new"}}``.  The aliases ``cues``, ``texts``,
and ``exact_replacements`` are accepted, but the list form above is preferred.

Examples::

    python3 helpers/apply_transcript_corrections.py \
        --srt master.srt \
        --corrections '{"cue_replacements":[{"cue":1,"replacement":"你好"}]}'
    python3 helpers/apply_transcript_corrections.py \
        --srt master.srt --corrections fixes.json -o master.corrected.srt
    python3 helpers/apply_transcript_corrections.py \
        --srt master.srt --corrections fixes.json --in-place

The default output is ``<input-stem>.corrected.srt``.  In-place mode creates a
timestamped ``<input-name>.backup-YYYYMMDD-HHMMSS-ffffff`` file first.  An
audit sidecar is always written beside the output as
``<output-path>.changes.json``.
"""

from __future__ import annotations

import argparse
import codecs
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


class CorrectionError(ValueError):
    """Raised when a correction cannot be applied without ambiguity."""


@dataclass(frozen=True)
class _Line:
    start: int
    end: int
    content: str
    ending: str


@dataclass(frozen=True)
class Cue:
    number: int
    number_line: str
    timing_line: str
    text_start: int
    text_end: int
    text: str


_TIMING_RE = re.compile(
    r"^\s*\d{1,3}:\d{2}:\d{2}[,.]\d{3}\s+-->\s+"
    r"\d{1,3}:\d{2}:\d{2}[,.]\d{3}(?:\s+.*)?$"
)


def _split_lines(text: str) -> list[_Line]:
    """Split text while retaining exact offsets and line endings."""

    lines: list[_Line] = []
    offset = 0
    for raw in text.splitlines(keepends=True):
        if raw.endswith("\r\n"):
            content, ending = raw[:-2], "\r\n"
        elif raw.endswith("\n") or raw.endswith("\r"):
            content, ending = raw[:-1], raw[-1:]
        else:
            content, ending = raw, ""
        lines.append(_Line(offset, offset + len(raw), content, ending))
        offset += len(raw)
    # str.splitlines() returns [] for an empty input, which is exactly what the
    # parser needs in order to report an empty/malformed SRT.
    return lines


def parse_srt(text: str) -> list[Cue]:
    """Parse a strict, ordinary SRT structure while preserving source offsets."""

    lines = _split_lines(text)
    blocks: list[list[_Line]] = []
    current: list[_Line] = []
    for line in lines:
        if not line.content.strip():
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)

    cues: list[Cue] = []
    seen_numbers: set[int] = set()
    for block_index, block in enumerate(blocks, start=1):
        if len(block) < 2:
            raise CorrectionError(f"SRT 第 {block_index} 個區塊缺少編號或時間碼。")
        number_line = block[0].content
        if not re.fullmatch(r"\d+", number_line.strip()):
            raise CorrectionError(f"SRT 第 {block_index} 個區塊的 cue 編號無效：{number_line!r}")
        number = int(number_line.strip())
        if number in seen_numbers:
            raise CorrectionError(f"SRT cue 編號重複：{number}")
        seen_numbers.add(number)

        timing_line = block[1].content
        if not _TIMING_RE.fullmatch(timing_line):
            raise CorrectionError(f"SRT cue {number} 的時間碼無效：{timing_line!r}")

        if len(block) >= 3:
            text_start = block[2].start
            # Exclude the final text line's line ending.  Any ending after it
            # remains in the original source and is therefore preserved.
            last = block[-1]
            text_end = last.start + len(last.content)
            cue_text = text[text_start:text_end]
        else:
            text_start = block[1].end
            text_end = text_start
            cue_text = ""
        cues.append(Cue(number, number_line, timing_line, text_start, text_end, cue_text))

    if not cues:
        raise CorrectionError("SRT 沒有可讀取的 cue。")
    return cues


def _read_utf8(path: Path) -> tuple[str, bool]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CorrectionError(f"無法讀取 SRT：{path}（{exc}）") from exc
    has_bom = raw.startswith(codecs.BOM_UTF8)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CorrectionError(f"SRT 必須是有效的 UTF-8：{path}（{exc}）") from exc
    return text, has_bom


def _write_utf8(path: Path, text: str, has_bom: bool) -> None:
    payload = ("\ufeff" if has_bom else "") + text
    try:
        path.write_bytes(payload.encode("utf-8"))
    except OSError as exc:
        raise CorrectionError(f"無法寫入檔案：{path}（{exc}）") from exc


def _as_cue_number(value: Any, where: str) -> int:
    if isinstance(value, bool):
        raise CorrectionError(f"{where} 的 cue 必須是正整數。")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise CorrectionError(f"{where} 的 cue 必須是正整數。") from exc
    if number <= 0 or str(number) != str(value).strip() and not isinstance(value, int):
        # Reject values such as 1.5 and "01.0" while accepting JSON's normal
        # integer representation and a string key such as "12".
        if not (isinstance(value, str) and value.strip().isdigit() and int(value) == number):
            raise CorrectionError(f"{where} 的 cue 必須是正整數。")
    return number


def _normalise_cue_items(raw: Any, field: str) -> list[dict[str, Any]]:
    """Accept canonical list syntax plus a compact cue->replacement mapping."""

    if raw is None:
        return []
    if isinstance(raw, dict):
        items: list[dict[str, Any]] = []
        for cue, replacement in raw.items():
            items.append({"cue": cue, "replacement": replacement})
        return items
    if not isinstance(raw, list):
        raise CorrectionError(f"{field} 必須是陣列或 cue->replacement 物件。")
    items = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise CorrectionError(f"{field}[{index}] 必須是物件。")
        items.append(item)
    return items


def _normalise_text_items(raw: Any, field: str) -> list[dict[str, Any]]:
    """Accept canonical list syntax plus an old->new mapping."""

    if raw is None:
        return []
    if isinstance(raw, dict):
        # A single operation object ({"old": "...", "new": "..."}) is also
        # useful and unambiguous, so accept it in addition to old->new maps.
        if "old" in raw or "new" in raw:
            return [raw]
        return [{"old": old, "new": new} for old, new in raw.items()]
    if not isinstance(raw, list):
        raise CorrectionError(f"{field} 必須是陣列或 old->new 物件。")
    items = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise CorrectionError(f"{field}[{index}] 必須是物件。")
        items.append(item)
    return items


def normalise_corrections(data: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convert documented JSON forms into cue and exact replacement lists."""

    if isinstance(data, list):
        # A list of operation objects is a convenient shorthand.  Operations
        # with cue/replacement are cue edits; old/new are exact edits.
        cue_items: list[dict[str, Any]] = []
        text_items: list[dict[str, Any]] = []
        for index, item in enumerate(data, start=1):
            if not isinstance(item, dict):
                raise CorrectionError(f"corrections[{index}] 必須是物件。")
            if "cue" in item and ("replacement" in item or "text" in item or "new" in item):
                cue_items.append(item)
            elif "old" in item and "new" in item:
                text_items.append(item)
            else:
                raise CorrectionError(f"corrections[{index}] 不是可辨識的修正格式。")
        return cue_items, text_items
    if not isinstance(data, dict):
        raise CorrectionError("corrections JSON 頂層必須是物件。")

    cue_raw = data.get("cue_replacements", data.get("cues"))
    text_raw = data.get(
        "text_replacements",
        data.get("exact_replacements", data.get("texts")),
    )
    cue_items = _normalise_cue_items(cue_raw, "cue_replacements")
    text_items = _normalise_text_items(text_raw, "text_replacements")
    if "replacements" in data:
        shorthand = data["replacements"]
        if isinstance(shorthand, list):
            shorthand_cues, shorthand_texts = normalise_corrections(shorthand)
            cue_items.extend(shorthand_cues)
            text_items.extend(shorthand_texts)
        else:
            # A mapping is unambiguous as old->new text replacements; cue
            # mappings should use the documented cue_replacements key.
            text_items.extend(_normalise_text_items(shorthand, "replacements"))
    unknown = set(data) - {
        "cue_replacements",
        "text_replacements",
        "cues",
        "texts",
        "exact_replacements",
        "replacements",
    }
    if unknown:
        names = ", ".join(sorted(map(str, unknown)))
        raise CorrectionError(f"不支援的 corrections 欄位：{names}")
    return cue_items, text_items


def _extract_replacement(item: dict[str, Any], index: int) -> tuple[int, str]:
    if "cue" not in item:
        raise CorrectionError(f"cue_replacements[{index}] 缺少 cue。")
    number = _as_cue_number(item["cue"], f"cue_replacements[{index}]")
    if "replacement" in item:
        replacement = item["replacement"]
    elif "text" in item:
        replacement = item["text"]
    elif "new" in item:
        replacement = item["new"]
    else:
        raise CorrectionError(f"cue_replacements[{index}] 缺少 replacement。")
    if not isinstance(replacement, str):
        raise CorrectionError(f"cue_replacements[{index}] 的 replacement 必須是文字。")
    return number, replacement


def _extract_text_replacement(item: dict[str, Any], index: int) -> tuple[str, str, int | None]:
    replacement_value = item.get("new", item.get("replacement"))
    if not isinstance(item.get("old"), str) or not isinstance(replacement_value, str):
        raise CorrectionError(f"text_replacements[{index}] 必須包含文字 old 與 new。")
    old, new = item["old"], replacement_value
    if not old:
        raise CorrectionError(f"text_replacements[{index}] 的 old 不可為空字串。")
    cue = None
    if "cue" in item:
        cue = _as_cue_number(item["cue"], f"text_replacements[{index}]")
    return old, new, cue


def apply_corrections(text: str, cue_items: Iterable[dict[str, Any]], text_items: Iterable[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Apply corrections in memory and return new text plus an audit list."""

    cues = parse_srt(text)
    cue_by_number = {cue.number: index for index, cue in enumerate(cues)}
    working = [cue.text for cue in cues]
    changes: list[dict[str, Any]] = []
    replaced_cues: set[int] = set()

    for raw_index, item in enumerate(cue_items, start=1):
        number, replacement = _extract_replacement(item, raw_index)
        if number not in cue_by_number:
            raise CorrectionError(f"cue_replacements[{raw_index}] 指定的 cue 不存在：{number}")
        if number in replaced_cues:
            raise CorrectionError(f"cue_replacements 重複指定 cue：{number}")
        replaced_cues.add(number)
        index = cue_by_number[number]
        old = working[index]
        working[index] = replacement
        changes.append(
            {
                "type": "cue_replacement",
                "cue": number,
                "old": old,
                "new": replacement,
            }
        )

    for raw_index, item in enumerate(text_items, start=1):
        old, new, cue_number = _extract_text_replacement(item, raw_index)
        if cue_number is not None:
            if cue_number not in cue_by_number:
                raise CorrectionError(f"text_replacements[{raw_index}] 指定的 cue 不存在：{cue_number}")
            indices = [cue_by_number[cue_number]]
        else:
            indices = list(range(len(working)))

        occurrences = sum(working[index].count(old) for index in indices)
        if occurrences != 1:
            scope = f" cue {cue_number}" if cue_number is not None else ""
            if occurrences == 0:
                raise CorrectionError(f"text_replacements[{raw_index}] 的 old{scope} 不存在。")
            raise CorrectionError(
                f"text_replacements[{raw_index}] 的 old{scope} 不唯一（找到 {occurrences} 次）。"
            )
        target_index = next(index for index in indices if old in working[index])
        working[target_index] = working[target_index].replace(old, new, 1)
        changes.append(
            {
                "type": "text_replacement",
                "cue": cues[target_index].number,
                "old": old,
                "new": new,
            }
        )

    pieces: list[str] = []
    cursor = 0
    for cue, replacement in zip(cues, working):
        pieces.append(text[cursor : cue.text_start])
        pieces.append(replacement)
        cursor = cue.text_end
    pieces.append(text[cursor:])
    corrected = "".join(pieces)

    # Reparse and compare immutable structure before any file write.  This also
    # rejects replacement strings that accidentally introduce blank cue blocks.
    corrected_cues = parse_srt(corrected)
    if len(corrected_cues) != len(cues):
        raise CorrectionError("修正後 cue 數量改變，已拒絕寫入。")
    for before, after in zip(cues, corrected_cues):
        if before.number != after.number or before.timing_line != after.timing_line:
            raise CorrectionError("修正後 cue 編號或時間碼改變，已拒絕寫入。")
    return corrected, changes


def _load_corrections(value: str) -> Any:
    # A JSON object starts with {/[; avoid treating a long inline JSON string
    # as a filesystem path (which can raise OSError for an overlong filename).
    stripped = value.lstrip()
    if not stripped.startswith(("{", "[")):
        candidate = Path(value).expanduser()
        try:
            if candidate.is_file():
                try:
                    return json.loads(candidate.read_text(encoding="utf-8-sig"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise CorrectionError(f"無法讀取 corrections JSON：{candidate}（{exc}）") from exc
        except OSError:
            # Fall through to json.loads so the user receives a JSON error for
            # an invalid inline value rather than a platform-specific path error.
            pass
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise CorrectionError(f"corrections 不是有效的 JSON 或檔案路徑：{exc}") from exc


def _default_output_path(source: Path) -> Path:
    suffix = source.suffix or ".srt"
    return source.with_name(f"{source.stem}.corrected{suffix}")


def _timestamped_backup(source: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    base = source.with_name(f"{source.name}.backup-{stamp}")
    candidate = base
    index = 2
    while candidate.exists():
        candidate = source.with_name(f"{source.name}.backup-{stamp}-{index}")
        index += 1
    try:
        shutil.copy2(source, candidate)
    except OSError as exc:
        raise CorrectionError(f"無法建立 in-place backup：{candidate}（{exc}）") from exc
    return candidate


def _atomic_write(path: Path, text: str, has_bom: bool) -> None:
    """Write beside the target and atomically replace it after validation."""

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            payload = ("\ufeff" if has_bom else "") + text
            handle.write(payload.encode("utf-8"))
        temporary.replace(path)
    except (OSError, UnicodeEncodeError) as exc:
        try:
            if "temporary" in locals() and temporary.exists():
                temporary.unlink()
        except OSError:
            pass
        raise CorrectionError(f"無法寫入檔案：{path}（{exc}）") from exc


def _build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="對 SRT 套用可稽核的文字修正；編號、cue 順序與時間碼永遠不變。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    parser.add_argument("--srt", required=True, type=Path, help="輸入 SRT 路徑。")
    parser.add_argument(
        "--corrections",
        required=True,
        help="內嵌 corrections JSON，或指向 JSON 檔案的路徑；格式見 --help 說明。",
    )
    parser.add_argument("-o", "--output", type=Path, help="輸出 SRT 路徑（預設 <stem>.corrected.srt）。")
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="直接更新輸入檔；寫入前先建立時間戳 backup。不可與不同的 -o 路徑並用。",
    )
    args = parser.parse_args(argv)

    source = args.srt.expanduser().resolve()
    if not source.is_file():
        parser.error(f"找不到 SRT：{source}")

    if args.in_place:
        if args.output is not None:
            requested_output = args.output.expanduser().resolve()
            if requested_output != source:
                parser.error("--in-place 不可與不同的 -o/--output 路徑並用。")
        output = source
    else:
        output = (args.output.expanduser() if args.output else _default_output_path(source)).resolve()
        if output == source:
            parser.error("輸入與輸出路徑相同；若要覆寫請明確指定 --in-place。")

    try:
        source_text, has_bom = _read_utf8(source)
        corrections_data = _load_corrections(args.corrections)
        cue_items, text_items = normalise_corrections(corrections_data)
        if not cue_items and not text_items:
            raise CorrectionError("corrections 沒有任何可套用的修正。")
        corrected_text, changes = apply_corrections(source_text, cue_items, text_items)

        # The backup is made only after all validation has passed, immediately
        # before the in-place write.  A failed correction therefore cannot
        # leave a misleading backup or partially modified input.
        backup = _timestamped_backup(source) if args.in_place else None
        _atomic_write(output, corrected_text, has_bom)

        audit_path = Path(f"{output}.changes.json")
        audit = {
            "version": 1,
            "source": str(source),
            "output": str(output),
            "backup": str(backup) if backup is not None else None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "changes": changes,
        }
        _atomic_write(audit_path, json.dumps(audit, ensure_ascii=False, indent=2) + "\n", False)
    except CorrectionError as exc:
        parser.error(str(exc))

    print(f"已寫入修正後 SRT：{output}")
    if backup is not None:
        print(f"in-place backup：{backup}")
    print(f"稽核記錄：{audit_path}")
    print(f"修改筆數：{len(changes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
