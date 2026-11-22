"""Build and render one Shotforge scene inside Blender's own Python."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from uuid import uuid4

import bmesh
import bpy
from mathutils import Vector


def _mesh(scene, name, center, dimensions, material, *, sphere=False, rotation=0):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    if sphere:
        bmesh.ops.create_uvsphere(bm, u_segments=20, v_segments=12, radius=0.5)
    else:
        bmesh.ops.create_cube(bm, size=1)
    for vertex in bm.verts:
        for axis, length in enumerate(dimensions):
            vertex.co[axis] *= length
    bm.to_mesh(mesh)
    bm.free()
    _uv_map(mesh, (0, 0, 0) if sphere else center)
    if sphere:
        for polygon in mesh.polygons:
            polygon.use_smooth = True
    mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    obj.location = center
    obj.rotation_euler.z = rotation
    return obj


def _uv_map(mesh, offset):
    # Planar UVs in metres. Room-space coordinates keep each wall's pieces
    # aligned around its window. Garments use local axes and follow the figure.
    uv = mesh.uv_layers.new(name="Surface UV")
    axes = ((1, 2), (0, 2), (0, 1))
    for face in mesh.polygons:
        dominant = max(range(3), key=lambda axis: abs(face.normal[axis]))
        u, v = axes[dominant]
        for index in face.loop_indices:
            point = mesh.vertices[mesh.loops[index].vertex_index].co
            uv.uv[index].vector = (point[u] + offset[u], point[v] + offset[v])


def _material(name, grey, *, emission=0.25):
    material = bpy.data.materials.new(name)
    material.diffuse_color = (grey, grey, grey, 1)
    material.use_nodes = True
    shader = material.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = material.diffuse_color
    shader.inputs["Roughness"].default_value = 0.82
    emission_color = shader.inputs.get("Emission Color") or shader.inputs["Emission"]
    emission_color.default_value = material.diffuse_color
    shader.inputs["Emission Strength"].default_value = emission
    return material


def _textured_material(base, assignment, images):
    if assignment is None:
        return base
    material = base.copy()
    texture = Path(assignment["texture"])
    material.name = texture.stem
    if texture not in images:
        image = bpy.data.images.load(str(texture))
        image.pack()
        image.filepath = f"//textures/{texture.name}"
        images[texture] = image

    nodes = material.node_tree.nodes
    links = material.node_tree.links
    coords = nodes.new("ShaderNodeTexCoord")
    scale = nodes.new("ShaderNodeVectorMath")
    scale.operation = "SCALE"
    scale.inputs["Scale"].default_value = assignment["uv_scale"]
    image_node = nodes.new("ShaderNodeTexImage")
    image_node.image = images[texture]
    image_node.extension = "REPEAT"
    shader = nodes.get("Principled BSDF")
    links.new(coords.outputs["UV"], scale.inputs[0])
    links.new(scale.outputs["Vector"], image_node.inputs["Vector"])
    links.new(image_node.outputs["Color"], shader.inputs["Base Color"])
    emission_color = shader.inputs.get("Emission Color") or shader.inputs["Emission"]
    links.new(image_node.outputs["Color"], emission_color)
    return material


def _room(scene, geometry, wall_material, floor_material):
    width, depth, height = geometry["room"]
    window = geometry.get("window")
    _mesh(scene, "Floor", (0, 0, -0.08), (width, depth, 0.16), floor_material)
    for name, span, fixed, along_x in (
        ("back", width, -depth / 2, True),
        ("front", width, depth / 2, True),
        ("left", depth, -width / 2, False),
        ("right", depth, width / 2, False),
    ):
        _wall(scene, name, span, fixed, along_x, height, window, wall_material)


def _wall(scene, name, span, fixed, along_x, height, window, wall_material):
    def panel(label, start, end, bottom, top, material, thickness=0.16, inset=0):
        if end <= start or top <= bottom:
            return
        middle = (start + end) / 2
        center = (middle, fixed + inset) if along_x else (fixed + inset, middle)
        footprint = (end - start, thickness) if along_x else (thickness, end - start)
        _mesh(
            scene, f"{name.title()} {label}", (*center, (bottom + top) / 2),
            (*footprint, top - bottom), material,
        )

    if not window or window["wall"] != name:
        panel("wall", -span / 2, span / 2, 0, height, wall_material)
        return

    left = window["offset"] - window["width"] / 2
    right = window["offset"] + window["width"] / 2
    sill = window["sill"]
    lintel = sill + window["height"]
    panel("wall left of window", -span / 2, left, 0, height, wall_material)
    panel("wall right of window", right, span / 2, 0, height, wall_material)
    panel("wall below window", left, right, 0, sill, wall_material)
    panel("wall above window", left, right, lintel, height, wall_material)

    trim = _material("Window trim", 0.16)
    for edge in (left, right, window["offset"]):
        panel("window jamb", edge - 0.035, edge + 0.035, sill, lintel, trim, thickness=0.19)
    for edge in (sill, lintel):
        panel("window rail", left, right, edge - 0.035, edge + 0.035, trim, thickness=0.19)
    outside = _material("Window exterior", 0.72, emission=0.8)
    panel(
        "window pane", left, right, sill, lintel, outside,
        thickness=0.02, inset=math.copysign(0.18, fixed),
    )


def _table(scene, table, material):
    x, y = table["center"]
    width, depth, height = table["size"]
    slab = min(0.09, height * 0.12)
    _mesh(scene, "Hero tabletop", (x, y, height - slab / 2), (width, depth, slab), material)
    for dx in (-0.40 * width, 0.40 * width):
        for dy in (-0.35 * depth, 0.35 * depth):
            _mesh(
                scene, "Hero table leg", (x + dx, y + dy, (height - slab) / 2),
                (min(0.10, width / 8), min(0.10, depth / 8), height - slab), material,
            )


def _mannequin(scene, figure, top, stool_material, material, garment, index):
    name = figure["char"]
    x, y = figure["position"]
    heading = figure["heading"]
    cosine, sine = math.cos(heading), math.sin(heading)

    def part(label, dx, dy, z, dimensions, *, body=True, clothed=False):
        center = (x + dx * cosine - dy * sine, y + dx * sine + dy * cosine, z)
        surface = garment if clothed else material if body else stool_material
        obj = _mesh(
            scene, f"{name} {label}", center, dimensions, surface,
            sphere=body, rotation=heading,
        )
        if body:
            obj.pass_index = index

    seat = max(0.12, top - 0.32)
    part("stool base", 0, 0, seat / 2, (0.22, 0.23, seat), body=False)
    part("stool seat", 0, 0, seat, (0.40, 0.39, 0.07), body=False)
    part("torso", 0, 0, seat + 0.34, (0.41, 0.27, 0.60), clothed=True)
    part("neck", 0, 0, seat + 0.66, (0.13, 0.13, 0.21))
    part("head", 0, 0, seat + 0.81, (0.24, 0.24, 0.30))
    part("face", 0, 0.10, seat + 0.79, (0.17, 0.11, 0.19))
    for dx in (-0.23, 0.23):
        part("upper arm", dx, 0.02, seat + 0.35, (0.14, 0.16, 0.39), clothed=True)
        part("forearm", dx, 0.16, seat + 0.31, (0.13, 0.36, 0.13))
    for dx in (-0.105, 0.105):
        part("thigh", dx, 0.15, seat + 0.06, (0.18, 0.43, 0.18))
        part("shin", dx, 0.30, seat / 2, (0.14, 0.16, seat * 0.92))


def _camera(scene, spec):
    data = bpy.data.cameras.new(spec["name"])
    data.lens = spec["lens_mm"]
    data.sensor_fit = "HORIZONTAL"
    data.shift_x = spec["shift_x"]
    camera = bpy.data.objects.new(spec["name"], data)
    scene.collection.objects.link(camera)
    camera.location = spec["location"]
    direction = Vector(spec["target"]) - camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    return camera


def _check_axis(scene, cameras, axis):
    start, end = axis["start"], axis["end"]
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)

    def distance(x, y):
        return (dx * (y - start[1]) - dy * (x - start[0])) / length

    side = math.copysign(1, distance(*axis["room_side"]))
    layer = scene.view_layers[0]
    with bpy.context.temp_override(scene=scene, view_layer=layer):
        layer.update()
        for shot, camera in cameras.items():
            position = camera.matrix_world.translation
            if side * distance(position.x, position.y) <= 1e-6:
                raise ValueError(
                    f"{shot}: camera {camera.name} at XY ({position.x:.3g}, {position.y:.3g}) "
                    "must be on the room side of scene.axis_180"
                )


def _lighting(scene):
    world = bpy.data.worlds.new("Blockout world")
    scene.world = world
    light = bpy.data.lights.new("Key", "SUN")
    light.energy = 1.8
    light.angle = 0
    obj = bpy.data.objects.new("Key", light)
    scene.collection.objects.link(obj)
    obj.rotation_euler = Vector((0.25, -0.42, -1)).to_track_quat("-Z", "Y").to_euler()


def _render_shot(scene, path, masks, indices):
    scene.render.filepath = path
    if not masks:
        bpy.ops.render.render(scene=scene.name, write_still=True)
        return

    view_layer = scene.view_layers[0]
    view_layer.use_pass_object_index = True
    # Blender 5 moved the compositor tree and changed File Output's path API.
    uses_node_group = hasattr(scene, "compositing_node_group")
    if uses_node_group:
        tree = bpy.data.node_groups.new("Shotforge masks", "CompositorNodeTree")
        scene.compositing_node_group = tree
        tree.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        composite = tree.nodes.new("NodeGroupOutput")
    else:
        scene.use_nodes = True
        tree = scene.node_tree
        tree.nodes.clear()
        composite = tree.nodes.new("CompositorNodeComposite")

    try:
        layers = tree.nodes.new("CompositorNodeRLayers")
        layers.scene = scene
        layers.layer = view_layer.name
        tree.links.new(layers.outputs["Image"], composite.inputs["Image"])
        index_pass = layers.outputs["Object Index" if uses_node_group else "IndexOB"]
        for char, output in masks.items():
            directory = Path(output).with_suffix("")
            directory.mkdir(parents=True)
            mask = tree.nodes.new("CompositorNodeIDMask")
            tree.links.new(index_pass, mask.inputs[0])
            save = tree.nodes.new("CompositorNodeOutputFile")
            if uses_node_group:
                mask.inputs["Index"].default_value = indices[char]
                save.format.media_type = "IMAGE"
                save.directory = str(directory)
                save.file_name = "mask"
                save.save_as_render = False
                save.file_output_items.new("FLOAT", "Mask")
            else:
                mask.index = indices[char]
                save.base_path = str(directory)
                save.file_slots[0].save_as_render = False
            save.format.file_format = "PNG"
            save.format.color_mode = "BW"
            save.format.color_depth = "8"
            tree.links.new(mask.outputs[0], save.inputs[0])

        # Beauty and masks share the exact same camera, visibility and resolution.
        bpy.ops.render.render(scene=scene.name, write_still=True)
        for output in map(Path, masks.values()):
            files = list(output.with_suffix("").glob("*.png"))
            if len(files) != 1:
                raise RuntimeError(f"Blender did not write {output.name}")
            files[0].replace(output)
    finally:
        view_layer.use_pass_object_index = False
        if uses_node_group:
            scene.compositing_node_group = None
            bpy.data.node_groups.remove(tree)
        else:
            tree.nodes.clear()
            scene.use_nodes = False


def render_job(job_path):
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    spec = job["spec"]

    # Blender's add-on shares a process with the artist's current file.
    # Track our data blocks so they can be removed without touching their scene.
    groups = (
        bpy.data.scenes, bpy.data.objects, bpy.data.meshes, bpy.data.materials,
        bpy.data.worlds, bpy.data.lights, bpy.data.cameras, bpy.data.images,
    )
    originals = [set(group) for group in groups]
    renamed = {}
    try:
        scene = bpy.data.scenes.new(spec["scene"])
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = 4
        scene.cycles.max_bounces = 1
        scene.cycles.diffuse_bounces = 0
        scene.cycles.use_denoising = False
        scene.render.threads_mode = "FIXED"
        scene.render.threads = min(8, scene.render.threads)
        scene.render.resolution_x, scene.render.resolution_y = spec["resolution"]
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_mode = "RGB"

        cameras = {}
        for shot, camera_spec in spec["cameras"].items():
            name = camera_spec["name"]
            if (original := bpy.data.objects.get(name)) is not None:
                renamed[original] = name
                original.name = f"Shotforge temporary {uuid4()}"
            cameras[shot] = _camera(scene, camera_spec)
        _check_axis(scene, cameras, spec["axis"])

        geometry = spec["geometry"]
        images = {}
        materials = spec.get("materials", {})
        wall = _textured_material(
            _material("Plaster blockout", 0.46), materials.get("wall"), images,
        )
        floor = _textured_material(
            _material("Floor blockout", 0.28), materials.get("floor"), images,
        )
        furniture = _material("Furniture blockout", 0.23)
        table = _textured_material(furniture, materials.get("table"), images)
        _room(scene, geometry, wall, floor)
        _table(scene, geometry["hero_table"], table)
        greys = (0.54, 0.68, 0.41)
        characters = dict.fromkeys(
            [figure["char"] for figure in spec["mannequins"]]
            + [char for masks in job["masks"].values() for char in masks]
        )
        indices = {char: index for index, char in enumerate(characters, start=1)}
        for index, figure in enumerate(spec["mannequins"]):
            char = figure["char"]
            material = _material(f"{char} grey", greys[index % len(greys)])
            garment = _textured_material(material, materials.get(f"{char}.garment"), images)
            _mannequin(
                scene, figure, geometry["hero_table"]["size"][2], furniture, material, garment,
                indices[char],
            )
        _lighting(scene)

        for shot, path in job["renders"].items():
            scene.camera = cameras[shot]
            _render_shot(scene, path, job["masks"][shot], indices)

        first = next(iter(cameras))
        scene.camera = cameras[first]
        scene.render.filepath = f'//renders/{spec["scene"]}/{first}/blockout.png'
        # Write only the generated scene and its dependencies from the current session.
        bpy.data.libraries.write(job["blend"], {scene})
    finally:
        bpy.data.batch_remove(ids=set().union(
            *(set(group) - old for group, old in zip(groups, originals, strict=True))
        ))
        for original, name in renamed.items():
            original.name = name


if __name__ == "__main__":
    render_job(sys.argv[sys.argv.index("--") + 1])
