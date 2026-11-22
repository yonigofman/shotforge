---
name: texture-from-sheet
stage: blockout
kind: adapter
tool: ChatGPT Image
description: Use an approved design sheet to generate a tileable material for a blockout surface.
inputs: [approved design sheet, target surface, texture name, UV scale]
outputs: [tileable texture prompt, materials.yaml entry]
---

# Texture from sheet

Approve the sheet and clean reference in the Bible, then run
`shotforge texture-prompt location.pub.wall pub_plaster --uv-scale 1`.
Attach the indicated approved sheet and paste the prompt into the image
generator. Add the assignment to `03_blender/textures/materials.yaml`.
Download the generated PNG into `_inbox/` and file it as the named texture.

Location surfaces are `wall`, `floor` and `table`. Use
`character.<id>.garment` for a mannequin's shirt or jacket. The target's
approved sheet is the default reference. Use `--source prop.<id>` or another
bible entity when its sheet supplies the material.
