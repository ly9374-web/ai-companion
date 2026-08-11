import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from open_llm_vtuber.chat_history_manager import get_character_history_dir
from open_llm_vtuber.long_term_memory_manager import (
    LongTermMemory,
    LongTermMemoryManager,
)
from open_llm_vtuber.long_term_relationship_manager import (
    LongTermRelationshipManager,
)
from open_llm_vtuber.short_term_relationship_manager import (
    ShortTermRelationshipManager,
)


ALGERNON = "algernon_default_001"
MILI = "mili_preset_001"


class CharacterHistoryPathTests(unittest.TestCase):
    def test_character_directory_is_scoped_below_history_root(self):
        root = Path("custom_history")

        self.assertEqual(
            get_character_history_dir(ALGERNON, root),
            root / ALGERNON,
        )
        with self.assertRaises(ValueError):
            get_character_history_dir("..", root)


class CharacterMemoryIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.history_root = Path(self.temp_dir.name)
        self.metadata_store = {}

    def fake_get_metadata(self, conf_uid, history_uid):
        key = (conf_uid, history_uid)
        return copy.deepcopy(self.metadata_store.get(key, {}))

    def fake_update_metadata(self, conf_uid, history_uid, metadata):
        key = (conf_uid, history_uid)
        self.metadata_store.setdefault(key, {}).update(copy.deepcopy(metadata))
        return True

    async def test_long_term_memories_are_written_to_separate_role_files(self):
        manager = LongTermMemoryManager(history_root=self.history_root)

        await manager.store_memories(
            [LongTermMemory("A记忆", "Algernon 的独立记忆。")],
            ALGERNON,
        )
        await manager.store_memories(
            [LongTermMemory("M记忆", "Mili 的独立记忆。")],
            MILI,
        )

        self.assertEqual(
            await manager.read_memories(ALGERNON),
            [LongTermMemory("A记忆", "Algernon 的独立记忆。")],
        )
        self.assertEqual(
            await manager.read_memories(MILI),
            [LongTermMemory("M记忆", "Mili 的独立记忆。")],
        )
        self.assertNotEqual(
            (self.history_root / ALGERNON / "long_term_memory.md").read_text(
                encoding="utf-8"
            ),
            (self.history_root / MILI / "long_term_memory.md").read_text(
                encoding="utf-8"
            ),
        )

    async def test_long_relationship_update_and_injection_are_role_scoped(self):
        for conf_uid, content in (
            (ALGERNON, "Algernon 的长期记忆"),
            (MILI, "Mili 的长期记忆"),
        ):
            role_dir = self.history_root / conf_uid
            role_dir.mkdir(parents=True)
            (role_dir / "long_term_memory.md").write_text(
                content,
                encoding="utf-8",
            )

        manager = LongTermRelationshipManager(
            history_root=self.history_root,
            update_interval=1,
            injection_interval=1,
        )

        async def summarize(memory_file, existing_relationship_file):
            role = "Algernon" if "Algernon" in memory_file else "Mili"
            return json.dumps(
                {"long_term_relationship": f"用户与 {role} 的长期关系。"},
                ensure_ascii=False,
            )

        module = "open_llm_vtuber.long_term_relationship_manager"
        with patch(f"{module}.get_metadata", self.fake_get_metadata), patch(
            f"{module}.update_metadate", self.fake_update_metadata
        ):
            self.assertTrue(
                await manager.record_completed_turn(ALGERNON, "same_history", summarize)
            )
            self.assertTrue(
                await manager.record_completed_turn(MILI, "same_history", summarize)
            )
            algernon_injection = await manager.consume_injection(
                ALGERNON, "same_history"
            )
            mili_injection = await manager.consume_injection(MILI, "same_history")

        self.assertIn("Algernon", algernon_injection)
        self.assertNotIn("Mili", algernon_injection)
        self.assertIn("Mili", mili_injection)
        self.assertNotIn("Algernon", mili_injection)

    async def test_short_relationship_inputs_and_outputs_are_role_scoped(self):
        for conf_uid, role in ((ALGERNON, "Algernon"), (MILI, "Mili")):
            role_dir = self.history_root / conf_uid
            role_dir.mkdir(parents=True)
            (role_dir / "long_term_relationship.md").write_text(
                json.dumps(
                    {"long_term_relationship": f"用户与 {role} 的长期关系。"},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (role_dir / "short_term_relationship.md").write_text(
                json.dumps(
                    {"short_term_relationship": f"用户与 {role} 的旧短期关系。"},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

        manager = ShortTermRelationshipManager(
            history_root=self.history_root,
            update_interval=1,
            injection_interval=1,
        )

        async def summarize(turns, long_relationship, short_relationship):
            self.assertEqual(len(turns), 1)
            if "Algernon" in long_relationship:
                self.assertIn("Algernon", short_relationship)
                role = "Algernon"
            else:
                self.assertIn("Mili", long_relationship)
                self.assertIn("Mili", short_relationship)
                role = "Mili"
            return json.dumps(
                {"short_term_relationship": f"用户与 {role} 的新短期关系。"},
                ensure_ascii=False,
            )

        module = "open_llm_vtuber.short_term_relationship_manager"
        with patch(f"{module}.get_metadata", self.fake_get_metadata), patch(
            f"{module}.update_metadate", self.fake_update_metadata
        ):
            self.assertTrue(
                await manager.record_completed_turn(
                    ALGERNON, "same_history", "A 用户", "A 回答", summarize
                )
            )
            self.assertTrue(
                await manager.record_completed_turn(
                    MILI, "same_history", "M 用户", "M 回答", summarize
                )
            )
            algernon_injection = await manager.consume_injection(
                ALGERNON, "same_history"
            )
            mili_injection = await manager.consume_injection(MILI, "same_history")

        self.assertIn("Algernon", algernon_injection)
        self.assertNotIn("Mili", algernon_injection)
        self.assertIn("Mili", mili_injection)
        self.assertNotIn("Algernon", mili_injection)


if __name__ == "__main__":
    unittest.main()
