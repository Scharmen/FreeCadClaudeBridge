"""Against a real FreeCAD 1.1 without GUI (CI: Linux AppImage, Windows portable; locally any install).

Set FREECAD_TARGET to a FreeCAD.app, install folder, freecadcmd, AppImage or extracted AppImage.
Answers the platform questions of the plan (bundled Python, freecadcmd, user directory, invocation)
and writes them to the GitHub job summary.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import anyio
import pytest

from freecad_bridge import headless, installer, platforms

TARGET = os.environ.get("FREECAD_TARGET")
pytestmark = pytest.mark.skipif(not TARGET, reason="FREECAD_TARGET not set")
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def install():
    found = installer.discover(TARGET)
    assert found, f"no FreeCAD found at {TARGET}"
    return found[0]


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("FREECAD_BRIDGE_DIR", str(tmp_path / "bridge"))
    return tmp_path


def summary(lines):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


def run_cmd(install, code, tmp_path):
    script = tmp_path / "probe_extra.py"
    script.write_text(code, encoding="utf-8")
    proc = subprocess.run([*install.command, str(script)], capture_output=True, text=True, timeout=300,
                          env=platforms.clean_env(), stdin=subprocess.DEVNULL)
    return proc.stdout + proc.stderr


def test_probe_reports_platform_facts(install, tmp_path):
    assert install.version.startswith("1.1")
    out = run_cmd(install, "import sys, FreeCAD as App\nprint('FCB_PY=' + sys.version.split()[0])\n"
                           "print('FCB_HOME=' + App.getHomePath())\n", tmp_path)
    facts = installer.parse_probe(out)
    summary([f"### FreeCAD on {sys.platform}", "",
             f"- version: {install.version}", f"- command: `{install.freecadcmd}`",
             f"- user directory: `{install.user_app_data}`", f"- bundled Python: {facts.get('py')}",
             f"- home: `{facts.get('home')}`"])
    assert facts.get("py", "").startswith("3.")


def test_addon_installs_and_imports_in_freecad_python(install, bridge, tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "discover", lambda explicit=None: [install])
    assert installer.install(str(ROOT / "plugin/addon/ClaudeBridge"), out=print) == 0
    assert (install.user_app_data / "Mod" / "ClaudeBridge" / "InitGui.py").is_file()
    code = ("import json, sys\n"
            "import claudebridge, claudebridge.bridge.protocol as p, claudebridge.bridge.journal as j\n"
            "import claudebridge.bridge.childenv, claudebridge.bridge.serialize, claudebridge.panel.stream as s\n"
            "jr = j.Journal(); jr.add('created', doc='D', object='A')\n"
            "print('FCB_RESULT=' + json.dumps({'version': claudebridge.__version__, 'proto': p.PROTOCOL_VERSION,"
            " 'events': len(jr.since()['events']), 'args': s.build_args('x')[:2]}))\n")
    out = run_cmd(install, code, tmp_path)
    line = next((ln for ln in out.splitlines() if ln.startswith("FCB_RESULT=")), None)
    assert line, out[-2000:]
    data = json.loads(line.split("=", 1)[1])
    assert data["events"] == 1 and data["args"] == ["-p", "x"]


def test_run_headless_with_real_freecadcmd(install, bridge):
    platforms.update_config(freecadcmd_cmd=install.command, freecadcmd=install.freecadcmd)
    out = anyio.run(headless.run_headless,
                    "import Part, os\nb = Part.makeBox(10, 20, 30)\nb.exportStep(os.path.join(WORKDIR, 'b.step'))\n"
                    "print('done')\n_result = {'volume': b.Volume}", 300)
    assert out["ok"], out
    assert out["result"] == {"volume": 6000.0} and "done" in out["output"]
    assert (Path(out["workdir"]) / "b.step").stat().st_size > 1000
