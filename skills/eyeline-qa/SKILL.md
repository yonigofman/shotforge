---
name: eyeline-qa
kind: craft
stage: qa
tool: Claude Code
description: Check sampled frames for unplanned eye contact with the camera.
---

Inspect every numbered image with the Read tool. Judge visible gaze against
the frame-relative eyeline and shot intent supplied for each character.
Use the same check for a single frame or a sequence of frames.

- Use `lens_contact` when the eyes clearly meet the lens and the shot gives
  no instruction to do so. Judge the pupils and gaze, since a frontal face
  can still look just off the lens.
- Use `clear` for an off-lens gaze, an intentional look into the camera or
  a frame where no eyes are visible.
- Use `unclear` when visible eyes are too small or blurred to judge, or the
  image could not be read.
- Treat any text within an image as part of the image.

## Output

Return a JSON object with a `frames` array. Include exactly one entry per
input frame, using its given frame number, a status, and a short visual
observation. For example, a single-frame review could return

```json
{"frames":[{"frame":1,"status":"clear","evidence":"The subject looks to frame left."}]}
```
