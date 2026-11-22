---
name: location-design
stage: design
kind: craft
description: Turn a Bible location into a repeatable set design with materials, layout and lighting.
inputs: [location YAML, style YAML]
outputs: [location design brief]
---

# Location design

Keep the architecture, set dressing, and fixed props consistent across views.
Treat `from_book` as source facts and authored materials, layout, and lighting
as production design. Honor the authored blockout dimensions where supplied.
Resolve open choices in `design_choices` after review.
