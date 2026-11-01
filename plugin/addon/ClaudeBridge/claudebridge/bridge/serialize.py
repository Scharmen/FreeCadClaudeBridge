"""Turn FreeCAD values into compact, JSON-safe data with units.

Duck-typed so it can be tested without FreeCAD: Quantity has `.Value` and
`.UserString`, Vector has `.x/.y/.z`, Placement has `.Base` and `.Rotation`.
"""

from __future__ import annotations

import json
import math
from typing import Any

MAX_LIST = 50
MAX_STR = 2000


def num(value: float, unit: str = "") -> str:
    """Six significant digits, with unit."""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        text = str(value)
    else:
        text = f"{value:.6g}"
    return f"{text} {unit}".strip()


def quantity(q: Any) -> str:
    """Quantity in the user's preferred unit with six significant digits.

    UserString follows the user's decimals setting ("12.00 mm"), so it's not used directly.
    """
    try:
        _, factor, unit = q.getUserPreferred()
        return num(q.Value / factor, unit)
    except Exception:
        return str(q.UserString)


def to_jsonable(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return repr(value)[:200]
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return float(f"{value:.6g}")
    if isinstance(value, str):
        return value if len(value) <= MAX_STR else value[:MAX_STR] + "…"
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, dict):
        return {str(k): to_jsonable(v, depth + 1) for k, v in list(value.items())[:MAX_LIST]}
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        out = [to_jsonable(v, depth + 1) for v in items[:MAX_LIST]]
        if len(items) > MAX_LIST:
            out.append(f"… {len(items) - MAX_LIST} more")
        return out
    if hasattr(value, "UserString") and hasattr(value, "Value"):  # Quantity
        return quantity(value)
    if all(hasattr(value, a) for a in ("x", "y", "z")) and not hasattr(value, "Base"):  # Vector
        return [float(f"{value.x:.6g}"), float(f"{value.y:.6g}"), float(f"{value.z:.6g}")]
    if hasattr(value, "Base") and hasattr(value, "Rotation"):  # Placement
        rot = value.Rotation
        out = {"base_mm": to_jsonable(value.Base, depth + 1)}
        if hasattr(rot, "Axis") and hasattr(rot, "Angle"):
            out["axis"] = to_jsonable(rot.Axis, depth + 1)
            out["angle"] = num(math.degrees(rot.Angle), "°")
        return out
    if hasattr(value, "Name") and hasattr(value, "TypeId") and hasattr(value, "Document"):  # DocumentObject
        return {"object": value.Name}
    if hasattr(value, "XMin") and hasattr(value, "ZMax"):  # BoundBox
        return bbox(value)
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)[:200]


def bbox(b: Any) -> dict[str, Any]:
    return {
        "min_mm": [float(f"{b.XMin:.6g}"), float(f"{b.YMin:.6g}"), float(f"{b.ZMin:.6g}")],
        "max_mm": [float(f"{b.XMax:.6g}"), float(f"{b.YMax:.6g}"), float(f"{b.ZMax:.6g}")],
        "size": num(b.XLength, "mm") + " × " + num(b.YLength, "mm") + " × " + num(b.ZLength, "mm"),
    }


def shape_summary(shape: Any) -> dict[str, Any]:
    if shape is None or getattr(shape, "isNull", lambda: True)():
        return {"null": True}
    out: dict[str, Any] = {
        "type": getattr(shape, "ShapeType", "?"),
        "valid": bool(shape.isValid()),
        "solids": len(shape.Solids),
        "faces": len(shape.Faces),
        "edges": len(shape.Edges),
        "vertices": len(shape.Vertexes),
        "bbox": bbox(shape.BoundBox),
    }
    if shape.Solids:
        out["volume"] = num(shape.Volume, "mm³")
    if shape.Faces:
        out["area"] = num(shape.Area, "mm²")
    return out


def result_value(value: Any) -> Any:
    """`_result` of run_python: JSON if possible, else the converted form."""
    return to_jsonable(value)
