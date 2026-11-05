"""Score a model against a task's numeric spec. Pure Python: the measurements come from `measure.py`.

Every check gives credit 0..1 (1 = met) and a detail that says what was found, so a result reads
like a review. The score is the weighted mean of the credits, 0..100.
"""

from __future__ import annotations

import math
from typing import Any

KNOWN = {"parts", "solids", "no_interference", "params", "expressions_min", "flex", "iou_min", "aligned_holes",
         "center_distance", "height", "total", "sketch_dof_zero", "requires", "joints", "clearance"}
PART_KEYS = {"name", "bbox", "tol", "oriented", "volume", "holes", "teeth", "radii_min", "surfaces_any", "surfaces_min",
             "inclined_min", "pitch"}
WEIGHTS = {"iou": 3.0}


def normalize(spec: dict[str, Any]) -> dict[str, Any]:
    """A single number where a range belongs means that number; a height may be 2 % off."""
    out = dict(spec)
    if isinstance(out.get("solids"), int | float):
        out["solids"] = [out["solids"], out["solids"]]
    if isinstance(out.get("height"), int | float):
        out["height"] = [out["height"] * 0.98, out["height"] * 1.02]
    return out


def n(x: float) -> str:
    return f"{round(x, 2):g}"


def rng(lo: float, hi: float) -> str:
    return f"{n(lo)}–{n(hi)}"


def dims_text(d: list[float]) -> str:
    return " × ".join(n(x) for x in d)


def _fit(p: dict[str, Any], m: dict[str, Any]) -> float:
    if "bbox" not in p:
        return 1e6 - m.get("volume", 0) / 1e6  # no size given: the biggest free solid
    want = p["bbox"] if p.get("oriented") else sorted(p["bbox"])
    have = m["dims"] if p.get("oriented") else m["sorted"]
    return sum(abs(a - b) for a, b in zip(want, have, strict=True)) / max(1e-9, sum(want))


def match(spec_parts: list[dict[str, Any]], parts: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    """Each spec part gets the closest measured solid; greedy over the best pairs first."""
    pairs = sorted((_fit(p, m), i, j) for i, p in enumerate(spec_parts) for j, m in enumerate(parts))
    out: dict[str, dict[str, Any] | None] = {p["name"]: None for p in spec_parts}
    used_p, used_m = set(), set()
    for _, i, j in pairs:
        if i in used_p or j in used_m:
            continue
        used_p.add(i)
        used_m.add(j)
        out[spec_parts[i]["name"]] = parts[j]
    return out


def _sub(a: list[float], b: list[float]) -> list[float]:
    return [x - y for x, y in zip(a, b, strict=True)]


def _cross(a: list[float], b: list[float]) -> list[float]:
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _norm(a: list[float]) -> float:
    return math.sqrt(sum(x * x for x in a))


def _unit(a: list[float]) -> list[float]:
    length = _norm(a) or 1.0
    return [x / length for x in a]


def same_axis(h1: dict[str, Any], h2: dict[str, Any], tol: float) -> bool:
    a1, a2 = _unit(h1["axis"]), _unit(h2["axis"])
    if _norm(_cross(a1, a2)) > 0.01:
        return False
    return _norm(_cross(_sub(h2["point"], h1["point"]), a1)) <= tol


def holes_in(part: dict[str, Any], lo: float, hi: float, open_ok: bool = False) -> list[dict[str, Any]]:
    """Holes of a diameter; bores open on one side (half a turn or more) only when `open_ok`."""
    return [h for h in part["holes"] if lo <= h["d"] <= hi and (open_ok or not h.get("open"))]


class Checks:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, name: str, ok: bool | float, detail: str = "", kind: str = "") -> None:
        credit = float(ok) if not isinstance(ok, bool) else (1.0 if ok else 0.0)
        credit = max(0.0, min(1.0, credit))
        self.items.append({"name": name, "ok": credit >= 1.0, "credit": round(credit, 4), "detail": detail,
                           "weight": WEIGHTS.get(kind, 1.0)})


