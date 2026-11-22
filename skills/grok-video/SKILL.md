---
name: grok-video
stage: video
kind: adapter
tool: Grok
description: Turn an approved first frame and the shot's beats into a Grok video prompt.
inputs: [approved first frame, beats with timecodes, character @tags]
outputs: [video prompt with timecodes, ordered attachment list]
---

# Video from the approved frame

The frame is approved and locked. This prompt animates it: the beats in order,
with timecodes, a static camera, and the `@tag` of every character who is
visible enough to need one.

## When to use it

The shot is at the `video` stage. If the frame changes afterwards, the video
prompt is regenerated — the frame is the first frame of the clip.

## Inputs

- the approved `first.png`
- the beats from `shot.yaml`, with their `t` ranges
- `ref.png` and the `@tag` of each character in frame
- `sound` from the shot spec for ambience

## Output

One prompt with a timecoded action line per beat, the dialogue exactly as
written, a static-camera statement, and the duration. Silent beats say that
nobody speaks.

The rules in `rules.md` are what scene 1 taught; they go into every prompt.
