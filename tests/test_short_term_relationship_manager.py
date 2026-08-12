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
from open_llm_vtuber.short_term_relationship_manager import (
    SHORT_TERM_RELATIONSHIP_METADATA_KEY,
    ShortTermRelationshipManager,
)


class ShortTermRelationshipParsingTests(unittest.TestCase):
    def setUp(self):
        self.manager = ShortTermRelationshipManager()

    def test_parse_valid_json_fence(self):
        raw = """```json
        {"short_term_relationship": "之前有些争执，最近关系正在缓和。"}
        ```"""

        self.assertEqual(
            self.manager.parse_summary(raw),
            "之前有些争执，最近关系正在缓和。",
        )

    def test_parse_enforces_seventy_character_value_limit(self):
        exact = json.dumps(
            {"short_term_relationship": "近" * 70},
            ensure_ascii=False,
        )
        too_long = json.dumps(
            {"short_term_relationship": "近" * 71},
            ensure_ascii=False,
        )

        self.assertEqual(len(self.manager.parse_summary(exact)), 70)
        with self.assertRaisesRegex(ValueError, "must not exceed 70"):
            self.manager.parse_summary(too_long)

    def test_parse_rejects_extra_fields_and_non_string_value(self):
        with self.assertRaisesRegex(ValueError, "contain only"):
            self.manager.parse_summary(
                '{"short_term_relationship":"升温中","mood":"开心"}'
            )
        with self.assertRaisesRegex(ValueError, "must be a string"):
            self.manager.parse_summary('{"short_term_relationship": null}')


class ShortTermRelationshipLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.long_relationship_path = root / "long_term_relationship.md"
        self.short_relationship_path = root / "short_term_relationship.md"
        self.manager = ShortTermRelationshipManager(
            relationship_path=self.short_relationship_path,
            long_term_relationship_path=self.long_relationship_path,
        )
        self.metadata_store = {}

    def fake_get_metadata(self, conf_uid, history_uid):
        return copy.deepcopy(self.metadata_store)

    def fake_update_metadata(self, conf_uid, history_uid, metadata):
        self.metadata_store.update(copy.deepcopy(metadata))
        return True

    async def test_fourth_turn_uses_latest_chats_and_both_complete_files(self):
        long_file = '{"long_term_relationship":"双方是逐渐熟悉的朋友。"}\n'
        old_short_file = '{"short_term_relationship":"最近互动平稳。"}\n'
        self.long_relationship_path.write_text(long_file, encoding="utf-8")
        self.short_relationship_path.write_text(old_short_file, encoding="utf-8")
        calls = []

        async def summarize(turns, long_relationship, short_relationship):
            calls.append((turns, long_relationship, short_relationship))
            return '{"short_term_relationship":"最近交流增多，关系正在升温。"}'

        module = "open_llm_vtuber.short_term_relationship_manager"
        with patch(f"{module}.get_metadata", self.fake_get_metadata), patch(
            f"{module}.update_metadate", self.fake_update_metadata
        ):
            for index in range(1, 4):
                self.assertFalse(
                    await self.manager.record_completed_turn(
                        "角色",
                        "会话",
                        f"用户{index}",
                        f"回答{index}",
                        summarize,
                    )
                )
            self.assertTrue(
                await self.manager.record_completed_turn(
                    "角色", "会话", "用户4", "回答4", summarize
                )
            )

        self.assertEqual(
            calls[0][0],
            [
                {"user": f"用户{index}", "assistant": f"回答{index}"}
                for index in range(1, 5)
            ],
        )
        self.assertEqual(calls[0][1], long_file)
        self.assertEqual(calls[0][2], old_short_file)
        self.assertEqual(
            json.loads(self.short_relationship_path.read_text(encoding="utf-8")),
            {"short_term_relationship": "最近交流增多，关系正在升温。"},
        )
        state = self.metadata_store[SHORT_TERM_RELATIONSHIP_METADATA_KEY]
        self.assertEqual(state["pending_turns"], [])

    async def test_failure_retries_with_latest_four_turns(self):
        calls = []

        async def summarize(turns, long_relationship, short_relationship):
            calls.append(copy.deepcopy(turns))
            if len(calls) == 1:
                return "not json"
            return '{"short_term_relationship":"最近关系趋于平稳。"}'

        module = "open_llm_vtuber.short_term_relationship_manager"
        with patch(f"{module}.get_metadata", self.fake_get_metadata), patch(
            f"{module}.update_metadate", self.fake_update_metadata
        ):
            for index in range(1, 5):
                result = await self.manager.record_completed_turn(
                    "角色",
                    "会话",
                    f"用户{index}",
                    f"回答{index}",
                    summarize,
                )
            self.assertFalse(result)
            self.assertTrue(
                await self.manager.record_completed_turn(
                    "角色", "会话", "用户5", "回答5", summarize
                )
            )

        self.assertEqual(
            [turn["user"] for turn in calls[0]],
            ["用户1", "用户2", "用户3", "用户4"],
        )
        self.assertEqual(
            [turn["user"] for turn in calls[1]],
            ["用户2", "用户3", "用户4", "用户5"],
        )
        self.assertEqual(
            self.metadata_store[SHORT_TERM_RELATIONSHIP_METADATA_KEY][
                "pending_turns"
            ],
            [],
        )

    async def test_injects_complete_json_on_first_and_every_four_turns(self):
        short_file = (
            '{\n  "short_term_relationship": "最近关系正在升温。"\n}\n'
        )
        self.short_relationship_path.write_text(short_file, encoding="utf-8")

        module = "open_llm_vtuber.short_term_relationship_manager"
        with patch(f"{module}.get_metadata", self.fake_get_metadata), patch(
            f"{module}.update_metadate", self.fake_update_metadata
        ):
            injections = [
                await self.manager.consume_injection(
                    "角色", "会话", turn_number=turn_number
                )
                for turn_number in range(1, 10)
            ]

        self.assertIn(short_file.rstrip(), injections[0])
        self.assertTrue(all(not value for value in injections[1:4]))
        self.assertIn(short_file.rstrip(), injections[4])
        self.assertTrue(all(not value for value in injections[5:8]))
        self.assertIn(short_file.rstrip(), injections[8])


