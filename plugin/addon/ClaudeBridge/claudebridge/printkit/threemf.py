"""A small 3MF writer: one object per print item, coloured parts inside it. Standard library only.

Written in the layout OrcaSlicer and Anycubic Slicer Next read as parts with filaments. A file claiming to
come from Bambu Studio without a full project crashes Anycubic Slicer's command line, so it says FreeCAD; a
project file with only the colours crashes OrcaSlicer's, so there is none. The colours go in as the standard
base materials, and the guide lists which filament has which colour.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr

CONTENT = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/'
           'content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
           'relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing'
           '-3dmodel+xml"/><Default Extension="config" ContentType="text/xml"/></Types>')
RELS = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/'
        '3dmanufacturing/2013/01/3dmodel"/></Relationships>')


def write(path: Any, objects: list[tuple[str, list[tuple], tuple[float, float, float]]], colors: list[str]) -> None:
    """objects: (name, [(part name, filament from 1, vertices, triangles)], (dx, dy, dz)); colors: one per filament."""
    base = "".join(f'<base name={quoteattr(c)} displaycolor="{c.upper()}FF"/>' for c in colors)
    res: list[str] = [f'<basematerials id="1">{base}</basematerials>'] if colors else []
    items: list[str] = []
    cfg: list[str] = []
    oid = 2
    for name, parts, (dx, dy, dz) in objects:
        comps, part_cfg = [], []
        for part, filament, verts, tris in parts:
            color = f' pid="1" pindex="{min(filament, len(colors)) - 1}"' if colors else ""
            res.append(f'<object id="{oid}" type="model"{color}><mesh><vertices>')
            res += [f'<vertex x="{a + dx:.4f}" y="{b + dy:.4f}" z="{c + dz:.4f}"/>' for a, b, c in verts]
            res.append("</vertices><triangles>")
            res += [f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in tris]
            res.append("</triangles></mesh></object>")
            comps.append(f'<component objectid="{oid}"/>')
            part_cfg.append(f'<part id="{oid}" subtype="normal_part"><metadata key="name" value={quoteattr(part)}/>'
                            f'<metadata key="extruder" value="{filament}"/></part>')
            oid += 1
        res.append(f'<object id="{oid}" type="model"><components>{"".join(comps)}</components></object>')
        items.append(f'<item objectid="{oid}"/>')
        cfg.append(f'<object id="{oid}"><metadata key="name" value={quoteattr(name)}/>'
                   f'<metadata key="extruder" value="1"/>{"".join(part_cfg)}</object>')
        oid += 1
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.'
             'microsoft.com/3dmanufacturing/core/2015/02"><metadata name="Application">FreeCAD</metadata><resources>'
             + "".join(res) + "</resources><build>" + "".join(items) + "</build></model>")
    settings = '<?xml version="1.0" encoding="UTF-8"?>\n<config>' + "".join(cfg) + "</config>"
    target = Path(path)
    temp = target.with_suffix(".tmp")
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT)
        z.writestr("_rels/.rels", RELS)
        z.writestr("3D/3dmodel.model", model)
        z.writestr("Metadata/model_settings.config", settings)
    temp.replace(target)
