"""Drag and drop into the chat: what each dropped thing becomes."""

import base64

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
    assert dropping.classify(str(tmp_path / "notes.txt")) == "text"
    assert dropping.classify(str(tmp_path)) == "folder"


def test_reference_lines():
    assert dropping.reference_line("cad", "/x/p.step") == "[CAD file: /x/p.step]"
    line = dropping.object_reference("Flange", "Pad001", "PartDesign::Pad", (80, 80, 12.5), ["Face3"])
    assert line == "[Object: Flange (Pad001), PartDesign::Pad, 80 × 80 × 12.5 mm, elements Face3]"
    assert dropping.object_reference("Sheet", "Spreadsheet", "Spreadsheet::Sheet", None) == \
        "[Object: Sheet (Spreadsheet), Spreadsheet::Sheet]"


def _files(tmp_path):
    (tmp_path / "pic.png").write_bytes(b"\x89PNGdata")
    (tmp_path / "sheet.pdf").write_bytes(b"%PDF-1.4 data")
    (tmp_path / "dims.csv").write_text("name;mm\nwidth;73\n", encoding="utf-8")
    (tmp_path / "part.step").write_text("ISO-10303-21;")
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02\xff")
    (tmp_path / "long.txt").write_text("x" * (dropping.MAX_TEXT_BYTES + 1))
    (tmp_path / "big.pdf").write_bytes(b"%PDF" + b"0" * dropping.MAX_PDF_BYTES)
    names = ("pic.png", "sheet.pdf", "dims.csv", "part.step", "blob.bin", "long.txt", "big.pdf")
    return [dropping.attachment(str(tmp_path / n)) for n in names] + [dropping.attachment(str(tmp_path))]


def test_attachment_kinds(tmp_path):
    kinds = [a["kind"] for a in _files(tmp_path)]
    assert kinds == ["image", "pdf", "text", "cad", "file", "file", "file", "folder"]
    pic, pdf, csv = _files(tmp_path)[:3]
    assert pic["data"] == b"\x89PNGdata" and pic["media_type"] == "image/png" and pic["name"] == "pic.png"
    assert pdf["data"].startswith(b"%PDF") and csv["data"] == b"name;mm\nwidth;73\n"


def test_uploaded_files_reach_claude_as_content(tmp_path):
    """A picture, a PDF and a text file are sent with their content, not as a path to look up."""
    atts = _files(tmp_path)
    blocks = dropping.content_blocks(atts)
    assert [b["type"] for b in blocks] == ["image", "document", "text"]
    assert base64.b64decode(blocks[0]["source"]["data"]) == b"\x89PNGdata"
    doc = blocks[1]
    assert doc["source"]["media_type"] == "application/pdf" and doc["title"] == "sheet.pdf"
    assert base64.b64decode(doc["source"]["data"]).startswith(b"%PDF")
    text = blocks[2]["text"]
    assert "dims.csv" in text and "width;73" in text and str(tmp_path / "dims.csv") in text


def test_other_files_are_named_by_path(tmp_path):
    """What cannot be sent as content (CAD, binary, too big, folders) stays a reference Claude opens itself."""
    lines = dropping.reference_lines(_files(tmp_path))
    assert lines[0] == f"[CAD file: {tmp_path / 'part.step'}]"
    assert [ln.split(":")[0] for ln in lines] == ["[CAD file", "[File", "[File", "[File", "[Folder"]
    assert dropping.reference_lines(_files(tmp_path)[:3]) == []


def test_text_must_be_text(tmp_path):
    latin = tmp_path / "old.txt"
    latin.write_bytes("Maß 12 mm".encode("latin-1"))
    assert dropping.attachment(str(latin))["kind"] == "file", "not UTF-8: Claude reads it by path"
    gone = dropping.attachment(str(tmp_path / "missing.txt"))
    assert gone["kind"] == "file" and "data" not in gone


def test_chip_text():
    assert dropping.chip_text({"kind": "pdf", "name": "sheet.pdf"}) == "📄 sheet.pdf"
    assert dropping.chip_text({"kind": "folder", "name": "parts"}) == "📁 parts"
    assert dropping.chip_text({"kind": "image", "name": "a" * 40}).endswith("…")
    assert "path" in dropping.chip_tip({"kind": "file", "name": "blob.bin"})
    assert "content" in dropping.chip_tip({"kind": "text", "name": "a.txt"})
