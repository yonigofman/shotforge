---
name: chatgpt-fix
stage: frame
kind: adapter
tool: ChatGPT
description: Turn one failed frame check into a targeted edit prompt.
inputs: [one failed QA finding, the numbered take, the shot spec, relevant references]
outputs: [edit prompt, edit mode, ordered attachments]
---

# Targeted frame fix

Edit the take named in the QA report. State what failed, how the result should
look, and which parts of the frame to preserve. Select only the affected region
for identity, prop and window findings. Use a full edit for grade and overall
image treatment.

For identity, name one in-frame character and use their approved face crop and
character mask. Mark only the face inside the silhouette. The blockout can
locate the edit when a mask is unavailable and guide prop and window selections.
