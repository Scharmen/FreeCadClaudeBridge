import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "plugin" / "addon" / "ClaudeBridge"
SERVER_SRC = ROOT / "plugin" / "server" / "src"
for p in (ADDON, SERVER_SRC):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))  # the design benchmark: `from design import spec`