class ShortRelationshipPromptInjectionTests(unittest.TestCase):
    def test_all_hidden_contexts_are_sent_and_saved_as_hidden_snapshots(self):
        agent = object.__new__(BasicMemoryAgent)
        agent._memory = []
        batch_input = BatchInput(
            texts=[TextData(TextSource.INPUT, "我们最近怎么样？", "Human")],
            metadata={
                "long_term_memory_context": "[长期记忆]\n- 用户喜欢咖啡。",
                "long_term_relationship_context": (
                    '[长期关系]\n{"long_term_relationship":"双方是朋友。"}'
                ),
                "short_term_relationship_context": (
                    '[短期关系]\n{"short_term_relationship":"最近正在升温。"}'
                ),
            },
        )

        messages = agent._to_messages(batch_input)

        request_text = messages[-1]["content"][0]["text"]
        self.assertIn("用户喜欢咖啡", request_text)
        self.assertIn("双方是朋友", request_text)
        self.assertIn("最近正在升温", request_text)
        self.assertIn("我们最近怎么样", request_text)
        self.assertEqual(
            agent._memory,
            [
                {
                    "role": "user",
                    "content": "我们最近怎么样？",
                    "context_injections": {
                        "long_term_memory_context": "[长期记忆]\n- 用户喜欢咖啡。",
                        "long_term_relationship_context": (
                            '[长期关系]\n{"long_term_relationship":"双方是朋友。"}'
                        ),
                        "short_term_relationship_context": (
                            '[短期关系]\n{"short_term_relationship":"最近正在升温。"}'
                        ),
                    },
                }
            ],
        )


class ShortRelationshipSummaryModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_uses_pro_client_and_sends_all_inputs(self):
        class FakeSummaryLLM:
            def __init__(self):
                self.calls = []

            async def chat_completion(self, messages, system):
                self.calls.append((messages, system))
                yield '{"short_term_relationship":"最近关系有所缓和。"}'

        class FailingChatLLM:
            async def chat_completion(self, messages, system):
                raise AssertionError("The normal chat LLM must not summarize")
                yield ""

        summary_llm = FakeSummaryLLM()
        agent = object.__new__(BasicMemoryAgent)
        agent._llm = FailingChatLLM()
        agent._summary_llm = summary_llm
        turns = [
            {"user": f"用户{index}", "assistant": f"回答{index}"}
            for index in range(1, 5)
        ]

        result = await agent.summarize_short_term_relationship(
            turns,
            "长期关系完整内容",
            "旧短期关系完整内容",
        )

        self.assertEqual(
            result,
            '{"short_term_relationship":"最近关系有所缓和。"}',
        )
        self.assertEqual(len(summary_llm.calls), 1)
        payload = json.loads(summary_llm.calls[0][0][0]["content"])
        self.assertEqual(payload["最近四轮聊天记录"], turns)
        self.assertEqual(
            payload["long_term_relationship.md全部内容"],
            "长期关系完整内容",
        )
        self.assertEqual(
            payload["现有short_term_relationship.md全部内容"],
            "旧短期关系完整内容",
        )
        self.assertIn("不超过70个字符", summary_llm.calls[0][1])


if __name__ == "__main__":
    unittest.main()
