"""Every ```python block in the skill references must run cleanly against the live FreeCAD.

Each block runs in a fresh document as one run_python call; it must not raise
(blocks contain their own asserts) and must leave no object in an error state.
"""

import re
from pathlib import Path

import pytest

from freecad_bridge import client

REFS = Path(__file__).resolve().parents[2] / "plugin/skills/freecad/references"
SKILL = REFS.parent / "SKILL.md"
DOC = "RefTest"


def blocks():
    out = []
    for md in sorted([*REFS.glob("*.md"), SKILL]):
        for i, code in enumerate(re.findall(r"```python\n(.*?)```", md.read_text(encoding="utf-8"), re.S), 1):
            out.append(pytest.param(code, id=f"{md.stem}-{i}"))
    return out


pytestmark = pytest.mark.skipif(not client.list_instances(), reason="no running FreeCAD with ClaudeBridge")


def call(op, timeout_s=60, **args):
    return client.call(op, args, timeout_s=timeout_s).result


@pytest.fixture(autouse=True)
def fresh_doc():
    for d in call("status").get("documents", []):
        if d["name"].startswith(DOC):
            call("document", action="close", name=d["name"], force=True)
    call("document", action="new", name=DOC)
    yield
    for d in call("status").get("documents", []):
        if d["name"].startswith(DOC):
            call("document", action="close", name=d["name"], force=True)


def test_every_reference_file_has_examples():
    names = {p.stem for p in REFS.glob("*.md")}
    assert names >= {"part", "partdesign", "sketcher", "assembly", "techdraw", "varset-spreadsheet", "export",
                     "api-changes-1.1"}
    assert len(blocks()) >= 15


@pytest.mark.parametrize("code", blocks())
def test_reference_block_runs(code):
    try:
        r = call("run_python", code=code, doc=DOC)
    except client.BridgeError as e:
        pytest.fail(f"{e.code}: {e.message}\n{e.traceback or ''}")
    assert r["errors"] == [], r["errors"]
