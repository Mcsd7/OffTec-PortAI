import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import portai
from portai_core.analyzer import analyze_project
from portai_core.build import detect_build_command, run_build
from portai_core.compatibility import compare_project
from portai_core.codex import backup_workspace, changed_paths, file_inventory
from portai_core.platforms import PLATFORMS, get_platform
from portai_core.projects import load_projects, register_project
from portai_core.reports import write_report


class CoreTests(unittest.TestCase):
    def test_console_platform_catalog_complete_and_never_claims_console_build(self):
        self.assertEqual(len(PLATFORMS), 16)
        for platform in PLATFORMS:
            status = platform.status()
            self.assertFalse(status["compile_capable"])
            self.assertIn("SDK", status["availability"])
        self.assertEqual(get_platform("switch").architecture, "ARMv8-A")

    def test_analyzer_detects_engines_build_apis_assets_and_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ProjectSettings").mkdir()
            (root / "ProjectSettings" / "ProjectVersion.txt").write_text("2022.3")
            (root / "CMakeLists.txt").write_text("find_package(SDL2 REQUIRED)\n")
            (root / "main.cpp").write_text("#include <SDL.h>\nD3D11CreateDevice();")
            (root / "hero.png").write_bytes(b"x")
            result = analyze_project(root)
            self.assertIn("Unity", result["engines"])
            self.assertEqual(result["assets"]["count"], 1)
            self.assertIn("Direct3D", result["graphics_audio_input_signals"]["graphics"])
            self.assertIn("CMakeLists.txt", result["build_systems"])

    def test_compatibility_reports_directx_and_console_sdk_limit(self):
        project = {"build_systems": ["CMakeLists.txt"], "graphics_audio_input_signals": {"graphics": {"Direct3D": ["main.cpp"]}}}
        result = compare_project(project, "windows", "ps5")
        self.assertEqual(result["classification"], "incompatível")
        self.assertFalse(result["compile_capable"])
        self.assertTrue(any("SDK" in item["title"] for item in result["findings"]))

    def test_build_detection_and_safe_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile").write_text("all:\n\t@true\n")
            with patch("portai_core.build.shutil.which", side_effect=lambda n: "/usr/bin/make" if n == "make" else None):
                self.assertEqual(detect_build_command(root)[0], "/usr/bin/make")
                result = run_build(root)
            self.assertEqual(result["status"], "succeeded")
            self.assertIsNone(result["artifact"])
            self.assertEqual(result["command"][0], "/usr/bin/make")

    def test_unknown_build_and_timeout_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run_build(Path(tmp))["status"], "not_run")
            with self.assertRaises(ValueError): run_build(Path(tmp), timeout=0)

    def test_project_registry_and_reports_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); project=root/"game"; project.mkdir()
            record=register_project(root/"state", project)
            self.assertEqual(load_projects(root/"state")[0]["path"],record["path"])
            md,js=write_report({"project":str(project),"classification":"analyzed"},root/"reports")
            self.assertIn("analyzed",md.read_text())
            self.assertEqual(json.loads(js.read_text())["project"],str(project))
            self.assertEqual(js.stat().st_mode & 0o077,0)

    def test_workspace_copy_skips_external_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); source=base/"source"; source.mkdir(); (source/"safe.txt").write_text("safe")
            outside=base/"outside.txt"; outside.write_text("private")
            try: (source/"linked.txt").symlink_to(outside)
            except OSError: self.skipTest("symlinks unavailable")
            with patch.object(portai,"ROOT",base/"tool"), patch.object(portai,"WORKSPACES",base/"workspaces"):
                copy=portai.make_workspace(source)
            self.assertTrue((copy/"safe.txt").is_file())
            self.assertFalse((copy/"linked.txt").exists())

    def test_codex_workspace_backup_and_change_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"workspace"; root.mkdir()
            target=root/"code.cpp"; target.write_text("before")
            before=file_inventory(root)
            archive=backup_workspace(root,Path(tmp)/"backups")
            target.write_text("after")
            changes=changed_paths(before,file_inventory(root))
            self.assertEqual(changes,["code.cpp"])
            import zipfile
            with zipfile.ZipFile(archive) as zf: self.assertEqual(zf.read("code.cpp"),b"before")

    def test_dashboard_renders_without_width_assumptions(self):
        from rich.console import Console
        from io import StringIO
        stream=StringIO(); old=portai.console
        try:
            portai.console=Console(file=stream,width=32,force_terminal=False,color_system=None)
            portai.professional_dashboard()
        finally: portai.console=old
        self.assertIn("OFF",stream.getvalue())
        self.assertIn("Codex CLI",stream.getvalue())


if __name__ == "__main__": unittest.main()
