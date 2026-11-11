"""A frozen FreeCAD: calls that must not run in the GUI, and naming the line a call hangs in."""

from claudebridge.bridge import hints
from freecad_bridge import stuck

CODE = ("def good_mesh(shape):\n"
        "    m = MeshPart.meshFromShape(Shape=shape, LinearDeflection=0.03)\n"
        "    m.fixSelfIntersections(); m.fixDegenerations(0.0001)\n"
        "    return m\n"
        "m = good_mesh(doc.getObject('E_House_yellow').Shape)\n")
DUMP = ('Thread 0x000000016f5a3000 (most recent call first):\n'
        '  File "/x/threading.py", line 320 in wait\n\n'
        'Thread 0x00000001f1e2c0c0 (most recent call first):\n'
        '  File "<claude>", line 3 in good_mesh\n'
        '  File "<claude>", line 5 in <module>\n'
        '  File "/x/claudebridge/bridge/ops.py", line 210 in run_python\n')


def test_mesh_repair_that_cannot_be_stopped_is_sent_to_the_headless_worker():
    why = hints.unstoppable(CODE)
    assert "fixDegenerations" in why and "run_headless" in why and "force-quit" in why
    assert hints.unstoppable("m.fixSelfIntersections ()")
    assert hints.unstoppable("m.fillupHoles(1000); m.removeNonManifolds(); m.harmonizeNormals()") == ""
    assert hints.unstoppable("# never call m.fixDegenerations(0.1) here\nx = 1") == ""
    assert hints.unstoppable("ok = m.hasSelfIntersections()") == ""


def test_the_line_a_frozen_call_hangs_in_is_named(tmp_path):
    path = hints.stuck_path(str(tmp_path), 4711)
    with open(path, "w", encoding="utf-8") as f:
        f.write(hints.stuck_header(CODE, 1000.0) + DUMP + DUMP)
    said = stuck.note(str(tmp_path), 4711, now=1000.0 + 26 * 60)
    assert "line 3" in said and "m.fixSelfIntersections(); m.fixDegenerations(0.0001)" in said
    assert "26:00 min" in said and "force-quit" in said and "run_headless" in said


def test_no_stack_yet_or_no_file_means_nothing_to_say(tmp_path):
    assert stuck.note(str(tmp_path), 1) == ""
    assert stuck.read(hints.stuck_header(CODE, 1000.0), now=1010.0) == ""
    assert stuck.MARK == hints.MARK
