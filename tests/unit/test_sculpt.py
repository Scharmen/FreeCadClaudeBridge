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


def test_one_piece_unites_a_template_into_a_single_closed_mesh(tmp_path):
    res = sculpt.sculpt_scene({"template": "dog_standing", "height": 30, "one_piece": True}, str(tmp_path), voxel=0.3)
    assert [p["name"] for p in res["parts"]] == ["dog_standing"]
    piece = res["parts"][0]
    assert piece["pieces"] == 1 and piece["closed"] is True and "warning" not in piece
    v, t = read_stl(piece["path"])
    assert closed(t)
    apart = sculpt.sculpt_scene({"template": "dog_standing", "height": 30}, str(tmp_path / "apart"), voxel=0.3)
    biggest = max(volume(*read_stl(p["path"])) for p in apart["parts"])
    assert volume(v, t) > biggest, "the base is in it too"
    assert res["size_mm"] == pytest.approx(apart["size_mm"], abs=0.5)


def test_one_piece_keeps_each_part_s_fur_and_takes_the_scene_name(tmp_path):
    body = {"name": "Body", "color": [0.8, 0.6, 0.5], "primitives": [{"type": "sphere", "center": [0, 0, 0],
                                                                         "radius": 6}]}
    hair = {"name": "Hair", "fur": {"depth": 0.3, "length": 4, "width": 1.0, "from": [0, 0, 8]},
            "primitives": [{"type": "sphere", "center": [0, 0, 4], "radius": 4}]}
    one = sculpt.sculpt_scene({"name": "Head", "one_piece": True, "parts": [body, hair]}, str(tmp_path), voxel=0.3)
    assert [p["name"] for p in one["parts"]] == ["Head"] and one["parts"][0]["color"] == [0.8, 0.6, 0.5]
    v, t = read_stl(one["parts"][0]["path"])
    assert closed(t)
    smooth = sculpt.sculpt_scene({"name": "Head", "one_piece": True, "parts": [body, {**hair, "fur": None}]},
                                 str(tmp_path / "smooth"), voxel=0.3)
    sv, st = read_stl(smooth["parts"][0]["path"])

    def grooves(pts):   # how much the hair's radius varies over its top
        top = pts[pts[:, 2] > 6.5]
        return np.linalg.norm(top - np.array([0, 0, 4]), axis=1).std()
    assert grooves(v) > 5 * grooves(sv), "the grooves of the hair are still there"


def test_small_holes_in_a_mesh_are_closed_and_reported():
    v, t = sculpt.mesh_part({"primitives": [{"type": "sphere", "center": [0, 0, 0], "radius": 10}]},
                            voxel=0.5, smooth=2)
    top = np.argsort(-v[t].mean(1)[:, 2])[:4]            # four triangles near the top: one hole
    opened = np.delete(t, top, axis=0)
    assert not closed(opened)
    fixed, holes = sculpt.close_holes(v, opened)
    assert holes == 1 and closed(fixed)
    assert volume(v, fixed) == pytest.approx(volume(v, t), rel=0.01)
    same, none = sculpt.close_holes(v, t)
    assert none == 0 and len(same) == len(t)


def test_a_hair_preset_expands_into_many_tapered_strands_from_the_scalp():
    prims = sculpt.expand_macros([{"type": "hair", "center": [0, 0, 70], "radius": 4, "count": 30, "length": 6,
                                   "style": "short", "seed": 3}])
    assert len(prims) == 30 and all(p["type"] == "strand" for p in prims)
    for p in prims:
        root, tip = np.array(p["points"][0]), np.array(p["points"][-1])
        assert np.linalg.norm(root - [0, 0, 70]) < 4.5, "roots on the scalp"
        assert 3 <= np.linalg.norm(tip - root) <= 6.5 and tip[2] <= root[2] + 1.5, "short hair lies down"
        assert p["radius_end"] < p["radius"] and len(p["points"]) >= 3
        assert root[2] > 70 - 1.5, "hair grows on the upper half of the head"
    again = sculpt.expand_macros([{"type": "hair", "center": [0, 0, 70], "radius": 4, "count": 30, "length": 6,
                                   "style": "short", "seed": 3}])
    assert again == prims, "the same seed gives the same hair"
    beard = sculpt.expand_macros([{"type": "hair", "center": [0, 0, 70], "radius": 4, "count": 20, "length": 5,
                                   "style": "beard", "seed": 1}])
    assert all(np.array(p["points"][0])[2] < 70 and np.array(p["points"][0])[1] < 0 for p in beard), \
        "a beard grows on the lower front of the head"
    fur = sculpt.expand_macros([{"type": "hair", "center": [0, 0, 20], "radii": [8, 12, 6], "count": 40, "length": 3,
                                 "style": "fur", "seed": 2}])
    assert len(fur) == 40
    with pytest.raises(ValueError, match="style"):
        sculpt.expand_macros([{"type": "hair", "center": [0, 0, 0], "radius": 1, "style": "mohawk"}])


