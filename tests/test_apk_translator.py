import json
import os
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from portai_apk_translator import ApkTranslator, find_android_value_files, inspect_apk, read_decoded_manifest
from portai_translation import TranslationEngine, TranslationProvider


class ApkFakeProvider(TranslationProvider):
    name = "apk-test-provider"

    def translate(self, entries, target, source):
        return {entry.entry_id: f"{target}: {entry.source}" for entry in entries}


class ApkTranslatorTests(unittest.TestCase):
    def make_apk(self, path):
        with zipfile.ZipFile(path, "w") as package:
            package.writestr("AndroidManifest.xml", b"binary-manifest-fixture")
            package.writestr("classes.dex", b"dex-fixture")
            package.writestr("lib/arm64-v8a/libgame.so", b"native-fixture")

    def fake_tools(self, root):
        apktool = root / "apktool-fake"
        apktool.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib,sys,zipfile\n"
            "args=sys.argv[1:]\n"
            "if args[0]=='d':\n"
            " out=pathlib.Path(args[args.index('-o')+1]); (out/'res/values').mkdir(parents=True)\n"
            " (out/'AndroidManifest.xml').write_text('<manifest xmlns:android=\"http://schemas.android.com/apk/res/android\" package=\"org.example.game\" android:versionCode=\"7\" android:versionName=\"1.2\"><application android:label=\"@string/app_name\"/></manifest>',encoding='utf-8')\n"
            " (out/'res/values/strings.xml').write_text('<resources><string name=\"start_button\">Start game</string></resources>',encoding='utf-8')\n"
            "elif args[0]=='b':\n"
            " src=pathlib.Path(args[1]); out=pathlib.Path(args[args.index('-o')+1])\n"
            " with zipfile.ZipFile(out,'w') as z:\n"
            "  z.write(src/'AndroidManifest.xml','AndroidManifest.xml')\n"
            "  [z.write(p,p.relative_to(src).as_posix()) for p in src.rglob('*.xml')]\n"
        )
        apktool.chmod(apktool.stat().st_mode | stat.S_IXUSR)
        signer = root / "apksigner-fake"
        signer.write_text(
            "#!/usr/bin/env python3\n"
            "import shutil,sys\n"
            "args=sys.argv[1:]\n"
            "if args[0]=='sign': shutil.copyfile(args[-1],args[args.index('--out')+1])\n"
            "elif args[0]=='verify': print('Verified using fake signer')\n"
        )
        signer.chmod(signer.stat().st_mode | stat.S_IXUSR)
        return {"apktool": str(apktool), "apksigner": str(signer), "java": "/usr/bin/java", "jadx": None, "zipalign": None}

    def test_apk_inspection_detects_version_architecture_and_split_neighbors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk = root / "base.apk"
            self.make_apk(apk)
            self.make_apk(root / "split_config.arm64_v8a.apk")
            info = inspect_apk(apk)
            self.assertEqual(info["architectures"], ["arm64-v8a"])
            self.assertTrue(info["has_dex"])
            self.assertTrue(info["neighboring_apks"])
            manifest = root / "AndroidManifest.xml"
            manifest.write_text(
                '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="org.example" android:versionCode="9" android:versionName="2.0"><application android:label="@string/app_name"/></manifest>',
                encoding="utf-8",
            )
            parsed = read_decoded_manifest(manifest)
            self.assertEqual(parsed["package"], "org.example")
            self.assertEqual(parsed["version_name"], "2.0")
            self.assertEqual(parsed["application_label"], "@string/app_name")

    def test_end_to_end_rebuild_signs_validates_and_keeps_source_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk = root / "game.apk"
            self.make_apk(apk)
            source_bytes = apk.read_bytes()
            keystore = root / "user-owned.keystore"
            keystore.write_bytes(b"test-key-placeholder")
            engine = TranslationEngine(ApkFakeProvider())
            tools = self.fake_tools(root)

            def approve_all(result, active_engine, targets):
                active_engine.approve(
                    result,
                    {(entry.entry_id, locale): True for entry in result.entries for locale in targets},
                    targets,
                )

            report = ApkTranslator(engine, tools=tools).run(
                apk=apk, workspace_root=root / "workspaces", targets=["es", "ar"], reviewer=approve_all,
                keystore=keystore, key_alias="release", keystore_password="secret-store-pass",
                key_password="secret-key-pass", source_language="en",
            )
            self.assertEqual(report["status"], "success", report["errors"])
            self.assertTrue(report["validated"])
            self.assertEqual(report["package"], "org.example.game")
            self.assertEqual(report["version_code"], "7")
            self.assertEqual(report["architectures"], ["arm64-v8a"])
            self.assertEqual(apk.read_bytes(), source_bytes)
            output = Path(report["output_apk"])
            with zipfile.ZipFile(output) as package:
                self.assertIn("res/values-es/strings.xml", package.namelist())
                localized = package.read("res/values-es/strings.xml").decode("utf-8")
                self.assertIn("res/values-ar/strings.xml", package.namelist())
                manifest = package.read("AndroidManifest.xml").decode("utf-8")
            self.assertIn("es: Start game", localized)
            self.assertIn('supportsRtl="true"', manifest)
            self.assertIn("res/values-ar/strings.xml", report["modified_files"])
            self.assertIn("AndroidManifest.xml", report["modified_files"])
            self.assertIn("signature_verification", report)
            report_file = Path(report["report_path"])
            persisted = json.loads(report_file.read_text(encoding="utf-8"))
            self.assertNotIn("secret-key-pass", report_file.read_text(encoding="utf-8"))
            self.assertEqual(persisted["modified_files"], [
                "AndroidManifest.xml", "res/values-es/strings.xml", "res/values-ar/strings.xml",
            ])

    def test_missing_build_dependency_is_reported_without_touching_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk = root / "game.apk"
            self.make_apk(apk)
            key = root / "key"
            key.write_text("fixture", encoding="utf-8")
            report = ApkTranslator(
                TranslationEngine(ApkFakeProvider()),
                tools={"apktool": None, "apksigner": "/not-used", "java": "/not-used", "jadx": None, "zipalign": None},
            ).run(apk, root / "workspaces", ["hi"], lambda *args: None, key, "alias", "pass", "pass")
            self.assertEqual(report["status"], "failed")
            self.assertIn("apktool", report["errors"][0])
            self.assertTrue(Path(report["report_path"]).is_file())

    def test_failed_signature_verification_never_reports_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk = root / "game.apk"
            self.make_apk(apk)
            keystore = root / "user-owned.keystore"
            keystore.write_bytes(b"test-key-placeholder")
            tools = self.fake_tools(root)
            signer = Path(tools["apksigner"])
            signer.write_text(
                "#!/usr/bin/env python3\n"
                "import shutil,sys\n"
                "args=sys.argv[1:]\n"
                "if args[0]=='sign': shutil.copyfile(args[-1],args[args.index('--out')+1])\n"
                "elif args[0]=='verify': sys.exit(9)\n",
                encoding="utf-8",
            )
            signer.chmod(signer.stat().st_mode | stat.S_IXUSR)
            engine = TranslationEngine(ApkFakeProvider())

            def approve_all(result, active_engine, targets):
                active_engine.approve(result, {(e.entry_id, lang): True for e in result.entries for lang in targets}, targets)

            report = ApkTranslator(engine, tools=tools).run(
                apk, root / "workspaces", ["hi"], approve_all, keystore, "release", "store", "key", "en",
            )
            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["validated"])
            self.assertIsNone(report["output_apk"])
            self.assertIn("código 9", report["errors"][0])

    def test_only_android_values_xml_is_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "res/values").mkdir(parents=True)
            (root / "res/layout").mkdir()
            (root / "res/values/strings.xml").write_text("<resources/>", encoding="utf-8")
            (root / "res/values-es").mkdir()
            (root / "res/values-es/strings.xml").write_text("<resources/>", encoding="utf-8")
            (root / "res/layout/activity.xml").write_text("<layout/>", encoding="utf-8")
            self.assertEqual([p.name for p in find_android_value_files(root)], ["strings.xml"])


if __name__ == "__main__":
    unittest.main()
