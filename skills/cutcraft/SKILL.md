---
name: cutcraft
description: "Edit and finish videos by conversation, including footage inspection, take selection, precise cuts, reframing, color, transitions, effects, licensed music/SFX/B-roll, and pixel-safe burned or sidecar subtitles. Use when the user asks to 剪片、剪影片、上字幕、加字幕、改逐字稿、合併影片、做短影音、加音效／配樂／特效, or wants a complete inspect → choose → edit → review → revise workflow."
---

# CutCraft

Turn raw footage and a conversation into one obvious final video. Keep every project inside a named `CutCraft` workspace, show visual subtitle choices before editing, ask the creative questions once, and make transcript corrections immediately reversible.

## Non-negotiable contract

1. **Run the readiness gate first.** On the first use and after environment changes, run `python3 helpers/check_environment.py`. If a required or requested conditional dependency is missing, pause editing, show the user the script's official URL and exact setup instructions, and ask only for the missing approval or credential. Read [references/onboarding.md](references/onboarding.md) for key handling and platform guidance.
2. **Never store secrets in this package or a project.** Store ElevenLabs keys in `~/.config/cutcraft/.env` with mode `600`, or use local Whisper so no key or transcription upload is needed. Never place a key in a command argument, log, transcript, screenshot, repository, plugin cache, or project folder.
3. **Create the workspace before generating anything.** Infer a short project name from the user's description and inspected footage. Confirm only when two materially different names are plausible. Run `helpers/project_workspace.py --name "<name>"` and use its returned paths.
4. **Inspect first, ask once, edit after approval.** Inventory and transcribe the sources, inspect representative visuals, create real subtitle preview images, then ask every remaining creative question in one consolidated checkpoint. Do not begin cutting before the user chooses or accepts recommended defaults.
5. **Show choices visually.** Generate three material-aware subtitle preview images (recommended, clean/narrative, and bold/social unless the footage calls for different choices). State font, size, placement, background, language, and cue rhythm under each image. The user may answer with an option number or adjustments such as `往下 4%` or `字大 15%`.
6. **Keep the final obvious.** Put only delivery artifacts in `成品/`. The primary video must be named `<project>_final.mp4`. Put previews, clips, transcripts, EDLs, downloads, generated assets, and reports in `過程/`.
7. **Export editable text with every captioned delivery.** Deliver `<project>_逐字稿.md` and `<project>.srt` beside the final video. Use stable cue IDs and timestamps so the user can say, for example, `第 18 句改成……`.
8. **Apply transcript fixes immediately.** Resolve the requested cues, run `helpers/apply_transcript_corrections.py` against `過程/master.srt`, re-run validation, re-render the final video with the corrected SRT, regenerate the transcript, and replace the delivery artifacts. Preserve timings unless the user reports a sync problem. Do not re-transcribe unchanged source media.
9. **No review front-end.** Do not start a server or create browser UI files. All review happens through delivered video/image files and conversational corrections.
10. **Make work resumable.** Update `cutcraft.project.json` at every checkpoint. Preserve source signatures, approval state, revisions, final paths, and asset provenance. Use proxies only for inspection; always render from originals.
11. **Verify before delivery.** Run deterministic preflight and `quality_report.py`, inspect the rendered output at cut boundaries and representative subtitle moments, listen to the final mix, and verify output duration/streams. If a cut removed a stutter or false start, re-transcribe the rendered section to prove it is gone.

## Project layout

Default root: `~/Documents/Codex/CutCraft/` (Finder-visible; keep the installable skill itself under `~/.codex`).

```text
CutCraft/
└── <project>/
    ├── cutcraft.project.json
    ├── 成品/
    │   ├── <project>_final.mp4
    │   ├── <project>_逐字稿.md
    │   └── <project>.srt
    └── 過程/
        ├── project.md
        ├── creative_brief.json
        ├── inventory.json
        ├── takes_packed.md
        ├── edl.json
        ├── master.srt
        ├── transcript_corrections.json
        ├── asset_registry.json
        ├── transcripts/
        ├── clips/ and clips_*/
        ├── assets/
        ├── animations/
        ├── proxies/
        ├── downloads/
        └── verify/
```

Never duplicate user source footage into the project unless the user asks. Record absolute source paths in `edl.json` and leave originals untouched.

## One consolidated checkpoint

Inspect enough material to make recommendations, render the subtitle comparison images, then ask all applicable items together:

- Goal, intended audience, platform, aspect ratio, resolution, target length, deadline-sensitive format.
- Story/order, must-keep lines, must-cut material, names/terms that ASR must spell exactly.
- Pace, crop/reframe, color mood, transitions, effects, overlays, B-roll, censoring/privacy needs.
- Subtitles: burned-in/sidecar/both, language or bilingual mode, verbatim/cleaned, font, size, position, background, cue rhythm, on-screen UI safe zones.
- Audio: cleanup, loudness, supplied music/SFX/voice-over, or desired mood and intensity.
- Asset source: user-provided files, generated assets, or specific free libraries. If free material is requested, offer the suitable choices in [references/free-assets.md](references/free-assets.md), ask the user to choose, and register the item, creator, URL, license, attribution, and checksum with `asset_registry.py`.
- Transcription privacy: recommend local Whisper when privacy matters; otherwise offer ElevenLabs for managed diarization. Explain upload behavior before sending media to any cloud provider.
- Editorial autonomy and anything explicitly forbidden.

