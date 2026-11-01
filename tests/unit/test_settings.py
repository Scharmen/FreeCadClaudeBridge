"""User settings: defaults, limits, precedence over the computed budget, CLI."""

import json
import os
import sys

import pytest

from freecad_bridge import cli, memguard, platforms, settings

GB = 1024 ** 3
MB = 1024 ** 2


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("FREECAD_BRIDGE_DIR", str(tmp_path / "bridge"))
    monkeypatch.delenv(memguard.ENV_MAX_MB, raising=False)
    return tmp_path / "bridge"


def test_defaults_without_a_file(bridge):
    values = settings.load(bridge)
    assert values == settings.defaults()
    assert values["headless.reserve_percent"] == 15 and values["headless.hard_cap_percent"] == 75


def test_save_clamps_and_keeps_only_changes(bridge):
    values = settings.save(bridge, {"headless.reserve_percent": 99, "headless.hard_cap_percent": "60"})
    assert values["headless.reserve_percent"] == 50, "clamped to the allowed range"
    assert values["headless.hard_cap_percent"] == 60
    stored = json.loads((bridge / settings.FILE_NAME).read_text())
    assert stored == {"headless.reserve_percent": 50, "headless.hard_cap_percent": 60}
    if sys.platform != "win32":
        assert oct(os.stat(bridge / settings.FILE_NAME).st_mode & 0o777) == "0o600"
    settings.save(bridge, {"headless.reserve_percent": 15})
    assert "headless.reserve_percent" not in json.loads((bridge / settings.FILE_NAME).read_text())


def test_unknown_key_is_rejected(bridge):
    with pytest.raises(KeyError):
        settings.save(bridge, {"headless.nope": 1})


def test_broken_file_falls_back_to_defaults(bridge):
    bridge.mkdir(parents=True)
    (bridge / settings.FILE_NAME).write_text("{not json")
    assert settings.load(bridge) == settings.defaults()
    (bridge / settings.FILE_NAME).write_text(json.dumps({"headless.reserve_percent": "abc",
                                                         "headless.hard_cap_percent": 50}))
    values = settings.load(bridge)
    assert values["headless.reserve_percent"] == 15 and values["headless.hard_cap_percent"] == 50


def test_budget_follows_the_settings(bridge):
    settings.save(bridge, {"headless.reserve_percent": 25, "headless.reserve_min_gb": 1,
                           "headless.hard_cap_percent": 50, "headless.contributor_min_mb": 256})
    b = memguard.Budget.current()
    total = memguard.total_memory()
    assert b.reserve == max(1 * GB, int(total * 0.25))
    assert b.hard_cap == int(total * 0.5)
    assert b.contributor_min == 256 * MB


def test_env_cap_beats_the_settings(bridge, monkeypatch):
    settings.save(bridge, {"headless.hard_cap_percent": 50})
    monkeypatch.setenv(memguard.ENV_MAX_MB, "1234")
    assert memguard.Budget.current().hard_cap == 1234 * MB


def test_cli_set_show_reset(bridge, capsys):
    assert cli.main(["settings", "set", "headless.reserve_percent", "20"]) == 0
    assert settings.load(bridge)["headless.reserve_percent"] == 20
    assert cli.main(["settings"]) == 0
    out = capsys.readouterr().out
    assert "headless.reserve_percent" in out and "20" in out and "On this computer" in out
    assert cli.main(["settings", "set", "headless.nope", "1"]) == 2
    assert cli.main(["settings", "reset"]) == 0
    assert settings.load(bridge) == settings.defaults()


def test_headless_timeout_default_comes_from_settings(bridge):
    settings.save(bridge, {"headless.timeout_s": 120})
    assert settings.load(platforms.bridge_dir())["headless.timeout_s"] == 120


def test_settings_file_survives_uninstall_unless_purged():
    from freecad_bridge import installer

    src = open(installer.__file__).read()
    assert '"settings.json"' in src
