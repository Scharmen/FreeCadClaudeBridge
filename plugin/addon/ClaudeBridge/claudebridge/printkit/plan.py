# ruff: noqa: E501
"""What the print export decides before any geometry: printers, colours, moving parts, layout, order, texts.

Standard library only, so it can be tested without FreeCAD.
"""

from __future__ import annotations

import math
from typing import Any

PRINTERS: dict[str, dict[str, Any]] = {
    "Anycubic Kobra S1": {"bed": [250, 250, 250], "nozzle": 0.4, "slots": 4, "process": "fdm"},
    "Bambu Lab A1 mini": {"bed": [180, 180, 180], "nozzle": 0.4, "slots": 4, "process": "fdm"},
    "Bambu Lab P1S / X1C": {"bed": [256, 256, 256], "nozzle": 0.4, "slots": 4, "process": "fdm"},
    "Prusa MK4": {"bed": [250, 210, 220], "nozzle": 0.4, "slots": 1, "process": "fdm"},
    "Creality Ender 3": {"bed": [220, 220, 250], "nozzle": 0.4, "slots": 1, "process": "fdm"},
    "Elegoo Mars (resin)": {"bed": [143, 89, 175], "nozzle": 0.05, "slots": 1, "process": "resin"},
}
CUSTOM = "custom"
HINTS = ("wheel", "tyre", "tire", "rim", "rad", "reifen", "felge", "roller", "rolle", "pulley", "gear", "zahnrad",
         "knob", "knopf", "drehknopf", "fan", "propeller", "rotor")
NAMED = [("#000000", "Schwarz", "Black"), ("#ffffff", "Weiss", "White"), ("#808080", "Grau", "Grey"),
         ("#b4b6ba", "Silber", "Silver"), ("#c8141a", "Rot", "Red"), ("#6e0a0e", "Dunkelrot", "Dark red"),
         ("#0a5a2b", "Gruen", "Green"), ("#22b14c", "Hellgruen", "Light green"), ("#1450c8", "Blau", "Blue"),
         ("#7ec8f0", "Hellblau", "Light blue"), ("#f0d214", "Gelb", "Yellow"), ("#f07814", "Orange", "Orange"),
         ("#8c5a28", "Braun", "Brown"), ("#d264c8", "Pink", "Pink"), ("#6e28b4", "Lila", "Purple")]
SAME_COLOR = 40.0


def limits(printer: dict[str, Any]) -> dict[str, float]:
    """Sizes that depend on the process: fits are wider on a filament printer than on resin."""
    if printer.get("process") == "resin":
        return {"dowel_d": 2.0, "hole_gap": 0.1, "free_gap": 0.2, "press_gap": 0.03, "axle_d": 2.5, "min_thick": 0.5,
                "dowel_depth": 3.0, "wall": 0.6}
    nozzle = float(printer.get("nozzle", 0.4))
    return {"dowel_d": max(2.0, 5 * nozzle), "hole_gap": 0.2, "free_gap": 0.4, "press_gap": 0.1,
            "axle_d": max(3.0, 7.5 * nozzle), "min_thick": 2 * nozzle, "dowel_depth": 3.0, "wall": 2 * nozzle}


def _rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _distance(a: str, b: str) -> float:
    return math.dist(_rgb(a), _rgb(b))


def color_name(color: str, lang: str = "de") -> str:
    best = min(NAMED, key=lambda row: _distance(row[0], color))
    return best[1] if lang == "de" else best[2]


def group_colors(colors: dict[str, str]) -> dict[str, str]:
    """Object to colour group. Colours closer than a filament difference are one group."""
    groups: list[str] = []
    out = {}
    for name, color in colors.items():
        hit = next((g for g in groups if _distance(g, color) < SAME_COLOR), None)
        if hit is None:
            groups.append(color)
            hit = color
        out[name] = hit
    return out


def assign_slots(colors: dict[str, str], slots: int) -> dict[str, int]:
    """Object to filament number from 1. With more colours than slots the closest colours share one."""
    groups = group_colors(colors)
    clusters = [[c] for c in dict.fromkeys(groups.values())]
    while len(clusters) > max(1, slots):
        pairs = [(min(_distance(a, b) for a in clusters[i] for b in clusters[j]), i, j)
                 for i in range(len(clusters)) for j in range(i + 1, len(clusters))]
        _, i, j = min(pairs)
        clusters[i] += clusters.pop(j)
    number = {c: i + 1 for i, cl in enumerate(clusters) for c in cl}
    return {name: number[g] for name, g in groups.items()}


