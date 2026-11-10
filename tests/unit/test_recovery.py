"""Finding the newest safety copy that still holds a part that went missing from the model."""

import os
import time
import zipfile

from claudebridge.bridge import recovery


def fcstd(path, names):
    objects = "".join(f'<Object type="App::DocumentObjectGroup" name="{n}" id="{i}"/>' for i, n in enumerate(names))
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Document.xml", f'<?xml version="1.0"?><Document><Objects Count="{len(names)}">{objects}'
                                   "</Objects></Document>")


def test_the_newest_copy_with_the_part_is_found(tmp_path):
    old, mid, new = tmp_path / "20261108-140421.FCStd", tmp_path / "20261109-144342.FCStd", \
        tmp_path / "20261109-160000.FCStd"
    fcstd(old, ["Board", "Connector_Test"])
    fcstd(mid, ["Board", "Connector_Test", "Clip_0_10"])
    fcstd(new, ["Board"])                                   # saved after the part was gone
    (tmp_path / "broken.FCStd").write_text("not a zip")
    for i, p in enumerate((old, mid, new)):
        os.utime(p, (time.time() - 100 + i, time.time() - 100 + i))
    assert recovery.newest_copy_with(str(tmp_path), "Connector_Test") == str(mid)
    assert recovery.newest_copy_with(str(tmp_path), "Nowhere") is None
    assert recovery.newest_copy_with(str(tmp_path / "missing"), "Connector_Test") is None


def test_a_copy_is_named_by_its_time():
    assert recovery.copy_time("/s/20261109-144342.FCStd") == "9 Nov 14:43"
    assert recovery.copy_time("/s/odd.FCStd") == "odd"
