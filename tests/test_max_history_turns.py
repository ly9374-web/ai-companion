import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from open_llm_vtuber.agent.agents.basic_memory_agent import BasicMemoryAgent
from open_llm_vtuber.agent.input_types import BatchInput, TextData, TextSource
from open_llm_vtuber.config_manager.agent import BasicMemoryAgentConfig


class MaxHistoryTurnsTests(unittest.TestCase):
    def _make_agent(self, max_history_turns: int) -> BasicMemoryAgent:
        agent = object.__new__(BasicMemoryAgent)
        agent._memory = [
            {"role": "user", "content": "user 1"},
            {"role": "assistant", "content": "assistant 1"},
            {"role": "user", "content": "user 2"},
            {"role": "assistant", "content": "assistant 2"},
            {"role": "user", "content": "user 3"},
            {"role": "assistant", "content": "assistant 3"},
            {"role": "user", "content": "user 4"},
            {"role": "assistant", "content": "assistant 4"},
        ]
        agent.set_max_history_turns(max_history_turns)
        return agent

    def test_default_is_eight_turns(self):
        config = BasicMemoryAgentConfig(llm_provider="deepseek_llm")
        self.assertEqual(config.max_history_turns, 8)

    def test_only_recent_turns_are_sent_but_full_memory_is_kept(self):
        agent = self._make_agent(2)
        messages = agent._to_messages(
            BatchInput(
                texts=[TextData(source=TextSource.INPUT, content="current input")]
            )
        )

        self.assertEqual(
            [message["role"] for message in messages],
            ["user", "assistant", "user", "assistant", "user"],
        )
        self.assertEqual(messages[0]["content"], "user 3")
        self.assertEqual(messages[3]["content"], "assistant 4")
        self.assertEqual(messages[4]["content"][0]["text"], "current input")

        self.assertEqual(len(agent._memory), 9)
        self.assertEqual(agent._memory[0]["content"], "user 1")
        self.assertEqual(agent._memory[-1]["content"], "current input")

    def test_value_must_be_an_integer_between_one_and_one_hundred(self):
        agent = self._make_agent(8)

        for invalid_value in (0, 101, True, 1.5):
            with self.subTest(invalid_value=invalid_value):
                with self.assertRaises(ValueError):
                    agent.set_max_history_turns(invalid_value)


if __name__ == "__main__":
    unittest.main()
