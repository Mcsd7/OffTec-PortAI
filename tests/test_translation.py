import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from portai_translation import TranslationEngine, TranslationProvider
from portai_translation.formats import extract
from portai_translation.providers import CommandProvider
from unittest.mock import patch
import subprocess


class FakeProvider(TranslationProvider):
    name = "test-provider"

    def __init__(self, corrupt=False):
        self.corrupt = corrupt

    def translate(self, entries, target, source):
        result = {}
        for entry in entries:
            result[entry.entry_id] = entry.source.replace("__PORTAI_KEEP_0__", "BROKEN") if self.corrupt else f"{target}: {entry.source}"
        return result


class TranslationTests(unittest.TestCase):
    def test_json_extract_apply_review_and_keep_source_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            project = base / "game"
            project.mkdir()
            source = project / "dialog.json"
            original = '{"menu": {"start": "Start {player}", "id": "MENU_START"}, "count": 3}\n'
            source.write_text(original, encoding="utf-8")
            engine = TranslationEngine(FakeProvider())
            result = engine.scan(project)
            self.assertEqual(len(result.entries), 1)
            engine.translate(result, ["pt-BR"])
            entry = result.entries[0]
            self.assertEqual(entry.translations["pt-BR"], "pt-BR: Start {player}")
            self.assertEqual(entry.target_status["pt-BR"], "review")
            engine.approve(result, {(entry.entry_id, "pt-BR"): True}, ["pt-BR"])
            report = engine.apply(result, project, base / "out", ["pt-BR"])
            translated = json.loads((base / "out" / "pt-BR" / "dialog.json").read_text(encoding="utf-8"))
            self.assertEqual(translated["menu"]["start"], "pt-BR: Start {player}")
            self.assertEqual(translated["menu"]["id"], "MENU_START")
            self.assertEqual(source.read_text(encoding="utf-8"), original)
            self.assertTrue(report.is_file())

    def test_android_resource_targets_have_locale_folders_and_rtl_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            project = base / "app"
            values = project / "app/src/main/res/values"
            values.mkdir(parents=True)
            xml = values / "strings.xml"
            xml.write_text('<resources>\n    <string name="welcome">Welcome</string>\n    <string name="app_name">Game</string>\n</resources>\n', encoding="utf-8")
            engine = TranslationEngine(FakeProvider())
            result = engine.scan(project)
            engine.translate(result, ["pt-BR", "ar", "hi"])
            decisions = {(entry.entry_id, locale): True for entry in result.entries for locale in result.targets}
            engine.approve(result, decisions)
            output = base / "locales"
            engine.apply(result, project, output)
            self.assertTrue((output / "pt-BR/values-pt-rBR/strings.xml").is_file())
            self.assertTrue((output / "ar/values-ar/strings.xml").is_file())
            self.assertTrue((output / "hi/values-hi/strings.xml").is_file())
            arabic = (output / "ar/values-ar/strings.xml").read_text(encoding="utf-8")
            self.assertIn("ar: Welcome", arabic)

    def test_json_pointer_keys_and_android_plural_resources_do_not_collide(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            project = base / "project"
            values = project / "app/src/main/res/values"
            values.mkdir(parents=True)
            (project / "data.json").write_text(
                '{"menu.start":"Start","menu":{"start":"Begin"}}', encoding="utf-8"
            )
            xml = values / "strings.xml"
            xml.write_text(
                '<resources><plurals name="apples"><item quantity="one">One apple</item>'
                '<item quantity="other">%d apples</item></plurals></resources>', encoding="utf-8"
            )
            engine = TranslationEngine(FakeProvider())
            result = engine.scan(project)
            self.assertEqual(len([entry for entry in result.entries if entry.path == "data.json"]), 2)
            plural_entries = [entry for entry in result.entries if entry.path.endswith("strings.xml")]
            self.assertEqual({entry.key for entry in plural_entries}, {"apples.one", "apples.other"})
            engine.translate(result, ["es"])
            engine.approve(result, {(entry.entry_id, "es"): True for entry in result.entries}, ["es"])
            engine.apply(result, project, base / "out", ["es"])
            output_json = json.loads((base / "out/es/data.json").read_text(encoding="utf-8"))
            self.assertEqual(output_json["menu.start"], "es: Start")
            self.assertEqual(output_json["menu"]["start"], "es: Begin")
            output_xml = (base / "out/es/values-es/strings.xml").read_text(encoding="utf-8")
            self.assertIn('name="apples"', output_xml)
            self.assertIn("es: One apple", output_xml)
            self.assertIn("es: %d apples", output_xml)

    def test_formats_extract_ids_and_text_without_rewriting_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = {
                "strings.csv": 'id,text\nMENU_START,Start game\n',
                "messages.po": 'msgid "Hello"\nmsgstr ""\n',
                "readme.txt": 'Hello player\n',
                "strings.properties": 'menu.start=Start game\n',
                "strings.resx": '<root><data name="greeting"><value>Hello there</value></data></root>',
            }
            for name, content in cases.items():
                path = root / name
                path.write_text(content, encoding="utf-8")
                fmt, entries = extract(path, root)
                self.assertTrue(entries, name)
                self.assertEqual(path.read_text(encoding="utf-8"), content)
                self.assertTrue(fmt)

    def test_android_non_translatable_resource_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "strings.xml"
            path.write_text(
                '<resources><string name="internal" translatable="false">Internal code</string>'
                '<string name="visible">Visible label</string></resources>', encoding="utf-8"
            )
            _, entries = extract(path, root)
            self.assertEqual([item[0] for item in entries], ["visible"])

    def test_placeholder_loss_rejected_and_untranslated_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            (root / "menu.json").write_text('{"title":"Hello {name}"}', encoding="utf-8")
            engine = TranslationEngine(FakeProvider(corrupt=True))
            result = engine.scan(root)
            engine.translate(result, ["es"])
            self.assertNotIn("es", result.entries[0].translations)
            self.assertIn("Placeholder", result.entries[0].error)
            report = engine.apply(result, root, Path(tmp) / "out", ["es"])
            data = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(data["entries"][0]["status"], "pending")
            self.assertFalse((Path(tmp) / "out/es/menu.json").exists())

    def test_apply_refuses_to_write_inside_source_and_backs_up_prior_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            project = base / "game"
            project.mkdir()
            (project / "strings.txt").write_text("Hello\n", encoding="utf-8")
            engine = TranslationEngine(FakeProvider())
            result = engine.scan(project)
            engine.translate(result, ["es"])
            entry = result.entries[0]
            engine.approve(result, {(entry.entry_id, "es"): True}, ["es"])
            with self.assertRaises(ValueError):
                engine.apply(result, project, project / "locales", ["es"])
            output = base / "out"
            dest = output / "es/strings.txt"
            dest.parent.mkdir(parents=True)
            dest.write_text("previous\n", encoding="utf-8")
            engine.apply(result, project, output, ["es"])
            backups = list((output / "backups").rglob("strings.txt"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "previous\n")

    def test_apply_breaks_output_hardlink_and_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            project = base / "game"
            project.mkdir()
            source = project / "strings.txt"
            source.write_text("Hello\n", encoding="utf-8")
            engine = TranslationEngine(FakeProvider())
            result = engine.scan(project)
            engine.translate(result, ["es"])
            entry = result.entries[0]
            engine.approve(result, {(entry.entry_id, "es"): True}, ["es"])
            output = base / "out"
            dest = output / "es/strings.txt"
            dest.parent.mkdir(parents=True)
            try:
                dest.hardlink_to(source)
            except (OSError, NotImplementedError):
                pass
            else:
                engine.apply(result, project, output, ["es"])
                self.assertEqual(source.read_text(encoding="utf-8"), "Hello\n")
                self.assertEqual(dest.read_text(encoding="utf-8"), "es: Hello\n")

            escaped = base / "escaped"
            escaped.mkdir()
            (escaped / "es").symlink_to(project, target_is_directory=True)
            with self.assertRaises(ValueError):
                engine.apply(result, project, escaped, ["es"])

    def test_command_provider_uses_documented_json_adapter_contract(self):
        from portai_translation import TranslationEntry
        entry = TranslationEntry("ui.json::/menu/start", "ui.json", "json", "/menu/start", "Start", "json;menu")
        completed = subprocess.CompletedProcess([], 0, '{"ui.json::/menu/start":"Iniciar"}', "")
        with patch("portai_translation.providers.subprocess.run", return_value=completed) as run:
            translated = CommandProvider("local-bridge").translate([entry], "es", "en")
        request = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(request["entries"][0]["key"], "/menu/start")
        self.assertEqual(request["entries"][0]["context"], "json;menu")
        self.assertEqual(translated[entry.entry_id], "Iniciar")


if __name__ == "__main__":
    unittest.main()
