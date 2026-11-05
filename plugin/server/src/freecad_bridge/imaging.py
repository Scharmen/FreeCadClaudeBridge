"""Image to 3D, server side (local, no network): outlines and height grids from a picture.

- outline_profile: the picture's shapes as closed polygons in mm (outer loops with their
  holes), found with marching squares on a thresholded image and simplified.
- height_grid: brightness as a height field in mm (dark = high by default).
- preview_png: a small PNG so Claude can look at the picture.

The FreeCAD side (extrude, revolve, mesh, image plane) is generated in mcp_server.
"""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

MAX_SIDE_OUTLINE = 700
MAX_SIDE_RELIEF = 400


class ImageError(Exception):
    pass


def _open(path: str | Path) -> Image.Image:
    p = Path(path).expanduser()
    if not p.is_file():
        raise ImageError(f"image not found: {p}")
    try:
        img = Image.open(p)
        img.load()
    except (UnidentifiedImageError, OSError) as e:
        raise ImageError(f"cannot read {p} as an image: {e}") from e
    return ImageOps.exif_transpose(img)


def _fit(img: Image.Image, max_side: int) -> Image.Image:
    scale = max_side / max(img.size)
    if scale < 1:
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    return img


def _gray(img: Image.Image) -> np.ndarray:
    """0 = black … 1 = white; transparent pixels count as white background."""
    rgba = np.asarray(img.convert("RGBA"), dtype=np.float32) / 255.0
    lum = 0.299 * rgba[..., 0] + 0.587 * rgba[..., 1] + 0.114 * rgba[..., 2]
    return lum * rgba[..., 3] + (1.0 - rgba[..., 3])


def otsu(values: np.ndarray) -> float:
    hist, edges = np.histogram(values, bins=256, range=(0.0, 1.0))
    w = hist.cumsum().astype(np.float64)
    m = (hist * (edges[:-1] + 0.5 / 256)).cumsum()
    total, mean = w[-1], m[-1]
    w1 = total - w
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mean * w - m * total) ** 2 / (w * w1)
    between[~np.isfinite(between)] = 0
    return float(edges[int(np.argmax(between)) + 1])


def foreground_mask(img: Image.Image, threshold: float | None = None, invert: bool = False) -> np.ndarray:
    """The picture's object: opaque pixels if it has transparency, else the darker side of the threshold."""
    if img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info:
        alpha = np.asarray(img.convert("RGBA"), dtype=np.float32)[..., 3] / 255.0
        if alpha.min() < 0.5 < alpha.max():
            mask = alpha > 0.5
            return ~mask if invert else mask
    gray = _gray(img)
    t = otsu(gray) if threshold is None else threshold
    mask = gray < t
    return ~mask if invert else mask


# marching squares: corner bits TL=8, TR=4, BR=2, BL=1; edges T, R, B, L
_SEGMENTS = {1: [("L", "B")], 2: [("B", "R")], 3: [("L", "R")], 4: [("T", "R")], 5: [("L", "B"), ("T", "R")],
             6: [("T", "B")], 7: [("T", "L")], 8: [("L", "T")], 9: [("T", "B")], 10: [("L", "T"), ("B", "R")],
             11: [("T", "R")], 12: [("L", "R")], 13: [("B", "R")], 14: [("L", "B")]}


def contours(mask: np.ndarray) -> list[list[tuple[float, float]]]:
    """Closed boundary loops of a boolean mask, in pixel coordinates (x right, y down)."""
    m = np.pad(mask.astype(np.uint8), 1)
    code = (m[:-1, :-1] << 3) | (m[:-1, 1:] << 2) | (m[1:, 1:] << 1) | m[1:, :-1]
    links: dict[tuple[float, float], list[tuple[float, float]]] = {}

    def edge_point(e: str, i: int, j: int) -> tuple[float, float]:
        return {"T": (j + 0.5, i), "R": (j + 1.0, i + 0.5), "B": (j + 0.5, i + 1.0), "L": (j, i + 0.5)}[e]

    for i, j in zip(*np.nonzero((code > 0) & (code < 15)), strict=True):
        for a, b in _SEGMENTS[int(code[i, j])]:
            pa, pb = edge_point(a, i, j), edge_point(b, i, j)
            links.setdefault(pa, []).append(pb)
            links.setdefault(pb, []).append(pa)
    loops, seen = [], set()
    for start in links:
        if start in seen:
            continue
        loop, prev, cur = [start], None, start
        seen.add(start)
        while True:
            nxt = next((q for q in links[cur] if q != prev and (q not in seen or q == start)), None)
            if nxt is None or nxt == start:
                break
            loop.append(nxt)
            seen.add(nxt)
            prev, cur = cur, nxt
        if len(loop) >= 4:
            loops.append(loop)
    return loops


