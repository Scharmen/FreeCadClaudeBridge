"""One folder per model: where the model, its exports, print kit and pictures go."""

import os

from claudebridge.bridge import projects

HOME = "/Users/k"
ROOT = "/Users/k/Documents/FreeCAD Projects"


def test_a_folder_name_is_safe_on_every_system():
    assert projects.folder_name("Duplo Dollhouse") == "Duplo Dollhouse"
    assert projects.folder_name('Roof: v2/"final"?') == "Roof_ v2_final_"
    assert projects.folder_name("  ") == "Model"


def test_an_unsaved_model_gets_its_own_folder_under_the_root():
    assert projects.project_folder("", "Dollhouse", ROOT, HOME) == f"{ROOT}/Dollhouse"
    assert projects.model_path(f"{ROOT}/Dollhouse", "Dollhouse") == f"{ROOT}/Dollhouse/Dollhouse.FCStd"


def test_a_model_in_its_own_folder_keeps_it_and_a_loose_one_gets_a_project_folder():
    own = projects.project_folder("/Users/k/Documents/RS5/RS5.FCStd", "RS5 demo", ROOT, HOME)
    assert own == "/Users/k/Documents/RS5"
    for loose in ("/Users/k/Labrador.FCStd", "/Users/k/Documents/Labrador.FCStd", "/Users/k/Desktop/Labrador.FCStd",
                  f"{ROOT}/Labrador.FCStd"):
        assert projects.project_folder(loose, "Labrador", ROOT, HOME) == f"{ROOT}/Labrador", loose


def test_a_bare_file_name_lands_in_the_project_folder():
    folder = f"{ROOT}/Dollhouse"
    assert projects.place("house.3mf", "export", folder) == f"{folder}/exports/house.3mf"
    assert projects.place("Dollhouse", "save", folder) == f"{folder}/Dollhouse.FCStd"
    assert projects.place("/tmp/x.stl", "export", folder) == "/tmp/x.stl", "a path the user gave stays"
    assert projects.place("~/x.stl", "export", folder) == os.path.expanduser("~/x.stl")


def test_the_context_tells_claude_where_files_go():
    text = projects.context("", "Dollhouse", ROOT, HOME)
    assert f"{ROOT}/Dollhouse/Dollhouse.FCStd" in text and "exports/" in text
    text = projects.context("/Users/k/Labrador.FCStd", "Labrador", ROOT, HOME)
    assert f"{ROOT}/Labrador" in text and "loose" in text