def _part_checks(c: Checks, p: dict[str, Any], m: dict[str, Any] | None) -> None:
    name = p["name"]
    c.add(f"{name}: found", m is not None, m["label"] if m else "no solid left for this part")
    missing = "part missing"
    if "bbox" in p:
        tol = p.get("tol", 0.5)
        if m is None:
            c.add(f"{name}: size", False, missing)
        else:
            want = p["bbox"] if p.get("oriented") else sorted(p["bbox"])
            have = m["dims"] if p.get("oriented") else m["sorted"]
            ok = all(abs(a - b) <= tol for a, b in zip(want, have, strict=True))
            c.add(f"{name}: size", ok, f"{dims_text(have)} mm, wanted {dims_text(want)} ± {n(tol)}")
    if "volume" in p:
        lo, hi = p["volume"]
        c.add(f"{name}: volume", m is not None and lo <= m["volume"] <= hi,
              f"{n(m['volume'])} mm³, wanted {rng(lo, hi)}" if m else missing)
    for h in p.get("holes", []):
        lo, hi = h["d"]
        clo, chi = h.get("count", [1, 99])
        found = len(holes_in(m, lo, hi, h.get("open", False))) if m else 0
        sizes = sorted({n(x["d"]) for x in m["holes"] if not x.get("open")}) if m else []
        c.add(f"{name}: holes {rng(lo, hi)} mm", clo <= found <= chi,
              f"found {found}, wanted {clo if clo == chi else rng(clo, chi)}; hole sizes {', '.join(sizes) or 'none'}")
    if "teeth" in p:
        got = m.get("teeth") if m else None
        c.add(f"{name}: {p['teeth']} teeth", got == p["teeth"], f"counted {got}" if m else missing)
    radii = p.get("radii_min", [])
    for want_r in radii if isinstance(radii, list) else [radii]:
        lo, hi = want_r["r"]
        want = want_r["count"]
        got = sum(1 for r in m["radii"] if lo <= r <= hi) if m else 0
        c.add(f"{name}: round faces r {rng(lo, hi)}", got >= want, f"{got} round faces, wanted ≥ {want}")
    if "surfaces_any" in p:
        kinds = p["surfaces_any"]
        got = {k: v for k, v in (m["surfaces"] if m else {}).items() if k in kinds}
        c.add(f"{name}: curved surfaces", bool(got), f"{got or 'none'} of {', '.join(kinds)}")
    if "inclined_min" in p:
        got = m.get("inclined", 0) if m else 0
        c.add(f"{name}: sloped faces", got >= p["inclined_min"], f"{got} sloped faces, wanted ≥ {p['inclined_min']}")
    if "pitch" in p:
        t = p["pitch"]
        got = (m.get("thread") or {}).get("pitch") if m else None
        c.add(f"{name}: thread pitch {n(t['value'])}", got is not None and abs(got - t["value"]) <= t["tol"],
              f"measured {n(got)} mm" if got is not None else "no thread found")
    for kind, want in p.get("surfaces_min", {}).items():
        got = (m["surfaces"] if m else {}).get(kind, 0)
        c.add(f"{name}: ≥ {want} {kind} faces", got >= want, f"{got} {kind} faces")


def add_check(result: dict[str, Any], name: str, ok: bool, detail: str = "") -> dict[str, Any]:
    """One more check on an evaluated result (the benchmark's print check), with the score updated."""
    checks = [*result["checks"],
              {"name": name, "ok": ok, "credit": 1.0 if ok else 0.0, "detail": detail, "weight": 1.0}]
    total = sum(i["weight"] for i in checks)
    score = 100.0 * sum(i["credit"] * i["weight"] for i in checks) / total
    return {**result, "checks": checks, "score": round(score, 1),
            "failed": [f"{i['name']}: {i['detail']}" for i in checks if not i["ok"]]}


