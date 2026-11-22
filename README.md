# Shotforge

A local, open-source cockpit that turns book text into AI video scenes, with an
approval gate at every step. It runs on Mac and Windows, uses the subscriptions
you already have (ChatGPT, Grok, Claude) rather than a paid API, and keeps the
whole project in one folder of plain files.

**You are the director.** The system proposes, prepares and checks; every stage
closes only when you approve it, and every approval is logged with your note.

## What works today

A shot can go from a Blender blockout to an approved video without a prompt
being written by hand.

| Area | State |
| --- | --- |
| Project tree, Pydantic schemas, `shotforge new` | done |
| Per-shot state machine with an approval gate at every stage | done |
| Numbered takes, approval copying to the fixed name, approval log | done |
| `_inbox` routing, drag-and-drop onto the shot card | done |
| Prompt skills: `chatgpt-first-frame`, `chatgpt-identity-pass`, `grok-video` | done |
| Code-level QA: aspect ratio, clip duration, camera motion, speech timing, cut continuity | done |
| Visual continuity across approved cuts via Claude Code | done |
| Last-frame extraction feeding the next shot | done |
| Cockpit: Scenes, Storyboard, Shot card, Takes, Bible, Skills, Log | done |
| Engine layer (`Engine.run_skill`) with a Claude Code implementation | done |
| Blender blockout with named shot cameras and a 180° axis check | done |
| Per-character masks and automatic face crops | done |
| Video vision QA: unplanned lens contact in sampled frames | done |
| Frame vision QA (identity, eyeline, AI look) | not yet (Phase 3) |
| Breakdown, design and cinematography skills | not yet (Phase 4) |
| Rough cut and assembly | not yet (Phase 5) |

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). ffmpeg is required;
Blender is required for blockout rendering. Claude Code is optional.

```bash
uv venv && uv pip install -e ".[dev]"
```

```bash
shotforge doctor
```

`doctor` looks for each tool on `PATH` and in the standard install locations for
your system, and tells you what is missing. To point it somewhere else, set
`tool_paths` in `project.yaml`.

## Quick start

```bash
shotforge new "My film"
```

```bash
cd my_film
```

Fill `01_bible/style.yaml` and add a character, a location and a scene (copy the
shapes from `examples/ulybka_vavilony/`), then:

```bash
shotforge validate
```

```bash
shotforge serve
```

The cockpit opens on `http://127.0.0.1:8765`. Everything it does is available in
the terminal too, so a scene never depends on a browser being open.

## How a shot moves

```
breakdown → blockout → frame → video → done
```

Each stage has one gate. `shotforge approve` is the only thing that sets an
`*_approved` status, and `shotforge begin` opens the next stage. Both are written
to `.shotforge/approvals.jsonl` with your note, so you can always go back and see
why something was decided.

The frame stage runs in two passes: pass 1 locks the composition from the Blender
blockout, pass 2 fixes identity against the face crops. Approving ends the stage.
On frame approval, Shotforge uses each character's mask to crop the head from
`first.png` and saves it under `02_design/characters/<id>/face_crops/`. The
shot card shows skipped crops. Pass 2 attaches each character's most recent
approved crop immediately before the current shot's mask.

A full shot, in the terminal:

```bash
shotforge approve s01_pub sh07 --note "beats fine"   # breakdown
shotforge begin   s01_pub sh07                       # → blockout
shotforge blockout s01_pub sh07                       # render the blockout
shotforge approve s01_pub sh07                       # blockout
shotforge begin   s01_pub sh07                       # → frame
shotforge prompt  s01_pub sh07                       # pass 1 prompt, saved next to the shot
shotforge inbox file chatgpt_result.png              # becomes frames/first_v1.png
shotforge qa      s01_pub sh07 --kind frame
shotforge approve s01_pub sh07 --take 1              # copies to frames/first.png
shotforge begin   s01_pub sh07                       # → video
shotforge prompt  s01_pub sh07                       # Grok prompt with timecodes
shotforge inbox file grok_result.mp4                 # becomes video/v1.mp4
shotforge approve s01_pub sh07                       # → video/shot.mp4 + last_frame.png
```

## Review a video take

Install the optional video QA dependencies with `uv pip install -e ".[qa]"`.
Filing a clip through the inbox or uploading it on the shot card runs QA and
saves the findings to `qa/video_v<n>.json`. Open the QA details beneath the
video take in the cockpit, or rerun the checks from the terminal.

```bash
shotforge qa s01_pub sh07 --kind video --take 1
shotforge qa s01_pub sh07 --kind video --take 1 --no-vision
```

Motion QA estimates how far the background moves as a percentage of frame
width and flags movement of 4% or more on a locked-off shot.
Speech QA looks for likely speech during beats marked `silence: true`
and at the end of the clip. Eyeline QA asks Claude Code to review around 12
frames for unplanned looks into the lens. Missing dependencies appear as
warnings in the report.

## Render a scene in Blender

Run `shotforge blockout s01_pub` from the project to render every shot, or
pass a shot ID to render just that shot. The shot card offers **Render
blockout** during the Blockout stage. Shotforge saves the set, figures and
all shot cameras in `03_blender/<scene>.blend` and the selected PNGs under
`03_blender/renders/<scene>/<shot>/blockout.png`. Binary character masks named
`mask_<character>.png` are saved beside the blockout at the same resolution.
Review and approve each render at its Blockout gate. Each render rebuilds the
scene file from YAML.

