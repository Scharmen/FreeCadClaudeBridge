"""Drag and drop into the chat: what each dropped thing becomes."""

from claudebridge.panel import dropping


def test_classify(tmp_path):
    img = tmp_path / "a.PNG"
    img.write_bytes(b"\x89PNG" + b"0" * 100)
    big = tmp_path / "huge.jpg"
    big.write_bytes(b"0" * (dropping.MAX_IMAGE_BYTES + 1))
    (tmp_path / "part.STEP").write_text("x")
    (tmp_path / "notes.txt").write_text("x")
    assert dropping.classify(str(img)) == "image" and dropping.media_type(str(img)) == "image/png"
    assert dropping.classify(str(big)) == "file", "too large to send as an image"
    assert dropping.classify(str(tmp_path / "part.STEP")) == "cad"
    assert dropping.classify(str(tmp_path / "notes.txt")) == "file"
    assert dropping.classify(str(tmp_path)) == "folder"


def test_reference_lines():
    assert dropping.reference_line("cad", "/x/p.step") == "[CAD file: /x/p.step]"
    line = dropping.object_reference("Flange", "Pad001", "PartDesign::Pad", (80, 80, 12.5), ["Face3"])
    assert line == "[Object: Flange (Pad001), PartDesign::Pad, 80 × 80 × 12.5 mm, elements Face3]"
    assert dropping.object_reference("Sheet", "Spreadsheet", "Spreadsheet::Sheet", None) == \
        "[Object: Sheet (Spreadsheet), Spreadsheet::Sheet]"
