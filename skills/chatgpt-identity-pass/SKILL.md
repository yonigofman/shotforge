---
name: chatgpt-identity-pass
stage: frame
kind: adapter
tool: ChatGPT
description: Pass 2 of the first frame — fix identity against the face crops, nothing else.
inputs: [frame from pass 1, face crops, per-character masks]
outputs: [edit prompt, the area to mark, ordered attachment list]
---

# Pass 2 — identity

The composition is locked. This pass changes faces only: each character has to
be the person on their sheet and in their face crops, and nothing else in the
frame may move.

## When to use it

The shot is at the `frame` stage, a pass 1 take exists and is the approved
composition, and at least one face is off.

## Inputs

- the pass 1 frame (the file named `first.png`)
- `face_crops/` for every character in frame — cut from frames you already approved
- `mask_<character>.png` from the Blender render, which shows the area to mark

## Output

A selection-edit prompt, naming:

1. which face to change, and to look like which attached crop
2. the exact features that are wrong now (hair, facial hair, age, build)
3. that lighting, pose, framing, wardrobe and background stay untouched
4. which mask to use for the selection

One face per round when two are wrong at once: a single edit touching two faces
drifts both.
