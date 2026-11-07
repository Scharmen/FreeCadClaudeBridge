"""Print analysis on triangle meshes: walls, overhangs, bridges, base, tipping, fits, sections."""

import numpy as np
import pytest

from freecad_bridge.checks import printing as pr


def box(lo, hi):
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    v = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                  [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], dtype=float)
    t = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
                  [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
    return v, t


def join(*meshes):
    vs, ts, off = [], [], 0
    for v, t in meshes:
        vs.append(v)
        ts.append(t + off)
        off += len(v)
    return np.vstack(vs), np.vstack(ts)


def hollow(size, wall, open_top=False):
    """A box with an inner cavity: the inner shell's triangles face into the cavity."""
    x, y, z = size
    outer = box((0, 0, 0), size)
    v, t = box((wall, wall, wall), (x - wall, y - wall, z - wall))
    return join(outer, (v, t[:, ::-1]))


def fine(v, t, n=3):
    """Split triangles so that area-weighted samples also land near edges."""
    for _ in range(n):
        a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
        ab, bc, ca = (a + b) / 2, (b + c) / 2, (c + a) / 2
        i = len(v)
        idx = np.arange(len(t))
        v = np.vstack([v, ab, bc, ca])
        m = len(t)
        iab, ibc, ica = i + idx, i + m + idx, i + 2 * m + idx
        t = np.vstack([np.c_[t[:, 0], iab, ica], np.c_[iab, t[:, 1], ibc], np.c_[ica, ibc, t[:, 2]],
                       np.c_[iab, ibc, ica]])
    return pr.merge(v, t)


def problems(rep):
    return " | ".join(p["cause"] for p in rep["problems"])


def warnings(rep):
    return " | ".join(p["cause"] for p in rep["warnings"])


def test_wall_thickness_of_a_hollow_box_is_found():
    w = pr.wall_thickness(*fine(*hollow((40, 30, 20), 1.0)), samples=1500)
    assert w["min_mm"] == pytest.approx(1.0, abs=0.05)
    assert w["thin_at"] is not None


def test_a_solid_block_is_as_thick_as_its_smallest_side():
    w = pr.wall_thickness(*fine(*box((0, 0, 0), (40, 30, 10))), samples=1500)
    assert w["min_mm"] == pytest.approx(10, abs=0.1)


def test_thin_walls_are_a_problem_with_where_and_how_to_fix():
    rep = pr.analyze(*fine(*hollow((40, 30, 20), 0.6)), process="fdm")
    assert not rep["ok"]
    bad = next(p for p in rep["problems"] if "wall" in p["cause"])
    assert "0.6" in bad["cause"] and "1.2" in bad["cause"]
    assert len(bad["where"]) == 3 and bad["fix"]
    assert pr.analyze(*fine(*hollow((40, 30, 20), 2.0)), process="fdm")["ok"]


def test_resin_allows_thinner_walls_than_fdm():
    mesh = fine(*hollow((40, 30, 20), 1.0))
    assert not pr.analyze(*mesh, process="fdm")["ok"]
    assert pr.analyze(*mesh, process="resin")["ok"]


def test_an_open_mesh_is_a_problem():
    v, t = box((0, 0, 0), (10, 10, 10))
    rep = pr.analyze(v, t[:-2], process="fdm")
    assert "not closed" in problems(rep)


def cells(xs, ys, zs, filled):
    """A closed mesh of grid cells: only the outer faces, sharing their vertices."""
    filled = set(filled)
    verts, tris, ids = [], [], {}

    def vid(i, j, k):
        if (i, j, k) not in ids:
            ids[(i, j, k)] = len(verts)
            verts.append((xs[i], ys[j], zs[k]))
        return ids[(i, j, k)]

    for i, j, k in filled:
        c = [vid(i + a, j + b, k + d) for d in (0, 1) for b in (0, 1) for a in (0, 1)]
        faces = {(-1, 0, 0): (0, 4, 6, 2), (1, 0, 0): (1, 3, 7, 5), (0, -1, 0): (0, 1, 5, 4),
                 (0, 1, 0): (2, 6, 7, 3), (0, 0, -1): (0, 2, 3, 1), (0, 0, 1): (4, 5, 7, 6)}
        for (dx, dy, dz), (p, q, r, u) in faces.items():
            if (i + dx, j + dy, k + dz) not in filled:
                tris += [(c[p], c[q], c[r]), (c[p], c[r], c[u])]
    return np.array(verts, dtype=float), np.array(tris)


def t_shape():
    return cells([-20, -3, 3, 20], [-3, 3], [0, 20, 26], [(1, 0, 0), (0, 0, 1), (1, 0, 1), (2, 0, 1)])


def test_overhang_is_measured_and_a_better_orientation_is_suggested():
    rep = pr.analyze(*fine(*t_shape(), n=1), process="fdm")
    assert rep["values"]["overhang_mm2"] == pytest.approx(2 * 17 * 6, rel=0.1)
    assert "support" in warnings(rep)
    assert rep["values"]["best_up"] != "+Z"
    assert rep["values"]["orientations"]["-Z"]["overhang_mm2"] < 1


def test_the_flat_bottom_is_not_an_overhang():
    rep = pr.analyze(*box((0, 0, 0), (20, 20, 20)), process="fdm")
    assert rep["values"]["overhang_mm2"] == 0
    assert rep["values"]["base_mm2"] == pytest.approx(400)
    assert rep["ok"] and not rep["warnings"]


def test_a_long_bridge_is_a_warning_and_a_short_one_is_not():
    def arch(span):
        return cells([0, 5, 5 + span, 10 + span], [0, 10], [0, 10, 13],
                     [(0, 0, 0), (2, 0, 0), (0, 0, 1), (1, 0, 1), (2, 0, 1)])
    assert "bridge" in warnings(pr.analyze(*arch(25), process="fdm"))
    assert "bridge" not in warnings(pr.analyze(*arch(6), process="fdm"))


def test_a_top_heavy_part_tips_over():
    rep = pr.analyze(*cells([0, 4, 30], [0, 8], [0, 4, 24], [(0, 0, 0), (0, 0, 1), (1, 0, 1)]), process="fdm")
    assert "tip" in problems(rep)
    assert pr.analyze(*box((0, 0, 0), (30, 30, 10)), process="fdm")["values"]["stable"]


def test_fit_verdicts_by_kind_and_process():
    assert pr.fit_verdict(0.25, 0, "sliding", "fdm")["ok"]
    bad = pr.fit_verdict(0.0, 0, "sliding", "fdm")
    assert not bad["ok"] and "0.15" in bad["problem"]["fix"]
    assert pr.fit_verdict(0.0, 0, "press", "fdm")["ok"]
    collide = pr.fit_verdict(0.0, 12.5, "sliding", "fdm")
    assert not collide["ok"] and "12.5" in collide["problem"]["cause"]
    assert pr.fit_verdict(0.1, 0, "sliding", "resin")["ok"] and not pr.fit_verdict(0.1, 0, "sliding", "fdm")["ok"]
    assert pr.fit_verdict(1.0, 0, "custom", "fdm", gap_range=[0.5, 2])["ok"]
    with pytest.raises(ValueError, match="sliding"):
        pr.fit_verdict(0, 0, "wobbly", "fdm")


def test_groups_of_overlapping_parts_print_as_one():
    a, b, c = box((0, 0, 0), (10, 10, 10)), box((5, 5, 5), (15, 15, 15)), box((40, 0, 0), (50, 10, 10))
    assert pr.print_groups([a, b, c]) == [[0, 1], [2]]


def test_section_segments_of_a_hollow_box():
    v, t = hollow((40, 30, 20), 2.0)
    seg = pr.section_segments(v, t, "z", 10.0)
    assert len(seg) > 0 and seg.shape[1:] == (2, 2)
    assert seg[..., 0].min() == pytest.approx(0) and seg[..., 0].max() == pytest.approx(40)
    assert seg[..., 1].max() == pytest.approx(30)
    assert len(pr.section_segments(v, t, "z", 99.0)) == 0


def test_overlapping_parts_are_one_print_with_walls_checked_per_part():
    body = fine(*box((0, 0, 0), (20, 20, 20)))
    plate = fine(*box((-1, -1, 19.5), (21, 21, 20.2)))      # 0.7 mm: too thin, sits on the body
    rep = pr.analyze_objects([("Body", *body), ("Plate", *plate)])
    assert len(rep["prints"]) == 1 and rep["prints"][0]["objects"] == ["Body", "Plate"]
    assert not rep["ok"] and "Plate:" in problems(rep["prints"][0]) and "wall" in problems(rep["prints"][0])
    apart = pr.analyze_objects([("A", *box((0, 0, 0), (20, 20, 20))), ("B", *box((40, 0, 0), (60, 20, 20)))])
    assert apart["ok"] and len(apart["prints"]) == 2


def test_a_seated_lid_may_touch_at_the_seat_but_needs_room_at_the_sides():
    good = pr.fit_verdict(0.0, 0, "seated", "fdm", side_gap=0.2)
    assert good["ok"] and good["side_gap_mm"] == 0.2
    tight = pr.fit_verdict(0.0, 0, "snap", "fdm", side_gap=0.0)
    assert not tight["ok"] and "side" in tight["problem"]["cause"]
    unknown = pr.fit_verdict(0.0, 0, "seated", "fdm")
    assert not unknown["ok"] and "seat" in unknown["problem"]["cause"]
    assert pr.fit_verdict(0.25, 0, "seated", "fdm")["ok"], "not touching at all: the gap itself counts"
    assert not pr.fit_verdict(0.0, 3.0, "seated", "fdm", side_gap=0.2)["ok"], "overlap is never seating"


def test_a_press_fit_does_not_excuse_parts_that_run_into_each_other():
    assert pr.fit_verdict(0.0, 30.0, "press", "fdm", overlap_depth=0.08)["ok"]
    deep = pr.fit_verdict(0.0, 20.8, "press", "fdm", overlap_depth=1.5)
    assert not deep["ok"] and "1.5" in deep["problem"]["cause"]


def test_check_all_report_puts_every_problem_first_with_its_source():
    from freecad_bridge.checks import runner

    design = {"score": 80.0, "passed": 4, "matched": {}, "parts": [], "gaps": [],
              "problems": [{"check": "bbox", "found": "50 × 50 × 13 mm"}], "tree_errors": ["Pad: broken"]}
    prints = {"ok": False, "prints": [{"objects": ["Lid"], "ok": False, "values": {"closed": True, "wall_mm": 0.8,
                                                                                   "best_up": "+Z"},
                                       "problems": [{"cause": "walls about 0.8 mm", "where": [0, 0, 1],
                                                     "fix": "thicken"}],
                                       "warnings": [{"cause": "a bridge of 12 mm", "where": [0, 0, 5], "fix": "rib"}]}]}
    fits = [{"a": "Lid", "b": "Box", "kind": "snap", "ok": False, "gap_mm": 0.0, "overlap_mm3": 0.0,
             "problem": {"cause": "the side gap is 0 mm", "fix": "widen it"}},
            {"a": "Pin", "b": "Hole", "kind": "sliding", "ok": True, "gap_mm": 0.2, "overlap_mm3": 0.0},
            {"a": "Ghost", "b": "Box", "error": "ValueError: 'Ghost' is not a solid"}]
    rep = runner.all_summary(design, prints, fits)
    assert rep["ok"] is False
    assert [p["from"] for p in rep["problems"]] == ["design", "design", "print", "fit", "fit"]
    assert "bbox" in rep["problems"][0]["cause"] and rep["problems"][2]["objects"] == ["Lid"]
    assert rep["warnings"][0]["cause"].startswith("a bridge")
    assert rep["print"][0]["wall_mm"] == 0.8 and rep["fits"][1]["ok"] is True
    assert runner.all_summary(None, None, [])["ok"] is True
