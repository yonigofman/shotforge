---
name: chatgpt-first-frame
stage: frame
kind: adapter
tool: ChatGPT
description: Pass 1 of the first frame — lock the composition from the Blender blockout.
inputs: [blockout render, shot spec, character sheets, location wall reference]
outputs: [prompt for a new ChatGPT chat, ordered attachment list]
---

# Pass 1 — lock the composition

Turn the shot spec and the Blender blockout into one ChatGPT image prompt that
reproduces the blockout's composition in the project's art style.

## When to use it

The shot is at the `frame` stage and no frame take exists yet, or pass 1 is
being run again after a composition change. Always a **new chat**: a long chat
drifts, and the identity pass has its own one.

## Inputs

- `shot.yaml` for the camera, who is in frame, and the frame-relative facts
- the blockout render for the composition (positions, perspective, windows)
- the approved character sheets for who these people are
- the location's `wall_ref.png` for what the set is made of

## Output

One prompt, plain text, and the attachment order. The prompt must:

1. state the art style and film stock from the bible, never invent a look
2. describe the frame from the blockout: who is where, at what size, facing where
3. give every gaze a frame-relative direction with a visible anchor
4. carry the project's aspect ratio
5. end with the negative list

Never describe how a pose was reached, only what is seen.
