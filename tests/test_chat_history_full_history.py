import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from open_llm_vtuber.chat_history_manager import (
    create_new_history,
    get_history,
    get_history_list,
    store_message,
)


class FullHistoryDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.previous_cwd = Path.cwd()
        os.chdir(self.temp_dir.name)
        self.addCleanup(os.chdir, self.previous_cwd)

    def test_legacy_history_is_migrated_and_remains_readable(self):
        character_dir = Path("chat_history") / "algernon"
        character_dir.mkdir(parents=True)
        legacy_path = character_dir / "legacy-history.json"
        legacy_path.write_text(
            json.dumps(
                [
                    {"role": "metadata", "timestamp": "2026-08-10T20:00:00"},
                    {
                        "role": "human",
                        "timestamp": "2026-08-10T20:00:01",
                        "content": "hello",
                    },
                ]
            ),
            encoding="utf-8",
        )

        histories = get_history_list("algernon")

        migrated_path = character_dir / "full_history" / legacy_path.name
        self.assertFalse(legacy_path.exists())
        self.assertTrue(migrated_path.exists())
        self.assertEqual(histories[0]["uid"], "legacy-history")
        self.assertEqual(get_history("algernon", "legacy-history")[0]["content"], "hello")

    def test_new_history_and_messages_are_stored_in_full_history(self):
        history_uid = create_new_history("mili")
        store_message("mili", history_uid, "human", "hello")

        expected_path = (
            Path("chat_history")
            / "mili"
            / "full_history"
            / f"{history_uid}.json"
        )
        self.assertTrue(expected_path.exists())
        self.assertEqual(get_history("mili", history_uid)[0]["content"], "hello")


if __name__ == "__main__":
    unittest.main()
