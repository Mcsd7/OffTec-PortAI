import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from game_portability import (
    analyze_game_project, backup_authorized_files, detect_engine,
    restore_authorized_backup, save_game_report,
)


class GamePortabilityTests(unittest.TestCase):
    def test_detects_unity_dependencies_controls_graphics_and_adaptation_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "unity-game"
            (root / "ProjectSettings").mkdir(parents=True)
            (root / "Assets/Scripts").mkdir(parents=True)
            (root / "Packages").mkdir()
            (root / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.10f1", encoding="utf-8")
            (root / "Packages/manifest.json").write_text('{"dependencies":{"com.unity.inputsystem":"1.7.0"}}', encoding="utf-8")
            script = root / "Assets/Scripts/PlayerController.cs"
            script.write_text(
                "void Update(){ if (Input.GetKeyDown(KeyCode.Space)) Jump(); "
                "var p = Input.mousePosition; Screen.SetResolution(1920,1080,true); }\n"
                "QualitySettings.SetQualityLevel(2);", encoding="utf-8"
            )
            report = analyze_game_project(root)
            self.assertEqual(report["engine"], "Unity")
            self.assertEqual(report["languages"], {"C#": 1})
            self.assertIn("Assets/Scripts/PlayerController.cs", report["control_findings"]["keyboard"])
            self.assertIn("Assets/Scripts/PlayerController.cs", report["control_findings"]["mouse"])
            self.assertTrue(report["resolution_findings"])
            self.assertTrue(report["performance_findings"])
            self.assertEqual(report["dependencies"][0]["name"], "com.unity.inputsystem")
            candidate = next(item for item in report["candidate_files"] if item["path"] == "Assets/Scripts/PlayerController.cs")
            self.assertIn("keyboard", candidate["reasons"])
            self.assertIn("Linux", report["external_build_host"])
            self.assertFalse(report["build_ready"])

    def test_detects_other_engines_and_custom_projects(self):
        cases = {
            "Unreal Engine": "Sample.uproject",
            "Godot": "project.godot",
            "GameMaker": "Sample.yyp",
        }
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for expected, marker in cases.items():
                folder = base / expected.replace(" ", "-")
                folder.mkdir()
                (folder / marker).write_text("{}", encoding="utf-8")
                self.assertEqual(detect_engine(folder)[0], expected)
            custom = base / "custom"
            custom.mkdir()
            (custom / "CMakeLists.txt").write_text("project(game)", encoding="utf-8")
            self.assertEqual(detect_engine(custom)[0], "Projeto personalizado")

    def test_analysis_and_existing_report_system_include_progress_and_limitations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "godot-game"
            root.mkdir()
            (root / "project.godot").write_text("[application]\nconfig/name=\"Demo\"", encoding="utf-8")
            report = analyze_game_project(root)
            md, js = save_game_report(report, Path(tmp) / "reports")
            self.assertTrue(md.is_file())
            saved = json.loads(js.read_text(encoding="utf-8"))
            self.assertEqual(saved["engine"], "Godot")
            self.assertIn("plan", saved)
            self.assertTrue(saved["limitations"])

    def test_authorized_file_backup_can_restore_edits_and_remove_new_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            workspace = base / "workspaces/game-copy"
            workspace.mkdir(parents=True)
            source = workspace / "Assets/Player.cs"
            source.parent.mkdir()
            source.write_text("original", encoding="utf-8")
            manifest = backup_authorized_files(workspace, ["Assets/Player.cs", "Assets/New.cs"], base / "backups")
            source.write_text("changed", encoding="utf-8")
            created = workspace / "Assets/New.cs"
            created.write_text("generated", encoding="utf-8")
            restored = restore_authorized_backup(workspace, manifest)
            self.assertEqual(set(restored), {"Assets/Player.cs", "Assets/New.cs"})
            self.assertEqual(source.read_text(encoding="utf-8"), "original")
            self.assertFalse(created.exists())

    def test_backup_rejects_path_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            with self.assertRaises(ValueError):
                backup_authorized_files(workspace, ["../outside.txt"], Path(tmp) / "backups")


if __name__ == "__main__":
    unittest.main()
