import json
import os
import subprocess
import os
import shutil
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import portai
from apk_build_center import ApkBuildCenter, detect_gradle_projects
from portai_apk_translator import ApkPipelineError, sign_apk


class ApkBuildCenterTests(unittest.TestCase):
    def make_apk(self, path):
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("AndroidManifest.xml", b"manifest")
            archive.writestr("classes.dex", b"dex")

    def center(self, root, prompts=(), confirms=()):
        return ApkBuildCenter(
            root, root / "workspaces", root / "reports",
            lambda path: (0, "fake build output", "", None), portai.validate_apk,
            MagicMock(side_effect=list(prompts)), MagicMock(side_effect=list(confirms)), MagicMock(),
        )

    def test_gradle_detection_groups_modules_at_settings_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            (root / "app").mkdir(parents=True)
            (root / "settings.gradle.kts").touch()
            (root / "app" / "build.gradle.kts").touch()
            (root / "library").mkdir()
            (root / "library" / "build.gradle").touch()
            self.assertEqual(detect_gradle_projects(root), [root.resolve()])

    def test_compile_uses_an_isolated_copy_and_records_a_simulated_build(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "android-project"
            project.mkdir()
            (project / "settings.gradle").write_text("include ':app'", encoding="utf-8")
            (project / "build.gradle").write_text("plugins { id 'com.android.application' }", encoding="utf-8")
            (project / "AndroidManifest.xml").write_text("<manifest/>", encoding="utf-8")
            wrapper = project / "gradlew"
            wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            source_mode = wrapper.stat().st_mode
            sdk = root / "sdk"
            (sdk / "platforms" / "android-35").mkdir(parents=True)

            def simulated_build(copy):
                self.assertNotEqual(copy.resolve(), project.resolve())
                apk = copy / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
                apk.parent.mkdir(parents=True)
                self.make_apk(apk)
                return 0, "simulated", "", apk

            center = ApkBuildCenter(
                root, root / "workspaces", root / "reports", simulated_build, portai.validate_apk,
                MagicMock(side_effect=[str(project), "1"]), MagicMock(return_value=True), MagicMock(),
            )
            located = {"java": "/fake/java", "gradle": "/fake/gradle", "cmake": None, "aapt": None, "apksigner": None}
            with patch.dict(os.environ, {"ANDROID_HOME": str(sdk)}), \
                 patch("apk_build_center.shutil.which", side_effect=lambda name: located.get(name)):
                center.compile_project()

            self.assertEqual(wrapper.stat().st_mode, source_mode)
            history = json.loads((root / "reports" / "apk-build-center-history.json").read_text(encoding="utf-8"))
            self.assertEqual(history[0]["result"], "success")
            self.assertTrue(history[0]["details"]["source_preserved"])
            self.assertTrue(Path(history[0]["apk_path"]).is_file())
            self.assertIn("simulated", json.dumps(history))

    def test_build_center_signs_to_new_workspace_and_never_records_passwords(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "original.apk"
            self.make_apk(source)
            original = source.read_bytes()
            keystore = root / "user.keystore"
            keystore.touch()
            signer = root / "apksigner-fake"
            signer.write_text(
                "#!/usr/bin/env python3\nimport shutil,sys\n"
                "a=sys.argv[1:]\n"
                "if a[0]=='sign': shutil.copyfile(a[-1],a[a.index('--out')+1])\n"
                "elif a[0]=='verify': print('verified')\n",
                encoding="utf-8",
            )
            signer.chmod(signer.stat().st_mode | stat.S_IXUSR)
            center = self.center(root, [str(source), str(keystore), "release"], [True])
            with patch("apk_build_center.shutil.which", return_value=str(signer)), \
                 patch("getpass.getpass", side_effect=["hidden-store", "hidden-key"]):
                center.sign_apk()
            history = json.loads((root / "reports" / "apk-build-center-history.json").read_text(encoding="utf-8"))
            serialized = json.dumps(history)
            self.assertNotIn("hidden-store", serialized)
            self.assertNotIn("hidden-key", serialized)
            self.assertEqual(source.read_bytes(), original)
            self.assertTrue(Path(history[0]["apk_path"]).is_file())
            self.assertNotIn("hidden-store", str(center.console.print.call_args_list))

    def test_signing_errors_redact_passwords_echoed_by_a_tool(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.apk"
            self.make_apk(source)
            keystore = root / "user.keystore"
            keystore.touch()

            def failing_runner(command, **kwargs):
                return subprocess.CompletedProcess(command, 1, "", "bad credentials: store-secret key-secret")

            with self.assertRaises(ApkPipelineError) as failure:
                sign_apk(source, root / "signed.apk", "/fake/apksigner", keystore, "release",
                         "store-secret", "key-secret", root, runner=failing_runner)
            self.assertNotIn("store-secret", str(failure.exception))
            self.assertNotIn("key-secret", str(failure.exception))

    def test_alignment_preserves_original_and_records_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.apk"
            self.make_apk(source)
            original = source.read_bytes()
            aligner = root / "zipalign-fake"
            aligner.write_text(
                "#!/usr/bin/env python3\nimport shutil,sys\nshutil.copyfile(sys.argv[-2],sys.argv[-1])\n",
                encoding="utf-8",
            )
            aligner.chmod(aligner.stat().st_mode | stat.S_IXUSR)
            center = self.center(root, [str(source)])
            with patch("apk_build_center.shutil.which", return_value=str(aligner)):
                center.align_apk()
            history = json.loads((root / "reports" / "apk-build-center-history.json").read_text(encoding="utf-8"))
            self.assertEqual(history[0]["result"], "success")
            self.assertEqual(source.read_bytes(), original)
            self.assertTrue((root / "reports" / "backups").is_dir() is False)
            self.assertEqual(len(list((root / "reports").glob("apk-build-*.md"))), 1)

    def test_rebuild_uses_apktool_in_an_isolated_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.apk"
            self.make_apk(source)
            original = source.read_bytes()
            apktool = root / "apktool-fake"
            apktool.write_text(
                "#!/usr/bin/env python3\nimport pathlib,sys,zipfile\n"
                "a=sys.argv[1:]\n"
                "if a[0]=='d':\n"
                " p=pathlib.Path(a[a.index('-o')+1]);p.mkdir();(p/'AndroidManifest.xml').write_bytes(b'manifest')\n"
                "elif a[0]=='b':\n"
                " p=pathlib.Path(a[1]);o=pathlib.Path(a[a.index('-o')+1])\n"
                " with zipfile.ZipFile(o,'w') as z:z.write(p/'AndroidManifest.xml','AndroidManifest.xml')\n",
                encoding="utf-8",
            )
            apktool.chmod(apktool.stat().st_mode | stat.S_IXUSR)
            center = self.center(root, [str(source)])
            with patch("apk_build_center.shutil.which", side_effect=lambda name: str(apktool) if name == "apktool" else "/fake/java"):
                center.rebuild_apk()
            history = json.loads((root / "reports" / "apk-build-center-history.json").read_text(encoding="utf-8"))
            self.assertEqual(history[0]["result"], "success")
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(len(history[0]["commands"]), 2)
            self.assertTrue(Path(history[0]["apk_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