def slot_colors(colors: dict[str, str], slots: dict[str, int]) -> list[str]:
    """The colour of each filament: the first one that uses it."""
    out: dict[int, str] = {}
    for name, number in slots.items():
        out.setdefault(number, colors[name])
    return [out[i] for i in sorted(out)]


def _round_axis(o: dict[str, Any]) -> tuple[str, float, float, float, float] | None:
    """(axis, first centre, second centre, diameter, length) when the bounding box is round about one axis."""
    size = [h - a for a, h in zip(o["lo"], o["hi"], strict=True)]
    mid = [(a + h) / 2 for a, h in zip(o["lo"], o["hi"], strict=True)]
    for i, axis in enumerate("xyz"):
        a, b = [k for k in range(3) if k != i]
        if size[a] > 0 and abs(size[a] - size[b]) <= 0.04 * max(size[a], size[b]):
            return axis, mid[a], mid[b], size[a], size[i]
    return None


def _along(o: dict[str, Any], axis: str) -> tuple[float, float]:
    i = "xyz".index(axis)
    return o["lo"][i], o["hi"][i]


def detect_moving(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Things that should turn: round parts that share an axis, named like a wheel or a knob, or found in pairs."""
    extent = max((max(h - a for a, h in zip(o["lo"], o["hi"], strict=True)) for o in objects), default=0.0)
    round_parts = [(o, r) for o in objects if (r := _round_axis(o)) and r[3] < 0.6 * extent]
    lines: list[list[tuple[dict[str, Any], tuple]]] = []
    for item in sorted(round_parts, key=lambda it: -it[1][3]):
        for line in lines:
            big = line[0][1]
            if big[0] == item[1][0] and math.hypot(big[1] - item[1][1], big[2] - item[1][2]) <= 0.05 * big[3]:
                line.append(item)
                break
        else:
            lines.append([item])
    found = []
    for line in lines:
        axis = line[0][1][0]
        biggest = line[0][1][3]
        axles = [o for o, r in line if r[3] < 0.35 * biggest and r[4] > 1.5 * biggest]
        rotors: list[list[dict[str, Any]]] = []
        for o, r in line:
            if o in axles or r[3] < 0.2 * biggest:
                continue
            lo, hi = _along(o, axis)
            for group in rotors:
                glo, ghi = _along(group[0], axis)
                if lo < ghi and glo < hi:
                    group.append(o)
                    break
            else:
                rotors.append([o])
        for group in rotors:
            named = any(h in o["label"].lower() for o in group for h in HINTS)
            size = max(_round_axis(o)[3] for o in group)
            paired = any(g is not group and abs(max(_round_axis(o)[3] for o in g) - size) <= 0.15 * size for g in rotors)
            if named or paired:
                found.append({"objects": [o["name"] for o in group], "axis": axis,
                              "axle": axles[0]["name"] if axles else None, "checked": named})
    return found


def layout(items: list[tuple[str, float, float, float]], bed: list[float],
           margin: float = 5.0) -> tuple[dict[str, dict[str, float]], list[str]]:
    """Shelf packing, biggest first, on the first plate with room: name to {plate, x, y, turned}; and what never fits."""
    placed: dict[str, dict[str, float]] = {}
    too_big = []
    width, depth = bed[0] - 2 * margin, bed[1] - 2 * margin
    plates: list[dict[str, float]] = []
    for name, a, b, h in sorted(items, key=lambda it: -max(it[1], it[2])):
        sx, sy = (max(a, b), min(a, b)) if max(a, b) <= width else (a, b)
        if sx > width or sy > depth or h > bed[2]:
            too_big.append(name)
            continue
        for number, p in enumerate([*plates, None]):
            if p is None:
                p = {"x": 0.0, "y": 0.0, "shelf": 0.0}
                plates.append(p)
            x, y, shelf = p["x"], p["y"], p["shelf"]
            if x + sx > width:
                x, y, shelf = 0.0, y + shelf + margin, 0.0
            if y + sy <= depth:
                placed[name] = {"plate": number + 1, "x": x + margin, "y": y + margin, "turned": sx != a}
                p.update(x=x + sx + margin, y=y, shelf=max(shelf, sy))
                break
    return placed, too_big


def assembly_order(pieces: list[dict[str, Any]], joints: list[dict[str, Any]]) -> list[str]:
    """Biggest piece first, then always the biggest piece that touches what is already built."""
    volume = {p["id"]: p["volume"] for p in pieces}
    near: dict[str, set[str]] = {p["id"]: set() for p in pieces}
    for j in joints:
        near[j["a"]].add(j["b"])
        near[j["b"]].add(j["a"])
    left = set(volume)
    order: list[str] = []
    while left:
        ready = [p for p in left if near[p] & set(order)] or list(left)
        pick = max(ready, key=lambda p: (volume[p], p))
        order.append(pick)
        left.remove(pick)
    return order


def split_needed(colors: int, slots: int, fits: bool) -> bool:
    """Splitting costs assembly, so it is the default only when one print cannot do the job."""
    return colors > slots or not fits


def print_count(report: dict[str, Any]) -> int:
    """How many things the user has to print: the split parts, or else the one print and its moving parts."""
    if report.get("pieces"):
        return len(report["pieces"])
    together = report.get("together")
    return 1 + len(together["moving"]) if together else 1


def together_lines(t: dict[str, str], moving: list[dict[str, Any]]) -> list[str]:
    """All the assembly the one print needs: mounting what moves."""
    out: list[str] = []
    seen: set[str] = set()
    for m in moving:
        name = " / ".join(m["objects"])
        if m["type"] == "stub":
            out.append(t["add_stub"].format(a=name, host=m.get("host", "")))
        elif m["type"] == "through":
            axle = m.get("axle", "")
            if axle not in seen:
                seen.add(axle)
                out.append(t["axle_in"].format(axle=axle))
            out.append(t["add_axle"].format(a=name, axle=axle))
    return out


def step_lines(t: dict[str, str], piece: str, joints: list[dict[str, Any]], built: set[str], name: dict[str, str],
               moving: dict[str, dict[str, Any]], axles_done: set[str]) -> list[str]:
    """What the guide says for adding one piece. Only the hub of a moving part goes on the axle."""
    out = []
    spin = moving.get(piece)
    if spin and piece in spin.get("hub", ()):
        if spin["type"] == "through":
            axle = spin.get("axle", "")
            if axle not in axles_done:
                axles_done.add(axle)
                out.append(t["axle_in"].format(axle=axle))
            out.append(t["add_axle"].format(a=name[piece], axle=axle))
        elif spin["type"] == "stub":
            out.append(t["add_stub"].format(a=name[piece], host=spin.get("host", "")))
    for j in joints:
        other = j["a"] if j["b"] == piece else j["b"] if j["a"] == piece else None
        if other is None or other not in built:
            continue
        if j["type"] == "dowel":
            out.append(t["add_dowel"].format(n=j["count"], a=name[other], b=name[piece]))
        else:
            out.append(t["add_glue"].format(a=name[other], b=name[piece]))
    return out or [t["add_loose"].format(b=name[piece])]


TEXT: dict[str, dict[str, str]] = {
    "de": {"guide_lang": "Sprache der Dateien", "support": "Schalte im Slicer Stützstrukturen ein für: {list}.", "filament": "Filament", "title": "3D-Druck: {name}", "dialog": "3D-Druck-Export", "printer": "Drucker", "custom": "Eigener Drucker",
           "bed": "Bauraum X × Y × Z (mm)", "nozzle": "Düse (mm)", "slots": "Farben gleichzeitig",
           "process": "Verfahren", "whole": "Ganzes Modell als ein Druck", "split": "Nach Farben aufgeteilt, zum Zusammenbauen",
           "join": "Zusammenbau", "pins": "Stecken: Löcher und Verbindungsstifte mitmodellieren",
           "glue": "Kleben: nur Klebeflächen, keine Stifte", "moving": "Bewegliche Teile", "none_moving": "Keine erkannt",
           "folder": "Ordner", "start": "Erstellen", "working": "Wird erstellt, das kann einige Minuten dauern…",
           "done": "Fertig: {n} Teile. Anleitung und Dateien liegen in {path}", "failed": "Export fehlgeschlagen: {why}",
           "no_objects": "Im aktiven Dokument ist nichts Sichtbares zum Drucken.", "open": "Ordner öffnen",
           "guide": "Anleitung", "overview": "Übersicht", "contents": "Was gedruckt wird", "print_tips": "Druckhinweise",
           "whole_tip": "Das ganze Modell als ein Druck: {file}, {x} × {y} × {z} mm, {n} Farben.",
           "too_big_whole": "Achtung: Das ganze Modell passt nicht in den Bauraum. Verwende die aufgeteilten Dateien oder skaliere es.",
           "piece": "Teil", "color": "Farbe", "file": "Datei", "plate": "Platte", "size": "Größe (mm)", "count": "Stück",
           "step": "Schritt {n}", "start_with": "Beginne mit {piece} ({color}).",
           "add_dowel": "Stecke {n} Stift(e) in {a} und setze {b} darauf. Etwas Kleber hält es dauerhaft.",
           "add_glue": "Klebe {b} an {a}.", "add_loose": "Setze {b} an seinen Platz und klebe es fest.",
           "add_axle": "Drücke {a} stramm auf das Ende von {axle}. Die Achse dreht sich mit, das Rad läuft frei. Nicht kleben.",
           "axle_in": "Schiebe {axle} durch die Bohrungen; sie soll sich frei drehen.",
           "dry_fit": "Setze alles zuerst ohne Kleber zusammen. So siehst du, in welcher Reihenfolge die Teile am besten passen, bevor etwas fest ist.",
           "add_stub": "Stecke die Steckachse durch {a} in {host}. Sie sitzt stramm, das Teil dreht sich frei. Nicht kleben.",
           "dowels": "Verbindungsstifte", "axles": "Achsen", "tips_fdm": "Drucke jede Platte in der angegebenen Farbe. Stützstrukturen nur, wo der Slicer sie verlangt. Stifte und Achsen liegend drucken.",
           "tips_resin": "Drucke jede Platte, wasche und härte die Teile, bevor du sie zusammensetzt.",
           "painted": "Zu dünn für einen eigenen Druck, deshalb im Nachbarteil enthalten; am besten bemalen: {list}",
           "warnings": "Bitte beachten", "fit_test": "Tipp: Drucke zuerst einen Stift und ein kleines Teil, um die Passung zu prüfen. Klemmt es, Loch vorsichtig aufbohren; ist es locker, etwas Kleber.",
           "no_split": "Die Aufteilung wurde nicht gewählt.",
           "together_title": "Am wenigsten Zusammenbau",
           "together_tip": "Alles Feste als ein Druck: {file}, {x} × {y} × {z} mm, {n} Farben. Die beweglichen Teile druckst du extra und steckst sie nur noch an:",
           "whole_fixed": "Im Gesamtmodell oben sitzen die beweglichen Teile fest, es ist also eher ein Standmodell."},
    "en": {"guide_lang": "Language of the files", "support": "Turn on supports in the slicer for: {list}.", "filament": "Filament", "title": "3D print: {name}", "dialog": "3D print export", "printer": "Printer", "custom": "Own printer",
           "bed": "Build volume X × Y × Z (mm)", "nozzle": "Nozzle (mm)", "slots": "Colours at once",
           "process": "Process", "whole": "Whole model as one print", "split": "Also split by colour, to assemble",
           "join": "Assembly", "pins": "Plug together: model holes and connector pins",
           "glue": "Glue: gluing faces only, no pins", "moving": "Moving parts", "none_moving": "None found",
           "folder": "Folder", "start": "Create", "working": "Working, this can take a few minutes…",
           "done": "Done: {n} parts. Guide and files are in {path}", "failed": "Export failed: {why}",
           "no_objects": "The active document shows nothing to print.", "open": "Open folder",
           "guide": "Assembly guide", "overview": "Overview", "contents": "What gets printed", "print_tips": "Printing tips",
           "whole_tip": "The whole model as one print: {file}, {x} × {y} × {z} mm, {n} colours.",
           "too_big_whole": "Note: the whole model does not fit the build volume. Use the split files or scale it.",
           "piece": "Part", "color": "Colour", "file": "File", "plate": "Plate", "size": "Size (mm)", "count": "Count",
           "step": "Step {n}", "start_with": "Start with {piece} ({color}).",
           "add_dowel": "Put {n} pin(s) into {a} and press {b} onto them. A little glue makes it permanent.",
           "add_glue": "Glue {b} to {a}.", "add_loose": "Put {b} in its place and glue it.",
           "add_axle": "Press {a} firmly onto the end of {axle}. The axle turns with it, the wheel runs freely. Do not glue.",
           "axle_in": "Slide {axle} through the bores; it should turn freely.",
           "dry_fit": "Put everything together without glue first. You will see which order works best before anything is fixed.",
           "add_stub": "Push the axle pin through {a} into {host}. It sits tight, the part turns freely. Do not glue.",
           "dowels": "Connector pins", "axles": "Axles", "tips_fdm": "Print each plate in the colour given. Use supports only where the slicer asks for them. Print pins and axles lying down.",
           "tips_resin": "Print each plate, wash and cure the parts before you assemble them.",
           "painted": "Too thin for a print of their own, so they are part of the neighbouring piece; best painted: {list}",
           "warnings": "Please note", "fit_test": "Tip: print one pin and a small part first to test the fit. If it binds, carefully drill the hole; if it is loose, add a little glue.",
           "no_split": "Splitting was not chosen.",
           "together_title": "Least assembly",
           "together_tip": "Everything fixed as one print: {file}, {x} × {y} × {z} mm, {n} colours. Print the moving parts apart and just plug them on:",
           "whole_fixed": "In the whole model above the moving parts are fixed, so it is more of a display model."},
}
