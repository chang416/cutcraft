# EDL v2

Store `edl.json` in the project's `過程/` directory. Resolve relative paths against that directory.

```json
{
  "version": 2,
  "output": {"width": 1080, "height": 1920, "fps": 30, "fit": "cover"},
  "sources": {"take-01": "/absolute/path/take-01.mp4"},
  "ranges": [
    {"source": "take-01", "start": 2.42, "end": 6.85,
     "focus_x": 0.5, "focus_y": 0.4, "speed": 1.0,
     "beat": "HOOK", "quote": "...", "reason": "cleanest delivery"}
  ],
  "grade": "neutral_punch",
  "transition": {"type": "none", "duration": 0.0},
  "overlays": [],
  "music": null,
  "subtitles": "master.srt",
  "total_duration_s": 4.43
}
```

`fit` is `cover` or `contain`. Per-range `focus_x`/`focus_y` are static crop anchors from 0 to 1. Per-range `grade`, `speed`, `gain_db`, and `mute` override project defaults. Represent a speed ramp as consecutive ranges. The built-in transition is project-wide; render chapter groups separately when only selected joins need it. Use overlays or a custom pass when independent audio/video J/L-cut fields are required.

Every range must use cached transcript word boundaries, include a reason, and remain within the source duration. Recalculate `total_duration_s` after speed or transition changes.
