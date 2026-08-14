# Free and user-supplied assets

Offer only sources appropriate to the requested asset type. “Free” never means license-free; inspect and record the item-level license before downloading.

## Recommended choices

| Need | Choices to offer | License check |
|---|---|---|
| Music or general SFX | Pixabay Music/Sound Effects — https://pixabay.com/ | Read https://pixabay.com/service/license-summary/ and retain the item URL. |
| Music, SFX, stock video | Mixkit — https://mixkit.co/ | Check the exact item type at https://mixkit.co/license/; Mixkit has both free and restricted stock-video licenses. |
| Specific community sound | Freesound — https://freesound.org/ | Prefer CC0. For CC BY, capture creator and attribution. Reject CC BY-NC for commercial work unless the use is confirmed non-commercial. See https://freesound.org/help/faq/. |
| B-roll, photos, textures | Pexels — https://www.pexels.com/ | Read https://www.pexels.com/legal-pages/license/ and check people, brands, endorsement, and redistribution restrictions. |
| Custom graphic, insert, background | User-provided or generated asset | Record `user-provided` or the generation method; confirm consent for faces, voices, and trademarks. |

## Ask in the one checkpoint

For each requested category, offer concrete directions such as:

- Music: upbeat electronic, warm acoustic, cinematic tension, lo-fi, or no music.
- SFX: subtle UI clicks, impact/whoosh accents, comedic cues, ambience, or none.
- Visual effects: zoom/punch-in, speed ramp, freeze-frame label, blur/censor, kinetic text, particles, or none.
- B-roll: user files, stock footage, generated visuals, or no B-roll.

Ask the user to upload their files or choose a source/direction before downloading. If they choose a free library, search only after approval, show the candidate item and license, then obtain final approval when the creative or licensing choice is material.

## Provenance record

For every third-party item, add to `過程/creative_brief.json` or an adjacent asset manifest:

```json
{
  "title": "...",
  "creator": "...",
  "source_url": "https://...",
  "license": "...",
  "downloaded_at": "YYYY-MM-DD",
  "attribution_required": true,
  "purpose": "music bed / impact SFX / B-roll"
}
```

Do not use ripped movie/TV/game clips, copyrighted songs, random memes, or an asset whose item page/license cannot be preserved.
