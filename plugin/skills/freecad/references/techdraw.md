# TechDraw (FreeCAD 1.1)

Tested by `tests/gui/test_references.py` (needs the GUI for PDF export). A page has a template; views reference 3D objects; dimensions reference view edges as `Edge0, Edge1, …` (0-based, unlike 3D `Edge1`). View geometry is computed in a background thread: after `doc.recompute()`, process events until `view.getVisibleEdges()` is non-empty before referencing edges.

Page with an ISO A4 template, a three-view projection group and a dimension, exported as PDF.

```python
import os, tempfile, time, TechDrawGui
from PySide import QtWidgets
box = doc.addObject("Part::Box", "Part"); box.Length, box.Width, box.Height = 60, 40, 20
doc.recompute()
page = doc.addObject("TechDraw::DrawPage", "Page")
tpl = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
tpl.Template = App.getResourceDir() + "Mod/TechDraw/Templates/ISO/A4_Landscape_blank.svg"
page.Template = tpl
group = doc.addObject("TechDraw::DrawProjGroup", "Views")
page.addView(group)
group.Source = [box]
group.addProjection("Front")
group.addProjection("Top")
group.addProjection("Right")
group.Scale = 1.5
doc.recompute()
front = next(v for v in group.Views if v.Type == "Front")
t0 = time.time()  # hidden-line removal runs in a thread; its result arrives via the event loop
while not front.getVisibleEdges() and time.time() - t0 < 10:
    QtWidgets.QApplication.processEvents()
    time.sleep(0.05)
edges = front.getVisibleEdges()  # index i is the dimension reference "Edge<i>"
horizontal = next(f"Edge{i}" for i, e in enumerate(edges)
                  if abs(e.Vertexes[0].Point.y - e.Vertexes[-1].Point.y) < 1e-6 and e.Length > 1)
dim = doc.addObject("TechDraw::DrawViewDimension", "Width")
dim.Type = "DistanceX"
dim.References2D = [(front, horizontal)]
page.addView(dim)
doc.recompute()
pdf = os.path.join(tempfile.mkdtemp(), "drawing.pdf")
TechDrawGui.exportPageAsPdf(page, pdf)
assert os.path.getsize(pdf) > 1000
assert abs(dim.getRawValue() - 60) < 1e-6
```

Templates live in `App.getResourceDir() + "Mod/TechDraw/Templates/"` (ISO: `ISO/A4_Landscape_TD.svg`, `ISO/A4_Landscape_ISO5457_minimal.svg`, …). Editable title-block fields are in `tpl.EditableTexts` (a dict); set it as a whole: `t = tpl.EditableTexts; t["DrawingTitle"] = "Bracket"; tpl.EditableTexts = t` (field names depend on the template).

SVG export works the same with `TechDrawGui.exportPageAsSvg(page, path)`.
