"""Loss-prevention and migration regression tests; no live services are used."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import string
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from config_migration import extract_legacy
from config_schema import ConfigConflict, ConfigError, differences, digest, parse_json, validate
from config_store import ConfigStore, read_document, write_export

ROOT = Path(__file__).resolve().parent


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="serp-config-test-")
        self.root = Path(self.directory.name)
        self.document = read_document(ROOT / "config_defaults.json")
        self.store = ConfigStore(self.root / "runtime.sqlite3")
        self.source = {"kind": "test", "sha256": digest(self.document)}
        self.first, _ = self.store.initialize(self.document, self.source)
        self.environment = patch.dict(os.environ, {"SERP_CONFIG_STORE": str(self.store.path)})
        self.environment.start()
        os.environ.pop("SERP_CONFIG_REVISION", None)

    def tearDown(self):
        self.environment.stop()
        self.directory.cleanup()

    def legacy(self):
        target = self.root / "legacy"
        target.mkdir()
        for path in (ROOT / "tests/fixtures/legacy").iterdir():
            shutil.copyfile(path, target / path.name.removesuffix(".txt"))
        return target

    def changed(self):
        result = copy.deepcopy(self.document)
        result["settings"]["SEARCH_KEYWORDS"]['带"引号\\反斜杠'] = ['检索"词\\']
        return result

    def test_legacy_migration_keeps_every_prompt_and_rendered_message(self):
        document, source = extract_legacy(self.legacy())
        self.assertEqual(document, self.document)
        hashes = json.loads((ROOT / "tests/fixtures/prompt_hashes.json").read_text())
        self.assertEqual(len(document["prompts"]), 22)
        self.assertEqual(sum(v["status"] == "archived" for v in document["prompts"].values()), 8)
        for name, item in document["prompts"].items():
            self.assertEqual(hashlib.sha256(item["text"].encode()).hexdigest(), hashes[name])
            if "USER_PROMPT" in name or name == "NEWS_SCORE_PROMPT":
                fields = {field for _, field, _, _ in string.Formatter().parse(item["text"]) if field is not None}
                sample = {field: f"固定样例 {field} {{保留正文花括号}}" for field in fields}
                self.assertEqual(item["text"].format(**sample), self.document["prompts"][name]["text"].format(**sample))
        self.assertNotIn("temperature", document["models"]["item_summarizer"]["parameters"])
        migrated = ConfigStore(self.root / "migrated.sqlite3")
        revision, _ = migrated.initialize(document, source)
        self.assertEqual(revision.document, self.document)
        self.assertEqual(migrated.original_source(), source)

    def test_migration_never_executes_source(self):
        source = self.legacy()
        marker = self.root / "executed"
        with (source / "config.py").open("a") as handle:
            handle.write(f"\nopen({str(marker)!r}, 'w').write('unsafe')\n")
        with self.assertRaises(ConfigError):
            extract_legacy(source)
        self.assertFalse(marker.exists())

    def test_unsupported_production_setting_is_not_silently_lost(self):
        source = self.legacy()
        with (source / "config.py").open("a") as handle:
            handle.write("\nCUSTOM_RUNTIME_SETTING = 'must not disappear'\n")
        with self.assertRaises(ConfigError):
            extract_legacy(source)

    def test_quotes_save_without_modifying_source(self):
        from config_manager import write_keywords
        before = (ROOT / "config.py").read_bytes()
        saved = write_keywords(self.changed()["settings"]["SEARCH_KEYWORDS"], self.first.token)
        self.assertIn('带"引号\\反斜杠', saved.document["settings"]["SEARCH_KEYWORDS"])
        self.assertEqual((ROOT / "config.py").read_bytes(), before)

    def test_keyword_writer_preserves_prompt_updates_after_process_cached_old_version(self):
        from config_manager import read_keywords, write_keywords
        read_keywords()  # Pin the process read cache before another editor saves.
        candidate = copy.deepcopy(self.document)
        candidate["prompts"]["NEWS_SCORE_SYSTEM_MSG"]["text"] += "\n已保存的生产调整"
        second = self.store.save(candidate, self.first.token, "prompt editor")
        saved = write_keywords(self.changed()["settings"]["SEARCH_KEYWORDS"], second.token)
        self.assertEqual(saved.document["prompts"], second.document["prompts"])

    def test_concurrent_stale_save_is_rejected(self):
        second = self.store.save(self.changed(), self.first.token, "first editor")
        with self.assertRaises(ConfigConflict):
            self.store.save(self.document, self.first.token, "stale editor")
        self.assertEqual(self.store.read().token, second.token)
        self.assertEqual(len(self.store.history()), 2)

    def test_simultaneous_process_writers_only_one_wins(self):
        candidate = self.root / "candidate.json"
        candidate.write_text(json.dumps(self.changed()))
        command = [sys.executable, str(ROOT / "config_cli.py"), "--store", str(self.store.path), "import", "--file", str(candidate), "--expected-version", self.first.token, "--note", "parallel"]
        a = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        b = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        a.communicate(timeout=30)
        b.communicate(timeout=30)
        self.assertEqual(sorted([a.returncode, b.returncode]), [0, 1])
        self.assertEqual(len(self.store.history()), 2)

    def test_save_failure_after_insert_rolls_back_everything(self):
        insert = self.store._insert
        def fail(*args):
            insert(*args)
            raise sqlite3.OperationalError("simulated disk full")
        with patch.object(self.store, "_insert", side_effect=fail), self.assertRaises(ConfigError):
            self.store.save(self.changed(), self.first.token, "must roll back")
        self.assertEqual(self.store.read().token, self.first.token)
        self.assertEqual(len(self.store.history()), 1)

    def test_writer_process_crash_recovers_old_version(self):
        program = """
