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


def read_stl(path):
    """Points and triangles as an STL reader (FreeCAD) sees them: identical coordinates are one point."""
    data = open(path, "rb").read()
    n = struct.unpack("<I", data[80:84])[0]
    rec = np.frombuffer(data[84:84 + 50 * n], dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]))
    coords = rec["v"].reshape(-1, 3)
    pts, idx = np.unique(coords, axis=0, return_inverse=True)
    return pts, idx.reshape(-1, 3)


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
    assert part["pieces"] == 2 and "float" in part["warning"]


def test_split_vertices_survive_an_stl_round_trip(tmp_path):
    part = {"blend": 0.8, "detail": {"amplitude": 0.15, "scale": 1.6},
            "primitives": [{"type": "cylinder", "a": [0, 0, 0], "b": [0, 0, 6], "radius": 13}]}
    v, t = sculpt.mesh_part(part, voxel=0.22, smooth=2)
    coords = np.round(v[t].reshape(-1, 3), 6)                      # what an STL reader sees
    _, merged = np.unique(coords, axis=0, return_inverse=True)
    assert closed(merged.reshape(-1, 3)), "identical points merged on reading must not pinch the surface again"


def test_evaluation_in_slabs_gives_the_same_field(monkeypatch):
    part = {"detail": {"amplitude": 0.3, "scale": 2},
            "primitives": [{"type": "ellipsoid", "center": [0, 0, 0], "radii": [9, 6, 4]},
                           {"type": "capsule", "a": [0, 0, 0], "b": [0, 0, 8], "radius": 2, "op": "subtract"}]}
    whole, _, _ = sculpt.field(part, voxel=0.5)
    monkeypatch.setattr(sculpt, "CHUNK", 777)             # many slabs and noise chunks of odd size
    sliced, _, _ = sculpt.field(part, voxel=0.5)
    assert np.allclose(whole, sliced, atol=1e-5)


def test_default_resolution_is_fine_enough_for_faces():
    v = sculpt.choose_voxel([(0, 0, 0), (30, 30, 60)])
    assert v == pytest.approx(60 / sculpt.DEFAULT_CELLS)
    assert 60 / v >= 300, "a 6 cm figure gets at least 300 cells: eyes and noses stay crisp"
    assert sculpt.choose_voxel([(0, 0, 0), (30, 30, 60)], requested=0.01) == pytest.approx(60 / sculpt.MAX_CELLS)


def test_all_parts_of_a_scene_share_one_grid(tmp_path):
    scene = {"parts": [{"name": "Body", "primitives": [{"type": "box", "center": [0, 0, 30], "size": [20, 10, 60]}]},
                       {"name": "Eye", "primitives": [{"type": "sphere", "center": [0, -5, 50], "radius": 0.8}]}]}
    res = sculpt.sculpt_scene(scene, str(tmp_path))
    assert res["voxel_mm"] == pytest.approx(60 / sculpt.DEFAULT_CELLS, rel=0.1)  # bounds include the blend
    eye = next(p for p in res["parts"] if p["name"] == "Eye")
    assert eye["triangles"] < 5000, "a small part is not meshed finer than the rest"


def fur_part(**fur):
    return {"primitives": [{"type": "capsule", "a": [0, 0, -10], "b": [0, 0, 10], "radius": 6}], "smooth": 1,
            "fur": {"depth": 0.4, "length": 6, "width": 0.8, **fur}}


def test_fur_grooves_run_along_the_flow_and_keep_the_size():
    v, t = sculpt.mesh_part(fur_part(direction=[0, 0, -1]), voxel=0.12)
    assert closed(t)
    r = np.hypot(v[:, 0], v[:, 1])
    mid = np.abs(v[:, 2]) < 7
    assert 5.3 < r[mid].mean() < 6.6
    assert r[mid].std() > 0.025, "the surface is grooved"
    # grooves follow the flow (z): the radius changes slowly along z and fast around the body
    ang = np.arctan2(v[mid, 1], v[mid, 0])
    z, rr = v[mid, 2], r[mid]
    ring = np.abs(z) < 0.15
    line = np.abs(ang - 0.5) < 0.03
    assert rr[ring].std() > 1.8 * rr[line].std()


