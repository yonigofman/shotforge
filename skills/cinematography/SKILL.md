---
name: cinematography
stage: breakdown
kind: craft
tool: Claude Code
description: Propose a lens, height, angle and techniques with a reason for each choice.
inputs: [shot spec, scene seating and axis, previous shot, next shot, technique library]
outputs: [lens, height, angle, techniques, reasons]
---

# Cinematography

Read the target shot's beats, framing, foreground, and eyelines alongside the
scene seating and `axis_180`. Use `previous_shot` and `next_shot` to work out
what the cuts need to preserve or reveal. Use `technique_library` to choose
techniques that the existing `in_frame` and beats can support. An empty list is
valid when none serves the shot.

Choose the lens, camera height and vertical angle separately. Give each setting
and each chosen technique one short sentence that names the choice and explains
what it does for this shot or cut. Refer to a beat, seat, eyeline, or framing
relationship when it explains the choice.

Keep the planned camera position, framing, and beats. Respect the 180° line,
including when a reverse uses the opposite view of the same exchange. If the
existing placement conflicts with the shot, return an `error` with the conflict
and the detail that needs to change.

In the S01 pub example, `sh07` has Dima in soft foreground and Durik raising
his finger before the cut to Dima's reaction. A 50 mm over-the-shoulder shot at
eye level holds the gesture and keeps Dima's seat and eyeline legible across
the cut to `sh08`. Use that choice when those conditions are present. Add cut
on action only when the adjacent beats support a cut through the movement.

## Output

Return one JSON object with these fields. `lens_mm` is a positive integer.
`height` is `eye level`, `table height`, `low`, `high`, or metres above the
floor. `angle` is `eye level`, `high angle`, `low angle`, or pitch in degrees
with positive values up and negative values down. Use the technique IDs in the
library. Enter each reason on one line.

```json
{
  "lens_mm": 50,
  "lens_why": "A 50 mm lens keeps Durik's lifted finger visible with his expression.",
  "height": "eye level",
  "height_why": "Eye-level height keeps the seated exchange at Dima and Durik's shared height.",
  "angle": "eye level",
  "angle_why": "A level angle preserves the eyeline into Dima's reaction in sh08.",
  "techniques": [
    {
      "name": "over_the_shoulder",
      "why": "The over-the-shoulder frame uses Dima's soft foreground to place us in the exchange while Durik delivers the line."
    }
  ]
}
```