Attach the preview images in this checkpoint. Give a recommended default for every choice and include `全部用建議值` as a valid answer. Persist the answer in `過程/creative_brief.json`; do not reopen settled questions mid-edit.

## Workflow

1. Run readiness checks and resolve only missing requirements.
2. Inspect filenames and the user's description; infer a project name and create the workspace.
3. Run `inventory.py --work-dir <過程> <sources_dir>`. For long or 4K sources, create cached proxies for inspection. Run `analyze_media.py` to locate shot changes, silence, black, and frozen intervals.
4. Transcribe into `<過程>/transcripts/` with the approved cloud/local provider, then pack transcripts. Read the packed transcript, sample early/middle/late frames and ambiguous edit points, and note ASR spelling risks.
5. Generate three subtitle style previews from representative frames. Ask the consolidated checkpoint and save the approved brief.
6. Build `edl.json`, source or generate approved assets, and edit without drip-feeding ordinary taste questions.
7. Build captions. For Chinese, use `build_cn_subtitles.py`; for other languages, use `render.py --build-subtitles`. Follow [references/subtitles.md](references/subtitles.md).
8. Run `validate_project.py`, render an evaluable preview in `過程/verify/`, and self-review. Cap fix/re-render loops at three before reporting a stubborn issue.
9. Render `<成品>/<project>_final.mp4` from original sources, copy the final SRT to `<成品>/<project>.srt`, and generate `<成品>/<project>_逐字稿.md` with `export_transcript.py`.
10. Run `quality_report.py`, resolve every error, visually and audibly review warnings, then mark the manifest delivered with `project_state.py`.
11. Deliver the three explicit paths, QC report, concise edit summary, and asset provenance. Never make the user search for `final` among intermediates.

## Production correctness

- Apply subtitles last, after every overlay and crop.
- Derive cut candidates from word boundaries and silence; never cut inside a word. Use 30–200 ms padding appropriate to the pace.
- Apply short audio fades at segment boundaries to prevent clicks.
- Normalize canvas, frame rate, pixel format, color space, sample rate, and channel layout before joining clips.
- Cache transcripts and proxies by source path, size, and modification time; re-run only when that signature changes.
- Calculate subtitle timestamps on the output timeline, not the source timeline.
- Guarantee pixel-safe single-line cues when single-line is approved; validate against the actual font and canvas.
- Use only user-provided, generated, public-domain, or license-compatible assets. Record title, creator, source URL, license, download date, and required attribution.
- Mix dialogue first. Duck music beneath speech and listen; loudness normalization cannot rescue a poor mix.
- Keep the main agent as owner of the creative brief, EDL, caption timing, final timeline, mix, and QC. Parallelize only immutable-input work that writes unique paths.

Read [references/editorial-craft.md](references/editorial-craft.md) when the project needs multi-take selection, transitions, grading, animation, speed ramps, or music-led editing. Read [references/edl.md](references/edl.md) before authoring or extending the EDL.

## Helpers

- `check_environment.py` — required/conditional dependency and credential readiness with official setup URLs.
- `project_workspace.py` — create or reuse the named `成品/` + `過程/` project.
- `project_state.py` — atomically record resumable checkpoints, approvals, revisions, and output paths.
- `inventory.py` — probe source formats and normalization risks.
- `analyze_media.py` — detect scene changes, silence, black frames, and frozen frames.
- `create_proxy.py` — cached lightweight inspection proxies mapped to immutable originals.
- `transcribe.py`, `transcribe_batch.py` — cached word-level ElevenLabs or local Whisper transcription.
- `pack_transcripts.py` — compact phrase-level editing view.
- `timeline_view.py` — filmstrip, waveform, words, and cut-point inspection.
- `subtitle_style_preview.py` — exact libass style/placement preview on real frames.
- `build_cn_subtitles.py` — Chinese-aware pixel-width cue construction.
- `render.py` — normalize, cut, join, overlay, mix, then subtitle last.
- `validate_project.py` — fail-fast EDL, asset, timing, and caption checks.
- `asset_registry.py` — fail-closed asset license, attribution, and checksum registry.
- `quality_report.py` — final stream, duration, black/freeze/silence, and QC evidence report.
- `build_cut_report.py` — categorized cut summary.
- `apply_transcript_corrections.py` — fail-closed cue/text corrections with audit log and backup.
- `export_transcript.py` — readable, cue-addressable transcript for delivery.
- `cleanup_session.py` — optional cleanup only after the user explicitly asks to delete process files; never interpret `完成` as deletion.

Resolve every helper and reference relative to the directory containing this `SKILL.md`.

## Revision behavior

Treat the user's delivered transcript as the wording source of truth after they correct it. For wording-only fixes, keep cue timing and edit decisions, patch the SRT, validate, and re-render. For sync reports, inspect the affected rendered window and adjust only those cue boundaries. For editorial changes, update the EDL and cut report, then run the full affected render/QC path.

Append each session to `過程/project.md` with strategy, approved decisions, asset provenance, revisions, QC evidence, and outstanding issues. Preserve `過程/` by default so future revisions remain cheap and traceable.
