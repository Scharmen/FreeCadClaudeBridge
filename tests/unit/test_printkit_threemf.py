"""The 3MF writer: slicers need one object with coloured parts and exactly this structure."""

import re
import zipfile

from claudebridge.printkit import threemf

TRI = ([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)], [(0, 2, 1), (0, 1, 3), (1, 2, 3), (2, 0, 3)])


def test_parts_keep_their_filament_and_colours(tmp_path):
    path = tmp_path / "a.3mf"
    threemf.write(path, [("Car", [("Green", 1, *TRI), ("Red", 2, *TRI)], (10, 20, 0))], ["#00ff00", "#ff0000"])
    z = zipfile.ZipFile(path)
    assert z.testzip() is None
    model = z.read("3D/3dmodel.model").decode()
    assert model.count("<mesh>") == 2 and model.count("<component ") == 2 and model.count("<item ") == 1
    assert 'x="11.0000"' in model, "the offset is written into the vertices"
    assert 'name="Application">FreeCAD' in model, "the slicers crash on a file that claims to be from Bambu Studio"
    cfg = z.read("Metadata/model_settings.config").decode()
    assert re.findall(r'key="extruder" value="(\d)"/></part>', cfg) == ["1", "2"]
    assert "Metadata/project_settings.config" not in z.namelist(), "OrcaSlicer crashes on a partial project"
    assert 'displaycolor="#FF0000FF"' in model and 'pindex="1"' in model


def test_two_objects_on_a_plate(tmp_path):
    path = tmp_path / "b.3mf"
    threemf.write(path, [("A", [("x", 1, *TRI)], (0, 0, 0)), ("B", [("y", 1, *TRI)], (5, 0, 0))], ["#000000"])
    assert zipfile.ZipFile(path).read("3D/3dmodel.model").decode().count("<item ") == 2


def test_names_are_escaped(tmp_path):
    path = tmp_path / "c.3mf"
    threemf.write(path, [('A&"B', [("<x>", 1, *TRI)], (0, 0, 0))], ["#000000"])
    cfg = zipfile.ZipFile(path).read("Metadata/model_settings.config").decode()
    assert "&amp;" in cfg and "&lt;x&gt;" in cfg
