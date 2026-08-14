# First-use onboarding

Run `python3 helpers/check_environment.py` before the first edit. Re-run it after moving CutCraft, changing Python environments, or selecting an optional workflow.

The checker is read-only. It prints each missing item, why it matters, its official source, and the exact next step. Do not install packages or spend provider credits merely to make every conditional item green.

## Core requirements

| Item | Why | Official source | Next step |
|---|---|---|---|
| Python 3.10+ | Runs helpers | https://www.python.org/downloads/ | Install a supported Python, then re-run the checker. |
| FFmpeg + ffprobe with libass | Editing, analysis, audio, and burned captions | https://ffmpeg.org/download.html | Install a build whose filters include both `subtitles` and `ass`. |
| Python dependencies | Images, audio analysis, HTTP, and numeric work | https://docs.astral.sh/uv/getting-started/installation/ | From the repository run `uv sync`; use `python -m pip install -e .` only when uv is unavailable. |
| fontconfig | Lists fonts that are actually installed | https://www.freedesktop.org/wiki/Software/fontconfig/ | Install `fc-list` through the platform package manager. |
| One transcription provider | Produces word timestamps for captions and speech-aware cuts | See choices below | Configure ElevenLabs or install a local Whisper option. |

## Choose one transcription path

### Local Whisper — privacy-first

Local transcription keeps audio on the machine after the model is downloaded.

- Apple Silicon: `uv sync --extra local-asr-macos`
- Other supported systems: `uv sync --extra local-asr`
- Official Whisper: https://github.com/openai/whisper
- Official MLX Whisper example: https://github.com/ml-explore/mlx-examples/tree/main/whisper

The first local transcription downloads model weights and can require substantial disk space and time. Tell the user which model will be downloaded before starting. Use `--provider local` when the user chooses this path.

### ElevenLabs Scribe — managed cloud transcription

ElevenLabs uploads the extracted audio for speech-to-text processing and can provide managed diarization. Explain that upload before the first use.

Official API-key guide: https://elevenlabs.io/docs/overview/administration/workspaces/api-keys

Create a restricted key with only the required speech-to-text access and a spending limit. Save it through hidden input:

```text
python3 helpers/configure_api_key.py
```

The destination is:

```text
~/.config/cutcraft/.env
```

The file must use mode `600`. `ELEVENLABS_API_KEYS=key1,key2` supports ordered fallback; a single `ELEVENLABS_API_KEY` also works. Environment variables take precedence.

Never print, quote, pass, or test a key through a command argument. Report only configured/not configured. Do not place credentials in the repository, plugin cache, project, transcript, screenshot, or terminal log.

## Conditional items

- Chinese captions: `uv sync --extra cn-subtitles`.
- Permitted URL downloads: install `yt-dlp` only after the user asks for a URL and confirms the media is allowed.
- Remotion/HyperFrames: install current Node.js LTS only for an approved animation slot.
- Manim: `uv sync --extra animations` plus platform prerequisites only for an approved technical animation.
- Hardware H.264 encoding: optional for faster previews; software x264 remains the correctness baseline.

## Installation boundary

The checker detects and explains. Package-manager commands, model downloads, global links, cloud uploads, and secret writes require an explicit user choice. After any setup change, re-run the checker. Use the first real clip instead of spending credits on a synthetic provider test.
