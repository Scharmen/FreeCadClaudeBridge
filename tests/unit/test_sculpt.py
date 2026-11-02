"""Sculpting organic shapes from smoothly blended primitives (signed distance fields) into closed meshes."""

import math
import struct

import numpy as np
import pytest

from freecad_bridge import sculpt


def volume(verts, tris):
    a, b, c = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def closed(tris):
    """Watertight and manifold: every edge belongs to exactly two triangles."""
    edges = np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return bool((counts == 2).all())


def test_sphere_is_closed_outward_and_the_right_size():
    v, t = sculpt.mesh_part({"primitives": [{"type": "sphere", "center": [0, 0, 0], "radius": 10}]},
                            voxel=0.5, smooth=3)
    assert closed(t)
    vol = volume(v, t)
    assert vol > 0, "normals point outward"
    assert vol == pytest.approx(4 / 3 * math.pi * 1000, rel=0.05)
    assert (v.max(0) - v.min(0)) == pytest.approx([20, 20, 20], abs=0.6)


def test_smooth_blend_adds_a_fillet_between_shapes():
    two = [{"type": "sphere", "center": [-5, 0, 0], "radius": 5}, {"type": "sphere", "center": [5, 0, 0], "radius": 5}]
    hard = volume(*sculpt.mesh_part({"primitives": two, "blend": 0}, voxel=0.4, smooth=0))
    soft = volume(*sculpt.mesh_part({"primitives": two, "blend": 4}, voxel=0.4, smooth=0))
    assert soft > hard * 1.03, "the blend fills the gap like clay"


def test_subtract_and_tapered_capsule():
    v, t = sculpt.mesh_part({"primitives": [
        {"type": "box", "center": [0, 0, 0], "size": [20, 20, 20], "round": 2},
        {"type": "capsule", "a": [0, 0, -15], "b": [0, 0, 15], "radius": 4, "op": "subtract"}]}, voxel=0.5, smooth=0)
    assert volume(v, t) < 20 ** 3 - math.pi * 16 * 20 * 0.8
    v2, _ = sculpt.mesh_part({"primitives": [{"type": "capsule", "a": [0, 0, 0], "b": [0, 0, 30], "radius": 5,
                                              "radius_b": 1}]}, voxel=0.4, smooth=0)
    assert v2[:, 2].min() == pytest.approx(-5, abs=0.6) and v2[:, 2].max() == pytest.approx(31, abs=0.6)


def test_strand_follows_its_points_and_tapers():
    path = [[0, 0, 0], [5, 0, 10], [0, 0, 20], [5, 0, 30]]
    v, t = sculpt.mesh_part({"primitives": [{"type": "strand", "points": path,
                                             "radius": 2, "radius_end": 0.6}]}, voxel=0.3, smooth=1)
    assert closed(t) and v[:, 2].max() == pytest.approx(30.6, abs=0.6)


def test_surface_detail_roughens_but_keeps_the_size():
    base = {"primitives": [{"type": "sphere", "center": [0, 0, 0], "radius": 10}]}
    v0, _ = sculpt.mesh_part(base, voxel=0.5, smooth=0)
    v1, _ = sculpt.mesh_part({**base, "detail": {"amplitude": 0.6, "scale": 3, "seed": 2}}, voxel=0.5, smooth=0)
    r0, r1 = np.linalg.norm(v0, axis=1), np.linalg.norm(v1, axis=1)
    assert r1.std() > r0.std() * 2 and abs(r1.mean() - r0.mean()) < 0.8


def test_rotated_ellipsoid():
    v, _ = sculpt.mesh_part({"primitives": [{"type": "ellipsoid", "center": [0, 0, 0], "radii": [10, 3, 3],
                                             "rotate": [0, 0, 90]}]}, voxel=0.4, smooth=0)
    size = v.max(0) - v.min(0)
    assert size[1] == pytest.approx(20, abs=1.0) and size[0] == pytest.approx(6, abs=1.0)


def test_resolution_is_capped_and_bad_input_is_explained():
    voxel = sculpt.choose_voxel([(-500, -500, -500), (500, 500, 500)], requested=0.1)
    assert 1000 / voxel <= sculpt.MAX_CELLS + 1
    with pytest.raises(ValueError, match="unknown primitive"):
        sculpt.mesh_part({"primitives": [{"type": "blob"}]})
    with pytest.raises(ValueError, match="radius"):
        sculpt.mesh_part({"primitives": [{"type": "sphere", "center": [0, 0, 0]}]})


def test_parts_and_binary_stl(tmp_path):
    scene = {"blend": 1.5, "parts": [
        {"name": "Body", "color": [0.8, 0.6, 0.5],
         "primitives": [{"type": "sphere", "center": [0, 0, 0], "radius": 5}]},
        {"name": "Hat", "primitives": [{"type": "torus", "center": [0, 0, 4], "radius": 5, "thickness": 1}]}]}
    out = sculpt.sculpt_scene(scene, str(tmp_path), voxel=0.4)
    assert [p["name"] for p in out["parts"]] == ["Body", "Hat"]
    data = open(out["parts"][0]["path"], "rb").read()
    count = struct.unpack("<I", data[80:84])[0]
    assert count == out["parts"][0]["triangles"] and len(data) == 84 + 50 * count


def test_surface_detail_stays_manifold():
    part = {"blend": 0.8, "detail": {"amplitude": 0.15, "scale": 1.6},
            "primitives": [{"type": "cylinder", "a": [0, 0, 0], "b": [0, 0, 6], "radius": 13},
                           {"type": "torus", "center": [0, 0, 6], "radius": 12.4, "thickness": 0.8}]}
    v, t = sculpt.mesh_part(part, voxel=0.22, smooth=2)
    assert closed(t) and volume(v, t) > 0


def test_floating_pieces_are_reported(tmp_path):
    scene = {"parts": [{"name": "Two", "primitives": [{"type": "sphere", "center": [0, 0, 0], "radius": 3},
                                                       {"type": "sphere", "center": [20, 0, 0], "radius": 3}]}]}
    part = sculpt.sculpt_scene(scene, str(tmp_path), voxel=0.4)["parts"][0]
    assert part["pieces"] == 2 and "separate pieces" in part["warning"]


def test_split_vertices_survive_an_stl_round_trip(tmp_path):
    part = {"blend": 0.8, "detail": {"amplitude": 0.15, "scale": 1.6},
            "primitives": [{"type": "cylinder", "a": [0, 0, 0], "b": [0, 0, 6], "radius": 13}]}
    v, t = sculpt.mesh_part(part, voxel=0.22, smooth=2)
    coords = np.round(v[t].reshape(-1, 3), 6)                      # what an STL reader sees
    _, merged = np.unique(coords, axis=0, return_inverse=True)
    assert closed(merged.reshape(-1, 3)), "identical points merged on reading must not pinch the surface again"