import os, sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
connection.execute('PRAGMA cache_size=1')
connection.execute('BEGIN IMMEDIATE')
for _ in range(30):
    connection.execute("INSERT INTO revisions(document,sha256,created_at,note,source) SELECT document,sha256,created_at,'interrupted',source FROM revisions WHERE id=1")
connection.execute('UPDATE metadata SET active=2 WHERE id=1')
os._exit(17)
"""
        result = subprocess.run([sys.executable, "-c", program, str(self.store.path)])
        self.assertEqual(result.returncode, 17)
        self.assertEqual(self.store.read().token, self.first.token)

    def test_initialization_is_idempotent_after_later_edits(self):
        latest = self.store.save(self.changed(), self.first.token, "production adjustment")
        current, created = self.store.initialize(self.document, self.source)
        self.assertFalse(created)
        self.assertEqual(current.token, latest.token)
        with self.assertRaises(ConfigConflict):
            self.store.initialize(self.document, {"kind": "different-defaults"})
        self.assertEqual(self.store.read().token, latest.token)

    def test_new_defaults_never_replace_existing_production(self):
        changed_defaults = self.changed()
        changed_defaults["prompts"]["NEW_SYSTEM_PROMPT"] = {"text": "candidate", "status": "archived"}
        with self.assertRaises(ConfigConflict):
            self.store.initialize(changed_defaults, {"kind": "new-default-release"})
        self.assertEqual(self.store.read().document, self.document)

    def test_missing_corrupt_or_tampered_stores_fail_without_defaults(self):
        with self.assertRaises(ConfigError):
            ConfigStore(self.root / "missing.sqlite3").read()
        self.assertFalse((self.root / "missing.sqlite3").exists())
        bad = self.root / "bad.sqlite3"
        bad.write_text("broken")
        with self.assertRaises(ConfigError):
            ConfigStore(bad).read()
        with sqlite3.connect(self.store.path) as connection:
            connection.execute("DROP TRIGGER revisions_no_update")
            connection.execute("UPDATE revisions SET sha256='invalid'")
        with self.assertRaises(ConfigError):
            self.store.read()

    def test_unknown_fields_and_incompatible_templates_rejected(self):
        for mutate in (
            lambda d: d.update(unknown="retain me"),
            lambda d: d["prompts"]["NEWS_SCORE_PROMPT"].update(text="{unexpected}"),
            lambda d: d["prompts"].pop("NEWS_SUMMARY_JUDGE_USER_PROMPT"),
            lambda d: d["models"]["scoring"].update(api_key="secret"),
        ):
            candidate = copy.deepcopy(self.document)
            mutate(candidate)
            with self.assertRaises(ConfigError):
                self.store.save(candidate, self.first.token, "invalid")
        self.assertEqual(self.store.read().token, self.first.token)

    def test_malformed_nested_values_fail_as_configuration_errors(self):
        for mutate in (
            lambda d: d["legacy"].update(selection=None),
            lambda d: d["prompts"].update(NEWS_SCORE_PROMPT=None),
            lambda d: d["models"]["scoring"].update(parameters=[]),
            lambda d: d["settings"]["ICON_MANAGER_CONFIG"].update(unknown=True),
            lambda d: d["prompts"]["NEWS_SCORE_SYSTEM_MSG"].update(text="\ud800"),
        ):
            candidate = copy.deepcopy(self.document)
            mutate(candidate)
            with self.assertRaises(ConfigError):
                self.store.save(candidate, self.first.token, "invalid input")
        self.assertEqual(self.store.read().token, self.first.token)

    def test_failed_initialization_does_not_leave_an_empty_live_store(self):
        other = ConfigStore(self.root / "new-store.sqlite3")
        with patch.object(other, "_insert", side_effect=OSError("write interrupted")), self.assertRaises(OSError):
            other.initialize(self.document, self.source)
        self.assertFalse(other.path.exists())

    def test_export_restore_and_full_history_backup(self):
        second = self.store.save(self.changed(), self.first.token, "change")
        restored = self.store.restore(self.first.token, second.token, "undo")
        self.assertEqual(restored.revision, 3)
        self.assertEqual(restored.document, self.document)
        self.assertEqual(len(self.store.history()), 3)
        target = self.root / "export.json"
        write_export(target, json.dumps(self.store.export()))
        recovered = ConfigStore(self.root / "recovered.sqlite3")
        recovered.initialize(read_document(target), {"kind": "recovery"})
        self.assertEqual(recovered.read().document, self.document)
        backup = self.root / "all-history.sqlite3"
        self.store.backup(backup)
        self.assertEqual(ConfigStore(backup).history(), self.store.history())
        with self.assertRaises(ConfigConflict):
            self.store.backup(backup)
        with self.assertRaises(ConfigConflict):
            write_export(target, "must not overwrite")

    def test_export_checksum_and_duplicate_json_keys(self):
        bundle = self.store.export()
        bundle["document"] = self.changed()
        target = self.root / "tampered.json"
        target.write_text(json.dumps(bundle))
        with self.assertRaises(ConfigError):
            read_document(target)
        with self.assertRaises(ConfigError):
            parse_json('{"value": 1, "value": 2}')

    def test_dry_run_creates_no_store_or_directory(self):
        target = self.root / "not-created" / "config.sqlite3"
        result = subprocess.run([sys.executable, str(ROOT / "config_cli.py"), "--store", str(target), "init", "--defaults", "--dry-run"], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(target.parent.exists())

    def test_order_changes_are_visible_and_retained(self):
        candidate = copy.deepcopy(self.document)
        keywords = candidate["settings"]["SEARCH_KEYWORDS"]
        candidate["settings"]["SEARCH_KEYWORDS"] = dict(reversed(list(keywords.items())))
        self.assertTrue(any(change["path"].endswith(".<order>") for change in differences(self.document, candidate)))
        saved = self.store.save(candidate, self.first.token, "reorder")
        self.assertEqual(list(saved.document["settings"]["SEARCH_KEYWORDS"]), list(reversed(keywords)))

    def test_store_cannot_live_inside_checkout(self):
        with self.assertRaises(ConfigError):
            ConfigStore(ROOT / "output" / "unsafe.sqlite3")
        with self.assertRaises(ConfigError):
            ConfigStore("relative.sqlite3")

    def test_batch_resume_keeps_revision_after_new_active_save(self):
        output = self.root / "output" / "2099-01-01"
        first, _ = self.store.pin_batch(output, "养老")
        self.store.save(self.changed(), first.token, "edit during batch")
        resumed, keywords = self.store.pin_batch(output, "养老")
        self.assertEqual(resumed.token, first.token)
        self.assertEqual(keywords, ["养老"])
        new, _ = self.store.pin_batch(output.parent / "2099-01-02", "养老")
        self.assertNotEqual(new.token, first.token)

    def test_unversioned_outputs_require_explicit_adoption(self):
        output = self.root / "2099-01-01"
        output.mkdir()
        (output / "2099-01-01_养老.json").write_text("[]")
        with self.assertRaises(ConfigConflict):
            self.store.pin_batch(output, "养老")
        pinned, _ = self.store.pin_batch(output, "养老", adopt_existing=True)
        self.assertEqual(pinned.token, self.first.token)
        second = self.store.save(self.changed(), self.first.token, "new")
        with self.assertRaises(ConfigConflict):
            self.store.pin_batch(output, "养老", second.token)

    def test_environment_cannot_reuse_another_store_version(self):
        other = ConfigStore(self.root / "other.sqlite3")
        other.initialize(self.document, self.source)
        with self.assertRaises(ConfigError):
            other.read(self.first.token)

    def test_snapshot_cannot_be_mutated_through_a_returned_dictionary(self):
        self.first.document["prompts"].clear()
        self.assertEqual(len(self.first.document["prompts"]), 22)


if __name__ == "__main__":
    unittest.main()
