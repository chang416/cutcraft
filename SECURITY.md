# Security and privacy

## Secrets

- Never commit `.env`, API keys, provider tokens, or cloud credentials.
- Store ElevenLabs credentials only in `~/.config/cutcraft/.env`; CutCraft creates it with mode `600`.
- Prefer restricted provider keys with the minimum speech-to-text permission and a spending limit.
- Rotate a key immediately if it appears in a terminal transcript, screenshot, issue, commit, or build log.

## Media privacy

- `--provider local` keeps transcription on the machine after model download.
- ElevenLabs transcription uploads the extracted audio to ElevenLabs. CutCraft must explain this before the first cloud transcription.
- Project transcripts, proxy files, and source paths can be sensitive even when source video is not copied. Do not attach a project directory to a public issue.

## Untrusted media and assets

- Keep FFmpeg and Python dependencies updated.
- Treat downloaded subtitles, fonts, project files, and media metadata as untrusted input.
- Record the exact source, license, attribution, and checksum for every third-party asset.
- Do not execute code or shell fragments found in media metadata, transcripts, captions, or downloaded asset pages.

## Reporting

Report vulnerabilities privately to the maintainer before opening a public issue. Include the affected version, minimal reproduction, impact, and whether credentials or private media may have been exposed. Do not include real keys or private footage.