def test_fur_can_flow_away_from_a_point_and_is_repeatable():
    a = sculpt.mesh_part(fur_part(**{"from": [0, 0, 12]}), voxel=0.25)
    b = sculpt.mesh_part(fur_part(**{"from": [0, 0, 12]}), voxel=0.25)
    assert np.array_equal(a[0], b[0])
    with pytest.raises(ValueError, match="fur"):
        sculpt.mesh_part(fur_part(depth=0), voxel=0.5)


def test_templates_are_listed_scaled_and_extended(tmp_path):
    names = sculpt.template_names()
    assert {"cat_sitting", "dog_standing", "person_standing"} <= set(names)
    scene = sculpt.expand_template({"template": "dog_standing", "height": 100,
                                    "parts": [{"name": "Dog", "color": [0.1, 0.1, 0.1],
                                               "primitives": [{"type": "sphere", "center": [0, 0, 120],
                                                               "radius": 2}]},
                                              {"name": "Collar", "primitives": [{"type": "sphere",
                                                                                 "center": [0, 0, 0], "radius": 1}]}]})
    base = sculpt.load_template("dog_standing")
    k = 100 / base["height"]
    dog = next(p for p in scene["parts"] if p["name"] == "Dog")
    orig = next(p for p in base["parts"] if p["name"] == "Dog")
    assert dog["color"] == [0.1, 0.1, 0.1]
    assert len(dog["primitives"]) == len(orig["primitives"]) + 1
    p0, q0 = dog["primitives"][0], orig["primitives"][0]
    assert p0["center"] == pytest.approx([c * k for c in q0["center"]])
    assert dog["blend"] == pytest.approx(orig["blend"] * k)
    assert dog["primitives"][-1]["center"] == [0, 0, 120], "added primitives are already in the final size"
    assert any(p["name"] == "Collar" for p in scene["parts"])
    with pytest.raises(ValueError, match="cat_sitting"):
        sculpt.expand_template({"template": "unicorn"})


@pytest.mark.parametrize("name", ["cat_sitting", "dog_standing", "person_standing"])
def test_every_template_meshes_watertight_in_one_piece_per_part(name, tmp_path):
    res = sculpt.sculpt_scene({"template": name, "height": 30}, str(tmp_path), voxel=0.3)
    assert res["parts"]
    assert not [p["name"] for p in res["parts"] if p.get("warning")]
    for p in res["parts"]:   # what FreeCAD's Mesh.isSolid() needs: every edge between exactly two triangles
        v, t = read_stl(p["path"])
        assert closed(t), p["name"]
    assert res["size_mm"][2] == pytest.approx(30, abs=1.5), "the template's height is the whole figure on its base"


def test_a_piece_held_by_another_part_is_not_floating(tmp_path):
    arms = {"name": "Arms", "primitives": [{"type": "capsule", "a": [-8, 0, 0], "b": [-4, 0, 0], "radius": 1},
                                           {"type": "capsule", "a": [4, 0, 0], "b": [8, 0, 0], "radius": 1}]}
    shirt = {"name": "Shirt", "primitives": [{"type": "box", "center": [0, 0, 0], "size": [10, 4, 4]}]}
    res = sculpt.sculpt_scene({"parts": [arms, shirt]}, str(tmp_path), voxel=0.3)
    part = res["parts"][0]
    assert part["pieces"] == 2 and "warning" not in part
    alone = sculpt.sculpt_scene({"parts": [arms]}, str(tmp_path / "b"), voxel=0.3)
    assert "float" in alone["parts"][0]["warning"]


def test_specks_smaller_than_three_voxels_are_dropped():
    part = {"primitives": [{"type": "sphere", "center": [0, 0, 0], "radius": 5},
                           {"type": "sphere", "center": [9, 0, 0], "radius": 0.3}], "blend": 0}
    v, t = sculpt.mesh_part(part, voxel=0.25, smooth=0)
    assert sculpt.pieces(len(v), t) == 1 and v[np.unique(t)][:, 0].max() < 6
