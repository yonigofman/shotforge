"""Run inside Blender, with the blockout file open and the shot camera selected."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

CHARACTER_PROPERTY = "shotforge_character"


def _character_id(obj):
    while obj is not None:
        character_id = obj.get(CHARACTER_PROPERTY)
        if character_id:
            return character_id
        obj = obj.parent
    return None


def render_character_masks(scene, view_layer, characters: list[str], output: Path) -> None:
    """Render binary silhouettes for all characters in one object-index pass."""
    indices = {char: index for index, char in enumerate(characters, start=1)}
    assigned = set()
    for obj in scene.objects:
        char = _character_id(obj)
        obj.pass_index = indices.get(char, 0)
        if obj.pass_index and obj.type not in {"EMPTY", "ARMATURE", "LIGHT", "CAMERA"}:
            assigned.add(char)
    missing = indices.keys() - assigned
    if missing:
        tags = ", ".join(sorted(missing))
        raise ValueError(
            f"Tag the mannequin objects or their parents with {CHARACTER_PROPERTY}: {tags}"
        )

    # Use Cycles for its object-index pass, including on Eevee blockouts.
    if scene.render.engine != "CYCLES":
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
    scene.cycles.samples = 1
    scene.cycles.use_denoising = False
    view_layer.use_pass_object_index = True
    scene.render.use_compositing = True
    scene.render.use_sequencer = False
    scene.use_nodes = True
    tree = scene.node_tree
    tree.nodes.clear()
    layers = tree.nodes.new("CompositorNodeRLayers")
    layers.layer = view_layer.name

    for char, index in indices.items():
        mask = tree.nodes.new("CompositorNodeIDMask")
        mask.index = index
        tree.links.new(layers.outputs["IndexOB"], mask.inputs[0])
        save = tree.nodes.new("CompositorNodeOutputFile")
        save.base_path = str(output / char)
        save.format.file_format = "PNG"
        save.format.color_mode = "BW"
        save.format.color_depth = "8"
        save.file_slots[0].save_as_render = False
        tree.links.new(mask.outputs[0], save.inputs[0])

    bpy.ops.render.render(layer=view_layer.name)
    for char in indices:
        files = list((output / char).glob("*.png"))
        if len(files) != 1:
            raise RuntimeError(f"Blender did not write the object-index mask for {char}")
        files[0].replace(output / f"mask_{char}.png")


def main() -> None:
    camera_name, output, *characters = sys.argv[sys.argv.index("--") + 1 :]
    scene = bpy.context.scene
    camera = scene.objects.get(camera_name) if camera_name else scene.camera
    if camera is None or camera.type != "CAMERA":
        raise ValueError(f"Camera {camera_name or '(active camera)'} is missing from {scene.name}")
    scene.camera = camera
    render_character_masks(scene, bpy.context.view_layer, characters, Path(output))


if __name__ == "__main__":
    main()