def evaluate(spec: dict[str, Any], measured: dict[str, Any]) -> dict[str, Any]:
    c = Checks()
    parts = measured.get("parts", [])
    spec_parts = spec.get("parts", [])
    matched = match(spec_parts, parts)
    for p in spec_parts:
        _part_checks(c, p, matched[p["name"]])

    if "solids" in spec:
        lo, hi = spec["solids"]
        c.add(f"solids {lo}–{hi}", lo <= len(parts) <= hi, f"{len(parts)} solids: "
              + ", ".join(p["label"] for p in parts[:8]))
    bad = [p["label"] for p in parts if not p["valid"] or not p.get("closed", True)]
    c.add("valid shapes", bool(parts) and not bad, "invalid or open: " + ", ".join(bad) if bad
          else ("no solid in the document" if not parts else f"{len(parts)} valid"))
    errors = measured.get("errors", [])
    c.add("no errors in the tree", not errors, ", ".join(errors[:8]))

    if spec.get("no_interference"):
        hits = measured.get("interference", [])
        c.add("no interference", not hits, "; ".join(f"{h['a']}–{h['b']} {n(h['volume'])} mm³" for h in hits))
    for cl in spec.get("clearance", []):
        pa, pb = matched.get(cl["a"]), matched.get(cl["b"])
        lo, hi = cl["range"]
        name = f"gap {cl['a']}–{cl['b']} {rng(lo, hi)} mm"
        pair = next((g for g in measured.get("gaps", []) if pa and pb
                     and {g["a"], g["b"]} == {pa["label"], pb["label"]}), None)
        got = pair["d"] if pair else None
        side = ""
        if pair and cl.get("seated") and got < 1e-3:      # touching at the seat is right: the sides count
            got = pair.get("side_gap")
            side = "at the sides" if got is not None else "they touch, and not only at one flat seat"
        ok = got is not None and lo <= got <= hi
        c.add(name, ok, f"{n(got)} mm {side}".strip() if got is not None else side or "part missing")
    if "params" in spec:
        have = {k.lower() for k in measured.get("params", {})}
        missing = [k for k in spec["params"] if k.lower() not in have]
        c.add("parameters", not missing, ("missing " + ", ".join(missing)) if missing else "all present")
    if "expressions_min" in spec:
        k = spec["expressions_min"]
        got = measured.get("expressions", 0)
        c.add(f"expressions ≥ {k}", got >= k, f"{got} expressions")
    if spec.get("sketch_dof_zero"):
        loose = [f"{s['label']} ({s['dof']})" for s in measured.get("sketches", []) if s["dof"]]
        c.add("sketches fully constrained", not loose, ("free: " + ", ".join(loose)) if loose else "")
    for req in spec.get("requires", []):
        types = measured.get("types", {})
        found = [t for t in req["types"] if types.get(t)]
        c.add(f"has {req['label']}", bool(found), ", ".join(found) or "not found")
    if "joints" in spec:
        have = measured.get("joints", [])
        missing = [j for j in spec["joints"] if j not in have]
        c.add("joints", not missing, f"have {', '.join(have) or 'none'}" + (f"; missing {', '.join(missing)}"
                                                                             if missing else ""))
    if "aligned_holes" in spec:
        a = spec["aligned_holes"]
        pa, pb = matched.get(a["a"]), matched.get(a["b"])
        hits = 0
        if pa and pb:
            others = holes_in(pb, *a["d_b"])
            hits = sum(1 for h in holes_in(pa, *a["d_a"]) if any(same_axis(h, o, a.get("tol", 0.3)) for o in others))
        c.add(f"holes line up {a['a']}–{a['b']}", hits >= a["count"], f"{hits} of {a['count']} lined up")
    if "center_distance" in spec:
        d = spec["center_distance"]
        pa, pb = matched.get(d["a"]), matched.get(d["b"])
        if pa and pb:
            got = _norm(_sub(pa["center"], pb["center"]))
            c.add(f"centre distance {d['a']}–{d['b']}", abs(got - d["value"]) <= d["tol"],
                  f"{n(got)} mm, wanted {n(d['value'])} ± {n(d['tol'])}")
        else:
            c.add(f"centre distance {d['a']}–{d['b']}", False, "part missing")
    if "height" in spec:
        lo, hi = spec["height"]
        got = measured.get("total_z")
        c.add(f"height {rng(lo, hi)} mm", got is not None and lo <= got <= hi, f"{n(got)} mm" if got else "nothing")
    if "total" in spec:
        t = spec["total"]
        got = measured.get("total")
        ok = bool(got) and all(abs(a - b) <= t.get("tol", 1) for a, b in zip(sorted(t["bbox"]), sorted(got),
                                                                              strict=True))
        c.add("total size", ok, f"{dims_text(got)} mm, wanted {dims_text(t['bbox'])}" if got else "nothing")
    if spec.get("iou_min"):
        iou = measured.get("iou")
        c.add("shape matches the reference (IoU)", (iou or 0) / spec["iou_min"],
              f"IoU {n(iou)} (target {n(spec['iou_min'])})" if iou is not None else "not measurable", kind="iou")
    if "flex" in spec:
        runs = measured.get("flex", [])
        bad_runs = [r for r in runs if not (r["ok"] and r["changed"])]
        detail = "; ".join(f"{r['param']} × {n(r['factor'])}: " + (r.get("error") or "no change")
                           for r in bad_runs)
        c.add("survives parameter changes", bool(runs) and not bad_runs, detail or
              ("parameters not found" if not runs else f"{len(runs)} changes rebuilt"))

    total = sum(i["weight"] for i in c.items)
    score = 100.0 * sum(i["credit"] * i["weight"] for i in c.items) / total if total else 0.0
    return {"score": round(score, 1), "checks": c.items,
            "matched": {k: (v["label"] if v else None) for k, v in matched.items()},
            "failed": [f"{i['name']}: {i['detail']}" for i in c.items if not i["ok"]]}
