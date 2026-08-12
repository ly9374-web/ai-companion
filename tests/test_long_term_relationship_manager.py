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
from open_llm_vtuber.agent.agent_factory import AgentFactory
from open_llm_vtuber.agent.input_types import BatchInput, TextData, TextSource
from open_llm_vtuber.long_term_relationship_manager import (
    LONG_TERM_RELATIONSHIP_METADATA_KEY,
    LongTermRelationshipManager,
)
from open_llm_vtuber.long_term_memory_manager import LongTermMemory


class LongTermRelationshipParsingTests(unittest.TestCase):
    def setUp(self):
        self.manager = LongTermRelationshipManager()

    def test_parse_valid_json_fence(self):
        raw = """```json
        {"long_term_relationship": "用户与角色正在建立信任。"}
        ```"""

        self.assertEqual(
            self.manager.parse_summary(raw),
            "用户与角色正在建立信任。",
        )

    def test_parse_rejects_more_than_two_hundred_characters(self):
        raw = json.dumps(
            {"long_term_relationship": "关" * 201},
            ensure_ascii=False,
        )

        with self.assertRaisesRegex(ValueError, "must not exceed 200"):
            self.manager.parse_summary(raw)

    def test_parse_allows_exactly_two_hundred_characters(self):
        raw = json.dumps(
            {"long_term_relationship": "关" * 200},
            ensure_ascii=False,
        )

        self.assertEqual(len(self.manager.parse_summary(raw)), 200)

    def test_parse_rejects_extra_fields_and_non_string_value(self):
        with self.assertRaisesRegex(ValueError, "contain only"):
            self.manager.parse_summary(
                '{"long_term_relationship": "朋友", "confidence": 1}'
            )
        with self.assertRaisesRegex(ValueError, "must be a string"):
            self.manager.parse_summary('{"long_term_relationship": ["朋友"]}')


class LongTermRelationshipLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.memory_path = root / "long_term_memory.md"
        self.relationship_path = root / "long_term_relationship.md"
        self.manager = LongTermRelationshipManager(
            relationship_path=self.relationship_path,
            memory_path=self.memory_path,
        )
        self.metadata_store = {}

    def fake_get_metadata(self, conf_uid, history_uid):
        return copy.deepcopy(self.metadata_store)

    def fake_update_metadata(self, conf_uid, history_uid, metadata):
        self.metadata_store.update(copy.deepcopy(metadata))
        return True

    async def test_updates_on_fourth_turn_with_complete_file_inputs(self):
        memory_file = "长期记忆数：1\n记忆命名：称呼偏好\n记忆内容：喜欢被叫小明。\n---\n"
        self.memory_path.write_text(memory_file, encoding="utf-8")
        calls = []

        async def summarize(long_term_memory_file, existing_relationship_file):
            calls.append((long_term_memory_file, existing_relationship_file))
            return '{"long_term_relationship": "用户与角色逐渐熟悉，正在建立信任。"}'

        module = "open_llm_vtuber.long_term_relationship_manager"
        with patch(f"{module}.get_metadata", self.fake_get_metadata), patch(
            f"{module}.update_metadate", self.fake_update_metadata
        ):
            for _ in range(3):
                self.assertFalse(
                    await self.manager.record_completed_turn(
                        "角色", "会话", summarize
                    )
                )
            self.assertTrue(
                await self.manager.record_completed_turn("角色", "会话", summarize)
            )

        self.assertEqual(calls, [(memory_file, "")])
        self.assertEqual(
            json.loads(self.relationship_path.read_text(encoding="utf-8")),
            {"long_term_relationship": "用户与角色逐渐熟悉，正在建立信任。"},
        )
        state = self.metadata_store[LONG_TERM_RELATIONSHIP_METADATA_KEY]
        self.assertEqual(state["pending_update_turns"], 0)

    async def test_failed_fourth_turn_retries_without_shifting_schedule(self):
        call_count = 0

        async def summarize(long_term_memory_file, existing_relationship_file):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "not json"
            return '{"long_term_relationship": "关系尚在建立中。"}'

        module = "open_llm_vtuber.long_term_relationship_manager"
        with patch(f"{module}.get_metadata", self.fake_get_metadata), patch(
            f"{module}.update_metadate", self.fake_update_metadata
        ):
            for _ in range(4):
                result = await self.manager.record_completed_turn(
                    "角色", "会话", summarize
                )
            self.assertFalse(result)
            self.assertEqual(
                self.metadata_store[LONG_TERM_RELATIONSHIP_METADATA_KEY][
                    "pending_update_turns"
                ],
                4,
            )

            self.assertTrue(
                await self.manager.record_completed_turn("角色", "会话", summarize)
            )
            self.assertEqual(
                self.metadata_store[LONG_TERM_RELATIONSHIP_METADATA_KEY][
                    "pending_update_turns"
                ],
                1,
            )

            for _ in range(2):
                self.assertFalse(
                    await self.manager.record_completed_turn(
                        "角色", "会话", summarize
                    )
                )
            self.assertTrue(
                await self.manager.record_completed_turn("角色", "会话", summarize)
            )

        self.assertEqual(call_count, 3)

    async def test_injects_complete_json_on_first_and_every_five_turns(self):
        relationship_file = (
            '{\n  "long_term_relationship": "用户与角色是逐渐熟悉的朋友。"\n}\n'
        )
        self.relationship_path.write_text(relationship_file, encoding="utf-8")

        module = "open_llm_vtuber.long_term_relationship_manager"
        with patch(f"{module}.get_metadata", self.fake_get_metadata), patch(
            f"{module}.update_metadate", self.fake_update_metadata
        ):
            injections = [
                await self.manager.consume_injection(
                    "角色", "会话", turn_number=turn_number
                )
                for turn_number in range(1, 12)
            ]

        self.assertIn(relationship_file.rstrip(), injections[0])
        self.assertTrue(all(not value for value in injections[1:5]))
        self.assertIn(relationship_file.rstrip(), injections[5])
        self.assertTrue(all(not value for value in injections[6:10]))
        self.assertIn(relationship_file.rstrip(), injections[10])


