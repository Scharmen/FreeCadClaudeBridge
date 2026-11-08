"""One folder per model: where the model, its exports, print kit and pictures go."""

import os

from claudebridge.bridge import projects

J = os.path.join                     # expected paths are built like the code builds them (\ on Windows)
HOME = J(os.sep, "Users", "k")
ROOT = J(HOME, "Documents", "FreeCAD Projects")


def test_a_folder_name_is_safe_on_every_system():
    assert projects.folder_name("Duplo Dollhouse") == "Duplo Dollhouse"
    assert projects.folder_name('Roof: v2/"final"?') == "Roof_ v2_final_"
    assert projects.folder_name("  ") == "Model"


def test_an_unsaved_model_gets_its_own_folder_under_the_root():
    assert projects.project_folder("", "Dollhouse", ROOT, HOME) == J(ROOT, "Dollhouse")
    assert projects.model_path(J(ROOT, "Dollhouse"), "Dollhouse") == J(ROOT, "Dollhouse", "Dollhouse.FCStd")


def test_a_model_in_its_own_folder_keeps_it_and_a_loose_one_gets_a_project_folder():
    own = projects.project_folder(J(HOME, "Documents", "RS5", "RS5.FCStd"), "RS5 demo", ROOT, HOME)
    assert own == J(HOME, "Documents", "RS5")
    for loose in (J(HOME, "Labrador.FCStd"), J(HOME, "Documents", "Labrador.FCStd"),
                  J(HOME, "Desktop", "Labrador.FCStd"), J(ROOT, "Labrador.FCStd")):
        assert projects.project_folder(loose, "Labrador", ROOT, HOME) == J(ROOT, "Labrador"), loose


def test_a_bare_file_name_lands_in_the_project_folder():
    folder = J(ROOT, "Dollhouse")
    assert projects.place("house.3mf", "export", folder) == J(folder, "exports", "house.3mf")
    assert projects.place("Dollhouse", "save", folder) == J(folder, "Dollhouse.FCStd")
    given = J(os.sep, "tmp", "x.stl")
    assert projects.place(given, "export", folder) == os.path.abspath(given), "a path the user gave stays"
    assert projects.place("~/x.stl", "export", folder) == os.path.abspath(os.path.expanduser("~/x.stl"))


def test_the_context_tells_claude_where_files_go():
    text = projects.context("", "Dollhouse", ROOT, HOME)
    assert J(ROOT, "Dollhouse", "Dollhouse.FCStd") in text and "exports/" in text
    text = projects.context(J(HOME, "Labrador.FCStd"), "Labrador", ROOT, HOME)
    assert J(ROOT, "Labrador") in text and "loose" in text
