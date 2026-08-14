#!/usr/bin/env python3
"""Check CutCraft readiness and print first-use setup guidance.

This command is read-only. It never installs packages, spends API credits, or
prints secret values. Run it before the first edit and after environment changes.

Usage:
    python3 helpers/check_environment.py
    python3 helpers/check_environment.py --json
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def result(name: str, ok: bool, detail: str, *, required: str, url: str, instructions: str) -> dict:
    return {
        "name": name,
        "ok": ok,
        "required": required,
        "detail": detail,
        "url": url,
        "instructions": instructions,
    }


def check_python() -> dict:
    version = platform.python_version()
    ok = sys.version_info >= (3, 10)
    return result(
        "Python 3.10+", ok, f"running {version}", required="core",
        url="https://www.python.org/downloads/",
        instructions="Install Python 3.10 or newer, then run this checker again.",
    )


def check_ffmpeg() -> dict:
    path = shutil.which("ffmpeg")
    if not path:
        return result(
            "FFmpeg with libass", False, "ffmpeg is not on PATH", required="core",
            url="https://ffmpeg.org/download.html",
            instructions="Install an FFmpeg build that includes the subtitles and ass filters. On macOS with Homebrew, use ffmpeg-full when the standard formula lacks libass.",
        )
    try:
        proc = subprocess.run([path, "-filters"], capture_output=True, text=True, timeout=15)
        filters = proc.stdout + proc.stderr
        has_subtitles = any(line.split()[-1:] == ["subtitles"] or " subtitles " in line for line in filters.splitlines())
        has_ass = any(line.split()[-1:] == ["ass"] or " ass " in line for line in filters.splitlines())
    except Exception as exc:
        return result(
            "FFmpeg with libass", False, f"found at {path} but could not inspect filters: {exc}", required="core",
            url="https://ffmpeg.org/download.html", instructions="Reinstall or repair FFmpeg, then re-run the checker.",
        )
    ok = proc.returncode == 0 and has_subtitles and has_ass
    detail = f"found at {path}; subtitles={'yes' if has_subtitles else 'no'}, ass={'yes' if has_ass else 'no'}"
    return result(
        "FFmpeg with libass", ok, detail, required="core",
        url="https://ffmpeg.org/download.html",
        instructions="Install an FFmpeg build with libass. Verify that `ffmpeg -filters` lists both subtitles and ass.",
    )


def check_command(name: str, command: str, *, required: str, url: str, instructions: str) -> dict:
    path = shutil.which(command)
    return result(name, bool(path), f"found at {path}" if path else f"{command} is not on PATH", required=required, url=url, instructions=instructions)


def check_python_deps() -> dict:
    modules = {"requests": "requests", "librosa": "librosa", "matplotlib": "matplotlib", "Pillow": "PIL", "numpy": "numpy"}
    missing = []
    for label, module in modules.items():
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(label)
    return result(
        "Python packages", not missing,
        "all core packages import" if not missing else f"missing: {', '.join(missing)}",
        required="core", url="https://docs.astral.sh/uv/getting-started/installation/",
        instructions="From the CutCraft repository run `uv sync`; if uv is unavailable, run `python3 -m pip install -e .`.",
    )


def configured_key_location() -> str | None:
    if os.environ.get("ELEVENLABS_API_KEYS") or os.environ.get("ELEVENLABS_API_KEY"):
        return "environment"
    candidates = [
        Path.home() / ".config" / "cutcraft" / ".env",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("ELEVENLABS_API_KEYS=") or stripped.startswith("ELEVENLABS_API_KEY="):
                if stripped.split("=", 1)[1].strip().strip('"').strip("'"):
                    return str(path)
    return None


def check_elevenlabs_key() -> dict:
    location = configured_key_location()
    return result(
        "ElevenLabs API key", bool(location),
        f"configured in {location}" if location else "not configured (the key value was not inspected or printed)",
        required="conditional: ElevenLabs cloud transcription",
        url="https://elevenlabs.io/docs/overview/administration/workspaces/api-keys",
        instructions="Create a restricted key with speech-to-text access and a credit limit. Save it with `python3 helpers/configure_api_key.py` to ~/.config/cutcraft/.env; never put it in the repository or a project.",
    )


def local_asr_provider() -> str | None:
    for module, label in (("mlx_whisper", "mlx-whisper"), ("whisper", "openai-whisper")):
        try:
            if importlib.util.find_spec(module):
                return label
        except (ImportError, ValueError):
            continue
    return None


def check_local_asr() -> dict:
    provider = local_asr_provider()
    apple = platform.system() == "Darwin" and platform.machine() == "arm64"
    suggested = "uv sync --extra local-asr-macos" if apple else "uv sync --extra local-asr"
    return result(
        "Local Whisper", bool(provider),
        f"available through {provider}" if provider else "not installed (optional privacy-first alternative to ElevenLabs)",
        required="conditional: local transcription",
        url="https://github.com/openai/whisper",
        instructions=f"Run `{suggested}` if you want word-timestamp transcription without uploading media. The first use downloads a model.",
    )


def check_transcription_provider() -> dict:
    cloud = configured_key_location()
    local = local_asr_provider()
    ready = bool(cloud or local)
    detail = f"ElevenLabs key in {cloud}" if cloud else (f"local {local}" if local else "neither ElevenLabs nor local Whisper is configured")
    return result(
        "Transcription provider", ready, detail, required="core for automatic transcription",
        url="https://github.com/openai/whisper",
        instructions="Choose one path: save a restricted ElevenLabs key with `python3 helpers/configure_api_key.py`, or install local Whisper with `uv sync --extra local-asr-macos` on Apple Silicon / `uv sync --extra local-asr` elsewhere.",
    )


def check_hardware_encoders() -> dict:
    ffmpeg = shutil.which("ffmpeg")
    found: list[str] = []
    if ffmpeg:
        try:
            proc = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=15)
            text = proc.stdout + proc.stderr
            found = [name for name in ("h264_videotoolbox", "h264_nvenc", "h264_qsv", "h264_vaapi") if name in text]
        except Exception:
            pass
    return result(
        "Hardware H.264 encoder", bool(found),
        ", ".join(found) if found else "none detected; software x264 remains available",
        required="conditional: faster preview renders",
        url="https://ffmpeg.org/ffmpeg-codecs.html",
        instructions="Use a platform FFmpeg build with VideoToolbox, NVENC, QSV, or VAAPI only when faster previews are needed.",
    )


def check_cn_deps() -> dict:
    missing = []
    for module in ("jieba", "opencc"):
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(module)
    return result(
        "Chinese subtitle packages", not missing,
        "jieba and opencc import" if not missing else f"missing: {', '.join(missing)}",
        required="conditional: Chinese captions",
        url="https://docs.astral.sh/uv/concepts/projects/dependencies/#optional-dependencies",
        instructions="Run `uv sync --extra cn-subtitles` only when the project needs Chinese-aware subtitle segmentation.",
    )


def check_node() -> dict:
    path = shutil.which("node")
    detail = "node is not on PATH"
    ok = False
    if path:
        try:
            version = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:
            version = "unknown"
        detail = f"found at {path} ({version})"
        ok = True
    return result(
        "Node.js", ok, detail, required="conditional: HyperFrames or Remotion",
        url="https://nodejs.org/en/download",
        instructions="Install the current Node.js LTS only when an approved animation slot needs HyperFrames or Remotion.",
    )


def collect() -> list[dict]:
    return [
        check_python(),
        check_ffmpeg(),
        check_command("ffprobe", "ffprobe", required="core", url="https://ffmpeg.org/download.html", instructions="Install ffprobe together with FFmpeg."),
        check_python_deps(),
        check_command("fontconfig", "fc-list", required="core", url="https://www.freedesktop.org/wiki/Software/fontconfig/", instructions="Install fontconfig so CutCraft can offer only fonts that are actually installed."),
        check_transcription_provider(),
        check_elevenlabs_key(),
        check_local_asr(),
        check_cn_deps(),
        check_command("yt-dlp", "yt-dlp", required="conditional: permitted URL downloads", url="https://github.com/yt-dlp/yt-dlp#installation", instructions="Install only when the user asks to download permitted online media."),
        check_node(),
        check_hardware_encoders(),
    ]


def is_core(item: dict) -> bool:
    return item["required"] == "core" or item["required"].startswith("core for")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check CutCraft dependencies and first-use credentials without changing the system")
    parser.add_argument("--json", action="store_true", help="Print machine-readable results")
    args = parser.parse_args()
    items = collect()
    core_missing = [item for item in items if is_core(item) and not item["ok"]]
    payload = {"skill": "CutCraft", "results": items, "all_core_ready": not core_missing}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in items:
            status = "READY" if item["ok"] else "MISSING"
            print(f"[{status:7}] {item['name']} ({item['required']}): {item['detail']}")
            if not item["ok"]:
                print(f"          Official: {item['url']}")
                print(f"          Next: {item['instructions']}")
        print()
        if core_missing:
            print(f"CutCraft is waiting on {len(core_missing)} core item(s). Resolve only those needed for this project, then re-run this check.")
        else:
            print("CutCraft core workflow is ready. Conditional items may still be installed later after the user selects those features.")
    return 1 if core_missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
