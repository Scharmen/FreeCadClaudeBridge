"""Standard parts: a catalog of families (pure Python) and their builders (inside freecadcmd)."""

from pathlib import Path


def builders_path() -> Path:
    return Path(__file__).with_name("builders.py")