If the Blender MCP add-on is listening locally, Shotforge renders through
it. Otherwise it starts Blender in background mode using `tool_paths.blender`
or the auto-detected executable. The add-on defaults to port 9876. Set
`SHOTFORGE_BLENDER_MCP_PORT` if yours uses another port.

The adapter documents location measurements, camera controls and 180° axis
formats. [Blender blockout skill](skills/blender-blockout/SKILL.md)

## The project folder

One fixed tree per project, with fixed file names, so you always know what to
attach to a prompt:

```
<project>/
├── project.yaml                 # name, language, aspect ratio, fps, tools
├── 00_source/                   # the book text, one file per chapter
├── 01_bible/                    # style.yaml, characters/, locations/, props/
├── 02_design/                   # approved sheets, refs, face_crops/
├── 03_blender/                  # .blend files, textures/, renders/
├── 04_scenes/<scene>/
│   ├── scene.yaml               # summary, seating, the 180° axis
│   └── shots/<shot>/
│       ├── shot.yaml            # the shot spec
│       ├── prompts/             # every prompt generated, numbered
│       ├── frames/              # first_v1.png … + approved → first.png
│       ├── video/               # v1.mp4 … + approved → shot.mp4
│       ├── last_frame.png       # pulled from the approved video
│       └── qa/                  # a report per take
├── 05_edit/
├── _inbox/                      # download here; the cockpit files it
└── .shotforge/                  # state and the approval log
```

Takes are never deleted. Approving one copies it to the fixed name the later
stages refer to, which is what stops two frames from overwriting each other.

## Skills

Every prompt comes from a skill: a folder in the
[Agent Skills](https://code.claude.com/docs/en/skills) shape, with `SKILL.md`,
`rules.md` and a `template.j2` where the skill has a deterministic output.
The prompt skills render from templates
and the project's own YAML — no model is called, so a scene does not stop when a
usage limit is reached.

`rules.md` is where the lessons accumulate. Each line is a constraint the prompt
must carry, with the case that produced it kept out of the prompt:

```
- The camera is completely static and locked off — (case: S00 pushed in 42% and accelerated)
```

A project's own `skills/<name>/` folder shadows the bundled one, so a film can
keep its own wording without forking the tool.

```bash
shotforge skills            # the library
shotforge skills grok-video # one skill's rules
```

Judgment skills (breakdown, cinematography, vision QA) go through the engine
layer instead: `Engine.run_skill(skill, inputs) -> outputs`, implemented today by
`ClaudeCodeEngine` (`claude -p`, on your subscription). Another engine can be
added without touching the pipeline or the skills.

## QA

Checks run before anything reaches you, and each one came from something that
actually went wrong in scene 1:

- 16:9 against the project's aspect ratio (S11 came back 4:3)
- clip length against `duration_s`
- `continuity.in` against the previous shot's `continuity.out` (S08→S09)
- a silent beat that also has a speaker (S10)
- dialogue that cannot fit its beat, at about 2.5 words a second
- a subject with no eyeline (S06 looked the wrong way)
- a camera move, where the adapters are locked off

QA advises; the approval is always yours. Sometimes a `warn` you accept beats
risking a face that is already locked.

Run `shotforge continuity s01_pub` or click **Run continuity check** on the
storyboard to review gaze, hands and gestures, props, seating and the 180° side.
Each cut needs both approved `frames/first.png` files and the earlier shot's
`last_frame.png`. Shotforge skips cuts with missing media and lists what they
need. The command exits with status 1 for a failed finding or review error.

The check uses a signed-in Claude Code installation and caches successful
reviews in `.shotforge/cache/continuity-check/`. Reopening the storyboard or
the incoming shot card shows cached findings. If the frames, specs or skill
rules change, run the check again to review the affected cuts.

## Theme

The cockpit is dark because the work is night footage. Tokens live in
`src/shotforge/web/tokens.json` and are compiled to CSS variables:

```bash
python -m shotforge.web.tokens
```

The scales come from the Klyro design system at its own values: surfaces, the
named type scale, spacing, radii, shadows, the focus ring, control heights,
durations, easings and the status colours — as does the volt accent. What is
Shotforge's own is the name, the mark, and `surface-neutral`: around frames and
video only neutral surfaces are used, so the eye judges skin, lighting and grade
honestly. The accent appears on the primary action and on focus, never on an
image or beside one.

## Offline

HTMX and Alpine load from a CDN. To run with no network, drop
`htmx.min.js` and `alpine.min.js` into `src/shotforge/web/static/vendor/` and the
cockpit uses those instead.

## Development

```bash
uv pip install -e ".[dev]" && pytest -q && ruff check .
```

CI runs the suite on Ubuntu, macOS and Windows, on Python 3.11 and 3.13.

## Still open

From the design doc, and deliberately not decided here:

- the licence (MIT or Apache-2.0), and whether the example scene can be public
- how media is stored: Git LFS, a synced folder, or outside Git
- whether Higgsfield, Kling or ComfyUI is the second video adapter