def test_hair_in_a_scene_meshes_as_locks_on_the_head(tmp_path):
    head = {"name": "Head", "primitives": [{"type": "sphere", "center": [0, 0, 0], "radius": 6}]}
    hair = {"name": "Hair", "blend": 0.5, "primitives": [{"type": "hair", "center": [0, 0, 0], "radius": 6,
                                                          "count": 40, "length": 5, "style": "short", "seed": 1}]}
    res = sculpt.sculpt_scene({"parts": [head, hair]}, str(tmp_path), voxel=0.3)
    locks = next(p for p in res["parts"] if p["name"] == "Hair")
    assert locks["pieces"] >= 1 and "warning" not in locks, locks


def test_a_pose_turns_the_head_and_raises_an_arm_of_the_template():
    plain = sculpt.expand_template({"template": "person_standing", "height": 83.4})
    posed = sculpt.expand_template({"template": "person_standing", "height": 83.4,
                                    "pose": {"head": 40, "arm_r": 80}})
    skin = next(p for p in plain["parts"] if p["name"] == "Skin")["primitives"]
    i_nose = next(i for i, q in enumerate(skin) if q["type"] == "sphere" and q["center"][1] < -4
                  and 75 < q["center"][2] < 77)
    hands = [i for i, q in enumerate(skin) if q["type"] == "ellipsoid" and abs(q["center"][0]) > 10
             and 36 < q["center"][2] < 42]
    i_right = next(i for i in hands if skin[i]["center"][0] > 0)
    i_left = next(i for i in hands if skin[i]["center"][0] < 0)

    def prim(scene, index):
        return np.array(next(p for p in scene["parts"] if p["name"] == "Skin")["primitives"][index]["center"])

    assert abs(prim(posed, i_nose)[0] - prim(plain, i_nose)[0]) > 2, "the head turned about its own axis"
    assert np.linalg.norm(prim(posed, i_nose) - [0, 0.4, 72]) == pytest.approx(
        np.linalg.norm(prim(plain, i_nose) - [0, 0.4, 72]), abs=0.3)
    assert prim(posed, i_right)[2] > prim(plain, i_right)[2] + 15, "the right arm is raised"
    assert prim(posed, i_right)[0] > prim(plain, i_right)[0] + 10, "and points away from the body"
    assert np.allclose(prim(posed, i_left), prim(plain, i_left)), "the left arm did not move"
    shirt = next(p for p in posed["parts"] if p["name"] == "Shirt")
    sleeve = [q for q in shirt["primitives"] if (q.get("center") or q.get("a") or [0])[0] > 7]
    assert sleeve and all((q.get("center") or q.get("a"))[2] > 58 for q in sleeve), "the sleeve went with the arm"
    with pytest.raises(ValueError, match="joint"):
        sculpt.expand_template({"template": "person_standing", "pose": {"tail": 10}})


def test_a_posed_template_still_meshes_closed(tmp_path):
    res = sculpt.sculpt_scene({"template": "dog_standing", "height": 30, "pose": {"head": 30, "tail": -40},
                               "one_piece": True}, str(tmp_path), voxel=0.3)
    assert res["parts"][0]["closed"] and res["parts"][0]["pieces"] == 1
