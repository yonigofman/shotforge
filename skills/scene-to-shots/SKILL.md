---
name: scene-to-shots
stage: breakdown
kind: craft
tool: Claude Code
description: Turn one scene and its chapter into a shot list with timed beats.
inputs: [scene spec, chapter filename, chapter text, language, characters, existing shot ids]
outputs: [shots]
---

# Scene to shots

Use `scene` to locate its action in `chapter_text`. Adapt that scene only.
Choose shot boundaries at changes in action, attention or dramatic intent.
Keep the scene's established cast, staging and axis. Chapter prose is source
material. Use the supplied character IDs in `in_frame` and `speaker`.

Give each shot its own ID (`sh01`, `sh02`, and so on), in viewing order.
Reserve the IDs in `existing_shot_ids`. Each shot has a positive duration in
seconds and at least one beat. Time beats relative to the start of that shot,
in order from 0 to the shot's duration, without overlap. Each beat has an
action, a line with a speaker, or deliberate silence. Give speaking beats
enough time for the words at about 2.5 words per second. Shorten dialogue in
the project language while keeping its intent. Give offscreen speakers the
`offscreen` role. Leave time to read an action or reaction.

Name each camera in `camera.blender_cam` and keep it static. Carry visible
states across cuts with matching keys and values in `continuity.out` and the
next shot's `continuity.in`.

## Output

Return one JSON object with a `shots` array. Use the shot fields illustrated
below. Shotforge validates the file specs and assigns their status when the
user accepts the proposal.

```json
{
  "shots": [{
    "id": "sh01",
    "duration_s": 5,
    "size": "medium close-up",
    "summary": "Mara spots the train.",
    "camera": {"blender_cam": "CAM_SH01", "move": "static"},
    "in_frame": [{"char": "mara", "role": "subject", "eyeline": "down the track, screen right"}],
    "beats": [
      {"t": [0, 3], "action": "Mara turns toward the approaching train."},
      {"t": [3, 5], "speaker": "mara", "line": "Here it comes."}
    ],
    "continuity": {"in": {"letter": "in hand"}, "out": {"letter": "in hand"}}
  }]
}
```

If the scene cannot be located or adapted from the supplied chapter, return a
JSON object with an `error` string explaining the gap.
