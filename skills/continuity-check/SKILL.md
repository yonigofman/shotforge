---
name: continuity-check
stage: qa
kind: craft
tool: Claude Code
description: Inspect adjacent approved frames for visible gaps across a cut.
inputs: [scene spec, two shot specs, two approved first frames, earlier last frame]
outputs: [continuity findings]
---

# Continuity check

## Inspect the cut

Use Read to open all three PNGs at the paths in `frames`. Compare the
`earlier_last_frame` directly with the `later_approved_frame`. Use the
`earlier_approved_frame` to identify the characters, objects and positions
established during the earlier shot. Use the shot specs, beats, scene seating
and camera axis to understand what can reasonably change at this cut.

Inspect the following in the pictures:

- **Gaze** — compare who or what each character is looking at. Account for
  reverse angles and the specified eyeline targets.
- **Hands and gestures** — follow each visible hand, the object it holds and the
  phase of an ongoing gesture. Use character-relative left and right.
- **Props** — compare who holds each prop, where it sits and its visible state,
  including the fill level of drinks.
- **Seating** — identify characters by their surroundings and compare their
  positions, facing and relationships to the table and other characters.
- **180° side** — use the scene axis and visible anchors to check whether screen
  directions and eyelines reverse unexpectedly.

Report visible discrepancies that the beats or staging leave unexplained, even
when the two specs declare identical continuity states. Check whether framing,
occlusion or an intentional change explains an apparent gap. Each finding needs
a concrete observation on both sides of the cut and a practical fix. Use `fail`
for clear breaks and `warn` for a visible but less certain gap.

## Output

Return a single JSON object with the key `findings` and a list of records. Each
record has `check`, `level`, `message` and `fix`. Use an empty list when you
reviewed the frames and found no gaps.

```json
{"findings":[]}
```

If any required image cannot be inspected, return a JSON object with the key
`error` and say which image you could not inspect and why.
