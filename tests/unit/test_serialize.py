import json
import math
from types import SimpleNamespace as NS

from claudebridge.bridge.serialize import num, shape_summary, to_jsonable


def test_num_six_significant_digits():
    assert num(7999.999999999998, "mm³") == "8000 mm³"
    assert num(3.14159265, "mm") == "3.14159 mm"
    assert num(0.000123456789) == "0.000123457"


def test_quantity_vector_placement():
    q = NS(Value=12.0, UserString="1.20 cm", getUserPreferred=lambda: ("1.20 cm", 10.0, "cm"))
    assert to_jsonable(q) == "1.2 cm"
    q = NS(Value=10.0, UserString="10 mm")  # no getUserPreferred: fall back to UserString
    v = NS(x=1.0, y=2.5, z=1 / 3)
    rot = NS(Axis=NS(x=0.0, y=0.0, z=1.0), Angle=math.pi / 2)
    pl = NS(Base=v, Rotation=rot)
    assert to_jsonable(q) == "10 mm"
    assert to_jsonable(v) == [1.0, 2.5, 0.333333]
    assert to_jsonable(pl) == {"base_mm": [1.0, 2.5, 0.333333], "axis": [0.0, 0.0, 1.0], "angle": "90 °"}


def test_long_lists_truncated_and_json_safe():
    out = to_jsonable(list(range(120)))
    assert len(out) == 51 and out[-1] == "… 70 more"
    json.dumps(to_jsonable({"a": object(), "b": b"xx", "c": {1, 2}}))


def test_shape_summary():
    bb = NS(XMin=0, YMin=0, ZMin=0, XMax=10, YMax=20, ZMax=30, XLength=10, YLength=20, ZLength=30)
    shape = NS(isNull=lambda: False, isValid=lambda: True, ShapeType="Solid", Solids=[1], Faces=[1] * 6,
               Edges=[1] * 12, Vertexes=[1] * 8, BoundBox=bb, Volume=6000.0, Area=2200.0)
    s = shape_summary(shape)
    assert s["volume"] == "6000 mm³" and s["area"] == "2200 mm²"
    assert s["faces"] == 6 and s["valid"] is True
    assert s["bbox"]["size"] == "10 mm × 20 mm × 30 mm"
