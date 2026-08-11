import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from open_llm_vtuber.agent.agents.basic_memory_agent import BasicMemoryAgent
from open_llm_vtuber.agent.input_types import BatchInput, TextData, TextSource
from open_llm_vtuber.long_term_memory_manager import (
    LONG_TERM_MEMORY_METADATA_KEY,
    LongTermMemory,
    LongTermMemoryManager,
)


class LongTermMemoryParsingTests(unittest.TestCase):
    def test_parse_valid_json_fence(self):
        raw = """```json
        {
          "长期记忆数": 1,
          "长期记忆": [
            {"记忆命名": "饮食偏好", "记忆内容": "用户不喜欢香菜。"}
          ]
        }
        ```"""

        self.assertEqual(
            LongTermMemoryManager.parse_summary(raw),
            [LongTermMemory(name="饮食偏好", content="用户不喜欢香菜。")],
        )

    def test_parse_ignores_count_mismatch(self):
        raw = json.dumps(
            {
                "长期记忆数": 2,
                "长期记忆": [
                    {"记忆命名": "用户职业", "记忆内容": "用户是设计师。"}
                ],
            },
            ensure_ascii=False,
        )

        self.assertEqual(
            LongTermMemoryManager.parse_summary(raw),
            [LongTermMemory(name="用户职业", content="用户是设计师。")],
        )

    def test_parse_accepts_output_without_count(self):
        raw = json.dumps(
            {
                "长期记忆": [
                    {"记忆命名": "饮食偏好", "记忆内容": "用户不喜欢香菜。"}
                ]
            },
            ensure_ascii=False,
        )

        self.assertEqual(
            LongTermMemoryManager.parse_summary(raw),
            [LongTermMemory(name="饮食偏好", content="用户不喜欢香菜。")],
        )

    def test_parse_rejects_name_with_eight_characters(self):
        raw = json.dumps(
            {
                "长期记忆数": 1,
                "长期记忆": [
                    {"记忆命名": "一二三四五六七八", "记忆内容": "内容"}
                ],
            },
            ensure_ascii=False,
        )

        with self.assertRaisesRegex(ValueError, "fewer than 8"):
            LongTermMemoryManager.parse_summary(raw)


class LongTermMemoryStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.memory_path = Path(self.temp_dir.name) / "long_term_memory.md"

    async def test_upsert_and_limit(self):
        manager = LongTermMemoryManager(self.memory_path, max_memories=3)
        await manager.store_memories(
            [
                LongTermMemory("记忆一", "内容一"),
                LongTermMemory("记忆二", "内容二"),
                LongTermMemory("记忆三", "内容三"),
            ]
        )
        await manager.store_memories(
            [
                LongTermMemory("记忆二", "更新内容"),
                LongTermMemory("记忆四", "内容四"),
            ]
        )

        self.assertEqual(
            await manager.read_memories(),
            [
                LongTermMemory("记忆二", "更新内容"),
                LongTermMemory("记忆四", "内容四"),
                LongTermMemory("记忆一", "内容一"),
            ],
        )
        memory_file = self.memory_path.read_text("utf-8")
        self.assertNotIn("长期记忆数", memory_file)
        self.assertLess(memory_file.index("记忆二"), memory_file.index("记忆一"))

    async def test_default_storage_keeps_all_memories(self):
        manager = LongTermMemoryManager(self.memory_path)
        await manager.store_memories(
            [LongTermMemory(f"记忆{index}", f"内容{index}") for index in range(101)]
        )

        memories = await manager.read_memories()
        self.assertEqual(len(memories), 101)
        self.assertEqual(memories[0], LongTermMemory("记忆0", "内容0"))
        self.assertEqual(memories[-1], LongTermMemory("记忆100", "内容100"))

    async def test_three_turn_trigger_and_one_time_injection(self):
        manager = LongTermMemoryManager(self.memory_path)
        metadata_store = {}
        summary_calls = []

        def fake_get_metadata(conf_uid, history_uid):
            return copy.deepcopy(metadata_store)

        def fake_update_metadata(conf_uid, history_uid, metadata):
            metadata_store.update(copy.deepcopy(metadata))
            return True

        async def summarize(turns, existing_memories):
            summary_calls.append((turns, existing_memories))
            return json.dumps(
                {
                    "长期记忆数": 1,
                    "长期记忆": [
                        {
                            "记忆命名": "用户职业",
                            "记忆内容": "用户从事产品设计工作。",
                        }
                    ],
                },
                ensure_ascii=False,
            )

        module = "open_llm_vtuber.long_term_memory_manager"
        with patch(f"{module}.get_metadata", fake_get_metadata), patch(
            f"{module}.update_metadate", fake_update_metadata
        ):
            self.assertFalse(
                await manager.record_completed_turn("角色", "会话", "用户1", "回答1", summarize)
            )
            self.assertFalse(
                await manager.record_completed_turn("角色", "会话", "用户2", "回答2", summarize)
            )
            self.assertTrue(
                await manager.record_completed_turn("角色", "会话", "用户3", "回答3", summarize)
            )

            self.assertEqual(len(summary_calls), 1)
            self.assertEqual(len(summary_calls[0][0]), 3)
            state = metadata_store[LONG_TERM_MEMORY_METADATA_KEY]
            self.assertEqual(state["pending_turns"], [])
            self.assertTrue(state["inject_next_turn"])

            injection = await manager.consume_injection("角色", "会话")
            self.assertIn("用户职业：用户从事产品设计工作。", injection)
            self.assertEqual(await manager.consume_injection("角色", "会话"), "")
            self.assertFalse(
                metadata_store[LONG_TERM_MEMORY_METADATA_KEY]["inject_next_turn"]
            )

    async def test_invalid_summary_keeps_pending_turns(self):
        manager = LongTermMemoryManager(self.memory_path)
        metadata_store = {}

        def fake_get_metadata(conf_uid, history_uid):
            return copy.deepcopy(metadata_store)

        def fake_update_metadata(conf_uid, history_uid, metadata):
            metadata_store.update(copy.deepcopy(metadata))
            return True

        async def summarize(turns, existing_memories):
            return "not json"

        module = "open_llm_vtuber.long_term_memory_manager"
        with patch(f"{module}.get_metadata", fake_get_metadata), patch(
            f"{module}.update_metadate", fake_update_metadata
        ):
            for index in range(3):
                result = await manager.record_completed_turn(
                    "角色", "会话", f"用户{index}", f"回答{index}", summarize
                )

        self.assertFalse(result)
        state = metadata_store[LONG_TERM_MEMORY_METADATA_KEY]
        self.assertEqual(len(state["pending_turns"]), 3)
        self.assertFalse(state["inject_next_turn"])

    async def test_zero_memories_still_advances_batch_and_marks_next_turn(self):
        manager = LongTermMemoryManager(self.memory_path)
        metadata_store = {}

        def fake_get_metadata(conf_uid, history_uid):
            return copy.deepcopy(metadata_store)

        def fake_update_metadata(conf_uid, history_uid, metadata):
            metadata_store.update(copy.deepcopy(metadata))
            return True

        async def summarize(turns, existing_memories):
            return '{"长期记忆数": 0, "长期记忆": []}'

        module = "open_llm_vtuber.long_term_memory_manager"
        with patch(f"{module}.get_metadata", fake_get_metadata), patch(
            f"{module}.update_metadate", fake_update_metadata
        ):
            for index in range(3):
                result = await manager.record_completed_turn(
                    "角色", "会话", f"用户{index}", f"回答{index}", summarize
                )

            self.assertTrue(result)
            state = metadata_store[LONG_TERM_MEMORY_METADATA_KEY]
            self.assertEqual(state["pending_turns"], [])
            self.assertTrue(state["inject_next_turn"])
            self.assertEqual(await manager.consume_injection("角色", "会话"), "")
            self.assertFalse(
                metadata_store[LONG_TERM_MEMORY_METADATA_KEY]["inject_next_turn"]
            )
        self.assertEqual(self.memory_path.read_text("utf-8"), "")


class PromptInjectionTests(unittest.TestCase):
    def test_hidden_context_is_sent_but_not_added_to_short_memory(self):
        agent = object.__new__(BasicMemoryAgent)
        agent._memory = []
        batch_input = BatchInput(
            texts=[TextData(TextSource.INPUT, "今天吃什么？", "Human")],
            metadata={
                "long_term_memory_context": "[长期记忆]\n- 饮食偏好：用户不喜欢香菜。"
            },
        )

        messages = agent._to_messages(batch_input)

        request_text = messages[-1]["content"][0]["text"]
        self.assertIn("饮食偏好", request_text)
        self.assertIn("今天吃什么？", request_text)
        self.assertEqual(
            agent._memory,
            [{"role": "user", "content": "今天吃什么？"}],
        )


if __name__ == "__main__":
    unittest.main()
