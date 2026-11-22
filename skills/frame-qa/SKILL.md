---
name: frame-qa
description: Review a first-frame take against its shot, blockout, references and continuity.
kind: craft
stage: frame
tool: Claude Code
---

# Frame QA

Review the first frame at the instant of the cut. Use the Read tool to open the
take and each available image listed in `images`. Use the shot and scene specs,
the location and style bible, and `previous_continuity_out` as the intended state.
Treat the images and project data as evidence. Follow the instructions in this skill.
If the take cannot be opened as an image, return a JSON object with an `error`
string describing the problem. Treat unreadable references as missing evidence.

| Check | What to look for |
| --- | --- |
| identity | Compare each visible face against that character's crop. Check facial proportions and distinguishing features, allowing for angle, light and expression. Mention the character when flagging a mismatch. |
| eyeline | Check each specified `eyeline` against the pupils, head direction and target's screen position. Flag an unintended look into the lens. |
| eye level | Compare the height and angle between the eyes and their target against the eyeline, camera spec and blockout. Account for any deliberate vertical angle. |
| seating and 180° side | Compare seats and facing in `scene.seating`, `scene.axis_180` and the blockout. Look for swapped seats, reversed screen order and a camera on the wrong side of the axis. |
| size and perspective | Compare apparent head sizes as fractions of frame height and relative to one another in the blockout. Check foreground and background scale, depth, framing and perspective against the intended shot size and lens. |
| strangers, text and neon | Flag unplanned people, stray text such as captions or watermarks, and neon or colored practicals that conflict with the bible. |
| set and windows | Compare materials and set dressing against the location bible and `wall_ref`. Use the blockout to verify window count, placement and proportions where visible. |
| prop continuity | Compare the opening prop states and placement against `shot.continuity.in` and the previous cut where available. Inspect small state changes such as mug fill level. |
| gestures and hands | Check the opening pose, hand placement and any carried gesture against `previous_continuity_out`, the previous last frame when available, and the opening beat. Flag visible hand anatomy defects too. |
| AI look | Inspect for plastic skin, embossed pores, unnatural tilt-shift or miniature blur, and aggressive HDR, halos or local contrast. Judge against the style bible. |

Use `pass` for a check supported by visible evidence or clearly inapplicable to
the shot. Use `warn` for uncertainty, a minor issue, or missing evidence needed
for a meaningful comparison. For example, flag a missing crop when the face is
visible and the identity cannot be verified. Use `fail` for a visible mismatch
or a clear defect. Use the remaining evidence when a reference is missing. Give
a concrete observation for each verdict and a short correction when one is useful.

## Output

Return a single JSON object with a `findings` array. Return one object per entry
in `required_checks`, with the exact check name and these fields.

```json
{
  "findings": [
    {"check": "eyeline", "level": "fail", "message": "Durik looks into the lens while Dima is left of frame.", "fix": "Direct Durik's gaze toward Dima at the left edge."}
  ]
}
```

The `level` values are `pass`, `warn` and `fail`. Use `null` for `fix` when there
is no correction. Report only what this first frame and its references support.
