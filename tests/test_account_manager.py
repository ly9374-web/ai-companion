import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from open_llm_vtuber import account_manager
from open_llm_vtuber.chat_history_manager import (
    create_new_history,
    get_history_list,
    store_message,
)


class AccountManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_cwd = Path.cwd()
        os.chdir(self.temp_dir.name)
        self.conf_uids = ["algernon_default_001", "cuige_preset_001"]
        self.uid_patch = patch.object(
            account_manager,
            "get_character_conf_uids",
            return_value=self.conf_uids,
        )
        self.uid_patch.start()

    def tearDown(self):
        self.uid_patch.stop()
        os.chdir(self.previous_cwd)
        self.temp_dir.cleanup()

    def test_register_creates_complete_role_profiles_and_resolves_casefold(self):
        canonical = account_manager.register_account("  Alice  ")

        self.assertEqual(canonical, "Alice")
        self.assertEqual(account_manager.resolve_account_name("aLiCe"), "Alice")
        for conf_uid in self.conf_uids:
            role_dir = Path("chat_history") / "Alice" / conf_uid
            self.assertTrue((role_dir / "full_history").is_dir())
            self.assertEqual(
                (role_dir / "long_term_memory.md").read_text(encoding="utf-8"),
                "",
            )
            self.assertEqual(
                json.loads(
                    (role_dir / "long_term_relationship.md").read_text(
                        encoding="utf-8"
                    )
                ),
                {"long_term_relationship": "暂无"},
            )
            short_relationship = json.loads(
                (role_dir / "short_term_relationship.md").read_text(
                    encoding="utf-8"
                )
            )["short_term_relationship"]
            self.assertIn("这是你第一次见到这位用户", short_relationship)

        with self.assertRaises(account_manager.AccountAlreadyExists):
            account_manager.register_account("ALICE")

    def test_rejects_unsafe_account_names(self):
        for name in (
            "",
            ".",
            "..",
            ".hidden",
            "a/b",
            "a\\b",
            "bad\x00name",
            "CON",
            "com1.txt",
        ):
            with self.subTest(name=name):
                with self.assertRaises(account_manager.InvalidAccountName):
                    account_manager.register_account(name)

    def test_role_uids_are_reserved_account_names(self):
        with self.assertRaises(account_manager.InvalidAccountName):
            account_manager.register_account("ALGERNON_DEFAULT_001")

    def test_failed_registration_never_removes_a_preexisting_directory(self):
        existing = Path("chat_history") / "Taken"
        existing.mkdir(parents=True)
        marker = existing / "keep.txt"
        marker.write_text("important", encoding="utf-8")

        with self.assertRaises(account_manager.AccountAlreadyExists):
            account_manager.register_account("Taken")

        self.assertEqual(marker.read_text(encoding="utf-8"), "important")

    def test_migration_conflict_is_preserved_without_blocking_login(self):
        legacy = Path("chat_history") / self.conf_uids[0]
        legacy.mkdir(parents=True)
        (legacy / "long_term_memory.md").write_text("legacy", encoding="utf-8")
        active = Path("chat_history") / "Jason" / self.conf_uids[0]
        active.mkdir(parents=True)
        (active / "long_term_memory.md").write_text("active", encoding="utf-8")

        self.assertEqual(account_manager.resolve_account_name("Jason"), "Jason")
        conflicts = list(
            (Path("chat_history") / ".migration-conflicts").glob(
                f"{self.conf_uids[0]}-*"
            )
        )
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(
            (conflicts[0] / "long_term_memory.md").read_text(encoding="utf-8"),
            "legacy",
        )
        self.assertEqual(
            (active / "long_term_memory.md").read_text(encoding="utf-8"),
            "active",
        )

    def test_legacy_role_directories_move_below_jason(self):
        legacy_dir = Path("chat_history") / self.conf_uids[0]
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "long_term_memory.md").write_text(
            "Jason memory",
            encoding="utf-8",
        )

        self.assertEqual(account_manager.resolve_account_name("jAsOn"), "Jason")
        migrated_file = (
            Path("chat_history")
            / "Jason"
            / self.conf_uids[0]
            / "long_term_memory.md"
        )
        self.assertEqual(migrated_file.read_text(encoding="utf-8"), "Jason memory")
        self.assertFalse(legacy_dir.exists())

    def test_default_jason_account_exists_on_a_fresh_install(self):
        self.assertEqual(account_manager.resolve_account_name("JASON"), "Jason")
        self.assertTrue((Path("chat_history") / "Jason").is_dir())

    def test_chat_histories_are_isolated_by_account_root(self):
        account_manager.register_account("Alice")
        account_manager.register_account("Bob")
        alice_root = Path("chat_history") / "Alice"
        bob_root = Path("chat_history") / "Bob"
        alice_history = create_new_history(self.conf_uids[0], alice_root)
        bob_history = create_new_history(self.conf_uids[0], bob_root)
        store_message(
            self.conf_uids[0],
            alice_history,
            "human",
            "Alice only",
            history_root=alice_root,
        )
        store_message(
            self.conf_uids[0],
            bob_history,
            "human",
            "Bob only",
            history_root=bob_root,
        )

        alice_list = get_history_list(self.conf_uids[0], alice_root)
        bob_list = get_history_list(self.conf_uids[0], bob_root)
        self.assertEqual(alice_list[0]["latest_message"]["content"], "Alice only")
        self.assertEqual(bob_list[0]["latest_message"]["content"], "Bob only")


if __name__ == "__main__":
    unittest.main()
