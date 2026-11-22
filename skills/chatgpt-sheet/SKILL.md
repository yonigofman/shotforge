---
name: chatgpt-sheet
stage: design
kind: adapter
tool: ChatGPT
description: Generate a design sheet prompt and a clean Grok tag reference prompt for a Bible entity.
inputs: [design brief, style YAML]
outputs: [design sheet prompt, clean reference prompt]
---

# Design sheet and clean reference

Generate the design sheet first. Choose the design and attach its sheet when
running the clean reference prompt so the single view matches it. The sheet is
for production review. The clean image is for the entity's Grok tag.