def _rdp(points: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    if len(points) < 3:
        return points
    a, b = np.array(points[0]), np.array(points[-1])
    ab = b - a
    norm = math.hypot(*ab)
    pts = np.array(points[1:-1])
    if norm == 0:
        d = np.hypot(*(pts - a).T)
    else:
        d = np.abs(ab[0] * (pts[:, 1] - a[1]) - ab[1] * (pts[:, 0] - a[0])) / norm
    k = int(np.argmax(d))
    if d[k] <= tol:
        return [points[0], points[-1]]
    left = _rdp(points[: k + 2], tol)
    return left[:-1] + _rdp(points[k + 1:], tol)


def simplify_loop(loop: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Douglas-Peucker on a closed loop: split at the point farthest from the start."""
    far = max(range(len(loop)), key=lambda k: (loop[k][0] - loop[0][0]) ** 2 + (loop[k][1] - loop[0][1]) ** 2)
    first = _rdp(loop[: far + 1], tol)
    second = _rdp(loop[far:] + [loop[0]], tol)
    out = first[:-1] + second[:-1]
    return out if len(out) >= 3 else loop


def area(loop: list[tuple[float, float]]) -> float:
    return 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(loop, loop[1:] + loop[:1], strict=True))


def _inside(pt: tuple[float, float], loop: list[tuple[float, float]]) -> bool:
    x, y, hit = pt[0], pt[1], False
    for (x0, y0), (x1, y1) in zip(loop, loop[1:] + loop[:1], strict=True):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            hit = not hit
    return hit


def nest(loops: list[list[tuple[float, float]]]) -> list[dict[str, Any]]:
    """Group loops into shapes: even nesting depth = outer boundary, odd = hole of its parent."""
    order = sorted(range(len(loops)), key=lambda k: -abs(area(loops[k])))
    depth, parent = {}, {}
    for idx, k in enumerate(order):
        probe = loops[k][0]
        containers = [c for c in order[:idx] if _inside(probe, loops[c])]
        depth[k] = len(containers)
        parent[k] = containers[-1] if containers else None
    shapes = {k: {"outer": loops[k], "holes": []} for k in order if depth[k] % 2 == 0}
    for k in order:
        if depth[k] % 2 == 1 and parent[k] in shapes:
            shapes[parent[k]]["holes"].append(loops[k])
    return [shapes[k] for k in order if k in shapes]


def outline_profile(path: str | Path, size_mm: float, fit: str = "width", threshold: float | None = None,
                    invert: bool = False, detail: float = 0.7) -> dict[str, Any]:
    """Shapes of the picture in mm (origin bottom-left, y up), scaled so the picture is size_mm wide (or high)."""
    img = _fit(_open(path), MAX_SIDE_OUTLINE)
    mask = foreground_mask(img, threshold, invert)
    if not mask.any() or mask.all():
        raise ImageError("no shape found: the picture is empty after thresholding; try invert or threshold")
    h, w = mask.shape
    min_area = max(6.0, 0.0004 * w * h)
    tol = 0.4 + (1.0 - max(0.0, min(1.0, detail))) * 2.6
    loops = [simplify_loop(lp, tol) for lp in contours(mask) if abs(area(lp)) >= min_area]
    scale = size_mm / (w if fit == "width" else h)

    def to_mm(loop: list[tuple[float, float]]) -> list[list[float]]:
        return [[round((x - 0.5) * scale, 4), round((h - (y - 0.5)) * scale, 4)] for x, y in loop]

    shapes = [{"outer": to_mm(s["outer"]), "holes": [to_mm(hl) for hl in s["holes"]]} for s in nest(loops)]
    return {"shapes": shapes, "width_mm": round(w * scale, 3), "height_mm": round(h * scale, 3),
            "pixels": [w, h], "points": sum(len(s["outer"]) + sum(map(len, s["holes"])) for s in shapes)}


def height_grid(path: str | Path, width_mm: float, max_height_mm: float = 3.0, base_mm: float = 1.0,
                resolution: int = 200, invert: bool = False) -> dict[str, Any]:
    """Brightness as heights in mm on a regular grid (row 0 = top of the picture); dark = high unless invert."""
    img = _open(path)
    res = max(8, min(int(resolution), MAX_SIDE_RELIEF))
    img = img.resize((res, max(2, round(res * img.height / img.width))) if img.width >= img.height
                     else (max(2, round(res * img.width / img.height)), res), Image.LANCZOS)
    gray = _gray(img)
    rel = gray if invert else 1.0 - gray
    heights = base_mm + rel * max_height_mm
    ny, nx = heights.shape
    return {"nx": nx, "ny": ny, "dx": round(width_mm / (nx - 1), 6),
            "heights": [round(float(v), 3) for v in heights.ravel()],
            "width_mm": width_mm, "depth_mm": round(width_mm / (nx - 1) * (ny - 1), 3)}


def preview_png(path: str | Path, max_side: int = 1024) -> bytes:
    img = _fit(_open(path), max_side).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def side_by_side(picture: str | Path, shot_png: bytes, label: str, height: int = 600) -> bytes:
    """The user's picture next to a screenshot of the model, both `height` pixels high, labelled."""
    left = _open(picture).convert("RGB")
    right = Image.open(io.BytesIO(shot_png)).convert("RGB")
    left = left.resize((max(1, round(left.width * height / left.height)), height))
    right = right.resize((max(1, round(right.width * height / right.height)), height))
    bar, gap = 28, 12
    out = Image.new("RGB", (left.width + gap + right.width, height + bar), "white")
    out.paste(left, (0, bar))
    out.paste(right, (left.width + gap, bar))
    draw = ImageDraw.Draw(out)
    draw.text((6, 8), "Picture", fill="black")
    draw.text((left.width + gap + 6, 8), label, fill="black")
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ---------- labelled views and sections

def _rotate(axis: np.ndarray, angle: float, vec: np.ndarray) -> np.ndarray:
    return (vec * math.cos(angle) + np.cross(axis, vec) * math.sin(angle)
            + axis * (axis @ vec) * (1 - math.cos(angle)))


def parse_camera(text: str) -> dict[str, Any]:
    """Size of the view in mm and the world axes as seen on screen, from FreeCAD's camera description."""
    out: dict[str, Any] = {"height": None, "right": None, "up": None}
    for line in text.splitlines():
        parts = line.split()
        if parts[:1] == ["height"] and len(parts) == 2:
            out["height"] = float(parts[1])
        elif parts[:1] == ["orientation"] and len(parts) == 5:
            axis = np.array([float(x) for x in parts[1:4]])
            axis /= max(float(np.linalg.norm(axis)), 1e-12)
            angle = float(parts[4])
            out["right"] = _rotate(axis, angle, np.array([1.0, 0, 0]))
            out["up"] = _rotate(axis, angle, np.array([0, 1.0, 0]))
    return out


def nice_length(mm: float) -> float:
    """A round length (1, 2 or 5 × 10^n) no longer than mm."""
    if mm <= 0:
        return 1.0
    base = 10 ** math.floor(math.log10(mm))
    return next(m * base for m in (5, 2, 1) if m * base <= mm)


def _scale_bar(draw: ImageDraw.ImageDraw, width: int, height: int, mm_per_px: float) -> None:
    bar = nice_length(width * 0.25 * mm_per_px)
    px = bar / mm_per_px
    x1, y = width - 16, height - 16
    draw.line([(x1 - px, y), (x1, y)], fill=(0, 0, 0), width=3)
    draw.line([(x1 - px, y - 5), (x1 - px, y + 5)], fill=(0, 0, 0), width=2)
    draw.line([(x1, y - 5), (x1, y + 5)], fill=(0, 0, 0), width=2)
    draw.text((x1 - px, y - 20), f"{bar:g} mm", fill=(0, 0, 0))


AXIS_COLORS = {"X": (200, 30, 30), "Y": (30, 150, 30), "Z": (30, 60, 200)}


def _axes(draw: ImageDraw.ImageDraw, height: int, right: np.ndarray, up: np.ndarray) -> None:
    ox, oy, size = 42, height - 42, 28
    for i, name in enumerate("XYZ"):
        dx, dy = float(right[i]) * size, -float(up[i]) * size
        if abs(dx) + abs(dy) < 2:
            draw.text((ox + 4, oy + 4 + 12 * i), f"{name} (towards you or away)", fill=AXIS_COLORS[name])
            continue
        draw.line([(ox, oy), (ox + dx, oy + dy)], fill=AXIS_COLORS[name], width=2)
        draw.text((ox + dx * 1.2 - 3, oy + dy * 1.2 - 5), name, fill=AXIS_COLORS[name])


def view_sheet(shots: list[dict[str, Any]], title: str = "", columns: int = 2) -> bytes:
    """Screenshots in a grid, each with its name, the axes as seen and a scale bar; the size on top."""
    images = []
    for s in shots:
        im = Image.open(io.BytesIO(s["png"])).convert("RGB")
        draw = ImageDraw.Draw(im)
        draw.text((10, 8), s["label"], fill=(0, 0, 0))
        cam = parse_camera(s.get("camera", ""))
        if cam["height"]:
            _scale_bar(draw, im.width, im.height, cam["height"] / im.height)
        if cam["right"] is not None:
            _axes(draw, im.height, cam["right"], cam["up"])
        draw.rectangle([0, 0, im.width - 1, im.height - 1], outline=(190, 190, 190))
        images.append(im)
    rows = math.ceil(len(images) / columns)
    w, h = images[0].size
    top = 28 if title else 0
    sheet = Image.new("RGB", (w * columns, h * rows + top), "white")
    if title:
        ImageDraw.Draw(sheet).text((10, 8), title, fill=(0, 0, 0))
    for i, im in enumerate(images):
        sheet.paste(im, ((i % columns) * w, top + (i // columns) * h))
    buf = io.BytesIO()
    sheet.save(buf, "PNG")
    return buf.getvalue()


def section_image(sections: list[tuple[str, np.ndarray]], axis: str, offset: float, size: int = 800) -> bytes:
    """Cross-sections as filled shapes with a scale bar: what is material, how thick the walls are."""
    used = [(n, s) for n, s in sections if len(s)]
    names = [c for c in "XYZ" if c != axis.upper()]
    im = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(im)
    label = f"Section at {axis.upper()} = {offset:g} mm, seen with {names[0]} to the right and {names[1]} up"
    if not used:
        draw.text((10, 8), label + ": the plane misses the model", fill=(0, 0, 0))
    else:
        pts = np.vstack([s.reshape(-1, 2) for _, s in used])
        lo, hi = pts.min(0), pts.max(0)
        mm_per_px = max(float((hi - lo).max()) / (size - 90), 1e-6)
        ox = (size - (hi[0] - lo[0]) / mm_per_px) / 2
        oy = (size - (hi[1] - lo[1]) / mm_per_px) / 2

        def to_px(p: np.ndarray) -> np.ndarray:
            return np.c_[ox + (p[..., 0].ravel() - lo[0]) / mm_per_px,
                         size - oy - (p[..., 1].ravel() - lo[1]) / mm_per_px]

        fills = [(150, 175, 205), (205, 170, 140), (160, 200, 160), (200, 170, 200)]
        for i, (_, seg) in enumerate(used):
            px = to_px(seg).reshape(-1, 2, 2)
            y0, y1 = px[:, :, 1].min(1), px[:, :, 1].max(1)
            for row in range(max(0, int(y0.min())), min(size, int(y1.max()) + 1)):
                y = row + 0.5
                hit = (y0 <= y) & (y1 > y)
                if not hit.any():
                    continue
                a, b = px[hit, 0], px[hit, 1]
                xs = np.sort(a[:, 0] + (y - a[:, 1]) / (b[:, 1] - a[:, 1]) * (b[:, 0] - a[:, 0]))
                for x0, x1 in zip(xs[::2], xs[1::2], strict=False):   # inside between odd and even crossings
                    draw.line([(x0, row), (x1, row)], fill=fills[i % len(fills)])
            for (ax, ay), (bx, by) in px:
                draw.line([(ax, ay), (bx, by)], fill=(20, 20, 20))
        _scale_bar(draw, size, size, mm_per_px)
        draw.text((10, 8), label, fill=(0, 0, 0))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()
