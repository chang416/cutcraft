# Competitive research and adopted decisions

Research date: 2026-08-14. Sources below are official repositories or project documentation. CutCraft is an independent implementation; this document records product ideas, not copied code.

## Name assessment

`CutCraft` is stronger than the previous generic name because it describes the durable value—editorial craft—without tying the brand to one model or AI trend. It is short, pronounceable, and works as both a product name and the `$cutcraft` command.

The tradeoff is that it combines two common English words, so it is more descriptive than legally distinctive. A current GitHub/web search did not reveal a dominant open-source video editor using the exact name, but unrelated businesses and products do use “CutCraft” or “Cutcraft.” Repository, package, domain, and trademark availability must be checked again before a public launch; this research is not trademark clearance.

## Sources and decisions

### FFmpeg

Official filter documentation: https://ffmpeg.org/ffmpeg-filters.html

Useful ideas:

- `silencedetect`, `blackdetect`, `freezedetect`, `loudnorm`, `subtitles`, and scene-score selection provide deterministic building blocks without a custom media engine.
- Filters expose timestamps and measured values that can be stored as evidence instead of inferred from filenames.

Adopted in CutCraft:

- `analyze_media.py` records scene changes, silence, black intervals, and frozen intervals.
- `render.py` uses two-pass loudness normalization and applies subtitles after compositing.
- `quality_report.py` stores automatic diagnostics beside human review evidence.

### LosslessCut

Official repository: https://github.com/mifi/lossless-cut

Useful ideas:

- Preserve originals, save project cut segments, show technical stream data, support undo/redo, label segments, and export interoperable edit decisions.
- Proxy unsupported/heavy media for preview while operating on originals for export.
- Distinguish frame-accurate re-encoded cuts from fast keyframe-bound lossless cuts.

Adopted in CutCraft:

- Projects record original absolute paths and source signatures without copying footage.
- `create_proxy.py` produces cached inspection proxies and a mapping back to the original.
- `cutcraft.project.json` stores checkpoints and revision history.
- The EDL remains readable JSON and the preflight rejects ambiguous multi-source output settings.

Deferred:

- Lossless stream-copy rendering is intentionally not the default because effects, reframing, transitions, grading, and burned captions require re-encoding. A future explicit rough-cut mode can add keyframe-aware stream copy.

### Auto-Editor

Official repository: https://github.com/WyattBlue/auto-editor

Official cookbook: https://auto-editor.com/docs/cookbook

Useful ideas:

- Find edit candidates from loudness, motion, and subtitles.
- Keep margins around automatically detected speech instead of cutting mechanically at silence boundaries.
- Hand off edits to professional NLEs rather than forcing every workflow through one renderer.

Adopted in CutCraft:

- Silence and word boundaries inform candidates, while the skill contract forbids cutting inside a word and requires 30–200 ms context padding.
- Analysis proposes decisions; the approved creative brief and EDL remain the source of truth.

Deferred:

- Direct Premiere/Resolve/Final Cut/Kdenlive export should use a tested interchange library and fixture suite before public release. CutCraft does not emit brittle XML merely to claim a checkbox.

### PySceneDetect

Official documentation: https://www.scenedetect.com/docs/latest/

CLI and algorithms: https://www.scenedetect.com/cli/

Useful ideas:

- Content-aware and adaptive detection identify adjacent-frame changes; threshold detection handles fades to black.
- Save scene statistics so thresholds can be tuned rather than treated as universal.
- Downscale analysis for speed while retaining source timecodes.

Adopted in CutCraft:

- `analyze_media.py` uses FFmpeg's scene score and records the chosen threshold in the report.
- Scene results are recommendations for representative frames and cut inspection, not automatic editorial truth.

Optional future path:

- PySceneDetect can be added as a conditional backend when adaptive detection or VFR-specific backends materially improve a project.

### OpenAI Whisper, MLX Whisper, and WhisperX

Official Whisper repository: https://github.com/openai/whisper

Official MLX Whisper example: https://github.com/ml-explore/mlx-examples/tree/main/whisper

Official WhisperX repository: https://github.com/m-bain/whisperX

Useful ideas:

- Local transcription provides a privacy-first path without API credentials.
- Word timestamps enable caption timing and speech-aware cuts.
- Forced alignment, VAD, batching, and diarization can improve long-form and multi-speaker work.

Adopted in CutCraft:

- `transcribe.py --provider local` supports MLX Whisper on Apple Silicon and OpenAI Whisper elsewhere, normalizing results to CutCraft's cached word schema.
- `--provider auto` uses a configured ElevenLabs key first, then a local engine; the user can explicitly force local processing.
- The environment checker treats cloud and local ASR as alternative setup paths and explains upload behavior.

Deferred:

- WhisperX remains optional because its alignment and diarization dependencies are large and hardware-sensitive. It should become a conditional provider with its own reproducible test fixtures, not a core install burden.

### FunClip

Official repository: https://github.com/modelscope/FunClip

Useful ideas:

- Speech-recognition results can drive natural-language clip selection.
- Named-entity customization helps proper nouns and domain vocabulary.
- LLM selection is most useful when the user can inspect and revise the transcript.

Adopted in CutCraft:

- Packed transcripts are the editing view, user-specified names/terms are collected before editing, and cue-addressable transcript corrections immediately re-render the video.
- The model proposes story order and cuts, but the EDL, source paths, and timestamps stay explicit and reviewable.

### OpenCut and Remotion

Official OpenCut repository: https://github.com/OpenCut-app/OpenCut

Official Remotion documentation: https://www.remotion.dev/docs/

Useful ideas:

- A plugin-first architecture, headless mode, scripting, and an agent-facing API keep creative automation extensible.
- Programmatic, frame-based compositions are valuable for approved motion graphics and repeatable templates.

Adopted in CutCraft:

- One canonical skill directory is bundled through a standard Codex plugin manifest.
- Headless helpers use explicit JSON inputs/outputs and stable project paths.
- Remotion/Node remains conditional and is installed only when the approved creative brief needs programmatic animation.

Explicitly not adopted:

- CutCraft does not ship a browser review UI. The product is conversation-led and file-based, so there is one fewer server, state store, and place for private footage to leak.

## Product principles derived from the comparison

1. One obvious final output, many traceable process artifacts.
2. Originals are immutable; proxies, transcripts, and renders are reproducible caches.
3. Analysis suggests edits but never silently becomes the edit.
4. Local processing is a first-class choice; cloud upload is explicit.
5. Every third-party asset has item-level provenance and license evidence.
6. Every expensive render has preflight; every delivery has machine QC plus human visual/listening review.
7. Optional power features stay conditional so first use remains understandable.
8. Interchange formats are shipped only when validated against real target editors.