class RelationshipPromptInjectionTests(unittest.TestCase):
    def test_both_hidden_contexts_are_sent_and_saved_as_hidden_snapshots(self):
        agent = object.__new__(BasicMemoryAgent)
        agent._memory = []
        batch_input = BatchInput(
            texts=[TextData(TextSource.INPUT, "我们熟悉吗？", "Human")],
            metadata={
                "long_term_memory_context": "[长期记忆]\n- 用户喜欢咖啡。",
                "long_term_relationship_context": (
                    '[长期关系]\n{"long_term_relationship":"正在建立信任。"}'
                ),
            },
        )

        messages = agent._to_messages(batch_input)

        request_text = messages[-1]["content"][0]["text"]
        self.assertIn("用户喜欢咖啡", request_text)
        self.assertIn("正在建立信任", request_text)
        self.assertIn("我们熟悉吗", request_text)
        self.assertEqual(
            agent._memory,
            [
                {
                    "role": "user",
                    "content": "我们熟悉吗？",
                    "context_injections": {
                        "long_term_memory_context": "[长期记忆]\n- 用户喜欢咖啡。",
                        "long_term_relationship_context": (
                            '[长期关系]\n{"long_term_relationship":"正在建立信任。"}'
                        ),
                    },
                }
            ],
        )


class SummaryModelRoutingTests(unittest.IsolatedAsyncioTestCase):
    def test_agent_factory_keeps_chat_on_flash_and_summaries_on_pro(self):
        created_models = []

        class FakeLLM:
            pass

        def create_llm(**kwargs):
            created_models.append(kwargs["model"])
            return FakeLLM()

        module = "open_llm_vtuber.agent.agent_factory.StatelessLLMFactory"
        with patch(f"{module}.create_llm", create_llm):
            agent = AgentFactory.create_agent(
                conversation_agent_choice="basic_memory_agent",
                agent_settings={
                    "basic_memory_agent": {
                        "llm_provider": "deepseek_llm",
                        "long_term_summary_model": "deepseek-v4-pro",
                    }
                },
                llm_configs={
                    "deepseek_llm": {
                        "model": "deepseek-v4-pro",
                        "base_url": "https://example.invalid/v1",
                        "llm_api_key": "test-key",
                        "temperature": 0.7,
                    }
                },
                system_prompt="测试角色",
            )

        self.assertEqual(
            created_models,
            ["deepseek-v4-pro", "deepseek-v4-pro"],
        )
        self.assertIsNot(agent._llm, agent._summary_llm)

    async def test_both_summaries_use_dedicated_summary_llm(self):
        class FakeSummaryLLM:
            def __init__(self):
                self.calls = []

            async def chat_completion(self, messages, system):
                self.calls.append((messages, system))
                if "长期关系记录器" in system:
                    yield '{"long_term_relationship":"关系尚在建立中。"}'
                else:
                    yield '{"长期记忆数":0,"长期记忆":[]}'

        class FailingChatLLM:
            async def chat_completion(self, messages, system):
                raise AssertionError("The normal chat LLM must not summarize")
                yield ""

        summary_llm = FakeSummaryLLM()
        agent = object.__new__(BasicMemoryAgent)
        agent._llm = FailingChatLLM()
        agent._summary_llm = summary_llm

        memory_result = await agent.summarize_long_term_memory(
            [{"user": "你好", "assistant": "你好呀"}],
            [LongTermMemory("称呼偏好", "用户喜欢被叫小明。")],
        )
        relationship_result = await agent.summarize_long_term_relationship(
            "长期记忆文件完整内容",
            "现有关系文件完整内容",
        )

        self.assertEqual(memory_result, '{"长期记忆数":0,"长期记忆":[]}')
        self.assertEqual(
            relationship_result,
            '{"long_term_relationship":"关系尚在建立中。"}',
        )
        self.assertEqual(len(summary_llm.calls), 2)
        relationship_payload = json.loads(summary_llm.calls[1][0][0]["content"])
        self.assertEqual(
            relationship_payload["long_term_memory.md全部内容"],
            "长期记忆文件完整内容",
        )
        self.assertEqual(
            relationship_payload["现有long_term_relationship.md全部内容"],
            "现有关系文件完整内容",
        )


if __name__ == "__main__":
    unittest.main()
