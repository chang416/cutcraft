"""Transcribe video with ElevenLabs Scribe or an optional local Whisper engine.

Extracts mono 16kHz audio with FFmpeg, uses the selected cloud or local
provider with word timestamps, and writes a normalized response to
<work_dir>/transcripts/<video_stem>.json.

Cached: if the output file already exists, the upload is skipped.

Usage:
    python helpers/transcribe.py <video_path>
    python helpers/transcribe.py <video_path> --work-dir /path/to/project/過程
    python helpers/transcribe.py <video_path> --language en
    python helpers/transcribe.py <video_path> --num-speakers 2
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests


SCRIBE_URL = "https://api.elevenlabs.io/v1/speech-to-text"


def find_api_keys() -> list[str]:
    """Load one or more ElevenLabs keys.

    Preferred: `ELEVENLABS_API_KEYS=key1,key2,key3` in a private .env — tried in order,
    falling to the next key when one is out of quota (see call_scribe_with_fallback).
    A lone `ELEVENLABS_API_KEY` (singular) also works.
    Env vars take precedence over .env if both are set for the same name.
    """
    found: str | None = None
    for candidate in [
        Path.home() / ".config" / "cutcraft" / ".env",
    ]:
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                if k in ("ELEVENLABS_API_KEYS", "ELEVENLABS_API_KEY"):
                    found = v.strip().strip('"').strip("'")
            if found:
                break

    env_val = os.environ.get("ELEVENLABS_API_KEYS") or os.environ.get("ELEVENLABS_API_KEY")
    if env_val:
        found = env_val

    if not found:
        return []

    keys = [k.strip() for k in found.split(",") if k.strip()]
    if not keys:
        return []
    return keys


def extract_audio(video_path: Path, dest: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(dest),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def source_signature(video: Path) -> dict[str, int | str]:
    """Cheap cache fingerprint: absolute path, byte size, and nanosecond mtime."""
    stat = video.stat()
    return {
        "path": str(video.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def cache_is_current(video: Path, transcript_path: Path) -> bool:
    if not transcript_path.exists():
        return False
    try:
        cached = json.loads(transcript_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return cached.get("_cutcraft_source") == source_signature(video)


def available_local_provider() -> str | None:
    if importlib.util.find_spec("mlx_whisper"):
        return "mlx-whisper"
    if importlib.util.find_spec("whisper"):
        return "openai-whisper"
    return None


def normalize_local_result(payload: dict, provider: str) -> dict:
    words: list[dict] = []
    for segment in payload.get("segments", []):
        segment_words = segment.get("words") or []
        if segment_words:
            for item in segment_words:
                text = str(item.get("word", "")).strip()
                if text and item.get("start") is not None and item.get("end") is not None:
                    words.append({
                        "text": text,
                        "start": float(item["start"]),
                        "end": float(item["end"]),
                        "type": "word",
                        "speaker_id": None,
                    })
        else:
            text = str(segment.get("text", "")).strip()
            if text and segment.get("start") is not None and segment.get("end") is not None:
                words.append({
                    "text": text,
                    "start": float(segment["start"]),
                    "end": float(segment["end"]),
                    "type": "word",
                    "speaker_id": None,
                })
    return {
        "language_code": payload.get("language"),
        "text": str(payload.get("text", "")).strip(),
        "words": words,
        "_cutcraft_provider": provider,
    }


def call_local_whisper(audio_path: Path, provider: str, model: str, language: str | None) -> dict:
    kwargs = {"word_timestamps": True}
    if language:
        kwargs["language"] = language
    if provider == "mlx-whisper":
        import mlx_whisper  # type: ignore

        payload = mlx_whisper.transcribe(str(audio_path), path_or_hf_repo=model, **kwargs)
    elif provider == "openai-whisper":
        import whisper  # type: ignore

        payload = whisper.load_model(model).transcribe(str(audio_path), **kwargs)
    else:
        raise RuntimeError(f"unsupported local provider: {provider}")
    return normalize_local_result(payload, provider)


def call_scribe(
    audio_path: Path,
    api_key: str,
    language: str | None = None,
    num_speakers: int | None = None,
) -> dict:
    data: dict[str, str] = {
        "model_id": "scribe_v1",
        "diarize": "true",
        "tag_audio_events": "true",
        "timestamps_granularity": "word",
    }
    if language:
        data["language_code"] = language
    if num_speakers:
        data["num_speakers"] = str(num_speakers)

    with open(audio_path, "rb") as f:
        resp = requests.post(
            SCRIBE_URL,
            headers={"xi-api-key": api_key},
            files={"file": (audio_path.name, f, "audio/wav")},
            data=data,
            timeout=1800,
        )

    if resp.status_code != 200:
        raise RuntimeError(f"Scribe returned {resp.status_code}: {resp.text[:500]}")

    return resp.json()


def call_scribe_with_fallback(
    audio_path: Path,
    api_keys: list[str],
    language: str | None = None,
    num_speakers: int | None = None,
    verbose: bool = True,
) -> dict:
    """Try each key in order; move to the next on any failure (quota
    exhaustion, auth, rate limit — Scribe doesn't distinguish these cleanly
    enough to special-case, so any non-200 rotates to the next key). Raises
    the last error if every key fails.
    """
    last_err: Exception | None = None
    for i, key in enumerate(api_keys):
        try:
            result = call_scribe(audio_path, key, language, num_speakers)
            if verbose and i > 0:
                print(f"  (used fallback key #{i + 1} of {len(api_keys)})")
            return result
        except RuntimeError as e:
            last_err = e
            if verbose:
                print(f"  key #{i + 1} of {len(api_keys)} failed ({e}); trying next" if i + 1 < len(api_keys) else f"  key #{i + 1} of {len(api_keys)} failed ({e}); no more keys")
    assert last_err is not None
    raise last_err


def transcribe_one(
    video: Path,
    edit_dir: Path,
    api_key: str | list[str] | None,
    language: str | None = None,
    num_speakers: int | None = None,
    verbose: bool = True,
    provider: str = "elevenlabs",
    model: str | None = None,
) -> Path:
    """Transcribe a single video. Returns path to transcript JSON.

    Cached only while the source signature still matches. Replaced/modified
    footage is transcribed again instead of silently reusing stale timings.
    """
    transcripts_dir = edit_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    out_path = transcripts_dir / f"{video.stem}.json"

    signature = source_signature(video)
    if out_path.exists():
        if cache_is_current(video, out_path):
            if verbose:
                print(f"cached: {out_path.name}")
            return out_path
        if verbose:
            print(f"  cache stale for {video.name}; source changed or cache has no signature")

    if verbose:
        print(f"  extracting audio from {video.name}", flush=True)

    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / f"{video.stem}.wav"
        extract_audio(video, audio)
        size_mb = audio.stat().st_size / (1024 * 1024)
        if provider == "elevenlabs":
            if verbose:
                print(f"  uploading {video.stem}.wav ({size_mb:.1f} MB)", flush=True)
            if api_key is None:
                raise RuntimeError("ElevenLabs provider selected without a key")
            keys = api_key if isinstance(api_key, list) else [api_key]
            payload = call_scribe_with_fallback(audio, keys, language, num_speakers, verbose=verbose)
            payload["_cutcraft_provider"] = "elevenlabs"
        else:
            if verbose:
                print(f"  transcribing locally with {provider} ({model})", flush=True)
            payload = call_local_whisper(audio, provider, model or "small", language)

    if isinstance(payload, dict):
        payload["_cutcraft_source"] = signature
    out_path.write_text(json.dumps(payload, indent=2))
    dt = time.time() - t0

    if verbose:
        kb = out_path.stat().st_size / 1024
        print(f"  saved: {out_path.name} ({kb:.1f} KB) in {dt:.1f}s")
        if isinstance(payload, dict) and "words" in payload:
            print(f"    words: {len(payload['words'])}")

    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Transcribe a video with ElevenLabs or local Whisper")
    ap.add_argument("video", type=Path, help="Path to video file")
    ap.add_argument(
        "--work-dir",
        dest="edit_dir",
        type=Path,
        required=True,
        help="CutCraft project process directory (過程).",
    )
    ap.add_argument(
        "--provider", choices=("auto", "elevenlabs", "local"), default="auto",
        help="auto prefers a configured ElevenLabs key, then local Whisper; local never uploads media.",
    )
    ap.add_argument(
        "--model", default=None,
        help="Local model name or MLX model path/repository (default: small, or mlx-community/whisper-small-mlx).",
    )
    ap.add_argument(
        "--language",
        type=str,
        default=None,
        help="Optional ISO language code (e.g., 'en'). Omit to auto-detect.",
    )
    ap.add_argument(
        "--num-speakers",
        type=int,
        default=None,
        help="Optional number of speakers when known. Improves diarization accuracy.",
    )
    args = ap.parse_args()

    video = args.video.resolve()
    if not video.exists():
        sys.exit(f"video not found: {video}")

    edit_dir = args.edit_dir.resolve()
    keys = find_api_keys()
    local = available_local_provider()
    if args.provider == "elevenlabs":
        if not keys:
            ap.error("ElevenLabs provider selected but no key is configured")
        provider = "elevenlabs"
    elif args.provider == "local":
        if not local:
            ap.error("local provider selected but neither mlx-whisper nor openai-whisper is installed")
        provider = local
    elif keys:
        provider = "elevenlabs"
    elif local:
        provider = local
    else:
        ap.error("no transcription provider is ready; run check_environment.py for setup choices")

    model = args.model
    if provider == "mlx-whisper" and model is None:
        model = "mlx-community/whisper-small-mlx"
    elif provider == "openai-whisper" and model is None:
        model = "small"

    transcribe_one(
        video=video,
        edit_dir=edit_dir,
        api_key=keys or None,
        language=args.language,
        num_speakers=args.num_speakers,
        provider=provider,
        model=model,
    )


if __name__ == "__main__":
    main()
