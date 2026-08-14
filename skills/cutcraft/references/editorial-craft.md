# Editorial craft

## Cuts and continuity

Use audio and meaning to choose the boundary; visuals confirm it. Prefer silence gaps of 400 ms or more, preserve laughs/reactions and speaker handoffs, and use 30–200 ms padding. A transition cannot rescue a poor cut.

Normalize every kept segment before joining. Prefer hard cuts, cut-on-action, eyeline matches, and J/L-cut coverage. Use short dissolves only across non-speech handles. Inspect rendered boundaries for flash frames, duplicated frames, pose jumps, room-tone changes, lip-sync drift, and double speech.

For multi-take speaking footage, assemble by narrative beat rather than filename order. Common structures include hook → problem → solution → evidence → call to action; tutorial setup → steps → gotchas → recap; and question → answer → follow-up. Adapt to the actual material.

## Pace, reframing, and grade

Express speed ramps as adjacent ranges of the same source with stepped speeds. Preserve pitch. Split moving-subject ranges when a static crop focus is insufficient.

Grade by inspecting real frames. Correct exposure and white balance before adding a look. Protect skin tones. Apply grade per segment during extraction to avoid an extra generation loss.

## Music, SFX, and effects

Choose music for narrative function. Start a speech bed conservatively, enable ducking, fade in/out, and listen to the final mix. Place SFX on actions, reveals, errors, reactions, or punchlines; do not stack several attention cues on one beat.

Every overlay needs a purpose and a precise output-timeline window. Shift overlay timestamps from frame zero into that window. Apply subtitles after overlays. Generated inserts must follow the approved palette, typography, tone, and consent boundaries.

## Animation engines

- HyperFrames: deterministic HTML/CSS/GSAP UI motion or transparent web overlays.
- Remotion: React component systems and existing React motion libraries.
- Manim: mathematical diagrams, state machines, equations, graphs.
- PIL/PNG sequence + FFmpeg: simple cards, counters, labels, progressive draws.

Create each independent slot in `過程/animations/slot_<id>/` with a self-contained brief and unique output. Do not parallelize take selection, EDL ownership, subtitle timing, mix decisions, or final QC.
