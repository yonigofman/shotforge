---
name: blender-blockout
stage: blockout
kind: adapter
tool: Blender
description: Build a reusable grey-box scene, then render its shot cameras to disk.
inputs: [scene.yaml, location YAML, shot YAMLs, project aspect ratio]
outputs: [grey-box set description, Blender scene, blockout PNGs]
---

# Blender blockout

The template maps the location's measurements and the scene's seating into a
set description. The Python adapter resolves the camera transforms and sends
the same description to Blender through the running add-on or in background
mode.

Use metres, with the room centered at the XY origin, Z up and the front of the
room toward +Y. Use the optional location `blockout` mapping to give the set
measurements. Locations without the mapping use a default set.

- `room` as [width, depth, height].
- `window` with `wall` (back, front, left or right), `offset` from the wall
  center (+X on front/back, +Y on left/right), `width`, `height` and `sill`.
  Use `window: false` for a room without a window.
- `hero_table` with `center` as [x, y] and `size` as [width, depth, top height].

Seat names ending in `.left`, `.center` and `.right` place the figures along
the back of the table, as viewed from the room side. Other seat names use
the order in `scene.yaml`.
`faces` can name a seated character, `room`, `table`, `window`, `left` or `right`.

Each shot names a Blender camera with `camera.blender_cam`. For measured
framing, use [x, y] for `camera.position`, metres for `camera.height`, and
pitch in degrees for `camera.angle` (positive points up). The camera aims
horizontally at the seated subject. Omitting `angle` aims at the upper body.
Use `window end of table` and `room side, past … shoulder` for the example's
named positions. Supported height cues are `eye level`, `table height`,
`low` and `high`. Supported angle cues are `eye level`, `high angle` and
`low angle`.

For a measured 180° line, give `scene.axis_180` three XY points, `start`, `end`
and a `room_side` reference point. The `room side` shorthand and an omitted axis
use the seating row and +Y as the room side.
