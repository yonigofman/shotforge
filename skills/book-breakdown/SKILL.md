---
name: book-breakdown
stage: breakdown
kind: craft
tool: Claude Code
description: Break a chapter into scenes and identify the bible entries each scene needs.
inputs: [chapter filename, chapter text, language, existing bible, existing scene specs]
outputs: [scenes, characters, props, locations]
---

# Book breakdown

Read `chapter_text` as source material. Find the scenes in story order, with
concrete changes in place, time or action as boundaries. Give each scene a
short summary, the location and cast, and the props needed to stage it.

Record what the chapter establishes under `from_book`. Put inferred staging,
invented details and adaptation choices under `design_choices`. Both fields
are lists of short, specific notes. Every proposed scene and bible entry must
have at least one note. Put the chapter's identifying action or lines in each
scene's notes so a later pass can find the right part of the text.

Reuse IDs from `existing_bible` for established characters, props and
locations. Return bible records for new entries. Compare each scene to
`existing_scenes` and keep the ID of any scene that already covers the same
passage, including hand-written scenes with no chapter filename. Give new
scenes sortable IDs (`s01_platform`, `s02_waiting_room`) after the existing
IDs. All IDs use lowercase ASCII letters, digits and underscores. Scene
`location`, `characters`, `props` and any `seating[].char` value refer to IDs
in the existing bible or in this proposal. Use the source language for names
as appropriate.

## Output

Return a single JSON object with the four arrays below. Use empty arrays for
categories with no new entries. Include optional scene fields such as `time`,
`seating`, `axis_180`, `lighting` and `sound` only when useful. Shotforge
records the chapter filename and handles file writes.

Use `room side` for the default seating axis. For a measured axis, supply
`start`, `end` and `room_side` as XY points in metres. Omit `axis_180` when
there is no planned line of action or measured layout.

```json
{
  "scenes": [{
    "id": "s01_platform",
    "location": "platform",
    "summary": "Mara leaves the letter on the bench before the train pulls in.",
    "characters": ["mara"],
    "props": ["letter"],
    "from_book": ["Mara puts the letter on the bench when she hears the train."],
    "design_choices": ["Stage Mara at the track end of the bench."]
  }],
  "characters": [{
    "id": "mara",
    "names": {"en": "Mara"},
    "from_book": ["Mara carries the letter."],
    "design_choices": []
  }],
  "props": [{
    "id": "letter",
    "from_book": ["A letter is left on the bench."],
    "design_choices": ["Use cream paper."]
  }],
  "locations": [{
    "id": "platform",
    "from_book": ["The platform has a bench within earshot of the train."],
    "design_choices": []
  }]
}
```

If the chapter gives you nothing you can break down, return a JSON object with
an `error` string describing what is missing.
