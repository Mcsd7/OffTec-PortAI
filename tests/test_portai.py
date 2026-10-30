import tempfile
import unittest
from io import StringIO
from pathlib import Path
import sys
import os
import stat
import subprocess
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import portai
from portai import analyze_project, discover_projects

class PortAITests(unittest.TestCase):
    def scan(self, root):
        projects, _ = discover_projects([root])
        return projects

    def test_desktop_discovery_filters_and_preserves_real_roots(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            win = base / "WinApp"; win.mkdir()
            (win / "CMakeLists.txt").write_text("project(WinApp)\nadd_executable(WinApp main.cpp)", encoding="utf-8")
            (win / "main.cpp").write_text("#include <windows.h>\nint WINAPI WinMain() { return 0; }", encoding="utf-8")
            sfml = base / "Game"; (sfml / "src").mkdir(parents=True)
            (sfml / "CMakeLists.txt").write_text("find_package(SFML 2 COMPONENTS graphics window)\nadd_executable(Game src/main.cpp)", encoding="utf-8")
            (sfml / "src" / "main.cpp").write_text("#include <SFML/Graphics.hpp>", encoding="utf-8")
            android = base / "NativeAndroid"; (android / "app" / "src" / "main").mkdir(parents=True)
            (android / "settings.gradle").write_text("include ':app'", encoding="utf-8")
            (android / "build.gradle").write_text("plugins { id 'com.android.application' }\nandroid {}", encoding="utf-8")
            (android / "app" / "src" / "main" / "AndroidManifest.xml").write_text("<manifest package='x.y'/>", encoding="utf-8")
            apk = base / "Extracted"; apk.mkdir()
            (apk / "classes.dex").write_bytes(b"dex\n")
            (apk / "AndroidManifest.xml").write_text("<manifest package='x.y'/>", encoding="utf-8")
            download = base / "Download"; download.mkdir()
            (download / "main.cpp").write_text("int main() {}", encoding="utf-8")
            multi = base / "Multi"; multi.mkdir()
            (multi / "CMakeLists.txt").write_text("project(Multi)\nfind_package(SDL2 REQUIRED)\nadd_executable(Multi main.cpp)", encoding="utf-8")
            (multi / "main.cpp").write_text("#include <SDL.h>\nint main() { SDL_Init(0); }", encoding="utf-8")
            ambiguous = base / "Maybe"; ambiguous.mkdir()
            (ambiguous / "CMakeLists.txt").write_text("project(Maybe)\nadd_executable(Maybe main.cpp)", encoding="utf-8")
            (ambiguous / "main.cpp").write_text("int main() {}", encoding="utf-8")
            results = self.scan(base)
            by_name = {p["path"].name: p for p in results}
            self.assertEqual(set(by_name), {"WinApp", "Game", "Multi", "Maybe"})
            self.assertEqual(by_name["WinApp"]["platform"], "Precisa de análise")
            self.assertIn("SFML", by_name["Game"]["technology"])
            self.assertIn("multiplataforma", by_name["Multi"]["platform"])
            self.assertEqual(by_name["Maybe"]["platform"], "Precisa de análise")
            self.assertEqual(by_name["WinApp"]["path"], win.resolve())
            self.assertNotIn("src", by_name)
            self.assertNotIn("main", by_name)
            self.assertNotIn("original", by_name)
            self.assertNotIn("output", by_name)

    def test_desktop_discovery_ignores_android_and_generic_extracts(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            for name in ("Download", "original", "output", "src", "app", "main"):
                (base / name).mkdir()
            (base / "Download" / "x.cpp").write_text("int main() {}", encoding="utf-8")
            (base / "original" / "x.cpp").write_text("int main() {}", encoding="utf-8")
            nested = base / "app" / "src" / "main"
            nested.mkdir(parents=True)
            (nested / "AndroidManifest.xml").write_text("<manifest package='x.y'/>", encoding="utf-8")
            self.assertEqual(self.scan(base), [])

    def test_roots_config_projects_and_never_promotes_source_subfolders(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            desktop = base / "desktop"
            (desktop / "src").mkdir(parents=True)
            (desktop / "CMakeLists.txt").write_text(
                "project(Desk)\nfind_package(SFML REQUIRED)\nadd_executable(Desk src/main.cpp)", encoding="utf-8")
            (desktop / "src" / "main.cpp").write_text("#include <windows.h>", encoding="utf-8")
            (desktop / "plugins" / "Nested").mkdir(parents=True)
            (desktop / "plugins" / "Nested" / "CMakeLists.txt").write_text(
                "project(Nested)\nfind_package(Qt6 REQUIRED COMPONENTS Widgets)\nadd_executable(Nested main.cpp)", encoding="utf-8")

            game = base / "ported-game"
            (game / "scripts").mkdir(parents=True)
            (game / "CMakeLists.txt").write_text("project(Game)\nfind_package(SDL2 REQUIRED)\nadd_executable(Game main.cpp)", encoding="utf-8")
            (game / "scripts" / "engine.cpp").write_text("#include <windows.h>\nint WINAPI WinMain() {}", encoding="utf-8")

            android = base / "android"
            android.mkdir()
            (android / "build.gradle").write_text("plugins { id 'com.android.application' }\nandroid {}", encoding="utf-8")
            library = base / "library"
            library.mkdir()
            (library / "CMakeLists.txt").write_text("project(OnlyLibrary)\nadd_library(OnlyLibrary lib.cpp)", encoding="utf-8")

            results = self.scan(base)
            by_path = {str(item["path"]): item for item in results}
            self.assertEqual(len([item for item in results if item["path"] == desktop.resolve()]), 1)
            self.assertIn(str(desktop.resolve()), by_path)
            self.assertIn(str((desktop / "plugins" / "Nested").resolve()), by_path)
            self.assertIn(str(game.resolve()), by_path)
            self.assertNotIn(str((game / "scripts").resolve()), by_path)
            self.assertEqual(by_path[str(game.resolve())]["kind"], "Jogo/motor/SDK/código-fonte de jogo")
            self.assertNotIn(str(android.resolve()), by_path)
            self.assertEqual(by_path[str(library.resolve())]["platform"], "Precisa de análise")
            self.assertEqual(by_path[str(library.resolve())]["kind"], "Biblioteca/SDK")

    def test_simple_calculator_search_returns_configured_root_and_dependencies(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            project = home / "simple-calculator"
            project.mkdir()
            (project / "CMakeLists.txt").write_text(
                "project(Calculator)\nadd_executable(Calculator WIN32 src/main.cpp app.rc)\n"
                "find_package(SFML 2.6 REQUIRED graphics)\nfind_package(Boost 1.83 REQUIRED)", encoding="utf-8")
            (project / "CMakePresets.json").write_text('{"configurePresets":[{"name":"windows","cacheVariables":{"CMAKE_CXX_COMPILER":"cl.exe"}}]}', encoding="utf-8")
            for name in ("LICENSE.txt", "README.md"):
                (project / name).write_text("fixture", encoding="utf-8")
            for name in ("include", "assets", "src"):
                (project / name).mkdir()
            with patch.object(Path, "home", return_value=home):
                roots = portai.scan_roots()
                projects, _ = discover_projects(roots)
            found = [item for item in projects if item["path"] == project.resolve()]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["path"], project.resolve())
            self.assertEqual(found[0]["platform"], "Windows desktop")
            self.assertEqual(found[0]["kind"], "Aplicativo desktop independente")
            self.assertIn("SFML", found[0]["technology"])
            self.assertIn("Boost", found[0]["technology"])
            self.assertIn("alvo Windows", found[0]["reason"])

    def test_search_roots_include_only_explicit_simple_calculator_under_home(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            (home / "simple-calculator").mkdir()
            (home / "unrelated").mkdir()
            with patch.object(Path, "home", return_value=home):
                roots = portai.scan_roots()
            self.assertEqual(roots[0], (home / "simple-calculator").resolve())
            self.assertNotIn((home / "unrelated").resolve(), roots)

    def test_detects_windows_api(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "main.cpp").write_text("#include <windows.h>\nint WINAPI WinMain() { return 0; }\n", encoding="utf-8")
            report = analyze_project(root)
            self.assertIn("Windows API / headers", report["windows_indicators"])
            self.assertEqual(report["files_found"], 1)

    def test_discovery_returns_project_and_executable_lists(self):
        with patch.object(portai, "scan_roots", return_value=[]):
            projects, executables = discover_projects()
        self.assertIsInstance(projects, list)
        self.assertIsInstance(executables, list)
        for item in projects:
            self.assertIn("path", item)
            self.assertTrue(Path(item["path"]).is_dir())

    def test_missing_directory_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "does-not-exist"
            with self.assertRaises(ValueError):
                analyze_project(missing)

    def test_codex_fake_executable_reports_exit_and_preserves_workspace_argument(self):
        cases = [
            ("success", 0, "completed", ""),
            ("auth", 7, "", "authentication required"),
            ("invalid", 2, "", "unknown option --bad-option"),
            ("network", 8, "", "network connection failed"),
            ("nonzero", 23, "partial output", "runner failed"),
            ("empty-stderr", 4, "failed", ""),
        ]
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            workspaces = base / "workspaces"
            workspace = workspaces / "project with spaces"
            workspace.mkdir(parents=True)
            executable = base / "fake-codex"
            executable.write_text(
                "#!/usr/bin/env python3\n"
                "import os, sys\n"
                "print(os.environ.get('FAKE_STDOUT', ''), end='')\n"
                "print(os.environ.get('FAKE_STDERR', ''), end='', file=sys.stderr)\n"
                "sys.exit(int(os.environ.get('FAKE_EXIT', '0')))\n",
                encoding="utf-8",
            )
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
            env = os.environ.copy()
            env["PATH"] = str(base) + os.pathsep + env.get("PATH", "")
            env["FAKE_CODEX"] = "1"
            with patch.object(portai, "WORKSPACES", workspaces), patch.dict(os.environ, env, clear=True):
                for scenario, code, stdout, stderr in cases:
                    with self.subTest(scenario=scenario):
                        os.environ["FAKE_EXIT"] = str(code)
                        os.environ["FAKE_STDOUT"] = stdout
                        os.environ["FAKE_STDERR"] = stderr
                        proc = portai.run_codex(str(executable), workspace, "inspect project")
                        self.assertEqual(proc.returncode, code)
                        self.assertEqual(proc.stdout, stdout)
                        self.assertEqual(proc.stderr, stderr)
            argv = portai.codex_exec_command(str(executable), workspace, "prompt")
            self.assertEqual(argv[argv.index("--cd") + 1], str(workspace))
            self.assertNotIn("--ask-for-approval", argv)
            self.assertIn("--sandbox", argv)

    def test_selector_displays_names_and_maps_number_to_matching_project(self):
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "Alpha"
            second = Path(temp) / "Beta"
            first.mkdir()
            second.mkdir()
            projects = [
                {"path": first, "source_count": 2, "platform": "Windows desktop", "technology": "C++", "reason": "WinMain"},
                {"path": second, "source_count": 3, "platform": "desktop multiplataforma", "technology": "SDL", "reason": "SDL"},
            ]
            output = StringIO()
            old_console = portai.console
            portai.console = portai.Console(file=output, force_terminal=False, color_system=None)
            try:
                with patch.object(portai, "discover_projects", return_value=(projects, [])), \
                     patch.object(portai.Prompt, "ask", return_value="2") as ask:
                    selected = portai.select_project()
            finally:
                portai.console = old_console

            self.assertEqual(selected, second)
            self.assertEqual(ask.call_args.kwargs["choices"], ["0", "1", "2", "a"])
            rendered = output.getvalue()
            self.assertIn("Alpha", rendered)
            self.assertIn("Beta", rendered)
            self.assertIn(str(first.resolve()), rendered)
            self.assertIn("Voltar ao menu", rendered)

    def test_selector_zero_returns_to_menu(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "Sample"
            project.mkdir()
            item = {"path": project, "source_count": 1, "platform": "Windows desktop", "technology": "C++", "reason": "WinMain"}
            with patch.object(portai, "discover_projects", return_value=([item], [])), \
                 patch.object(portai.Prompt, "ask", return_value="0"):
                self.assertIsNone(portai.select_project())

    def test_workspace_state_reuses_copy_for_same_source_only(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); workspaces = base / "workspaces"; workspaces.mkdir()
            source = base / "source"; source.mkdir()
            workspace = workspaces / "copy"; workspace.mkdir()
            with patch.object(portai, "WORKSPACES", workspaces):
                state = {"source_path": str(source.resolve()), "session_id": "thread-123", "analysis_done": True}
                portai.save_workspace_state(workspace, state)
                self.assertEqual(portai.find_workspace(source), workspace.resolve())
                self.assertEqual(portai.load_workspace_state(workspace)["session_id"], "thread-123")
                other = base / "other"; other.mkdir()
                self.assertIsNone(portai.find_workspace(other))

    def test_codex_resume_uses_saved_id_and_workspace(self):
        command = portai.codex_resume_command("codex", Path("/tmp/project"), "thread-123")
        self.assertEqual(command[:4], ["codex", "exec", "resume", "thread-123"])
        self.assertEqual(command[command.index("--cd") + 1], "/tmp/project")

    def test_codex_commands_accept_explicit_read_only_sandbox(self):
        initial = portai.codex_exec_command("codex", Path("/tmp/work"), "review", sandbox="read-only")
        resumed = portai.codex_resume_command("codex", Path("/tmp/work"), "thread-123", sandbox="read-only")
        self.assertEqual(initial[initial.index("--sandbox") + 1], "read-only")
        self.assertEqual(resumed[resumed.index("--sandbox") + 1], "read-only")

    def test_game_portability_category_returns_to_main_menu(self):
        with patch.object(portai.Prompt, "ask", side_effect=["4", "1", "0", "0"]), \
             patch.object(portai, "game_recreation_menu") as sub, \
             patch.object(portai, "banner"), patch.object(portai.console, "print"):
            portai.menu()
        sub.assert_called_once_with("1")

    def test_projects_codex_option_dispatches_persistent_submenu(self):
        with patch.object(portai.Prompt, "ask", side_effect=["1", "2", "0", "0"]), \
             patch.object(portai, "reconstruction_menu") as sub, \
             patch.object(portai, "banner"), patch.object(portai.console, "print"):
            portai.menu()
        sub.assert_called_once_with()

    def test_apk_build_center_category_dispatches_and_returns(self):
        with patch.object(portai.Prompt, "ask", side_effect=["2", "0", "0"]), \
             patch.object(portai, "apk_build_center_menu") as sub, \
             patch.object(portai, "banner"), patch.object(portai.console, "print"):
            portai.menu()
        sub.assert_called_once_with()

    def test_build_requires_success_and_nonempty_apk(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            (workspace / "gradlew").write_text("#!/bin/sh\nmkdir -p app/build/outputs/apk/debug\npython -c 'import zipfile; z=zipfile.ZipFile(\"app/build/outputs/apk/debug/app-debug.apk\",\"w\"); z.writestr(\"AndroidManifest.xml\",\"manifest\"); z.close()'\n", encoding="utf-8")
            (workspace / "gradlew").chmod(0o755)
            with patch.object(portai.console, "print"):
                code, _, _, apk = portai.run_build(workspace)
            self.assertEqual(code, 0)
            self.assertEqual(apk.name, "app-debug.apk")
            (workspace / "gradlew").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            code, _, _, apk = portai.run_build(workspace)
            self.assertEqual(code, 1)
            self.assertIsNone(apk)

    def test_termux_gradle_build_prioritizes_its_native_library_for_aapt2(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prefix = root / "termux"
            (prefix / "lib").mkdir(parents=True)
            (prefix / "lib" / "libc++_shared.so").touch()
            aapt2 = prefix / "bin" / "aapt2"
            aapt2.parent.mkdir()
            aapt2.touch()
            project = root / "project"
            project.mkdir()
            (project / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
            apk = project / "app" / "build" / "outputs" / "apk" / "debug" / "app.apk"
            apk.parent.mkdir(parents=True)
            with zipfile.ZipFile(apk, "w") as package:
                package.writestr("AndroidManifest.xml", b"manifest")

            def fake_run(_command, **kwargs):
                self.assertEqual(
                    kwargs["env"]["LD_LIBRARY_PATH"],
                    str(prefix / "lib") + os.pathsep + "/some/other/native/libs",
                )
                return subprocess.CompletedProcess([], 0, "done", "")

            old_console = portai.console
            portai.console = portai.Console(file=StringIO(), force_terminal=False, color_system=None)
            try:
                with patch.dict(os.environ, {"PREFIX": str(prefix), "LD_LIBRARY_PATH": "/some/other/native/libs"}), \
                     patch.object(portai.shutil, "which", side_effect=lambda name: str(aapt2) if name == "aapt2" else None), \
                     patch.object(portai.subprocess, "run", side_effect=fake_run), \
                     patch.object(portai, "find_apk", return_value=apk):
                    code, _stdout, _stderr, built_apk = portai.run_build(project)
            finally:
                portai.console = old_console
            self.assertEqual(code, 0)
            self.assertEqual(built_apk, apk)

    def test_codex_error_is_saved_and_reported_without_losing_state(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspaces" / "copy"; workspace.mkdir(parents=True)
            state = {"source_path": "/source", "history": []}
            fake = subprocess.CompletedProcess([], 9, "partial", "authentication required")
            output = StringIO(); old = portai.console
            portai.console = portai.Console(file=output, force_terminal=False, color_system=None)
            try:
                with patch.object(portai, "run_codex", return_value=fake):
                    portai.codex_task("codex", workspace, state, "inspect")
            finally:
                portai.console = old
            saved = portai.load_workspace_state(workspace)
            self.assertEqual(saved["status"], "erro")
            self.assertIn("authentication required", saved["last_error"])
            self.assertIn("authentication required", output.getvalue())

if __name__ == "__main__":
    unittest.main()
